"""Hard filter, AHP criterion weights, TOPSIS ranking, explanations and data confidence.

The LLM never ranks. It only produces features; ranking here is deterministic and campaign-relative.
"""
import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

import config


@dataclass(frozen=True)
class Criterion:
    name: str
    group: str
    kind: str      # "B" benefit (higher is better) / "C" cost (lower is better)
    sub_weight: float
    label: str


GROUPS = ["Campaign Fit", "Content Credibility", "Audience Quality", "Reach & Performance", "Cost & Risk"]

CRITERIA = [
    # audience relevance weighs most: the creator's value is who watches, not how close the content is to the product
    Criterion("audience_relevance", "Campaign Fit", "B", 0.30, "Target-audience relevance"),
    Criterion("niche_relevance", "Campaign Fit", "B", 0.20, "Niche relevance"),
    Criterion("product_relevance", "Campaign Fit", "B", 0.15, "Product relevance"),
    Criterion("price_segment_relevance", "Campaign Fit", "B", 0.10, "Price-segment relevance"),
    Criterion("target_lang_share", "Campaign Fit", "B", 0.25, "Target-language audience (proxy)"),
    Criterion("first_hand_experience", "Content Credibility", "B", 0.30, "First-hand experience"),
    Criterion("benchmark_discussion", "Content Credibility", "B", 0.20, "Benchmarks"),
    Criterion("product_comparison", "Content Credibility", "B", 0.15, "Product comparison"),
    Criterion("price_discussion", "Content Credibility", "B", 0.15, "Price discussion"),
    Criterion("purchase_recommendation", "Content Credibility", "B", 0.20, "Purchase recommendation"),
    # VLM-derived, deliberately small: share of thumbnails showing the product category / hands-on demo
    Criterion("visual_product_share", "Content Credibility", "B", 0.10, "Product visible in thumbnails (VLM)"),
    Criterion("meaningful_ratio", "Audience Quality", "B", 0.30, "Meaningful comments"),
    Criterion("technical_question_ratio", "Audience Quality", "B", 0.20, "Technical / question comments"),
    Criterion("purchase_intent_ratio", "Audience Quality", "B", 0.35, "Purchase-intent comments"),
    Criterion("creator_reply_rate", "Audience Quality", "B", 0.15, "Creator reply rate"),
    Criterion("log_median_views", "Reach & Performance", "B", 0.40, "Median relevant views (log)"),
    Criterion("engagement_rate", "Reach & Performance", "B", 0.30, "Engagement rate"),
    Criterion("view_efficiency", "Reach & Performance", "B", 0.15, "Views / subscribers"),
    Criterion("view_cv", "Reach & Performance", "C", 0.15, "View volatility"),
    Criterion("log_est_cost_eur", "Cost & Risk", "C", 0.50, "Estimated Cost Proxy (assumed CPM, log)"),
    Criterion("spam_ratio", "Cost & Risk", "C", 0.25, "Spam / suspicious comments"),
    Criterion("days_since_last_relevant", "Cost & Risk", "C", 0.25, "Days since last relevant upload"),
]
# Twitch / Instagram: own criteria from the official APIs + one LLM fit judgement on their own text (bio, stream
# titles, captions). Same 5 AHP groups, so the campaign goal and manual weights apply to every platform.
TWITCH_CRITERIA = [
    Criterion("tw_audience_relevance", "Campaign Fit", "B", 1.0, "Target-audience relevance (stream titles, LLM)"),
    Criterion("tw_vod_view_ratio", "Audience Quality", "B", 1.0, "VOD views / followers"),
    Criterion("tw_log_followers", "Reach & Performance", "B", 0.4, "Followers (log)"),
    Criterion("tw_log_median_vod_views", "Reach & Performance", "B", 0.4, "Median VOD views (log)"),
    Criterion("tw_streams_30d", "Reach & Performance", "B", 0.2, "Streams in the last 30 days"),
    Criterion("tw_days_since_last_stream", "Cost & Risk", "C", 1.0, "Days since last stream"),
]
INSTAGRAM_CRITERIA = [
    Criterion("ig_audience_relevance", "Campaign Fit", "B", 0.75, "Target-audience relevance (captions, LLM)"),
    Criterion("ig_target_lang", "Campaign Fit", "B", 0.25, "Captions in the target language"),
    Criterion("ig_engagement_rate", "Audience Quality", "B", 0.7, "Engagement rate"),
    Criterion("ig_comment_like_ratio", "Audience Quality", "B", 0.3, "Comments per like (conversation)"),
    Criterion("ig_log_followers", "Reach & Performance", "B", 0.5, "Followers (log)"),
    Criterion("ig_log_median_likes", "Reach & Performance", "B", 0.3, "Median likes (log)"),
    Criterion("ig_posts_30d", "Reach & Performance", "B", 0.2, "Posts in the last 30 days"),
    Criterion("ig_days_since_last_post", "Cost & Risk", "C", 1.0, "Days since last post"),
]
PLATFORM_CRITERIA = {"youtube": CRITERIA, "twitch": TWITCH_CRITERIA, "instagram": INSTAGRAM_CRITERIA}
CRITERION_BY_NAME = {c.name: c for crits in PLATFORM_CRITERIA.values() for c in crits}

