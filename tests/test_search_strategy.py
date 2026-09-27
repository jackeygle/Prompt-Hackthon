"""Search strategy: audience -> current topics (web) -> content searches (main) + creator searches (secondary)."""
import pytest

import config
import pipeline
import web_discovery as web
from models import CampaignSpecDraft, CurrentTopic, ResearchQueries, SearchPlan, TopicResearch

DRAFT = CampaignSpecDraft(
    product="refurbished gaming PCs", product_keywords=["gaming PC"], niche="PC gaming", target_country="DE",
    target_language="de", audience="gamers in Germany", audience_interests=["PC gaming", "competitive gaming"],
    search_queries=["gaming pc kaufen", "neueste spiele"], web_queries=["beste gaming youtuber"])
TOPICS = [CurrentTopic(name="Counter-Strike 2", kind="game", why="big competitive scene"),
          CurrentTopic(name="RTX 5070", kind="hardware", why="current mid-range GPU")]


@pytest.fixture(autouse=True)
def default_sizes(monkeypatch):  # independent of a developer's .env (e.g. N_QUERIES=1 to save quota)
    monkeypatch.setattr(config, "N_QUERIES", 6)
    monkeypatch.setattr(config, "N_CREATOR_QUERIES", 2)


class FakeLLM:
    def __init__(self, plan=None, fail_plan=False):
        self.plan, self.fail_plan, self.prompts = plan, fail_plan, []

    def extract(self, schema, system, user, **_):
        self.prompts.append((schema.__name__, system, user))
        if schema is CampaignSpecDraft:
            return DRAFT
        if schema is SearchPlan:
            if self.fail_plan:
                raise RuntimeError("model down")
            return self.plan
        raise AssertionError(schema)


PLAN = SearchPlan(content_queries=["CS2 FPS Einstellungen", "RTX 5070 Test", "CS2 FPS Einstellungen"],
                  creator_queries=["beste Gaming YouTuber Deutschland", "top tech youtuber", "one too many", "x"],
                  web_queries=["CS2 creator deutsch", "site:twitch.tv cs2 deutsch"])


def test_topics_drive_the_plan(monkeypatch):
    monkeypatch.setattr(web, "research_current_topics", lambda spec, llm: (TOPICS, None))
    llm = FakeLLM(PLAN)
    spec = pipeline.parse_brief(llm, "brief")
    assert spec.topics_source == "web search" and [t.name for t in spec.current_topics] == ["Counter-Strike 2", "RTX 5070"]
    plan_prompt = next(u for n, _, u in llm.prompts if n == "SearchPlan")
    assert "Counter-Strike 2" in plan_prompt and "RTX 5070" in plan_prompt  # the concrete names reach the planner
    assert spec.search_queries == ["CS2 FPS Einstellungen", "RTX 5070 Test"]  # deduplicated
    assert len(spec.creator_queries) == config.N_CREATOR_QUERIES
    assert spec.field_sources["search_queries"] == "web search"


def test_content_searches_come_first_and_dominate(monkeypatch):
    monkeypatch.setattr(web, "research_current_topics", lambda spec, llm: (TOPICS, None))
    spec = pipeline.parse_brief(FakeLLM(PLAN), "brief")
    spec.search_queries = [f"topic {i}" for i in range(10)]
    qs = pipeline.discovery_queries(spec)
    assert qs[:config.N_QUERIES] == spec.search_queries[:config.N_QUERIES]
    creator = [q for q in qs if q in spec.creator_queries]
    assert 0 < len(creator) < len(qs) - len(creator)  # secondary path present but in the minority


def test_without_web_search_it_says_so(monkeypatch):
    monkeypatch.setattr(web, "research_current_topics", lambda spec, llm: ([], "no web search provider"))
    llm = FakeLLM(PLAN)
    spec = pipeline.parse_brief(llm, "brief")
    assert spec.topics_source == "model knowledge" and not spec.current_topics
    assert "no web research available" in next(u for n, _, u in llm.prompts if n == "SearchPlan")


