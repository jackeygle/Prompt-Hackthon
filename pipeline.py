"""End-to-end pipeline: brief -> spec -> discovery -> cheap filter -> collection -> features -> ranking.

CLI:  python pipeline.py "Find creators for €600–900 refurbished gaming PCs targeting gamers in Germany"
"""
import json
import logging
import shutil
import sqlite3
import sys
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import config
import db
import features as fx
import ranking
import youtube as yt
from collectors import LinkedAccountCollector, YouTubeCollector, extract_social_links
from llm import LLMClient
from models import CampaignSpec

log = logging.getLogger("pipeline")

SPEC_SYSTEM = """You turn a marketer's campaign brief into a structured campaign spec for creator discovery.
Interpret price ranges like '€600–900' as the PRODUCT price segment, not the campaign budget.
Write exactly 6 diverse YouTube search queries a target-audience viewer would type, mostly in the
target language (e.g. German for Germany), covering: buying advice, used/refurbished, budget builds,
price-segment comparisons, benchmarks. Keep each query short (2-6 words)."""


def parse_brief(llm: LLMClient, brief: str) -> CampaignSpec:
    return llm.extract(CampaignSpec, SPEC_SYSTEM, brief)


def _video_row(v: dict) -> dict:
    s, stt, cd = v["snippet"], v.get("statistics", {}), v.get("contentDetails", {})
    num = lambda k: int(stt[k]) if k in stt else None
    return {"id": v["id"], "channel_id": s["channelId"], "title": s.get("title", ""),
            "description": s.get("description", ""), "published_at": s["publishedAt"],
            "duration_s": yt.iso_duration_s(cd.get("duration", "")), "views": num("viewCount"),
            "likes": num("likeCount"), "comments": num("commentCount"),
            "language": s.get("defaultAudioLanguage") or s.get("defaultLanguage"),
            "thumbnail": (s.get("thumbnails", {}).get("medium") or {}).get("url")}


def _save_content(rows: list[dict]) -> None:
    db.executemany(
        "INSERT OR REPLACE INTO content (id, account_id, platform, title, description, published_at, duration_s,"
        " views, likes, comments, language, transcript, transcript_source, raw_json, fetched_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(r["id"], "yt:" + r["channel_id"], "youtube", r["title"], r["description"], r["published_at"],
          r["duration_s"], r["views"], r["likes"], r["comments"], r["language"], r.get("transcript"),
          r.get("transcript_source"), json.dumps({"thumbnail": r.get("thumbnail")}), db.now()) for r in rows])


class Progress:
    def __init__(self, cb=None):
        self.cb = cb or (lambda msg, frac: log.info("[%3d%%] %s", int(frac * 100), msg))

    def __call__(self, msg: str, frac: float):
        self.cb(msg, min(max(frac, 0.0), 1.0))


