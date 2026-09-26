# Creator Intelligence Engine (hackathon MVP)

Campaign brief → YouTube creator discovery → structured AI features → **AHP + TOPSIS** ranking →
explainable Streamlit dashboard.

The LLM never ranks creators. It only turns unstructured text (titles, descriptions, transcripts,
comments) into structured, evidence-backed features. Ranking is deterministic, campaign-specific and
reported next to a separate **Data Confidence** score.

## Quick start

```bash
pip install -r requirements.txt
cp .env.example .env        # add YOUTUBE_API_KEY and GEMINI_API_KEY
streamlit run app.py        # use the "Demo snapshot" source, or start a new run in the sidebar
# or headless:
python pipeline.py "Find creators for €600–900 refurbished gaming PCs targeting gamers in Germany."
python -m pytest -q
```

## Pipeline

| Stage | Module | Notes |
|---|---|---|
| Brief → `CampaignSpec` + 6 search queries | `pipeline.parse_brief` | 1 LLM call; price range = product segment, not budget |
| YouTube search (DE / de, last 12 months) | `youtube.py` | 100 quota units per query |
| Cheap filter | `pipeline.run_campaign` | subscriber band, country ∈ DACH if set, top 25 by search hits |
| Upload screening | `features.relevant_video_ids` | 1 LLM call per channel over its last 30 titles |
| Deep analysis (top 15) | `features.py` | 5 relevant videos, transcripts if available, 50 comments/video |
| Statistical features | `features.statistical_features` | median views, engagement, view efficiency, volatility, recency, est. cost |
| Content features (0–3 ordinal) | `features.content_features` | evidence quotes verified by substring match; unsupported high scores are downgraded |
| Comment features | `features.classify_comments` | short/emoji comments pre-labelled `generic` without LLM; batches of 50 |
| Hard filter → AHP → TOPSIS | `ranking.py` | AHP over 5 criterion groups (CR checked), fixed sub-weights |
| Dashboard | `app.py` | live re-weighting, score vs confidence scatter, “why this rank” with quotes |

All YouTube and LLM responses are cached in SQLite (`api_cache`), so re-runs cost no quota.
`data/demo.db` is a committed snapshot without the raw cache.

## Criteria (B = benefit, C = cost)

- **Campaign Fit**: niche, product, price-segment relevance (LLM) · target-language comment share (proxy for audience country)
- **Content Credibility**: first-hand experience, benchmarks, comparison, price discussion, purchase recommendation (LLM)
- **Audience Quality**: meaningful / technical+question / purchase-intent comment ratios · creator reply rate
- **Reach & Performance**: log median relevant views · engagement rate · views/subscriber · view volatility (C)
- **Cost & Risk**: estimated cost per video (C, CPM assumption) · spam ratio (C) · days since last relevant upload (C)

Missing values are imputed with the unfavourable quartile and lower confidence; columns are winsorised
(5/95) and vector-normalised.

## Known limitations (by design)

- YouTube's public API has **no audience geography** → German-comment share is used as a proxy.
- Transcripts: no official API for third-party videos. The unofficial library works from most home
  connections but is often blocked from cloud IPs; the pipeline falls back to title + description.
- No video downloading / frame extraction (YouTube ToS).
- TikTok / Instagram: only handles linked in YouTube descriptions are shown. `collectors.ProviderCollector`
  is the plug-in point for a licensed data vendor.
- Gemini free tier allows ~5 requests/min per model; the client rotates across models and rate-limits
  itself. Enabling billing makes runs much faster.

## Switching LLM provider

`LLM_MODELS=anthropic/<model>` or `openai/<model>` (with the matching SDK and API key). All extraction
goes through `LLMClient.extract(schema, system, user, images=None)` with Pydantic validation.
