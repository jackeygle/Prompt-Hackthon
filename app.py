"""Prenew Creator Intelligence: Streamlit front-end.

Campaign → Creators (ranked creator cards) → Creator analysis (why + evidence) → Shortlist.
Presentation only: discovery, feature extraction and AHP/TOPSIS ranking live in pipeline.py / ranking.py.

Run:  streamlit run app.py
"""
import json
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

import config
import db
import pipeline
import ranking
import relationships as rel
import ui
import tiktok_scout as tts
import web_discovery as web
from llm import LLMClient, missing_keys
from models import CampaignSpec
from ui import T, esc, html

st.set_page_config(page_title="Prenew Creator Intelligence", page_icon="◆", layout="wide",
                   initial_sidebar_state="collapsed")
ui.inject_css()

DEFAULT_BRIEF = "Find creators for €600–900 refurbished gaming PCs targeting gamers in Germany."  # demo example only
BRIEF_EXAMPLES = ("e.g. “Gaming PCs under €800 for Fortnite and CS2 players in Sweden” or "
                  "“RTX 4070 PCs for streamers in Finland, creators with 20k–500k subscribers”")
COUNTRY = {"DE": "Germany", "AT": "Austria", "CH": "Switzerland", "FR": "France", "NL": "Netherlands",
           "SE": "Sweden", "FI": "Finland", "DK": "Denmark", "NO": "Norway", "US": "United States",
           "GB": "United Kingdom", "UK": "United Kingdom", "ES": "Spain", "IT": "Italy", "PL": "Poland"}
LANG = {"de": "German", "en": "English", "fr": "French", "nl": "Dutch", "sv": "Swedish", "fi": "Finnish",
        "da": "Danish", "no": "Norwegian", "es": "Spanish", "it": "Italian", "pl": "Polish"}
# Campaign priority = the existing AHP presets, named as the business decision they encode
PRIORITY = {"conversion": ("Drive sales", "Find creators whose audience is likely to buy."),
            "awareness": ("Reach more gamers", "Prioritize reach, views and brand exposure."),
            "balanced": ("Balanced", "Balance sales potential and reach.")}
PRIORITY_HELP = "What matters most for this campaign? Changes how the creators are ranked, never who is found."
BUDGET_LABEL = "Budget per creator video (€)"
BUDGET_HELP = ("Maximum you expect to spend on one creator video (0 = no limit). Creators whose estimated cost "
               f"(median views × assumed €{config.ASSUMED_CPM_EUR:.0f} CPM) is above it are marked Over budget, "
               "listed last and left out of the top picks. It does not change the score or the search.")
GROUP_LABEL = {"Campaign Fit": "Campaign fit", "Content Credibility": "Product evidence",
               "Audience Quality": "Community", "Reach & Performance": "Performance", "Cost & Risk": "Cost & risk"}
# Card bars are ABSOLUTE 0-100 values (comparable across campaigns and not stretched by a small candidate set);
# only the campaign score is relative to the candidates. (label, feature(s), scale)
CARD_BARS = [("Audience relevance", ["audience_relevance"], 3),          # LLM ordinal 0-3
             ("Content relevance", ["niche_relevance", "product_relevance", "price_segment_relevance"], 3),
             ("Community quality", ["meaningful_ratio"], 1),                # share of substantive comments
             ("Market fit", ["target_lang_share"], 1)]                     # share of target-language comments
EVIDENCE_BAR = ("Product evidence", ["first_hand_experience", "benchmark_discussion", "product_comparison",
                                     "price_discussion", "purchase_recommendation"], 3)
# plain-language reasons for strong / weak criteria (derived from TOPSIS closeness, never from an LLM verdict)
REASON = {
    "audience_relevance": ("Reaches the gamers and tech buyers we target", "Weak link to our target audience"),
    "niche_relevance": ("Content squarely in the campaign niche", "Only partly in the campaign niche"),
    "product_relevance": ("Covers this product type directly", "Rarely covers this product type"),
    "price_segment_relevance": ("Talks about the campaign price segment", "Little coverage of this price segment"),
    "target_lang_share": ("Strong {lang}-speaking audience signal", "Weak {lang}-speaking audience signal"),
    "first_hand_experience": ("Hands-on, first-hand product content", "Little hands-on content"),
    "benchmark_discussion": ("Shows tests and measurable results", "Few tests or measurable results"),
    "product_comparison": ("Compares products for viewers", "Few product comparisons"),
    "price_discussion": ("Discusses prices and value", "Rarely discusses prices"),
    "purchase_recommendation": ("Gives concrete buying advice", "Little buying advice"),
    "visual_product_share": ("Product visible in thumbnails", "Product rarely visible in thumbnails"),
    "meaningful_ratio": ("Substantive comment discussions", "Mostly generic comments"),
    "technical_question_ratio": ("In-depth, question-driven community", "Few in-depth questions"),
    "purchase_intent_ratio": ("Viewers show purchase intent", "Little purchase intent in comments"),
    "creator_reply_rate": ("Actively replies to the community", "Rarely replies to comments"),
    "log_median_views": ("Strong reach on relevant videos", "Lower reach on relevant videos"),
    "engagement_rate": ("High engagement rate", "Lower engagement rate"),
    "view_efficiency": ("Views outperform channel size", "Views lag behind channel size"),
    "view_cv": ("Consistent views across videos", "Volatile views across videos"),
    "log_est_cost_eur": ("Low estimated cost proxy", "High estimated cost proxy"),
    "spam_ratio": ("Clean, low-spam comments", "Noticeable spam in comments"),
    "days_since_last_relevant": ("Recently active on the topic", "Not recently active on the topic"),
    # Twitch / Instagram rankings
    "tw_audience_relevance": ("Streams what our target audience watches", "Weak link to our target audience"),
    "tw_vod_view_ratio": ("Replays watched well for the follower count", "Few replay views for the follower count"),
    "tw_log_followers": ("Large follower base", "Smaller follower base"),
    "tw_log_median_vod_views": ("Strong replay views", "Low replay views"),
    "tw_streams_30d": ("Streams regularly", "Streams rarely"),
    "tw_days_since_last_stream": ("Streamed recently", "Has not streamed recently"),
    "ig_audience_relevance": ("Posts what our target audience follows", "Weak link to our target audience"),
    "ig_target_lang": ("Posts in {lang}", "Posts mostly not in {lang}"),
    "ig_engagement_rate": ("High engagement rate", "Lower engagement rate"),
    "ig_comment_like_ratio": ("Followers actively comment", "Few comments per like"),
    "ig_log_followers": ("Large follower base", "Smaller follower base"),
    "ig_log_median_likes": ("Strong likes per post", "Fewer likes per post"),
    "ig_posts_30d": ("Posts regularly", "Posts rarely"),
    "ig_days_since_last_post": ("Posted recently", "Has not posted recently"),
}
PLATFORMS = {"youtube": "YouTube", "twitch": "Twitch", "instagram": "Instagram", "tiktok": "TikTok"}
EVIDENCE_KIND = {"audience_relevance": "Audience relevance", "niche_relevance": "Niche relevance", "product_relevance": "Product relevance",
                 "price_segment_relevance": "Price-segment evidence", "first_hand_experience": "Hands-on evidence",
                 "benchmark_discussion": "Test & results evidence", "price_discussion": "Price evidence",
                 "product_comparison": "Comparison evidence", "purchase_recommendation": "Buying-advice evidence"}
COMMENT_LABEL = {"purchase_intent": "Purchase intent", "technical": "In-depth / expert", "question": "Question",
                 "meaningful": "Meaningful", "generic": "Generic", "spam": "Suspicious / spam"}
GROUP_ORDER = {g: i for i, g in enumerate(ranking.GROUPS)}  # fit → credibility → community → reach → cost
CRITERION_UI_LABEL = {"benchmark_discussion": "Tests & measurable results",
                      "technical_question_ratio": "In-depth / question comments",
                      "log_est_cost_eur": "Estimated cost proxy (assumed CPM, log)"}


def crit_label(c) -> str:
    return CRITERION_UI_LABEL.get(c.name, c.label)


STAGES = ["Understanding campaign", "Searching YouTube", "Searching the web", "Screening candidates",
          "Analyzing content & communities", "Ranking candidates"]

ss = st.session_state
ss.setdefault("view", "campaign")


# ------------------------------------------------------------------ data access
def open_db() -> None:
    db.use(config.DB_PATH)
    rel.ensure_schema()  # shortlist (+ position, favorite), sponsorships, sponsorship KPIs


def list_campaigns() -> list[dict]:
    return db.query("SELECT id, brief, spec_json, created_at FROM campaigns ORDER BY created_at DESC")


def shortlist_ids(campaign_id: str) -> list[str]:
    return rel.shortlist_ids(campaign_id)  # the marketer's manual order


def toggle_shortlist(campaign_id: str, cid: str) -> None:
    name = ("@" + cid[len(tts.PREFIX):] if cid.startswith(tts.PREFIX) else
            (db.query("SELECT name FROM creators WHERE id=?", (cid,)) or [{"name": "Creator"}])[0]["name"])
    if cid in shortlist_ids(campaign_id):
        rel.remove_from_shortlist(campaign_id, cid)
        ss.toast = (f"Removed {name} from the shortlist", ":material/bookmark_remove:")
    else:
        rel.add_to_shortlist(campaign_id, cid)
        ss.toast = (f"Added {name} to the shortlist", ":material/bookmark_added:")


def go(view: str, creator: str | None = None) -> None:
    ss.view = view
    if creator:
        ss.creator = creator
    if view == "shortlist":
        ss.sl_from = "discover"  # reached from the search's results (the cart); the global list sets its own


def open_shortlist(campaign_id: str) -> None:
    """Global Shortlists → one search's shortlist (the existing page, same controls)."""
    ss.update(campaign=campaign_id, view="shortlist", creator=None, sl_from="shortlists")


def open_sponsorships(campaign_id: str | None = None) -> None:
    """Global Sponsorships, optionally narrowed to one search (the campaign filter, kept like any other filter)."""
    ss["sp_f_campaign"] = campaign_id or "all"
    ss.view = "sponsorships"


def new_search() -> None:
    ss.view = "new"


def keep(store: str, default) -> dict:
    """Widget kwargs that keep its value in a plain session key. Streamlit wipes a widget's own key on any run that
    doesn't render it, so filters and ranking settings would otherwise reset after visiting another page."""
    ss.setdefault(store, default)
    widget = "_w_" + store
    ss[widget] = ss[store]
    return {"key": widget, "on_change": lambda: ss.update({store: ss[widget]})}


def preset_of(spec: CampaignSpec, campaign_id: str) -> str:
    return ss.get(f"preset_{campaign_id}") or spec.goal


def group_weights(spec: CampaignSpec, campaign_id: str) -> dict[str, float]:
    preset = preset_of(spec, campaign_id)
    w, _ = ranking.ahp_weights(ranking.AHP_PRESETS[preset])
    gw = {g: float(x) for g, x in zip(ranking.GROUPS, w)}
    if ss.get(f"custom_{campaign_id}"):
        gw = {g: ss.get(f"w_{campaign_id}_{preset}_{g}", gw[g]) for g in ranking.GROUPS}
    total = sum(gw.values()) or 1.0
    return {g: v / total for g, v in gw.items()}


