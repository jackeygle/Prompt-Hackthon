"""Feature extraction: deterministic statistics, LLM content features, LLM comment classification.

AI only converts unstructured text into structured features; it never ranks.
"""
import hashlib
import math
import re
import statistics as st
from collections import Counter
from datetime import datetime, timezone

import config
from llm import LLMClient
from models import CampaignSpec, CommentBatch, ContentFeatures, RelevantVideos

CONTENT_FEATURES = ["niche_relevance", "audience_relevance", "product_relevance", "price_segment_relevance", "first_hand_experience",
                    "benchmark_discussion", "price_discussion", "product_comparison", "purchase_recommendation"]


def _days_since(iso: str) -> float:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return (datetime.now(timezone.utc) - dt).total_seconds() / 86400


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[\"'“”„‘’`]", "", s or "")).strip().casefold()


# ---------------------------------------------------------------- statistics
def statistical_features(relevant: list[dict], all_uploads: list[dict], subscribers: int | None) -> dict:
    """relevant/all_uploads: dicts with views, likes, comments, published_at."""
    f: dict = {}
    views = [v["views"] for v in relevant if v.get("views") is not None]
    f["n_relevant_videos"] = len(relevant)
    if views:
        med = st.median(views)
        f["median_relevant_views"] = med
        f["log_median_views"] = math.log10(1 + med)
        f["view_cv"] = (st.pstdev(views) / st.mean(views)) if len(views) > 1 and st.mean(views) > 0 else None
        f["est_cost_eur"] = med / 1000 * config.ASSUMED_CPM_EUR
        f["log_est_cost_eur"] = math.log10(1 + f["est_cost_eur"])
        f["view_efficiency"] = (med / subscribers) if subscribers else None
    eng = [((v.get("likes") or 0) + (v.get("comments") or 0)) / v["views"]
           for v in relevant if v.get("views") and v.get("likes") is not None]
    f["engagement_rate"] = st.median(eng) if eng else None
    cr = [(v.get("comments") or 0) / v["views"] for v in relevant if v.get("views") and v.get("comments") is not None]
    f["comment_rate"] = st.median(cr) if cr else None
    f["hidden_likes"] = any(v.get("likes") is None for v in relevant)
    if relevant:
        f["days_since_last_relevant"] = min(_days_since(v["published_at"]) for v in relevant)
    if len(all_uploads) >= 2:
        span = max(_days_since(v["published_at"]) for v in all_uploads)
        f["uploads_per_month"] = len(all_uploads) / max(span / 30.4, 0.5)
    return f


# ---------------------------------------------------------------- relevance of uploads
RELEVANCE_SYSTEM = """You screen YouTube channels for an influencer-marketing campaign.
Given the campaign and a channel's recent video titles, return the IDs of videos whose viewers plausibly
belong to the campaign's TARGET AUDIENCE: videos about the product or niche, AND videos about the topics that
audience already watches (its audience interests). A video does not need to mention the product to count.
Exclude videos with no plausible connection to that audience (unrelated lifestyle, news, music, etc.)."""


def relevant_video_ids(llm: LLMClient, spec: CampaignSpec, channel_title: str, uploads: list[dict]) -> RelevantVideos:
    lines = "\n".join(f"{u['id']} | {u['title']}" for u in uploads)
    user = (f"Campaign niche: {spec.niche}\nProduct: {spec.product}\nKeywords: {', '.join(spec.product_keywords)}\n"
            f"Target audience: {spec.audience}\nAudience interests: {', '.join(spec.audience_interests) or spec.niche}"
            f"\n\nChannel: {channel_title}\nVideos (id | title):\n{lines}")
    out = llm.extract(RelevantVideos, RELEVANCE_SYSTEM, user)
    valid = {u["id"] for u in uploads}
    out.relevant_ids = [i for i in out.relevant_ids if i in valid]
    return out


# ---------------------------------------------------------------- content features
CONTENT_SYSTEM = """You extract structured features about a YouTube creator for an influencer campaign.
Score each feature on this ordinal scale across the provided videos:
0 = not present, 1 = briefly mentioned, 2 = clearly present in at least one video, 3 = central / systematic across videos.
Be strict and conservative. Judge only from the provided text.
For every feature scored >= 2, include at least one evidence item whose quote is copied VERBATIM
(exact characters, <= 20 words) from the provided title, description or transcript of that video_id."""


