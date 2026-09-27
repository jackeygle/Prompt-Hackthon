"""End-to-end UI tests: drive app.py headlessly with Streamlit's AppTest against a throwaway copy of the
committed snapshot (data/demo.db). Every page is rendered for every campaign and creator, and the UI rules
(confidence cap, budget flag, fit-first reasons, breadcrumb, shortlist) are checked on the rendered output."""
import json
import os
import re
import shutil
import sqlite3
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import config
import db

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = Path(os.getenv("UI_TEST_DB", ROOT / "data" / "demo.db"))  # UI_TEST_DB=<copy source> to test other data
pytestmark = pytest.mark.skipif(not SNAPSHOT.exists(), reason=f"{SNAPSHOT} not available")

BADGE = re.compile(r"(HIGH|MEDIUM) CONFIDENCE · (\d+) VIDEOS? · (\d+) COMMENTS")


@pytest.fixture
def db_copy(tmp_path, monkeypatch):
    path = tmp_path / "ui.db"
    shutil.copy(SNAPSHOT, path)
    monkeypatch.setattr(config, "DB_PATH", path)
    db.use(path)
    yield path
    db.use(path)  # close the connection before tmp_path is removed


def campaigns_with_results(path: Path) -> list[str]:
    c = sqlite3.connect(path)
    rows = c.execute("SELECT campaign_id FROM rankings WHERE passed_hard_filter=1 GROUP BY campaign_id "
                     "ORDER BY COUNT(*) DESC, campaign_id").fetchall()  # largest campaign first
    c.close()
    return [r[0] for r in rows]


def start(db_copy) -> AppTest:
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60).run()
    assert not at.exception, at.exception
    return at


def click(at: AppTest, key: str) -> AppTest:
    at.button(key=key).click().run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def text(at: AppTest) -> str:
    return "\n".join(m.value for m in at.markdown)


def keys(at: AppTest, prefix: str) -> list[str]:
    return [b.key for b in at.button if b.key and b.key.startswith(prefix)]


def open_campaign(at: AppTest, cid: str) -> AppTest:
    at.session_state["campaign"], at.session_state["view"] = cid, "discover"
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


# ------------------------------------------------------------------ home
def test_home_has_no_offline_demo_and_no_campaign_nav(db_copy):
    at = start(db_copy)
    assert "Offline demo" not in text(at) and not at.segmented_control
    assert not keys(at, "crumb_") and not keys(at, "btn_cart")  # breadcrumb + cart only inside a campaign
    assert keys(at, "open_")


def test_empty_reports_hidden_until_toggled(db_copy):
    conn = sqlite3.connect(db_copy)
    conn.execute("INSERT INTO campaigns VALUES ('c_empty', 'empty brief', ?, '2026-01-01T00:00:00')",
                 (conn.execute("SELECT spec_json FROM campaigns LIMIT 1").fetchone()[0],))
    conn.commit()
    conn.close()
    at = start(db_copy)
    assert "open_c_empty" not in keys(at, "open_")
    at.toggle(key="show_empty_reports").set_value(True).run()
    assert "open_c_empty" in keys(at, "open_")


# ------------------------------------------------------------------ creators page, every campaign
def test_every_campaign_renders_cards_and_picks(db_copy):
    for cid in campaigns_with_results(db_copy):
        at = open_campaign(start(db_copy), cid)
        cards = keys(at, "view_")
        assert cards, f"no creator cards for {cid}"
        if len(cards) >= 2:
            assert keys(at, "pick_"), f"no top picks for {cid}"
        for high, _videos, comments in BADGE.findall(text(at)):
            assert not (high == "HIGH" and comments == "0"), f"HIGH confidence with 0 comments in {cid}"


def test_every_creator_analysis_renders(db_copy):
    for cid in campaigns_with_results(db_copy):
        at = open_campaign(start(db_copy), cid)
        for key in keys(at, "view_"):
            click(at, key)
            assert at.session_state["view"] == "analysis"
            assert keys(at, "crumb_campaign"), "breadcrumb back to the campaign missing"
            click(at, "crumb_campaign")
            assert at.session_state["view"] == "discover"


def test_compare_view_and_search(db_copy):
    cid = campaigns_with_results(db_copy)[0]
    at = open_campaign(start(db_copy), cid)
    at.segmented_control(key=f"_w_f_layout_{cid}").set_value("Compare").run()
    assert not at.exception and at.dataframe
    at.segmented_control(key=f"_w_f_layout_{cid}").set_value("Cards").run()
    at.text_input(key=f"_w_f_query_{cid}").set_value("zzz-no-such-creator").run()
    assert not at.exception
    assert "No creators match these filters." in text(at)


