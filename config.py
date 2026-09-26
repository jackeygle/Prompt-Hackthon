"""Central configuration. All tunables for a demo run live here."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "")
AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY", "")
AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")

DB_PATH = Path(os.getenv("DB_PATH", ROOT / "data" / "creator_intel.db"))

# Text LLM: "provider/model", tried in order; later entries are fallbacks (missing key, 404, rate limit).
# Groq is primary (fast, free tier). Gemini stays as a fallback because it is already configured.
LLM_MODELS = [m.strip() for m in os.getenv(
    "LLM_MODELS",
    "groq/meta-llama/llama-4-scout-17b-16e-instruct,groq/openai/gpt-oss-20b,"
    "gemini/gemini-3.5-flash,gemini/gemini-3.1-flash-lite",
).split(",") if m.strip()]
# Vision: cheapest OpenAI vision models first; images are sent as URLs with detail=low.
VLM_MODELS = [m.strip() for m in os.getenv(
    "VLM_MODELS", "openai/gpt-4.1-nano,openai/gpt-5-nano,openai/gpt-4o-mini"
).split(",") if m.strip()]
LLM_CONCURRENCY = int(os.getenv("LLM_CONCURRENCY", "6"))
# Requests per minute per model, by provider (free-tier defaults; raise when on paid tiers)
PROVIDER_RPM = {"gemini": int(os.getenv("GEMINI_RPM", "5")), "groq": int(os.getenv("GROQ_RPM", "25")),
                "openai": int(os.getenv("OPENAI_RPM", "60")), "anthropic": 50}

# Transcripts: unofficial library works from residential IPs, usually not from cloud.
TRANSCRIPTS_ENABLED = os.getenv("TRANSCRIPTS_ENABLED", "1") == "1"

# Pipeline sizes (see assessment §7)
N_QUERIES = int(os.getenv("N_QUERIES", "6"))
SEARCH_RESULTS_PER_QUERY = int(os.getenv("SEARCH_RESULTS_PER_QUERY", "50"))
MIN_SUBSCRIBERS = 2_000
MAX_SUBSCRIBERS = 3_000_000
DEFAULT_N_CREATORS = int(os.getenv("DEFAULT_N_CREATORS", "10"))
N_CREATOR_OPTIONS = [5, 10, 20, 50]
N_AFTER_CHEAP_FILTER = int(os.getenv("N_AFTER_CHEAP_FILTER", "25"))
MAX_SCREENED = int(os.getenv("MAX_SCREENED", "100"))
UPLOADS_TO_SCAN = int(os.getenv("UPLOADS_TO_SCAN", "30"))
N_DEEP = int(os.getenv("N_DEEP", "15"))
VIDEOS_PER_CREATOR = int(os.getenv("VIDEOS_PER_CREATOR", "5"))
COMMENTS_PER_VIDEO = int(os.getenv("COMMENTS_PER_VIDEO", "50"))
TRANSCRIPT_CHARS = 6_000   # per video; keeps content prompts within Groq free-tier TPM

# Hard filter
MAX_DAYS_SINCE_UPLOAD = 120
MIN_RELEVANT_VIDEOS = 2
MIN_NICHE_RELEVANCE = 1.0   # 0..3 scale
MIN_TARGET_LANG_SHARE = 0.3  # share of comments in the target language (audience-country proxy)

ASSUMED_CPM_EUR = 20.0      # ASSUMED CPM for the cost proxy. Not a creator quote; shown as such in the UI.

# Web discovery (Tavily). basic search = 1 credit; dev tier ~1,000 credits/month.
WEB_DISCOVERY_ENABLED = os.getenv("WEB_DISCOVERY_ENABLED", "1") == "1"
TAVILY_MAX_QUERIES = 6
TAVILY_RESULTS_PER_QUERY = 10
MAX_WEB_HANDLE_LOOKUPS = 15  # channels.list forHandle lookups (1 quota unit each)

# Visual analysis (VLM) on official YouTube thumbnails
VLM_ENABLED = os.getenv("VLM_ENABLED", "1") == "1"
VLM_IMAGES_PER_CREATOR = 4
MIN_CRITERION_COVERAGE = 0.5  # criteria known for fewer candidates than this are dropped from TOPSIS
