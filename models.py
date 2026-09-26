"""Pydantic models: domain objects and the JSON contracts for every LLM extraction."""
from typing import Literal

from pydantic import BaseModel, Field


# ---------- Campaign ----------
class CampaignSpecDraft(BaseModel):
    """What the LLM extracts from the brief. The user reviews/edits it before the run starts."""
    product: str = Field(description="What is being promoted, e.g. 'refurbished gaming PCs'")
    product_keywords: list[str] = Field(description="Terms indicating product relevance, in German and English")
    niche: str = Field(description="Content niche creators should be in, e.g. 'PC gaming hardware'")
    price_segment: str = Field(description="Product price segment, e.g. '€600–900'. NOT the campaign budget.")
    target_country: str = Field(description="ISO 3166-1 alpha-2, e.g. 'DE'")
    target_language: str = Field(description="ISO 639-1, e.g. 'de'")
    audience: str = Field(description="Target audience description")
    goal: Literal["conversion", "awareness", "balanced"] = Field(description="Main campaign goal")
    search_queries: list[str] = Field(description="6 diverse YouTube search queries, mostly in the target language")
    web_queries: list[str] = Field(default_factory=list, description=(
        "6 web search queries to discover creators and their cross-platform profiles: 2 general queries "
        "(e.g. lists of best creators in the niche/country), then one each starting with "
        "'site:tiktok.com', 'site:instagram.com', 'site:twitch.tv', 'site:youtube.com'"))


class CampaignSpec(CampaignSpecDraft):
    """Draft + creator constraints set by the user (not by the LLM)."""
    min_subscribers: int = 2_000
    max_subscribers: int = 3_000_000


# ---------- Web discovery ----------
class WebCreatorMention(BaseModel):
    name: str = Field(description="Creator / channel name as written in the source")
    platform: Literal["youtube", "tiktok", "instagram", "twitch", "x", "website", "unknown"]
    handle: str | None = Field(default=None, description="Handle without @ if explicitly present, else null")
    source_index: int = Field(description="Index [n] of the search result where this creator is mentioned")
    evidence: str = Field(description="Short verbatim snippet (<= 20 words) from that result naming the creator")


class WebCreatorMentions(BaseModel):
    creators: list[WebCreatorMention]


# ---------- Visual analysis (VLM) ----------
class ImageTags(BaseModel):
    i: int = Field(description="Index of the image in the input order")
    face_visible: bool = Field(description="A real human face is clearly visible")
    product_category_visible: bool = Field(
        description="The campaign's product category (given in the prompt) is physically visible")
    product_demo: bool = Field(description="Hands-on use, building, unboxing, testing or comparing a physical product")
    benchmark_or_screen_content: bool = Field(
        description="Performance charts/benchmark bars or on-screen/in-game footage are shown")
    text_heavy: bool = Field(description="Large overlay text dominates the image")
    focus: Literal["product", "person", "mixed", "other"] = Field(description="Main visual focus")
    production_quality: int = Field(ge=0, le=2, description="0 low / 1 average / 2 polished (lighting, composition)")
    note: str = Field(description="<= 12 words describing what is shown")


class VisualAnalysis(BaseModel):
    images: list[ImageTags]


# ---------- LLM extraction contracts ----------
class RelevantVideos(BaseModel):
    relevant_ids: list[str] = Field(description="IDs of videos relevant to the campaign niche/product")
    niche_label: str = Field(description="2-5 word label of what this channel mainly covers")


class Evidence(BaseModel):
    feature: str = Field(description="Which feature this supports, e.g. 'benchmark_discussion'")
    quote: str = Field(description="Short verbatim quote (<=20 words) copied exactly from the source text")
    video_id: str


class ContentFeatures(BaseModel):
    """Ordinal 0-3 scales. 0=none, 1=mentioned, 2=clearly present, 3=central/systematic."""
    niche_relevance: int = Field(ge=0, le=3, description="Fit with the campaign niche")
    product_relevance: int = Field(ge=0, le=3, description="Covers the promoted product type (e.g. refurbished/used/prebuilt PCs)")
    price_segment_relevance: int = Field(ge=0, le=3, description="Discusses products in the campaign price segment")
    first_hand_experience: int = Field(ge=0, le=3, description="Creator personally uses/tests/builds the product")
    benchmark_discussion: int = Field(ge=0, le=3, description="FPS/benchmarks/performance tests")
    price_discussion: int = Field(ge=0, le=3, description="Talks about prices, value for money, deals")
    product_comparison: int = Field(ge=0, le=3, description="Compares products/options")
    purchase_recommendation: int = Field(ge=0, le=3, description="Gives buying advice / recommendations")
    evidence: list[Evidence] = Field(description="Up to 6 quotes supporting the highest scores")
    summary: str = Field(description="One sentence on why this creator does or does not fit the campaign")


CommentLabel = Literal["meaningful", "technical", "question", "purchase_intent", "generic", "spam"]


class CommentLabelItem(BaseModel):
    i: int = Field(description="Index of the comment in the input list")
    label: CommentLabel
    lang: str = Field(description="ISO 639-1 language code of the comment")


class CommentBatch(BaseModel):
    items: list[CommentLabelItem]
