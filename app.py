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
        seeds = st.text_input("Seed channels (optional, comma-separated @handles)")
        st.caption("A full run uses ~700–1,000 of the 10,000 daily YouTube quota units and takes a few minutes. "
                   "Repeated runs are served from cache.")
        if st.button("Run pipeline", type="primary"):
            db.use(LIVE_DB)
            bar = st.progress(0.0, "Starting")
            try:
                cid = pipeline.run_campaign(brief, seed_handles=[s for s in seeds.split(",") if s.strip()],
                                            progress=lambda m, f: bar.progress(f, m))
                st.session_state["campaign"] = cid
                st.success("Done")
                st.rerun()
            except Exception as e:
                st.error(f"Run failed: {e}")

campaigns = db.query("SELECT id, brief, spec_json, created_at FROM campaigns c WHERE EXISTS "
                     "(SELECT 1 FROM rankings r WHERE r.campaign_id=c.id) ORDER BY created_at DESC")
if not campaigns:
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
                 "Est. cost €": f.get("est_cost_eur"), "German comments": f.get("target_lang_share"),
                 "Other platforms": ", ".join(links_for(cid)), "id": cid})
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
            "Est. cost €": st.column_config.NumberColumn(format="€%d"),
            "German comments": st.column_config.ProgressColumn(format="%.2f", min_value=0, max_value=1),
        })
    st.caption(f"Est. cost assumes €{config.ASSUMED_CPM_EUR:.0f} CPM on median relevant views. "
               "Audience geography is not available from YouTube; 'German comments' is a proxy.")
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

t1, t2, t3, t4 = st.tabs(["Criteria breakdown", "Evidence", "Analysed videos", "Comments"])
with t1:
    bd = pd.DataFrame([{"Group": c.group, "Criterion": c.label, "Type": "benefit" if c.kind == "B" else "cost",
                        "Raw value": f.get(c.name), "Closeness to ideal": res.loc[pick, f"close__{c.name}"],
                        "Weight": weights[c.name],
                        "Imputed": bool(res.loc[pick, f"imputed__{c.name}"])} for c in ranking.CRITERIA])
    st.dataframe(bd, hide_index=True, use_container_width=True, column_config={
        "Closeness to ideal": st.column_config.ProgressColumn(format="%.2f", min_value=0, max_value=1),
        "Weight": st.column_config.NumberColumn(format="%.3f"),
        "Raw value": st.column_config.NumberColumn(format="%.3f")})
    conf = ranking.confidence(f, int(bd["Imputed"].sum()))
    st.caption(f"Data confidence {conf:.2f} ({ranking.confidence_label(conf)}) · "
               f"{int(f.get('n_analyzed_videos', 0))} videos · transcript coverage "
               f"{f.get('transcript_coverage', 0):.0%} · {int(f.get('n_classified_comments', 0))} classified comments")
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