def _video_text(v: dict) -> str:
    tr = v.get("transcript") or ""
    if len(tr) > config.TRANSCRIPT_CHARS:
        head = int(config.TRANSCRIPT_CHARS * 0.7)
        tr = tr[:head] + " … " + tr[-(config.TRANSCRIPT_CHARS - head):]
    return (f"TITLE: {v['title']}\nDESCRIPTION: {(v.get('description') or '')[:1500]}\n"
            f"TRANSCRIPT: {tr or '(not available)'}")


def content_features(llm: LLMClient, spec: CampaignSpec, channel_title: str, vids: list[dict]):
    """Returns (features dict, verified evidence list, summary)."""
    blocks = "\n\n".join(f"=== video_id: {v['id']} ===\n{_video_text(v)}" for v in vids)
    user = (f"CAMPAIGN\nProduct: {spec.product}\nPrice segment: {spec.price_segment}\nNiche: {spec.niche}\n"
            f"Audience: {spec.audience}\nAudience interests: {', '.join(spec.audience_interests) or spec.niche}"
            f"\n\nCREATOR: {channel_title}\n\n{blocks}")
    out = llm.extract(ContentFeatures, CONTENT_SYSTEM, user)

    # Deterministic hallucination guard: a quote must appear in the source text of that video.
    sources = {v["id"]: _norm(_video_text(v)) for v in vids}
    evidence = []
    for e in out.evidence:
        ok = e.video_id in sources and len(_norm(e.quote)) >= 8 and _norm(e.quote) in sources[e.video_id]
        evidence.append({"feature": e.feature, "video_id": e.video_id, "quote": e.quote, "verified": ok})
    feats = {}
    for name in CONTENT_FEATURES:
        score = getattr(out, name)
        has_proof = any(ev["verified"] and ev["feature"] == name for ev in evidence)
        if score >= 2 and not has_proof and name not in ("niche_relevance", "audience_relevance"):
            score -= 1  # unsupported high score -> downgrade
        feats[name] = float(score)
    return feats, evidence, out.summary


# ---------------------------------------------------------------- comments
COMMENT_SYSTEM = """Classify YouTube comments for audience-quality analysis. Labels:
- purchase_intent: wants to buy / asks where to buy / price-for-me / "just ordered"
- question: asks a question (not purchase)
- technical: discusses specs, performance, parts, settings substantively
- meaningful: substantive opinion or experience, not technical
- generic: short praise/filler ("nice video", "first", emojis)
- spam: self-promotion, scams, bots, links, irrelevant copy-paste
Also give the ISO 639-1 language. Return one item per input index."""

_WORD = re.compile(r"[^\W\d_]{2,}", re.UNICODE)


def author_hash(channel_id: str) -> str:
    return hashlib.sha256((channel_id or "").encode()).hexdigest()[:16]


def classify_comments(llm: LLMClient, comments: list[dict], batch_size: int = 50) -> None:
    """Mutates comments in place, setting 'label' and 'language'. Short/emoji-only -> generic, no LLM."""
    todo = []
    for c in comments:
        if len(_WORD.findall(c["text"])) < 4:
            c["label"], c["language"] = "generic", None
        else:
            todo.append(c)
    for i in range(0, len(todo), batch_size):
        chunk = todo[i:i + batch_size]
        user = "\n".join(f"[{j}] {c['text'][:400]}" for j, c in enumerate(chunk))
        try:
            out = llm.extract(CommentBatch, COMMENT_SYSTEM, user)
        except Exception:
            continue  # leave unlabeled; lowers n_classified -> lowers confidence
        for item in out.items:
            if 0 <= item.i < len(chunk):
                chunk[item.i]["label"], chunk[item.i]["language"] = item.label, item.lang.lower()[:2]


def comment_features(comments: list[dict], threads: int, creator_replied_threads: int, target_lang: str) -> dict:
    labeled = [c for c in comments if c.get("label")]
    n = len(labeled)
    f = {"n_classified_comments": n,
         "creator_reply_rate": (creator_replied_threads / threads) if threads else None}
    if n == 0:
        return f
    cnt = Counter(c["label"] for c in labeled)
    f["meaningful_ratio"] = (cnt["meaningful"] + cnt["technical"] + cnt["question"] + cnt["purchase_intent"]) / n
    f["technical_question_ratio"] = (cnt["technical"] + cnt["question"]) / n
    f["purchase_intent_ratio"] = cnt["purchase_intent"] / n
    f["spam_ratio"] = cnt["spam"] / n
    langs = [c["language"] for c in labeled if c.get("language")]
    f["target_lang_share"] = (sum(l == target_lang for l in langs) / len(langs)) if langs else None
    return f
