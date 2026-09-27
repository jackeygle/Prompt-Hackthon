"""Pydantic models: domain objects and the JSON contracts for every LLM extraction."""
from typing import Literal

from pydantic import BaseModel, Field, field_validator


# ---------- Campaign ----------
TOPIC_KINDS = {"game", "hardware", "product", "technology", "community", "event", "question", "topic"}
TOPIC_ALIASES = {"creator": "topic", "streamer": "topic", "channel": "topic", "person": "topic",
                 "tournament": "event", "league": "event", "esports": "event", "team": "community",
                 "gpu": "hardware", "cpu": "hardware", "software": "technology"}


class CurrentTopic(BaseModel):
    """A concrete, currently relevant thing the target audience follows (found via web search when available)."""
    name: str = Field(description="Specific name as people search for it, e.g. an actual game title, product "
                                  "model, event, community or recurring question; never a generic category")
    kind: Literal["game", "hardware", "product", "technology", "community", "event", "question", "topic"]
    why: str = Field(default="", description="<= 15 words: why this audience cares about it right now")

    @field_validator("kind", mode="before")
    @classmethod
    def _known_kind(cls, v):
        """Models sometimes invent kinds ('creator', 'patch', 'tournament', …). One odd label must not discard the
        whole research, so anything outside the list becomes a plain 'topic'."""
        v = str(v or "").strip().lower()
        return v if v in TOPIC_KINDS else TOPIC_ALIASES.get(v, "topic")


class ResearchQueries(BaseModel):
    queries: list[str] = Field(description=(
        "Web searches that reveal what the target audience in the market plays, watches, uses, buys and asks about "
        "right now (charts, most played / most watched, current releases, current products, current debates)"))


class TopicResearch(BaseModel):
    topics: list[CurrentTopic]

    @field_validator("topics", mode="before")
    @classmethod
    def _drop_unnamed(cls, v):
        return [t for t in (v or []) if not isinstance(t, dict) or str(t.get("name") or "").strip()]


class SearchPlan(BaseModel):
    """Audience -> current topic -> content -> creator. Content searches dominate; creator/list searches are secondary."""
    content_queries: list[str] = Field(default_factory=list, description=(
        "Searches a real viewer from the target audience would type to find VIDEOS about the concrete current topics "
        "(the channels behind those videos are the creators we want)"))
    creator_queries: list[str] = Field(default_factory=list,
                                       description="A few direct creator searches (best / top creators, lists)")
    web_queries: list[str] = Field(default_factory=list, description=(
        "Web searches to find creators and their profiles on other platforms: mostly topic-driven, one or two "
        "editorial creator lists, plus one each starting with 'site:tiktok.com', 'site:instagram.com', 'site:twitch.tv'"))


class CampaignSpecDraft(BaseModel):
    """What the LLM extracts from the brief. The user reviews/edits it before the run starts."""
    product: str = Field(description="What is being promoted, e.g. 'refurbished gaming PCs'")
    product_keywords: list[str] = Field(description="Terms indicating product relevance, in German and English")
    niche: str = Field(description="Content niche creators should be in, e.g. 'PC gaming hardware'")
    price_segment: str | None = Field(default=None, description=(
        "Product price segment if stated in the brief, e.g. '€600–900' (NOT the campaign budget); null if not stated"))
    target_country: str = Field(description="ISO 3166-1 alpha-2, e.g. 'DE'")
    target_language: str = Field(description="ISO 639-1, e.g. 'de'")
    audience: str = Field(description="Target audience description")
    audience_interests: list[str] = Field(default_factory=list, description=(
        "5-8 content topics this target audience already watches, broader than the product itself "
        "(e.g. for gaming PCs: PC gaming, competitive gaming, game performance/FPS, GPUs & hardware, "
        "gaming setups, tech reviews, budget gaming)"))
    goal: Literal["conversion", "awareness", "balanced"] = Field(default="balanced", description=(
        "Campaign priority. 'balanced' unless the brief explicitly says the campaign is mainly about sales / "
        "conversions ('conversion') or mainly about reach / awareness ('awareness')"))
    search_queries: list[str] = Field(description="6 diverse YouTube search queries, mostly in the target language")
    min_subscribers: int | None = Field(default=None, description="Minimum creator subscribers ONLY if explicitly "
                                        "stated in the brief (e.g. 'at least 10k' -> 10000), else null")
    max_subscribers: int | None = Field(default=None, description="Maximum creator subscribers ONLY if explicitly "
                                        "stated in the brief (e.g. 'under 500k' -> 500000), else null")
    web_queries: list[str] = Field(default_factory=list, description=(
        "6 web search queries to discover creators and their cross-platform profiles: 2 general queries "
        "(e.g. lists of best creators in the niche/country), then one each starting with "
        "'site:tiktok.com', 'site:instagram.com', 'site:twitch.tv', 'site:youtube.com'"))