# ------------------------------------------------------------------ AHP
RI = {1: 0.0, 2: 0.0, 3: 0.58, 4: 0.90, 5: 1.12, 6: 1.24, 7: 1.32, 8: 1.41, 9: 1.45}

# Pairwise matrices over GROUPS (row vs column), Saaty 1-9 scale.
AHP_PRESETS = {
    "conversion": [[1, 2, 2, 4, 3],
                   [1/2, 1, 1, 3, 2],
                   [1/2, 1, 1, 3, 2],
                   [1/4, 1/3, 1/3, 1, 1/2],
                   [1/3, 1/2, 1/2, 2, 1]],
    "awareness": [[1, 2, 2, 1/2, 2],
                  [1/2, 1, 1, 1/3, 1],
                  [1/2, 1, 1, 1/3, 1],
                  [2, 3, 3, 1, 3],
                  [1/2, 1, 1, 1/3, 1]],
    "balanced": [[1, 1, 1, 2, 2],
                 [1, 1, 1, 2, 2],
                 [1, 1, 1, 2, 2],
                 [1/2, 1/2, 1/2, 1, 1],
                 [1/2, 1/2, 1/2, 1, 1]],
}


def ahp_weights(matrix) -> tuple[np.ndarray, float]:
    """Geometric-mean priority vector and consistency ratio (CR <= 0.1 is acceptable)."""
    A = np.asarray(matrix, dtype=float)
    n = A.shape[0]
    if A.shape != (n, n) or not np.allclose(A * A.T, 1.0):
        raise ValueError("AHP matrix must be square and reciprocal (a_ji = 1/a_ij)")
    w = np.prod(A, axis=1) ** (1 / n)
    w = w / w.sum()
    lam = float(np.mean((A @ w) / w))
    ci = (lam - n) / (n - 1) if n > 1 else 0.0
    cr = ci / RI[n] if RI.get(n) else 0.0
    return w, cr


def criterion_weights(group_weights: dict[str, float], criteria=None) -> dict[str, float]:
    """Final weight = group weight (AHP) x sub-weight (normalised within the group).
    Groups without criteria on a platform (e.g. Content Credibility on Twitch) drop out; the rest renormalise."""
    criteria = CRITERIA if criteria is None else criteria
    out = {}
    for g in GROUPS:
        members = [c for c in criteria if c.group == g]
        total = sum(c.sub_weight for c in members)
        for c in members:
            out[c.name] = group_weights.get(g, 0.0) * c.sub_weight / total
    s = sum(out.values()) or 1.0
    return {k: v / s for k, v in out.items()}