def test_confidence_filter_high_only_shows_high(db_copy):
    cid = campaigns_with_results(db_copy)[0]
    at = open_campaign(start(db_copy), cid)
    at.segmented_control(key=f"_w_f_conf_{cid}").set_value("High").run()
    assert not at.exception
    assert all(level == "HIGH" for level, _, _ in BADGE.findall(text(at)))


# ------------------------------------------------------------------ budget
def test_budget_flags_and_sinks_over_budget_creators(db_copy):
    cid = campaigns_with_results(db_copy)[0]
    at = open_campaign(start(db_copy), cid)
    at.session_state[f"budget_{cid}"] = 1  # €1 per video: everyone with a cost proxy is over budget
    at.run()
    assert not at.exception
    assert "OVER BUDGET" in text(at)
    assert not keys(at, "pick_") and "No creator fits the budget" in text(at)  # never recommend over budget
    # over-budget cards come after within-budget ones: cards without the flag must all come first
    cards = [m.value for m in at.markdown if "pn-card-head" in m.value]
    flags = ["OVER BUDGET" in c for c in cards]
    assert flags == sorted(flags)
    # the budget survives a trip to the analysis page and back (widget keys are cleared on other pages)
    click(at, keys(at, "view_")[0])
    click(at, "crumb_campaign")
    assert "OVER BUDGET" in text(at)


# ------------------------------------------------------------------ shortlist
def test_shortlist_flow(db_copy):
    at = open_campaign(start(db_copy), campaigns_with_results(db_copy)[0])
    first = keys(at, "sl_")[0]
    click(at, first)
    assert at.button(key="btn_cart").label == "Shortlist (1)"
    click(at, "btn_cart")
    assert at.session_state["view"] == "shortlist"
    assert at.code and "score" in at.code[0].value, "shareable summary missing"
    assert at.get("download_button")
    click(at, keys(at, "slr_")[0])
    assert "Your shortlist is empty." in text(at)
    assert at.button(key="btn_cart").label == "Shortlist"


def test_breadcrumb_home(db_copy):
    at = open_campaign(start(db_copy), campaigns_with_results(db_copy)[0])
    click(at, "crumb_home")
    assert at.session_state["view"] == "campaign"
    assert not keys(at, "btn_cart")


# ------------------------------------------------------------------ edge cases (hostile / incomplete data)
def _sql(path: Path, *statements) -> None:
    conn = sqlite3.connect(path)
    for s in statements:
        conn.execute(*s) if isinstance(s, tuple) else conn.execute(s)
    conn.commit()
    conn.close()


def test_creator_with_missing_numbers_and_no_accounts(db_copy):
    cid = campaigns_with_results(db_copy)[0]
    creator = sqlite3.connect(db_copy).execute(
        "SELECT creator_id FROM rankings WHERE campaign_id=? AND passed_hard_filter=1", (cid,)).fetchone()[0]
    _sql(db_copy,
         ("DELETE FROM features WHERE campaign_id=? AND creator_id=? AND name IN "
          "('subscribers','median_relevant_views','est_cost_eur','engagement_rate')", (cid, creator)),
         ("DELETE FROM platform_accounts WHERE creator_id=?", (creator,)),
         ("UPDATE creators SET name=?, country=NULL WHERE id=?", ('<b>Evil & "Co"</b>', creator)))
    at = open_campaign(start(db_copy), cid)
    at.session_state[f"budget_{cid}"] = 1
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert "<b>Evil" not in text(at) and "&lt;b&gt;Evil" in text(at)  # names are escaped
    assert f"view_{creator}" in keys(at, "view_"), "edited creator no longer ranked"
    if True:
        click(at, f"view_{creator}")
        click(at, "sl_detail")
        click(at, "btn_cart")
        assert at.code  # summary renders with missing cost / url


def test_stale_creator_and_filtered_shortlist_entry(db_copy):
    cid = campaigns_with_results(db_copy)[0]
    _sql(db_copy, ("INSERT INTO shortlist VALUES (?, 'yt:gone', '2026-01-01')", (cid,)))
    at = open_campaign(start(db_copy), cid)
    at.session_state["view"], at.session_state["creator"] = "analysis", "yt:does-not-exist"
    at.run()
    assert not at.exception and "Creator not found in this ranking." in text(at)
    click(at, "btn_cart")
    assert "no longer passes the hard filters" in text(at)