def build_context(campaign: dict) -> dict:
    """Loads one campaign and ranks it live with the current weights (same calls as before the redesign)."""
    spec = CampaignSpec.model_validate_json(campaign["spec_json"])
    cid_c = campaign["id"]
    feats = pipeline.load_features(cid_c)
    stored = {r["creator_id"]: r for r in db.query("SELECT * FROM rankings WHERE campaign_id=?", (cid_c,))}
    creators = {r["id"]: r for r in db.query("SELECT * FROM creators")}
    accounts = pd.DataFrame(db.query("SELECT * FROM platform_accounts"))
    discoveries = pd.DataFrame(db.query("SELECT * FROM discoveries WHERE campaign_id=?", (cid_c,)))
    run_stats = next((json.loads(r["stats_json"]) for r in
                      db.query("SELECT stats_json FROM run_stats WHERE campaign_id=?", (cid_c,))), None)
    gw = group_weights(spec, cid_c)
    preset = preset_of(spec, cid_c)
    weights = ranking.criterion_weights(gw, preset=preset)
    passed = {c: ranking.hard_filter(f) for c, f in feats.items()}
    ok_ids = [c for c, (p, _) in passed.items() if p]
    df = pd.DataFrame({c: feats[c] for c in ok_ids}).T.reindex(columns=[c.name for c in ranking.CRITERIA])
    res = ranking.topsis(df, weights) if ok_ids else pd.DataFrame(columns=["score", "rank"])
    used = res.attrs.get("weights", {})
    lang = LANG.get(spec.target_language, spec.target_language.upper())
    budget = ss.get(f"budget_{cid_c}", spec.budget_per_video)

    def meta(c):
        return json.loads(stored.get(c, {}).get("breakdown_json") or "{}")

    vms = {}
    for c in res.index:
        f = feats[c]
        n_imp = sum(bool(res.loc[c, f"imputed__{k}"]) for k in used)
        conf = ranking.confidence(f, n_imp)
        n_vid, n_com = int(f.get("n_analyzed_videos") or 0), int(f.get("n_classified_comments") or 0)
        groups = {}
        for g in ranking.GROUPS:
            ks = [k.name for k in ranking.CRITERIA if k.group == g and k.name in used]
            wsum = sum(used[k] for k in ks)
            groups[g] = 100 * sum(used[k] * res.loc[c, f"close__{k}"] for k in ks) / wsum if wsum else None
        ex = ranking.explain(res.loc[c], used, k=len(used))
        # fit before reach before cost: a creator is never "recommended because cheap"
        fit_first = sorted((s for s in ex["strengths"] if s["closeness"] >= 0.6 and s["criterion"] in REASON),
                           key=lambda s: (GROUP_ORDER[ranking.CRITERION_BY_NAME[s["criterion"]].group],
                                          -s["weight"] * s["closeness"]))
        good = [REASON[s["criterion"]][0].format(lang=lang) for s in fit_first][:3]
        gaps = [REASON[s["criterion"]][1].format(lang=lang) for s in ex["gaps"][:4]
                if s["closeness"] <= 0.35 and s["criterion"] in REASON][:3]
        acc = accounts[accounts.creator_id == c] if not accounts.empty else pd.DataFrame()
        yt_row = acc[acc.platform == "youtube"].iloc[0] if not acc.empty and (acc.platform == "youtube").any() else None
        raw = json.loads(yt_row["raw_json"]) if yt_row is not None and yt_row["raw_json"] else {}
        others = {p: h for p, h in zip(acc.platform, acc.handle) if p != "youtube"} if not acc.empty else {}
        other_followers = ({p: int(n) for p, n in zip(acc.platform, acc.followers) if p != "youtube" and pd.notna(n)}
                           if not acc.empty else {})
        d = discoveries[discoveries.creator_key == c] if not discoveries.empty else pd.DataFrame()
        web_platforms = (sorted(set(d[d.source.isin(["openai_web", "tavily_web"])].platform))
                         if not d.empty else [])
        def absolute(names, scale):
            vals = [f[k] for k in names if f.get(k) is not None]
            return 100 * sum(vals) / len(vals) / scale if vals else None

        bars = {label: absolute(names, scale) for label, names, scale in CARD_BARS + [EVIDENCE_BAR]}
        conf_label = ranking.confidence_label(conf)
        # "High" must not sit next to empty evidence: no comments or 2+ unmeasured signals caps it at Medium
        if conf_label == "High" and (n_com == 0 or sum(v is None for v in bars.values()) >= 2):
            conf_label = "Medium"
        cost = f.get("est_cost_eur")
        vms[c] = {
            "bars": bars, "basis": f"{n_vid} video{'s' if n_vid != 1 else ''} · {n_com} comments",
            "over_budget": bool(budget and cost and cost > budget),
            "engagement": f.get("engagement_rate"), "n_relevant": f.get("n_relevant_videos"),
            "n_analyzed": f.get("n_analyzed_videos"),
            "id": c, "name": creators.get(c, {}).get("name", c), "rank": int(res.loc[c, "rank"]),
            "score": round(100 * float(res.loc[c, "score"])), "conf": conf, "conf_label": conf_label,
            "subs": f.get("subscribers"), "median_views": f.get("median_relevant_views"),
            "purchase_intent": f.get("purchase_intent_ratio"), "lang_share": f.get("target_lang_share"),
            "cost": cost, "country": creators.get(c, {}).get("country"),
            "avatar": raw.get("thumbnail"), "url": yt_row["url"] if yt_row is not None else None,
            "niche": meta(c).get("niche_label", ""), "summary": meta(c).get("summary", ""),
            "videos": meta(c).get("videos", []), "groups": groups, "reasons": good, "gaps": gaps,
            "others": others, "other_followers": other_followers, "web_platforms": web_platforms, "features": f,
        }
    # Hidden gem: derived from existing numbers only (rule shown in the badge tooltip)
    subs = [v["subs"] for v in vms.values() if v["subs"]]
    med_subs = float(pd.Series(subs).median()) if subs else None
    med_score = float(pd.Series([v["score"] for v in vms.values()]).median()) if vms else None
    for v in vms.values():
        fit = v["groups"].get("Campaign Fit")
        v["gem"] = bool(med_subs and v["subs"] and fit is not None and fit >= 75 and v["subs"] < med_subs
                        and v["score"] >= med_score)
        v["tier"] = ui.tier_of(v["rank"], len(vms))
    vids = sorted({vid for v in vms.values() for vid in v.get("videos") or []})
    if vids:
        rows = db.query(f"SELECT id, views, published_at FROM content WHERE id IN ({','.join('?' * len(vids))})", vids)
        by_id = {r["id"]: r for r in rows}
        for v in vms.values():
            vs = sorted((by_id[i] for i in v.get("videos") or [] if i in by_id), key=lambda r: r["published_at"] or "")
            v["video_views"] = [r["views"] for r in vs]
    platforms = {pl: build_platform(pl, cid_c, gw, stored, creators, lang, preset) for pl in ("twitch", "instagram")}
    return {"campaign": campaign, "spec": spec, "feats": feats, "res": res, "weights": weights, "used": used,
            "group_w": gw, "passed": passed, "vms": vms, "creators": creators, "discoveries": discoveries,
            "run_stats": run_stats, "lang": lang, "budget": budget, "platforms": platforms}


def build_platform(platform: str, cid_c: str, gw: dict, stored: dict, creators: dict, lang: str,
                   preset: str | None = None) -> dict:
    """Separate Twitch / Instagram ranking, re-computed live with the same group weights as YouTube."""
    crits = ranking.PLATFORM_CRITERIA[platform]
    feats = pipeline.load_features(cid_c, platform + ":")
    passed = {c: ranking.platform_hard_filter(platform, f) for c, f in feats.items()}
    ok = [c for c, (p, _) in passed.items() if p]
    df = pd.DataFrame({c: feats[c] for c in ok}).T.reindex(columns=[c.name for c in crits])
    weights = ranking.criterion_weights(gw, crits, preset)
    res = ranking.topsis(df, weights, crits) if ok else pd.DataFrame(columns=["score", "rank"])
    used = res.attrs.get("weights", {})
    acc = {r["id"]: r for r in db.query("SELECT * FROM platform_accounts WHERE platform=?", (platform,))}
    quotes: dict[str, list[str]] = {}
    for r in db.query("SELECT creator_id, quote FROM evidence WHERE campaign_id=? AND creator_id LIKE ? AND verified=1",
                      (cid_c, platform + ":%")):
        quotes.setdefault(r["creator_id"], []).append(r["quote"])
    pre = "tw" if platform == "twitch" else "ig"
    vms = {}
    for c in res.index:
        f, a = feats[c], acc.get(c, {})
        raw = json.loads(a.get("raw_json") or "{}")
        ex = ranking.explain(res.loc[c], used, k=len(used))
        good = sorted((x for x in ex["strengths"] if x["closeness"] >= 0.6),
                      key=lambda x: (GROUP_ORDER[ranking.CRITERION_BY_NAME[x["criterion"]].group],
                                     -x["weight"] * x["closeness"]))
        conf = ranking.platform_confidence(platform, f)
        vms[c] = {
            "id": c, "platform": platform, "name": creators.get(c, {}).get("name") or a.get("handle") or c,
            "handle": a.get("handle"), "url": a.get("url"), "avatar": raw.get("avatar"),
            "rank": int(res.loc[c, "rank"]), "score": round(100 * float(res.loc[c, "score"])),
            "conf": conf, "conf_label": ranking.confidence_label(conf),
            "followers": f.get(f"{pre}_followers"), "features": f,
            "sub": raw.get("game") if platform == "twitch" else (raw.get("biography") or "")[:80],
            "summary": json.loads(stored.get(c, {}).get("breakdown_json") or "{}").get("summary", ""),
            "quotes": quotes.get(c, []),
            "reasons": [REASON[x["criterion"]][0].format(lang=lang) for x in good if x["criterion"] in REASON][:3],
            "gaps": [REASON[x["criterion"]][1].format(lang=lang) for x in ex["gaps"][:3]
                     if x["closeness"] <= 0.35 and x["criterion"] in REASON],
            "youtube": a.get("creator_id") if str(a.get("creator_id") or "").startswith("yt:") else None,
        }
    for v in vms.values():
        v["tier"] = ui.tier_of(v["rank"], len(vms))
    return {"vms": vms, "passed": passed, "feats": feats, "used": used}


# ------------------------------------------------------------------ chrome
def top_nav(n_short: int, campaign: dict | None) -> None:
    """Logo left; the product's global sections right (Shortlists, Sponsorships, plus the open search's shortlist);
    below, one back link to the parent level. The current level is the page title, so nothing is shown twice."""
    view = ss.view
    inside = campaign is not None and view in ("discover", "analysis", "shortlist")
    left, right = st.columns([3.6, 2.8], vertical_alignment="center")
    with left:
        html(ui.brand_header())
    with right, st.container(horizontal=True, horizontal_alignment="right", gap="small"):
        with st.container(key="cart-on-sls" if view == "shortlists" else "cart-sls"):
            st.button("Shortlists", icon=":material/bookmarks:", key="btn_shortlists", on_click=go,
                      args=("shortlists",))
        with st.container(key="cart-on-sp" if view == "sponsorships" else "cart-sp"):
            st.button("Sponsorships", icon=":material/handshake:", key="btn_sponsorships", on_click=open_sponsorships)
        if inside:  # this search's shortlist
            with st.container(key=(f"cart-on-{n_short}" if view == "shortlist" else f"cart-{n_short}")):
                st.button(f"Shortlist ({n_short})" if n_short else "Shortlist", icon=":material/bookmark:",
                          key="btn_cart", on_click=go, args=("shortlist",))
    home = lambda: st.button("Home", key="crumb_home", type="tertiary", icon=":material/arrow_back:", on_click=go,
                             args=("campaign",))
    if view in ("shortlists", "sponsorships"):
        with st.container(key="back"):
            home()
    elif inside:
        with st.container(key="back"):
            if view == "shortlist" and ss.get("sl_from") == "shortlists":
                st.button("Shortlists", key="crumb_shortlists", type="tertiary", icon=":material/arrow_back:",
                          on_click=go, args=("shortlists",))
            elif view in ("analysis", "shortlist"):
                spec = CampaignSpec.model_validate_json(campaign["spec_json"])
                label = f"{spec.product} · {spec.target_country}"
                st.button(label, key="crumb_campaign", type="tertiary", icon=":material/arrow_back:",
                          on_click=go, args=("discover",), help=f"Back to {label}")
            else:
                home()


def band_title(title: str, chips: str = "", meta: str = "") -> None:
    """Current page inside the green band: one title, then chips and counts on a single line."""
    html(f'<div class="pn-band-title" role="heading" aria-level="1">{esc(title[:1].upper() + title[1:])}</div>'
         f'<div class="pn-band-meta">{chips}<span class="pn-subtle">{esc(meta)}</span></div>')


# ------------------------------------------------------------------ campaign page
def stage_index(msg: str) -> int:
    m = msg.lower()
    if m.startswith("parsing"):
        return 0
    if m.startswith("youtube search") or m.startswith("loading"):
        return 1
    if m.startswith("web search") or m.startswith("merging") or m.startswith("twitch"):
        return 2
    if m.startswith("cheap filter") or m.startswith("screening"):
        return 3
    if m.startswith("deep analysis"):
        return 4
    return 5


def run_with_stages(spec: CampaignSpec, brief: str, seeds: list[str]) -> None:
    box = st.empty()
    state = {"i": 0}

    def cb(msg: str, _frac: float) -> None:
        state["i"] = max(state["i"], stage_index(msg))
        detail = msg.split(":", 1)[-1].strip() if ":" in msg else msg
        box.markdown(ui.boot_log(STAGES, state["i"], detail[:60]), unsafe_allow_html=True)

    box.markdown(ui.boot_log(STAGES, 0, "Preparing"), unsafe_allow_html=True)
    cid = pipeline.run_campaign(brief, spec=spec, seed_handles=seeds, progress=cb)
    ss.campaign, ss.view, ss.creator = cid, "discover", None
    ss.pop("draft", None)
    st.rerun()  # the run itself was the page render; one rerun shows the results


def _submit_brief() -> None:
    brief = ss.get("campaign_brief_v2", "").strip()
    if brief:
        ss.parse_request = brief
    else:
        ss.brief_error = True


def _discard_draft() -> None:
    ss.pop("draft", None)


def _leave_new_search() -> None:
    ss.pop("draft", None)
    ss.view = "campaign"


def _submit_settings() -> None:
    """Form callback: runs before the next script run, so ONE click creates the campaign and starts discovery."""
    k = f"s{ss.draft_n}_"
    g = lambda name: ss[k + name]
    lines = lambda name: [x.strip() for x in g(name).splitlines() if x.strip()]
    commas = lambda name: [x.strip() for x in g(name).split(",") if x.strip()]
    draft: CampaignSpec = ss.draft
    sources = dict(draft.field_sources)
    for name, old in [("min_subscribers", draft.min_subscribers), ("max_subscribers", draft.max_subscribers),
                      ("price_segment", draft.price_segment), ("n_creators", draft.n_creators)]:
        if g(name) != old:
            sources[name] = "user"
    spec = CampaignSpec(
        product=g("product"), niche=g("niche"), price_segment=g("price_segment"),
        target_country=g("country").strip().upper(), target_language=g("lang").strip().lower(), goal=g("goal"),
        audience=g("audience"), audience_interests=commas("interests"), product_keywords=commas("keywords"),
        search_queries=lines("yt_q"), creator_queries=lines("cr_q"), web_queries=lines("web_q"),
        current_topics=draft.current_topics, topics_source=draft.topics_source, strategy_notes=draft.strategy_notes, min_subscribers=int(g("min_subscribers")),
        max_subscribers=int(g("max_subscribers")), n_creators=int(g("n_creators")),
        budget_per_video=int(g("budget") or 0), field_sources=sources)
    ss.run_request = (spec, ss.get("draft_brief", DEFAULT_BRIEF), commas("seeds"))


