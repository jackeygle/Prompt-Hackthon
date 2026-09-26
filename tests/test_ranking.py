import numpy as np
import pandas as pd
import pytest

import ranking
from collectors import extract_social_links
from features import statistical_features


def test_ahp_presets_are_consistent():
    for name, m in ranking.AHP_PRESETS.items():
        w, cr = ranking.ahp_weights(m)
        assert abs(w.sum() - 1) < 1e-9
        assert cr < 0.1, name


def test_ahp_conversion_prioritises_fit():
    w, _ = ranking.ahp_weights(ranking.AHP_PRESETS["conversion"])
    assert np.argmax(w) == ranking.GROUPS.index("Campaign Fit")


def test_ahp_rejects_non_reciprocal():
    with pytest.raises(ValueError):
        ranking.ahp_weights([[1, 2], [2, 1]])


def test_criterion_weights_sum_to_one():
    w, _ = ranking.ahp_weights(ranking.AHP_PRESETS["balanced"])
    cw = ranking.criterion_weights(dict(zip(ranking.GROUPS, w)))
    assert abs(sum(cw.values()) - 1) < 1e-9
    assert set(cw) == {c.name for c in ranking.CRITERIA}


def _weights():
    w, _ = ranking.ahp_weights(ranking.AHP_PRESETS["conversion"])
    return ranking.criterion_weights(dict(zip(ranking.GROUPS, w)))


def test_topsis_dominant_creator_wins_and_cost_criteria_invert():
    cols = [c.name for c in ranking.CRITERIA]
    good = {c.name: (1.0 if c.kind == "C" else 3.0) for c in ranking.CRITERIA}
    bad = {c.name: (3.0 if c.kind == "C" else 1.0) for c in ranking.CRITERIA}
    mid = {c.name: 2.0 for c in ranking.CRITERIA}
    df = pd.DataFrame([bad, good, mid], index=["bad", "good", "mid"])[cols]
    res = ranking.topsis(df, _weights())
    assert list(res.index) == ["good", "mid", "bad"]
    assert res.loc["good", "score"] == pytest.approx(1.0)
    assert res.loc["bad", "score"] == pytest.approx(0.0)


def test_topsis_handles_missing_and_constant_columns():
    df = pd.DataFrame({"niche_relevance": [3, 2, np.nan], "spam_ratio": [0.1, 0.1, 0.1]}, index=list("abc"))
    res = ranking.topsis(df, {"niche_relevance": 0.5, "spam_ratio": 0.5})
    assert res["score"].notna().all()
    assert bool(res.loc["c", "imputed__niche_relevance"])
    assert res.loc["a", "rank"] == 1


def test_explain_returns_strengths_and_gaps():
    cols = [c.name for c in ranking.CRITERIA]
    rng = np.random.default_rng(0)
    df = pd.DataFrame(rng.random((4, len(cols))), columns=cols, index=list("abcd"))
    res = ranking.topsis(df, _weights())
    ex = ranking.explain(res.loc["a"], _weights())
    assert len(ex["strengths"]) == 3 and len(ex["gaps"]) == 3


def test_hard_filter():
    ok, _ = ranking.hard_filter({"days_since_last_relevant": 0, "n_relevant_videos": 5, "niche_relevance": 3})
    assert ok
    ok, reason = ranking.hard_filter({"days_since_last_relevant": 400, "n_relevant_videos": 1, "niche_relevance": 0})
    assert not ok and "relevant" in reason
    ok, reason = ranking.hard_filter({"days_since_last_relevant": 3, "n_relevant_videos": 5, "niche_relevance": 3,
                                      "target_lang_share": 0.05})
    assert not ok and "target language" in reason


def test_confidence_bounds():
    assert ranking.confidence({}) == 0.0
    full = {"n_analyzed_videos": 5, "transcript_coverage": 1, "n_classified_comments": 300,
            "source_reliability": 1.0, "days_since_last_relevant": 0}
    assert ranking.confidence(full) == pytest.approx(1.0)
    assert ranking.confidence_label(0.5) == "Medium"


def test_statistical_features():
    vids = [{"views": v, "likes": v // 20, "comments": v // 100, "published_at": "2026-09-01T00:00:00Z"}
            for v in (1000, 2000, 3000)]
    f = statistical_features(vids, vids, subscribers=10_000)
    assert f["median_relevant_views"] == 2000
    assert f["view_efficiency"] == pytest.approx(0.2)
    assert f["engagement_rate"] == pytest.approx(0.06)
    assert f["n_relevant_videos"] == 3


def test_social_links():
    links = extract_social_links("Folgt mir: https://www.tiktok.com/@pc_max und instagram.com/p/xyz "
                                 "https://instagram.com/pcmax.de twitch.tv/pcmax")
    assert links == {"tiktok": "pc_max", "instagram": "pcmax.de", "twitch": "pcmax"}
