"""End-to-end pipeline: brief -> spec -> YouTube + web + Twitch discovery -> identity merge (+ Instagram numbers) -> cheap filter ->
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
import instagram
import twitch
import vision
import web_discovery as web
import youtube as yt
from collectors import InstagramCollector, LinkedAccountCollector, TwitchCollector, YouTubeCollector, extract_social_links
from llm import LLMClient
from llm import dead_models as llm_dead_models
from llm import missing_keys as llm_missing_keys
from models import CampaignSpec, CampaignSpecDraft, SearchPlan

log = logging.getLogger("pipeline")

SPEC_SYSTEM = """You turn a marketer's campaign brief into a structured campaign spec for creator discovery.
Interpret price ranges like '€600–900' as the PRODUCT price segment, not the campaign budget.
List the audience_interests: the broader content this target audience already watches, not only the product.
Write exactly 6 diverse YouTube search queries a target-audience viewer would type, mostly in the target
language (e.g. German for Germany): about half about the product/purchase (buying advice, price segment,
used/refurbished), the rest about the audience's interests (e.g. for gaming PCs: PC gaming, game performance,
GPUs, gaming setups, tech reviews). Keep each query short (2-6 words).
Only fill min_subscribers / max_subscribers / price_segment when the brief states them; otherwise null.
Also write 6 web_queries for discovering creators and their profiles on other platforms (see field description)."""


PLAN_SYSTEM = """You write the creator-discovery search strategy for an influencer campaign.
Principle: audience -> what they currently care about -> the content they watch -> the creator behind it.
A real viewer does not search for "newest games" or "gaming influencer"; they search for the actual game, product
or question. Build the content_queries from the CONCRETE CURRENT TOPICS given (actual names), phrased the way
the target audience searches (gameplay, tips, test, review, FPS, settings, comparison, "is it worth it", ...), in
the target market's language where people search that way, keeping product and game names as people type them.
Spread the content_queries over the audience's whole attention, not only the product angle:
- about half on the main things they follow for their own sake (for gamers: the games themselves: gameplay, tips,
  updates, news, highlights, tournaments, where the big gaming creators are), without a hardware or buying angle;