def cost_estimate(n: int) -> str:
    screen = min(max(config.N_AFTER_CHEAP_FILTER, 2 * n), config.MAX_SCREENED)
    yt = 100 * (config.N_QUERIES + pipeline.n_creator_searches(config.N_QUERIES)) + 2 * screen + 5 * n
    ai = 2 + screen + 6 * n
    return f"≈ {yt:,} YouTube units (of 10,000/day), {ai} AI calls"


def campaign_page(campaigns: list[dict]) -> None:
    running = ss.pop("run_request", None)
    parse = ss.pop("parse_request", None)
    # parse first, so the page below already reflects the result (settings on success, the brief box on failure)
    parse_error = None
    if parse:
        with st.spinner("Reading the brief and researching what this audience follows right now (about a minute)…"):
            try:
                ss.draft = pipeline.parse_brief(LLMClient(config.LLM_MODELS), parse)
                ss.draft_brief = parse
                ss.draft_n = ss.get("draft_n", 0) + 1  # fresh widget keys for every new draft
            except Exception as e:
                miss = missing_keys(config.LLM_MODELS)
                parse_error = f"Could not read the brief: {e}" + (f" (missing: {', '.join(miss)})" if miss else "")
        parse = None
    # Home is an operational dashboard; a new search is its own 3-step flow with one task per page
    if ss.view == "campaign" and not running and not parse:
        home_page(campaigns)
        return
    has_draft = ss.get("draft") is not None
    title = ("Finding creators…" if running else "Reading your brief…" if parse
             else "Confirm campaign details" if has_draft else "New creator search")
    meta = ("Step 3 of 3 · this usually takes a few minutes" if running
            else "Step 2 of 3 · review what we understood, then create the campaign" if has_draft
            else "Step 1 of 3 · describe the campaign; we research the audience and plan the search")
    with band:
        st.button("Home", key="crumb_new", type="tertiary", icon=":material/arrow_back:",
                  on_click=_leave_new_search, disabled=bool(running or parse))
        band_title(title, meta=meta)
    form = st.container(key="stepwrap")
    with form:
        with st.container(key="panel-campaign"):
            if parse_error:
                st.error(parse_error)
            if running:
                html(ui.steps(2) + '<div class="pn-kicker">Finding creators…</div>')
                try:
                    run_with_stages(*running)
                except Exception as e:
                    st.error(f"Discovery failed: {e}")
                return
            if ss.get("draft") is None:
                html(ui.steps(0) + '<div class="pn-kicker">Campaign brief</div>')
                with st.form("brief_form", border=False):
                    st.text_area("Campaign brief", value="", height=150, placeholder="Describe your campaign…",
                                 label_visibility="collapsed", key="campaign_brief_v2")
                    if ss.pop("brief_error", False):
                        st.warning("Describe your campaign before continuing.")
                    html(f'<div class="pn-subtle" style="margin:-4px 0 12px">{esc(BRIEF_EXAMPLES)}</div>')
                    st.form_submit_button("Continue →", type="primary", width="stretch",
                                          on_click=_submit_brief)
            else:
                html(ui.steps(1))
                campaign_form(ss.draft)


def campaign_form(draft: CampaignSpec) -> None:
    k = f"s{ss.draft_n}_"
    src = draft.field_sources
    tag = lambda name: {"brief": " · from brief", "default": " · default", "user": ""}.get(src.get(name, ""), "")
    with st.form("settings_form", border=False):
        html('<div class="pn-kicker">Campaign settings</div>')
        st.text_input("Product", draft.product, key=k + "product")
        a, b, c = st.columns(3)
        a.text_input("Market (country code)", draft.target_country, key=k + "country",
                     help=COUNTRY.get(draft.target_country, ""))
        b.text_input("Language", draft.target_language, key=k + "lang")
        c.text_input("Price range" + tag("price_segment"), draft.price_segment, key=k + "price_segment",
                     placeholder="not specified")
        st.text_input("Target audience", draft.audience, key=k + "audience")
        st.text_input("Audience interests (what they watch)" + tag("audience_interests"),
                      ", ".join(draft.audience_interests), key=k + "interests")
        st.radio("Campaign priority", list(PRIORITY), index=list(PRIORITY).index(draft.goal), key=k + "goal",
                 format_func=lambda p: PRIORITY[p][0], captions=[PRIORITY[p][1] for p in PRIORITY],
                 horizontal=True, help=PRIORITY_HELP)
        b, c, d = st.columns(3)
        b.number_input("Min subscribers" + tag("min_subscribers"), 0, 100_000_000, draft.min_subscribers,
                       step=1000, key=k + "min_subscribers")
        c.number_input("Max subscribers" + tag("max_subscribers"), 0, 100_000_000, draft.max_subscribers,
                       step=100_000, key=k + "max_subscribers")
        d.number_input(BUDGET_LABEL, 0, 10_000_000, draft.budget_per_video, step=500, key=k + "budget",
                       help=BUDGET_HELP)
        st.slider("Creators to analyze", 1, config.MAX_N_CREATORS, min(max(draft.n_creators, 1), config.MAX_N_CREATORS),
                  step=1, key=k + "n_creators", help="How many creators are analysed in depth (videos, comments, "
                  "evidence). More creators take longer and use more API quota.")
        html(f'<div class="pn-subtle" style="margin:-6px 0 8px">10 creators {esc(cost_estimate(10))} · '
             f'50 creators {esc(cost_estimate(50))}</div>')
        if draft.strategy_notes:  # research / planning fell back: say so before anything is created
            st.warning("**The search strategy is incomplete.** " + " ".join(draft.strategy_notes)
                       + " Check the searches under Search setup.", icon=":material/warning:")
        with st.expander("Search setup", expanded=bool(draft.strategy_notes)):
            st.text_input("Niche", draft.niche, key=k + "niche")
            st.text_input("Product keywords", ", ".join(draft.product_keywords), key=k + "keywords")
            if draft.current_topics:
                html('<div class="pn-subtle" style="margin-bottom:6px">What this audience follows now '
                     f'({esc(draft.topics_source)}): '
                     + esc(" · ".join(t.name for t in draft.current_topics)) + "</div>")
            else:
                html('<div class="pn-subtle" style="margin-bottom:6px">No current topics were found; the searches '
                     'below are based on model knowledge.</div>')
            st.text_area("Topic & content searches, YouTube (one per line)", "\n".join(draft.search_queries),
                         height=130, key=k + "yt_q", help="Main path: videos about what the audience follows now; "
                         "the channels behind them become candidates.")
            if config.N_QUERIES < len(draft.search_queries):
                html(f'<div class="pn-subtle" style="margin:-6px 0 8px">Only the first {config.N_QUERIES} of these '
                     f'run (N_QUERIES={config.N_QUERIES} in .env limits YouTube quota use).</div>')
            st.text_area("Direct creator searches, YouTube (one per line)", "\n".join(draft.creator_queries),
                         height=70, key=k + "cr_q", help="Secondary path: 'best / top creators' style searches.")
            st.text_area("Web & social searches (one per line)", "\n".join(draft.web_queries), height=130,
                         key=k + "web_q")
            st.text_input("Must-include channels (@handles, comma-separated)", "", key=k + "seeds")
        missing = sorted(set(missing_keys(config.LLM_MODELS) + missing_keys(config.VLM_MODELS)
                             + ([] if web.provider() else ["AZURE_OPENAI_API_KEY, OPENAI_API_KEY or TAVILY_API_KEY"])
                             + ([] if config.YOUTUBE_API_KEY else ["YOUTUBE_API_KEY"])))
        if missing:
            html(f'<div class="pn-note">Missing keys (skipped): {esc(", ".join(missing))}</div>')
        back, go_btn = st.columns([1, 2])
        back.form_submit_button("← Edit brief", width="stretch", on_click=_discard_draft)
        go_btn.form_submit_button("Create campaign →", type="primary", width="stretch",
                                  on_click=_submit_settings)


def rel_time(iso: str) -> str:
    """'Today 14:26', 'Yesterday 09:10', '3 days ago', or the date for older reports."""
    from datetime import datetime, timezone
    try:
        t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return iso[:16]
    days = (datetime.now(timezone.utc).date() - t.date()).days
    if days == 0:
        return f"Today {t:%H:%M}"
    if days == 1:
        return f"Yesterday {t:%H:%M}"
    return f"{days} days ago" if days < 7 else f"{t:%d %b %Y}"


def home_page(campaigns: list[dict]) -> None:
    """Operational home: start a search, jump into Shortlists / Sponsorships (live counts), continue recent work."""
    with band:
        top, cta = st.columns([4, 1.4], vertical_alignment="bottom")
        with top:
            band_title("Creator Intelligence", meta="Find, evaluate and manage creator partnerships.")
        with cta, st.container(key="home-cta"):
            st.button("New creator search", icon=":material/add:", type="primary", key="btn_new_search",
                      on_click=new_search, width="stretch")
    groups = rel.shortlist_groups()
    n_sl, n_fav = sum(g["n"] for g in groups.values()), sum(g["favorites"] for g in groups.values())
    tot = rel.summary(rel.sponsorships())
    sl_col, sp_col = st.columns(2, gap="medium")
    with sl_col, st.container(key="panel-home-sl"):
        html('<div class="pn-kicker">Shortlists</div><div class="pn-muted">Review creators saved from previous '
             'searches.</div>')
        if n_sl:
            html(f'<div class="pn-home-num"><b>{n_sl}</b> creator{"s" if n_sl != 1 else ""} · <b>{len(groups)}</b> '
                 f'search{"es" if len(groups) != 1 else ""}{f" · ★ {n_fav}" if n_fav else ""}</div>')
            st.button("Open shortlists", key="home_sl", on_click=go, args=("shortlists",), icon=":material/bookmarks:")
        else:
            html('<div class="pn-home-num pn-home-empty">No shortlisted creators yet</div>')
            st.button("Find creators", key="home_sl_find", on_click=new_search, icon=":material/search:")
    with sp_col, st.container(key="panel-home-sp"):
        html('<div class="pn-kicker">Sponsorships</div><div class="pn-muted">Track active partnerships and what '
             'they delivered.</div>')
        if tot["active"] or tot["completed"]:
            money_line = " · ".join(x for x in [f"{money(tot['spend'])} spent" if tot["spend"] is not None else "",
                                                 f"{money(tot['revenue'])} tracked revenue"
                                                 if tot["revenue"] is not None else ""] if x)
            html(f'<div class="pn-home-num"><b>{tot["active"]}</b> in progress · <b>{tot["completed"]}</b> done'
                 f'{" · " + money_line if money_line else ""}</div>')
            st.button("Open sponsorships", key="home_sp", on_click=open_sponsorships, icon=":material/handshake:")
        else:
            html('<div class="pn-home-num pn-home-empty">No sponsorships yet</div>')
            st.button("View shortlists", key="home_sp_sl", on_click=go, args=("shortlists",),
                      icon=":material/bookmarks:", disabled=not n_sl)
    st.write("")
    recent_campaigns(campaigns)


def shortlists_page(campaigns: list[dict]) -> None:
    """Every search's shortlist in one place, grouped by the search that produced it (existing shortlist table)."""
    groups = rel.shortlist_groups()
    by_id = {c["id"]: c for c in campaigns}
    groups = {k: g for k, g in groups.items() if k in by_id}
    n = sum(g["n"] for g in groups.values())
    with band:
        band_title("Shortlists", meta=(f"{n} creator{'s' if n != 1 else ''} across {len(groups)} "
                                       f"search{'es' if len(groups) != 1 else ''}") if n else "")
    if not groups:
        ui.empty_state("No shortlisted creators yet", "Discover creators and add promising candidates to your "
                                                      "shortlist.")
        st.button("Find creators", key="sls_find", type="primary", on_click=new_search, icon=":material/search:")
        return
    f1, f2 = st.columns([1.6, 3], vertical_alignment="bottom")
    mode = f1.segmented_control("View", ["By search", "All creators"], required=True, label_visibility="collapsed",
                                **keep("sls_mode", "By search"))
    query = f2.text_input("Search", placeholder="Search searches or creators", label_visibility="collapsed",
                          **keep("sls_query", ""))
    q = query.lower().strip()
    order = [c["id"] for c in campaigns if c["id"] in groups]  # newest search first
    specs = {cid: CampaignSpec.model_validate_json(by_id[cid]["spec_json"]) for cid in order}
    names = {r["id"]: r["name"] for r in db.query("SELECT id, name FROM creators")}

    def label_of(camp: str, cid: str) -> str:
        if cid.startswith(tts.PREFIX):
            L = tts.lead(camp, cid[len(tts.PREFIX):]) or {}
            return L.get("name") or "@" + cid[len(tts.PREFIX):]
        return names.get(cid, cid)

    st.write("")
    if mode == "All creators":
        scores = {(r["campaign_id"], r["creator_id"]): r["topsis_score"] for r in
                  db.query("SELECT campaign_id, creator_id, topsis_score FROM rankings")}
        status = {(r["campaign_id"], r["creator_id"]): rel.STATUSES[r["status"]]
                  for r in reversed(rel.sponsorships())}
        rows = []
        for camp in order:
            for e in rel.shortlist(camp):
                cid, sc = e["creator_id"], scores.get((camp, e["creator_id"]))
                rows.append({"★": "★" if e["favorite"] else "", "Creator": label_of(camp, cid),
                             "Platform": PLATFORMS.get(rel.platform_of(cid), ""), "Search": specs[camp].product,
                             "Market": specs[camp].target_country,
                             "Score in this search": None if sc is None else round(100 * sc),
                             "Sponsorship": status.get((camp, cid), "")})
        if q:
            rows = [r for r in rows if q in r["Creator"].lower() or q in r["Search"].lower()]
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                     column_config={"Score in this search": st.column_config.NumberColumn(
                         format="%d", help="Campaign score from that search's ranking (search-specific; TikTok "
                                           "leads have none)")})
        st.caption("A creator shortlisted in two searches appears twice: each search keeps its own score and state.")
        return
    shown = [c for c in order if not q or q in specs[c].product.lower() or q in (by_id[c]["brief"] or "").lower()
             or any(q in label_of(c, x).lower() for x in groups[c]["creators"])]
    if not shown:
        ui.empty_state("No shortlists match this search.", "Clear the search to see all.")
    for i in range(0, len(shown), 2):
        for col, camp in zip(st.columns(2, gap="medium"), shown[i:i + 2]):
            g, spec = groups[camp], specs[camp]
            with col, st.container(key=f"card-slg-{ui.key(camp)}"):
                icons = " ".join(ui.platform_icon(p, 14) for p in g["platforms"])
                fav = f" · ★ {g['favorites']}" if g["favorites"] else ""
                who = ", ".join(label_of(camp, x) for x in g["creators"][:4])
                more = f" +{g['n'] - 4} more" if g["n"] > 4 else ""
                html(f'<div class="pn-rank">{esc(rel_time(by_id[camp]["created_at"]))} · '
                     f'{esc(COUNTRY.get(spec.target_country, spec.target_country))} · {esc(PRIORITY[spec.goal][0])}</div>'
                     f'<div class="pn-group-name">{esc(spec.product)}</div>'
                     f'<div class="pn-home-num"><b>{g["n"]}</b> shortlisted{fav}<span class="pn-group-icons">{icons}'
                     f'</span></div><div class="pn-subtle" style="margin:4px 0 10px">{esc(who)}{more}</div>')
                st.button("Open shortlist", key=f"open_sl_{camp}", on_click=open_shortlist, args=(camp,),
                          icon=":material/arrow_forward:", icon_position="right")


