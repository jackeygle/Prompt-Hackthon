"""Central configuration. All tunables for a demo run live here."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

DB_PATH = Path(os.getenv("DB_PATH", ROOT / "data" / "creator_intel.db"))

# LLM: "provider/model". Tried in order; later entries are fallbacks on overload/errors.
LLM_MODELS = [m.strip() for m in os.getenv(
    "LLM_MODELS", "gemini/gemini-3.8-flash,gemini/gemini-3.5-flash,gemini/gemini-3.1-flash-lite"
).split(",") if m.strip()]
LLM_CONCURRENCY = int(os.getenv("LLM_CONCURRENCY", "6"))

# Transcripts: unofficial library works from residential IPs, usually not from cloud.
TRANSCRIPTS_ENABLED = os.getenv("TRANSCRIPTS_ENABLED", "1") == "1"

# Pipeline sizes (see assessment §7)
N_QUERIES = 6
SEARCH_RESULTS_PER_QUERY = 50
MIN_SUBSCRIBERS = 2_000
MAX_SUBSCRIBERS = 3_000_000
N_AFTER_CHEAP_FILTER = 25
UPLOADS_TO_SCAN = 30
N_DEEP = 15
VIDEOS_PER_CREATOR = 5
COMMENTS_PER_VIDEO = 50
TRANSCRIPT_CHARS = 12_000

# Hard filter
MAX_DAYS_SINCE_UPLOAD = 120
MIN_RELEVANT_VIDEOS = 2
MIN_NICHE_RELEVANCE = 1.0   # 0..3 scale
MIN_TARGET_LANG_SHARE = 0.3  # share of comments in the target language (audience-country proxy)

ASSUMED_CPM_EUR = 20.0      # assumption for cost estimate, shown in UI
LLM_RPM = int(os.getenv("LLM_RPM", "5"))   # per model; Gemini free tier = 5
