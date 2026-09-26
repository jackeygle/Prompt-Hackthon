"""Prenew Creator Intelligence: Streamlit front-end.

Campaign → Discover (ranked creator cards) → Creator analysis (why + evidence) → Shortlist.
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

LIVE_DB = config.DB_PATH
DEMO_DB = config.ROOT / "data" / "demo.db"
DEFAULT_BRIEF = "Find creators for €600–900 refurbished gaming PCs targeting gamers in Germany."
COUNTRY = {"DE": "Germany", "AT": "Austria", "CH": "Switzerland", "FR": "France", "NL": "Netherlands",
           "SE": "Sweden", "FI": "Finland", "DK": "Denmark", "NO": "Norway", "US": "United States",
           "GB": "United Kingdom", "UK": "United Kingdom", "ES": "Spain", "IT": "Italy", "PL": "Poland"}
LANG = {"de": "German", "en": "English", "fr": "French", "nl": "Dutch", "sv": "Swedish", "fi": "Finnish",
        "da": "Danish", "no": "Norwegian", "es": "Spanish", "it": "Italian", "pl": "Polish"}
GROUP_LABEL = {"Campaign Fit": "Campaign fit", "Content Credibility": "Product evidence",
               "Audience Quality": "Community", "Reach & Performance": "Performance", "Cost & Risk": "Cost & risk"}
CARD_GROUPS = ["Campaign Fit", "Audience Quality", "Reach & Performance", "Content Credibility"]
# plain-language reasons for strong / weak criteria (derived from TOPSIS closeness, never from an LLM verdict)
REASON = {
    "niche_relevance": ("Content squarely in the campaign niche", "Only partly in the campaign niche"),
    "product_relevance": ("Covers this product type directly", "Rarely covers this product type"),
    "price_segment_relevance": ("Talks about the campaign price segment", "Little coverage of this price segment"),
    "target_lang_share": ("Strong {lang}-speaking audience signal", "Weak {lang}-speaking audience signal"),
    "first_hand_experience": ("Hands-on, first-hand product content", "Little hands-on content"),
    "benchmark_discussion": ("Shows benchmarks & performance tests", "Few benchmarks shown"),
    "product_comparison": ("Compares products for viewers", "Few product comparisons"),
    "price_discussion": ("Discusses prices and value", "Rarely discusses prices"),
    "purchase_recommendation": ("Gives concrete buying advice", "Little buying advice"),
    "visual_product_share": ("Product visible in thumbnails", "Product rarely visible in thumbnails"),
    "meaningful_ratio": ("Substantive comment discussions", "Mostly generic comments"),
    "technical_question_ratio": ("Technical, question-driven community", "Few technical questions"),
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
EVIDENCE_KIND = {"niche_relevance": "Niche relevance", "product_relevance": "Product evidence",
                 "price_segment_relevance": "Price-segment evidence", "first_hand_experience": "Hands-on evidence",
                 "benchmark_discussion": "Benchmark evidence", "price_discussion": "Price evidence",
                 "product_comparison": "Comparison evidence", "purchase_recommendation": "Buying-advice evidence"}
COMMENT_LABEL = {"purchase_intent": "Purchase intent", "technical": "Technical", "question": "Question",
                 "meaningful": "Meaningful", "generic": "Generic", "spam": "Suspicious / spam"}
STAGES = ["Understanding campaign", "Searching YouTube", "Searching the web", "Screening candidates",
          "Analyzing content & communities", "Ranking candidates"]

ss = st.session_state
ss.setdefault("view", "campaign")
ss.setdefault("source", "live" if LIVE_DB.exists() or not DEMO_DB.exists() else "demo")


# ------------------------------------------------------------------ data access
def use_source() -> None:
    db.use(DEMO_DB if ss.source == "demo" and DEMO_DB.exists() else LIVE_DB)
    db.execute("CREATE TABLE IF NOT EXISTS shortlist (campaign_id TEXT, creator_id TEXT, added_at TEXT, "
               "PRIMARY KEY (campaign_id, creator_id))")


def list_campaigns() -> list[dict]:
    return db.query("SELECT id, brief, spec_json, created_at FROM campaigns c WHERE EXISTS "
                    "(SELECT 1 FROM rankings r WHERE r.campaign_id=c.id) ORDER BY created_at DESC")


def shortlist_ids(campaign_id: str) -> list[str]:
    return [r["creator_id"] for r in db.query(
        "SELECT creator_id FROM shortlist WHERE campaign_id=? ORDER BY added_at", (campaign_id,))]


def toggle_shortlist(campaign_id: str, cid: str) -> None:
    if cid in shortlist_ids(campaign_id):
        db.execute("DELETE FROM shortlist WHERE campaign_id=? AND creator_id=?", (campaign_id, cid))
    else:
        db.execute("INSERT OR IGNORE INTO shortlist VALUES (?,?,?)", (campaign_id, cid, db.now()))


def go(view: str, creator: str | None = None) -> None:
    ss.view = view
    if creator:
        ss.creator = creator


def group_weights(spec: CampaignSpec) -> dict[str, float]:
    preset = ss.get("preset", spec.goal)
    w, _ = ranking.ahp_weights(ranking.AHP_PRESETS[preset])
    gw = {g: float(x) for g, x in zip(ranking.GROUPS, w)}
    if ss.get("custom_weights"):
        gw = {g: ss.get(f"w_{preset}_{g}", gw[g]) for g in ranking.GROUPS}
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
    gw = group_weights(spec)
    weights = ranking.criterion_weights(gw)
    passed = {c: ranking.hard_filter(f) for c, f in feats.items()}
    ok_ids = [c for c, (p, _) in passed.items() if p]
    df = pd.DataFrame({c: feats[c] for c in ok_ids}).T.reindex(columns=[c.name for c in ranking.CRITERIA])
    res = ranking.topsis(df, weights) if ok_ids else pd.DataFrame(columns=["score", "rank"])
    used = res.attrs.get("weights", {})
    lang = LANG.get(spec.target_language, spec.target_language.upper())

    def meta(c):
        return json.loads(stored.get(c, {}).get("breakdown_json") or "{}")

    vms = {}
    for c in res.index:
        f = feats[c]
        n_imp = sum(bool(res.loc[c, f"imputed__{k}"]) for k in used)
        conf = ranking.confidence(f, n_imp)
        groups = {}
        for g in ranking.GROUPS:
            ks = [k.name for k in ranking.CRITERIA if k.group == g and k.name in used]
            wsum = sum(used[k] for k in ks)
            groups[g] = 100 * sum(used[k] * res.loc[c, f"close__{k}"] for k in ks) / wsum if wsum else None
        ex = ranking.explain(res.loc[c], used, k=4)
        good = [REASON[s["criterion"]][0].format(lang=lang) for s in ex["strengths"]
                if s["closeness"] >= 0.6 and s["criterion"] in REASON][:3]
        gaps = [REASON[s["criterion"]][1].format(lang=lang) for s in ex["gaps"]
                if s["closeness"] <= 0.35 and s["criterion"] in REASON][:3]
        acc = accounts[accounts.creator_id == c] if not accounts.empty else pd.DataFrame()
        yt_row = acc[acc.platform == "youtube"].iloc[0] if not acc.empty and (acc.platform == "youtube").any() else None
        raw = json.loads(yt_row["raw_json"]) if yt_row is not None and yt_row["raw_json"] else {}
        others = {p: h for p, h in zip(acc.platform, acc.handle) if p != "youtube"} if not acc.empty else {}
        d = discoveries[discoveries.creator_key == c] if not discoveries.empty else pd.DataFrame()
        web_platforms = sorted(set(d[d.source == "tavily_web"].platform)) if not d.empty else []
        vms[c] = {
            "id": c, "name": creators.get(c, {}).get("name", c), "rank": int(res.loc[c, "rank"]),
            "score": round(100 * float(res.loc[c, "score"])), "conf": conf, "conf_label": ranking.confidence_label(conf),
            "subs": f.get("subscribers"), "median_views": f.get("median_relevant_views"),
            "purchase_intent": f.get("purchase_intent_ratio"), "lang_share": f.get("target_lang_share"),
            "cost": f.get("est_cost_eur"), "country": creators.get(c, {}).get("country"),
            "avatar": raw.get("thumbnail"), "url": yt_row["url"] if yt_row is not None else None,
            "niche": meta(c).get("niche_label", ""), "summary": meta(c).get("summary", ""),
            "videos": meta(c).get("videos", []), "groups": groups, "reasons": good, "gaps": gaps,
            "others": others, "web_platforms": web_platforms, "features": f,
        }
    # Hidden gem: derived from existing numbers only (rule shown in the badge tooltip)
    subs = [v["subs"] for v in vms.values() if v["subs"]]
    med_subs = float(pd.Series(subs).median()) if subs else None
    for v in vms.values():
        fit = v["groups"].get("Campaign Fit")
        v["gem"] = bool(med_subs and v["subs"] and fit is not None and fit >= 70 and v["subs"] < med_subs)
    return {"campaign": campaign, "spec": spec, "feats": feats, "res": res, "weights": weights, "used": used,
            "group_w": gw, "passed": passed, "vms": vms, "creators": creators, "discoveries": discoveries,
            "run_stats": run_stats, "lang": lang}


# ------------------------------------------------------------------ chrome
def top_nav(n_short: int, has_campaign: bool) -> None:
    left, *navs = st.columns([5.2, 1.05, 1.05, 1.25], vertical_alignment="center")
    with left:
        html('<div class="pc-brand"><span class="logo">prenew<span>.</span></span>'
             '<span class="product">Creator Intelligence</span></div>')
    current = "discover" if ss.view == "analysis" else ss.view
    for col, (view, label) in zip(navs, [("campaign", "Campaign"), ("discover", "Discover"),
                                         ("shortlist", f"Shortlist · {n_short}")]):
        with col:
            k = ("navon-" if current == view else "nav-") + view
            with st.container(key=k):
                st.button(label, key=f"btn_{k}", on_click=go, args=(view,), use_container_width=True,
                          disabled=(view != "campaign" and not has_campaign))
    html('<div class="pc-divider" style="margin:6px 0 22px"></div>')


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
    ss.source = "live"
    use_source()
    box = st.empty()
    state = {"i": 0}

    def cb(msg: str, _frac: float) -> None:
        state["i"] = max(state["i"], stage_index(msg))
        detail = msg.split(":", 1)[-1].strip() if ":" in msg else msg
        box.markdown(ui.stage_list(STAGES, state["i"], detail[:60]), unsafe_allow_html=True)

    box.markdown(ui.stage_list(STAGES, 0, "Preparing"), unsafe_allow_html=True)
    cid = pipeline.run_campaign(brief, spec=spec, seed_handles=seeds, progress=cb)
    box.markdown(ui.stage_list(STAGES, len(STAGES)), unsafe_allow_html=True)
    ss.campaign, ss.view, ss.creator = cid, "discover", None
    ss.pop("draft", None)
    st.rerun()


def campaign_page(campaigns: list[dict]) -> None:
    hero, form = st.columns([1.05, 1], gap="large")
    with hero:
        html('<div class="pc-hero"><div class="pc-kicker">Prenew Creator Intelligence</div>'
             '<h1>Find the right creators for your next campaign.</h1>'
             '<p>Discover and evaluate creators using content, community and campaign intelligence — '
             'then see exactly why each one is recommended.</p></div>')
        html('<div style="margin-top:26px">' + "".join(ui.chip(t) for t in [
            "YouTube data", "Web & social discovery", "Comment intelligence", "Evidence-backed scores"]) + "</div>")
        html(f'<div class="pc-note" style="margin-top:22px;max-width:560px">Like choosing a PC by its specs: every '
             f'creator is scored on the same campaign criteria — fit, community, performance and product '
             f'evidence — ranked with AHP + TOPSIS. AI only extracts facts and evidence; it never picks the '
             f'winner.</div>')
    with form:
        running = ss.pop("run_request", None)
        draft: CampaignSpec | None = ss.get("draft")
        with st.container(key="panel-campaign"):
            html(ui.steps(2 if running else (1 if draft else 0)))
            if running:
                html('<div class="pc-kicker">Finding creators for your campaign…</div>')
                try:
                    run_with_stages(*running)
                except Exception as e:
                    st.error(f"Discovery failed: {e}")
                return
            if draft is None:
                html('<div class="pc-kicker">Campaign brief</div>')
                brief = st.text_area("Campaign brief", ss.get("brief", DEFAULT_BRIEF), height=110,
                                     label_visibility="collapsed")
                html('<div class="pc-subtle" style="margin:-4px 0 12px">Describe product, market, audience and '
                     'price range in one sentence.</div>')
                if st.button("Continue →", type="primary", use_container_width=True):
                    ss.brief = brief
                    ss.source = "live"
                    use_source()
                    try:
                        with st.spinner("Understanding your campaign…"):
                            ss.draft = pipeline.parse_brief(LLMClient(config.LLM_MODELS), brief)
                        st.rerun()
                    except Exception as e:
                        miss = missing_keys(config.LLM_MODELS)
                        st.error(f"Could not read the brief: {e}" + (f" (missing: {', '.join(miss)})" if miss else ""))
            else:
                campaign_form(draft)
    recent_campaigns(campaigns)


def campaign_form(draft: CampaignSpec) -> None:
    html('<div class="pc-kicker">Campaign</div>')
    product = st.text_input("Product", draft.product)
    a, b = st.columns(2)
    country = a.text_input("Market (country code)", draft.target_country,
                           help=COUNTRY.get(draft.target_country, "")).strip().upper()
    price = b.text_input("Price range (product)", draft.price_segment)
    audience = st.text_input("Audience", draft.audience)
    a, b, c = st.columns(3)
    lang = a.text_input("Language", draft.target_language).strip().lower()
    goals = ["conversion", "awareness", "balanced"]
    goal = b.selectbox("Goal", goals, index=goals.index(draft.goal))
    niche = c.text_input("Niche", draft.niche)
    with st.expander("Search strategy & creator constraints"):
        keywords = st.text_input("Product keywords", ", ".join(draft.product_keywords))
        yt_q = st.text_area("YouTube searches (one per line)", "\n".join(draft.search_queries), height=130)
        web_q = st.text_area("Web & social searches (one per line)", "\n".join(draft.web_queries), height=130)
        x, y = st.columns(2)
        min_subs = x.number_input("Min subscribers", 0, 100_000_000, draft.min_subscribers, step=1000)
        max_subs = y.number_input("Max subscribers", 0, 100_000_000, draft.max_subscribers, step=100_000)
        seeds = st.text_input("Must-include channels (optional @handles, comma-separated)")
    missing = sorted(set(missing_keys(config.LLM_MODELS) + missing_keys(config.VLM_MODELS)
                         + ([] if config.TAVILY_API_KEY else ["TAVILY_API_KEY"])
                         + ([] if config.YOUTUBE_API_KEY else ["YOUTUBE_API_KEY"])))
    if missing:
        html(f'<div class="pc-note">Missing keys, those enrichments will be skipped: {esc(", ".join(missing))}</div>')
    back, go_btn = st.columns([1, 2])
    if back.button("← Edit brief", use_container_width=True):
        ss.pop("draft", None)
        st.rerun()
    if go_btn.button("Find creators →", type="primary", use_container_width=True):
        spec = CampaignSpec(
            product=product, niche=niche, price_segment=price, target_country=country, target_language=lang,
            goal=goal, audience=audience, product_keywords=[k.strip() for k in keywords.split(",") if k.strip()],
            search_queries=[q.strip() for q in yt_q.splitlines() if q.strip()],
            web_queries=[q.strip() for q in web_q.splitlines() if q.strip()],
            min_subscribers=int(min_subs), max_subscribers=int(max_subs))
        ss.run_request = (spec, ss.get("brief", DEFAULT_BRIEF), [s for s in seeds.split(",") if s.strip()])
        st.rerun()


def recent_campaigns(campaigns: list[dict]) -> None:
    st.write("")
    head, src = st.columns([3, 2], vertical_alignment="bottom")
    head.markdown("#### Recent campaigns")
    options = {"live": "Live data", "demo": "Offline demo snapshot"}
    choice = src.segmented_control("Data source", list(options), format_func=options.get, default=ss.source,
                                   label_visibility="collapsed", key="source_picker")
    if choice and choice != ss.source:
        ss.source, ss.campaign = choice, None
        st.rerun()
    if not campaigns:
        ui.empty_state("Start by creating a campaign.", "Describe your product and market above to discover creators.")
        return
    cols = st.columns(3)
    for col, c in zip(cols, campaigns[:3]):
        spec = CampaignSpec.model_validate_json(c["spec_json"])
        with col, st.container(key=f"card-camp-{ui.key(c['id'])}"):
            html(f'<div class="pc-subtle">{esc(c["created_at"][:16].replace("T", " "))}</div>'
                 f'<div style="font-weight:700;margin:4px 0 8px">{esc(spec.product)}</div>'
                 + ui.chip(COUNTRY.get(spec.target_country, spec.target_country)) + ui.chip(spec.price_segment)
                 + ui.chip(spec.goal))
            st.button("Open results →", key=f"open_{c['id']}", on_click=lambda i=c["id"]: (
                ss.update(campaign=i, view="discover", creator=None)), use_container_width=True)


# ------------------------------------------------------------------ discover page
def creator_card(vm: dict, ctx: dict, shortlisted: bool) -> None:
    with st.container(key=f"card-{ui.key(vm['id'])}"):
        badges = ui.confidence_badge(vm["conf"], vm["conf_label"]) + (" " + ui.gem_badge() if vm["gem"] else "")
        bars = "".join(ui.metric_bar(GROUP_LABEL[g], vm["groups"].get(g), accent=(g == "Campaign Fit"))
                       for g in CARD_GROUPS)
        plat = "YouTube" + (f" · {COUNTRY.get(vm['country'], vm['country'])}" if vm["country"] else "")
        specs = ui.spec_tile("Median relevant views", ui.fmt_count(vm["median_views"])) + ui.spec_tile(
            "Purchase-intent comments", ui.pct(vm["purchase_intent"]))
        reasons = ui.reasons_list(vm["reasons"]) if vm["reasons"] else \
            '<div class="pc-subtle" style="margin:10px 0">No standout strengths vs. other candidates.</div>'
        html(f'<div class="pc-card-head">{ui.avatar(vm["name"], vm["avatar"])}<div class="who">'
             f'<div class="pc-rank">#{vm["rank"]}</div><div class="name" title="{esc(vm["name"])}">{esc(vm["name"])}</div>'
             f'<div class="meta">{esc(plat)} · {ui.fmt_count(vm["subs"])} subscribers</div></div>'
             f'{ui.score_block(vm["score"])}</div>'
             f'<div style="margin:10px 0 4px">{badges}</div>{bars}<div class="pc-specs">{specs}</div>{reasons}')
        a, b = st.columns(2)
        a.button("View analysis", key=f"view_{vm['id']}", type="primary", on_click=go, args=("analysis", vm["id"]),
                 use_container_width=True)
        b.button("✓ Shortlisted" if shortlisted else "+ Shortlist", key=f"sl_{vm['id']}", use_container_width=True,
                 on_click=toggle_shortlist, args=(ctx["campaign"]["id"], vm["id"]))


def weights_controls(spec: CampaignSpec) -> None:
    presets = list(ranking.AHP_PRESETS)
    ss.setdefault("preset", spec.goal)
    with st.popover("Ranking priorities", use_container_width=True):
        st.segmented_control("Campaign goal", presets, key="preset", format_func=str.capitalize)
        w, cr = ranking.ahp_weights(ranking.AHP_PRESETS[ss.preset])
        st.caption(f"AHP pairwise preset · consistency ratio {cr:.3f} {'(consistent)' if cr <= 0.1 else '(> 0.1!)'}")
        st.toggle("Adjust weights manually", key="custom_weights")
        for g, x in zip(ranking.GROUPS, w):
            st.slider(GROUP_LABEL[g], 0.0, 1.0, float(round(x, 2)), 0.01, key=f"w_{ss.preset}_{g}",
                      disabled=not ss.get("custom_weights"))


def discover_page(ctx: dict, short: list[str]) -> None:
    spec, vms = ctx["spec"], ctx["vms"]
    n_web = sum(1 for v in vms.values() if v["web_platforms"])
    html(f'<div class="pc-kicker">Discover</div><h2 style="margin:0 0 6px">{len(vms)} creators ranked for '
         f'{esc(spec.product)}</h2><div class="pc-muted" style="margin-bottom:10px">'
         f'{len(ctx["feats"])} analysed in depth · scores are relative to this candidate set</div>'
         + ui.chip(COUNTRY.get(spec.target_country, spec.target_country), dark=True) + ui.chip("YouTube")
         + ui.chip(spec.price_segment) + ui.chip(ctx["lang"]) + ui.chip(f"Goal: {ss.get('preset', spec.goal)}")
         + (ui.chip(f"{n_web} also found on web/social") if n_web else ""))
    f1, f2, f3, f4, f5 = st.columns([1.6, 1.3, 1.1, 1.1, 1.2], vertical_alignment="bottom")
    query = f1.text_input("Search", placeholder="Search creators", label_visibility="collapsed")
    conf_filter = f2.segmented_control("Confidence", ["All", "Medium+", "High"], default="All",
                                       label_visibility="collapsed")
    gems = f3.toggle("◆ Hidden gems")
    sort = f4.selectbox("Sort", ["Campaign score", "Data confidence", "Audience size"],
                        label_visibility="collapsed")
    with f5:
        weights_controls(spec)

    items = sorted(vms.values(), key=lambda v: v["rank"])
    if query:
        items = [v for v in items if query.lower() in v["name"].lower()]
    if conf_filter == "High":
        items = [v for v in items if v["conf_label"] == "High"]
    elif conf_filter == "Medium+":
        items = [v for v in items if v["conf_label"] in ("High", "Medium")]
    if gems:
        items = [v for v in items if v["gem"]]
    if sort == "Data confidence":
        items.sort(key=lambda v: -v["conf"])
    elif sort == "Audience size":
        items.sort(key=lambda v: -(v["subs"] or 0))

    st.write("")
    if not vms:
        ui.empty_state("No strong creator matches found.",
                       "Try expanding the market, language or subscriber range in the campaign.")
    elif not items:
        ui.empty_state("No creators match these filters.", "Clear the search or loosen the confidence filter.")
    for i in range(0, len(items), 3):
        cols = st.columns(3, gap="medium")
        for col, vm in zip(cols, items[i:i + 3]):
            with col:
                creator_card(vm, ctx, vm["id"] in short)
    method_section(ctx)


def method_section(ctx: dict) -> None:
    st.write("")
    html('<div class="pc-kicker" style="margin-top:18px">Behind the ranking</div>')
    with st.expander("How the ranking works"):
        gw = ctx["group_w"]
        html('<div class="pc-muted" style="margin-bottom:8px">Hard filters first (recent relevant uploads, niche '
             f'relevance, ≥{config.MIN_TARGET_LANG_SHARE:.0%} target-language comments). Then AHP weights five '
             'criterion groups and TOPSIS ranks creators by distance to the ideal candidate. Data confidence is '
             'computed separately and never changes the score.</div>')
        html("".join(ui.metric_bar(GROUP_LABEL[g], 100 * w, display=f"{w:.0%}") for g, w in gw.items()))
        if ctx["res"].attrs.get("dropped"):
            html('<div class="pc-note">Not used in this ranking (known for &lt; '
                 f'{config.MIN_CRITERION_COVERAGE:.0%} of candidates): '
                 + esc(", ".join(ranking.CRITERION_BY_NAME[c].label for c in ctx["res"].attrs["dropped"])) + "</div>")
        html(f'<div class="pc-note" style="margin-top:8px"><b>Estimated Cost Proxy</b> = median relevant views × an '
             f'assumed €{config.ASSUMED_CPM_EUR:.0f} CPM. A comparison proxy — <b>not the creator\'s actual '
             f'quote</b>. Audience geography is not available from YouTube; target-language comment share is used '
             f'as a proxy.</div>')
        vms = list(ctx["vms"].values())
        if vms:
            dfc = pd.DataFrame([{"Creator": v["name"], "Rank": v["rank"], "Campaign score": v["score"],
                                 "Data confidence": round(100 * v["conf"])} for v in vms])
            ch = alt.Chart(dfc).mark_circle(size=90, color=T["text"]).encode(
                x=alt.X("Data confidence:Q", scale=alt.Scale(zero=False, padding=15)),
                y=alt.Y("Campaign score:Q", scale=alt.Scale(zero=False, padding=15)),
                tooltip=["Rank", "Creator", "Campaign score", "Data confidence"])
            st.caption("Campaign score vs data confidence — top-left means strong fit but thin data: check manually.")
            st.altair_chart(ch + ch.mark_text(dx=9, align="left", fontSize=10).encode(text="Rank:O"),
                            use_container_width=True)
    excluded = [(ctx["creators"].get(c, {}).get("name", c), r) for c, (p, r) in ctx["passed"].items() if not p]
    if excluded:
        with st.expander(f"Excluded by hard filters ({len(excluded)})"):
            st.dataframe(pd.DataFrame(excluded, columns=["Creator", "Reason"]), hide_index=True,
                         use_container_width=True)
    d = ctx["discoveries"]
    if not d.empty and d.creator_key.str.startswith("web:").any():
        wo = d[d.creator_key.str.startswith("web:")]
        g = wo.groupby("creator_key").agg(
            Creator=("handle", "first"), Platforms=("platform", lambda x: ", ".join(sorted(set(x)))),
            Mentions=("url", "count"), Evidence=("evidence", "first"), Source=("url", "first"))
        with st.expander(f"Found on web & social, no YouTube match ({len(g)}) — not ranked"):
            st.caption("Discovered via web search. No quantitative metrics available, so listed for manual review.")
            st.dataframe(g.sort_values("Mentions", ascending=False), hide_index=True, use_container_width=True,
                         column_config={"Source": st.column_config.LinkColumn()})
    if ctx["run_stats"]:
        with st.expander("Run statistics (external API usage)"):
            st.json(ctx["run_stats"])


# ------------------------------------------------------------------ creator analysis page
def community_block(comments: pd.DataFrame, f: dict, lang: str) -> None:
    html('<div class="pc-kicker">Community intelligence</div>')
    if comments.empty:
        ui.empty_state("No comments analysed.", "Comments may be disabled; this lowers data confidence.")
        return
    labeled = comments[comments.label.notna()]
    n = max(len(labeled), 1)
    share = lambda *ls: labeled.label.isin(ls).sum() / n
    rows = [("Substantive discussion", f.get("meaningful_ratio")), ("Technical discussion", share("technical")),
            ("Questions", share("question")), ("Purchase intent", share("purchase_intent")),
            ("Generic comments", share("generic")), ("Suspicious / spam", share("spam")),
            ("Creator response rate", f.get("creator_reply_rate")), (f"{lang}-language comments", f.get("target_lang_share"))]
    html("".join(ui.metric_bar(l, None if v is None else 100 * v, accent=l in ("Purchase intent",),
                               display=ui.pct(v)) for l, v in rows))
    html(f'<div class="pc-subtle">{len(labeled)} comments classified from the analysed videos.</div>')


def analysis_page(ctx: dict, cid: str, short: list[str]) -> None:
    vm = ctx["vms"].get(cid)
    st.button("← Back to creators", on_click=go, args=("discover",))
    if vm is None:
        ui.empty_state("Creator not found in this ranking.", "Go back to the creator list.")
        return
    f, res = vm["features"], ctx["res"]
    campaign_id = ctx["campaign"]["id"]

    # header
    with st.container(key="panel-head"):
        left, mid, right = st.columns([3.2, 1.2, 1.4], vertical_alignment="center")
        with left:
            loc = COUNTRY.get(vm["country"], vm["country"]) if vm["country"] else "country not declared"
            links = "".join(ui.chip(f"{p}: @{h}") for p, h in vm["others"].items())
            name = f'<a href="{esc(vm["url"])}" target="_blank" style="text-decoration:none">{esc(vm["name"])}</a>' \
                if vm["url"] else esc(vm["name"])
            html(f'<div class="pc-card-head">{ui.avatar(vm["name"], vm["avatar"], large=True)}<div class="who">'
                 f'<div class="pc-rank">#{vm["rank"]} OF {len(ctx["vms"])}</div>'
                 f'<div style="font-size:1.9rem;font-weight:800;letter-spacing:-.03em">{name}</div>'
                 f'<div class="meta">YouTube · {esc(loc)} · {esc(vm["niche"])}</div>'
                 f'<div style="margin-top:8px">{ui.confidence_badge(vm["conf"], vm["conf_label"])}'
                 f'{" " + ui.gem_badge() if vm["gem"] else ""}</div>'
                 f'<div style="margin-top:8px">{links}</div></div></div>')
        with mid:
            html(ui.score_block(vm["score"], "Campaign score", xl=True))
        with right:
            html('<div class="pc-specs" style="grid-template-columns:1fr;margin:0">'
                 + ui.spec_tile("Subscribers", ui.fmt_count(vm["subs"]))
                 + ui.spec_tile("Median relevant views", ui.fmt_count(vm["median_views"]))
                 + ui.spec_tile("Est. cost proxy (assumed CPM)", f"€{vm['cost']:,.0f}" if vm["cost"] else "–")
                 + "</div>")
            sl = cid in short
            st.button("✓ Shortlisted" if sl else "+ Add to shortlist", type="secondary" if sl else "primary",
                      on_click=toggle_shortlist, args=(campaign_id, cid), use_container_width=True, key="sl_detail")
    conf_line = (f"Data confidence {vm['conf'] * 100:.0f}% · {int(f.get('n_analyzed_videos', 0))} videos · "
                 f"transcripts {f.get('transcript_coverage', 0):.0%} · {int(f.get('n_classified_comments', 0))} "
                 f"comments · {int(f.get('n_visual_images', 0))} thumbnails. Confidence is shown separately and "
                 f"does not change the score.")
    html(f'<div class="pc-subtle" style="margin:8px 2px 18px">{esc(conf_line)}</div>')

    # why
    why, summary = st.columns([1.1, 1], gap="large")
    with why:
        with st.container(key="panel-why"):
            html('<div class="pc-kicker">Why this creator?</div>'
                 + "".join(ui.metric_bar(GROUP_LABEL[g], vm["groups"].get(g), accent=(g == "Campaign Fit"))
                           for g in ranking.GROUPS)
                 + '<div class="pc-subtle" style="margin-top:6px">0–100 = closeness to the best candidate on each '
                   'criterion group, weighted by the campaign goal.</div>')
    with summary:
        with st.container(key="panel-sum"):
            html('<div class="pc-kicker">Assessment</div>'
                 + (f'<div style="font-size:1.02rem;line-height:1.5">{esc(vm["summary"])}</div>'
                    '<div class="pc-subtle" style="margin:4px 0 8px">AI summary of the analysed content</div>'
                    if vm["summary"] else "")
                 + ui.reasons_list(vm["reasons"], vm["gaps"]))

    # evidence
    st.write("")
    html('<div class="pc-kicker">Why we think this — evidence</div>')
    ev = pd.DataFrame(db.query("SELECT feature, content_id, quote, verified FROM evidence WHERE campaign_id=? AND "
                               "creator_id=?", (campaign_id, cid)))
    vids = vm["videos"]
    titles = {r["id"]: r["title"] for r in db.query(
        f"SELECT id, title FROM content WHERE id IN ({','.join('?' * len(vids))})", vids)} if vids else {}
    cards = []
    if not ev.empty:
        ok = ev[ev.verified == 1].copy()
        ok["w"] = ok.feature.map(lambda x: ctx["used"].get(x, 0))
        for _, r in ok.sort_values("w", ascending=False).drop_duplicates("quote").head(6).iterrows():
            cards.append(ui.evidence_card(EVIDENCE_KIND.get(r.feature, r.feature.replace("_", " ")), f"“{r.quote}”",
                                          f"Video: {titles.get(r.content_id, r.content_id)}",
                                          f"https://youtu.be/{r.content_id}", ["Verified in source text"]))
    vt = db.query("SELECT video_id, url, tags_json FROM visual_tags WHERE campaign_id=? AND creator_id=?",
                  (campaign_id, cid))
    for r in vt[:2]:
        t = json.loads(r["tags_json"])
        tags = [lbl for k, lbl in [("product_category_visible", "Product visible"), ("product_demo", "Hands-on demo"),
                                   ("benchmark_or_screen_content", "Benchmark / screen"),
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
            html(f'<div class="pc-subtle">{dropped} AI-cited quote(s) could not be found in the source text and were '
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
        html('<div class="pc-kicker">What the community says</div>')
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
        bd = pd.DataFrame([{"Group": GROUP_LABEL[c.group], "Criterion": c.label,
                            "Type": "benefit" if c.kind == "B" else "cost", "Raw value": f.get(c.name),
                            "Closeness to ideal": res.loc[cid, f"close__{c.name}"] if c.name in used else None,
                            "Weight": used.get(c.name, 0.0),
                            "Imputed": bool(res.loc[cid, f"imputed__{c.name}"]) if c.name in used else False,
                            "Source": pipeline._method(c.name)} for c in ranking.CRITERIA])
        st.dataframe(bd, hide_index=True, use_container_width=True, column_config={
            "Closeness to ideal": st.column_config.ProgressColumn(format="%.2f", min_value=0, max_value=1),
            "Weight": st.column_config.NumberColumn(format="%.3f"),
            "Raw value": st.column_config.NumberColumn(format="%.3f")})
    if vids:
        with st.expander(f"Analysed videos ({len(vids)})"):
            vdf = pd.DataFrame(db.query(
                f"SELECT id, title, published_at, views, likes, comments, transcript_source FROM content "
                f"WHERE id IN ({','.join('?' * len(vids))})", vids))
            vdf["url"] = "https://youtu.be/" + vdf["id"]
            st.dataframe(vdf.drop(columns=["id"]), hide_index=True, use_container_width=True,
                         column_config={"url": st.column_config.LinkColumn()})
    d = ctx["discoveries"]
    if not d.empty and (d.creator_key == cid).any():
        with st.expander("How we found this creator"):
            st.dataframe(d[d.creator_key == cid][["source", "platform", "handle", "query", "url", "evidence"]],
                         hide_index=True, use_container_width=True, column_config={"url": st.column_config.LinkColumn()})


# ------------------------------------------------------------------ shortlist page
def shortlist_page(ctx: dict, short: list[str]) -> None:
    html(f'<div class="pc-kicker">Shortlist</div><h2 style="margin:0 0 4px">{len(short)} creator'
         f'{"s" if len(short) != 1 else ""} shortlisted</h2><div class="pc-muted" style="margin-bottom:18px">'
         f'For {esc(ctx["spec"].product)} · {esc(COUNTRY.get(ctx["spec"].target_country, ctx["spec"].target_country))}'
         f'</div>')
    if not short:
        ui.empty_state("Your shortlist is empty.", "Open Discover and add creators with “+ Shortlist”.")
        return
    rows = []
    for cid in short:
        vm = ctx["vms"].get(cid)
        name = vm["name"] if vm else ctx["creators"].get(cid, {}).get("name", cid)
        with st.container(key=f"card-sl-{ui.key(cid)}"):
            a, b, c, d = st.columns([3.2, 1.2, 1.1, 1.1], vertical_alignment="center")
            if vm:
                a.markdown(f'<div class="pc-card-head">{ui.avatar(name, vm["avatar"])}<div class="who">'
                           f'<div class="name">{esc(name)}</div><div class="meta">YouTube · '
                           f'{esc(COUNTRY.get(vm["country"], vm["country"] or "–"))} · {ui.fmt_count(vm["subs"])} '
                           f'subscribers</div><div style="margin-top:6px">'
                           f'{ui.confidence_badge(vm["conf"], vm["conf_label"])}</div></div></div>',
                           unsafe_allow_html=True)
                b.markdown(ui.score_block(vm["score"]), unsafe_allow_html=True)
                c.button("View", key=f"slv_{cid}", on_click=go, args=("analysis", cid), use_container_width=True)
                rows.append({"creator": name, "campaign_score": vm["score"], "data_confidence": round(vm["conf"], 2),
                             "subscribers": vm["subs"], "median_relevant_views": vm["median_views"],
                             "channel_url": vm["url"], "reasons": "; ".join(vm["reasons"])})
            else:
                a.markdown(f"**{esc(name)}** — no longer passes the hard filters for this campaign")
            d.button("Remove", key=f"slr_{cid}", on_click=toggle_shortlist, args=(ctx["campaign"]["id"], cid),
                     use_container_width=True)
    st.write("")
    if rows:
        st.download_button("Export shortlist (CSV)", pd.DataFrame(rows).to_csv(index=False).encode(),
                           file_name="prenew_creator_shortlist.csv", mime="text/csv")
    html('<div class="pc-subtle" style="margin-top:10px">Next step: outreach — contact details are not collected by '
         'this tool.</div>')


# ------------------------------------------------------------------ router
use_source()
campaigns = list_campaigns()
ids = [c["id"] for c in campaigns]
if ss.get("campaign") not in ids:
    ss.campaign = ids[0] if ids else None
current = next((c for c in campaigns if c["id"] == ss.campaign), None)
short = shortlist_ids(current["id"]) if current else []
top_nav(len(short), current is not None)

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