def recent_campaigns(campaigns: list[dict], n_cols: int = 3, overlap: bool = False) -> None:
    """Report history as a compact list: identity, key settings, size, age; open and delete per row."""
    if not campaigns:
        st.write("")
        ui.empty_state("No campaigns yet.", "Describe a campaign above to find creators.")
        return
    counts = {c["id"]: db.query("SELECT COUNT(*) n FROM rankings WHERE campaign_id=? AND passed_hard_filter=1",
                                (c["id"],))[0]["n"] for c in campaigns}
    empty = [c for c in campaigns if not counts[c["id"]]]
    shown = campaigns if ss.get("show_empty_reports") else [c for c in campaigns if counts[c["id"]]]
    # only the search-style home lifts the card over the band; elsewhere it must not cover the form above
    home = st.container(key="homecard" if overlap else "histcard")
    home.markdown(f'<div class="pn-list-head"><span class="t">Recent searches</span>'
                  f'<span class="n">{len(shown)} search{"es" if len(shown) != 1 else ""}</span></div>',
                  unsafe_allow_html=True)
    groups = rel.shortlist_groups()
    sp_groups = rel.sponsorship_groups(rel.sponsorships())
    with home, st.container(key="reportlist"):
        for c in shown:
            spec = CampaignSpec.model_validate_json(c["spec_json"])
            n = counts[c["id"]]
            brief = " ".join((c.get("brief") or "").split())
            brief = brief if len(brief) <= 90 else brief[:87] + "…"
            specs = " · ".join(x for x in [spec.target_country, spec.price_segment, PRIORITY[spec.goal][0]] if x)
            with st.container(key=f"row-camp-{ui.key(c['id'])}", horizontal=True, vertical_alignment="center",
                              gap="small"):
                html(f'<div class="pn-row-main"><div class="nm">{esc(spec.product)}</div>'
                     f'<div class="br">{esc(brief) if brief else "&nbsp;"}</div></div>')
                html(f'<div class="pn-row-spec">{esc(specs)}</div>')
                g, sg = groups.get(c["id"], {}), sp_groups.get(c["id"], {})
                sponsored = (f'<b>{sg["active"] + sg["completed"]}</b> sponsored' if sg else
                             '<span class="pn-row-none">no sponsorships</span>')
                html(f'<div class="pn-row-num" title="Creators the ranking found"><b>{n}</b> found</div>')
                html(f'<div class="pn-row-num" title="Creators you shortlisted"><b>{g.get("n", 0)}</b> shortlisted</div>')
                html(f'<div class="pn-row-num pn-row-sp" title="Sponsorships started from this search">{sponsored}</div>')
                html(f'<div class="pn-row-time">{esc(rel_time(c["created_at"]))}</div>')
                st.button("Open", key=f"open_{c['id']}", type="tertiary", icon=":material/arrow_forward:",
                          icon_position="right", on_click=lambda i=c["id"]: (
                              ss.update(campaign=i, view="discover", creator=None)))
                if st.button("", key=f"delete_{c['id']}", type="tertiary", icon=":material/delete:",
                             help="Delete report"):
                    confirm_delete(c["id"], spec.product)
    if empty:
        with home:
            st.toggle(f"Show reports without results ({len(empty)})", key="show_empty_reports")


@st.dialog("Delete this report?")
def confirm_delete(campaign_id: str, product: str) -> None:
    st.write(f'This will permanently remove the saved report for “{product}”.')
    st.caption("Other reports, their creator data and all sponsorship records will be kept.")
    cancel, delete = st.columns(2)
    if cancel.button("Cancel", width="stretch"):
        st.rerun()
    if delete.button("Delete", type="primary", width="stretch"):
        db.delete_campaign(campaign_id)
        if ss.get("campaign") == campaign_id:
            ss.campaign, ss.creator, ss.view = None, None, "campaign"
        st.rerun()


# ------------------------------------------------------------------ discover page
def creator_card(vm: dict, ctx: dict, shortlisted: bool) -> None:
    """Commerce-style card: score, confidence, three key figures, one reason. Details live on the analysis page
    and side-by-side numbers in Compare."""
    with st.container(key=f"card-cr-{ui.key(vm['id'])}"):
        badges = (ui.confidence_badge(vm["conf"], vm["conf_label"], basis=vm["basis"])
                  + (" " + ui.gem_badge() if vm["gem"] else "") + (" " + ui.budget_badge() if vm["over_budget"] else ""))
        if (h := rel.history(vm["id"])).any:
            badges += " " + ui.history_badge(h.sponsorships, h.positive, h.negative)
        loc = COUNTRY.get(vm["country"], vm["country"]) if vm["country"] else ""
        stats = "".join([
            ui.stat_tile("Subscribers", ui.fmt_count(vm["subs"])),
            ui.stat_tile("Median views", ui.fmt_count(vm["median_views"]), hint="Median views of relevant videos"),
            ui.stat_tile("Engagement", ui.pct1(vm["engagement"])),
            ui.stat_tile("Relevant videos", "–" if vm["n_relevant"] is None else f"{vm['n_relevant']:.0f}",
                         hint=f"{vm['n_analyzed'] or 0:.0f} analysed in depth"),
            ui.stat_tile("Purchase intent", ui.pct(vm["purchase_intent"]), hint="Share of comments showing buying intent"),
            ui.stat_tile("Cost proxy", f"€{ui.fmt_count(vm['cost'])}" if vm["cost"] else "–",
                         hint=f"Per video: median views × assumed €{config.ASSUMED_CPM_EUR:.0f} CPM, not a quote"),
        ])
        reason = ui.reasons_list(vm["reasons"][:1]) if vm["reasons"] else ""
        for plat in ("twitch", "instagram"):  # real follower totals from the official APIs, never scored
            if (n := vm["other_followers"].get(plat)) is not None:
                badges += " " + ui.platform_chip(plat, vm["others"][plat], n)
        html(f'<div class="pn-card-head">{ui.avatar(vm["name"], vm["avatar"])}<div class="who">'
             f'<div class="pn-rank" title="YouTube">#{vm["rank"]} {ui.platform_icon("youtube", 13)}'
             f'{esc(loc)}</div>'
             f'<div class="name" title="{esc(vm["name"])}">{esc(vm["name"])}</div>'
             f'<div class="meta">{esc(vm["niche"])}</div></div>'
             f'<div class="pn-scorewrap">{ui.tier_badge(vm["tier"])}{ui.score_ring(vm["score"], "Score")}</div></div>'
             f'<div style="margin:10px 0 2px">{badges}</div><div class="pn-stats three">{stats}</div>'
             f'{ui.views_sparkline(vm.get("video_views") or [])}{reason}')
        a, b = st.columns(2)
        a.button("View analysis", key=f"view_{vm['id']}", type="primary", on_click=go, args=("analysis", vm["id"]),
                 width="stretch")
        b.button("✓ Shortlisted" if shortlisted else "+ Shortlist", key=f"sl_{vm['id']}", width="stretch",
                 on_click=toggle_shortlist, args=(ctx["campaign"]["id"], vm["id"]))


def top_picks(ctx: dict) -> None:
    """One-line verdict before the grid: best fit, best reach, best value (within budget when one is set)."""
    vms = list(ctx["vms"].values())
    if len(vms) < 2:
        return
    pool = [v for v in vms if not v["over_budget"]]  # only recommend what the budget allows
    if not pool:
        html(f'<div class="pn-note">No creator fits the budget of €{ctx["budget"]:,.0f} per video. '
             'Raise it under Ranking priorities to get recommendations.</div>')
        return
    fit = max(pool, key=lambda v: (v["groups"].get("Campaign Fit") or 0, v["score"]))
    reach = max(pool, key=lambda v: v["median_views"] or 0)
    priced = [v for v in pool if v["cost"]]
    value = max(priced, key=lambda v: v["score"] / v["cost"]) if priced else None
    picks = [("Best fit", fit, f"Campaign fit {fit['groups'].get('Campaign Fit') or 0:.0f}/100 · score {fit['score']}"),
             ("Best reach", reach, f"{ui.fmt_count(reach['median_views'])} median views on relevant videos")]
    if value:
        picks.append(("Best value", value, f"Score {value['score']} for ≈ €{ui.fmt_count(value['cost'])} per video"))
    for col, (label, vm, detail) in zip(st.columns(len(picks), gap="medium"), picks):
        with col, st.container(key=f"panel-pick-{ui.key(label)}"):
            html(f'<div class="pn-pick"><div class="k">{label}</div><div class="n">{esc(vm["name"])}</div>'
                 f'<div class="d">{esc(detail)}</div></div>')
            st.button("View analysis →", key=f"pick_{ui.key(label)}", type="tertiary", on_click=go,
                      args=("analysis", vm["id"]))


def compare_table(items: list[dict], ctx: dict, short: list[str]) -> None:
    lang = ctx["lang"]
    df = pd.DataFrame([{
        "#": v["rank"], "Tier": v["tier"], "Creator": v["name"], "Score": v["score"], "Confidence": v["conf_label"],
        "Subscribers": v["subs"], "Median views": v["median_views"],
        "Engagement %": None if v["engagement"] is None else 100 * v["engagement"],
        "Relevant videos": v["n_relevant"], "Audience relevance": v["bars"]["Audience relevance"],
        "Content relevance": v["bars"]["Content relevance"], "Community": v["bars"]["Community quality"],
        "Purchase intent %": None if v["purchase_intent"] is None else 100 * v["purchase_intent"],
        f"{lang} comments %": None if v["lang_share"] is None else 100 * v["lang_share"],
        "Covers": v["niche"], "★": "✓" if v["id"] in short else ""} for v in items])
    bar = lambda: st.column_config.ProgressColumn(format="%.0f", min_value=0, max_value=100)
    st.dataframe(df, hide_index=True, width="stretch", height=min(36 * (len(df) + 1) + 4, 640),
                 column_config={
                     "Score": st.column_config.NumberColumn(format="%d"),
                     "Subscribers": st.column_config.NumberColumn(format="compact"),
                     "Median views": st.column_config.NumberColumn(format="compact"),
                     "Engagement %": st.column_config.NumberColumn(format="%.1f"),
                     "Relevant videos": st.column_config.NumberColumn(format="%d"),
                     "Audience relevance": bar(), "Content relevance": bar(), "Community": bar(),
                     "Purchase intent %": st.column_config.NumberColumn(format="%.0f"),
                     f"{lang} comments %": st.column_config.NumberColumn(format="%.0f")})
    pick = st.selectbox("Open creator", [v["id"] for v in items], index=None, placeholder="Open a creator's analysis…",
                        format_func=lambda c: f"#{ctx['vms'][c]['rank']} {ctx['vms'][c]['name']}",
                        label_visibility="collapsed")
    if pick:
        go("analysis", pick)
        st.rerun()


def weights_controls(spec: CampaignSpec, campaign_id: str) -> None:
    with st.popover("Priority & budget", width="stretch"):
        st.radio("Campaign priority", list(PRIORITY), format_func=lambda p: PRIORITY[p][0],
                 captions=[PRIORITY[p][1] for p in PRIORITY], help=PRIORITY_HELP,
                 **keep(f"preset_{campaign_id}", spec.goal))
        st.number_input(BUDGET_LABEL, 0, 10_000_000, step=500, help=BUDGET_HELP,
                        **keep(f"budget_{campaign_id}", spec.budget_per_video))
        preset = preset_of(spec, campaign_id)
        w, _ = ranking.ahp_weights(ranking.AHP_PRESETS[preset])
        st.toggle("Fine-tune weights", **keep(f"custom_{campaign_id}", False))
        for g, x in zip(ranking.GROUPS, w):
            st.slider(GROUP_LABEL[g], 0.0, 1.0, step=0.01, disabled=not ss.get(f"custom_{campaign_id}"),
                      **keep(f"w_{campaign_id}_{preset}_{g}", float(round(x, 2))))


