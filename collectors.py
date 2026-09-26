"""Platform collector interface. YouTube is the only deep collector in the MVP.

Other platforms declare what they can do; missing capabilities lower confidence instead of failing.
"""
import re
from enum import Flag, auto
from typing import Protocol


class Capability(Flag):
    DISCOVER = auto()
    PROFILE_STATS = auto()
    CONTENT = auto()
    COMMENTS = auto()
    AUDIENCE = auto()


class PlatformCollector(Protocol):
    platform: str
    capabilities: Capability
    source_reliability: float  # 1.0 official API, 0.7 licensed provider, 0.4 web snippet


class YouTubeCollector:
    """Implemented functionally in youtube.py / pipeline.py."""
    platform = "youtube"
    capabilities = Capability.DISCOVER | Capability.PROFILE_STATS | Capability.CONTENT | Capability.COMMENTS
    source_reliability = 1.0


class LinkedAccountCollector:
    """Other-platform handles parsed from YouTube descriptions. Identity only, no metrics."""
    platform = "multi"
    capabilities = Capability.DISCOVER
    source_reliability = 0.4


class ProviderCollector:
    """Stub for a licensed creator-data vendor (TikTok/Instagram metrics, audience geography).
    Production path; not used in the MVP."""
    platform = "provider"
    capabilities = Capability.PROFILE_STATS | Capability.AUDIENCE
    source_reliability = 0.7

    def get_profile(self, platform: str, handle: str):
        raise NotImplementedError("Configure a data provider to enable this collector")


SOCIAL_PATTERNS = {
    "tiktok": r"tiktok\.com/@([A-Za-z0-9_.]{2,30})",
    "instagram": r"instagram\.com/([A-Za-z0-9_.]{2,30})",
    "twitch": r"twitch\.tv/([A-Za-z0-9_]{3,25})",
    "x": r"(?:twitter|x)\.com/([A-Za-z0-9_]{2,15})",
    "facebook": r"facebook\.com/([A-Za-z0-9.]{3,50})",
}
_IGNORE = {"p", "reel", "reels", "explore", "stories", "share", "intent", "home", "watch", "videos", "hashtag"}


def extract_social_links(*texts: str) -> dict[str, str]:
    """First handle per platform found in the given texts."""
    found: dict[str, str] = {}
    blob = "\n".join(t for t in texts if t)
    for platform, pat in SOCIAL_PATTERNS.items():
        for m in re.finditer(pat, blob, flags=re.I):
            handle = m.group(1).rstrip(".")
            if handle.lower() not in _IGNORE:
                found[platform] = handle
                break
    return found
