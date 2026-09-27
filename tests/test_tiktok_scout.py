"""TikTok Scout: human-in-the-loop leads. These tests pin the safety rules as well as the behaviour."""
import ast
import sqlite3
from pathlib import Path

import pytest

import db
import tiktok_scout as tts
import web_discovery as web
from models import CampaignSpec, CurrentTopic

SRC = Path(tts.__file__).read_text()
SPEC = CampaignSpec(product="refurbished gaming PCs", product_keywords=["gaming PC"], niche="PC gaming",
                    target_country="DE", target_language="de", audience="gamers in Germany",
                    audience_interests=["PC gaming"], search_queries=["x"],
                    current_topics=[CurrentTopic(name="Counter-Strike 2", kind="game")])


# ------------------------------------------------------------------ URL validation (no network)
@pytest.mark.parametrize("url,handle", [
    ("https://www.tiktok.com/@pc_max", "pc_max"),
    ("tiktok.com/@PC.Max/", "pc.max"),
    ("https://m.tiktok.com/@pc_max/video/7351234567890123456?lang=de", "pc_max"),
])
def test_valid_creator_links(url, handle):
    ref = tts.parse_tiktok_url(url)
    assert ref and ref.handle == handle and ref.profile_url == f"https://www.tiktok.com/@{handle}"
    assert "?" not in ref.url


@pytest.mark.parametrize("url", [
    "https://www.tiktok.com/shop/pdp/gaming-pc/123", "https://www.tiktok.com/tag/gamingpc",
    "https://www.tiktok.com/search?q=gaming", "https://www.tiktok.com/music/abc-123", "https://vm.tiktok.com/ZMabc/",
    "https://www.tiktok.com.evil.example/@pc_max", "https://example.com/@pc_max", "", "not a url",
    "https://www.tiktok.com/@pc_max/photo/123",
])
def test_non_creator_links_are_rejected(url):
    assert tts.parse_tiktok_url(url) is None


# ------------------------------------------------------------------ safety rules, checked on the source itself
def test_module_never_talks_to_tiktok_or_automates_a_browser():
    tree = ast.parse(SRC)
    imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))
                for a in (n.names if isinstance(n, ast.Import) else [ast.alias(n.module or "")])}
    assert not imported & {"requests", "httpx", "urllib3", "aiohttp", "selenium", "playwright", "bs4", "lxml"}
    code = "\n".join(l for l in SRC.splitlines() if not l.lstrip().startswith("#"))
    body = code.split('"""', 2)[2]  # skip the module docstring that states the rules
    for forbidden in ("oembed", "requests.", "urlopen", "webdriver"):
        assert forbidden not in body.lower()


def test_scout_only_uses_the_model_web_search(monkeypatch):
    calls = []

    def fake_search(prompt, via="azure"):
        calls.append(via)
        return {"leads": []}, set()
    monkeypatch.setattr(web, "openai_web_search", fake_search)
    monkeypatch.setattr(web, "tavily_search", lambda *a, **k: pytest.fail("TikTok Scout must not use Tavily"))
    monkeypatch.setattr(web, "available_providers", lambda: ["tavily", "azure"])
    tts.WebResearchScout().find_leads(SPEC)
    assert calls == ["azure"]
    monkeypatch.setattr(web, "available_providers", lambda: ["tavily"])
    leads, errors = tts.WebResearchScout().find_leads(SPEC)
    assert leads == [] and "model web search" in errors[0]


def test_prompt_is_content_first_and_asks_for_no_metrics(monkeypatch):
    seen = {}
    monkeypatch.setattr(web, "available_providers", lambda: ["azure"])
    monkeypatch.setattr(web, "openai_web_search", lambda prompt, via="azure": (seen.setdefault("p", prompt), ({}, set()))[1])
    tts.WebResearchScout().find_leads(SPEC)
    assert "Counter-Strike 2" in seen["p"] and "Do not describe follower counts" in seen["p"]
    assert "never add gaming" in seen["p"]  # flexible: other campaign topics are followed, not gamified