def discover_page(ctx: dict, short: list[str]) -> None:
    spec, vms = ctx["spec"], ctx["vms"]
    analysed = len(ctx["feats"])
    with band:
        band_title(spec.product,
                   "".join(ui.chip(x) for x in [COUNTRY.get(spec.target_country, spec.target_country), ctx["lang"],
                                                 spec.price_segment,
                                                 f"Priority: {PRIORITY[preset_of(spec, ctx['campaign']['id'])][0]}"] if x),
                   f"YouTube: {len(vms)} ranked · {analysed} analysed · {analysed - len(vms)} filtered out")
    c = ctx["campaign"]["id"]  # filters survive a visit to a creator's analysis and back
    counts = {"youtube": len(vms), **{pl: len(P["vms"]) for pl, P in ctx["platforms"].items()},
              "tiktok": len(tts.leads_for(c, "suggested"))}
    platform = st.segmented_control(
        "Platform", list(PLATFORMS), required=True, label_visibility="collapsed",
        format_func=lambda pl: (f"TikTok Scout · {counts[pl]} leads" if pl == "tiktok"
                                else f"{PLATFORMS[pl]} · {counts[pl]}"), **keep(f"f_platform_{c}", "youtube"))
    if platform == "tiktok":
        tiktok_scout_page(ctx, short)
        return
    if platform != "youtube":
        platform_page(ctx, platform, short)
        return
    f1, f2, f3, f4, f5, f6 = st.columns([1.4, 1.7, 1.1, 1.3, 1.2, 1.2], vertical_alignment="bottom")
    query = f1.text_input("Search", placeholder="Search creators", label_visibility="collapsed",
                          **keep(f"f_query_{c}", ""))
    conf_filter = f2.segmented_control("Confidence", ["All", "Medium+", "High"], required=True,
                                       label_visibility="collapsed", **keep(f"f_conf_{c}", "All"))
    gems = f3.toggle("Hidden gems", **keep(f"f_gems_{c}", False))
    sort = f4.selectbox("Sort", ["Campaign score", "Audience relevance", "Median views", "Engagement",
                                 "Subscribers", "Data confidence"], label_visibility="collapsed",
                        **keep(f"f_sort_{c}", "Campaign score"))
    layout = f5.segmented_control("View", ["Cards", "Compare"], required=True, label_visibility="collapsed",
                                  **keep(f"f_layout_{c}", "Cards"))
    with f6:
        weights_controls(spec, ctx["campaign"]["id"])

    items = sorted(vms.values(), key=lambda v: v["rank"])
    if query:
        items = [v for v in items if query.lower() in v["name"].lower()]
    if conf_filter == "High":
        items = [v for v in items if v["conf_label"] == "High"]
    elif conf_filter == "Medium+":
        items = [v for v in items if v["conf_label"] in ("High", "Medium")]
    if gems:
        items = [v for v in items if v["gem"]]
    key_fn = {"Audience relevance": lambda v: -(v["bars"]["Audience relevance"] or 0),
              "Median views": lambda v: -(v["median_views"] or 0), "Engagement": lambda v: -(v["engagement"] or 0),
              "Subscribers": lambda v: -(v["subs"] or 0), "Data confidence": lambda v: -v["conf"]}.get(sort)
    if key_fn:
        items.sort(key=key_fn)
    items.sort(key=lambda v: v["over_budget"])  # stable: keeps the chosen order, over-budget creators last

    st.write("")
    if not vms:
        ui.empty_state("No strong creator matches found.",
                       "Try a wider subscriber range, more creators, or broader audience interests.")
    elif not items:
        ui.empty_state("No creators match these filters.", "Clear the search or loosen the confidence filter.")
    elif layout == "Compare":
        compare_table(items, ctx, short)
    else:
        top_picks(ctx)
        st.write("")
        for i in range(0, len(items), 3):
            cols = st.columns(3, gap="medium")
            for col, vm in zip(cols, items[i:i + 3]):
                with col:
                    creator_card(vm, ctx, vm["id"] in short)
    method_section(ctx)


PLATFORM_STATS = {
    "twitch": [("Followers", lambda v: ui.fmt_count(v["followers"])),
               ("Median VOD views", lambda v: ui.fmt_count(v["features"].get("tw_median_vod_views"))),
               ("Streams · 30 days", lambda v: ui.fmt_count(v["features"].get("tw_streams_30d")))],
    "instagram": [("Followers", lambda v: ui.fmt_count(v["followers"])),
                  ("Engagement", lambda v: ui.pct1(v["features"].get("ig_engagement_rate"))),
                  ("Posts · 30 days", lambda v: ui.fmt_count(v["features"].get("ig_posts_30d")))],
}
EMPTY_HINT = {
    "twitch": "Twitch candidates are streamers live in the target language during the run, plus Twitch accounts "
              "linked from YouTube or the web. Needs TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET; reports created before "
              "the Twitch ranking have no Twitch data.",
    "instagram": "Instagram candidates are handles linked from YouTube descriptions, Twitch bios or web search "
                 "(professional accounts only). Needs INSTAGRAM_ACCESS_TOKEN; older reports have no Instagram data.",
}


def platform_card(vm: dict, ctx: dict, shortlisted: bool) -> None:
    pl, name = vm["platform"], PLATFORMS[vm["platform"]]
    with st.container(key=f"card-cr-{ui.key(vm['id'])}"):
        n = vm["features"].get("tw_n_vods" if pl == "twitch" else "ig_n_posts")
        basis = f"{int(n or 0)} {'streams' if pl == 'twitch' else 'posts'} · official API"
        badges = ui.confidence_badge(vm["conf"], vm["conf_label"], basis=basis)
        yt = ctx["vms"].get(vm["youtube"]) if vm["youtube"] else None
        if yt:
            badges += (f' <span class="pn-chip pn-pchip" title="Same creator in the YouTube ranking">'
                       f'{ui.platform_icon("youtube")}<span>YouTube #{yt["rank"]}</span></span>')
        stats = "".join(ui.stat_tile(k, fn(vm)) for k, fn in PLATFORM_STATS[pl])
        reason = ui.reasons_list(vm["reasons"][:1]) if vm["reasons"] else ""
        quote = (f'<div class="pn-subtle" style="margin-top:6px" title="Verbatim from the profile">'
                 f'“{esc(vm["quotes"][0])}”</div>' if vm["quotes"] else "")
        html(f'<div class="pn-card-head">{ui.avatar(vm["name"], vm["avatar"])}<div class="who">'
             f'<div class="pn-rank" title="{name}">#{vm["rank"]} {ui.platform_icon(pl, 13)}{name}</div>'
             f'<div class="name" title="{esc(vm["name"])}">{esc(vm["name"])}</div>'
             f'<div class="meta">{esc(vm["sub"] or ("@" + (vm["handle"] or "")))}</div></div>'
             f'<div class="pn-scorewrap">{ui.tier_badge(vm["tier"])}{ui.score_ring(vm["score"], "Score")}</div></div>'
             f'<div style="margin:10px 0 2px">{badges}</div><div class="pn-stats three">{stats}</div>{reason}{quote}')
        a, b = st.columns(2)
        if vm["url"]:
            a.link_button(f"Open on {name}", vm["url"], type="primary", width="stretch")
        b.button("✓ Shortlisted" if shortlisted else "+ Shortlist", key=f"sl_{vm['id']}", width="stretch",
                 on_click=toggle_shortlist, args=(ctx["campaign"]["id"], vm["id"]))


def platform_page(ctx: dict, platform: str, short: list[str]) -> None:
    P, name, c = ctx["platforms"][platform], PLATFORMS[platform], ctx["campaign"]["id"]
    f1, f2, f3 = st.columns([2.2, 1.6, 1.2], vertical_alignment="bottom")
    query = f1.text_input("Search", placeholder=f"Search {name} creators", label_visibility="collapsed",
                          **keep(f"f_query_{platform}_{c}", ""))
    sort = f2.selectbox("Sort", ["Campaign score", "Followers", "Data confidence"], label_visibility="collapsed",
                        **keep(f"f_sort_{platform}_{c}", "Campaign score"))
    with f3:
        weights_controls(ctx["spec"], c)
    items = sorted(P["vms"].values(), key=lambda v: v["rank"])
    if query:
        items = [v for v in items if query.lower() in v["name"].lower()]
    if sort == "Followers":
        items.sort(key=lambda v: -(v["followers"] or 0))
    elif sort == "Data confidence":
        items.sort(key=lambda v: -v["conf"])
    st.write("")
    if not P["feats"]:
        ui.empty_state(f"No {name} candidates in this report.", EMPTY_HINT[platform])
    elif not P["vms"]:
        ui.empty_state(f"No {name} creator passed the filters.", "See the excluded list below.")
    elif not items:
        ui.empty_state("No creators match this search.", "Clear the search.")
    else:
        for i in range(0, len(items), 3):
            for col, vm in zip(st.columns(3, gap="medium"), items[i:i + 3]):
                with col:
                    platform_card(vm, ctx, vm["id"] in short)
    st.write("")
    with st.expander(f"How the {name} ranking works"):
        html(f'<div class="pn-muted" style="margin-bottom:8px">Own ranking for {name}: numbers from the official '
             f'{name} API plus one AI judgement of the profile’s own text (audience relevance, with verbatim quotes). '
             'Same campaign priority and weights as YouTube; scores are relative to the other '
             f'{name} candidates and not comparable with the YouTube score.</div>')
        if P["used"]:
            html("".join(ui.metric_bar(ranking.CRITERION_BY_NAME[k].label, 100 * w, display=f"{w:.0%}")
                         for k, w in sorted(P["used"].items(), key=lambda t: -t[1])))
    excluded = [(ctx["creators"].get(k, {}).get("name", k), r) for k, (p, r) in P["passed"].items() if not p]
    if excluded:
        with st.expander(f"Excluded by filters ({len(excluded)})"):
            st.dataframe(pd.DataFrame(excluded, columns=["Creator", "Reason"]), hide_index=True, width="stretch")


# ------------------------------------------------------------------ TikTok Scout (human-in-the-loop, see tiktok_scout.py)
def _tt_add_from_form(campaign_id: str) -> None:
    url, name = ss.get("tt_url", ""), ss.get("tt_name", "")
    ref = tts.add_manual(campaign_id, url, name)
    if ref is None:
        ss.tt_error = "That isn't a TikTok creator link. Use a link like https://www.tiktok.com/@handle."
        return
    ss.tt_url, ss.tt_name = "", ""
    ss.toast = (f"Added @{ref.handle} to the shortlist", ":material/bookmark_added:")


def tiktok_lead_card(L: dict, campaign_id: str, shortlisted: bool) -> None:
    h = L["handle"]
    same = L["source_url"] == L["url"]
    host = (tts.urlparse(L["source_url"] or "").hostname or "").removeprefix("www.")
    source = ("TikTok link found by public web search" if same else
              f'<a href="{esc(L["source_url"])}" target="_blank">{esc(host)} ↗</a>')
    with st.container(key=f"card-tt-{ui.key(h)}"):
        html(f'<div class="pn-card-head"><div class="who"><div class="pn-rank">{ui.platform_icon("tiktok", 13)} '
             f'TikTok · {"potential lead" if L["origin"] != "manual" else "added by you"}</div>'
             f'<div class="name">@{esc(h)}</div></div></div>'
             f'<div class="pn-subtle" style="margin:8px 0 2px">Found through: <b>{esc(L["topic"] or "campaign research")}</b></div>'
             f'<div class="pn-subtle">Source: {source}</div>'
             + ('' if L["verified"] or L["origin"] == "manual" else
                '<div class="pn-note" style="margin-top:8px">This TikTok content could not be verified. '
                'Open TikTok to review it manually.</div>'))
        a, b = st.columns(2)
        a.link_button("Open on TikTok", L["url"], icon=":material/open_in_new:", width="stretch")
        b.button("✓ Shortlisted" if shortlisted else "+ Add to shortlist", key=f"tt_sl_{h}", width="stretch",
                 type="secondary" if shortlisted else "primary",
                 on_click=toggle_shortlist, args=(campaign_id, tts.PREFIX + h))
        if not shortlisted:
            st.button("Ignore", key=f"tt_ignore_{h}", type="tertiary", on_click=tts.set_status,
                      args=(campaign_id, h, "ignored"))


