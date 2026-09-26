"""YouTube Data API v3 wrapper (API key only). Every response is cached in SQLite to save quota.

Quota costs: search.list=100, everything else here=1 per call.
"""
import hashlib
import json
import logging
import re

import requests

import config
import db

log = logging.getLogger(__name__)
BASE = "https://www.googleapis.com/youtube/v3/"


class QuotaUsage:
    units = 0


class YouTubeError(RuntimeError):
    def __init__(self, reason: str, message: str):
        super().__init__(f"{reason}: {message}")
        self.reason = reason


def _get(endpoint: str, cost: int, **params) -> dict:
    key = "yt:" + hashlib.sha256(json.dumps([endpoint, params], sort_keys=True).encode()).hexdigest()
    if (hit := db.cache_get(key)) is not None:
        return hit
    r = requests.get(BASE + endpoint, params={**params, "key": config.YOUTUBE_API_KEY}, timeout=30)
    QuotaUsage.units += cost
    data = r.json()
    if "error" in data:
        err = data["error"]
        reason = (err.get("errors") or [{}])[0].get("reason", str(err.get("code")))
        raise YouTubeError(reason, err.get("message", ""))
    db.cache_put(key, data)
    return data


def _chunks(xs: list, n: int = 50):
    for i in range(0, len(xs), n):
        yield xs[i:i + n]


def search_videos(q: str, region: str, lang: str, published_after: str, max_results: int = 50) -> list[dict]:
    data = _get("search", 100, part="snippet", q=q, type="video", regionCode=region,
                relevanceLanguage=lang, publishedAfter=published_after, maxResults=max_results)
    return data.get("items", [])


def channels(ids: list[str]) -> list[dict]:
    out = []
    for chunk in _chunks(ids):
        out += _get("channels", 1, part="snippet,statistics,contentDetails,brandingSettings",
                    id=",".join(chunk), maxResults=50).get("items", [])
    return out


def channel_by_handle(handle: str) -> dict | None:
    items = _get("channels", 1, part="snippet,statistics,contentDetails,brandingSettings",
                 forHandle=handle if handle.startswith("@") else "@" + handle).get("items", [])
    return items[0] if items else None


def playlist_video_ids(playlist_id: str, n: int) -> list[str]:
    try:
        data = _get("playlistItems", 1, part="contentDetails", playlistId=playlist_id, maxResults=min(n, 50))
    except YouTubeError as e:
        log.warning("uploads playlist %s: %s", playlist_id, e)
        return []
    return [i["contentDetails"]["videoId"] for i in data.get("items", [])]


def videos(ids: list[str]) -> list[dict]:
    out = []
    for chunk in _chunks(ids):
        out += _get("videos", 1, part="snippet,statistics,contentDetails",
                    id=",".join(chunk), maxResults=50).get("items", [])
    return out


def comment_threads(video_id: str, n: int) -> list[dict] | None:
    """Returns None when comments are disabled/unavailable (not the same as zero comments)."""
    try:
        data = _get("commentThreads", 1, part="snippet,replies", videoId=video_id,
                    maxResults=min(n, 100), order="relevance", textFormat="plainText")
    except YouTubeError as e:
        if e.reason in ("commentsDisabled", "forbidden", "videoNotFound"):
            return None
        raise
    return data.get("items", [])


def iso_duration_s(d: str) -> int:
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", d or "")
    if not m:
        return 0
    days, h, mi, s = (int(x or 0) for x in m.groups())
    return days * 86400 + h * 3600 + mi * 60 + s


def transcript(video_id: str, langs=("de", "en")) -> str | None:
    """Unofficial transcript fetch. Often blocked from cloud IPs; callers must handle None."""
    if not config.TRANSCRIPTS_ENABLED:
        return None
    key = f"tr:{video_id}"
    if (hit := db.cache_get(key)) is not None:
        return hit or None
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        t = YouTubeTranscriptApi().fetch(video_id, languages=list(langs))
        text = " ".join(s.text for s in t.snippets)
    except Exception as e:
        msg = str(e)
        log.info("transcript %s unavailable: %s", video_id, msg[:120])
        if "ProxyError" in type(e).__name__ or "Proxy" in msg or "IpBlocked" in type(e).__name__:
            config.TRANSCRIPTS_ENABLED = False  # blocked environment: stop trying for this run
            return None
        text = ""
    db.cache_put(key, text)
    return text or None
