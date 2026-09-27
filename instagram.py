"""Instagram Graph API — Business Discovery (Facebook Login token). Enrichment only, no discovery.

Given an Instagram username (from YouTube descriptions, web discovery or Twitch bios), returns the public numbers
of a professional (creator/business) account: followers, media count and the latest posts' likes/comments.
Personal accounts, audience demographics and keyword search are not available through this API.

Needs INSTAGRAM_ACCESS_TOKEN (user token with instagram_basic + pages_show_list + pages_read_engagement +
business_management). The caller's own IG professional account id is found via /me/accounts unless
INSTAGRAM_USER_ID is set. Responses are cached per day in SQLite, except the
Page-to-Instagram relationship lookup, which is always refreshed so a newly
linked Professional account is not hidden by a stale negative result.
"""
import hashlib
import json
import logging
import statistics
from datetime import datetime, timedelta, timezone

import requests

import config
import db
from collectors import InstagramCollector

log = logging.getLogger(__name__)
GRAPH = f"https://graph.facebook.com/{config.INSTAGRAM_GRAPH_VERSION}/"
FIELDS = ("username,name,followers_count,follows_count,media_count,biography,profile_picture_url,website,"
          "media.limit({n}){{caption,like_count,comments_count,timestamp,permalink,media_type}}")


class InstagramError(RuntimeError):
    def __init__(self, message: str, fatal: bool = False):
        super().__init__(message)
        self.fatal = fatal  # token/network problem: stop further calls in this run


class InstagramUsage:
    calls = 0


_me: dict = {}


def available() -> bool:
    return bool(config.INSTAGRAM_ACCESS_TOKEN)


def _get(path: str, params: dict, *, use_cache: bool = True) -> dict:
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    key = "ig:" + hashlib.sha256(json.dumps([path, params, day], sort_keys=True).encode()).hexdigest()
    if use_cache and (hit := db.cache_get(key)) is not None:
        return hit
    if not available():
        raise InstagramError("environment variable INSTAGRAM_ACCESS_TOKEN is not set", fatal=True)
    try:
        r = requests.get(GRAPH + path, params={**params, "access_token": config.INSTAGRAM_ACCESS_TOKEN}, timeout=20)
    except requests.RequestException as e:
        raise InstagramError(f"Instagram Graph API unreachable: {e}", fatal=True) from e
    InstagramUsage.calls += 1
    data = r.json()
    if "error" in data:
        err = data["error"]
        code = err.get("code")
        # 190 = expired/invalid token, 4/17/32/613 = rate limits, 10/200 = missing permission
        fatal = code in (190, 4, 17, 32, 613, 10, 200)
        raise InstagramError(f"Instagram {code}: {err.get('message', '')[:160]}", fatal=fatal)
    if use_cache:
        db.cache_put(key, data)
    return data


def my_ig_id() -> str:
    if config.INSTAGRAM_USER_ID:
        return config.INSTAGRAM_USER_ID
    if "id" not in _me:
        # This relationship can change after the app has already run. Do not
        # reuse an old daily cache entry, especially a previous empty response.
        pages = _get("me/accounts", {"fields": "name,instagram_business_account"}, use_cache=False).get("data", [])
        ids = [p["instagram_business_account"]["id"] for p in pages if p.get("instagram_business_account")]
        if not ids:
            raise InstagramError("no Instagram professional account is linked to the token's Facebook Page",
                                 fatal=True)
        _me["id"] = ids[0]
    return _me["id"]


def _metrics(bd: dict) -> dict:
    posts = (bd.get("media") or {}).get("data", [])
    followers = bd.get("followers_count")
    likes = [p["like_count"] for p in posts if p.get("like_count") is not None]  # hidden likes -> absent
    comments = [p.get("comments_count") or 0 for p in posts]
    eng = [p.get("like_count", 0) + (p.get("comments_count") or 0) for p in posts if p.get("like_count") is not None]
    times = sorted(p["timestamp"] for p in posts if p.get("timestamp"))
    recent = [t for t in times if datetime.fromisoformat(t.replace("+0000", "+00:00"))
              >= datetime.now(timezone.utc) - timedelta(days=30)]
    return {
        "username": bd.get("username"), "name": bd.get("name"), "followers": followers,
        "media_count": bd.get("media_count"), "biography": (bd.get("biography") or "")[:500],
        "avatar": bd.get("profile_picture_url"), "website": bd.get("website"),
        "url": f"https://www.instagram.com/{bd.get('username')}/",
        "n_posts": len(posts), "median_likes": int(statistics.median(likes)) if likes else None,
        "median_comments": int(statistics.median(comments)) if comments else None,
        "engagement_rate": (statistics.mean(eng) / followers) if eng and followers else None,
        "posts_last_30d": len(recent), "last_post_at": times[-1] if times else None,
        "captions": [(p.get("caption") or "")[:200] for p in posts[:6]],
        "source_reliability": InstagramCollector.source_reliability,
    }


def profile(username: str) -> dict | None:
    """None when the account does not exist or is a personal account (Business Discovery cannot see it)."""
    u = username.lstrip("@").strip().lower()
    try:
        data = _get(my_ig_id(), {"fields": f"business_discovery.username({u}){{{FIELDS.format(n=config.INSTAGRAM_MEDIA)}}}"})
    except InstagramError as e:
        if e.fatal:
            raise
        log.info("instagram %s: %s", u, e)
        return None
    bd = data.get("business_discovery")
    return _metrics(bd) if bd else None


def profiles(usernames: list[str]) -> tuple[dict[str, dict], list[str]]:
    """username(lower) -> metrics. Stops at the first token/network/rate-limit error."""
    out, errors = {}, []
    for u in list(dict.fromkeys(x.lstrip("@").lower() for x in usernames if x))[:config.INSTAGRAM_MAX_PROFILES]:
        try:
            if p := profile(u):
                out[u] = p
        except InstagramError as e:
            errors.append(str(e))
            break
    return out, errors