def test_planning_failure_is_explained_and_topped_up_with_topics(monkeypatch):
    monkeypatch.setattr(web, "research_current_topics", lambda spec, llm: (TOPICS, None))
    spec = pipeline.parse_brief(FakeLLM(fail_plan=True), "brief")
    # the brief's own searches first, then the concrete researched topics, never a silent single query
    assert spec.search_queries == [*DRAFT.search_queries, "Counter-Strike 2", "RTX 5070"]
    assert spec.field_sources["search_queries"] == "fallback"
    assert any("Search planning failed (model down)" in n for n in spec.strategy_notes)


def test_prompts_are_topic_agnostic():
    """Prenew-aware, not gaming-only: the prompts tell the model to follow other topics without adding gaming."""
    for prompt in (pipeline.PLAN_SYSTEM, web.AUDIENCE_LENS):
        assert "never add gaming" in prompt


def test_tavily_topics_must_appear_in_results(monkeypatch):
    monkeypatch.setattr(web, "available_providers", lambda: ["tavily"])
    monkeypatch.setattr(web, "tavily_search", lambda q, n=6: [
        {"url": "u", "title": "Most watched games in Germany", "content": "Counter-Strike 2 and Fortnite lead."}])

    class LLM:
        def extract(self, schema, system, user, **_):
            if schema is ResearchQueries:
                return ResearchQueries(queries=["most played games Germany"])
            assert schema is TopicResearch
            return TopicResearch(topics=[CurrentTopic(name="Counter-Strike 2", kind="game"),
                                         CurrentTopic(name="Invented Game 9", kind="game")])
    spec = pipeline.CampaignSpec(**DRAFT.model_dump(exclude_none=True))
    topics, err = web.research_current_topics(spec, LLM())
    assert err is None and [t.name for t in topics] == ["Counter-Strike 2"]  # hallucination guard


def test_topic_research_failure_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(web, "available_providers", lambda: ["azure"])  # no fallback available

    def boom(prompt, **_):
        raise web.WebDiscoveryError("OpenAI web search HTTP 500")
    monkeypatch.setattr(web, "openai_web_search", boom)
    topics, err = web.research_current_topics(pipeline.CampaignSpec(**DRAFT.model_dump(exclude_none=True)), None)
    assert topics == [] and "500" in err


@pytest.mark.parametrize("n_content,expected", [(1, 0), (3, 1), (6, 2), (10, 2)])
def test_creator_searches_never_outnumber_content_searches(monkeypatch, n_content, expected):
    monkeypatch.setattr(config, "N_QUERIES", n_content)
    assert pipeline.n_creator_searches(n_content) == expected
    spec = pipeline.CampaignSpec(**DRAFT.model_dump(exclude_none=True))
    spec.search_queries = [f"topic {i}" for i in range(n_content)]
    spec.creator_queries = ["top creators a", "top creators b"]
    qs = pipeline.discovery_queries(spec)
    assert sum(q.startswith("top creators") for q in qs) == expected


def test_topic_research_falls_back_to_tavily_when_model_search_fails(monkeypatch):
    monkeypatch.setattr(web, "available_providers", lambda: ["azure", "tavily"])

    def no_credits(prompt, **_):
        raise web.WebDiscoveryError("azure web search HTTP 429: quota")
    monkeypatch.setattr(web, "openai_web_search", no_credits)
    monkeypatch.setattr(web, "tavily_search", lambda q, n=6: [
        {"url": "u", "title": "Trending in Germany", "content": "Counter-Strike 2 dominates Twitch Germany."}])

    class LLM:
        def extract(self, schema, system, user, **_):
            if schema is ResearchQueries:
                return ResearchQueries(queries=["most played games Germany"])
            return TopicResearch(topics=[CurrentTopic(name="Counter-Strike 2", kind="game")])
    topics, err = web.research_current_topics(pipeline.CampaignSpec(**DRAFT.model_dump(exclude_none=True)), LLM())
    assert err is None and [t.name for t in topics] == ["Counter-Strike 2"]


def test_priority_starts_balanced_even_if_the_model_guesses(monkeypatch):
    monkeypatch.setattr(web, "research_current_topics", lambda spec, llm: (TOPICS, None))
    llm = FakeLLM(PLAN)
    llm.extract = lambda schema, system, user, **_: (DRAFT.model_copy(update={"goal": "conversion"})
                                                     if schema is CampaignSpecDraft else PLAN)
    spec = pipeline.parse_brief(llm, "brief")
    assert spec.goal == "balanced" and spec.field_sources["goal"] == "default"