def run_campaign(brief: str, spec: CampaignSpec | None = None, seed_handles: list[str] | None = None,
                 progress=None) -> str:
    p = Progress(progress)
    llm = LLMClient()
    yt.QuotaUsage.units = 0
    campaign_id = "c_" + uuid.uuid4().hex[:8]

    # 1. Understand campaign
    p("Parsing campaign brief", 0.02)
    spec = spec or parse_brief(llm, brief)
    db.execute("INSERT INTO campaigns VALUES (?,?,?,?)", (campaign_id, brief, spec.model_dump_json(), db.now()))

    # 2. Discovery: search videos, group by channel
    after = (datetime.now(timezone.utc) - timedelta(days=365)).strftime("%Y-%m-%dT00:00:00Z")  # day granularity keeps the cache key stable
    hits: Counter = Counter()
    queries = spec.search_queries[:config.N_QUERIES]
    for i, q in enumerate(queries):
        p(f"YouTube search: “{q}”", 0.05 + 0.10 * i / len(queries))
        try:
            for item in yt.search_videos(q, spec.target_country, spec.target_language, after,
                                         config.SEARCH_RESULTS_PER_QUERY):
                hits[item["snippet"]["channelId"]] += 1
        except yt.YouTubeError as e:
            log.error("search failed for %r: %s", q, e)
            if e.reason == "quotaExceeded":
                break

    seeds = []
    for h in seed_handles or []:
        try:
            if ch := yt.channel_by_handle(h.strip()):
                seeds.append(ch["id"])
                hits[ch["id"]] += 0
        except yt.YouTubeError as e:
            log.warning("seed %s: %s", h, e)

    # 3. Cheap filter on channel metadata (free after 1 unit / 50 channels)
    p(f"Loading {len(hits)} channels", 0.17)
    chans = {c["id"]: c for c in yt.channels(list(hits))}
    dach = {"DE", "AT", "CH"} if spec.target_country == "DE" else {spec.target_country}
    cands = []
    for cid, c in chans.items():
        stats, country = c.get("statistics", {}), c["snippet"].get("country")
        subs = int(stats.get("subscriberCount", 0))
        if cid not in seeds:
            if not (config.MIN_SUBSCRIBERS <= subs <= config.MAX_SUBSCRIBERS):
                continue
            if country and country not in dach:
                continue
        cands.append((cid in seeds, hits[cid], subs, cid))
    cands.sort(reverse=True)
    shortlist = [cid for *_, cid in cands[:config.N_AFTER_CHEAP_FILTER]]
    p(f"Cheap filter: {len(chans)} → {len(shortlist)} channels", 0.2)

    # 4. Uploads + LLM relevance classification per channel
    uploads: dict[str, list[dict]] = {}
    relevance: dict[str, object] = {}

    def scan(cid: str):
        up_pl = chans[cid]["contentDetails"]["relatedPlaylists"]["uploads"]
        vids = [_video_row(v) for v in yt.videos(yt.playlist_video_ids(up_pl, config.UPLOADS_TO_SCAN))]
        vids = [v for v in vids if v["duration_s"] >= 60]  # skip Shorts for depth analysis
        uploads[cid] = vids
        if vids:
            relevance[cid] = fx.relevant_video_ids(llm, spec, chans[cid]["snippet"]["title"], vids)

    done = 0
    with ThreadPoolExecutor(config.LLM_CONCURRENCY) as ex:
        for fut in [ex.submit(scan, cid) for cid in shortlist]:
            try:
                fut.result()
            except Exception as e:
                log.warning("scan failed: %s", e)
            done += 1
            p(f"Screening uploads ({done}/{len(shortlist)})", 0.2 + 0.25 * done / len(shortlist))
    for vids in uploads.values():
        _save_content(vids)

    deep = sorted([cid for cid in shortlist if cid in relevance and relevance[cid].relevant_ids],
                  key=lambda c: (c in seeds, len(relevance[c].relevant_ids), hits[c]), reverse=True)[:config.N_DEEP]

    # 5. Deep analysis per creator
    results: dict[str, dict] = {}

    def analyze(cid: str) -> None:
        ch = chans[cid]
        title = ch["snippet"]["title"]
        by_id = {v["id"]: v for v in uploads[cid]}
        rel = sorted((by_id[i] for i in relevance[cid].relevant_ids), key=lambda v: v["published_at"], reverse=True)
        top = rel[:config.VIDEOS_PER_CREATOR]
        for v in top:
            v["transcript"] = yt.transcript(v["id"])
            v["transcript_source"] = "unofficial" if v["transcript"] else None
        _save_content(top)

        comments, threads, replied = [], 0, 0
        comments_available = False
        for v in top:
            th = yt.comment_threads(v["id"], config.COMMENTS_PER_VIDEO)
            if th is None:
                continue
            comments_available = True
            for t in th:
                threads += 1
                top_c = t["snippet"]["topLevelComment"]
                sn = top_c["snippet"]
                replies = t.get("replies", {}).get("comments", [])
                if any(r["snippet"].get("authorChannelId", {}).get("value") == cid for r in replies):
                    replied += 1
                if sn.get("authorChannelId", {}).get("value") == cid:
                    continue  # creator's own top-level comment
                comments.append({"id": top_c["id"], "content_id": v["id"], "text": sn.get("textOriginal", ""),
                                 "likes": sn.get("likeCount", 0), "published_at": sn.get("publishedAt"),
                                 "author_hash": fx.author_hash(sn.get("authorChannelId", {}).get("value")),
                                 "is_creator_reply": 0})
        fx.classify_comments(llm, comments)
        db.executemany("INSERT OR REPLACE INTO comments VALUES (?,?,?,?,?,?,?,?,?)",
                       [(c["id"], c["content_id"], c["author_hash"], c["text"], c["likes"], c["is_creator_reply"],
                         c["published_at"], c.get("label"), c.get("language")) for c in comments])

        subs_hidden = ch["statistics"].get("hiddenSubscriberCount", False)
        subs = None if subs_hidden else int(ch["statistics"].get("subscriberCount", 0))
        f = fx.statistical_features(rel, uploads[cid], subs)
        f.update(fx.comment_features(comments, threads, replied, spec.target_language))
        summary, evidence = "", []
        try:
            cf, evidence, summary = fx.content_features(llm, spec, title, top)
            f.update(cf)
        except Exception as e:
            log.warning("content features failed for %s: %s", title, e)
        f.update({
            "n_analyzed_videos": len(top),
            "transcript_coverage": sum(bool(v.get("transcript")) for v in top) / max(len(top), 1),
            "source_reliability": YouTubeCollector.source_reliability,
            "hidden_subscribers": subs_hidden,
            "comments_enabled": comments_available,
            "subscribers": subs,
            "search_hits": hits[cid],
        })
        links = extract_social_links(ch["snippet"].get("description", ""), *[v["description"] for v in top])
        results[cid] = {"features": f, "evidence": evidence, "summary": summary, "links": links,
                        "niche_label": relevance[cid].niche_label, "videos": [v["id"] for v in top]}

    done = 0
    with ThreadPoolExecutor(max(2, config.LLM_CONCURRENCY // 2)) as ex:
        futs = {ex.submit(analyze, cid): cid for cid in deep}
        for fut in futs:
            try:
                fut.result()
            except Exception as e:
                log.exception("analysis failed for %s: %s", futs[fut], e)
            done += 1
            p(f"Deep analysis ({done}/{len(deep)})", 0.45 + 0.45 * done / max(len(deep), 1))

    # 6. Persist creators, accounts, features, evidence
    p("Ranking", 0.92)
    for cid, r in results.items():
        ch = chans[cid]
        sn = ch["snippet"]
        creator_id = "yt:" + cid
        db.execute("INSERT OR REPLACE INTO creators VALUES (?,?,?,?,?)",
                   (creator_id, sn["title"], "youtube", sn.get("country"), db.now()))
        db.execute("INSERT OR REPLACE INTO platform_accounts VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (creator_id, creator_id, "youtube", sn.get("customUrl") or cid,
                    f"https://www.youtube.com/channel/{cid}", r["features"].get("subscribers"),
                    "youtube_data_api", YouTubeCollector.source_reliability,
                    json.dumps({"thumbnail": (sn.get("thumbnails", {}).get("default") or {}).get("url"),
                                "description": sn.get("description", "")[:500]}), db.now()))
        for plat, handle in r["links"].items():
            db.execute("INSERT OR IGNORE INTO platform_accounts VALUES (?,?,?,?,?,?,?,?,?,?)",
                       (f"{plat}:{handle.lower()}", creator_id, plat, handle, None, None, "description_link",
                        LinkedAccountCollector.source_reliability, None, db.now()))
        db.executemany("INSERT OR REPLACE INTO features VALUES (?,?,?,?,?,?)",
                       [(campaign_id, creator_id, k, float(v), _method(k), None)
                        for k, v in r["features"].items() if v is not None])
        db.executemany("INSERT INTO evidence (campaign_id, creator_id, feature, content_id, quote, verified)"
                       " VALUES (?,?,?,?,?,?)",
                       [(campaign_id, creator_id, e["feature"], e["video_id"], e["quote"], int(e["verified"]))
                        for e in r["evidence"]])
        r["creator_id"] = creator_id

    # 7. Default ranking (the dashboard re-ranks live with other weights)
    rank_and_store(campaign_id, spec.goal, {r["creator_id"]: r for r in results.values()})
    p(f"Done: {len(results)} creators analysed · {llm.calls} LLM calls ({llm.cache_hits} cached) · "
      f"~{yt.QuotaUsage.units} YouTube quota units", 1.0)
    return campaign_id


def _method(name: str) -> str:
    if name in fx.CONTENT_FEATURES:
        return "llm"
    if name in {"meaningful_ratio", "technical_question_ratio", "purchase_intent_ratio", "spam_ratio",
                "target_lang_share", "n_classified_comments"}:
        return "llm_comments"
    return "stat"


def load_features(campaign_id: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for row in db.query("SELECT creator_id, name, value FROM features WHERE campaign_id=?", (campaign_id,)):
        out.setdefault(row["creator_id"], {})[row["name"]] = row["value"]
    return out


def rank_and_store(campaign_id: str, preset: str, extra: dict | None = None) -> None:
    import pandas as pd
    feats = load_features(campaign_id)
    w_groups, _ = ranking.ahp_weights(ranking.AHP_PRESETS[preset])
    weights = ranking.criterion_weights(dict(zip(ranking.GROUPS, w_groups)))
    passed = {cid: ranking.hard_filter(f) for cid, f in feats.items()}
    ok = [cid for cid, (p, _) in passed.items() if p]
    df = pd.DataFrame({cid: feats[cid] for cid in ok}).T.reindex(columns=[c.name for c in ranking.CRITERIA])
    res = ranking.topsis(df, weights) if len(df) else pd.DataFrame()
    previous = {r["creator_id"]: json.loads(r["breakdown_json"] or "{}")
                for r in db.query("SELECT creator_id, breakdown_json FROM rankings WHERE campaign_id=?", (campaign_id,))}
    db.execute("DELETE FROM rankings WHERE campaign_id=?", (campaign_id,))
    for cid, f in feats.items():
        p_ok, reason = passed[cid]
        n_imp = int(sum(res.loc[cid, f"imputed__{c}"] for c in weights)) if p_ok else 0
        conf = ranking.confidence(f, n_imp)
        meta = {k: v for k, v in previous.get(cid, {}).items() if k != "explain"}
        meta.update({k: v for k, v in (extra or {}).get(cid, {}).items() if k in ("summary", "niche_label", "videos")})
        if p_ok:
            meta["explain"] = ranking.explain(res.loc[cid], weights)
        db.execute("INSERT INTO rankings VALUES (?,?,?,?,?,?,?,?)",
                   (campaign_id, cid, int(res.loc[cid, "rank"]) if p_ok else None,
                    float(res.loc[cid, "score"]) if p_ok else None, conf, int(p_ok), reason, json.dumps(meta)))


def export_demo(path=config.ROOT / "data" / "demo.db") -> None:
    """Copy of the DB without the raw API/LLM cache (keeps the committed snapshot small, no raw author data)."""
    path.unlink(missing_ok=True)
    shutil.copy(config.DB_PATH, path)
    c = sqlite3.connect(path)
    c.execute("DELETE FROM api_cache")
    c.commit()
    c.execute("VACUUM")
    c.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "google_genai", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    brief = sys.argv[1] if len(sys.argv) > 1 else \
        "Find creators for €600–900 refurbished gaming PCs targeting gamers in Germany."
    cid = run_campaign(brief)
    export_demo()
    print("campaign:", cid)
