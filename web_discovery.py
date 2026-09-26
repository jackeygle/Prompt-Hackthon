"""Web / cross-platform creator discovery: ChatGPT web search (default) or Tavily.

ChatGPT (OpenAI Responses API + web_search tool) -> nominated creators, each with a profile URL or a cited source;
                nominations without either are dropped (the model only nominates, it never supplies metrics)
Tavily results  ->  creator identities (deterministic URL parsing + one LLM call for names in articles)
                ->  resolved to YouTube channels where possible (YouTube stays the metrics source)
                ->  the rest are kept as web-only creators (profile links + evidence, no metrics, not ranked).

No scraping of TikTok/Instagram: only search-result URLs, titles and snippets are used.
"""
import hashlib
import json
import logging
import re
from datetime import datetime, timezone

import requests

import config
import db
import youtube as yt
from collectors import SOCIAL_PATTERNS, _IGNORE
from llm import LLMClient
from models import CampaignSpec, WebCreatorMentions, WebSearchCreators

log = logging.getLogger(__name__)


class WebDiscoveryError(RuntimeError):
    pass


class TavilyUsage:
    calls = 0  # real (non-cached) API calls; basic search = 1 credit each


class OpenAISearchUsage:
    calls = 0  # real (non-cached) Responses API calls with the web_search tool


def provider() -> str | None:
    """Configured web-search provider, falling back to the other one when its key is missing."""
    have = {"openai": bool(config.OPENAI_API_KEY), "tavily": bool(config.TAVILY_API_KEY)}
    first = config.WEB_SEARCH_PROVIDER if config.WEB_SEARCH_PROVIDER in have else "openai"
    return next((p for p in (first, *have) if have[p]), None)


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


# ------------------------------------------------------------------ ChatGPT web search
SEARCH_FOCUS = [("YouTube", "YouTube channels (lists of the best creators, recommendations, rankings)"),
                ("TikTok and Instagram", "TikTok and Instagram creators"),
                ("Twitch", "Twitch streamers")]
SEARCH_PROMPT = """Use web search to find individual content creators for an influencer campaign.
Product: {product}
Target audience: {audience}
Audience interests: {interests}
Market: {country}; the creators should publish in language '{lang}'.
Focus: {focus}.

Find up to {n} real individual creators (not brands, shops, magazines or agencies) whose audience matches.
Only include creators you actually found in search results. Never invent names, handles or URLs; if you do not
know a creator's exact handle, use null. Do not rank or rate them.
Answer with ONLY this JSON, no other text:
{{"creators": [{{"name": "...", "platform": "youtube|tiktok|instagram|twitch|x|unknown", "handle": "handle without @ or null",
"profile_url": "creator's profile URL or null", "source_url": "URL of the page where you found them",
"evidence": "short phrase from that page (<= 20 words)"}}]}}"""


def _response_text(data: dict) -> tuple[str, set[str]]:
    """Output text + every URL the model cited (url_citation annotations)."""
    text, cited = [], set()
    for item in data.get("output", []):
        for c in item.get("content", []) or []:
            if c.get("type") == "output_text":
                text.append(c.get("text", ""))
                cited |= {a.get("url", "") for a in c.get("annotations", []) if a.get("type") == "url_citation"}
    return "\n".join(text), {u for u in cited if u}


def _json_block(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise WebDiscoveryError("ChatGPT web search returned no JSON")
    return json.loads(text[start:end + 1])


def _url_key(u: str | None) -> str:
    return re.sub(r"^https?://(www\.)?|[?#].*$|/+$", "", (u or "").strip().lower())


def openai_web_search(prompt: str) -> tuple[dict, set[str]]:
    """One Responses API call with the web_search tool (cached per day). Returns (parsed JSON, cited URLs)."""
    if not config.OPENAI_API_KEY:
        raise WebDiscoveryError("environment variable OPENAI_API_KEY is not set")
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    key = "oaws:" + hashlib.sha256(json.dumps([config.WEB_SEARCH_MODEL, prompt, day]).encode()).hexdigest()
    if (hit := db.cache_get(key)) is not None:
        return hit["data"], set(hit["cited"])
    last = ""
    for tool in ("web_search", "web_search_preview"):  # GA name first, older accounts/models use the preview name
        try:
            r = requests.post("https://api.openai.com/v1/responses", timeout=120,
                              headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}"},
                              json={"model": config.WEB_SEARCH_MODEL, "tools": [{"type": tool}], "input": prompt})
        except requests.RequestException as e:
            raise WebDiscoveryError(f"OpenAI unreachable: {e}") from e
        OpenAISearchUsage.calls += 1
        if r.status_code == 200:
            text, cited = _response_text(r.json())
            data = _json_block(text)
            db.cache_put(key, {"data": data, "cited": sorted(cited)})
            return data, cited
        last = f"OpenAI web search HTTP {r.status_code}: {r.text[:200]}"
        if r.status_code != 400:
            break
    raise WebDiscoveryError(last)


