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
import ui
from llm import LLMClient, missing_keys
from models import CampaignSpec
from ui import T, esc, html

st.set_page_config(page_title="Prenew Creator Intelligence", page_icon="◆", layout="wide",
                   initial_sidebar_state="collapsed")
ui.inject_css()

DEFAULT_BRIEF = "Find creators for €600–900 refurbished gaming PCs targeting gamers in Germany."  # demo example only
BRIEF_EXAMPLES = ("e.g. “Gaming PCs under €800 for Fortnite and CS2 players in Sweden” or "
                  "“RTX 4070 PCs for streamers in Finland, creators with 20k–500k subscribers”")
# one-click example briefs on the home search bar: (chip label, brief)
BRIEF_CHIPS = [("Gaming PCs < €800 · Sweden", "Gaming PCs under €800 for Fortnite and CS2 players in Sweden"),
               ("RTX 4070 PCs · Finland", "RTX 4070 PCs for streamers in Finland, creators with 20k–500k subscribers"),
               ("Refurbished PCs · Germany", "Refurbished gaming PCs at €600–900 for budget-conscious gamers in Germany")]
COUNTRY = {"DE": "Germany", "AT": "Austria", "CH": "Switzerland", "FR": "France", "NL": "Netherlands",
           "SE": "Sweden", "FI": "Finland", "DK": "Denmark", "NO": "Norway", "US": "United States",
           "GB": "United Kingdom", "UK": "United Kingdom", "ES": "Spain", "IT": "Italy", "PL": "Poland"}
LANG = {"de": "German", "en": "English", "fr": "French", "nl": "Dutch", "sv": "Swedish", "fi": "Finnish",
        "da": "Danish", "no": "Norwegian", "es": "Spanish", "it": "Italian", "pl": "Polish"}
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
}
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
    db.execute("CREATE TABLE IF NOT EXISTS shortlist (campaign_id TEXT, creator_id TEXT, added_at TEXT, "
               "PRIMARY KEY (campaign_id, creator_id))")


def list_campaigns() -> list[dict]:
    return db.query("SELECT id, brief, spec_json, created_at FROM campaigns ORDER BY created_at DESC")


def shortlist_ids(campaign_id: str) -> list[str]:
    return [r["creator_id"] for r in db.query(
        "SELECT creator_id FROM shortlist WHERE campaign_id=? ORDER BY added_at", (campaign_id,))]


def toggle_shortlist(campaign_id: str, cid: str) -> None:
    name = (db.query("SELECT name FROM creators WHERE id=?", (cid,)) or [{"name": "Creator"}])[0]["name"]
    if cid in shortlist_ids(campaign_id):
        db.execute("DELETE FROM shortlist WHERE campaign_id=? AND creator_id=?", (campaign_id, cid))
        ss.toast = (f"Removed {name} from the shortlist", ":material/bookmark_remove:")
    else:
        db.execute("INSERT OR IGNORE INTO shortlist VALUES (?,?,?)", (campaign_id, cid, db.now()))
        ss.toast = (f"Added {name} to the shortlist", ":material/bookmark_added:")


def go(view: str, creator: str | None = None) -> None:
    ss.view = view
    if creator:
        ss.creator = creator


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
    weights = ranking.criterion_weights(gw)
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
        d = discoveries[discoveries.creator_key == c] if not discoveries.empty else pd.DataFrame()
        web_platforms = sorted(set(d[d.source == "tavily_web"].platform)) if not d.empty else []
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
            "others": others, "web_platforms": web_platforms, "features": f,
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
    return {"campaign": campaign, "spec": spec, "feats": feats, "res": res, "weights": weights, "used": used,
            "group_w": gw, "passed": passed, "vms": vms, "creators": creators, "discoveries": discoveries,
            "run_stats": run_stats, "lang": lang, "budget": budget}


# ------------------------------------------------------------------ chrome
def top_nav(n_short: int, campaign: dict | None) -> None:
    """Logo left, cart-style shortlist right; below it, on its own line above the page title, one back link to the
    parent level (all campaigns, or the campaign). The current level is the title, so nothing is shown twice."""
    inside = campaign is not None and ss.view != "campaign"
    left, right = st.columns([5, 1.2], vertical_alignment="center")
    with left:
        html(ui.brand_header())
    if inside:
        with right, st.container(horizontal=True, horizontal_alignment="right",
                                 key=(f"cart-on-{n_short}" if ss.view == "shortlist" else f"cart-{n_short}")):
            st.button(f"Shortlist ({n_short})" if n_short else "Shortlist", icon=":material/bookmark:",
                      key="btn_cart", on_click=go, args=("shortlist",))
        with st.container(key="back"):
            if ss.view in ("analysis", "shortlist"):
                spec = CampaignSpec.model_validate_json(campaign["spec_json"])
                label = f"{spec.product} · {spec.target_country}"
                st.button(label, key="crumb_campaign", type="tertiary", icon=":material/arrow_back:",
                          on_click=go, args=("discover",), help=f"Back to {label}")
            else:
                st.button("All campaigns", key="crumb_home", type="tertiary", icon=":material/arrow_back:",
                          on_click=go, args=("campaign",))


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
    if m.startswith("web search") or m.startswith("merging"):
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