- some on adjacent questions (for gamers: performance / FPS, settings, hardware comparisons, setups);
- at most one about buying the promoted product itself, and only if it has a product. Relevance means the creator reaches an audience that could buy, not that they cover the product: for
gaming PCs that includes gaming, tech and hardware creators broadly, not only PC-building channels.
If the campaign is not about gaming or tech, follow its own topic and never add gaming or tech terms.
creator_queries are the secondary path: direct "best / top creators" searches for this audience in this market.
Keep every query short (2-6 words). Do not just append the country name to generic phrases."""


def plan_searches(llm: LLMClient, spec: CampaignSpec) -> SearchPlan:
    """Search strategy from the researched topics (or, without them, from the audience interests)."""
    topics = "\n".join(f"- {t.name} ({t.kind}): {t.why}" for t in spec.current_topics) or \
        "(no web research available: use your own knowledge of concrete current names, and say nothing generic)"
    plan = llm.extract(SearchPlan, PLAN_SYSTEM, (
        f"Product: {spec.product}\nNiche: {spec.niche}\nTarget audience: {spec.audience}\n"
        f"Audience interests: {', '.join(spec.audience_interests)}\n"
        f"Market: {spec.target_country}; language: {spec.target_language}\n\nCONCRETE CURRENT TOPICS\n{topics}\n\n"
        f"Write {config.N_QUERIES} content_queries, {config.N_CREATOR_QUERIES} creator_queries and 6 web_queries."))
    clean = lambda qs, n: list(dict.fromkeys(q.strip() for q in qs if q and q.strip()))[:n]
    return SearchPlan(content_queries=clean(plan.content_queries, config.N_QUERIES),
                      creator_queries=clean(plan.creator_queries, config.N_CREATOR_QUERIES),
                      web_queries=clean(plan.web_queries, 6))


def n_creator_searches(n_content: int) -> int:
    """Direct creator searches stay the minority: at most half the topic/content searches."""
    return min(config.N_CREATOR_QUERIES, n_content // 2)


def discovery_queries(spec: CampaignSpec) -> list[str]:
    """YouTube searches in run order: topic/content searches (main path), then a few direct creator searches."""
    content = spec.search_queries[:config.N_QUERIES]
    return list(dict.fromkeys(content + spec.creator_queries[:n_creator_searches(len(content))]))


def parse_brief(llm: LLMClient, brief: str) -> CampaignSpec:
    """LLM extracts a draft; creator constraints get defaults the user can edit before the run."""
    draft = llm.extract(CampaignSpecDraft, SPEC_SYSTEM, brief)
    data = draft.model_dump()
    sources = {}
    defaults = {"min_subscribers": config.MIN_SUBSCRIBERS, "max_subscribers": config.MAX_SUBSCRIBERS,
                "price_segment": ""}
    for k, default in defaults.items():
        extracted = data.get(k)
        sources[k] = "brief" if extracted not in (None, "", 0) else "default"
        data[k] = extracted if sources[k] == "brief" else default
    if data["min_subscribers"] > data["max_subscribers"]:  # inconsistent extraction -> keep the stated bound only
        data["max_subscribers"] = config.MAX_SUBSCRIBERS
        sources["max_subscribers"] = "default"
    if not data["audience_interests"]:
        data["audience_interests"] = [data["niche"]]
        sources["audience_interests"] = "default"
    sources["n_creators"] = "default"
    # Campaign priority is a business decision the user makes on the settings page: start Balanced, never guessed
    data["goal"], sources["goal"] = "balanced", "default"
    spec = CampaignSpec(**data, n_creators=config.DEFAULT_N_CREATORS, field_sources=sources)
    # Current web information first, then the search strategy built on it. Both are optional: without them the
    # brief's own queries stay (the UI says the strategy came from model knowledge).
    if config.WEB_DISCOVERY_ENABLED:
        spec.current_topics, err = web.research_current_topics(spec, llm)
        if err:
            log.warning("topic research: %s", err)
    spec.topics_source = "web search" if spec.current_topics else "model knowledge"
    try:
        plan = plan_searches(llm, spec)
        if plan.content_queries:
            spec.search_queries, spec.creator_queries = plan.content_queries, plan.creator_queries
            spec.web_queries = plan.web_queries or spec.web_queries
            spec.field_sources["search_queries"] = spec.topics_source  # the spec holds its own copy of `sources`
    except Exception as e:  # keep the brief's queries
        log.warning("search planning failed: %s", e)
    if not spec.web_queries:
        spec.web_queries = web.default_web_queries(spec)
    return spec


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
    web.OpenAISearchUsage.calls = 0
    twitch.TwitchUsage.calls = 0
    instagram.InstagramUsage.calls = 0
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
    queries = discovery_queries(spec)
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

    # 2b. Web / cross-platform discovery (gpt-5.6-sol web search on Azure, or Tavily). Optional: failures leave YouTube-only discovery intact.
    web_matches: dict[str, list[dict]] = {}
    web_only: list[dict] = []
    identities: list[dict] = []
    if config.WEB_DISCOVERY_ENABLED:
        identities, werr = web.discover(spec, llm, lambda m, f: p(m, 0.14 + 0.04 * f))
        errors += [f"Web discovery: {e}" for e in werr]
    n_identities = len(identities)

    # 2c. Twitch (optional): live streamers in the target language + verification of web-found Twitch handles
    twitch_profiles: dict[str, dict] = {}
    twitch_kept: set[str] = set()
    if config.TWITCH_ENABLED and twitch.available():
        web_tw = [i["handle"] for i in identities if i["platform"] == "twitch" and i["handle"]]
        tw_ids, twitch_profiles, terr = twitch.discover(spec, web_tw, lambda m, f: p(m, 0.18 + 0.01 * f))
        errors += [f"Twitch: {e}" for e in terr]
        twitch_kept = {i["handle"].lower() for i in tw_ids if i["platform"] == "twitch"}
        identities += tw_ids
    if identities:
        p("Merging web & Twitch identities with YouTube channels", 0.19)
        web_matches, web_only = web.resolve_to_youtube(identities, chans)
    # a web-only creator whose Twitch profile was verified is listed once, with metrics, under Twitch
    web_only = [w for w in web_only if (w["profiles"].get("twitch") or "").lower() not in twitch_kept]

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
    # screen ~2x the requested creators (some fail relevance / hard filters); bounded for API cost
    n_screen = min(max(config.N_AFTER_CHEAP_FILTER, 2 * spec.n_creators), config.MAX_SCREENED)
    shortlist = [cid for *_, cid in cands[:n_screen]]
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
                  reverse=True)[:spec.n_creators]

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
            if ident.get("twitch_login"):
                links["twitch"] = (ident["twitch_login"], "twitch_api")  # YouTube link in the Twitch bio
            elif ident["platform"] not in ("youtube", "unknown", "website") and ident["handle"]:
                links.setdefault(ident["platform"], (ident["handle"], ident.get("source", "tavily_web")))
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

    # streamers whose YouTube channel was not analysed in depth stay visible in the Twitch-only list
    twitch_matched = {(i.get("twitch_login") or i["handle"] or "").lower() for cid in results
                      for i in web_matches.get(cid, []) if i.get("source") == "twitch_api"}
    twitch_only = [twitch_profiles[l] for l in sorted(twitch_kept - twitch_matched)]

    # 5b. Twitch metrics for ranked creators with a Twitch account (descriptive; never changes the score)
    tw_logins = {r["links"]["twitch"][0].lower() for r in results.values() if "twitch" in r["links"]}
    missing = sorted(tw_logins - set(twitch_profiles))
    if missing and config.TWITCH_ENABLED and twitch.available():
        try:
            twitch_profiles.update(twitch.profiles(missing))
        except twitch.TwitchError as e:
            errors.append(f"Twitch: {e}")
    for r in results.values():
        tp = twitch_profiles.get(r["links"].get("twitch", ("",))[0].lower())
        if tp and tp.get("followers") is not None:
            r["features"]["twitch_followers"] = tp["followers"]

    # 5c. Instagram numbers for ranked and web-only creators with a known handle (descriptive; never scored)
    ig_profiles: dict[str, dict] = {}
    ig_handles = [r["links"]["instagram"][0] for r in results.values() if "instagram" in r["links"]]
    ig_handles += [w["profiles"]["instagram"] for w in web_only if w["profiles"].get("instagram")]
    ig_handles += [tp["links"]["instagram"] for tp in twitch_profiles.values() if tp.get("links", {}).get("instagram")]
    ig_handles += [h for cid in shortlist  # screened channels that link an Instagram account in their description
                   if (h := extract_social_links(chans[cid]["snippet"].get("description", "")).get("instagram"))]
    if ig_handles and config.INSTAGRAM_ENABLED and instagram.available():
        p(f"Instagram: loading {len(set(ig_handles))} profiles", 0.91)
        ig_profiles, ierr = instagram.profiles(ig_handles)
        errors += [f"Instagram: {e}" for e in ierr]
    for r in results.values():
        ip = ig_profiles.get(r["links"].get("instagram", ("",))[0].lower())
        if ip and ip["followers"] is not None:
            r["features"]["instagram_followers"] = ip["followers"]
        if ip and ip["engagement_rate"] is not None:
            r["features"]["instagram_engagement_rate"] = ip["engagement_rate"]

    # 5d. Separate Twitch / Instagram rankings: official numbers + one LLM fit judgement on the profile's own text
    in_range = lambda n: n is None or spec.min_subscribers <= n <= spec.max_subscribers
    by_platform = {
        "twitch": {k: v for k, v in twitch_profiles.items() if in_range(v.get("followers"))
                   and (not v.get("language") or v["language"] == spec.target_language)},
        "instagram": {k: v for k, v in ig_profiles.items() if in_range(v.get("followers"))},
    }
    platform_rows: dict[str, tuple] = {}
    for plat, profs in by_platform.items():
        if profs:
            p(f"{plat.capitalize()}: judging {len(profs)} profiles", 0.915)
            fits = fx.platform_fit(llm, spec, plat, profs)
            for k, prof in profs.items():
                platform_rows[f"{plat}:{k}"] = (plat, prof, fits.get(k),
                                                fx.platform_features(plat, prof, fits.get(k), spec.target_language))

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
            tp = twitch_profiles.get(handle.lower()) if plat == "twitch" else None
            ip = ig_profiles.get(handle.lower()) if plat == "instagram" else None
            if tp:
                _save_twitch_account(tp, creator_id)
            elif ip:
                _save_instagram_account(ip, creator_id)
            else:
                db.execute("INSERT OR IGNORE INTO platform_accounts VALUES (?,?,?,?,?,?,?,?,?,?)",
                           (f"{plat}:{handle.lower()}", creator_id, plat, handle, None, None, source,
                            LinkedAccountCollector.source_reliability, None, db.now()))
        rows = [(campaign_id, creator_id, "youtube_search", "youtube", sn.get("customUrl"), q, None, None, "search")
                for q in sorted(hit_queries.get(cid, []))]
        rows += [(campaign_id, creator_id, "seed", "youtube", sn.get("customUrl"), None, None, None, "manual")
                 for _ in [0] if cid in seeds]
        rows += [(campaign_id, creator_id, i.get("source", "tavily_web"), i["platform"], i["handle"] or i["name"],
                  i["query"], i["source_url"], i["evidence"], i["method"]) for i in web_matches.get(cid, [])]
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
        if ip := ig_profiles.get((w["profiles"].get("instagram") or "").lower()):
            _save_instagram_account(ip, None)
        db.executemany("INSERT INTO discoveries (campaign_id, creator_key, source, platform, handle, query, url,"
                       " evidence, method) VALUES (?,?,?,?,?,?,?,?,?)",
                       [(campaign_id, key, s_.get("source", "tavily_web"), s_["platform"], w["profiles"].get(s_["platform"]) or w["name"],
                         s_["query"], s_["source_url"], s_["evidence"], s_["method"]) for s_ in w["sources"]])

    # Twitch / Instagram candidates (ranked per platform); accounts keep any link to a YouTube creator made above
    for pid, (plat, prof, fit, f) in platform_rows.items():
        (_save_twitch_account if plat == "twitch" else _save_instagram_account)(prof, None)
        db.execute("INSERT OR REPLACE INTO creators VALUES (?,?,?,?,?)",
                   (pid, prof.get("name") or pid.split(":", 1)[1], plat, None, db.now()))
        db.executemany("INSERT OR REPLACE INTO features VALUES (?,?,?,?,?,?)",
                       [(campaign_id, pid, k, float(v), _method(k), None) for k, v in f.items() if v is not None])
        feat = ("tw" if plat == "twitch" else "ig") + "_audience_relevance"
        db.executemany("INSERT INTO evidence (campaign_id, creator_id, feature, content_id, quote, verified)"
                       " VALUES (?,?,?,?,?,?)",
                       [(campaign_id, pid, feat, prof["url"], e["quote"], int(e["verified"]))
                        for e in (fit or {}).get("evidence", [])])
        if plat == "instagram":
            db.execute("INSERT INTO discoveries (campaign_id, creator_key, source, platform, handle, query, url,"
                       " evidence, method) VALUES (?,?,?,?,?,?,?,?,?)",
                       (campaign_id, pid, "instagram_graph_api", "instagram", prof["username"], None, prof["url"],
                        "handle from YouTube / web / Twitch profile, numbers verified", "business_discovery"))
    # Twitch-found streamers: how they were found (also for those whose YouTube channel was not analysed)
    for tp in twitch_only:
        _save_twitch_account(tp, None)
        db.execute("INSERT INTO discoveries (campaign_id, creator_key, source, platform, handle, query, url,"
                   " evidence, method) VALUES (?,?,?,?,?,?,?,?,?)",
                   (campaign_id, "twitch:" + tp["login"].lower(), "twitch_api", "twitch", tp["login"],
                    next((i["query"] for i in identities if i.get("source") == "twitch_api"
                          and i["handle"] == tp["login"]), None), tp["url"], tp["game"], "twitch_api"))

    # 7. Default ranking (the dashboard re-ranks live with other weights)
    rank_and_store(campaign_id, spec.goal, {r["creator_id"]: r for r in results.values()})
    for plat in ("twitch", "instagram"):
        rank_platform_and_store(campaign_id, plat, spec.goal,
                                {pid: {"summary": (fit or {}).get("summary", "")}
                                 for pid, (pl, _, fit, _) in platform_rows.items() if pl == plat})
    stats = {
        "duration_s": round(time.time() - t0, 1),
        "youtube_quota_units": yt.QuotaUsage.units,
        "web_search_provider": web.provider() if config.WEB_DISCOVERY_ENABLED else None,
        "openai_web_search_calls": web.OpenAISearchUsage.calls, "tavily_calls": web.TavilyUsage.calls,
        "twitch_calls": twitch.TwitchUsage.calls, "twitch_profiles": len(twitch_profiles),
        "twitch_matched_channels": len(twitch_matched), "twitch_only_creators": len(twitch_only),
        "instagram_calls": instagram.InstagramUsage.calls, "instagram_profiles": len(ig_profiles),
        "platform_candidates": {pl: sum(r[0] == pl for r in platform_rows.values()) for pl in ("twitch", "instagram")},
        "text_llm_attempts": dict(llm.calls_by_model), "text_llm_cache_hits": llm.cache_hits,
        "vlm_attempts": dict(vlm.calls_by_model), "vlm_cache_hits": vlm.cache_hits,
        "transcripts": dict(yt.TranscriptStatus.counts), "transcripts_blocked": yt.TranscriptStatus.blocked,
        "web_identities": n_identities, "web_matched_channels": len(web_matches), "web_only_creators": len(web_only),
        "creators_analysed": len(results), "vlm_failures": vlm_failures[:10],
        "disabled_models": llm_dead_models(),
        "missing_env": sorted(set(llm_missing_keys(config.LLM_MODELS) + llm_missing_keys(config.VLM_MODELS)
                                  + ([] if web.provider() else ["AZURE_OPENAI_API_KEY, OPENAI_API_KEY or TAVILY_API_KEY"])
                                  + ([] if twitch.available() else ["TWITCH_CLIENT_ID", "TWITCH_CLIENT_SECRET"])
                                  + ([] if instagram.available() else ["INSTAGRAM_ACCESS_TOKEN"]))),
        "errors": errors[:20],
    }
    db.execute("INSERT OR REPLACE INTO run_stats VALUES (?,?)", (campaign_id, json.dumps(stats)))
    p(f"Done: {len(results)} creators analysed · {llm.calls} text-LLM calls ({llm.cache_hits} cached) · "
      f"{vlm.calls} VLM calls · {web.OpenAISearchUsage.calls} model web searches · {web.TavilyUsage.calls} Tavily calls · {twitch.TwitchUsage.calls} Twitch calls · ~{yt.QuotaUsage.units} YouTube quota units",
      1.0)
    return campaign_id


def _save_twitch_account(tp: dict, creator_id: str | None) -> None:
    if creator_id is None:  # keep a link to a YouTube creator made in an earlier campaign
        prev = db.query("SELECT creator_id FROM platform_accounts WHERE platform='twitch' AND lower(handle)=?",
                        (tp["login"].lower(),))
        creator_id = prev[0]["creator_id"] if prev else None
    db.execute("INSERT OR REPLACE INTO platform_accounts VALUES (?,?,?,?,?,?,?,?,?,?)",
               ("twitch:" + tp["login"].lower(), creator_id, "twitch", tp["login"], tp["url"], tp.get("followers"),
                "twitch_api", TwitchCollector.source_reliability,
                json.dumps({k: tp.get(k) for k in ("name", "avatar", "description", "language", "game",
                                                   "n_vods", "median_vod_views", "last_stream_at")}), db.now()))


def _save_instagram_account(ip: dict, creator_id: str | None) -> None:
    if creator_id is None:
        prev = db.query("SELECT creator_id FROM platform_accounts WHERE platform='instagram' AND lower(handle)=?",
                        (ip["username"].lower(),))
        creator_id = prev[0]["creator_id"] if prev else None
    db.execute("INSERT OR REPLACE INTO platform_accounts VALUES (?,?,?,?,?,?,?,?,?,?)",
               ("instagram:" + ip["username"].lower(), creator_id, "instagram", ip["username"], ip["url"],
                ip.get("followers"), "instagram_graph_api", InstagramCollector.source_reliability,
                json.dumps({k: ip.get(k) for k in ("name", "avatar", "biography", "media_count", "n_posts",
                                                   "median_likes", "median_comments", "engagement_rate",
                                                   "posts_last_30d", "last_post_at")}), db.now()))


def _method(name: str) -> str:
    if name.startswith("twitch_"):
        return "twitch_api"
    if name.startswith("instagram_"):
        return "instagram_api"
    if name in fx.CONTENT_FEATURES:
        return "llm"
    if name.startswith("visual_") or name == "n_visual_images":
        return "vlm"
    if name in {"meaningful_ratio", "technical_question_ratio", "purchase_intent_ratio", "spam_ratio",
                "target_lang_share", "n_classified_comments"}:
        return "llm_comments"
    return "stat"


def load_features(campaign_id: str, prefix: str = "yt:") -> dict[str, dict]:
    """Features of one platform's candidates (creator ids are prefixed: yt: / twitch: / instagram:)."""
    out: dict[str, dict] = {}
    for row in db.query("SELECT creator_id, name, value FROM features WHERE campaign_id=? AND creator_id LIKE ?",
                        (campaign_id, prefix + "%")):
        out.setdefault(row["creator_id"], {})[row["name"]] = row["value"]
    return out


