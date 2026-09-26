# Creator Intelligence Engine (hackathon MVP)

Campaign brief → Groq structured extraction → **human review/edit** → YouTube + Tavily web discovery →
identity merge → YouTube data collection → text / comment / thumbnail (VLM) features → hard filter →
**AHP + TOPSIS** → explainable Streamlit dashboard.

The LLM never ranks creators. It only turns unstructured text (titles, descriptions, transcripts,
comments) into structured, evidence-backed features. Ranking is deterministic, campaign-specific and
reported next to a separate **Data Confidence** score.

## Quick start

```bash
pip install -r requirements.txt
cp .env.example .env        # YOUTUBE_API_KEY + GROQ_API_KEY required; TAVILY/OPENAI/GEMINI optional
streamlit run app.py        # Campaign → Continue → confirm details → Find creators → Discover → View analysis
                            # → Shortlist. "Offline demo snapshot" under Recent campaigns works without network.
# or headless:
python pipeline.py "Find creators for €600–900 refurbished gaming PCs targeting gamers in Germany."
python -m pytest -q
python check_setup.py      # one tiny request per API: shows exactly which keys/hosts/models work
```

## Pipeline

| Stage | Module | Provider | Notes |
|---|---|---|---|
| Brief → `CampaignSpec` (+ YouTube and web queries) | `pipeline.parse_brief` | Groq | 1 call; price range = product segment, not budget |
| Human review / edit of the spec | `app.spec_editor` | – | product, niche, market, language, price, goal, queries, subscriber range, seeds |
| YouTube search (market / language, last 12 months) | `youtube.py` | YouTube | 100 quota units per query |
| Web discovery | `web_discovery.discover` | Tavily + Groq | 6 basic searches (general + `site:` TikTok/Instagram/Twitch/YouTube); profile URLs parsed deterministically; 1 LLM call extracts creator names from articles (names must appear in the cited result) |
| Identity merge / dedup | `web_discovery.resolve_to_youtube` | YouTube | handles/channel IDs/normalised names → YouTube channels (≤15 `forHandle` lookups); unmatched → *web-only* list (not ranked) |
| Cheap filter | `pipeline.run_campaign` | – | subscriber range from spec, country in market if declared, top 25 by search hits + 2×web mentions |
| Upload screening | `features.relevant_video_ids` | Groq | 1 call per channel over its last 30 titles |
| Deep analysis (top 15) | `features.py` | YouTube + Groq | 5 relevant videos, transcript if available else title+description, 50 comments/video |
| Visual features | `vision.py` | OpenAI | 1 call per creator, 4 official thumbnail URLs, `detail=low` |
| Hard filter → AHP → TOPSIS | `ranking.py` | – | deterministic; criteria with < 50% coverage are dropped and shown |
| UI | `app.py` + `ui.py` | – | Campaign / Discover / Shortlist; creator cards, analysis with evidence cards, live AHP re-weighting, staged loader driven by real pipeline progress |

AI models only extract features and evidence. They never compare or rank creators.

All YouTube and LLM responses are cached in SQLite (`api_cache`), so re-runs cost no quota.
`data/demo.db` is a committed snapshot without the raw cache.

## Criteria (B = benefit, C = cost)

- **Campaign Fit**: niche, product, price-segment relevance (LLM) · target-language comment share (proxy for audience country)
- **Content Credibility**: first-hand experience, benchmarks, comparison, price discussion, purchase recommendation (LLM) ·
  product visible in thumbnails (VLM, sub-weight 0.10 → ~2% total weight; other visual tags are descriptive only)
- **Audience Quality**: meaningful / technical+question / purchase-intent comment ratios · creator reply rate
- **Reach & Performance**: log median relevant views · engagement rate · views/subscriber · view volatility (C)
- **Cost & Risk**: *Estimated Cost Proxy* = median relevant views × assumed €20 CPM (C) — **not a creator quote** ·
  spam ratio (C) · days since last relevant upload (C)

Missing values are imputed with the unfavourable quartile and lower confidence; columns are winsorised
(5/95) and vector-normalised.

## Data Confidence (separate from the campaign score)

`0.25·videos + 0.20·transcript coverage + 0.20·classified comments + 0.10·source reliability +
0.05·visual coverage + 0.10·recency + 0.10·stats completeness` (imputed criteria reduce the last term).

## Graceful degradation

| Missing / failing | Behaviour |
|---|---|
| `TAVILY_API_KEY` / Tavily down | YouTube-only discovery; error listed in run statistics |
| `GROQ_API_KEY` / Groq down or rate-limited | falls back along `LLM_MODELS` (Gemini) |
| `OPENAI_API_KEY` / OpenAI down | no visual features; visual criterion dropped/imputed; confidence −0.05 |
| Transcript blocked / missing | title + description only; transcript coverage lowers confidence |
| Single API error | retried / that enrichment skipped for that creator; the run continues |

## Known limitations

- YouTube's public API has **no audience geography** → target-language comment share is used as a proxy.
- Transcripts use the unofficial `youtube-transcript-api`; works from most home connections, usually blocked
  from cloud/datacenter IPs.
- No video downloading / frame extraction (YouTube ToS); VLM sees official thumbnails only.
- TikTok / Instagram / Twitch contribute discovery, handles and evidence only — no metrics, no scraping.
- LLM cache keys are provider-independent: an identical prompt answered once is reused, whichever model answered.

## Switching LLM provider

`LLM_MODELS=groq/<model>,gemini/<model>,openai/<model>,anthropic/<model>` (first available wins). All extraction
goes through `LLMClient.extract(schema, system, user, images=None)` with Pydantic validation.

## UI / design system

`ui.py` holds all design tokens (`TOKENS`), the global CSS and small render helpers (cards, score, confidence
badge, metric bars, evidence cards, stage list). `.streamlit/config.toml` mirrors the main tokens for native widgets.
The palette is a provisional Prenew-inspired one — replace the hex values in `TOKENS` (and the config file)
with the brand's exact colours. Group scores on cards (Campaign fit, Community, Performance, Product evidence)
are the weighted TOPSIS closeness per criterion group, relative to the ranked candidate set; **Hidden gem** =
campaign fit ≥ 70 with fewer subscribers than the median ranked creator. Shortlists are stored per campaign in a
`shortlist` table.