def test_providers_default_to_azure_gpt56(monkeypatch):
    monkeypatch.setattr(config, "WEB_SEARCH_PROVIDER", "azure")
    for k, v in {"AZURE_OPENAI_API_KEY": "k", "AZURE_OPENAI_ENDPOINT": "https://x.services.ai.azure.com/openai/v1/responses",
                 "OPENAI_API_KEY": "k", "TAVILY_API_KEY": "k", "AZURE_OPENAI_DEPLOYMENT": "gpt-5.6-sol"}.items():
        monkeypatch.setattr(config, k, v)
    assert web.available_providers() == ["azure", "openai", "tavily"]
    assert web.search_model("azure") == "gpt-5.6-sol"
    url, headers = web._responses_endpoint("azure")
    assert url.endswith("/openai/v1/responses") and "api-key" in headers
    monkeypatch.setattr(config, "AZURE_OPENAI_API_KEY", "")
    assert web.provider() == "openai"  # falls through to the next provider with credentials


def test_research_uses_the_model_search_with_its_provider(monkeypatch):
    monkeypatch.setattr(web, "available_providers", lambda: ["azure"])
    seen = {}

    def fake(prompt, via="azure"):
        seen["via"] = via
        return {"topics": [{"name": "Counter-Strike 2", "kind": "game", "why": "x"}]}, {"u"}
    monkeypatch.setattr(web, "openai_web_search", fake)
    topics, err = web.research_current_topics(pipeline.CampaignSpec(**DRAFT.model_dump(exclude_none=True)), None)
    assert err is None and seen["via"] == "azure" and topics[0].name == "Counter-Strike 2"


def test_partial_plan_and_failed_research_are_reported(monkeypatch):
    monkeypatch.setattr(web, "research_current_topics", lambda spec, llm: ([], "azure: HTTP 400; openai: HTTP 429"))
    spec = pipeline.parse_brief(FakeLLM(SearchPlan(content_queries=["Patch 26.18 Tier List"])), "brief")
    assert spec.search_queries == ["Patch 26.18 Tier List"]
    assert any("Current-topic research failed (azure: HTTP 400; openai: HTTP 429)" in n for n in spec.strategy_notes)
    assert any("only 1 of 6 content searches" in n for n in spec.strategy_notes)


def test_plan_without_content_queries_falls_back_with_a_note(monkeypatch):
    monkeypatch.setattr(web, "research_current_topics", lambda spec, llm: (TOPICS, None))
    spec = pipeline.parse_brief(FakeLLM(SearchPlan(creator_queries=["top lol youtubers"])), "brief")
    assert spec.field_sources["search_queries"] == "fallback" and len(spec.search_queries) == 4
    assert any("no content searches" in n for n in spec.strategy_notes)


def test_the_plan_always_proposes_six_even_if_fewer_run(monkeypatch):
    monkeypatch.setattr(config, "N_QUERIES", 1)
    monkeypatch.setattr(web, "research_current_topics", lambda spec, llm: (TOPICS, None))
    llm = FakeLLM(SearchPlan(content_queries=[f"q{i}" for i in range(8)], creator_queries=["c"]))
    spec = pipeline.parse_brief(llm, "brief")
    assert len(spec.search_queries) == 6 and not spec.strategy_notes
    assert "Write 6 content_queries" in next(u for n, _, u in llm.prompts if n == "SearchPlan")
    assert pipeline.discovery_queries(spec) == ["q0"]  # N_QUERIES=1: only the first one runs


@pytest.mark.parametrize("kind,expected", [("creator", "topic"), ("Tournament", "event"), ("patch", "topic"),
                                           ("game", "game"), (None, "topic")])
def test_unexpected_topic_kinds_do_not_break_research(kind, expected):
    research = TopicResearch.model_validate({"topics": [{"name": "Faker", "kind": kind}, {"name": " ", "kind": "game"}]})
    assert [(t.name, t.kind) for t in research.topics] == [("Faker", expected)]
