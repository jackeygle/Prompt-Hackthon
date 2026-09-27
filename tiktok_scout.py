"""TikTok Scout: a small set of TikTok research leads for a human marketer to review. Not TikTok analytics.

HARD RULES (enforced by this module's design, not only by prompts):
- No TikTok scraping, crawling or HTML/DOM extraction, and no request of any kind to tiktok.com from this code.
  TikTok URLs are only validated with a regular expression; the marketer opens them in their own browser.
- No TikTok browser automation (Selenium / Playwright / screenshots / OCR), no CAPTCHA, login-wall or anti-bot
  circumvention, no undocumented or reverse-engineered TikTok APIs. If TikTok restricts something, we stop.
- No automated TikTok analytics: no followers, views, comments, engagement, demographics or scores are extracted
  or inferred. A lead is a handle, its link, why it surfaced and where the link came from. Nothing more.
- No bulk profiling and no TikTok creator database: leads are stored per campaign (table `tiktok_leads`) and are
  deleted with the campaign.
- Human review is required: nothing is shortlisted automatically. Only the marketer's explicit "Add to shortlist"
  (or a URL they paste themselves) puts a TikTok creator on the shortlist.
- Public visibility is not permission: TikTok oEmbed and any deeper access stay DISABLED until Prenew confirms the
  commercial use is allowed under TikTok's terms. Only an official, approved integration may later be plugged in
  as another `ScoutProvider`.

The only automated step is public web research with the EXISTING model web search (Responses API + web_search
tool, gpt-5.6-sol on Azure or OpenAI). No other search provider is used here.
"""
import re
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import unquote, urlparse

import db
import web_discovery as web
from models import CampaignSpec

MAX_LEADS = 8

# tiktok.com/@handle or tiktok.com/@handle/video/<id>; anything else (shop, product, tag, search, music, discover,
# vm.tiktok.com short links without a visible handle, look-alike domains) is not a creator lead.
_HOSTS = {"tiktok.com", "www.tiktok.com", "m.tiktok.com"}
_PATH = re.compile(r"^/@([A-Za-z0-9_.]{2,24})(?:/video/(\d{5,25}))?/?$")


@dataclass(frozen=True)
class TikTokRef:
    handle: str
    url: str              # the creator/content link as given (query string removed)
    profile_url: str      # https://www.tiktok.com/@handle
    video_id: str | None = None


def parse_tiktok_url(url: str | None) -> TikTokRef | None:
    """Pure string validation. Never fetches the URL."""
    raw = (url or "").strip()
    if not raw:
        return None
    if not re.match(r"^https?://", raw, re.I):
        raw = "https://" + raw
    try:
        u = urlparse(raw)
    except ValueError:
        return None
    if (u.hostname or "").lower() not in _HOSTS:
        return None
    m = _PATH.match(u.path)
    if not m:
        return None
    handle = m.group(1).lower()
    profile = f"https://www.tiktok.com/@{handle}"
    clean = profile + (f"/video/{m.group(2)}" if m.group(2) else "")
    return TikTokRef(handle=handle, url=clean, profile_url=profile, video_id=m.group(2))


@dataclass
class Lead:
    handle: str
    url: str
    topic: str                 # why it surfaced: the campaign topic it relates to
    source_url: str            # where the link came from (the cited page, or the TikTok link itself)
    verified: bool             # the link or its source was actually cited by the web search
    via: str = ""              # "tiktok link" | "article"


class ScoutProvider(Protocol):
    """Where leads come from. Current: public web research for human review. Future: only an official, approved
    TikTok integration may implement this interface; unofficial access must never be added."""
    name: str

    def find_leads(self, spec: CampaignSpec, n: int = MAX_LEADS) -> tuple[list[Lead], list[str]]: ...


SCOUT_PROMPT = """Use web search to find a FEW TikTok creators a marketer should review by hand for this campaign.
Product: {product}
Target audience: {audience}
Market: {country}; language '{lang}'.
Current topics this audience follows: {topics}

""" + web.AUDIENCE_LENS + """

Search content-first: for the current topics, find publicly indexed TikTok creator profiles or videos
(tiktok.com/@handle or tiktok.com/@handle/video/...) from creators who publish about them for this market.
At most two leads may come from editorial "top TikTok creators" articles. Never return TikTok Shop, product,
tag, search or music pages. Only include links you actually saw in search results; never invent handles.
Do not describe follower counts, views or audiences.
Answer with ONLY this JSON, no other text:
{{"leads": [{{"tiktok_url": "https://www.tiktok.com/@handle or .../video/...", "topic": "which current topic it relates to",
"found_via": "tiktok link|article", "source_url": "URL of the page where you found it"}}]}}"""