def _use_example(brief: str) -> None:
    ss["campaign_brief_v2"] = brief  # fills the search bar; the user still reviews and clicks Continue


def _discard_draft() -> None:
    ss.pop("draft", None)


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
        search_queries=lines("yt_q"), web_queries=lines("web_q"), min_subscribers=int(g("min_subscribers")),
        max_subscribers=int(g("max_subscribers")), n_creators=int(g("n_creators")),
        budget_per_video=int(g("budget") or 0), field_sources=sources)
    ss.run_request = (spec, ss.get("draft_brief", DEFAULT_BRIEF), commas("seeds"))


def cost_estimate(n: int) -> str:
    screen = min(max(config.N_AFTER_CHEAP_FILTER, 2 * n), config.MAX_SCREENED)
    yt = 600 + 2 * screen + 5 * n
    ai = 2 + screen + 6 * n
    return f"≈ {yt:,} YouTube units (of 10,000/day), {ai} AI calls"


def campaign_page(campaigns: list[dict]) -> None:
    running = ss.pop("run_request", None)
    parse = ss.pop("parse_request", None)
    # returning users with reports: the pitch shrinks to one line and their reports come first
    slim = bool(campaigns) and not running and not parse and ss.get("draft") is None
    if slim:
        with band:
            html('<div class="pn-hero-slim pn-center"><div class="t">Find the creators our gamers already watch.</div>'
                 '<div class="p">01 · Brief <span>→</span> 02 · Creators <span>→</span> 03 · Shortlist</div></div>')
            form = st.container(key="briefbar")
    else:
        hero, form = st.columns([1, 1.1], gap="large")
        with hero:
            html('<div class="pn-hero"><div class="pn-kicker">Prenew Creator Intelligence</div>'
                 '<h1>Find the creators our gamers already watch.</h1>'
                 '<p>Describe the campaign. We find, evaluate and rank relevant gaming and tech creators — '
                 'with the evidence behind every recommendation.</p>'
                 '<div class="pn-hero-proof"><span>01 · Brief</span><span>02 · Creators</span>'
                 '<span>03 · Shortlist</span></div></div>')
    with form:
        with st.container(key="panel-campaign"):
            if parse:
                try:
                    with st.spinner("Reading the brief…"):
                        ss.draft = pipeline.parse_brief(LLMClient(config.LLM_MODELS), parse)
                        ss.draft_brief = parse
                        ss.draft_n = ss.get("draft_n", 0) + 1  # fresh widget keys for every new draft
                except Exception as e:
                    miss = missing_keys(config.LLM_MODELS)
                    st.error(f"Could not read the brief: {e}" + (f" (missing: {', '.join(miss)})" if miss else ""))
            if running:
                html(ui.steps(2) + '<div class="pn-kicker">Finding creators…</div>')
                try:
                    run_with_stages(*running)
                except Exception as e:
                    st.error(f"Discovery failed: {e}")
                return
            if ss.get("draft") is None:
                if slim:  # search-bar style: one input + button in a row, examples as a quiet line below
                    with st.form("brief_form", border=False):
                        inp, go_col = st.columns([6, 1.25], vertical_alignment="center", gap="small")
                        inp.text_area("Campaign brief", height=68, label_visibility="collapsed",
                                      placeholder="Describe your campaign…",
                                      key="campaign_brief_v2")
                        with go_col:
                            st.form_submit_button("Continue →", type="primary", width="stretch",
                                                  on_click=_submit_brief)
                        if ss.pop("brief_error", False):
                            st.warning("Describe your campaign before continuing.")
                    with st.container(key="briefchips", horizontal=True, horizontal_alignment="center",
                                      gap="small"):
                        html('<span class="pn-brief-ex">Try:</span>')
                        for i, (label, brief) in enumerate(BRIEF_CHIPS):
                            st.button(label, key=f"ex_{i}", on_click=_use_example, args=(brief,),
                                      help=brief)
                else:
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
    recent_campaigns(campaigns, overlap=slim)


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
        a, b, c, d = st.columns(4)
        goals = ["conversion", "awareness", "balanced"]
        a.selectbox("Goal", goals, index=goals.index(draft.goal), key=k + "goal", format_func=str.capitalize)
        b.number_input("Min subscribers" + tag("min_subscribers"), 0, 100_000_000, draft.min_subscribers,
                       step=1000, key=k + "min_subscribers")
        c.number_input("Max subscribers" + tag("max_subscribers"), 0, 100_000_000, draft.max_subscribers,
                       step=100_000, key=k + "max_subscribers")
        d.number_input("Budget per video (€)", 0, 10_000_000, draft.budget_per_video, step=500, key=k + "budget",
                       help="0 = no limit. Creators above it are flagged, not removed.")
        opts = config.N_CREATOR_OPTIONS
        st.segmented_control("Creators to analyse", opts, default=draft.n_creators if draft.n_creators in opts
                             else config.DEFAULT_N_CREATORS, key=k + "n_creators", required=True)
        html(f'<div class="pn-subtle" style="margin:-6px 0 8px">10 creators {esc(cost_estimate(10))} · '
             f'50 creators {esc(cost_estimate(50))}</div>')
        with st.expander("Search setup"):
            st.text_input("Niche", draft.niche, key=k + "niche")
            st.text_input("Product keywords", ", ".join(draft.product_keywords), key=k + "keywords")
            st.text_area("YouTube searches (one per line)", "\n".join(draft.search_queries), height=130,
                         key=k + "yt_q")
            st.text_area("Web & social searches (one per line)", "\n".join(draft.web_queries), height=130,
                         key=k + "web_q")
            st.text_input("Must-include channels (@handles, comma-separated)", "", key=k + "seeds")
        missing = sorted(set(missing_keys(config.LLM_MODELS) + missing_keys(config.VLM_MODELS)
                             + ([] if config.TAVILY_API_KEY else ["TAVILY_API_KEY"])
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
    home.markdown(f'<div class="pn-list-head"><span class="t">Recent campaigns</span>'
                  f'<span class="n">{len(shown)} report{"s" if len(shown) != 1 else ""}</span></div>',
                  unsafe_allow_html=True)
    with home, st.container(key="reportlist"):
        for c in shown:
            spec = CampaignSpec.model_validate_json(c["spec_json"])
            n = counts[c["id"]]
            brief = " ".join((c.get("brief") or "").split())
            brief = brief if len(brief) <= 90 else brief[:87] + "…"
            specs = " · ".join(x for x in [spec.target_country, spec.price_segment, spec.goal.capitalize()] if x)
            with st.container(key=f"row-camp-{ui.key(c['id'])}", horizontal=True, vertical_alignment="center",
                              gap="small"):
                html(f'<div class="pn-row-main"><div class="nm">{esc(spec.product)}</div>'
                     f'<div class="br">{esc(brief) if brief else "&nbsp;"}</div></div>')
                html(f'<div class="pn-row-spec">{esc(specs)}</div>')
                html(f'<div class="pn-row-num"><b>{n}</b> creator{"s" if n != 1 else ""}</div>')
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
    st.caption("Other reports and their creator data will be kept.")
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
        loc = COUNTRY.get(vm["country"], vm["country"]) if vm["country"] else ""
        stats = "".join([
            ui.stat_tile("Subscribers", ui.fmt_count(vm["subs"])),
            ui.stat_tile("Median views", ui.fmt_count(vm["median_views"])),
            ui.stat_tile("Cost proxy", f"€{ui.fmt_count(vm['cost'])}" if vm["cost"] else "–",
                         hint=f"Per video: median views × assumed €{config.ASSUMED_CPM_EUR:.0f} CPM, not a quote"),
        ])
        reason = ui.reasons_list(vm["reasons"][:1]) if vm["reasons"] else ""
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
    presets = list(ranking.AHP_PRESETS)
    with st.popover("Ranking priorities", width="stretch"):
        st.number_input("Budget per video (€, 0 = no limit)", 0, 10_000_000, step=500,
                        **keep(f"budget_{campaign_id}", spec.budget_per_video),
                        help="Creators whose cost proxy is above this are flagged and listed last. "
                             "The campaign score does not change.")
        st.segmented_control("Campaign goal", presets, format_func=str.capitalize, required=True,
                             **keep(f"preset_{campaign_id}", spec.goal))
        preset = preset_of(spec, campaign_id)
        w, cr = ranking.ahp_weights(ranking.AHP_PRESETS[preset])
        st.caption(f"AHP pairwise preset · consistency ratio {cr:.3f} {'(consistent)' if cr <= 0.1 else '(> 0.1!)'}")
        st.toggle("Adjust weights manually", **keep(f"custom_{campaign_id}", False))
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
                                                 f"Goal: {preset_of(spec, ctx['campaign']['id']).capitalize()}"] if x),
                   f"{len(vms)} ranked · {analysed} analysed · {analysed - len(vms)} filtered out")
    f1, f2, f3, f4, f5, f6 = st.columns([1.4, 1.7, 1.1, 1.3, 1.2, 1.2], vertical_alignment="bottom")
    c = ctx["campaign"]["id"]  # filters survive a visit to a creator's analysis and back
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


