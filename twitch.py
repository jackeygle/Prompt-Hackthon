"""Twitch Helix API (app access token, free). Discovery + real channel metrics for streamers.

Discovery: live streams in the target language within relevant categories (audience interests, top games,
Science & Technology) + live channel search + Twitch handles found by web discovery (verified here).
Metrics per channel: follower total, main category, recent VOD views, last stream date, profile description
(YouTube links in it are used to merge the streamer with their YouTube channel).

Every GET is cached in SQLite per day (live data changes daily; re-runs on the same day cost nothing).
"""
import hashlib
import json
import logging
import statistics
import time
from datetime import datetime, timedelta, timezone

import requests

import config
import db
from collectors import TwitchCollector

log = logging.getLogger(__name__)
HELIX = "https://api.twitch.tv/helix/"
SCIENCE_TECH = "Science & Technology"


class TwitchError(RuntimeError):
    pass


class TwitchUsage:
    calls = 0  # real (non-cached) Helix calls


_token: dict = {}


def available() -> bool:
    return bool(config.TWITCH_CLIENT_ID and config.TWITCH_CLIENT_SECRET)


def _access_token() -> str:
    if _token.get("value") and _token["expires"] > time.time() + 60:
        return _token["value"]
    if not available():
        raise TwitchError("environment variables TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET are not set")
    try:
        r = requests.post("https://id.twitch.tv/oauth2/token", timeout=20, data={
            "client_id": config.TWITCH_CLIENT_ID, "client_secret": config.TWITCH_CLIENT_SECRET,
            "grant_type": "client_credentials"})
    except requests.RequestException as e:
        raise TwitchError(f"Twitch unreachable: {e}") from e
    if r.status_code != 200:
        raise TwitchError(f"Twitch token HTTP {r.status_code}: {r.text[:200]}")
    data = r.json()
    _token.update(value=data["access_token"], expires=time.time() + data.get("expires_in", 3600))
    return _token["value"]


def _get(endpoint: str, params: list[tuple[str, str]]) -> dict:
    """params as a list of pairs: Helix repeats keys (login=a&login=b, game_id=1&game_id=2)."""
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    key = "tw:" + hashlib.sha256(json.dumps([endpoint, sorted(params), day]).encode()).hexdigest()
    if (hit := db.cache_get(key)) is not None:
        return hit
    headers = {"Client-Id": config.TWITCH_CLIENT_ID, "Authorization": f"Bearer {_access_token()}"}
    try:
        r = requests.get(HELIX + endpoint, params=params, headers=headers, timeout=20)
    except requests.RequestException as e:
        raise TwitchError(f"Twitch unreachable: {e}") from e
    TwitchUsage.calls += 1
    if r.status_code == 401:  # token expired/revoked -> one retry with a fresh token
        _token.clear()
        headers["Authorization"] = f"Bearer {_access_token()}"
        r = requests.get(HELIX + endpoint, params=params, headers=headers, timeout=20)
    if r.status_code != 200:
        raise TwitchError(f"Twitch {endpoint} HTTP {r.status_code}: {r.text[:200]}")
    data = r.json()
    db.cache_put(key, data)
    return data


# ------------------------------------------------------------------ endpoints
def search_categories(query: str, first: int = 3) -> list[dict]:
    return _get("search/categories", [("query", query), ("first", str(first))]).get("data", [])


def top_games(first: int = 20) -> list[dict]:
    return _get("games/top", [("first", str(first))]).get("data", [])


def live_streams(language: str, game_ids: list[str], pages: int = 2) -> list[dict]:
    out, after = [], None
    for _ in range(pages):
        params = [("language", language), ("first", "100")] + [("game_id", g) for g in game_ids[:100]]
        if after:
            params.append(("after", after))
        data = _get("streams", params)
        out += data.get("data", [])
        after = (data.get("pagination") or {}).get("cursor")
        if not after:
            break
    return out


def search_live_channels(query: str, first: int = 50) -> list[dict]:
    # live_only=true matches on display name AND category name (false matches login names only)
    return _get("search/channels", [("query", query), ("first", str(first)), ("live_only", "true")]).get("data", [])


def users(logins: list[str]) -> list[dict]:
    out = []
    for i in range(0, len(logins), 100):
        out += _get("users", [("login", l.lower()) for l in logins[i:i + 100]]).get("data", [])
    return out


def follower_total(broadcaster_id: str) -> int | None:
    try:
        return _get("channels/followers", [("broadcaster_id", broadcaster_id)]).get("total")
    except TwitchError as e:
        log.info("followers %s: %s", broadcaster_id, e)
        return None


def recent_vods(user_id: str, first: int = 20) -> list[dict]:
    try:
        return _get("videos", [("user_id", user_id), ("type", "archive"), ("first", str(first))]).get("data", [])
    except TwitchError as e:
        log.info("videos %s: %s", user_id, e)
        return []