# ------------------------------------------------------------------ Hard filter
def hard_filter(f: dict) -> tuple[bool, str]:
    reasons = []
    days = f.get("days_since_last_relevant")
    if days is None or days > config.MAX_DAYS_SINCE_UPLOAD:
        reasons.append(f"no relevant upload in last {config.MAX_DAYS_SINCE_UPLOAD} days")
    if (f.get("n_relevant_videos") or 0) < config.MIN_RELEVANT_VIDEOS:
        reasons.append(f"fewer than {config.MIN_RELEVANT_VIDEOS} relevant videos")
    relevance = max(f.get("niche_relevance") or 0, f.get("audience_relevance") or 0)
    if f.get("niche_relevance") is not None and relevance < config.MIN_NICHE_RELEVANCE:
        reasons.append("niche and audience relevance too low")
    lang = f.get("target_lang_share")
    if lang is not None and lang < config.MIN_TARGET_LANG_SHARE:
        reasons.append(f"only {lang:.0%} of comments in target language")
    if f.get("niche_relevance") is None:
        reasons.append("content analysis failed")
    return (not reasons, "; ".join(reasons))


MAX_DAYS_INACTIVE = 60  # Twitch / Instagram: last stream / post


def platform_hard_filter(platform: str, f: dict) -> tuple[bool, str]:
    reasons = []
    p = {"twitch": "tw", "instagram": "ig"}[platform]
    days = f.get(f"{p}_days_since_last_stream" if p == "tw" else f"{p}_days_since_last_post")
    if days is None or days > MAX_DAYS_INACTIVE:
        reasons.append(f"no {'stream' if p == 'tw' else 'post'} in the last {MAX_DAYS_INACTIVE} days")
    rel = f.get(f"{p}_audience_relevance")
    if rel is None:
        reasons.append("content analysis failed")
    elif rel < config.MIN_NICHE_RELEVANCE:
        reasons.append("audience relevance too low")
    if p == "ig" and (f.get("ig_n_posts") or 0) < 3:
        reasons.append("fewer than 3 recent posts visible")
    return (not reasons, "; ".join(reasons))


def platform_confidence(platform: str, f: dict) -> float:
    """Official-API numbers (reliable) but no comment analysis: capped below YouTube's maximum."""
    p = {"twitch": "tw", "instagram": "ig"}[platform]
    n = f.get("tw_n_vods" if p == "tw" else "ig_n_posts") or 0
    days = f.get(f"{p}_days_since_last_stream" if p == "tw" else f"{p}_days_since_last_post")
    recency = math.exp(-days / 90) if days is not None else 0.0
    c = (0.30 * 1.0                                    # official API source
         + 0.25 * min(n / 10, 1)                       # items behind the numbers
         + 0.20 * (f.get(f"{p}_fit_evidence") or 0.0)  # verified evidence quote for the fit judgement
         + 0.15 * recency
         + 0.10 * (f.get(f"{p}_followers") is not None))
    return round(float(min(max(c, 0.0), 0.9)), 3)


