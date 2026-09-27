"""Instagram Business Discovery with a fake Graph API (no network)."""
from datetime import datetime, timedelta, timezone

import pytest

import instagram

NOW = datetime.now(timezone.utc)
TS = lambda days: (NOW - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S+0000")

ACCOUNTS = {
    "xwaretv": {"username": "xwaretv", "name": "Xware", "followers_count": 10_000, "media_count": 300,
                "media": {"data": [{"like_count": 400, "comments_count": 20, "timestamp": TS(3)},
                                   {"like_count": 200, "comments_count": 10, "timestamp": TS(12)},
                                   {"comments_count": 5, "timestamp": TS(60)}]}},  # hidden like count
}


def fake_get(path, params, **kwargs):
    if path == "me/accounts":
        return {"data": [{"name": "Page", "instagram_business_account": {"id": "1784"}}]}
    assert path == "1784"
    user = params["fields"].split("username(")[1].split(")")[0]
    if user == "expired":
        raise instagram.InstagramError("Instagram 190: token expired", fatal=True)
    if user not in ACCOUNTS:
        raise instagram.InstagramError("Instagram 110: Invalid user id")  # personal / unknown account
    return {"business_discovery": ACCOUNTS[user], "id": "1784"}


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setattr(instagram.config, "INSTAGRAM_ACCESS_TOKEN", "t")
    monkeypatch.setattr(instagram.config, "INSTAGRAM_USER_ID", "")
    monkeypatch.setattr(instagram, "_get", fake_get)
    instagram._me.clear()


def test_profile_metrics(fake):
    p = instagram.profile("@XwareTV")
    assert p["followers"] == 10_000 and p["n_posts"] == 3
    assert p["median_likes"] == 300  # the hidden-likes post is not counted as 0 likes
    assert p["engagement_rate"] == pytest.approx((420 + 210) / 2 / 10_000)
    assert p["posts_last_30d"] == 2


def test_professional_account_is_discovered_from_page_link(fake):
    assert instagram.my_ig_id() == "1784"


def test_personal_accounts_are_skipped_and_token_errors_stop(fake):
    out, errors = instagram.profiles(["xwaretv", "someone_private", "expired", "never_called"])
    assert list(out) == ["xwaretv"]
    assert len(errors) == 1 and "190" in errors[0]


def test_missing_token(monkeypatch):
    monkeypatch.setattr(instagram.config, "INSTAGRAM_ACCESS_TOKEN", "")
    assert not instagram.available()