def channel_info(broadcaster_ids: list[str]) -> list[dict]:
    out = []
    for i in range(0, len(broadcaster_ids), 100):
        out += _get("channels", [("broadcaster_id", b) for b in broadcaster_ids[i:i + 100]]).get("data", [])
    return out


# ------------------------------------------------------------------ profiles
def profiles(logins: list[str]) -> dict[str, dict]:
    """login -> real metrics. Unknown logins are simply absent (no fabrication)."""
    from collectors import extract_social_links
    from web_discovery import identities_from_url_text
    us = users(list(dict.fromkeys(l.lower() for l in logins if l)))
    info = {c["broadcaster_id"]: c for c in channel_info([u["id"] for u in us])} if us else {}
    out = {}
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    for u in us:
        vods = recent_vods(u["id"])
        views = [v["view_count"] for v in vods if v.get("view_count") is not None]
        ch = info.get(u["id"], {})
        desc = u.get("description") or ""
        out[u["login"].lower()] = {
            "login": u["login"], "name": u.get("display_name") or u["login"], "id": u["id"],
            "url": f"https://www.twitch.tv/{u['login']}", "avatar": u.get("profile_image_url"),
            "description": desc[:500], "language": ch.get("broadcaster_language"), "game": ch.get("game_name"),
            "followers": follower_total(u["id"]),
            "n_vods": len(vods), "median_vod_views": int(statistics.median(views)) if views else None,
            "vod_titles": [v.get("title", "") for v in vods[:10]],
            "streams_30d": sum(v["created_at"] >= cutoff for v in vods),
            "last_stream_at": max((v["created_at"] for v in vods), default=None),
            "youtube": [i for i in identities_from_url_text(desc) if i["platform"] == "youtube"],
            "links": extract_social_links(desc),
            "source_reliability": TwitchCollector.source_reliability,
        }
    return out


def discover(spec, extra_logins: list[str] | None = None, progress=None) -> tuple[list[dict], dict[str, dict], list[str]]:
    """Returns (identities, profiles by login, errors).

    identities use the web-discovery shape so they merge with YouTube channels the same way.
    """
    errors: list[str] = []
    if not available():
        return [], {}, ["environment variables TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET are not set"]
    seen: dict[str, dict] = {}  # login -> {"viewers", "query"}
    try:
        if progress:
            progress("Twitch: finding relevant categories", 0.0)
        cats: dict[str, str] = {}
        games = [t.name for t in getattr(spec, "current_topics", []) if t.kind == "game"][:6]  # researched, current
        for q in [SCIENCE_TECH, *games, *spec.audience_interests[:6]]:
            for c in search_categories(q, 1 if q == SCIENCE_TECH else 2):
                cats.setdefault(c["id"], c["name"])
        for g in top_games(config.TWITCH_TOP_GAMES):
            cats.setdefault(g["id"], g["name"])
        if progress:
            progress(f"Twitch: live {spec.target_language.upper()} streams in {len(cats)} categories", 0.3)
        for s in live_streams(spec.target_language, list(cats)):
            login = s["user_login"].lower()
            if login not in seen or s.get("viewer_count", 0) > seen[login]["viewers"]:
                seen[login] = {"viewers": s.get("viewer_count", 0), "query": f"live · {s.get('game_name', '')}"}
        for q in [spec.niche, *spec.audience_interests[:2]]:
            for c in search_live_channels(q):
                if c.get("broadcaster_language") == spec.target_language:
                    seen.setdefault(c["broadcaster_login"].lower(), {"viewers": 0, "query": q})
    except TwitchError as e:
        errors.append(str(e))
        if "unreachable" in str(e) or "not set" in str(e) or "token" in str(e):
            return [], {}, errors

    live = sorted(seen, key=lambda l: -seen[l]["viewers"])[:config.TWITCH_MAX_PROFILES]
    web_logins = [l.lower() for l in extra_logins or []][:config.TWITCH_MAX_PROFILES]
    if progress:
        progress(f"Twitch: loading {len(set(live + web_logins))} channel profiles", 0.6)
    try:
        profs = profiles(live + web_logins)
    except TwitchError as e:
        return [], {}, errors + [str(e)]

    identities = []
    for login, p in profs.items():
        if login not in web_logins and p["language"] and p["language"] != spec.target_language:
            continue
        if p["followers"] is not None and not (spec.min_subscribers <= p["followers"] <= spec.max_subscribers):
            continue
        how = seen.get(login, {"query": "web discovery handle"})["query"]
        ev = " · ".join(x for x in [p["game"], f"{p['followers']:,} followers" if p["followers"] is not None else ""] if x)
        base = {"name": p["name"], "url": p["url"], "query": how, "source_url": p["url"], "evidence": ev,
                "method": "twitch_api", "source": "twitch_api"}
        identities.append({**base, "platform": "twitch", "handle": p["login"], "kind": "handle"})
        for y in p["youtube"]:  # YouTube link in the Twitch bio: exact identity merge
            identities.append({**base, "platform": "youtube", "handle": y["handle"], "kind": y["kind"],
                               "twitch_login": login})
    return identities, profs, errors
