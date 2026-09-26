import pytest

import llm
import web_discovery as web
from models import CampaignSpec


def test_identities_from_urls():
    text = ("https://www.youtube.com/@KreativEcke https://www.tiktok.com/@pc_max/video/123 "
            "https://www.youtube.com/channel/UCabcdefghijklmnopqrstuv instagram.com/p/xyz twitch.tv/pcmax")
    ids = {(i["platform"], i["handle"]) for i in web.identities_from_url_text(text)}
    assert ("youtube", "KreativEcke") in ids
    assert ("youtube", "UCabcdefghijklmnopqrstuv") in ids
    assert ("tiktok", "pc_max") in ids and ("twitch", "pcmax") in ids
    assert not any(p == "instagram" for p, _ in ids)  # /p/ post URL is not a profile


def test_resolve_matches_by_normalised_name_without_api_calls():
    chans = {"UC1": {"id": "UC1", "snippet": {"title": "Tech Keller", "customUrl": "@technikkeller"}}}
    idents = [{"platform": "tiktok", "handle": "technikkeller", "kind": "handle", "name": None, "url": None,
               "query": "q", "source_url": "u", "evidence": "e", "method": "url"}]
    matched, web_only = web.resolve_to_youtube(idents, chans)
    assert list(matched) == ["UC1"] and web_only == []


def test_default_web_queries_cover_platforms():
    spec = CampaignSpec(product="refurbished gaming PCs", product_keywords=[], niche="PC hardware",
                        price_segment="€600–900", target_country="DE", target_language="de", audience="gamers",
                        goal="conversion", search_queries=["q"])
    qs = " ".join(web.default_web_queries(spec))
    for site in ("site:tiktok.com", "site:instagram.com", "site:twitch.tv", "site:youtube.com"):
        assert site in qs


def test_missing_tavily_key_is_reported(monkeypatch):
    monkeypatch.setattr(web.config, "TAVILY_API_KEY", "")
    with pytest.raises(web.WebDiscoveryError, match="TAVILY_API_KEY"):
        web.tavily_search("x")


def test_retry_delay_parsing():
    assert llm._retry_delay("Please try again in 1m2.5s", 0) == pytest.approx(63.5)
    assert llm._retry_delay("Please retry in 27.6s.", 0) == pytest.approx(28.6)
    assert llm._retry_delay("nothing", 30) == 30


def test_missing_key_falls_back_to_next_model(monkeypatch, tmp_path):
    from pydantic import BaseModel
    import db

    class Out(BaseModel):
        x: int

    db.use(tmp_path / "t.db")
    monkeypatch.setattr(llm.config, "GROQ_API_KEY", "")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setitem(llm.BACKENDS, "fake", lambda *a: '{"x": 7}')
    monkeypatch.setitem(llm.config.PROVIDER_RPM, "fake", 100)
    llm._limiter.dead.pop("groq/test-model", None)
    client = llm.LLMClient(["groq/test-model", "fake/m"])
    assert client.extract(Out, "s", "u").x == 7
    assert "groq/test-model" in llm._limiter.dead
    assert llm.missing_keys(["groq/a"]) == ["GROQ_API_KEY"]
