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
    conn = sqlite3.connect(path)  # start from the ranked searches only, not whatever a marketer already saved
    for table in ("shortlist", "sponsorships", "sponsorship_kpis", "tiktok_leads"):
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
            conn.execute(f"DELETE FROM {table}")
    conn.commit()
    conn.close()
    monkeypatch.setattr(config, "DB_PATH", path)
    db.use(path)
    yield path
    db.use(path)  # close the connection before tmp_path is removed


def campaigns_with_results(path: Path) -> list[str]:
    c = sqlite3.connect(path)
    rows = c.execute("SELECT campaign_id FROM rankings WHERE passed_hard_filter=1 AND creator_id LIKE 'yt:%' "
                     "GROUP BY campaign_id "
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
        "SELECT creator_id FROM rankings WHERE campaign_id=? AND passed_hard_filter=1 AND creator_id LIKE 'yt:%'", (cid,)).fetchone()[0]
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
    _sql(db_copy, ("INSERT INTO shortlist (campaign_id, creator_id, added_at) VALUES (?, 'yt:gone', '2026-01-01')", (cid,)))
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
        "SELECT creator_id FROM rankings WHERE campaign_id=? AND passed_hard_filter=1 AND creator_id LIKE 'yt:%'", (cid,)).fetchone()[0]
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
    assert not at.text_area  # home is a dashboard: the brief lives in the New creator search flow
    click(at, "btn_new_search")
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


# ------------------------------------------------------------------ shortlist → sponsorship → performance → history
def test_full_sponsorship_lifecycle(db_copy):
    import relationships as rel
    cid = campaigns_with_results(db_copy)[0]
    at = open_campaign(start(db_copy), cid)
    creators = [k[len("sl_"):] for k in keys(at, "sl_")][:2]
    assert len(creators) == 2, "needs two ranked creators"
    first, second = creators
    click(at, f"sl_{first}")
    click(at, f"sl_{second}")
    click(at, "btn_cart")
    assert rel.shortlist_ids(cid) == [first, second]
    # reorder + favorite (persisted, separate from the score)
    click(at, f"slup_{second}")
    assert rel.shortlist_ids(cid) == [second, first]
    assert at.button(key=f"slup_{second}").disabled  # already on top
    click(at, f"slfav_{first}")
    assert at.button(key=f"slfav_{first}").label == "★"
    at.segmented_control(key=f"_w_sl_order_{cid}").set_value("Favorites first").run()
    assert keys(at, "slfav_")[0] == f"slfav_{first}"
    # start sponsorship (confirmation dialog)
    click(at, f"slsp_{first}")
    click(at, "sp_confirm_yes")
    assert not at.exception and at.session_state["view"] == "sponsorships"
    sp = rel.sponsorships()[0]
    assert (sp["creator_id"], sp["status"], sp["outcome"]) == (first, "in_progress", "neutral")
    assert [m.label for m in at.metric] == ["Active sponsorships", "Completed sponsorships",
                                            "Total sponsorship spend", "Total tracked revenue"]
    assert at.metric[0].value == "1" and at.metric[2].value == "–"  # nothing recorded yet: no fake totals
    # enter KPI data through the form
    sid = sp["id"]
    at.number_input(key=f"spf_{sid}_price").set_value(1200.0)
    at.number_input(key=f"spf_{sid}_views").set_value(125000.0)
    at.number_input(key=f"spf_{sid}_revenue").set_value(8900.0).run()
    at.button(key=f"FormSubmitter:spf_form_{sid}-Save").click().run()
    assert not at.exception
    assert rel.kpis(sid) == {"views": 125000.0, "revenue": 8900.0} and rel.get(sid)["price_paid"] == 1200.0
    assert "7.4×" in text(at)  # ROAS, deterministic
    # finish + outcome
    at.selectbox(key=f"sp_status_{sid}").set_value("done").run()
    at.segmented_control(key=f"sp_outcome_{sid}").set_value("positive").run()
    assert not at.exception
    assert rel.get(sid)["status"] == "done" and rel.get(sid)["completed_at"]
    h = rel.history(first)
    assert (h.sponsorships, h.completed, h.positive, h.negative) == (1, 1, 1, 0)
    # filters work together
    at.segmented_control(key="_w_sp_f_status").set_value("in_progress").run()
    assert "No sponsorships match these filters." in text(at)
    at.segmented_control(key="_w_sp_f_status").set_value("done").run()
    assert f"sp_status_{sid}" in [s.key for s in at.selectbox]
    # history shows next to the score on the creator's analysis page, never inside it
    open_campaign(at, cid)
    assert "WORKED WITH PRENEW · 👍 1 👎 0" in text(at)
    click(at, f"view_{first}")
    t = text(at)
    assert "Prenew relationship" in t and "Historical feedback: 👍 1 · 👎 0" in t and "7.4×" in t


