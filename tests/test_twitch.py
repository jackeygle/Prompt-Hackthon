"""Twitch discovery with a fake Helix API (no network)."""
import twitch
import web_discovery as web
from models import CampaignSpec

SPEC = CampaignSpec(product="refurbished gaming PCs", product_keywords=[], niche="PC gaming", target_country="DE",
                    target_language="de", audience="gamers", audience_interests=["PC gaming"], goal="conversion",
                    search_queries=["q"], min_subscribers=2_000, max_subscribers=3_000_000)

USERS = {
    "pcmax": {"id": "1", "login": "pcmax", "display_name": "PCMax", "profile_image_url": "img",
              "description": "Videos: https://www.youtube.com/@PCMaxTV"},
    "tinystream": {"id": "2", "login": "tinystream", "display_name": "TinyStream", "description": ""},
    "englishguy": {"id": "3", "login": "englishguy", "display_name": "EnglishGuy", "description": ""},
}
CHANNELS = {"1": ("de", "Counter-Strike"), "2": ("de", "Minecraft"), "3": ("en", "Valorant")}
FOLLOWERS = {"1": 120_000, "2": 150, "3": 90_000}


def fake_get(endpoint, params):
    q = dict(params)
    if endpoint == "search/categories":
        return {"data": [{"id": "509670", "name": "Science & Technology"}]}
    if endpoint == "games/top":
        return {"data": [{"id": "32399", "name": "Counter-Strike"}]}
    if endpoint == "streams":
        return {"data": [{"user_login": "pcmax", "viewer_count": 900, "game_name": "Counter-Strike"},
                         {"user_login": "tinystream", "viewer_count": 3, "game_name": "Minecraft"}]}
    if endpoint == "search/channels":
        return {"data": [{"broadcaster_login": "englishguy", "broadcaster_language": "en"}]}
    if endpoint == "users":
        return {"data": [USERS[v] for k, v in params if k == "login" and v in USERS]}
    if endpoint == "channels":
        return {"data": [{"broadcaster_id": v, "broadcaster_language": CHANNELS[v][0], "game_name": CHANNELS[v][1]}
                         for k, v in params if k == "broadcaster_id"]}
    if endpoint == "channels/followers":
        return {"total": FOLLOWERS[q["broadcaster_id"]]}
    if endpoint == "videos":
        return {"data": [{"view_count": 400, "created_at": "2026-09-20T18:00:00Z"},
                         {"view_count": 800, "created_at": "2026-09-22T18:00:00Z"}]}
    raise AssertionError(endpoint)


def _patch(monkeypatch):
    monkeypatch.setattr(twitch.config, "TWITCH_CLIENT_ID", "id")
    monkeypatch.setattr(twitch.config, "TWITCH_CLIENT_SECRET", "secret")
    monkeypatch.setattr(twitch, "_get", fake_get)


def test_discover_filters_language_and_follower_range(monkeypatch):
    _patch(monkeypatch)
    ids, profs, errors = twitch.discover(SPEC, extra_logins=["englishguy"])
    assert errors == []
    assert profs["pcmax"]["followers"] == 120_000 and profs["pcmax"]["median_vod_views"] == 600
    assert profs["pcmax"]["last_stream_at"] == "2026-09-22T18:00:00Z"
    twitch_handles = {i["handle"] for i in ids if i["platform"] == "twitch"}
    # tinystream is below the follower minimum; englishguy came from the web, so its language is not filtered
    assert twitch_handles == {"pcmax", "englishguy"}
    yt = [i for i in ids if i["platform"] == "youtube"]
    assert yt and yt[0]["handle"] == "PCMaxTV" and yt[0]["twitch_login"] == "pcmax"
    assert all(i["source"] == "twitch_api" for i in ids)


def test_missing_twitch_keys_skip_quietly(monkeypatch):
    monkeypatch.setattr(twitch.config, "TWITCH_CLIENT_ID", "")
    ids, profs, errors = twitch.discover(SPEC)
    assert ids == [] and profs == {} and "TWITCH_CLIENT_ID" in errors[0]


def test_twitch_only_streamers_not_listed_as_web_only():
    chans = {"UC1": {"id": "UC1", "snippet": {"title": "PCMax", "customUrl": "@pcmaxtv"}}}
    base = {"url": None, "query": "q", "source_url": "u", "evidence": "e", "method": "twitch_api",
            "source": "twitch_api", "kind": "handle"}
    idents = [{**base, "platform": "twitch", "handle": "pcmax", "name": "PCMax"},
              {**base, "platform": "twitch", "handle": "zz", "name": "zz"}]  # too short for a YouTube lookup
    matched, web_only = web.resolve_to_youtube(idents, chans)
    assert [i["handle"] for i in matched["UC1"]] == ["pcmax"]
    assert web_only == []