def test_unknown_country_language_and_single_creator(db_copy):
    cid = campaigns_with_results(db_copy)[0]
    spec = sqlite3.connect(db_copy).execute("SELECT spec_json FROM campaigns WHERE id=?", (cid,)).fetchone()[0]
    spec = json.dumps({**json.loads(spec), "target_country": "XX", "target_language": "zz", "price_segment": ""})
    keep = sqlite3.connect(db_copy).execute(
        "SELECT creator_id FROM rankings WHERE campaign_id=? AND passed_hard_filter=1", (cid,)).fetchone()[0]
    _sql(db_copy, ("UPDATE campaigns SET spec_json=? WHERE id=?", (spec, cid)),
         ("DELETE FROM features WHERE campaign_id=? AND creator_id<>?", (cid, keep)))
    at = open_campaign(start(db_copy), cid)
    assert len(keys(at, "view_")) == 1 and not keys(at, "pick_")  # no "best of" with a single creator
    assert "XX" in text(at) and "ZZ" in text(at)  # unknown codes shown as-is instead of crashing


def test_selected_campaign_deleted_falls_back_home_or_first(db_copy):
    at = open_campaign(start(db_copy), campaigns_with_results(db_copy)[0])
    gone = at.session_state["campaign"]
    db.delete_campaign(gone)
    at.run()
    assert not at.exception
    assert at.session_state["campaign"] != gone


# ------------------------------------------------------------------ regressions: state must survive navigation
def _scores(at: AppTest) -> list[tuple[str, str]]:
    return re.findall(r'class="name" title="([^"]+)".*?<div class="num">(\d+)<', text(at), re.S)


def test_ranking_goal_survives_analysis_round_trip(db_copy):
    cid = campaigns_with_results(db_copy)[0]
    at = open_campaign(start(db_copy), cid)
    default = at.session_state[f"preset_{cid}"]
    other = next(g for g in ("conversion", "awareness", "balanced") if g != default)
    at.radio(key=f"_w_preset_{cid}").set_value(other).run()
    ranked = _scores(at)
    click(at, keys(at, "view_")[0])
    click(at, "sl_detail")  # any interaction on the analysis page reruns without the ranking widgets
    click(at, "crumb_campaign")
    assert at.session_state[f"preset_{cid}"] == other
    assert _scores(at) == ranked


def test_ranking_goal_is_per_campaign(db_copy):
    first, *rest = campaigns_with_results(db_copy)
    if not rest:
        pytest.skip("needs two campaigns")
    at = open_campaign(start(db_copy), first)
    spec_goal = at.session_state[f"preset_{first}"]
    other = next(g for g in ("conversion", "awareness", "balanced") if g != spec_goal)
    at.radio(key=f"_w_preset_{first}").set_value(other).run()
    open_campaign(at, rest[0])
    goal = json.loads(sqlite3.connect(db_copy).execute(
        "SELECT spec_json FROM campaigns WHERE id=?", (rest[0],)).fetchone()[0])["goal"]
    assert at.session_state[f"preset_{rest[0]}"] == goal


def test_filters_survive_analysis_round_trip(db_copy):
    cid = campaigns_with_results(db_copy)[0]
    at = open_campaign(start(db_copy), cid)
    at.selectbox(key=f"_w_f_sort_{cid}").set_value("Subscribers").run()
    at.segmented_control(key=f"_w_f_conf_{cid}").set_value("Medium+").run()
    click(at, keys(at, "view_")[0])
    click(at, "crumb_campaign")
    assert at.selectbox(key=f"_w_f_sort_{cid}").value == "Subscribers"
    assert at.segmented_control(key=f"_w_f_conf_{cid}").value == "Medium+"


def test_priority_is_human_and_changes_ranking(db_copy):
    cid = campaigns_with_results(db_copy)[0]
    at = open_campaign(start(db_copy), cid)
    radio = at.radio(key=f"_w_preset_{cid}")
    assert list(radio.options) == ["Drive sales", "Reach more gamers", "Balanced"]  # labels, not AHP preset ids
    assert "AHP" not in radio.label + (radio.help or "")
    scores = {}
    for p in ("conversion", "awareness", "balanced"):
        at.radio(key=f"_w_preset_{cid}").set_value(p).run()
        assert not at.exception
        scores[p] = _scores(at)
    assert scores["conversion"] != scores["awareness"], "priority must change the ranking, not just a label"