def test_cancelling_the_sponsorship_dialog_changes_nothing(db_copy):
    import relationships as rel
    cid = campaigns_with_results(db_copy)[0]
    at = open_campaign(start(db_copy), cid)
    first = keys(at, "sl_")[0][len("sl_"):]
    click(at, f"sl_{first}")
    click(at, "btn_cart")
    click(at, f"slsp_{first}")
    assert "Start a sponsorship with" in text(at)
    click(at, "sp_confirm_no")
    assert rel.sponsorships() == [] and "confirm_sp" not in at.session_state


# ------------------------------------------------------------------ global Shortlists / Sponsorships, grouped by search
def test_global_sections_grouped_by_search_survive_restart(db_copy, monkeypatch):
    import pipeline
    import relationships as rel
    from models import CampaignSpec
    A, B = campaigns_with_results(db_copy)[:2]
    n_creators = sqlite3.connect(db_copy).execute("SELECT COUNT(*) FROM creators").fetchone()[0]
    at = start(db_copy)
    assert "No shortlisted creators yet" in text(at) and "No sponsorships yet" in text(at)  # honest empty home
    # Search A: shortlist two creators; Search B: one (the same creator as A's first)
    open_campaign(at, A)
    a1, a2 = [k[len("sl_"):] for k in keys(at, "sl_")][:2]
    shared = a1
    conn = sqlite3.connect(db_copy)  # search B also found this creator, with its own features and score
    conn.execute("INSERT OR REPLACE INTO features SELECT ?, creator_id, name, value, method, n_samples FROM features "
                 "WHERE campaign_id=? AND creator_id=?", (B, A, shared))
    conn.execute("INSERT OR REPLACE INTO rankings SELECT ?, creator_id, 99, 0.123, confidence, 1, filter_reason, "
                 "breakdown_json FROM rankings WHERE campaign_id=? AND creator_id=?", (B, A, shared))
    conn.commit()
    conn.close()
    click(at, f"sl_{a1}")
    click(at, f"sl_{a2}")
    open_campaign(at, B)
    click(at, f"sl_{shared}")
    # Home -> Shortlists, grouped by search
    click(at, "crumb_home")
    assert "<b>3</b> creators · <b>2</b> searches" in text(at)
    click(at, "btn_shortlists")
    cards = {m.value.split('pn-group-name">')[1].split("<")[0]: m.value for m in at.markdown if 'class="pn-group-name"' in m.value}
    assert len(cards) == 2
    assert sorted(v.split("<b>")[1].split("</b>")[0] for v in cards.values()) == ["1", "2"]
    # All creators: the shared creator appears once per search, each with its own score
    at.segmented_control(key="_w_sls_mode").set_value("All creators").run()
    df = at.dataframe[0].value
    shared_rows = df[df["Creator"] == df[df["Search"] == df["Search"].iloc[0]]["Creator"].iloc[0]]
    assert len(df) == 3 and len(shared_rows) == 2 and shared_rows["Score in this search"].nunique() == 2
    # open A's shortlist from the global list; reorder + favorite; back link returns to Shortlists
    at.segmented_control(key="_w_sls_mode").set_value("By search").run()
    click(at, f"open_sl_{A}")
    assert at.session_state["view"] == "shortlist" and keys(at, "crumb_shortlists")
    click(at, f"slup_{a2}")
    click(at, f"slfav_{a1}")
    click(at, f"slsp_{a1}")
    click(at, "sp_confirm_yes")
    # Sponsorships opens on this search, grouped, provenance kept
    assert at.session_state["view"] == "sponsorships" and at.session_state["sp_f_campaign"] == A
    sp = rel.sponsorships()[0]
    assert (sp["campaign_id"], sp["creator_id"], sp["platform"]) == (A, a1, "youtube")
    rel.update_performance(sp["id"], 1000, {"views": 50000})
    rel.set_status(sp["id"], "done")
    # Search C is created: A and B stay intact
    spec = CampaignSpec.model_validate_json(sqlite3.connect(db_copy).execute(
        "SELECT spec_json FROM campaigns WHERE id=?", (A,)).fetchone()[0])
    monkeypatch.setattr(pipeline, "parse_brief", lambda llm, brief: spec.model_copy(update={"n_creators": 10}))

    def fake_run(brief, spec=None, seed_handles=(), progress=None):
        import db
        db.execute("INSERT INTO campaigns VALUES ('c_search_c', 'search C', ?, ?)", (spec.model_dump_json(), db.now()))
        return "c_search_c"
    monkeypatch.setattr(pipeline, "run_campaign", fake_run)
    click(at, "crumb_home")
    click(at, "btn_new_search")
    at.text_area(key="campaign_brief_v2").set_value("Search C").run()
    at.button(key="FormSubmitter:brief_form-Continue →").click().run()
    at.button(key="FormSubmitter:settings_form-Create campaign →").click().run()
    assert not at.exception, [e.value for e in at.exception]

    # restart: a fresh app session on the same database
    at = start(db_copy)
    assert rel.shortlist_ids(A) == [a2, a1] and rel.shortlist_ids(B) == [shared]
    assert rel.shortlist(A)[1]["favorite"] == 1
    assert "<b>3</b> creators · <b>2</b> searches" in text(at)
    assert "<b>0</b> in progress · <b>1</b> done" in text(at)
    click(at, "btn_sponsorships")
    assert at.session_state["sp_f_campaign"] == "all"
    assert rel.get(sp["id"])["status"] == "done" and rel.kpis(sp["id"]) == {"views": 50000.0}
    # filters work together and keep the search grouping
    at.selectbox(key="_w_sp_f_platform").set_value("youtube").run()
    at.segmented_control(key="_w_sp_f_status").set_value("done").run()
    assert [k for k in (c.key for c in at.button) if k and k.startswith("sp_open_")] == [f"sp_open_{A}"]
    at.segmented_control(key="_w_sp_f_status").set_value("in_progress").run()
    assert "No sponsorships match these filters." in text(at)
    # no creator records were created or duplicated along the way
    assert sqlite3.connect(db_copy).execute("SELECT COUNT(*) FROM creators").fetchone()[0] == n_creators


