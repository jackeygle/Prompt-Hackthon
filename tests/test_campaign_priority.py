"""Campaign priority must change the ranking in the intended direction without ever dropping relevance.

Archetypes (same AHP/TOPSIS pipeline as production):
  S  small, fully relevant, strong sales signals (purchase intent, buying advice)
  R  relevant audience with much larger relevant reach
  H2 huge, only partly relevant
  H  huge, weakly relevant
"""
import math

import pandas as pd
import pytest

import ranking


def creator(aud, niche, prod, price, lang, fh, bench, comp, pdisc, rec, vis, mean, tech, intent, reply, views, eng,
            eff, cv, spam, days):
    return dict(audience_relevance=aud, niche_relevance=niche, product_relevance=prod, price_segment_relevance=price,
                target_lang_share=lang, first_hand_experience=fh, benchmark_discussion=bench, product_comparison=comp,
                price_discussion=pdisc, purchase_recommendation=rec, visual_product_share=vis, meaningful_ratio=mean,
                technical_question_ratio=tech, purchase_intent_ratio=intent, creator_reply_rate=reply,
                log_median_views=math.log10(1 + views), engagement_rate=eng, view_efficiency=eff, view_cv=cv,
                log_est_cost_eur=math.log10(1 + views / 1000 * 20), spam_ratio=spam, days_since_last_relevant=days)


ARCHETYPES = {
    "S": creator(3, 3, 3, 3, .95, 3, 3, 3, 3, 3, .8, .7, .5, .20, .3, 20_000, .07, .5, .3, .01, 10),
    "R": creator(3, 3, 1, 1, .90, 2, 2, 1, 1, 1, .5, .5, .3, .05, .1, 160_000, .05, .4, .4, .02, 7),
    "H2": creator(2, 2, 0, 0, .80, 1, 1, 0, 0, 0, .2, .3, .1, .02, .02, 1_500_000, .03, .3, .5, .04, 4),
    "H": creator(1, 1, 0, 0, .80, 0, 0, 0, 0, 0, .1, .2, .05, .01, .01, 2_000_000, .02, .3, .5, .05, 5),
}


def scores(preset: str) -> pd.Series:
    df = pd.DataFrame(ARCHETYPES).T.reindex(columns=[c.name for c in ranking.CRITERIA])
    w, _ = ranking.ahp_weights(ranking.AHP_PRESETS[preset])
    weights = ranking.criterion_weights(dict(zip(ranking.GROUPS, w)), preset=preset)
    return ranking.topsis(df, weights)["score"]


@pytest.mark.parametrize("preset", list(ranking.AHP_PRESETS))
def test_presets_are_consistent_and_relevance_always_weighs(preset):
    w, cr = ranking.ahp_weights(ranking.AHP_PRESETS[preset])
    assert cr <= 0.1
    assert dict(zip(ranking.GROUPS, w))["Campaign Fit"] >= 0.2  # relevance never becomes an afterthought


def test_drive_sales_favours_purchase_signals():
    s = scores("conversion")
    assert s.idxmax() == "S"


def test_reach_favours_relevant_reach_not_the_biggest_channel():
    s = scores("awareness")
    assert s.idxmax() == "R"           # relevant and far bigger than S
    assert s["H"] == s.min()           # the biggest channel, weakly relevant, is last
    assert s["R"] > s["H2"] > s["H"]   # among big channels, the more relevant one wins


def test_balanced_sits_between():
    gap = {p: scores(p)["S"] - scores(p)["R"] for p in ranking.AHP_PRESETS}
    assert gap["conversion"] > gap["balanced"] > gap["awareness"]


@pytest.mark.parametrize("preset", list(ranking.AHP_PRESETS))
def test_weakly_relevant_giant_never_wins(preset):
    assert scores(preset).idxmax() != "H"
