"""End-to-end pipeline: brief -> spec -> YouTube + web discovery -> identity merge -> cheap filter ->
collection -> text / comment / visual features -> hard filter -> AHP + TOPSIS.

CLI:  python pipeline.py "Find creators for €600–900 refurbished gaming PCs targeting gamers in Germany"
"""
import json
import logging
import shutil
import sqlite3
import sys
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import config
import db
import features as fx
import ranking
import vision
import web_discovery as web
import youtube as yt
from collectors import LinkedAccountCollector, YouTubeCollector, extract_social_links
from llm import LLMClient
from llm import dead_models as llm_dead_models
from llm import missing_keys as llm_missing_keys
from models import CampaignSpec, CampaignSpecDraft

log = logging.getLogger("pipeline")

SPEC_SYSTEM = """You turn a marketer's campaign brief into a structured campaign spec for creator discovery.
Interpret price ranges like '€600–900' as the PRODUCT price segment, not the campaign budget.
Write exactly 6 diverse YouTube search queries a target-audience viewer would type, mostly in the
target language (e.g. German for Germany), covering: buying advice, used/refurbished, budget builds,
price-segment comparisons, benchmarks. Keep each query short (2-6 words).
Also write 6 web_queries for discovering creators and their profiles on other platforms (see field description)."""


def parse_brief(llm: LLMClient, brief: str) -> CampaignSpec:
    """LLM extracts a draft; creator constraints get defaults the user can edit before the run."""
    draft = llm.extract(CampaignSpecDraft, SPEC_SYSTEM, brief)
    if not draft.web_queries:
        draft.web_queries = web.default_web_queries(CampaignSpec(**draft.model_dump()))
    return CampaignSpec(**draft.model_dump(), min_subscribers=config.MIN_SUBSCRIBERS,
                        max_subscribers=config.MAX_SUBSCRIBERS)


