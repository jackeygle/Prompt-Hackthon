# Creator Intelligence Engine (hackathon MVP)

Campaign brief → gpt-5.6-sol extraction → **current-topic web research** → search strategy (audience → current topics →
content → creator) → **human review/edit** → YouTube + gpt-5.6-sol web search + Twitch discovery →
identity merge → YouTube data collection → text / comment / thumbnail (VLM) features → hard filter →
**AHP + TOPSIS** → explainable Streamlit dashboard.

The LLM never ranks creators. It only turns unstructured text (titles, descriptions, transcripts,
comments) into structured, evidence-backed features. Ranking is deterministic, campaign-specific and
reported next to a separate **Data Confidence** score.

## Quick start

```bash
pip install -r requirements.txt
cp .env.example .env        # YOUTUBE_API_KEY + AZURE_OPENAI_* (gpt-5.6-sol) required; TAVILY/TWITCH/INSTAGRAM optional
streamlit run app.py        # Campaign → Continue → confirm details → Find creators → Discover → View analysis
                            # → Shortlist.
# or headless:
python pipeline.py "Find creators for €600–900 refurbished gaming PCs targeting gamers in Germany."
python -m pytest -q
python check_setup.py      # one tiny request per API: shows exactly which keys/hosts/models work
```

## Pipeline

