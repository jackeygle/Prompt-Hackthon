"""Web / cross-platform creator discovery via Tavily.

Tavily results  ->  creator identities (deterministic URL parsing + one LLM call for names in articles)
                ->  resolved to YouTube channels where possible (YouTube stays the metrics source)
                ->  the rest are kept as web-only creators (profile links + evidence, no metrics, not ranked).

No scraping of TikTok/Instagram: only search-result URLs, titles and snippets are used.
"""
import hashlib
import json
import logging
import re

import requests

import config
import db
import youtube as yt
from collectors import SOCIAL_PATTERNS, _IGNORE
from llm import LLMClient
from models import CampaignSpec, WebCreatorMentions

log = logging.getLogger(__name__)


class WebDiscoveryError(RuntimeError):
    pass


class TavilyUsage:
    calls = 0  # real (non-cached) API calls; basic search = 1 credit each


def default_web_queries(spec: CampaignSpec) -> list[str]:
    """Used when the spec has no web_queries (e.g. older campaigns)."""
    kw = spec.niche
    country = {"DE": "Deutschland", "AT": "Österreich", "CH": "Schweiz"}.get(spec.target_country, spec.target_country)
    return [f"beste {kw} YouTuber {country}", f"{spec.product} Influencer {country}",
            f"site:tiktok.com {kw} {spec.target_language}", f"site:instagram.com {kw} {country}",
            f"site:twitch.tv {kw} {spec.target_language}", f"site:youtube.com {spec.product} Test"]


def tavily_search(query: str, max_results: int = config.TAVILY_RESULTS_PER_QUERY) -> list[dict]:
    if not config.TAVILY_API_KEY:
        raise WebDiscoveryError("environment variable TAVILY_API_KEY is not set")
    key = "tv:" + hashlib.sha256(json.dumps([query, max_results]).encode()).hexdigest()
    if (hit := db.cache_get(key)) is not None:
        return hit
    try:
        r = requests.post("https://api.tavily.com/search", timeout=30,
                          headers={"Authorization": f"Bearer {config.TAVILY_API_KEY}"},
                          json={"query": query, "max_results": max_results, "search_depth": "basic",
                                "include_answer": False, "include_raw_content": False})
    except requests.RequestException as e:
        raise WebDiscoveryError(f"Tavily unreachable: {e}") from e
    TavilyUsage.calls += 1
    if r.status_code != 200:
        raise WebDiscoveryError(f"Tavily HTTP {r.status_code}: {r.text[:200]}")
    results = [{"url": x.get("url", ""), "title": x.get("title", ""), "content": (x.get("content") or "")[:600]}
               for x in r.json().get("results", [])]
    db.cache_put(key, results)
    return results


YT_PATTERNS = [(r"youtube\.com/@([A-Za-z0-9_.\-]{3,30})", "handle"),
               (r"youtube\.com/channel/(UC[A-Za-z0-9_-]{22})", "channel_id")]