def _video_row(v: dict) -> dict:
    s, stt, cd = v["snippet"], v.get("statistics", {}), v.get("contentDetails", {})
    num = lambda k: int(stt[k]) if k in stt else None
    thumbs = s.get("thumbnails", {})
    return {"id": v["id"], "channel_id": s["channelId"], "title": s.get("title", ""),
            "description": s.get("description", ""), "published_at": s["publishedAt"],
            "duration_s": yt.iso_duration_s(cd.get("duration", "")), "views": num("viewCount"),
            "likes": num("likeCount"), "comments": num("commentCount"),
            "language": s.get("defaultAudioLanguage") or s.get("defaultLanguage"),
            "thumbnail": (thumbs.get("high") or thumbs.get("medium") or {}).get("url")}


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
    t0 = time.time()
    llm = LLMClient(config.LLM_MODELS)
    vlm = LLMClient(config.VLM_MODELS)
    yt.QuotaUsage.units = 0
    yt.TranscriptStatus.reset()
    web.TavilyUsage.calls = 0
    errors: list[str] = []
    campaign_id = "c_" + uuid.uuid4().hex[:8]

    # 1. Understand campaign (normally already parsed + edited in the UI)
    p("Parsing campaign brief", 0.02)
    spec = spec or parse_brief(llm, brief)
    db.execute("INSERT INTO campaigns VALUES (?,?,?,?)", (campaign_id, brief, spec.model_dump_json(), db.now()))

    # 2a. YouTube discovery: search videos, group by channel
    after = (datetime.now(timezone.utc) - timedelta(days=365)).strftime("%Y-%m-%dT00:00:00Z")  # day granularity keeps the cache key stable
    hits: Counter = Counter()
    hit_queries: dict[str, set] = {}
    queries = spec.search_queries[:config.N_QUERIES]
    for i, q in enumerate(queries):
        p(f"YouTube search: “{q}”", 0.04 + 0.08 * i / max(len(queries), 1))
        try:
            for item in yt.search_videos(q, spec.target_country, spec.target_language, after,
                                         config.SEARCH_RESULTS_PER_QUERY):
                cid = item["snippet"]["channelId"]
                hits[cid] += 1
                hit_queries.setdefault(cid, set()).add(q)
        except yt.YouTubeError as e:
            log.error("search failed for %r: %s", q, e)
            errors.append(f"YouTube search: {e}")
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

    p(f"Loading {len(hits)} channels", 0.13)
    chans = {c["id"]: c for c in yt.channels(list(hits))}

    # 2b. Web / cross-platform discovery (Tavily). Optional: failures leave YouTube-only discovery intact.
    web_matches: dict[str, list[dict]] = {}
    web_only: list[dict] = []
    n_identities = 0
    if config.WEB_DISCOVERY_ENABLED:
        identities, werr = web.discover(spec, llm, lambda m, f: p(m, 0.14 + 0.05 * f))
        errors += [f"Web discovery: {e}" for e in werr]
        n_identities = len(identities)
        if identities:
            p("Merging web identities with YouTube channels", 0.19)
            web_matches, web_only = web.resolve_to_youtube(identities, chans)

    # 3. Identity merge + cheap filter on channel metadata
    dach = {"DE", "AT", "CH"} if spec.target_country == "DE" else {spec.target_country}
    web_hits = {cid: len(ids) for cid, ids in web_matches.items()}
    cands = []
    for cid, c in chans.items():
        stats, country = c.get("statistics", {}), c["snippet"].get("country")
        subs = int(stats.get("subscriberCount", 0))
        if cid not in seeds:
            if not (spec.min_subscribers <= subs <= spec.max_subscribers):
                continue
            if country and country not in dach:
                continue
        score = hits[cid] + 2 * web_hits.get(cid, 0)  # found on the web too -> stronger discovery signal
        cands.append((cid in seeds, score, subs, cid))
    cands.sort(reverse=True)
    shortlist = [cid for *_, cid in cands[:config.N_AFTER_CHEAP_FILTER]]
    p(f"Cheap filter: {len(chans)} → {len(shortlist)} channels "
      f"({sum(1 for c in shortlist if c in web_hits)} also found on the web)", 0.2)

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
                errors.append(f"Upload screening: {str(e)[:150]}")
            done += 1
            p(f"Screening uploads ({done}/{len(shortlist)})", 0.2 + 0.25 * done / max(len(shortlist), 1))
    for vids in uploads.values():
        _save_content(vids)

    deep = sorted([cid for cid in shortlist if cid in relevance and relevance[cid].relevant_ids],
                  key=lambda c: (c in seeds, len(relevance[c].relevant_ids), hits[c] + 2 * web_hits.get(c, 0)),
                  reverse=True)[:config.N_DEEP]

    # 5. Deep analysis per creator
    results: dict[str, dict] = {}
    vlm_failures: list[str] = []

    def analyze(cid: str) -> None:
        ch = chans[cid]
        title = ch["snippet"]["title"]
        by_id = {v["id"]: v for v in uploads[cid]}
        rel = sorted((by_id[i] for i in relevance[cid].relevant_ids), key=lambda v: v["published_at"], reverse=True)
        top = rel[:config.VIDEOS_PER_CREATOR]
        for v in top:  # transcript if available, otherwise title + description only (never fabricated)
            v["transcript"] = yt.transcript(v["id"], (spec.target_language, "en"))
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

        # Visual enrichment (optional): failure only lowers confidence
        visual_tags = []
        if config.VLM_ENABLED and vlm.available_models():
            try:
                vf, visual_tags = vision.analyze_creator(vlm, spec, top)
                f.update(vf)
            except Exception as e:
                vlm_failures.append(f"{title}: {str(e)[:150]}")
                log.warning("visual analysis failed for %s: %s", title, str(e)[:200])

        f.update({
            "n_analyzed_videos": len(top),
            "transcript_coverage": sum(bool(v.get("transcript")) for v in top) / max(len(top), 1),
            "source_reliability": YouTubeCollector.source_reliability,
            "hidden_subscribers": subs_hidden,
            "comments_enabled": comments_available,
            "subscribers": subs,
            "search_hits": hits[cid],
            "web_mentions": web_hits.get(cid, 0),
        })
        links = {k: (v, "description_link") for k, v in
                 extract_social_links(ch["snippet"].get("description", ""), *[v["description"] for v in top]).items()}
        for ident in web_matches.get(cid, []):
            if ident["platform"] not in ("youtube", "unknown", "website") and ident["handle"]:
                links.setdefault(ident["platform"], (ident["handle"], "tavily_web"))
        results[cid] = {"features": f, "evidence": evidence, "summary": summary, "links": links,
                        "niche_label": relevance[cid].niche_label, "videos": [v["id"] for v in top],
                        "visual_tags": visual_tags}

    done = 0
    with ThreadPoolExecutor(max(2, config.LLM_CONCURRENCY // 2)) as ex:
        futs = {ex.submit(analyze, cid): cid for cid in deep}
        for fut in futs:
            try:
                fut.result()
            except Exception as e:
                log.exception("analysis failed for %s: %s", futs[fut], e)
                errors.append(f"Analysis {futs[fut]}: {str(e)[:150]}")
            done += 1
            p(f"Deep analysis ({done}/{len(deep)})", 0.45 + 0.45 * done / max(len(deep), 1))

    # 6. Persist creators, accounts, discoveries, features, evidence, visual tags
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
        for plat, (handle, source) in r["links"].items():
            db.execute("INSERT OR IGNORE INTO platform_accounts VALUES (?,?,?,?,?,?,?,?,?,?)",
                       (f"{plat}:{handle.lower()}", creator_id, plat, handle, None, None, source,
                        LinkedAccountCollector.source_reliability, None, db.now()))
        rows = [(campaign_id, creator_id, "youtube_search", "youtube", sn.get("customUrl"), q, None, None, "search")
                for q in sorted(hit_queries.get(cid, []))]
        rows += [(campaign_id, creator_id, "seed", "youtube", sn.get("customUrl"), None, None, None, "manual")
                 for _ in [0] if cid in seeds]
        rows += [(campaign_id, creator_id, "tavily_web", i["platform"], i["handle"] or i["name"], i["query"],
                  i["source_url"], i["evidence"], i["method"]) for i in web_matches.get(cid, [])]
        db.executemany("INSERT INTO discoveries (campaign_id, creator_key, source, platform, handle, query, url,"
                       " evidence, method) VALUES (?,?,?,?,?,?,?,?,?)", rows)
        db.executemany("INSERT OR REPLACE INTO features VALUES (?,?,?,?,?,?)",
                       [(campaign_id, creator_id, k, float(v), _method(k), None)
                        for k, v in r["features"].items() if v is not None])
        db.executemany("INSERT INTO evidence (campaign_id, creator_id, feature, content_id, quote, verified)"
                       " VALUES (?,?,?,?,?,?)",
                       [(campaign_id, creator_id, e["feature"], e["video_id"], e["quote"], int(e["verified"]))
                        for e in r["evidence"]])
        db.executemany("INSERT OR REPLACE INTO visual_tags VALUES (?,?,?,?,?)",
                       [(campaign_id, creator_id, t["video_id"], t["url"], json.dumps(t)) for t in r["visual_tags"]])
        r["creator_id"] = creator_id

    # Web-only creators: discovery evidence and profiles only (no metrics -> not ranked)
    for w in web_only:
        key = "web:" + web._norm(w["name"])
        db.executemany("INSERT INTO discoveries (campaign_id, creator_key, source, platform, handle, query, url,"
                       " evidence, method) VALUES (?,?,?,?,?,?,?,?,?)",
                       [(campaign_id, key, "tavily_web", s_["platform"], w["profiles"].get(s_["platform"]) or w["name"],
                         s_["query"], s_["source_url"], s_["evidence"], s_["method"]) for s_ in w["sources"]])

    # 7. Default ranking (the dashboard re-ranks live with other weights)
    rank_and_store(campaign_id, spec.goal, {r["creator_id"]: r for r in results.values()})
    stats = {
        "duration_s": round(time.time() - t0, 1),
        "youtube_quota_units": yt.QuotaUsage.units,
        "tavily_calls": web.TavilyUsage.calls,
        "text_llm_attempts": dict(llm.calls_by_model), "text_llm_cache_hits": llm.cache_hits,
        "vlm_attempts": dict(vlm.calls_by_model), "vlm_cache_hits": vlm.cache_hits,
        "transcripts": dict(yt.TranscriptStatus.counts), "transcripts_blocked": yt.TranscriptStatus.blocked,
        "web_identities": n_identities, "web_matched_channels": len(web_matches), "web_only_creators": len(web_only),
        "creators_analysed": len(results), "vlm_failures": vlm_failures[:10],
        "disabled_models": llm_dead_models(),
        "missing_env": sorted(set(llm_missing_keys(config.LLM_MODELS) + llm_missing_keys(config.VLM_MODELS)
                                  + ([] if config.TAVILY_API_KEY else ["TAVILY_API_KEY"]))),
        "errors": errors[:20],
    }
    db.execute("INSERT OR REPLACE INTO run_stats VALUES (?,?)", (campaign_id, json.dumps(stats)))
    p(f"Done: {len(results)} creators analysed · {llm.calls} text-LLM calls ({llm.cache_hits} cached) · "
      f"{vlm.calls} VLM calls · {web.TavilyUsage.calls} Tavily calls · ~{yt.QuotaUsage.units} YouTube quota units",
      1.0)
    return campaign_id


def _method(name: str) -> str:
    if name in fx.CONTENT_FEATURES:
        return "llm"
    if name.startswith("visual_") or name == "n_visual_images":
        return "vlm"
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
        n_imp = int(sum(bool(res.loc[cid, f"imputed__{c}"]) for c in res.attrs["weights"])) if p_ok else 0
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