@dataclass
class WebResearchScout:
    """Current provider: public web research only, via the existing model web search (never Tavily, never TikTok)."""
    name: str = "public web research"
    errors: list[str] = field(default_factory=list)

    def find_leads(self, spec: CampaignSpec, n: int = MAX_LEADS) -> tuple[list[Lead], list[str]]:
        providers = [p for p in web.available_providers() if p in web.MODEL_SEARCH]
        if not providers:
            return [], ["TikTok Scout needs the model web search (Azure gpt-5.6-sol or OpenAI key)."]
        prompt = SCOUT_PROMPT.format(
            product=spec.product, audience=spec.audience, lang=spec.target_language,
            country=web.COUNTRY_NAME.get(spec.target_country, spec.target_country),
            topics=", ".join(t.name for t in spec.current_topics) or ", ".join(spec.audience_interests) or spec.niche)
        errors = []
        for via in providers:
            try:
                data, cited = web.openai_web_search(prompt, via=via)
            except Exception as e:  # report, never work around a failure
                errors.append(f"{via}: {str(e)[:160]}")
                continue
            return select_leads(data.get("leads") or [], cited, n), errors
        return [], errors


def select_leads(raw: list[dict], cited: set[str], n: int = MAX_LEADS) -> list[Lead]:
    """Keep valid creator links only, one per handle, at most two from articles, at most n in total."""
    retrieved = [unquote(u) for u in cited]  # pages the web search cited or actually retrieved
    cited_keys = {web._url_key(u) for u in retrieved}
    seen_handles = {r.handle for r in map(parse_tiktok_url, retrieved) if r}
    out: list[Lead] = []
    seen: set[str] = set()
    articles = 0
    for item in raw:
        ref = parse_tiktok_url(item.get("tiktok_url"))
        if ref is None or ref.handle in seen:
            continue
        via = "article" if str(item.get("found_via", "")).startswith("article") else "tiktok link"
        if via == "article":
            if articles >= 2:
                continue  # listicles are a secondary path
            articles += 1
        source = item.get("source_url") or ref.url
        verified = ref.handle in seen_handles or web._url_key(source) in cited_keys
        out.append(Lead(handle=ref.handle, url=ref.url, topic=str(item.get("topic") or "")[:80],
                        source_url=source, verified=verified, via=via))
        seen.add(ref.handle)
        if len(out) >= n:
            break
    return out


def provider() -> ScoutProvider:
    # Future: an official, TikTok-approved commercial integration may replace this. oEmbed stays off (see above).
    return WebResearchScout()


# ------------------------------------------------------------------ campaign-scoped storage (only what the workflow needs)
PREFIX = "tiktok:"  # shortlist creator_id for a human-selected TikTok lead


def save_suggestions(campaign_id: str, leads: list[Lead]) -> int:
    """New suggestions only; a lead the marketer already ignored or added keeps its decision."""
    before = len(leads_for(campaign_id, "suggested"))
    db.executemany("INSERT OR IGNORE INTO tiktok_leads VALUES (?,?,?,?,?,?,?,?,?,?)",
                   [(campaign_id, l.handle, l.url, None, l.topic, l.source_url, int(l.verified), l.via, "suggested",
                     db.now()) for l in leads])
    return len(leads_for(campaign_id, "suggested")) - before


def add_manual(campaign_id: str, url: str, name: str | None = None) -> TikTokRef | None:
    """A creator the marketer already knows. Validated as a URL only; nothing is fetched."""
    ref = parse_tiktok_url(url)
    if ref is None:
        return None
    db.execute("INSERT OR REPLACE INTO tiktok_leads VALUES (?,?,?,?,?,?,?,?,?,?)",
               (campaign_id, ref.handle, ref.url, (name or "").strip()[:80] or None, "Added by you", ref.url, 0,
                "manual", "suggested", db.now()))
    import relationships  # the one shortlist (order / favorites) shared with every platform
    relationships.add_to_shortlist(campaign_id, PREFIX + ref.handle)
    return ref


def set_status(campaign_id: str, handle: str, status: str) -> None:
    assert status in ("suggested", "ignored")
    db.execute("UPDATE tiktok_leads SET status=? WHERE campaign_id=? AND handle=?", (status, campaign_id, handle))


def leads_for(campaign_id: str, status: str | None = None) -> list[dict]:
    sql = "SELECT * FROM tiktok_leads WHERE campaign_id=?" + (" AND status=?" if status else "") + " ORDER BY created_at, rowid"
    return db.query(sql, (campaign_id, status) if status else (campaign_id,))


def lead(campaign_id: str, handle: str) -> dict | None:
    rows = db.query("SELECT * FROM tiktok_leads WHERE campaign_id=? AND handle=?", (campaign_id, handle))
    return rows[0] if rows else None
