"""Shortlist management + sponsorship tracking: order, favorites, sponsorship lifecycle, KPIs, derived metrics,
feedback history. Pure business logic on a real SQLite file (so persistence across restarts is tested too)."""
import sqlite3

import pytest

import config
import db
import relationships as rel


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = tmp_path / "rel.db"
    monkeypatch.setattr(config, "DB_PATH", path)
    db.use(path)
    rel.ensure_schema()
    yield path
    db.use(path)


def start(cid="yt:a", camp="c1", name="Creator A", platform="youtube", country="DE"):
    return rel.start_sponsorship(camp, cid, creator_name=name, platform=platform, country=country,
                                 campaign_label="Gaming PCs · Germany")


# ------------------------------------------------------------------ shortlist
def test_existing_shortlist_is_migrated_in_added_order(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE shortlist (campaign_id TEXT, creator_id TEXT, added_at TEXT, "
              "PRIMARY KEY (campaign_id, creator_id))")
    c.executemany("INSERT INTO shortlist VALUES (?,?,?)", [("c1", "yt:b", "2026-01-01"), ("c1", "yt:a", "2026-01-02")])
    c.commit()
    c.close()
    monkeypatch.setattr(config, "DB_PATH", path)
    db.use(path)
    rel.ensure_schema()
    rel.ensure_schema()  # idempotent
    assert rel.shortlist_ids("c1") == ["yt:b", "yt:a"]
    assert all(r["favorite"] == 0 for r in rel.shortlist("c1"))


def test_manual_order_persists_across_restarts(store):
    for cid in ("yt:a", "yt:b", "yt:c"):
        rel.add_to_shortlist("c1", cid)
    rel.move("c1", "yt:c", -1)
    rel.move("c1", "yt:c", -1)
    rel.move("c1", "yt:c", -1)  # already first: no-op
    rel.move("c1", "yt:a", 1)
    assert rel.shortlist_ids("c1") == ["yt:c", "yt:b", "yt:a"]
    db.use(store)  # new connection = app restart
    assert rel.shortlist_ids("c1") == ["yt:c", "yt:b", "yt:a"]
    rel.remove_from_shortlist("c1", "yt:b")
    rel.add_to_shortlist("c1", "yt:d")
    assert rel.shortlist_ids("c1") == ["yt:c", "yt:a", "yt:d"]  # new entries go to the end


def test_favorite_is_a_separate_persistent_flag(store):
    rel.add_to_shortlist("c1", "yt:a")
    assert rel.toggle_favorite("c1", "yt:a") is True
    db.use(store)
    assert rel.shortlist("c1")[0]["favorite"] == 1
    assert rel.toggle_favorite("c1", "yt:a") is False


# ------------------------------------------------------------------ sponsorships
def test_one_active_sponsorship_per_creator_and_campaign(store):
    sid = start()
    assert start() == sid
    rel.set_status(sid, "done")
    assert start() != sid  # a finished one can be followed by a new sponsorship


def test_status_and_completion_date(store):
    sid = start()
    rel.set_status(sid, "done")
    done = rel.get(sid)
    assert done["status"] == "done" and done["completed_at"]
    rel.set_status(sid, "in_progress")
    assert rel.get(sid)["completed_at"] is None
    with pytest.raises(AssertionError):
        rel.set_status(sid, "paused")


def test_kpis_are_optional_and_removable(store):
    sid = start()
    rel.update_performance(sid, 1200, {"views": 125000, "clicks": None, "revenue": 8900}, "went well")
    assert rel.kpis(sid) == {"views": 125000, "revenue": 8900}
    rel.update_performance(sid, 1200, {"views": None})
    assert rel.kpis(sid) == {"revenue": 8900}
    assert rel.get(sid)["notes"] == "went well"
    with pytest.raises(ValueError):
        rel.update_performance(sid, -5, {})
    with pytest.raises(AssertionError):
        rel.update_performance(sid, 10, {"followers": 3})  # only registered KPI types


def test_derived_metrics_only_from_recorded_inputs():
    d = rel.derived(1200, {"views": 125000, "referrals": 620, "conversions": 84, "revenue": 8900})
    assert d["cost_per_view"] == pytest.approx(0.0096) and d["cost_per_referral"] == pytest.approx(1200 / 620)
    assert d["roas"] == pytest.approx(8900 / 1200)
    assert "cost_per_click" not in d  # clicks not recorded
    assert rel.derived(None, {"views": 10, "revenue": 50}) == {}  # no price -> no cost metrics, no ROAS
    assert rel.derived(0, {"revenue": 50, "views": 0}) == {}  # division by zero is skipped, not faked


def test_feedback_is_per_sponsorship_and_history_is_derived(store):
    outcomes = ["positive", "positive", "negative", "positive", "positive"]
    for i, o in enumerate(outcomes):
        sid = start(camp=f"c{i}")
        rel.update_performance(sid, 1000, {"revenue": 3000} if i < 3 else {})
        rel.set_outcome(sid, o)
        rel.set_outcome(sid, o)  # repeated clicks on the same sponsorship do not add up
        if i < 4:
            rel.set_status(sid, "done")
    h = rel.history("yt:a")
    assert (h.sponsorships, h.completed, h.in_progress) == (5, 4, 1)
    assert (h.positive, h.negative) == (4, 1)
    assert h.total_spend == 5000 and h.total_revenue == 9000
    assert h.roas == pytest.approx(9000 / 3000)  # only sponsorships with both spend and revenue
    assert rel.history("yt:nobody").any is False


def test_changing_an_outcome_changes_the_history(store):
    sid = start()
    rel.set_outcome(sid, "positive")
    rel.set_outcome(sid, "negative")
    h = rel.history("yt:a")
    assert (h.positive, h.negative) == (0, 1)


def test_summary_uses_stored_values_only(store):
    a, b = start("yt:a"), start("yt:b")
    rel.update_performance(a, 1200, {"revenue": 8900})
    rel.set_status(a, "done")
    s = rel.summary(rel.sponsorships())
    assert s == {"active": 1, "completed": 1, "spend": 1200, "revenue": 8900}
    assert rel.summary([{"status": "in_progress", "price_paid": None, "kpis": {}}])["spend"] is None


def test_sponsorships_survive_deleting_the_campaign_report(store):
    db.execute("INSERT INTO campaigns VALUES ('c1', '', '{}', '')")
    rel.add_to_shortlist("c1", "yt:a")
    sid = start()
    db.delete_campaign("c1")
    assert rel.shortlist_ids("c1") == []  # the report's shortlist goes with it
    kept = rel.get(sid)
    assert kept and kept["creator_name"] == "Creator A" and kept["campaign_label"] == "Gaming PCs · Germany"