def test_every_new_page_has_a_way_back(db_copy):
    at = start(db_copy)
    for nav, back in [("btn_shortlists", "crumb_home"), ("btn_sponsorships", "crumb_home"),
                      ("btn_new_search", "crumb_new")]:
        click(at, nav)
        assert keys(at, back), nav
        click(at, back)
        assert at.session_state["view"] == "campaign"


def test_incomplete_search_strategy_is_shown_not_silent(db_copy, monkeypatch):
    import pipeline
    from models import CampaignSpec
    A = campaigns_with_results(db_copy)[0]
    spec = CampaignSpec.model_validate_json(sqlite3.connect(db_copy).execute(
        "SELECT spec_json FROM campaigns WHERE id=?", (A,)).fetchone()[0])
    broken = spec.model_copy(update={"search_queries": ["Patch 26.18 Tier List"], "n_creators": 10,
                                     "strategy_notes": ["Search planning failed (bad format)."]})
    monkeypatch.setattr(pipeline, "parse_brief", lambda llm, brief: broken)
    at = start(db_copy)
    click(at, "btn_new_search")
    at.text_area(key="campaign_brief_v2").set_value("League of Legends").run()
    at.button(key="FormSubmitter:brief_form-Continue →").click().run()
    assert at.warning and "The search strategy is incomplete." in at.warning[0].value
    assert "Search planning failed (bad format)." in at.warning[0].value
    assert at.expander[0].label == "Search setup"  # opened, so the single fallback search is visible