def tiktok_scout_page(ctx: dict, short: list[str]) -> None:
    c, spec = ctx["campaign"]["id"], ctx["spec"]
    head, act = st.columns([3, 1.2], vertical_alignment="center")
    head.markdown('<div class="pn-kicker">TikTok Scout</div><div class="pn-muted">A few TikTok leads from public web '
                  'research for you to review on TikTok. TikTok itself is not crawled or scored; you decide who goes '
                  'on the shortlist.</div>', unsafe_allow_html=True)
    pending = tts.leads_for(c, "suggested")
    if act.button("Find more leads" if pending else "Find TikTok leads", icon=":material/travel_explore:",
                  key="tt_find", type="secondary" if pending else "primary", width="stretch"):
        with st.spinner("Searching the public web for TikTok leads (about a minute)…"):
            leads, errors = tts.provider().find_leads(spec)
        new = tts.save_suggestions(c, leads)
        ss.tt_result = (new, errors)
        st.rerun()
    if res := ss.pop("tt_result", None):
        new, errors = res
        if errors and not new:
            st.warning("Web research could not find TikTok leads right now. " + errors[0][:160])
        else:
            st.caption(f"{new} new lead{'s' if new != 1 else ''} from public web research.")
    st.write("")
    if not pending:
        ui.empty_state("No TikTok leads yet.", "Find leads from public web research, or add a creator you already know.")
    for i in range(0, len(pending), 3):
        for col, L in zip(st.columns(3, gap="medium"), pending[i:i + 3]):
            with col:
                tiktok_lead_card(L, c, tts.PREFIX + L["handle"] in short)
    st.write("")
    with st.container(key="panel-tt-add"):
        html('<div class="pn-kicker">Add TikTok creator</div><div class="pn-subtle" style="margin-bottom:6px">'
             'Paste the profile link of a creator you already reviewed. Only the link is stored.</div>')
        u, n, go_ = st.columns([3, 1.6, 1], vertical_alignment="bottom")
        u.text_input("TikTok creator/profile URL", key="tt_url", placeholder="https://www.tiktok.com/@creator")
        n.text_input("Name (optional)", key="tt_name")
        go_.button("Add", key="tt_add", on_click=_tt_add_from_form, args=(c,), width="stretch")
        if err := ss.pop("tt_error", None):
            st.error(err)
    ignored = tts.leads_for(c, "ignored")
    if ignored:
        with st.expander(f"Ignored leads ({len(ignored)})"):
            for L in ignored:
                x, y = st.columns([4, 1], vertical_alignment="center")
                x.markdown(f"@{esc(L['handle'])} · {esc(L['topic'] or '')}")
                y.button("Restore", key=f"tt_restore_{L['handle']}", type="tertiary", on_click=tts.set_status,
                         args=(c, L["handle"], "suggested"))


def method_section(ctx: dict) -> None:
    st.write("")
    with st.expander("Ranking method & weights"):
        gw = ctx["group_w"]
        preset = preset_of(ctx["spec"], ctx["campaign"]["id"])
        _, cr = ranking.ahp_weights(ranking.AHP_PRESETS[preset])
        html('<div class="pn-muted" style="margin-bottom:8px">Scores are relative to this candidate set. '
             'Confidence is shown separately and never changes the score. '
             f'Campaign priority “{PRIORITY[preset][0]}” sets these group weights (AHP pairwise preset, '
             f'consistency ratio {cr:.3f}); TOPSIS then scores each creator against them.</div>')
        html("".join(ui.metric_bar(GROUP_LABEL[g], 100 * w, display=f"{w:.0%}") for g, w in gw.items()))
        if ctx["res"].attrs.get("dropped"):
            html('<div class="pn-note">Not used in this ranking (known for &lt; '
                 f'{config.MIN_CRITERION_COVERAGE:.0%} of candidates): '
                 + esc(", ".join(crit_label(ranking.CRITERION_BY_NAME[c]) for c in ctx["res"].attrs["dropped"])) + "</div>")
        html(f'<div class="pn-note" style="margin-top:8px">Cost proxy = median views × assumed '
             f'€{config.ASSUMED_CPM_EUR:.0f} CPM, not a creator quote. Market fit uses comment language as a '
             f'proxy for audience country.</div>')
        vms = list(ctx["vms"].values())
        if vms:
            dfc = pd.DataFrame([{"Creator": v["name"], "Rank": v["rank"], "Campaign score": v["score"],
                                 "Data confidence": round(100 * v["conf"]), "Level": v["conf_label"]} for v in vms])
            x = alt.X("Data confidence:Q", scale=alt.Scale(domain=[0, 100]))  # absolute scale: no false spread
            y = alt.Y("Campaign score:Q", scale=alt.Scale(zero=False, padding=28))
            levels = list(ui.CONFIDENCE_STYLE)
            dots = alt.Chart(dfc).mark_circle(size=110, opacity=0.9).encode(
                x=x, y=y, tooltip=["Rank", "Creator", "Campaign score", "Data confidence"],
                color=alt.Color("Level:N", title="Data confidence", legend=alt.Legend(orient="bottom"),
                                scale=alt.Scale(domain=levels, range=[ui.CONFIDENCE_STYLE[l][1] for l in levels])))
            ranks = alt.Chart(dfc).mark_text(dx=9, align="left", fontSize=11, color=T["text"]).encode(
                x=x, y=y, text="Rank:O")
            rule = dict(strokeDash=[4, 4], color=T["border_strong"])
            medians = (alt.Chart(pd.DataFrame({"Data confidence": [dfc["Data confidence"].median()]}))
                       .mark_rule(**rule).encode(x=x)
                       + alt.Chart(pd.DataFrame({"Campaign score": [dfc["Campaign score"].median()]}))
                       .mark_rule(**rule).encode(y=y))
            corner = lambda text, xv, align: alt.Chart(pd.DataFrame(
                {"Data confidence": [xv], "Campaign score": [dfc["Campaign score"].max()], "t": [text]})).mark_text(
                align=align, dy=-18, fontSize=11, fontWeight="bold", color=T["text_muted"]).encode(x=x, y=y, text="t:N")
            corners = (corner("← Verify manually", 0, "left")
                       + corner("Safe bets →", 100, "right"))
            st.caption("Dashed lines = median. Top-left: strong fit, thin data — verify manually.")
            st.altair_chart(medians + corners + dots + ranks, width="stretch")
    excluded = [(ctx["creators"].get(c, {}).get("name", c), r) for c, (p, r) in ctx["passed"].items() if not p]
    if excluded:
        with st.expander(f"Excluded by hard filters ({len(excluded)})"):
            st.dataframe(pd.DataFrame(excluded, columns=["Creator", "Reason"]), hide_index=True,
                         width="stretch")
    d = ctx["discoveries"]
    if not d.empty and d.creator_key.str.startswith("web:").any():
        wo = d[d.creator_key.str.startswith("web:")]
        ig_acc = {r["handle"].lower(): r for r in db.query(
            "SELECT * FROM platform_accounts WHERE platform='instagram' AND followers IS NOT NULL")}
        ig_handle = wo[wo.platform == "instagram"].groupby("creator_key")["handle"].first()
        verified = {k: ig_acc[str(h).lower()] for k, h in ig_handle.items() if str(h).lower() in ig_acc}
        rest = wo[~wo.creator_key.isin(verified)]
        if not rest.empty:
            g = rest.groupby("creator_key").agg(
                Creator=("handle", "first"), Platforms=("platform", lambda x: ", ".join(sorted(set(x)))),
                Mentions=("url", "count"), Evidence=("evidence", "first"), Source=("url", "first"))
            with st.expander(f"Found on web & social, no YouTube match ({len(g)}) — not ranked"):
                st.caption("No verified metrics available — review manually. Creators with a verified Instagram "
                           "account are ranked in the Instagram tab.")
                st.dataframe(g.sort_values("Mentions", ascending=False), hide_index=True, width="stretch",
                             column_config={"Source": st.column_config.LinkColumn()})
    if ctx["run_stats"]:
        with st.expander("Run statistics (external API usage)"):
            st.json(ctx["run_stats"])


# ------------------------------------------------------------------ creator analysis page
def community_block(comments: pd.DataFrame, f: dict, lang: str) -> None:
    html('<div class="pn-kicker">Community intelligence</div>')
    if comments.empty:
        ui.empty_state("No comments analysed.", "Comments may be disabled; this lowers data confidence.")
        return
    labeled = comments[comments.label.notna()]
    n = max(len(labeled), 1)
    share = lambda *ls: labeled.label.isin(ls).sum() / n
    rows = [("Substantive discussion", f.get("meaningful_ratio")), ("In-depth / expert discussion", share("technical")),
            ("Questions", share("question")), ("Purchase intent", share("purchase_intent")),
            ("Generic comments", share("generic")), ("Suspicious / spam", share("spam")),
            ("Creator response rate", f.get("creator_reply_rate")), (f"{lang}-language comments", f.get("target_lang_share"))]
    html("".join(ui.metric_bar(l, None if v is None else 100 * v, accent=l in ("Purchase intent",),
                               display=ui.pct(v)) for l, v in rows))
    html(f'<div class="pn-subtle">{len(labeled)} comments classified from the analysed videos.</div>')


