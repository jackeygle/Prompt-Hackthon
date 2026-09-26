import config
import pipeline
import ranking
from models import CampaignSpecDraft

BASE = dict(product="refurbished gaming PCs", product_keywords=["gaming pc"], niche="PC gaming",
            target_country="DE", target_language="de", audience="gamers", goal="conversion",
            search_queries=["gaming pc"], web_queries=["site:tiktok.com gaming"])


class StubLLM:
    def __init__(self, draft):
        self.draft = draft

    def extract(self, schema, system, user):
        assert schema is CampaignSpecDraft
        return self.draft


def test_unstated_values_use_defaults_and_are_marked_as_defaults():
    spec = pipeline.parse_brief(StubLLM(CampaignSpecDraft(**BASE)), "brief without numbers")
    assert (spec.min_subscribers, spec.max_subscribers) == (config.MIN_SUBSCRIBERS, config.MAX_SUBSCRIBERS)
    assert spec.field_sources["min_subscribers"] == "default"
    assert spec.field_sources["price_segment"] == "default" and spec.price_segment == ""
    assert spec.n_creators == config.DEFAULT_N_CREATORS
    assert spec.audience_interests == ["PC gaming"] and spec.field_sources["audience_interests"] == "default"


def test_stated_values_are_kept_and_marked_as_extracted():
    draft = CampaignSpecDraft(**BASE, price_segment="€600–900", min_subscribers=50_000, max_subscribers=800_000,
                              audience_interests=["PC gaming", "GPUs"])
    spec = pipeline.parse_brief(StubLLM(draft), "creators with 50k–800k subscribers")
    assert (spec.min_subscribers, spec.max_subscribers) == (50_000, 800_000)
    assert spec.field_sources["min_subscribers"] == spec.field_sources["max_subscribers"] == "brief"
    assert spec.price_segment == "€600–900" and spec.field_sources["price_segment"] == "brief"


def test_inconsistent_range_keeps_only_the_stated_minimum():
    draft = CampaignSpecDraft(**BASE, min_subscribers=900_000, max_subscribers=100_000)
    spec = pipeline.parse_brief(StubLLM(draft), "x")
    assert spec.min_subscribers == 900_000 and spec.max_subscribers == config.MAX_SUBSCRIBERS


def test_audience_relevance_can_carry_a_gaming_creator_through_the_hard_filter():
    f = {"days_since_last_relevant": 5, "n_relevant_videos": 8, "niche_relevance": 0, "audience_relevance": 3}
    assert ranking.hard_filter(f)[0]
    f["audience_relevance"] = 0
    assert not ranking.hard_filter(f)[0]