# ------------------------------------------------------------------ campaign creation (LLM + YouTube stubbed)
def test_create_campaign_flow_passes_budget(db_copy, monkeypatch):
    import pipeline
    from models import CampaignSpec
    existing = campaigns_with_results(db_copy)[0]
    spec = CampaignSpec.model_validate_json(sqlite3.connect(db_copy).execute(
        "SELECT spec_json FROM campaigns WHERE id=?", (existing,)).fetchone()[0])
    seen = {}
    monkeypatch.setattr(pipeline, "parse_brief", lambda llm, brief: spec.model_copy(update={"budget_per_video": 0, "n_creators": 10}))

    def fake_run(brief, spec=None, seed_handles=(), progress=None):
        seen["spec"] = spec
        return existing
    monkeypatch.setattr(pipeline, "run_campaign", fake_run)

    at = start(db_copy)
    at.text_area(key="campaign_brief_v2").set_value("Gaming PCs under €800 in Germany").run()
    at.button(key="FormSubmitter:brief_form-Continue →").click().run()
    assert not at.exception, [e.value for e in at.exception]
    n = at.session_state["draft_n"]
    at.number_input(key=f"s{n}_budget").set_value(2500).run()
    slider = at.slider(key=f"s{n}_n_creators")
    assert (slider.min, slider.max, slider.value) == (1, 50, 10)
    slider.set_value(23).run()
    at.radio(key=f"s{n}_goal").set_value("conversion").run()
    at.button(key="FormSubmitter:settings_form-Create campaign →").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert seen["spec"].budget_per_video == 2500
    assert seen["spec"].n_creators == 23 and seen["spec"].goal == "conversion"  # reach the pipeline, not only the UI
    assert at.session_state["view"] == "discover" and at.session_state["campaign"] == existing


# ------------------------------------------------------------------ TikTok Scout (human-in-the-loop)
def _scout(at: AppTest, cid: str) -> AppTest:
    at.segmented_control(key=f"_w_f_platform_{cid}").set_value("tiktok").run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def test_tiktok_scout_leads_need_an_explicit_human_decision(db_copy, monkeypatch):
    import tiktok_scout as tts
    leads = [tts.Lead("pc_max", "https://www.tiktok.com/@pc_max", "Counter-Strike 2",
                      "https://www.tiktok.com/@pc_max", True, "tiktok link"),
             tts.Lead("ghost_user", "https://www.tiktok.com/@ghost_user", "Fortnite", "https://nowhere.example",
                      False, "article")]
    monkeypatch.setattr(tts.WebResearchScout, "find_leads", lambda self, spec, n=8: (leads, []))
    cid = campaigns_with_results(db_copy)[0]
    at = _scout(open_campaign(start(db_copy), cid), cid)
    assert "TikTok itself is not crawled or scored" in text(at)
    click(at, "tt_find")
    assert keys(at, "tt_sl_") == ["tt_sl_pc_max", "tt_sl_ghost_user"]
    assert at.button(key="btn_cart").label == "Shortlist"  # surfacing a lead never shortlists it
    assert "could not be verified. Open TikTok to review it manually." in text(at)
    click(at, "tt_ignore_ghost_user")
    assert keys(at, "tt_sl_") == ["tt_sl_pc_max"]
    click(at, "tt_sl_pc_max")  # the marketer's explicit decision
    assert at.button(key="btn_cart").label == "Shortlist (1)"
    click(at, "btn_cart")
    t = text(at)
    assert "HUMAN-SELECTED TIKTOK LEAD" in t and "Not analysed · no score" in t
    assert "human-selected lead (reviewed on TikTok, not analysed, no score)" in at.code[0].value


def test_tiktok_manual_add_validates_the_link(db_copy):
    cid = campaigns_with_results(db_copy)[0]
    at = _scout(open_campaign(start(db_copy), cid), cid)
    at.text_input(key="tt_url").set_value("https://www.tiktok.com/shop/pdp/123").run()
    click(at, "tt_add")
    assert at.error and "isn't a TikTok creator link" in at.error[0].value
    assert at.button(key="btn_cart").label == "Shortlist"
    at.text_input(key="tt_url").set_value("https://www.tiktok.com/@known_creator").run()
    at.text_input(key="tt_name").set_value("Known Creator").run()
    click(at, "tt_add")
    assert at.button(key="btn_cart").label == "Shortlist (1)"
    click(at, "btn_cart")
    assert "Known Creator" in text(at)
