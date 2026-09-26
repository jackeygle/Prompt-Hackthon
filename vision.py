"""Lightweight visual analysis on official YouTube thumbnails (no downloading of video content).

One VLM call per creator with a few thumbnail URLs at detail=low. The VLM only tags images;
tags are aggregated deterministically into creator-level features. It never ranks.
"""
import logging

import config
from llm import LLMClient
from models import CampaignSpec, VisualAnalysis

log = logging.getLogger(__name__)

VISION_SYSTEM = """You tag YouTube video thumbnails for an influencer-marketing feature pipeline.
For each image (in input order, index starting at 0) return the requested boolean/ordinal tags.
Judge only what is visible. Be conservative: if unsure, answer false."""

VISUAL_FEATURES = ["visual_product_share", "visual_face_share", "visual_demo_share", "visual_screen_share",
                   "visual_text_heavy_share", "visual_product_focus_share", "visual_quality"]


def thumbnail_urls(videos: list[dict], n: int = config.VLM_IMAGES_PER_CREATOR) -> list[tuple[str, str]]:
    """(video_id, url) of official thumbnails for the most relevant (already ordered) videos."""
    out = []
    for v in videos:
        if v.get("thumbnail"):
            out.append((v["id"], v["thumbnail"]))
        if len(out) >= n:
            break
    return out


def analyze_creator(vlm: LLMClient, spec: CampaignSpec, videos: list[dict]) -> tuple[dict, list[dict]]:
    """Returns (creator-level features, per-image tags). Raises on VLM failure (caller skips enrichment)."""
    thumbs = thumbnail_urls(videos)
    if not thumbs:
        return {}, []
    user = (f"Campaign product category: {spec.product}\nNiche: {spec.niche}\n"
            f"{len(thumbs)} thumbnails follow, index 0..{len(thumbs) - 1}.")
    out = vlm.extract(VisualAnalysis, VISION_SYSTEM, user, images=[u for _, u in thumbs])
    tags = []
    for t in out.images:
        if 0 <= t.i < len(thumbs):
            tags.append({**t.model_dump(), "video_id": thumbs[t.i][0], "url": thumbs[t.i][1]})
    n = len(tags)
    if n == 0:
        return {}, []
    share = lambda f: sum(bool(f(t)) for t in tags) / n
    feats = {
        "visual_product_share": share(lambda t: t["product_category_visible"] or t["product_demo"]),
        "visual_face_share": share(lambda t: t["face_visible"]),
        "visual_demo_share": share(lambda t: t["product_demo"]),
        "visual_screen_share": share(lambda t: t["benchmark_or_screen_content"]),
        "visual_text_heavy_share": share(lambda t: t["text_heavy"]),
        "visual_product_focus_share": share(lambda t: t["focus"] in ("product", "mixed")),
        "visual_quality": sum(t["production_quality"] for t in tags) / (2 * n),
        "n_visual_images": n,
    }
    return feats, tags