def analysis_page(ctx: dict, cid: str, short: list[str]) -> None:
    vm = ctx["vms"].get(cid)
    if vm is None:
        ui.empty_state("Creator not found in this ranking.", "Go back to the creator list.")
        return
    f, res = vm["features"], ctx["res"]
    campaign_id = ctx["campaign"]["id"]

    # header
    with st.container(key="panel-head"):
        left, mid, right = st.columns([3.2, 1.2, 1.4], vertical_alignment="center")
        with left:
            loc = COUNTRY.get(vm["country"], vm["country"]) if vm["country"] else ""
            links = "".join(ui.platform_chip(p, h, vm["other_followers"].get(p)) for p, h in vm["others"].items())
            name = f'<a href="{esc(vm["url"])}" target="_blank" style="text-decoration:none">{esc(vm["name"])}</a>' \
                if vm["url"] else esc(vm["name"])
            html(f'<div class="pn-card-head">{ui.avatar(vm["name"], vm["avatar"], large=True)}<div class="who">'
                 f'<div class="pn-rank">#{vm["rank"]} OF {len(ctx["vms"])}</div>'
                 f'<div style="font-size:1.9rem;font-weight:800;letter-spacing:-.03em">{name}</div>'
                 f'<div class="meta">{ui.platform_icon("youtube")} '
                 f'{" · ".join(esc(x) for x in ["YouTube", loc, vm["niche"]] if x)}</div>'
                 f'<div style="margin-top:8px">{ui.confidence_badge(vm["conf"], vm["conf_label"], basis=vm["basis"])}'
                 f'{" " + ui.gem_badge() if vm["gem"] else ""}'
                 f'{" " + ui.budget_badge() if vm["over_budget"] else ""}</div>'
                 f'<div style="margin-top:8px">{links}</div></div></div>')
        with mid:
            html(f'<div class="pn-scorewrap" style="justify-content:flex-end">{ui.tier_badge(vm["tier"], large=True)}'
                 f'{ui.score_block(vm["score"], "Campaign score", xl=True)}</div>')
        with right:
            html('<div class="pn-stats" style="grid-template-columns:1fr;margin:0">'
                 + ui.stat_tile("Subscribers", ui.fmt_count(vm["subs"]), ico="users")
                 + ui.stat_tile("Median views · engagement",
                                f"{ui.fmt_count(vm['median_views'])} · {ui.pct1(vm['engagement'])}", ico="eye")
                 + ui.stat_tile("Cost proxy (assumed CPM)", f"€{vm['cost']:,.0f}" if vm["cost"] else "–", ico="euro")
                 + "</div>")
            sl = cid in short
            st.button("✓ Shortlisted" if sl else "+ Add to shortlist", type="secondary" if sl else "primary",
                      on_click=toggle_shortlist, args=(campaign_id, cid), width="stretch", key="sl_detail")
    conf_line = (f"Based on {int(f.get('n_analyzed_videos', 0))} relevant videos · "
                 f"{int(f.get('n_classified_comments', 0))} comments · transcripts "
                 f"{f.get('transcript_coverage', 0):.0%} · {int(f.get('n_visual_images', 0))} thumbnails")
    html(f'<div class="pn-subtle" style="margin:8px 2px 18px">{esc(conf_line)}</div>')
    if (h := rel.history(cid)).any:  # actual Prenew experience, next to (never inside) the predicted score
        with st.container(key="panel-relationship"):
            html('<div class="pn-kicker">Prenew relationship</div><div class="pn-subtle" style="margin-bottom:6px">'
                 'What happened when Prenew worked with this creator. Separate from the campaign score above.</div>'
                 '<div class="pn-stats three">'
                 + "".join(ui.stat_tile(a, b) for a, b in [
                     ("Sponsorships", str(h.sponsorships)), ("Completed", str(h.completed)),
                     ("In progress", str(h.in_progress)), ("Total spend", money(h.total_spend)),
                     ("Total revenue", money(h.total_revenue)),
                     ("Historical ROAS", "–" if h.roas is None else f"{h.roas:.1f}×")])
                 + f'</div><div class="pn-subtle">Historical feedback: 👍 {h.positive} · 👎 {h.negative}</div>')
        st.write("")

    # why
    why, summary = st.columns([1.1, 1], gap="large")
    with why:
        with st.container(key="panel-why"):
            rows = [(label, vm["bars"].get(label)) for label, _, _ in CARD_BARS + [EVIDENCE_BAR]]
            vs_cands = [("Performance vs. candidates", vm["groups"].get("Reach & Performance")),
                   ("Cost & risk vs. candidates", vm["groups"].get("Cost & Risk"))]
            html('<div class="pn-kicker">Why this creator?</div>'
                 + "".join(ui.metric_bar(l, v, accent=(l == "Audience relevance")) for l, v in rows)
                 + '<div class="pn-divider"></div>'
                 + "".join(ui.metric_bar(l, v) for l, v in vs_cands))
    with summary:
        with st.container(key="panel-sum"):
            html('<div class="pn-kicker">Assessment</div>'
                 + (f'<div style="font-size:1.02rem;line-height:1.5">{esc(vm["summary"])}</div>'
                    '<div class="pn-subtle" style="margin:4px 0 8px">Summary of analysed content</div>'
                    if vm["summary"] else "")
                 + ui.reasons_list(vm["reasons"], vm["gaps"]))

    # relevant videos: what the audience actually watches from this creator
    vids = vm["videos"]
    if vids:
        st.write("")
        html('<div class="pn-kicker">Relevant videos analysed</div>')
        vdf = pd.DataFrame(db.query(
            f"SELECT id, title, published_at, views, likes, comments FROM content "
            f"WHERE id IN ({','.join('?' * len(vids))}) ORDER BY published_at DESC", vids))
        vdf["Engagement %"] = 100 * (vdf["likes"].fillna(0) + vdf["comments"].fillna(0)) / vdf["views"].where(vdf["views"] > 0)
        vdf["Video"] = "https://youtu.be/" + vdf["id"]
        vdf["Published"] = vdf["published_at"].str[:10]
        st.dataframe(vdf[["title", "views", "Engagement %", "comments", "Published", "Video"]].rename(
            columns={"title": "Title", "views": "Views", "comments": "Comments"}), hide_index=True,
            width="stretch", column_config={
                "Views": st.column_config.NumberColumn(format="compact"),
                "Engagement %": st.column_config.NumberColumn(format="%.1f"),
                "Video": st.column_config.LinkColumn(display_text="Open ↗")})

    # evidence
    st.write("")
    html('<div class="pn-kicker">Evidence</div>')
    ev = pd.DataFrame(db.query("SELECT feature, content_id, quote, verified FROM evidence WHERE campaign_id=? AND "
                               "creator_id=?", (campaign_id, cid)))
    titles = {r["id"]: r["title"] for r in db.query(
        f"SELECT id, title FROM content WHERE id IN ({','.join('?' * len(vids))})", vids)} if vids else {}
    cards = []
    if not ev.empty:
        ok = ev[ev.verified == 1].copy()
        ok = ok[ok.feature.map(lambda x: f.get(x) is None or f.get(x) > 0)]  # a quote cannot back a score of 0
        ok["w"] = ok.feature.map(lambda x: ctx["used"].get(x, 0))
        for _, r in ok.sort_values("w", ascending=False).drop_duplicates("quote").head(6).iterrows():
            cards.append(ui.evidence_card(EVIDENCE_KIND.get(r.feature, r.feature.replace("_", " ")), f"“{r.quote}”",
                                          f"Video: {titles.get(r.content_id, r.content_id)}",
                                          f"https://youtu.be/{r.content_id}", ["Verified in source text"]))
    vt = db.query("SELECT video_id, url, tags_json FROM visual_tags WHERE campaign_id=? AND creator_id=?",
                  (campaign_id, cid))
    for r in vt[:2]:
        t = json.loads(r["tags_json"])
        in_focus = t.get("product_category_visible") and t.get("focus") in ("product", "mixed")
        tags = (["Campaign product in view"] if in_focus else []) + [
            lbl for k, lbl in [("product_demo", "Hands-on demo"), ("benchmark_or_screen_content", "Results / on-screen"),
                               ("face_visible", "Creator on camera")] if t.get(k)]
        cards.append(ui.evidence_card("Visual evidence · thumbnail", t.get("note", ""),
                                      f"Video: {titles.get(r['video_id'], r['video_id'])}",
                                      f"https://youtu.be/{r['video_id']}", tags, image=r["url"]))
    if cards:
        for i in range(0, len(cards), 3):
            cols = st.columns(3, gap="medium")
            for col, card in zip(cols, cards[i:i + 3]):
                col.markdown(card, unsafe_allow_html=True)
                col.write("")
        dropped = int((ev.verified == 0).sum()) if not ev.empty else 0
        if dropped:
            html(f'<div class="pn-subtle">{dropped} AI-cited quote(s) could not be found in the source text and were '
                 f'discarded; the related scores were lowered.</div>')
    else:
        ui.empty_state("Limited evidence available.", "This creator's score has lower confidence.")

    # community + comment evidence
    st.write("")
    comments = pd.DataFrame(db.query(
        f"SELECT content_id, text, likes, label, language FROM comments WHERE content_id IN "
        f"({','.join('?' * len(vids))})", vids)) if vids else pd.DataFrame()
    c1, c2 = st.columns([1, 1.3], gap="large")
    with c1, st.container(key="panel-community"):
        community_block(comments, f, ctx["lang"])
    with c2:
        html('<div class="pn-kicker">What the community says</div>')
        picks = []
        if not comments.empty:
            for label in ["purchase_intent", "technical", "question", "meaningful"]:
                sub = comments[(comments.label == label) & (comments.text.str.len() > 20)]
                if not sub.empty and len(picks) < 3:
                    picks.append(sub.sort_values("likes", ascending=False).iloc[0])
        if picks:
            for r in picks:
                txt = r.text if len(r.text) <= 220 else r.text[:217] + "…"
                html(ui.evidence_card(COMMENT_LABEL[r.label], f"“{txt}”",
                                      f"Comment on: {titles.get(r.content_id, r.content_id)} · {int(r.likes or 0)} likes",
                                      f"https://youtu.be/{r.content_id}",
                                      [f"Detected as: {COMMENT_LABEL[r.label]}"] + ([r.language.upper()] if r.language else [])))
                st.write("")
        else:
            ui.empty_state("No classified comments to show.", "Community signals have lower confidence.")

    # details
    st.write("")
    with st.expander("All criteria — raw values, weights and closeness"):
        used = ctx["used"]
        bd = pd.DataFrame([{"Group": GROUP_LABEL[c.group], "Criterion": crit_label(c),
                            "Type": "benefit" if c.kind == "B" else "cost", "Raw value": f.get(c.name),
                            "Closeness to ideal": res.loc[cid, f"close__{c.name}"] if c.name in used else None,
                            "Weight": used.get(c.name, 0.0),
                            "Imputed": bool(res.loc[cid, f"imputed__{c.name}"]) if c.name in used else False,
                            "Source": pipeline._method(c.name)} for c in ranking.CRITERIA])
        st.dataframe(bd, hide_index=True, width="stretch", column_config={
            "Closeness to ideal": st.column_config.ProgressColumn(format="%.2f", min_value=0, max_value=1),
            "Weight": st.column_config.NumberColumn(format="%.3f"),
            "Raw value": st.column_config.NumberColumn(format="%.3f")})
    d = ctx["discoveries"]
    if not d.empty and (d.creator_key == cid).any():
        with st.expander("How we found this creator"):
            st.dataframe(d[d.creator_key == cid][["source", "platform", "handle", "query", "url", "evidence"]],
                         hide_index=True, width="stretch", column_config={"url": st.column_config.LinkColumn()})


# ------------------------------------------------------------------ shortlist page
def money(v: float | None, cur: str = rel.CURRENCY) -> str:
    if v is None:
        return "–"
    sym = rel.CURRENCY_SYMBOL.get(cur, cur + " ")
    return f"{sym}{v:,.3f}" if 0 < v < 1 else f"{sym}{v:,.0f}"


def relationship_line(h: "rel.History") -> str:
    """Actual Prenew experience with a creator, shown next to (never merged into) the campaign score."""
    parts = [f"Prenew history: {h.sponsorships} sponsorship{'s' if h.sponsorships != 1 else ''}",
             f"👍 {h.positive} · 👎 {h.negative}"]
    if h.roas is not None:
        parts.append(f"historical ROAS {h.roas:.1f}×")
    if h.total_spend is not None:
        parts.append(f"{money(h.total_spend)} spent")
    return esc(" · ".join(parts))


def _ask_sponsorship(**request) -> None:
    ss.confirm_sp = request  # the dialog stays open across reruns until confirmed, cancelled or dismissed


@st.dialog("Start sponsorship?", on_dismiss=lambda: ss.pop("confirm_sp", None))
def confirm_sponsorship(campaign_id: str, cid: str, name: str, platform: str, spec: CampaignSpec) -> None:
    st.write(f"Start a sponsorship with **{name}** for “{spec.product}”?")
    st.caption("It appears under Sponsorships as In progress. The creator stays on this shortlist.")
    no, yes = st.columns(2)
    if no.button("Cancel", key="sp_confirm_no", width="stretch"):
        ss.pop("confirm_sp", None)
        st.rerun()
    if yes.button("Start sponsorship", key="sp_confirm_yes", type="primary", width="stretch"):
        ss.pop("confirm_sp", None)
        rel.start_sponsorship(campaign_id, cid, creator_name=name, platform=platform, country=spec.target_country,
                              campaign_label=f"{spec.product} · {COUNTRY.get(spec.target_country, spec.target_country)}")
        ss.toast = (f"Sponsorship with {name} started", ":material/handshake:")
        open_sponsorships(campaign_id)
        st.rerun()


def shortlist_page(ctx: dict, short: list[str]) -> None:
    with band:
        spec = ctx["spec"]
        band_title("Shortlist", "".join(ui.chip(x) for x in [spec.product,
                                                              COUNTRY.get(spec.target_country, spec.target_country),
                                                              PRIORITY[spec.goal][0]]),
                   f"{len(short)} creator{'s' if len(short) != 1 else ''} · search run "
                   f"{rel_time(ctx['campaign']['created_at'])}")
    if not short:
        ui.empty_state("Your shortlist is empty.", "Go back to the campaign and add creators with “+ Shortlist”.")
        return
    camp = ctx["campaign"]["id"]
    entries = rel.shortlist(camp)
    view = st.segmented_control("Order", ["Your order", "Favorites first"], required=True,
                                label_visibility="collapsed", **keep(f"sl_order_{camp}", "Your order"))
    if view == "Favorites first":
        entries = sorted(entries, key=lambda e: -(e["favorite"] or 0))  # stable: keeps the manual order within
    sponsored = rel.active_for(camp)
    if (req := ss.get("confirm_sp")) and req["campaign_id"] == camp:
        confirm_sponsorship(**req, spec=ctx["spec"])
    rows = []
    for pos, entry in enumerate(entries):
        cid, fav = entry["creator_id"], bool(entry["favorite"])
        vm = ctx["vms"].get(cid)
        pvm = next((P["vms"][cid] for P in ctx["platforms"].values() if cid in P["vms"]), None)
        name = vm["name"] if vm else ctx["creators"].get(cid, {}).get("name", cid)
        with st.container(key=f"card-sl-{ui.key(cid)}"):
            star, a, b, c, d = st.columns([0.45, 3.0, 1.2, 1.35, 0.9], vertical_alignment="center")
            star.button("★" if fav else "☆", key=f"slfav_{cid}", type="tertiary",
                        help="Favorite: your own marker, it never changes the score",
                        on_click=rel.toggle_favorite, args=(camp, cid))
            label = pvm["name"] if pvm else name  # display name, snapshotted into a sponsorship
            if cid.startswith(tts.PREFIX):  # human-selected TikTok lead: link + why, never a score
                h = cid[len(tts.PREFIX):]
                L = tts.lead(ctx["campaign"]["id"], h) or {}
                label = L.get("name") or f"@{h}"
                url = L.get("url") or f"https://www.tiktok.com/@{h}"
                a.markdown(f'<div class="pn-card-head"><div class="who"><div class="name">{esc(label)}</div>'
                           f'<div class="meta">{ui.platform_icon("tiktok", 12)} TikTok · @{esc(h)}'
                           f'{" · found through " + esc(L["topic"]) if L.get("topic") else ""}</div>'
                           f'<div style="margin-top:6px">{ui.human_lead_badge()}</div></div></div>',
                           unsafe_allow_html=True)
                b.markdown('<div class="pn-subtle" style="text-align:right">Not analysed · no score</div>',
                           unsafe_allow_html=True)
                c.link_button("Open on TikTok", url, width="stretch")
                rows.append({"creator": label, "platform": "TikTok", "rank": None, "cost_proxy_eur": None,
                             "campaign_score": None, "data_confidence": None, "subscribers": None,
                             "median_relevant_views": None, "channel_url": url,
                             "reasons": "Human-selected TikTok lead" + (f"; found through {L['topic']}"
                                                                         if L.get("topic") else "")})
            elif pvm:
                pl = PLATFORMS[pvm["platform"]]
                a.markdown(f'<div class="pn-card-head">{ui.avatar(pvm["name"], pvm["avatar"])}<div class="who">'
                           f'<div class="name">{esc(pvm["name"])}</div><div class="meta">'
                           f'{ui.platform_icon(pvm["platform"], 12)} {pl} · {ui.fmt_count(pvm["followers"])} followers'
                           f'</div><div style="margin-top:6px">'
                           f'{ui.confidence_badge(pvm["conf"], pvm["conf_label"])}</div></div></div>',
                           unsafe_allow_html=True)
                b.markdown(f'<div class="pn-scorewrap" style="justify-content:flex-end">{ui.tier_badge(pvm["tier"])}'
                           f'{ui.score_block(pvm["score"])}</div>', unsafe_allow_html=True)
                if pvm["url"]:
                    c.link_button("Open", pvm["url"], width="stretch")
                rows.append({"creator": pvm["name"], "platform": pl, "rank": pvm["rank"], "cost_proxy_eur": None,
                             "campaign_score": pvm["score"], "data_confidence": round(pvm["conf"], 2),
                             "subscribers": pvm["followers"], "median_relevant_views": None,
                             "channel_url": pvm["url"], "reasons": "; ".join(pvm["reasons"])})
            elif vm:
                a.markdown(f'<div class="pn-card-head">{ui.avatar(name, vm["avatar"])}<div class="who">'
                           f'<div class="name">{esc(name)}</div><div class="meta">{ui.platform_icon("youtube", 12)} YouTube · '
                           f'{esc(COUNTRY.get(vm["country"], vm["country"]) + " · " if vm["country"] else "")}'
                           f'{ui.fmt_count(vm["subs"])} '
                           f'subscribers</div><div style="margin-top:6px">'
                           f'{ui.confidence_badge(vm["conf"], vm["conf_label"], basis=vm["basis"])}</div></div></div>',
                           unsafe_allow_html=True)
                b.markdown(f'<div class="pn-scorewrap" style="justify-content:flex-end">{ui.tier_badge(vm["tier"])}'
                           f'{ui.score_block(vm["score"])}</div>', unsafe_allow_html=True)
                c.button("View", key=f"slv_{cid}", on_click=go, args=("analysis", cid), width="stretch")
                rows.append({"creator": name, "platform": "YouTube", "rank": vm["rank"], "cost_proxy_eur": vm["cost"], "campaign_score": vm["score"], "data_confidence": round(vm["conf"], 2),
                             "subscribers": vm["subs"], "median_relevant_views": vm["median_views"],
                             "channel_url": vm["url"], "reasons": "; ".join(vm["reasons"])})
            else:
                a.markdown(f"**{esc(name)}** — no longer passes the hard filters for this campaign")
            plat = ("tiktok" if cid.startswith(tts.PREFIX) else pvm["platform"] if pvm else "youtube")
            sp = sponsored.get(cid)
            if sp and sp["status"] == "in_progress":
                c.button("In sponsorship", key=f"slsp_{cid}", icon=":material/handshake:", width="stretch",
                         on_click=open_sponsorships, args=(camp,), help="Open this search's sponsorships")
            else:
                c.button("Start sponsorship" if not sp else "New sponsorship", key=f"slsp_{cid}",
                         icon=":material/handshake:", width="stretch", on_click=_ask_sponsorship,
                         kwargs=dict(campaign_id=camp, cid=cid, name=label, platform=plat))
            with d, st.container(horizontal=True, gap="small", key=f"slord_{ui.key(cid)}"):
                st.button("↑", key=f"slup_{cid}", help="Move up", disabled=view != "Your order" or pos == 0,
                          on_click=rel.move, args=(camp, cid, -1))
                st.button("↓", key=f"sldown_{cid}", help="Move down",
                          disabled=view != "Your order" or pos == len(entries) - 1,
                          on_click=rel.move, args=(camp, cid, 1))
            d.button("Remove", key=f"slr_{cid}", type="tertiary", on_click=toggle_shortlist, args=(camp, cid))
            if (h := rel.history(cid)).any:
                html(f'<div class="pn-subtle" style="margin:4px 0 0 2px">{relationship_line(h)}</div>')
    st.write("")
    if rows:
        spec = ctx["spec"]
        lines = [f"Creator shortlist — {spec.product} ({COUNTRY.get(spec.target_country, spec.target_country)})", ""]
        order = {p: i for i, p in enumerate(PLATFORMS.values())}
        for r in sorted(rows, key=lambda r: (order.get(r["platform"], 99), r["rank"] or 0)):
            if r["platform"] == "TikTok":
                lines.append(f"TikTok {r['creator']} — human-selected lead (reviewed on TikTok, not analysed, no score)")
                lines.append(f"   {r['channel_url']}")
                continue
            if r["platform"] == "YouTube":
                cost = f"≈ €{ui.fmt_count(r['cost_proxy_eur'])}/video" if r["cost_proxy_eur"] else "cost n/a"
                size = f"{ui.fmt_count(r['subscribers'])} subs · {cost}"
            else:
                size = f"{ui.fmt_count(r['subscribers'])} followers"
            lines.append(f"{r['platform']} #{r['rank']} {r['creator']} — score {r['campaign_score']}/100 · {size}")
            if r["reasons"]:
                lines.append(f"   Why: {r['reasons']}")
            if r["channel_url"]:
                lines.append(f"   {r['channel_url']}")
        lines += ["", f"Cost = median views × assumed €{config.ASSUMED_CPM_EUR:.0f} CPM (not a quote)."]
        html('<div class="pn-kicker" style="margin-top:6px">Summary to share</div>')
        st.code("\n".join(lines), language=None, wrap_lines=True)
        st.download_button("Export shortlist (CSV)", pd.DataFrame(rows).to_csv(index=False).encode(),
                           file_name="prenew_creator_shortlist.csv", mime="text/csv")
    html('<div class="pn-subtle" style="margin-top:10px">Next step: outreach — contact details are not collected by '
         'this tool.</div>')