# ------------------------------------------------------------------ lead selection
def test_select_leads_filters_dedupes_limits_articles_and_marks_unverified():
    raw = [{"tiktok_url": "https://www.tiktok.com/@aa_creator/video/123456789", "topic": "CS2", "found_via": "tiktok link",
            "source_url": "https://www.tiktok.com/@aa_creator/video/123456789"},
           {"tiktok_url": "https://www.tiktok.com/@aa_creator", "topic": "dup"},
           {"tiktok_url": "https://www.tiktok.com/shop/pdp/x/1", "topic": "shop"},
           {"tiktok_url": "https://www.tiktok.com/@bb_creator", "found_via": "article", "source_url": "https://news.example/top"},
           {"tiktok_url": "https://www.tiktok.com/@cc_creator", "found_via": "article", "source_url": "https://news.example/top"},
           {"tiktok_url": "https://www.tiktok.com/@dd_creator", "found_via": "article", "source_url": "https://news.example/top"},
           {"tiktok_url": "https://www.tiktok.com/@ghost", "topic": "made up", "source_url": "https://nowhere.example"}]
    cited = {"https://www.tiktok.com/@aa_creator/video/123456789", "https://news.example/top"}
    leads = tts.select_leads(raw, cited)
    assert [l.handle for l in leads] == ["aa_creator", "bb_creator", "cc_creator", "ghost"]  # dup, shop and third article dropped
    assert {l.handle: l.verified for l in leads} == {"aa_creator": True, "bb_creator": True, "cc_creator": True,
                                                     "ghost": False}
    assert len(tts.select_leads([{"tiktok_url": f"https://www.tiktok.com/@u{i}"} for i in range(20)], set())) \
        == tts.MAX_LEADS


# ------------------------------------------------------------------ storage: campaign-scoped, human decisions only
@pytest.fixture
def mem_db(monkeypatch):
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(db.SCHEMA)
    monkeypatch.setattr(db, "_conn", conn)
    import relationships
    relationships.ensure_schema()
    yield conn
    conn.close()


def test_suggestions_are_never_shortlisted_automatically(mem_db):
    lead = tts.Lead("pc_max", "https://www.tiktok.com/@pc_max", "CS2", "https://news.example", True, "article")
    assert tts.save_suggestions("c1", [lead]) == 1
    assert tts.save_suggestions("c1", [lead]) == 0  # no duplicates
    assert mem_db.execute("SELECT COUNT(*) FROM shortlist").fetchone()[0] == 0
    tts.set_status("c1", "pc_max", "ignored")
    tts.save_suggestions("c1", [lead])
    assert tts.lead("c1", "pc_max")["status"] == "ignored"  # a human decision is not overwritten by new research


def test_manual_add_validates_and_is_an_explicit_shortlist(mem_db):
    assert tts.add_manual("c1", "https://www.tiktok.com/shop/pdp/1") is None
    assert mem_db.execute("SELECT COUNT(*) FROM tiktok_leads").fetchone()[0] == 0
    ref = tts.add_manual("c1", "https://www.tiktok.com/@Known.Creator", "Known")
    assert ref.handle == "known.creator"
    row = tts.lead("c1", "known.creator")
    assert (row["name"], row["origin"]) == ("Known", "manual")
    assert mem_db.execute("SELECT creator_id FROM shortlist").fetchall()[0][0] == "tiktok:known.creator"
    stored = set(row.keys())
    assert not stored & {"followers", "views", "likes", "engagement", "score", "bio"}  # no profile data


def test_leads_are_deleted_with_their_campaign(mem_db):
    mem_db.execute("INSERT INTO campaigns VALUES ('c1', '', '{}', '')")
    tts.add_manual("c1", "https://www.tiktok.com/@aa_creator")
    db.delete_campaign("c1")
    assert mem_db.execute("SELECT COUNT(*) FROM tiktok_leads").fetchone()[0] == 0


def test_verification_uses_pages_the_search_actually_retrieved():
    retrieved = {"https://www.tiktok.com/%40pc_max/video/7608954765791300886?u_code=x"}  # percent-encoded, as returned
    leads = tts.select_leads([{"tiktok_url": "https://www.tiktok.com/@pc_max", "topic": "RTX 5060"},
                              {"tiktok_url": "https://www.tiktok.com/@not_retrieved", "topic": "x"}], retrieved)
    assert {l.handle: l.verified for l in leads} == {"pc_max": True, "not_retrieved": False}


def test_response_sources_count_as_evidence():
    data = {"output": [{"type": "web_search_call", "action": {"sources": [{"url": "https://www.tiktok.com/%40pc_max"}]}},
                       {"type": "message", "content": [{"type": "output_text", "text": "{}", "annotations": []}]}]}
    _, cited = web._response_text(data)
    assert "https://www.tiktok.com/@pc_max" in cited
