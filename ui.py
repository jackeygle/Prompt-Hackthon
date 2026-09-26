"""Prenew Creator Intelligence: design tokens, global CSS and reusable render helpers.

Design direction: Prenew-inspired clean Nordic commerce × AI intelligence. The business context is gaming and
technology audiences, but the visual language is deliberately not "gaming themed" (no RGB/neon/esports styling).

All visual values live in TOKENS. They are a PROVISIONAL Prenew-inspired palette (prenew.com could not be
inspected from the build environment): replace the hex values here with the brand's real ones and the
whole app follows.
"""
from html import escape

import streamlit as st

TOKENS = {
    # surfaces
    "bg": "#F5F5F2",            # warm off-white page background
    "surface": "#FFFFFF",       # cards
    "surface_alt": "#F0F0EC",   # subtle panels, bar tracks
    # text
    "text": "#141414",
    "text_muted": "#5C5F64",
    "text_subtle": "#8B8E93",
    # lines
    "border": "#E4E4DF",
    "border_strong": "#CBCBC4",
    # actions
    "cta": "#141414",           # primary buttons (black pill, e-commerce style)
    "cta_hover": "#2E2E2E",
    "cta_text": "#FFFFFF",
    # accent (brand) + semantic
    "accent": "#1C9A58",        # single calm accent: campaign fit, checks, positive signals
    "accent_soft": "#E5F4EB",
    "warn": "#A8620A",
    "warn_soft": "#FDF1DC",
    "neutral_soft": "#EDEDE8",
    "gem": "#3D5AFE",           # hidden gem badge only
    "gem_soft": "#E9EDFF",
    # shape + type
    "radius": "12px",
    "radius_sm": "8px",
    "font": "'Inter', -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif",
}
T = TOKENS

CONFIDENCE_STYLE = {  # text is always shown; colour is secondary
    "High": ("HIGH CONFIDENCE", T["accent"], T["accent_soft"]),
    "Medium": ("MEDIUM CONFIDENCE", T["warn"], T["warn_soft"]),
    "Low": ("LIMITED DATA", T["text_muted"], T["neutral_soft"]),
}


def inject_css() -> None:
    st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
:root {{ --radius: {T['radius']}; }}
html, body, [data-testid="stAppViewContainer"], .stApp {{ background: {T['bg']}; color: {T['text']};
  font-family: {T['font']}; }}
.stApp p, .stApp li, .stApp label, .stApp input, .stApp textarea, .stApp button {{ font-family: {T['font']}; }}
header[data-testid="stHeader"], #MainMenu, footer, [data-testid="stSidebar"], [data-testid="collapsedControl"],
[data-testid="stToolbar"], [data-testid="stDecoration"] {{ display: none !important; }}
.block-container {{ max-width: 1280px; padding: 1.2rem 2rem 4rem; }}
h1, h2, h3, h4 {{ font-family: {T['font']}; color: {T['text']}; letter-spacing: -0.02em; }}
.stApp a, .stApp a:visited {{ color: {T['text']}; }}
.stApp a:hover {{ color: {T['accent']}; }}
hr {{ border-color: {T['border']}; }}

/* buttons: black pill primary, outlined secondary */
.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button {{
  border-radius: 999px; font-weight: 600; padding: 0.45rem 1.1rem; border: 1px solid {T['border_strong']};
  background: {T['surface']}; color: {T['text']}; box-shadow: none; transition: all .15s ease; }}
.stButton > button:hover, .stDownloadButton > button:hover, .stFormSubmitButton > button:hover {{
  border-color: {T['text']}; color: {T['text']}; background: {T['surface']}; }}
.stButton > button[kind^="primary"], .stFormSubmitButton > button[kind^="primary"] {{
  background: {T['cta']}; color: {T['cta_text']}; border-color: {T['cta']}; }}
.stButton > button[kind^="primary"]:hover, .stFormSubmitButton > button[kind^="primary"]:hover {{
  background: {T['cta_hover']}; color: {T['cta_text']}; }}