def discover_openai(spec: CampaignSpec, progress=None) -> tuple[list[dict], list[str]]:
    """ChatGPT nominates creators. Kept only with a profile URL (parsed deterministically) or a source URL the model
    actually cited; everything is verified later against the official YouTube / Twitch / Instagram APIs."""
    identities, errors = [], []
    country = {"DE": "Germany", "AT": "Austria", "CH": "Switzerland"}.get(spec.target_country, spec.target_country)
    for i, (label, focus) in enumerate(SEARCH_FOCUS[:config.OPENAI_SEARCH_CALLS]):
        q = f"ChatGPT web search: {label} creators · {spec.niche} · {country}"
        if progress:
            progress(f"Web search (ChatGPT): {label} creators", i / len(SEARCH_FOCUS))
        prompt = SEARCH_PROMPT.format(product=spec.product, audience=spec.audience, country=country,
                                      interests=", ".join(spec.audience_interests) or spec.niche,
                                      lang=spec.target_language, focus=focus, n=config.OPENAI_SEARCH_CREATORS)
        try:
            data, cited = openai_web_search(prompt)
            found = WebSearchCreators.model_validate(data).creators
        except Exception as e:
            errors.append(str(e)[:200])
            log.warning("ChatGPT web search: %s", e)
            if "not set" in str(e) or "unreachable" in str(e) or "401" in str(e) or "429" in str(e):
                break
            continue
        cited_keys = {_url_key(u) for u in cited}
        for c in found:
            from_url = identities_from_url_text(c.profile_url or "")
            source_ok = bool(c.source_url) and _url_key(c.source_url) in cited_keys
            if not from_url and not source_ok:
                continue  # neither a profile URL nor a cited source: an unverifiable nomination
            base = {"name": c.name, "url": c.profile_url, "query": q, "source_url": c.source_url or c.profile_url,
                    "evidence": (c.evidence or c.name)[:160], "method": "openai_web_search", "source": "openai_web"}
            if from_url:
                identities += [{**base, **ident} for ident in from_url]
            elif c.handle and c.platform in ("youtube", "tiktok", "instagram", "twitch", "x"):
                identities.append({**base, "platform": c.platform, "handle": c.handle.lstrip("@"), "kind": "handle"})
            else:
                identities.append({**base, "platform": c.platform, "handle": None, "kind": "mention"})
    return identities, errors


def discover(spec: CampaignSpec, llm: LLMClient, progress=None) -> tuple[list[dict], list[str]]:
    """Returns (identities, errors) from the configured provider (ChatGPT by default, Tavily as fallback)."""
    which = provider()
    if which is None:
        return [], ["no web search provider: set OPENAI_API_KEY (ChatGPT) or TAVILY_API_KEY"]
    if which == "openai":
        identities, errors = discover_openai(spec, progress)
        if identities or not config.TAVILY_API_KEY:
            return identities, errors
        log.warning("ChatGPT web search found nothing, falling back to Tavily")
        more, errs = discover_tavily(spec, llm, progress)
        return more, errors + errs
    return discover_tavily(spec, llm, progress)


def discover_tavily(spec: CampaignSpec, llm: LLMClient, progress=None) -> tuple[list[dict], list[str]]:
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
        # a Twitch login guessed as a YouTube handle can belong to someone else: the channel title must equal
        # the streamer's display name or login (no containment)
        strict = i.get("source") == "twitch_api"
        if c and (title_n in (key, _norm(i["name"])) if strict else
                  (key in title_n or title_n in key) and min(len(title_n), len(key)) >= 4):
            chans[c["id"]] = c
            by_handle = index()
            matched.setdefault(c["id"], []).append(i)
        else:
            still.append(i)

    # group web-only identities per creator (same normalised handle/name)
    web_only: dict[str, dict] = {}
    for i in still:
        if i.get("source") == "twitch_api":
            continue  # Twitch-only streamers are listed separately with their real metrics
        k = _norm(i["handle"] or i["name"])
        if len(k) < 3:
            continue
        g = web_only.setdefault(k, {"name": i["name"] or i["handle"], "profiles": {}, "sources": []})
        if i["handle"] and i["platform"] not in ("unknown", "website"):
            g["profiles"][i["platform"]] = i["handle"]
        g["sources"].append({**{k2: i[k2] for k2 in ("query", "source_url", "evidence", "method", "platform")},
                             "source": i.get("source", "tavily_web")})
    return matched, list(web_only.values())