# ------------------------------------------------------------------ sponsorships (what Prenew actually did)
def _bound(key: str, value, on_change, *args) -> dict:
    """Widget kwargs whose value always comes from the database (the source of truth), written back on change."""
    ss[key] = value
    return {"key": key, "on_change": lambda: on_change(*args, ss[key])}


def _save_performance(sid: str) -> None:
    k = f"spf_{sid}_"
    try:
        rel.update_performance(sid, ss.get(k + "price"), {kpi: ss.get(k + kpi) for kpi in rel.KPIS},
                               ss.get(k + "notes", ""))
        ss.toast = ("Performance saved", ":material/check:")
    except ValueError as e:
        ss.toast = (str(e), ":material/error:")


def sponsorship_card(r: dict) -> None:
    sid, k, d = r["id"], r["kpis"], r["derived"]
    market = COUNTRY.get(r["country"], r["country"]) if r["country"] else ""
    h = rel.history(r["creator_id"])
    with st.container(key=f"card-sp-{sid}"):
        html(f'<div class="pn-card-head"><div class="who"><div class="pn-rank">{ui.platform_icon(r["platform"], 13)} '
             f'{esc(PLATFORMS.get(r["platform"], r["platform"]))}{" · " + esc(market) if market else ""}</div>'
             f'<div class="name">{esc(r["creator_name"])}</div>'
             f'<div class="meta">{esc(r["campaign_label"])} · started {esc(r["started_at"][:10])}'
             f'{" · done " + esc(r["completed_at"][:10]) if r["completed_at"] else ""}</div></div></div>')
        st.selectbox("Status", list(rel.STATUSES), format_func=rel.STATUSES.get,
                     **_bound(f"sp_status_{sid}", r["status"], rel.set_status, sid))
        roas = d.get("roas")
        tiles = [("Price paid", money(r["price_paid"], r["currency"])), ("Views", ui.fmt_count(k.get("views"))),
                 ("Referrals", ui.fmt_count(k.get("referrals"))), ("Conversions", ui.fmt_count(k.get("conversions"))),
                 ("Revenue", money(k.get("revenue"), r["currency"])), ("ROAS", "–" if roas is None else f"{roas:.1f}×")]
        html('<div class="pn-stats three">' + "".join(ui.stat_tile(a, b) for a, b in tiles) + "</div>")
        st.segmented_control("Outcome", list(rel.OUTCOMES), format_func=rel.OUTCOMES.get, required=True,
                             help="How this sponsorship went. One outcome per sponsorship; the creator's history "
                                  "adds up all sponsorships.",
                             **_bound(f"sp_outcome_{sid}", r["outcome"] or "neutral", rel.set_outcome, sid))
        html(f'<div class="pn-subtle">History with this creator: 👍 {h.positive} · 👎 {h.negative} · '
             f'{h.sponsorships} sponsorship{"s" if h.sponsorships != 1 else ""}</div>')
        with st.popover("Edit performance", icon=":material/edit:", width="stretch"):
            with st.form(f"spf_form_{sid}", border=False):
                f = f"spf_{sid}_"
                st.number_input(f"Price paid ({rel.CURRENCY_SYMBOL.get(r['currency'], r['currency'])})", min_value=0.0,
                                value=r["price_paid"], step=100.0, format="%.2f", key=f + "price",
                                placeholder="not recorded")
                cols = st.columns(2)
                for i, (kpi, label) in enumerate(rel.KPIS.items()):
                    cols[i % 2].number_input(label + (f" ({rel.CURRENCY_SYMBOL.get(r['currency'], '')})"
                                                      if kpi in rel.MONEY_KPIS else ""),
                                             min_value=0.0, value=k.get(kpi), step=1.0, key=f + kpi,
                                             format="%.2f" if kpi in rel.MONEY_KPIS else "%.0f",
                                             placeholder="not recorded")
                st.text_area("Notes", r["notes"] or "", key=f + "notes", height=70)
                st.form_submit_button("Save", type="primary", width="stretch", on_click=_save_performance,
                                      args=(sid,))
        if d:
            with st.expander("All metrics"):
                for name, (label, _, _) in rel.DERIVED.items():
                    if name in d:
                        v = d[name]
                        html(f'<div class="pn-kv"><span class="k">{label}</span><span class="v">'
                             f'{f"{v:.1f}×" if name == "roas" else money(v, r["currency"])}</span></div>')
                if r["notes"]:
                    html(f'<div class="pn-note" style="margin-top:8px">{esc(r["notes"])}</div>')


def sponsorships_page(campaigns: list[dict]) -> None:
    """All sponsorships, filterable across searches but always grouped by the search they came from."""
    rows = rel.sponsorships()
    live = {c["id"] for c in campaigns}
    groups = rel.sponsorship_groups(rows)
    camp = ss.get("sp_f_campaign", "all")
    in_one = camp != "all" and camp in groups
    with band:
        band_title(groups[camp]["label"] if in_one else "Sponsorships",
                   meta="Sponsorships from this search" if in_one
                   else "Creators Prenew decided to work with, and what they delivered.")
    if not rows:
        ui.empty_state("No sponsorships yet", "Move a shortlisted creator into Sponsorships when Prenew begins "
                                              "working with them.")
        st.button("View shortlists", key="sp_empty_sl", type="primary", on_click=go, args=("shortlists",),
                  icon=":material/bookmarks:")
        return
    tot = rel.summary(groups[camp]["rows"] if in_one else rows)
    for col, (label, value) in zip(st.columns(4), [
            ("Active sponsorships", tot["active"]), ("Completed sponsorships", tot["completed"]),
            ("Total sponsorship spend", money(tot["spend"])), ("Total tracked revenue", money(tot["revenue"]))]):
        col.metric(label, value, border=True)
    f0, f1, f2, f3, f4 = st.columns([1.7, 1.5, 1.1, 1.1, 1.5], vertical_alignment="bottom")
    order = list(groups)  # newest sponsorship first
    camp = f0.selectbox("Search", ["all", *order], format_func=lambda c: "All searches" if c == "all"
                        else groups[c]["label"], **keep("sp_f_campaign", "all"))
    status = f1.segmented_control("Status", ["all", *rel.STATUSES], required=True, label_visibility="collapsed",
                                  format_func=lambda s_: "All" if s_ == "all" else rel.STATUSES[s_],
                                  **keep("sp_f_status", "all"))
    markets = sorted({r["country"] for r in rows if r["country"]})
    market = f2.selectbox("Market", ["all", *markets], format_func=lambda m: "All markets" if m == "all"
                          else COUNTRY.get(m, m), **keep("sp_f_market", "all"))
    plats = [p for p in PLATFORMS if any(r["platform"] == p for r in rows)]  # only platforms with real records
    plat = f3.selectbox("Platform", ["all", *plats], format_func=lambda p: "All platforms" if p == "all"
                        else PLATFORMS[p], **keep("sp_f_platform", "all"))
    query = f4.text_input("Search creators", placeholder="Search creators", **keep("sp_f_query", ""))
    match = lambda r: ((camp == "all" or r["campaign_id"] == camp) and (status == "all" or r["status"] == status)
                       and (market == "all" or r["country"] == market) and (plat == "all" or r["platform"] == plat)
                       and (not query or query.lower() in (r["creator_name"] or "").lower()))
    st.write("")
    shown = {c: [r for r in g["rows"] if match(r)] for c, g in groups.items()}
    shown = {c: rs for c, rs in shown.items() if rs}
    if not shown:
        ui.empty_state("No sponsorships match these filters.", "Clear a filter to see more.")
    for c, rs in shown.items():
        g = groups[c]
        with st.container(key=f"sp-group-{ui.key(c)}"):
            head, act = st.columns([4, 1.3], vertical_alignment="center")
            n_all = g["active"] + g["completed"]
            spend = f" · {money(g['spend'])} spent" if g["spend"] is not None else ""
            gone = "" if c in live else ' <span class="pn-chip">report deleted</span>'
            head.markdown(f'<div class="pn-group-name">{esc(g["label"])}{gone}</div><div class="pn-subtle">'
                          f'{n_all} sponsorship{"s" if n_all != 1 else ""} · {g["active"]} in progress · '
                          f'{g["completed"]} done{spend}</div>', unsafe_allow_html=True)
            if c in live:
                act.button("Open search", key=f"sp_open_{c}", type="tertiary", icon=":material/arrow_forward:",
                           icon_position="right", on_click=lambda i=c: ss.update(campaign=i, view="discover",
                                                                                   creator=None))
        for i in range(0, len(rs), 2):
            for col, r in zip(st.columns(2, gap="medium"), rs[i:i + 2]):
                with col:
                    sponsorship_card(r)
        st.write("")


# ------------------------------------------------------------------ router
open_db()
campaigns = list_campaigns()
ids = [c["id"] for c in campaigns]
if ss.get("campaign") not in ids:
    ss.campaign = ids[0] if ids else None
current = next((c for c in campaigns if c["id"] == ss.campaign), None)
short = shortlist_ids(current["id"]) if current else []
if msg := ss.pop("toast", None):
    st.toast(msg[0], icon=msg[1])
band = st.container(key="band")  # deep-green top band: nav + the page's title; pages add their heading to it
with band:
    top_nav(len(short), current)

if ss.view == "sponsorships":
    sponsorships_page(campaigns)
elif ss.view == "shortlists":
    shortlists_page(campaigns)
elif ss.view in ("campaign", "new") or current is None:
    campaign_page(campaigns)
else:
    ctx = build_context(current)
    if ss.view == "analysis" and ss.get("creator"):
        analysis_page(ctx, ss.creator, short)
    elif ss.view == "shortlist":
        shortlist_page(ctx, short)
    else:
        discover_page(ctx, short)