.stButton > button:focus-visible {{ outline: 2px solid {T['accent']}; outline-offset: 2px; }}

/* inputs */
.stTextInput input, .stTextArea textarea, .stNumberInput input, div[data-baseweb="select"] > div {{
  border-radius: {T['radius_sm']} !important; background: {T['surface']} !important; }}

/* bordered containers = cards */
div[data-testid="stVerticalBlockBorderWrapper"]:has(> div > div[class*="st-key-card"]),
div[class*="st-key-card"] {{ background: {T['surface']}; border-radius: {T['radius']}; }}
div[class*="st-key-card"] {{ border: 1px solid {T['border']}; padding: 20px 20px 20px;
  transition: box-shadow .15s ease, border-color .15s ease; }}
div[class*="st-key-card"]:hover {{ border-color: {T['border_strong']}; box-shadow: 0 6px 24px rgba(20,20,20,.06); }}
div[class*="st-key-panel"] {{ background: {T['surface']}; border: 1px solid {T['border']};
  border-radius: {T['radius']}; padding: 22px 24px; }}

/* while discovery runs, hide the stale form under the live stage list */
div[class*="st-key-panel-campaign"] [data-stale="true"] {{ display: none; }}

/* nav */
div[class*="st-key-nav-"] button {{ border: none !important; background: transparent !important;
  color: {T['text_muted']} !important; font-weight: 600; border-radius: 999px; }}
div[class*="st-key-nav-"] button:hover {{ color: {T['text']} !important; background: {T['neutral_soft']} !important; }}
div[class*="st-key-navon-"] button {{ background: {T['text']} !important; color: {T['cta_text']} !important;
  border: none !important; font-weight: 600; border-radius: 999px; }}

/* expanders / tabs */
div[data-testid="stExpander"] details {{ border: 1px solid {T['border']}; border-radius: {T['radius']};
  background: {T['surface']}; }}
.stTabs [data-baseweb="tab"] {{ font-weight: 600; }}

/* custom components */
.pn-brand {{ display:flex; align-items:baseline; gap:10px; }}
.pn-brand .logo {{ font-weight:700; font-size:1.45rem; letter-spacing:-0.04em; }}
.pn-brand .logo span {{ color:{T['accent']}; }}
.pn-brand .product {{ font-size:.82rem; color:{T['text_muted']}; font-weight:600; text-transform:uppercase;
  letter-spacing:.08em; }}
.pn-kicker {{ font-size:.75rem; font-weight:700; letter-spacing:.12em; text-transform:uppercase;
  color:{T['text_muted']}; margin-bottom:6px; }}
.pn-hero h1 {{ font-size:2.9rem; line-height:1.08; font-weight:700; letter-spacing:-0.03em; margin:.2rem 0 .9rem; }}
.pn-hero p {{ font-size:1.1rem; color:{T['text_muted']}; max-width:620px; }}
.pn-muted {{ color:{T['text_muted']}; }}
.pn-subtle {{ color:{T['text_subtle']}; font-size:.85rem; }}
.pn-chip {{ display:inline-flex; align-items:center; gap:6px; padding:4px 11px; border-radius:999px;
  background:{T['surface']}; border:1px solid {T['border']}; font-size:.8rem; font-weight:600; color:{T['text']};
  margin:0 6px 6px 0; white-space:nowrap; }}
.pn-chip.dark {{ background:{T['text']}; color:{T['cta_text']}; border-color:{T['text']}; }}
.pn-badge {{ display:inline-flex; align-items:center; gap:6px; padding:3px 9px; border-radius:6px;
  font-size:.7rem; font-weight:700; letter-spacing:.06em; white-space:nowrap; }}
.pn-badge .dot {{ width:7px; height:7px; border-radius:50%; display:inline-block; }}
.pn-avatar {{ position:relative; overflow:hidden; width:52px; height:52px; border-radius:50%;
  background-color:{T['neutral_soft']}; display:flex; align-items:center; justify-content:center; flex:none;
  font-weight:700; color:{T['text_muted']}; border:1px solid {T['border']}; }}