| Stage | Module | Provider | Notes |
|---|---|---|---|
| Brief → `CampaignSpec` (+ audience interests) | `pipeline.parse_brief` | gpt-5.6-sol | 1 call; subscriber range and price only when stated in the brief, otherwise marked defaults (`field_sources`); campaign priority starts **Balanced** |
| Current-topic research | `web_discovery.research_current_topics` | gpt-5.6-sol + `web_search` (Tavily fallback) | resolves abstract interests into concrete current names the audience follows in the market (games, hardware, questions, events…); Tavily path keeps only names present in the results. Prenew-aware (gamers who could buy a gaming PC), follows any other campaign topic without adding gaming |
| Search strategy | `pipeline.plan_searches` | gpt-5.6-sol | topic/content searches built on those names (main path: the channel behind a relevant video becomes a candidate) + a few direct "top creator" searches (secondary, never more than half the content searches) + web queries |
| Campaign settings (review / edit) | `app.campaign_form` | – | market, language, price, audience + interests, **campaign priority** (Drive sales / Reach more gamers / Balanced), subscriber range, budget per creator video, **creators to analyze slider (1–50, default 10)**, researched topics, both search lists, seeds |
| YouTube search (market / language, last 12 months) | `youtube.py` | YouTube | 100 quota units per query |
| Web discovery (default) | `web_discovery.discover_openai` | Azure Responses API, gpt-5.6-sol + `web_search` tool (`WEB_SEARCH_PROVIDER=azure`) | 3 searches (YouTube / TikTok+Instagram / Twitch focus) seeded with the current topics; the model only **nominates**: each nomination needs a profile URL (parsed deterministically) or a source URL it actually cited, otherwise it is dropped; all are verified later via the official APIs |
| Web discovery (alternative) | `web_discovery.discover_tavily` | Tavily + Groq | `WEB_SEARCH_PROVIDER=tavily`, or automatic fallback when the model web search finds nothing / has no key: 6 basic searches (general + `site:` TikTok/Instagram/Twitch/YouTube); profile URLs parsed deterministically; 1 LLM call extracts creator names from articles (names must appear in the cited result) |
| Twitch discovery | `twitch.discover` | Twitch Helix (free) | live streams in the target language in audience-interest categories + top games + Science & Technology; live channel search; Tavily-found Twitch handles verified. ≤30 profiles: follower total, main category, median VOD views, last stream; follower range from spec |
| Identity merge / dedup | `web_discovery.resolve_to_youtube` | YouTube | handles/channel IDs/normalised names → YouTube channels (≤25 `forHandle` lookups); unmatched Twitch/Instagram accounts go to their platform ranking, the rest → *web-only* list (not ranked) |
| Cheap filter | `pipeline.run_campaign` | – | subscriber range from spec, country in market if declared; screens max(20, 2×creators) channels (≤100) by search hits + 2×web mentions |
| Upload screening | `features.relevant_video_ids` | gpt-5.6-sol | 1 call per channel over its last 30 titles; a video counts if its viewers plausibly belong to the target audience (product/niche **or** the audience's interests, e.g. PC gaming, game performance, tech) |
| Deep analysis (N creators, user-selected) | `features.py` | YouTube + gpt-5.6-sol | 5 relevant videos, transcript if available else title+description, 50 comments/video |
| Instagram numbers | `instagram.py` | Instagram Graph API (Business Discovery) | Instagram handles linked from YouTube descriptions (screened channels), Twitch bios and web search: followers, median likes/comments of the last 12 posts, engagement rate, posts in the last 30 days. Professional accounts only; feeds the Instagram ranking and the chip on YouTube cards |
| Twitch / Instagram rankings | `features.platform_fit` + `ranking.py` | gpt-5.6-sol + official APIs | every Twitch streamer and verified Instagram account in the follower range gets its **own ranking**: one batched LLM call judges audience relevance from the profile's own text (bio, stream titles, captions; verbatim quotes checked), numbers come from the APIs. Same AHP groups and campaign priority as YouTube, platform criteria below |
| Visual features | `vision.py` | gpt-5.6-sol | 1 call per creator, 4 official thumbnail URLs, `detail=low` |
| Hard filter → AHP → TOPSIS | `ranking.py` | – | deterministic; criteria with < 50% coverage are dropped and shown |
| UI | `app.py` + `ui.py` | – | Campaign / Discover / Shortlist; creator cards, analysis with evidence cards, live AHP re-weighting, staged loader driven by real pipeline progress |

AI models only extract features and evidence. They never compare or rank creators.

All YouTube and LLM responses are cached in SQLite (`api_cache`), so re-runs cost no quota.
`data/demo.db` is a committed snapshot without the raw cache; the UI tests run against a copy of it.

## Criteria (B = benefit, C = cost)

- **Campaign Fit**: **target-audience relevance** (0.30) · niche (0.20) · product (0.15) · price segment (0.10) (LLM) ·
  target-language comment share (0.25, proxy for audience country). Hard filter passes if niche **or** audience relevance ≥ 1.
- **Content Credibility**: first-hand experience, benchmarks, comparison, price discussion, purchase recommendation (LLM) ·
  product visible in thumbnails (VLM, sub-weight 0.10 → ~2% total weight; other visual tags are descriptive only)
- **Audience Quality**: meaningful / technical+question / purchase-intent comment ratios · creator reply rate
- **Reach & Performance**: log median relevant views · engagement rate · views/subscriber · view volatility (C)
- **Cost & Risk**: *Estimated Cost Proxy* = median relevant views × assumed €20 CPM (C) — **not a creator quote** ·
  spam ratio (C) · days since last relevant upload (C)

**Twitch** (own ranking): audience relevance of stream titles (LLM) · VOD views / followers · followers (log) ·
median VOD views (log) · streams in 30 days · days since last stream (C). Hard filter: streamed in the last 60 days,
relevance ≥ 1.
**Instagram** (own ranking): audience relevance of captions (LLM) · captions in target language · engagement rate ·
comments per like · followers (log) · median likes (log) · posts in 30 days · days since last post (C). Hard filter:
posted in the last 60 days, ≥ 3 visible posts, relevance ≥ 1. Scores are relative within each platform and are not
comparable across platforms; Data Confidence for these is capped at 0.9 (no comment analysis).

Missing values are imputed with the unfavourable quartile and lower confidence; columns are winsorised
(5/95) and vector-normalised.

## Data Confidence (separate from the campaign score)

`0.25·videos + 0.20·transcript coverage + 0.20·classified comments + 0.10·source reliability +
0.05·visual coverage + 0.10·recency + 0.10·stats completeness` (imputed criteria reduce the last term).

## TikTok Scout (human-in-the-loop)

`tiktok_scout.py`, the **TikTok Scout** tab on the creators page. A few TikTok *leads* for a marketer to review
on TikTok, not TikTok analytics:

Campaign → current topics → one gpt-5.6-sol web search (existing model web search, never Tavily) → ≤ 8 links to
TikTok creator profiles/videos (shop/tag/search pages and look-alike domains rejected, ≤ 2 from "top creator"
articles) → **the marketer opens TikTok, reviews, and explicitly clicks Add to shortlist** (or Ignore). A creator the
marketer already knows can be added by pasting their profile link.

- No request to tiktok.com from the code (links are validated with a regex only), no browser automation,
  scraping, oEmbed, unofficial APIs or circumvention; no followers/views/engagement/scores are extracted.
- A lead stores only handle, link, why it surfaced, where the link came from, whether the web search actually
  retrieved it ("could not be verified" otherwise) and the marketer's decision, per campaign (`tiktok_leads`,
  deleted with the campaign). Nothing is shortlisted automatically.
- Shortlisted TikTok creators appear as **Human-selected TikTok lead**, never with a score.
- `ScoutProvider` is the seam for a future official, TikTok-approved integration; oEmbed stays disabled until
  Prenew confirms the commercial use is permitted.

## Home and global sections

Home is an operational dashboard, not a landing page: **New creator search** (the 3-step brief → confirm → discover
flow), live **Shortlists** and **Sponsorships** cards (counts from the database; empty states when there is none)
and **Recent searches** with work state per search (found · shortlisted · sponsored). Shortlists and Sponsorships
are global sections in the header on every page:

- **Shortlists**: the existing `shortlist` table grouped by `campaign_id` ("By search"), or every entry in one table
  ("All creators": a creator shortlisted in two searches appears twice, each with that search's own score).
  "Open shortlist" enters the existing per-search shortlist (order, ★, Start sponsorship unchanged).
- **Sponsorships**: grouped by the search they came from (kept, labelled "report deleted", if the report was
  deleted); filters for search, status, market, platform and name combine and keep the grouping.
- Creators keep one canonical record (`creators`); scores stay per search (`rankings`); favorites are per
  creator × search (`shortlist.favorite`); sponsorships are separate records linked to both.

## Shortlist management and sponsorship tracking

`relationships.py` (business logic) + the **Shortlist** and **Sponsorships** sections:
Discovery → Ranking → Shortlist → Sponsorship → Performance → Historical creator relationship.

- **Shortlist**: manual order (↑ / ↓, persisted as `shortlist.position`) and ☆/★ favorites (`shortlist.favorite`,
  "Favorites first" view). Existing shortlists are migrated in place, in their old order.
- **Start sponsorship** (with confirmation) creates a `sponsorships` record for the existing creator and campaign;
  the creator stays on the shortlist. One active sponsorship per creator and campaign.
- **Sponsorships** (header button): active / completed / spend / tracked revenue from stored data only; filters for
  status, market, platform (only platforms with records) and name; per sponsorship: status (In progress / Done),
  price paid (EUR), KPIs, notes, and one outcome (👍 positive / not rated / 👎 negative).
- **KPIs** are optional, manually entered, one row per KPI in `sponsorship_kpis` (`rel.KPIS` registry: views,
  clicks, referrals, conversions, revenue); derived metrics (cost per view / click / referral / conversion, ROAS)
  are computed deterministically only when their inputs exist and the divisor is > 0.
- **History** (👍/👎 totals, spend, revenue, historical ROAS) is always derived from the sponsorship records, never
  stored as counters, so repeated clicks cannot inflate it. It is shown next to the campaign score (results card
  badge, "Prenew relationship" panel on the analysis page), never mixed into AHP/TOPSIS.
- Sponsorships are business records: deleting a campaign report keeps them (creator/platform/market/campaign are
  snapshotted), which is also the data a future feedback loop into creator selection would learn from.

## Graceful degradation

| Missing / failing | Behaviour |
|---|---|
| gpt-5.6-sol web search fails / no Azure key | Tavily (if `TAVILY_API_KEY`), else YouTube-only discovery; topic research falls back the same way, else the strategy is marked "model knowledge"; errors in run statistics |
| Azure down or rate-limited | falls back along `LLM_MODELS` if you list fallbacks (e.g. `groq/…`, `gemini/…`) |
| `TWITCH_CLIENT_ID/SECRET` / Twitch down | no Twitch discovery or metrics; listed in run statistics |
| `INSTAGRAM_ACCESS_TOKEN` missing / expired | no Instagram numbers; token errors listed in run statistics (`check_setup.py` flags an expired token) |
| Vision model down | no visual features; visual criterion dropped/imputed; confidence −0.05 |
| Transcript blocked / missing | title + description only; transcript coverage lowers confidence |
| Single API error | retried / that enrichment skipped for that creator; the run continues |

## Known limitations

- YouTube's public API has **no audience geography** → target-language comment share is used as a proxy.
- Transcripts use the unofficial `youtube-transcript-api`; works from most home connections, usually blocked
  from cloud/datacenter IPs.
- No video downloading / frame extraction (YouTube ToS); VLM sees official thumbnails only.
- TikTok contributes discovery, handles and evidence only — no metrics, no scraping.
- Instagram: Business Discovery only reads known handles of professional (creator/business) accounts — no
  keyword search, no audience demographics. The user token expires after 60 days (extend it in Meta's Access
  Token Tool).
- Twitch: streamers are merged with their YouTube channel (YouTube link in the Twitch bio, or exact name match)
  (YouTube card shows the Twitch follower chip) and every streamer is also ranked in the separate Twitch ranking.
  YouTube, Twitch and Instagram each have their own ranking; a creator can appear in several, and the YouTube score
  never uses Twitch/Instagram numbers.
  Discovery uses streams that are live at run time, so results vary by time of day.
- LLM cache keys are provider-independent: an identical prompt answered once is reused, whichever model answered.

## Switching LLM provider

Default: `azure/gpt-5.6-sol` for text and vision, and gpt-5.6-sol web search. Override with
`LLM_MODELS=azure/<deployment>,groq/<model>,gemini/<model>,openai/<model>,anthropic/<model>` (first available wins). All extraction
goes through `LLMClient.extract(schema, system, user, images=None)` with Pydantic validation.

## UI / design system

Design direction: Prenew-inspired clean Nordic commerce × AI intelligence — a daily decision tool for Prenew's
marketing team. Business context: gaming + technology audiences; visual design: professional, not gaming-themed.
`ui.py` holds all design tokens (`TOKENS`), the global CSS and small render helpers (cards, score, confidence
badge, metric bars, evidence cards, stage list). `.streamlit/config.toml` mirrors the main tokens for native widgets.
The palette is a provisional Prenew-inspired one — replace the hex values in `TOKENS` (and the config file)
with the brand's exact colours. Group scores on cards (Campaign fit, Community, Performance, Product evidence)
are weighted TOPSIS closeness values (Audience relevance, Content relevance, Community quality, Market fit),
relative to the ranked candidate set; **Hidden gem** =
campaign fit ≥ 75, campaign score ≥ median and fewer subscribers than the median ranked creator. Shortlists are stored per campaign in a
`shortlist` table.
