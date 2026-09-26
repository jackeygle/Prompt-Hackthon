"""Streamlit dashboard: run a campaign, re-weight criteria live, inspect explainable rankings.

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
from llm import LLMClient, missing_keys
from models import CampaignSpec

st.set_page_config(page_title="Creator Intelligence", layout="wide")

LIVE_DB = config.DB_PATH
DEMO_DB = config.ROOT / "data" / "demo.db"
DEFAULT_BRIEF = "Find creators for €600–900 refurbished gaming PCs targeting gamers in Germany."

# ------------------------------------------------------------------ sidebar: data source + new run
with st.sidebar:
    st.header("Data")
    sources = {"Live database": LIVE_DB, "Demo snapshot": DEMO_DB}
    available = {k: v for k, v in sources.items() if Path(v).exists()}
    src = st.radio("Source", list(available) or ["Live database"], label_visibility="collapsed")
    db.use(available.get(src, LIVE_DB))

    with st.expander("▶ New campaign run", expanded=not available):
        brief = st.text_area("Campaign brief", DEFAULT_BRIEF, height=100)
        st.caption("Step 1 parses the brief (1 LLM call). You review and edit the spec before anything is searched.")
        if st.button("1 · Parse brief", type="primary"):
            db.use(LIVE_DB)
            try:
                with st.spinner("Extracting campaign spec…"):
                    st.session_state["draft_spec"] = pipeline.parse_brief(LLMClient(config.LLM_MODELS), brief)
                    st.session_state["draft_brief"] = brief
            except Exception as e:
                missing = missing_keys(config.LLM_MODELS)
                st.error(f"Brief parsing failed: {e}" + (f" — missing env vars: {', '.join(missing)}" if missing else ""))


def spec_editor() -> None:
    """Human review of the LLM-extracted spec; discovery only starts after confirmation."""
    draft: CampaignSpec = st.session_state["draft_spec"]
    st.subheader("Review campaign spec before running")
    st.caption(f"Brief: {st.session_state.get('draft_brief', '')}")
    with st.form("spec_form"):
        a, b, c = st.columns(3)
        product = a.text_input("Product", draft.product)
        niche = b.text_input("Niche / category", draft.niche)
        price = c.text_input("Price segment (product, not budget)", draft.price_segment)
        country = a.text_input("Target country (ISO-2)", draft.target_country).strip().upper()
        lang = b.text_input("Language (ISO 639-1)", draft.target_language).strip().lower()
        goal = c.selectbox("Campaign goal", ["conversion", "awareness", "balanced"],
                           index=["conversion", "awareness", "balanced"].index(draft.goal))
        audience = st.text_input("Audience", draft.audience)
        keywords = st.text_input("Product keywords (comma-separated)", ", ".join(draft.product_keywords))
        a, b = st.columns(2)
        yt_q = a.text_area("YouTube search queries (one per line)", "\n".join(draft.search_queries), height=160)
        web_q = b.text_area("Web discovery queries (Tavily, one per line)", "\n".join(draft.web_queries), height=160)
        a, b, c = st.columns(3)
        min_subs = a.number_input("Min subscribers", 0, 100_000_000, draft.min_subscribers, step=1000)
        max_subs = b.number_input("Max subscribers", 0, 100_000_000, draft.max_subscribers, step=100_000)
        seeds = c.text_input("Seed channels (optional @handles, comma-separated)")
        go, cancel = st.columns([1, 5])
        run = go.form_submit_button("2 · Confirm & run", type="primary")
        discard = cancel.form_submit_button("Discard")
    if discard:
        st.session_state.pop("draft_spec", None)
        st.rerun()
    if run:
        spec = CampaignSpec(
            product=product, niche=niche, price_segment=price, target_country=country, target_language=lang,
            goal=goal, audience=audience, product_keywords=[k.strip() for k in keywords.split(",") if k.strip()],
            search_queries=[q.strip() for q in yt_q.splitlines() if q.strip()],
            web_queries=[q.strip() for q in web_q.splitlines() if q.strip()],
            min_subscribers=int(min_subs), max_subscribers=int(max_subs))
        missing = sorted(set(missing_keys(config.LLM_MODELS) + missing_keys(config.VLM_MODELS)
                             + ([] if config.TAVILY_API_KEY else ["TAVILY_API_KEY"])
                             + ([] if config.YOUTUBE_API_KEY else ["YOUTUBE_API_KEY"])))
        if missing:
            st.warning("Missing env vars (those enrichments will be skipped): " + ", ".join(missing))
        db.use(LIVE_DB)
        bar = st.progress(0.0, "Starting")
        try:
            cid = pipeline.run_campaign(st.session_state.get("draft_brief", ""), spec=spec,
                                        seed_handles=[x for x in seeds.split(",") if x.strip()],
                                        progress=lambda m, f: bar.progress(f, m))
            st.session_state["campaign"] = cid
            st.session_state.pop("draft_spec", None)
            st.rerun()
        except Exception as e:
            st.error(f"Run failed: {e}")
    st.divider()


if "draft_spec" in st.session_state:
    st.title("Creator Intelligence Engine")
    spec_editor()

campaigns = db.query("SELECT id, brief, spec_json, created_at FROM campaigns c WHERE EXISTS "
                     "(SELECT 1 FROM rankings r WHERE r.campaign_id=c.id) ORDER BY created_at DESC")
if not campaigns:
    if "draft_spec" not in st.session_state:
        st.title("Creator Intelligence Engine")
    st.info("No completed campaign yet. Start a run from the sidebar.")
    st.stop()

with st.sidebar:
    labels = {c["id"]: f"{c['created_at'][:16]} · {c['brief'][:40]}" for c in campaigns}
    ids = list(labels)
    default = ids.index(st.session_state["campaign"]) if st.session_state.get("campaign") in ids else 0
    campaign_id = st.selectbox("Campaign", ids, index=default, format_func=labels.get)

camp = next(c for c in campaigns if c["id"] == campaign_id)
spec = CampaignSpec.model_validate_json(camp["spec_json"])

# ------------------------------------------------------------------ header
if "draft_spec" not in st.session_state:
    st.title("Creator Intelligence Engine")
st.markdown(f"**Brief:** {camp['brief']}")
c1, c2, c3, c4 = st.columns([3, 2, 2, 2])
for col, label, value in [(c1, "Product", spec.product), (c2, "Price segment (product)", spec.price_segment),
                          (c3, "Market / language", f"{spec.target_country} / {spec.target_language}"),
                          (c4, "Goal", spec.goal)]:
    col.caption(label)
    col.markdown(f"**{value}**")
with st.expander("Interpreted campaign spec & search queries"):
    st.json(spec.model_dump())

# ------------------------------------------------------------------ weights (AHP)
with st.sidebar:
    st.header("Criteria weights (AHP)")
    preset = st.selectbox("Preset", list(ranking.AHP_PRESETS),
                          index=list(ranking.AHP_PRESETS).index(spec.goal))
    w_preset, cr = ranking.ahp_weights(ranking.AHP_PRESETS[preset])
    st.caption(f"Consistency ratio CR = {cr:.3f} {'✅' if cr <= 0.1 else '⚠️ > 0.1'}")
    custom = st.toggle("Adjust manually")
    group_w = {}
    for g, w in zip(ranking.GROUPS, w_preset):
        group_w[g] = st.slider(g, 0.0, 1.0, float(round(w, 2)), 0.01, key=f"w_{preset}_{g}") if custom else float(w)
    total = sum(group_w.values()) or 1.0
    group_w = {g: w / total for g, w in group_w.items()}
    st.dataframe(pd.DataFrame({"weight": group_w}).style.format("{:.0%}"), use_container_width=True)

# ------------------------------------------------------------------ load + rank live
feats = pipeline.load_features(campaign_id)
stored = {r["creator_id"]: r for r in db.query("SELECT * FROM rankings WHERE campaign_id=?", (campaign_id,))}
creators = {r["id"]: r for r in db.query("SELECT * FROM creators")}
accounts = pd.DataFrame(db.query("SELECT * FROM platform_accounts"))
discoveries = pd.DataFrame(db.query("SELECT * FROM discoveries WHERE campaign_id=?", (campaign_id,)))
run_stats = next((json.loads(r["stats_json"]) for r in
                  db.query("SELECT stats_json FROM run_stats WHERE campaign_id=?", (campaign_id,))), None)
LANG_COL = f"{spec.target_language.upper()} comments"
COST_COL = "Cost proxy € (assumed CPM)"

weights = ranking.criterion_weights(group_w)
passed = {cid: ranking.hard_filter(f) for cid, f in feats.items()}
ok_ids = [cid for cid, (p, _) in passed.items() if p]
df = pd.DataFrame({cid: feats[cid] for cid in ok_ids}).T.reindex(columns=[c.name for c in ranking.CRITERIA])
res = ranking.topsis(df, weights) if ok_ids else pd.DataFrame(columns=["score", "rank"])


def links_for(cid: str) -> list[str]:
    if accounts.empty:
        return []
    rows = accounts[(accounts.creator_id == cid) & (accounts.platform != "youtube")]
    return sorted(rows.platform.tolist())


def sources_for(cid: str) -> str:
    if discoveries.empty:
        return "YouTube search"
    d = discoveries[discoveries.creator_key == cid]
    labels = []
    if (d.source == "youtube_search").any():
        labels.append("YouTube search")
    if (d.source == "seed").any():
        labels.append("seed")
    web_platforms = sorted(set(d[d.source == "tavily_web"].platform))
    if web_platforms:
        labels.append("Web: " + "/".join(web_platforms))
    return " + ".join(labels) or "YouTube search"


def meta(cid: str) -> dict:
    return json.loads(stored.get(cid, {}).get("breakdown_json") or "{}")


rows = []
for cid in res.index:
    f = feats[cid]
    n_imp = int(sum(bool(res.loc[cid, f"imputed__{c}"]) for c in weights if f"imputed__{c}" in res.columns))
    conf = ranking.confidence(f, n_imp)
    rows.append({"Rank": int(res.loc[cid, "rank"]), "Creator": creators.get(cid, {}).get("name", cid),
                 "Campaign score": float(res.loc[cid, "score"]), "Confidence": conf,
                 "Conf.": ranking.confidence_label(conf), "Niche": meta(cid).get("niche_label", ""),
                 "Subscribers": f.get("subscribers"), "Median views": f.get("median_relevant_views"),
                 COST_COL: f.get("est_cost_eur"), LANG_COL: f.get("target_lang_share"),
                 "Found via": sources_for(cid), "Other platforms": ", ".join(links_for(cid)), "id": cid})
table = pd.DataFrame(rows).sort_values("Rank") if rows else pd.DataFrame()

# ------------------------------------------------------------------ ranking table + scatter
st.subheader(f"Ranking · {len(ok_ids)} creators passed the hard filter (of {len(feats)} analysed)")
if table.empty:
    st.warning("No creator passed the hard filter.")
    st.stop()

left, right = st.columns([3, 2])
with left:
    st.dataframe(
        table.drop(columns=["id", "Confidence"]),
        hide_index=True, use_container_width=True, height=min(38 * (len(table) + 1), 560),
        column_config={
            "Campaign score": st.column_config.ProgressColumn(format="%.2f", min_value=0, max_value=1),
            "Subscribers": st.column_config.NumberColumn(format="%d"),
            "Median views": st.column_config.NumberColumn(format="%d"),
            COST_COL: st.column_config.NumberColumn(format="€%d", help=(
                "Median relevant views × assumed CPM. NOT the creator's actual price or quote.")),
            LANG_COL: st.column_config.ProgressColumn(format="%.2f", min_value=0, max_value=1),
        })
    st.caption(f"**{COST_COL}** = median relevant views × an assumed €{config.ASSUMED_CPM_EUR:.0f} CPM. "
               "It is a media-value proxy for comparing creators, **not the creator's actual quote**. "
               f"Audience geography is not available from YouTube; '{LANG_COL}' is a proxy.")
    if res.attrs.get("dropped"):
        st.info("Not used in this ranking (known for < "
                f"{config.MIN_CRITERION_COVERAGE:.0%} of candidates): "
                + ", ".join(ranking.CRITERION_BY_NAME[c].label for c in res.attrs["dropped"]))
with right:
    chart = alt.Chart(table).mark_circle(size=120).encode(
        x=alt.X("Confidence:Q", scale=alt.Scale(zero=False, padding=20), title="Data confidence"),
        y=alt.Y("Campaign score:Q", scale=alt.Scale(zero=False, padding=20)),
        tooltip=["Rank", "Creator", alt.Tooltip("Campaign score:Q", format=".2f"),
                 alt.Tooltip("Confidence:Q", format=".2f")],
    )
    text = chart.mark_text(align="left", dx=8, fontSize=10).encode(text="Rank:O")
    st.altair_chart(chart + text, use_container_width=True)
    st.caption("Labels = rank. Top-left = strong fit but thin data → worth a manual check.")

# ------------------------------------------------------------------ creator detail
st.divider()
pick = st.selectbox("Creator detail", table["id"].tolist(),
                    format_func=lambda c: f"#{int(res.loc[c, 'rank'])} · {creators.get(c, {}).get('name', c)}")
f = feats[pick]
m = meta(pick)
acc = accounts[accounts.creator_id == pick] if not accounts.empty else pd.DataFrame()
yt_acc = acc[acc.platform == "youtube"].iloc[0] if not acc.empty and (acc.platform == "youtube").any() else None

h1, h2 = st.columns([1, 5])
with h1:
    raw = json.loads(yt_acc["raw_json"]) if yt_acc is not None and yt_acc["raw_json"] else {}
    if raw.get("thumbnail"):
        st.image(raw["thumbnail"], width=88)
with h2:
    name = creators.get(pick, {}).get("name", pick)
    url = yt_acc["url"] if yt_acc is not None else ""
    st.markdown(f"### #{int(res.loc[pick, 'rank'])} [{name}]({url})")
    st.markdown(f"*{m.get('summary', '')}*")
    badges = [f"`{p}: @{h}`" for p, h in zip(acc.platform, acc.handle) if p != "youtube"] if not acc.empty else []
    st.markdown("Linked accounts (from descriptions): " + (" ".join(badges) if badges else "none found"))

evidence = pd.DataFrame(db.query("SELECT feature, content_id, quote, verified FROM evidence "
                                 "WHERE campaign_id=? AND creator_id=?", (campaign_id, pick)))
ex = ranking.explain(res.loc[pick], weights)


def evidence_lines(criterion: str) -> str:
    if evidence.empty:
        return ""
    ev = evidence[(evidence.feature == criterion) & (evidence.verified == 1)].head(2)
    return "".join(f"\n  > “{q}” — [video](https://youtu.be/{v})" for q, v in zip(ev.quote, ev.content_id))


s_col, g_col = st.columns(2)
with s_col:
    st.markdown("#### ✅ Why it ranks high")
    for s in ex["strengths"]:
        st.markdown(f"- **{s['label']}** — better than {s['closeness']:.0%} of the range "
                    f"(weight {s['weight']:.0%}){evidence_lines(s['criterion'])}")
with g_col:
    st.markdown("#### ⚠️ Gaps vs. ideal")
    for g in ex["gaps"]:
        st.markdown(f"- **{g['label']}** — {1 - g['closeness']:.0%} below best "
                    f"(weight {g['weight']:.0%}){evidence_lines(g['criterion'])}")

t1, t2, t3, t4, t5, t6 = st.tabs(["Criteria breakdown", "Evidence", "Analysed videos", "Comments", "Visual (VLM)",
                                  "Discovery"])
with t1:
    used = res.attrs.get("weights", {})
    bd = pd.DataFrame([{"Group": c.group, "Criterion": c.label, "Type": "benefit" if c.kind == "B" else "cost",
                        "Raw value": f.get(c.name),
                        "Closeness to ideal": res.loc[pick, f"close__{c.name}"] if c.name in used else None,
                        "Weight": used.get(c.name, 0.0),
                        "Imputed": bool(res.loc[pick, f"imputed__{c.name}"]) if c.name in used else False,
                        "Source": pipeline._method(c.name)} for c in ranking.CRITERIA])
    st.dataframe(bd, hide_index=True, use_container_width=True, column_config={
        "Closeness to ideal": st.column_config.ProgressColumn(format="%.2f", min_value=0, max_value=1),
        "Weight": st.column_config.NumberColumn(format="%.3f"),
        "Raw value": st.column_config.NumberColumn(format="%.3f")})
    conf = ranking.confidence(f, int(bd["Imputed"].sum()))
    st.caption(f"Data confidence {conf:.2f} ({ranking.confidence_label(conf)}) · "
               f"{int(f.get('n_analyzed_videos', 0))} videos · transcript coverage "
               f"{f.get('transcript_coverage', 0):.0%} · {int(f.get('n_classified_comments', 0))} classified comments · "
               f"{int(f.get('n_visual_images', 0))} thumbnails analysed (VLM)")
with t2:
    if evidence.empty:
        st.info("No evidence recorded.")
    else:
        evidence["verified"] = evidence["verified"].map({1: "✅ verified", 0: "❌ not found in source (discarded)"})
        st.dataframe(evidence, hide_index=True, use_container_width=True)
with t3:
    vids = m.get("videos", [])
    if vids:
        qs = ",".join("?" * len(vids))
        vdf = pd.DataFrame(db.query(f"SELECT id, title, published_at, views, likes, comments, transcript_source "
                                    f"FROM content WHERE id IN ({qs})", vids))
        vdf["url"] = "https://youtu.be/" + vdf["id"]
        st.dataframe(vdf.drop(columns=["id"]), hide_index=True, use_container_width=True,
                     column_config={"url": st.column_config.LinkColumn()})
with t4:
    vids = m.get("videos", [])
    if vids:
        qs = ",".join("?" * len(vids))
        cdf = pd.DataFrame(db.query(f"SELECT label, language, text, likes FROM comments WHERE content_id IN ({qs})",
                                    vids))
        if not cdf.empty:
            a, b = st.columns([1, 2])
            a.bar_chart(cdf["label"].value_counts())
            lab = b.selectbox("Show label", ["purchase_intent", "technical", "question", "meaningful", "spam",
                                             "generic"])
            b.dataframe(cdf[cdf.label == lab][["text", "language", "likes"]].head(20), hide_index=True,
                        use_container_width=True)

# ------------------------------------------------------------------ excluded
excluded = [(creators.get(cid, {}).get("name", cid), reason) for cid, (p, reason) in passed.items() if not p]
if excluded:
    with st.expander(f"Excluded by hard filter ({len(excluded)})"):
        st.dataframe(pd.DataFrame(excluded, columns=["Creator", "Reason"]), hide_index=True,
                     use_container_width=True)
with t5:
    vt = db.query("SELECT video_id, url, tags_json FROM visual_tags WHERE campaign_id=? AND creator_id=?",
                  (campaign_id, pick))
    if not vt:
        st.info("No visual analysis for this creator (VLM disabled, unavailable or failed). "
                "This lowers Data Confidence slightly and the visual criterion is imputed/dropped.")
    else:
        vis = {k: f.get(k) for k in ["visual_product_share", "visual_face_share", "visual_demo_share",
                                     "visual_screen_share", "visual_text_heavy_share", "visual_product_focus_share",
                                     "visual_quality"]}
        st.caption("Creator-level visual features (shares of analysed thumbnails). Only **Product visible in "
                   f"thumbnails** enters TOPSIS, with weight {weights.get('visual_product_share', 0):.1%}; "
                   "the others are descriptive.")
        st.dataframe(pd.DataFrame([vis]), hide_index=True, use_container_width=True)
        cols = st.columns(len(vt))
        for col, r in zip(cols, vt):
            t = json.loads(r["tags_json"])
            col.image(r["url"], use_container_width=True)
            col.caption(f"[video](https://youtu.be/{r['video_id']}) · {t['note']}")
            col.markdown(" ".join(f"`{k}`" for k in ("face_visible", "product_category_visible", "product_demo",
                                                     "benchmark_or_screen_content", "text_heavy") if t.get(k))
                         + f" · focus: {t['focus']} · quality {t['production_quality']}/2")
with t6:
    if discoveries.empty or (discoveries.creator_key == pick).sum() == 0:
        st.info("Found via YouTube search.")
    else:
        st.dataframe(discoveries[discoveries.creator_key == pick][["source", "platform", "handle", "query", "url",
                                                                   "evidence", "method"]],
                     hide_index=True, use_container_width=True, column_config={"url": st.column_config.LinkColumn()})

# ------------------------------------------------------------------ web-only creators
if not discoveries.empty and discoveries.creator_key.str.startswith("web:").any():
    wo = discoveries[discoveries.creator_key.str.startswith("web:")]
    grouped = wo.groupby("creator_key").agg(
        Creator=("handle", "first"), Platforms=("platform", lambda x: ", ".join(sorted(set(x)))),
        Mentions=("url", "count"), Query=("query", "first"), Evidence=("evidence", "first"), Source=("url", "first"))
    with st.expander(f"Web-discovered creators without a matched YouTube channel ({len(grouped)}) — not ranked"):
        st.caption("Found via Tavily web search. No quantitative metrics are available for them (YouTube is the "
                   "metrics source), so they are listed for manual review instead of being ranked.")
        st.dataframe(grouped.sort_values("Mentions", ascending=False), hide_index=True, use_container_width=True,
                     column_config={"Source": st.column_config.LinkColumn()})

# ------------------------------------------------------------------ run statistics
if run_stats:
    with st.expander("Run statistics (external API usage)"):
        st.json(run_stats)