.pn-avatar .img {{ position:absolute; inset:0; background-size:cover; background-position:center; }}
.pn-avatar.lg {{ width:84px; height:84px; font-size:1.6rem; }}
.pn-card-head {{ display:flex; gap:12px; align-items:flex-start; }}
.pn-card-head .who {{ flex:1; min-width:0; }}
.pn-card-head .name {{ font-weight:650; font-size:1.08rem; line-height:1.2; overflow:hidden; text-overflow:ellipsis;
  white-space:nowrap; }}
.pn-card-head .meta {{ color:{T['text_muted']}; font-size:.82rem; margin-top:2px; }}
.pn-rank {{ font-size:.75rem; font-weight:700; color:{T['text_subtle']}; letter-spacing:.06em; }}
.pn-score {{ text-align:right; line-height:1; }}
.pn-score .num {{ font-size:2.3rem; font-weight:700; letter-spacing:-0.03em; }}
.pn-score .num small {{ font-size:.9rem; color:{T['text_subtle']}; font-weight:600; }}
.pn-score .lbl {{ font-size:.66rem; font-weight:700; letter-spacing:.1em; color:{T['text_muted']};
  text-transform:uppercase; margin-top:4px; }}
.pn-score.xl .num {{ font-size:3.6rem; }}
.pn-bar {{ display:grid; grid-template-columns: 1fr auto; gap:2px 10px; align-items:center; margin:7px 0; }}
.pn-bar .l {{ font-size:.72rem; font-weight:700; letter-spacing:.07em; text-transform:uppercase;
  color:{T['text_muted']}; }}
.pn-bar .v {{ font-size:.85rem; font-weight:700; text-align:right; }}
.pn-bar .track {{ grid-column: 1 / 3; height:6px; border-radius:6px; background:{T['surface_alt']}; overflow:hidden; }}
.pn-bar .fill {{ height:100%; border-radius:6px; background:{T['text']}; }}
.pn-bar.accent .fill {{ background:{T['accent']}; }}
.pn-stats {{ display:grid; grid-template-columns:1fr 1fr; gap:8px; margin:12px 0 6px; }}
.pn-stats.three {{ grid-template-columns:1fr 1fr 1fr; gap:6px; margin:12px 0 10px; }}
.pn-stats.three .pn-stat .val {{ font-size:.98rem; }}
.pn-stat {{ background:{T['surface_alt']}; border-radius:{T['radius_sm']}; padding:8px 10px; }}
.pn-stat .k {{ font-size:.68rem; color:{T['text_muted']}; font-weight:600; text-transform:uppercase; letter-spacing:.06em; }}
.pn-stat .val {{ font-size:1rem; font-weight:700; }}
.pn-reasons {{ list-style:none; padding:0; margin:10px 0 4px; }}
.pn-reasons li {{ font-size:.86rem; margin:4px 0; display:flex; gap:8px; }}
.pn-reasons li .ic {{ color:{T['accent']}; font-weight:800; width:14px; flex:none; text-align:center; }}
.pn-reasons li.gap .ic {{ color:{T['warn']}; }}
.pn-divider {{ height:1px; background:{T['border']}; margin:12px 0; }}
.pn-ev {{ background:{T['surface']}; border:1px solid {T['border']}; border-radius:{T['radius']}; padding:16px 18px;
  height:100%; display:flex; flex-direction:column; gap:6px; }}
.pn-ev .kind {{ font-size:.68rem; font-weight:700; letter-spacing:.1em; text-transform:uppercase; color:{T['accent']}; }}
.pn-ev .quote {{ font-size:1rem; font-weight:500; line-height:1.35; }}
.pn-ev .src {{ font-size:.8rem; color:{T['text_muted']}; }}
.pn-ev .tags {{ margin-top:auto; padding-top:6px; }}
.pn-ev img {{ width:100%; border-radius:{T['radius_sm']}; aspect-ratio:16/9; object-fit:cover;
  background:{T['surface_alt']}; }}