class CampaignSpec(CampaignSpecDraft):
    """Reviewed campaign settings. Values not stated in the brief carry recommended defaults;
    `field_sources` records which ("brief" vs "default") so the UI never presents a default as extracted."""
    price_segment: str = ""
    min_subscribers: int = 2_000
    max_subscribers: int = 3_000_000
    n_creators: int = 10            # creators analysed in depth (set by the user, drives API cost)
    budget_per_video: int = 0       # max cost proxy per video in €, 0 = no limit (flags, orders and gates top picks; never the score)
    creator_queries: list[str] = Field(default_factory=list)       # secondary, direct "top creator" YouTube searches
    current_topics: list[CurrentTopic] = Field(default_factory=list)  # what the audience follows now (search strategy)
    topics_source: str = ""         # "web search" | "model knowledge" (no web provider or it failed)
    strategy_notes: list[str] = Field(default_factory=list)  # what went wrong while researching / planning (shown)
    field_sources: dict[str, str] = Field(default_factory=dict)


# ---------- Web discovery ----------
class WebCreatorMention(BaseModel):
    name: str = Field(description="Creator / channel name as written in the source")
    platform: Literal["youtube", "tiktok", "instagram", "twitch", "x", "website", "unknown"]
    handle: str | None = Field(default=None, description="Handle without @ if explicitly present, else null")
    source_index: int = Field(description="Index [n] of the search result where this creator is mentioned")
    evidence: str = Field(description="Short verbatim snippet (<= 20 words) from that result naming the creator")


class WebCreatorMentions(BaseModel):
    creators: list[WebCreatorMention]


class WebSearchCreator(BaseModel):
    """A creator nominated by ChatGPT web search (verified later against official APIs)."""
    name: str
    platform: str = "unknown"
    handle: str | None = None
    profile_url: str | None = None
    source_url: str | None = None
    evidence: str | None = None


class WebSearchCreators(BaseModel):
    creators: list[WebSearchCreator] = []


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
    audience_relevance: int = Field(ge=0, le=3, description=(
        "How strongly this content attracts the campaign's TARGET AUDIENCE (see audience interests), whether or "
        "not it covers the product itself. E.g. popular PC-gaming or game-performance content scores high for a "
        "gaming-PC campaign even without any PC building"))
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


# ---------- Twitch / Instagram fit (one judgement per profile, from its own text only)
class PlatformFitItem(BaseModel):
    i: int = Field(description="Index of the profile in the input list")
    audience_relevance: int = Field(ge=0, le=3, description=(
        "How strongly this creator's content attracts the campaign's TARGET AUDIENCE (see audience interests): "
        "0 none, 1 weak/occasional, 2 clearly, 3 central"))
    content_language: str = Field(description="ISO 639-1 code of the language most of the given text is written in")
    summary: str = Field(description="One sentence on why this creator does or does not reach the target audience")
    evidence: list[str] = Field(description="Up to 2 short verbatim quotes (<= 15 words) copied from the profile text")


class PlatformFitBatch(BaseModel):
    items: list[PlatformFitItem]