def _norm(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def identities_from_url_text(text: str) -> list[dict]:
    out = []
    for pat, kind in YT_PATTERNS:
        for m in re.finditer(pat, text, flags=re.I):
            out.append({"platform": "youtube", "handle": m.group(1), "kind": kind})
    for platform, pat in SOCIAL_PATTERNS.items():
        for m in re.finditer(pat, text, flags=re.I):
            h = m.group(1).rstrip(".")
            if h.lower() not in _IGNORE:
                out.append({"platform": platform, "handle": h, "kind": "handle"})
    return out


MENTION_SYSTEM = """You extract content creators (YouTubers, TikTokers, streamers, Instagram creators) mentioned in
web search results. Only include individual creators or creator channels, not brands, shops, magazines or
companies. Use only information present in the results. Do not rank or judge them."""


def discover(spec: CampaignSpec, llm: LLMClient, progress=None) -> tuple[list[dict], list[str]]:
    """Returns (identities, errors). Each identity: platform, handle, name, url, query, source_url, evidence, method."""
    queries = (spec.web_queries or default_web_queries(spec))[:config.TAVILY_MAX_QUERIES]
    identities, errors, results = [], [], []
    for i, q in enumerate(queries):
        if progress:
            progress(f"Web search (Tavily): “{q}”", i / len(queries))
        try:
            res = tavily_search(q)
        except WebDiscoveryError as e:
            errors.append(str(e))
            log.warning("web discovery: %s", e)
            if "not set" in str(e) or "unreachable" in str(e) or "401" in str(e) or "432" in str(e):
                break  # no key / blocked / plan limit: stop spending time, YouTube-only continues
            continue
        for r in res:
            r["query"] = q
            results.append(r)
            for ident in identities_from_url_text(r["url"] + "\n" + r["content"]):
                identities.append({**ident, "name": None, "url": r["url"], "query": q, "source_url": r["url"],
                                   "evidence": r["title"][:160], "method": "url"})

    # One LLM call to pull creator names out of articles/lists (results without a profile URL)
    articles = [r for r in results if not identities_from_url_text(r["url"])][:25]
    if articles:
        listing = "\n\n".join(f"[{j}] {r['title']}\n{r['content']}" for j, r in enumerate(articles))
        try:
            out = llm.extract(WebCreatorMentions, MENTION_SYSTEM,
                              f"Niche: {spec.niche}\nCountry: {spec.target_country}\n\nRESULTS:\n{listing}")
            for m in out.creators:
                if not (0 <= m.source_index < len(articles)):
                    continue
                src = articles[m.source_index]
                if _norm(m.name) not in _norm(src["title"] + src["content"]):
                    continue  # name not actually in the cited result -> drop (hallucination guard)
                identities.append({"platform": m.platform, "handle": m.handle, "kind": "mention", "name": m.name,
                                   "url": None, "query": src["query"], "source_url": src["url"],
                                   "evidence": m.evidence[:160], "method": "llm_mention"})
        except Exception as e:
            errors.append(f"mention extraction failed: {e}")
    return identities, errors


def resolve_to_youtube(identities: list[dict], chans: dict[str, dict]) -> tuple[dict[str, list[dict]], list[dict]]:
    """Merge identities into YouTube channels.

    chans: channel_id -> channels.list item (mutated: newly resolved channels are added).
    Returns (channel_id -> identities, web_only identities grouped per creator).
    """
    def index():
        idx = {}
        for cid, c in chans.items():
            sn = c["snippet"]
            for k in (sn.get("title"), (sn.get("customUrl") or "").lstrip("@")):
                if _norm(k):
                    idx.setdefault(_norm(k), cid)
        return idx

    # 1. direct YouTube channel ids / handles
    ids = {i["handle"] for i in identities if i["platform"] == "youtube" and i["kind"] == "channel_id"}
    new_ids = [x for x in ids if x not in chans]
    for c in yt.channels(new_ids) if new_ids else []:
        chans[c["id"]] = c
    lookups = 0
    by_handle = index()
    for i in identities:
        if i["platform"] == "youtube" and i["kind"] == "handle" and _norm(i["handle"]) not in by_handle \
                and lookups < config.MAX_WEB_HANDLE_LOOKUPS:
            lookups += 1
            try:
                if c := yt.channel_by_handle(i["handle"]):
                    chans[c["id"]] = c
                    by_handle = index()
            except yt.YouTubeError as e:
                log.info("handle lookup %s: %s", i["handle"], e)

    # 2. match everything else by normalised handle/name; try a cheap forHandle lookup for the rest
    matched: dict[str, list[dict]] = {}
    unmatched = []
    for i in identities:
        if i["platform"] == "youtube" and i["kind"] == "channel_id":
            cid = i["handle"] if i["handle"] in chans else None
        else:
            cid = by_handle.get(_norm(i["handle"])) or by_handle.get(_norm(i["name"]))
        if cid:
            matched.setdefault(cid, []).append(i)
        else:
            unmatched.append(i)
    still = []
    tried = set()
    for i in unmatched:
        guess = i["handle"] or (i["name"] or "").replace(" ", "")
        key = _norm(guess)
        if len(key) < 4 or key in tried or lookups >= config.MAX_WEB_HANDLE_LOOKUPS:
            still.append(i)
            continue
        tried.add(key)
        lookups += 1
        try:
            c = yt.channel_by_handle(guess)
        except yt.YouTubeError:
            c = None
        title_n = _norm(c["snippet"]["title"]) if c else ""
        if c and (key in title_n or title_n in key) and min(len(title_n), len(key)) >= 4:
            chans[c["id"]] = c
            by_handle = index()
            matched.setdefault(c["id"], []).append(i)
        else:
            still.append(i)

    # group web-only identities per creator (same normalised handle/name)
    web_only: dict[str, dict] = {}
    for i in still:
        k = _norm(i["handle"] or i["name"])
        if len(k) < 3:
            continue
        g = web_only.setdefault(k, {"name": i["name"] or i["handle"], "profiles": {}, "sources": []})
        if i["handle"] and i["platform"] not in ("unknown", "website"):
            g["profiles"][i["platform"]] = i["handle"]
        g["sources"].append({k2: i[k2] for k2 in ("query", "source_url", "evidence", "method", "platform")})
    return matched, list(web_only.values())