def rank_and_store(campaign_id: str, preset: str, extra: dict | None = None) -> None:
    import pandas as pd
    feats = load_features(campaign_id)
    w_groups, _ = ranking.ahp_weights(ranking.AHP_PRESETS[preset])
    weights = ranking.criterion_weights(dict(zip(ranking.GROUPS, w_groups)), preset=preset)
    passed = {cid: ranking.hard_filter(f) for cid, f in feats.items()}
    ok = [cid for cid, (p, _) in passed.items() if p]
    df = pd.DataFrame({cid: feats[cid] for cid in ok}).T.reindex(columns=[c.name for c in ranking.CRITERIA])
    res = ranking.topsis(df, weights) if len(df) else pd.DataFrame()
    previous = {r["creator_id"]: json.loads(r["breakdown_json"] or "{}")
                for r in db.query("SELECT creator_id, breakdown_json FROM rankings WHERE campaign_id=?", (campaign_id,))}
    db.execute("DELETE FROM rankings WHERE campaign_id=? AND creator_id LIKE 'yt:%'", (campaign_id,))
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


def rank_platform_and_store(campaign_id: str, platform: str, preset: str, meta: dict | None = None) -> None:
    import pandas as pd
    crits = ranking.PLATFORM_CRITERIA[platform]
    feats = load_features(campaign_id, platform + ":")
    w_groups, _ = ranking.ahp_weights(ranking.AHP_PRESETS[preset])
    weights = ranking.criterion_weights(dict(zip(ranking.GROUPS, w_groups)), crits, preset)
    passed = {cid: ranking.platform_hard_filter(platform, f) for cid, f in feats.items()}
    ok = [cid for cid, (p_ok, _) in passed.items() if p_ok]
    df = pd.DataFrame({cid: feats[cid] for cid in ok}).T.reindex(columns=[c.name for c in crits])
    res = ranking.topsis(df, weights, crits) if ok else pd.DataFrame()
    db.execute("DELETE FROM rankings WHERE campaign_id=? AND creator_id LIKE ?", (campaign_id, platform + ":%"))
    for cid, f in feats.items():
        p_ok, reason = passed[cid]
        m = dict((meta or {}).get(cid, {}), platform=platform)
        if p_ok:
            m["explain"] = ranking.explain(res.loc[cid], res.attrs["weights"])
        db.execute("INSERT INTO rankings VALUES (?,?,?,?,?,?,?,?)",
                   (campaign_id, cid, int(res.loc[cid, "rank"]) if p_ok else None,
                    float(res.loc[cid, "score"]) if p_ok else None, ranking.platform_confidence(platform, f),
                    int(p_ok), reason, json.dumps(m)))


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