# ------------------------------------------------------------------ TOPSIS
def _prepare(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Winsorise at 5/95th percentile; impute missing with the 25th percentile (conservative)."""
    X = df.astype(float).copy()
    imputed = X.isna()
    for col in X.columns:
        s = X[col]
        if s.notna().sum() == 0:
            X[col] = 0.0
            continue
        crit = CRITERION_BY_NAME.get(col)
        # conservative imputation: bad-side quartile
        fill = s.quantile(0.75) if crit and crit.kind == "C" else s.quantile(0.25)
        X[col] = s.fillna(fill)
        if len(X) >= 5:
            lo, hi = X[col].quantile(0.05), X[col].quantile(0.95)
            X[col] = X[col].clip(lo, hi)
    return X, imputed


def topsis(df: pd.DataFrame, weights: dict[str, float], criteria=None) -> pd.DataFrame:
    """df: rows = creators, columns = criteria names. Returns score, rank, and per-criterion closeness."""
    present = [c.name for c in (CRITERIA if criteria is None else criteria) if c.name in df.columns]
    coverage = df[present].notna().mean() if len(df) else pd.Series(0.0, index=present)
    # criteria known for too few candidates would be mostly imputed -> excluded, and reported
    dropped = [c for c in present if coverage[c] < config.MIN_CRITERION_COVERAGE]
    cols = [c for c in present if c not in dropped]
    X, imputed = _prepare(df[cols])
    w = np.array([weights[c] for c in cols])
    w = w / w.sum()
    benefit = np.array([CRITERION_BY_NAME[c].kind == "B" for c in cols])

    M = X.to_numpy()
    norm = np.sqrt((M ** 2).sum(axis=0))
    norm[norm == 0] = 1.0
    V = (M / norm) * w
    best = np.where(benefit, V.max(axis=0), V.min(axis=0))
    worst = np.where(benefit, V.min(axis=0), V.max(axis=0))
    d_pos = np.sqrt(((V - best) ** 2).sum(axis=1))
    d_neg = np.sqrt(((V - worst) ** 2).sum(axis=1))
    denom = d_pos + d_neg
    score = np.divide(d_neg, denom, out=np.full_like(d_neg, 0.5), where=denom > 0)

    # closeness per criterion in [0,1]: 1 = at the ideal among candidates
    span = best - worst
    closeness = np.divide(V - worst, span, out=np.full_like(V, 0.5), where=span != 0)

    out = pd.DataFrame(index=df.index)
    out["score"] = score
    out["rank"] = out["score"].rank(ascending=False, method="first").astype(int)
    for j, c in enumerate(cols):
        out[f"close__{c}"] = closeness[:, j]
        out[f"imputed__{c}"] = imputed[c].to_numpy()
    out.attrs["weights"] = dict(zip(cols, w))
    out.attrs["dropped"] = dropped
    return out.sort_values("rank")


def explain(row: pd.Series, weights: dict[str, float], k: int = 3) -> dict:
    """Top strengths (high weight x closeness) and gaps (high weight x distance to ideal)."""
    items = []
    for name, w in weights.items():
        cl = row.get(f"close__{name}")
        if cl is None or (isinstance(cl, float) and math.isnan(cl)):
            continue
        items.append((name, w, float(cl)))
    strengths = sorted(items, key=lambda t: -t[1] * t[2])[:k]
    gaps = sorted(items, key=lambda t: -t[1] * (1 - t[2]))[:k]
    fmt = lambda t: {"criterion": t[0], "label": CRITERION_BY_NAME[t[0]].label,
                     "weight": round(t[1], 3), "closeness": round(t[2], 2)}
    return {"strengths": [fmt(t) for t in strengths], "gaps": [fmt(t) for t in gaps]}


# ------------------------------------------------------------------ Data confidence
def confidence(f: dict, n_imputed: int = 0) -> float:
    """Separate from campaign score: how much do we trust the features we computed?"""
    n_vid = f.get("n_analyzed_videos") or 0
    days = f.get("days_since_last_relevant")
    recency = math.exp(-days / 180) if days is not None else 0.0
    stats_ok = 1.0 - 0.25 * bool(f.get("hidden_subscribers")) - 0.25 * bool(f.get("hidden_likes"))
    stats_ok -= min(n_imputed, 4) * 0.125
    if not n_vid:
        stats_ok = 0.0
    visual = min((f.get("n_visual_images") or 0) / config.VLM_IMAGES_PER_CREATOR, 1)
    c = (0.25 * min(n_vid / config.VIDEOS_PER_CREATOR, 1)
         + 0.20 * (f.get("transcript_coverage") or 0.0)
         + 0.20 * min((f.get("n_classified_comments") or 0) / 200, 1)
         + 0.10 * (f.get("source_reliability") or 0.0)
         + 0.05 * visual
         + 0.10 * recency
         + 0.10 * max(stats_ok, 0.0))
    return round(float(min(max(c, 0.0), 1.0)), 3)


def confidence_label(c: float) -> str:
    return "High" if c >= 0.7 else "Medium" if c >= 0.4 else "Low"