def method_section(ctx: dict) -> None:
    st.write("")
    with st.expander("Ranking method & weights"):
        gw = ctx["group_w"]
        html('<div class="pn-muted" style="margin-bottom:8px">Scores are relative to this candidate set. '
             'Confidence is shown separately and never changes the score.</div>')
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
        g = wo.groupby("creator_key").agg(
            Creator=("handle", "first"), Platforms=("platform", lambda x: ", ".join(sorted(set(x)))),
            Mentions=("url", "count"), Evidence=("evidence", "first"), Source=("url", "first"))
        with st.expander(f"Found on web & social, no YouTube match ({len(g)}) — not ranked"):
            st.caption("No YouTube metrics available — review manually.")
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
            links = "".join(ui.platform_chip(p, h) for p, h in vm["others"].items())
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

    # why
    why, summary = st.columns([1.1, 1], gap="large")
    with why:
        with st.container(key="panel-why"):
            rows = [(label, vm["bars"].get(label)) for label, _, _ in CARD_BARS + [EVIDENCE_BAR]]
            rel = [("Performance vs. candidates", vm["groups"].get("Reach & Performance")),
                   ("Cost & risk vs. candidates", vm["groups"].get("Cost & Risk"))]
            html('<div class="pn-kicker">Why this creator?</div>'
                 + "".join(ui.metric_bar(l, v, accent=(l == "Audience relevance")) for l, v in rows)
                 + '<div class="pn-divider"></div>'
                 + "".join(ui.metric_bar(l, v) for l, v in rel))
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
def shortlist_page(ctx: dict, short: list[str]) -> None:
    with band:
        spec = ctx["spec"]
        band_title("Shortlist", "", f"{len(short)} creator{'s' if len(short) != 1 else ''} for {spec.product} · "
                                    f"{COUNTRY.get(spec.target_country, spec.target_country)}")
    if not short:
        ui.empty_state("Your shortlist is empty.", "Go back to the campaign and add creators with “+ Shortlist”.")
        return
    rows = []
    for cid in short:
        vm = ctx["vms"].get(cid)
        name = vm["name"] if vm else ctx["creators"].get(cid, {}).get("name", cid)
        with st.container(key=f"card-sl-{ui.key(cid)}"):
            a, b, c, d = st.columns([3.2, 1.2, 1.1, 1.1], vertical_alignment="center")
            if vm:
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
                rows.append({"creator": name, "rank": vm["rank"], "cost_proxy_eur": vm["cost"], "campaign_score": vm["score"], "data_confidence": round(vm["conf"], 2),
                             "subscribers": vm["subs"], "median_relevant_views": vm["median_views"],
                             "channel_url": vm["url"], "reasons": "; ".join(vm["reasons"])})
            else:
                a.markdown(f"**{esc(name)}** — no longer passes the hard filters for this campaign")
            d.button("Remove", key=f"slr_{cid}", on_click=toggle_shortlist, args=(ctx["campaign"]["id"], cid),
                     width="stretch")
    st.write("")
    if rows:
        spec = ctx["spec"]
        lines = [f"Creator shortlist — {spec.product} ({COUNTRY.get(spec.target_country, spec.target_country)})", ""]
        for r in sorted(rows, key=lambda r: r["rank"]):
            cost = f"≈ €{ui.fmt_count(r['cost_proxy_eur'])}/video" if r["cost_proxy_eur"] else "cost n/a"
            lines.append(f"#{r['rank']} {r['creator']} — score {r['campaign_score']}/100 · "
                         f"{ui.fmt_count(r['subscribers'])} subs · {cost}")
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

if ss.view == "campaign" or current is None:
    campaign_page(campaigns)
else:
    ctx = build_context(current)
    if ss.view == "analysis" and ss.get("creator"):
        analysis_page(ctx, ss.creator, short)
    elif ss.view == "shortlist":
        shortlist_page(ctx, short)
    else:
        discover_page(ctx, short)