.pn-empty {{ text-align:center; padding:48px 24px; border:1px dashed {T['border_strong']}; border-radius:{T['radius']};
  background:{T['surface']}; }}
.pn-empty .t {{ font-weight:700; font-size:1.15rem; }}
.pn-empty .b {{ color:{T['text_muted']}; margin-top:6px; }}
.pn-stages {{ list-style:none; padding:0; margin:8px 0; }}
.pn-stages li {{ display:flex; gap:12px; align-items:center; padding:9px 0; font-weight:600; color:{T['text_subtle']};
  border-bottom:1px solid {T['border']}; }}
.pn-stages li:last-child {{ border-bottom:none; }}
.pn-stages li .st {{ width:22px; height:22px; border-radius:50%; display:flex; align-items:center; justify-content:center;
  font-size:.75rem; border:2px solid {T['border_strong']}; flex:none; }}
.pn-stages li.done {{ color:{T['text']}; }}
.pn-stages li.done .st {{ background:{T['accent']}; border-color:{T['accent']}; color:#fff; }}
.pn-stages li.active {{ color:{T['text']}; }}
.pn-stages li.active .st {{ border-color:{T['text']}; border-top-color:transparent; animation: pcspin .9s linear infinite; }}
.pn-stages li .detail {{ margin-left:auto; font-weight:500; font-size:.82rem; color:{T['text_muted']}; }}
@keyframes pcspin {{ to {{ transform: rotate(360deg); }} }}
.pn-steps {{ display:flex; gap:18px; font-size:.8rem; font-weight:700; color:{T['text_subtle']}; margin-bottom:10px; }}
.pn-steps .on {{ color:{T['text']}; }}
.pn-steps .n {{ display:inline-flex; width:20px; height:20px; border-radius:50%; align-items:center; justify-content:center;
  background:{T['neutral_soft']}; margin-right:6px; font-size:.72rem; }}
.pn-steps .on .n {{ background:{T['text']}; color:#fff; }}
.pn-kv {{ display:grid; grid-template-columns: 150px 1fr; row-gap:10px; font-size:.95rem; }}
.pn-kv .k {{ color:{T['text_muted']}; font-weight:600; }}
.pn-kv .v {{ font-weight:600; }}
.pn-note {{ background:{T['surface_alt']}; border-radius:{T['radius_sm']}; padding:10px 12px; font-size:.84rem;
  color:{T['text_muted']}; }}
@media (max-width: 1100px) {{ .pn-hero h1 {{ font-size:2.3rem; }} .block-container {{ padding: 1rem 1rem 3rem; }} }}
</style>""", unsafe_allow_html=True)


# ------------------------------------------------------------------ formatting helpers
def esc(x) -> str:
    return escape(str(x if x is not None else ""))


def fmt_count(n) -> str:
    if n is None:
        return "–"
    n = float(n)
    if n >= 1e6:
        return f"{n / 1e6:.1f}M".replace(".0M", "M")
    if n >= 1e3:
        return f"{n / 1e3:.0f}K" if n >= 1e4 else f"{n / 1e3:.1f}K".replace(".0K", "K")
    return f"{n:.0f}"


def pct(x) -> str:
    return "–" if x is None else f"{x * 100:.0f}%"


def pct1(x) -> str:
    return "–" if x is None else f"{x * 100:.1f}%"


def key(s: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in s)


def html(s: str) -> None:
    st.markdown(s, unsafe_allow_html=True)


# ------------------------------------------------------------------ small components (return HTML strings)
def avatar(name: str, url: str | None, large: bool = False) -> str:
    """Initials underneath, channel image on top: if the image cannot load, the initials stay visible."""
    initials = "".join(w[0] for w in name.split()[:2] if w[:1].isalnum()).upper() or "?"
    img = f'<div class="img" style="background-image:url(\'{esc(url)}\')"></div>' if url else ""
    return f'<div class="pn-avatar{" lg" if large else ""}">{esc(initials)}{img}</div>'


def chip(text: str, dark: bool = False) -> str:
    return f'<span class="pn-chip{" dark" if dark else ""}">{esc(text)}</span>'


def confidence_badge(conf: float, label: str, show_pct: bool = True) -> str:
    text, fg, bg = CONFIDENCE_STYLE[label]
    val = f" · {conf * 100:.0f}%" if show_pct else ""
    return (f'<span class="pn-badge" style="color:{fg};background:{bg}" title="Data confidence: how much data '
            f'backs this score. Separate from the campaign score."><span class="dot" style="background:{fg}"></span>'
            f'{text}{val}</span>')


def gem_badge() -> str:
    return (f'<span class="pn-badge" style="color:{T["gem"]};background:{T["gem_soft"]}" title="Hidden gem: '
            f'campaign fit ≥ 75, campaign score at or above the median, and fewer subscribers than the median '
            f'ranked creator">◆ HIDDEN GEM</span>')


def score_block(score: int, label: str = "Campaign score", xl: bool = False) -> str:
    """The TOPSIS score: relative to the creators in this campaign (tooltip says so)."""
    return (f'<div class="pn-score{" xl" if xl else ""}" title="Campaign score: ranking against the other creators '
            f'in this campaign (AHP-weighted TOPSIS)"><div class="num">{score}<small>/100</small></div>'
            f'<div class="lbl">{esc(label)}</div></div>')


def metric_bar(label: str, value: float | None, accent: bool = False, display: str | None = None) -> str:
    v = 0 if value is None else max(0.0, min(100.0, value))
    shown = display if display is not None else ("–" if value is None else f"{value:.0f}")
    return (f'<div class="pn-bar{" accent" if accent else ""}"><span class="l">{esc(label)}</span>'
            f'<span class="v">{esc(shown)}</span><div class="track"><div class="fill" style="width:{v:.0f}%"></div>'
            f'</div></div>')


def stat_tile(k: str, v: str) -> str:
    """Small key figure (label + value), used on creator cards and the analysis header."""
    return f'<div class="pn-stat"><div class="k">{esc(k)}</div><div class="val">{esc(v)}</div></div>'


def reasons_list(good: list[str], gaps: list[str] | None = None) -> str:
    items = "".join(f'<li><span class="ic">✓</span><span>{esc(r)}</span></li>' for r in good)
    items += "".join(f'<li class="gap"><span class="ic">!</span><span>{esc(r)}</span></li>' for r in gaps or [])
    return f'<ul class="pn-reasons">{items}</ul>'


def evidence_card(kind: str, quote: str, source: str, link: str | None = None, tags: list[str] | None = None,
                  image: str | None = None) -> str:
    img = f'<img src="{esc(image)}" alt="">' if image else ""
    src = f'<a href="{esc(link)}" target="_blank">{esc(source)} ↗</a>' if link else esc(source)
    tg = "".join(chip(t) for t in tags or [])
    return (f'<div class="pn-ev">{img}<div class="kind">{esc(kind)}</div><div class="quote">{esc(quote)}</div>'
            f'<div class="src">{src}</div><div class="tags">{tg}</div></div>')


def empty_state(title: str, body: str) -> None:
    html(f'<div class="pn-empty"><div class="t">{esc(title)}</div><div class="b">{esc(body)}</div></div>')


def stage_list(stages: list[str], current: int, detail: str = "") -> str:
    out = []
    for i, s in enumerate(stages):
        cls, mark = ("done", "✓") if i < current else (("active", "") if i == current else ("", ""))
        d = f'<span class="detail">{esc(detail)}</span>' if i == current and detail else ""
        out.append(f'<li class="{cls}"><span class="st">{mark}</span>{esc(s)}{d}</li>')
    return f'<ul class="pn-stages">{"".join(out)}</ul>'


def steps(active: int) -> str:
    names = ["Describe campaign", "Confirm details", "Discover creators"]
    return '<div class="pn-steps">' + "".join(
        f'<span class="{"on" if i <= active else ""}"><span class="n">{i + 1}</span>{n}</span>'
        for i, n in enumerate(names)) + "</div>"
