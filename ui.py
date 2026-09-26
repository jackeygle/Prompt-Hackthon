"""Prenew Creator Intelligence: design tokens, global CSS and reusable render helpers.

Design direction: Prenew-inspired clean Nordic commerce × AI intelligence. The business context is gaming and
technology audiences, but the visual language is deliberately not "gaming themed" (no RGB/neon/esports styling).

Brand colors, typography and the official header logo were inspected on prenew.com/en-FI.
The shared tokens apply across Campaign, Creators, Analysis and Shortlist.
"""
from html import escape
from pathlib import Path

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

# Verified against prenew.com/en-FI and its official stylesheet, 2026-09-26.
TOKENS.update(bg="#F8F8FF", surface_alt="#EEEEF2", text="#1D1D35", text_muted="#4A4A5D",
              text_subtle="#777786", border="#DDDDE4", border_strong="#A9AABC",
              cta="#9DF69A", cta_hover="#B1F8AE", cta_text="#1D1D35",
              accent="#256F50", accent_soft="#EBFDEB", gem="#0A74FF", gem_soft="#E6F1FF",
              radius="24px", radius_sm="12px",
              font="'Titillium Web', 'Segoe UI', Helvetica, Arial, sans-serif")


def brand_header() -> str:
    logo = (Path(__file__).parent / "assets" / "prenew-logo.svg").read_text()
    return ('<div class="pn-brand"><span class="pn-official-logo" role="img" aria-label="Prenew">'
            + logo + '</span><span class="product">Creator Intelligence</span></div>')

CONFIDENCE_STYLE = {  # text is always shown; colour is secondary
    "High": ("HIGH CONFIDENCE", T["accent"], T["accent_soft"]),
    "Medium": ("MEDIUM CONFIDENCE", T["warn"], T["warn_soft"]),
    "Low": ("LIMITED DATA", T["text_muted"], T["neutral_soft"]),
}


def inject_css() -> None:
    st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Sora:wght@400;600;700;800&family=Titillium+Web:wght@400;600;700&family=JetBrains+Mono:wght@500;700&display=swap');
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
/* Official Prenew palette and commerce-inspired hierarchy. */
h1, h2, h3, h4, .pn-score .num, .pn-card-head .name {{ font-family:'Sora', sans-serif; }}
.pn-brand {{ gap:16px; padding:18px 22px; background:#256F50; border-radius:16px; width:fit-content; }}
.pn-official-logo {{ display:flex; color:#9DF69A; }}
.pn-brand .product {{ color:#fff; font-size:.72rem; border-left:1px solid #7CA996; padding-left:16px; }}
.pn-hero {{ background:#256F50; border-radius:24px; padding:38px 32px; min-height:400px; }}
.pn-hero h1 {{ font-family:'Sora',sans-serif; color:#fff; font-size:clamp(2rem,3.4vw,3.3rem); font-weight:800; line-height:1.15; }}
.pn-hero .pn-kicker {{ color:#9DF69A; }}
.pn-hero p {{ color:#EBFDEB; font-size:1.15rem; line-height:1.6; }}
.pn-hero-proof {{ display:flex; flex-wrap:wrap; gap:10px 18px; margin-top:34px; padding-top:20px;
 border-top:1px solid #518C73; color:#9DF69A; font-weight:600; }}
.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button {{ border-radius:12px; min-height:44px; }}
.stButton > button[kind="tertiary"] {{ border:none; background:transparent; color:#4A4A5D; padding:4px 0; min-height:32px; }}
.stButton > button[kind="tertiary"]:hover {{ color:#BF000F; text-decoration:underline; }}
div[class*="st-key-card"] {{ border-top:3px solid #A8C5B9; }}
div[class*="st-key-card"]:hover {{ border-color:#256F50; box-shadow:0 8px 22px #1d1d3510; }}
.pn-score {{ background:#EBFDEB; border:1px solid #A8C5B9; padding:12px; border-radius:16px; color:#256F50; }}
.pn-score .num {{ font-size:2rem; }}
.pn-rank {{ color:#256F50; }}
.pn-stat {{ border:1px solid #EEEef2; background:#F8F8FF; }}
.pn-stat .val {{ font-variant-numeric:tabular-nums; }}
.pn-empty {{ border-style:solid; border-top:4px solid #256F50; background:#EBFDEB; }}
.pn-stages li.active {{ background:#EBFDEB; border-radius:12px; padding:12px; }}
.pn-steps {{ flex-wrap:wrap; gap:8px 14px; }}
.pn-steps .on .n {{ background:#256F50; color:white; }}
.pn-chip.dark {{ background:#256F50; color:white; border-color:#256F50; }}
.pn-stages li.done .st {{ color:white; }}
.pn-card-head .name {{ white-space:normal; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; }}
.pn-bar.na .v {{ color:{T['text_subtle']}; font-weight:600; font-size:.75rem; }}
/* top bar: logo + cart-style shortlist; back link to the parent level on its own line above the title */
div[class*="st-key-back"] {{ margin-top:14px; }}
div[class*="st-key-back"] .stButton > button[kind="tertiary"] {{ font-weight:600; min-height:28px; padding:2px 0;
  max-width:520px; }}
div[class*="st-key-back"] .stButton > button p {{ white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
div[class*="st-key-back"] .stButton > button[kind="tertiary"]:hover {{ text-decoration:underline; }}
div[class*="st-key-cart"] button {{ border-radius:999px !important; }}
div[class*="st-key-cart-on"] button {{ border-color:#256F50 !important; background:#EBFDEB !important; color:#256F50 !important; }}
/* deep-green top band (Prenew hero): full-bleed behind nav + page title, light text inside */
div[class*="st-key-band"] {{ background:#256F50; box-shadow:0 0 0 100vmax #256F50;
  clip-path:inset(-100vmax -100vmax 0 -100vmax); padding:4px 0 26px; margin-bottom:26px; color:#fff; }}
div[class*="st-key-band"] .pn-brand {{ background:transparent; padding:10px 0; }}
div[class*="st-key-band"] .pn-brand .product {{ border-left-color:#518C73; }}
div[class*="st-key-band"] h2 {{ color:#fff !important; }}
div[class*="st-key-band"] .pn-kicker {{ color:#9DF69A; }}
div[class*="st-key-band"] .pn-subtle, div[class*="st-key-band"] .pn-muted {{ color:#CFE9D6; }}
div[class*="st-key-band"] .pn-chip {{ background:rgba(255,255,255,.08); border-color:rgba(255,255,255,.28); color:#fff; }}
div[class*="st-key-band"] .pn-chip.dark {{ background:#9DF69A; border-color:#9DF69A; color:#1D1D35; }}
div[class*="st-key-band"] .pn-hero-slim {{ background:transparent; padding:6px 0 0; margin:0; }}
div[class*="st-key-band"] .stButton > button[kind="tertiary"] {{ color:#CFE9D6 !important; }}
div[class*="st-key-band"] .stButton > button[kind="tertiary"]:hover {{ color:#9DF69A !important; }}
div[class*="st-key-band"] .pn-band-title {{ font-family:'Sora',sans-serif; font-weight:700; color:#fff; line-height:1.2;
  font-size:clamp(1.6rem,2.6vw,2.3rem); letter-spacing:-.02em; margin:2px 0 12px; }}
div[class*="st-key-band"] .pn-band-meta {{ display:flex; flex-wrap:wrap; align-items:center; gap:6px 0; }}
div[class*="st-key-band"] div[class*="st-key-cart"] button {{ background:transparent !important; color:#fff !important;
  border-color:rgba(255,255,255,.45) !important; }}
div[class*="st-key-band"] div[class*="st-key-cart"] button:hover {{ border-color:#9DF69A !important; color:#9DF69A !important; }}
div[class*="st-key-band"] div[class*="st-key-cart-on"] button {{ background:#9DF69A !important; color:#1D1D35 !important;
  border-color:#9DF69A !important; }}
/* discover: one-line verdict above the cards */
.pn-pick .k {{ font-size:.7rem; font-weight:700; letter-spacing:.1em; text-transform:uppercase; color:{T['accent']}; }}
.pn-pick .n {{ font-family:'Sora',sans-serif; font-weight:700; font-size:1.15rem; margin:2px 0; }}
.pn-pick .d {{ font-size:.85rem; color:{T['text_muted']}; }}
div[class*="st-key-panel-pick"] {{ padding:16px 18px; border-left:4px solid {T['accent']}; }}
/* campaign page once reports exist: the pitch shrinks to one line */
.pn-hero-slim {{ background:#256F50; border-radius:16px; padding:18px 24px; display:flex; flex-wrap:wrap;
  align-items:baseline; gap:6px 18px; margin-bottom:18px; }}
.pn-hero-slim .t {{ font-family:'Sora',sans-serif; color:#fff; font-weight:700; font-size:1.3rem; }}
.pn-hero-slim .p {{ color:#9DF69A; font-weight:600; font-size:.9rem; }}
/* narrow cards (3 columns on a small screen): score moves under the name instead of squeezing it */
div[class*="st-key-card-cr-"] {{ container-type:inline-size; }}  /* creator cards only */
@container (max-width: 340px) {{
 .pn-card-head {{ flex-wrap:wrap; }}
 .pn-card-head .who {{ min-width:120px; }}
 .pn-score {{ order:3; width:100%; display:flex; align-items:baseline; gap:8px; text-align:left; padding:8px 12px; }}
 .pn-score .lbl {{ margin-top:0; }}
 .pn-stats.three {{ grid-template-columns:repeat(2,minmax(0,1fr)); }}
 .pn-stat .k {{ font-size:.6rem; letter-spacing:.02em; }}
 [data-testid="stHorizontalBlock"] {{ flex-wrap:wrap; }}
 [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {{ min-width:100%; }}
}}
.pn-bar.na .track {{ background:repeating-linear-gradient(90deg, {T['surface_alt']} 0 6px, transparent 6px 10px); }}
@media (max-width:640px) {{
 .pn-brand {{ padding:12px; flex-wrap:wrap; }}
 .pn-hero {{ padding:26px 22px; min-height:0; }}
 .pn-stats.three {{ grid-template-columns:repeat(2,minmax(0,1fr)); }}
 .pn-card-head {{ flex-wrap:wrap; }}
}}
@media (prefers-reduced-motion:reduce) {{ * {{ transition:none !important; animation:none !important; }} }}
/* ===== Gaming-PC identity, built only from Prenew's palette: perforated case mesh, spec-sheet numerals,
   dark "case" card heads, one lime accent strip. No RGB, glow or animation beyond hover. ===== */
:root {{ --pn-green:#256F50; --pn-lime:#9DF69A; --pn-navy:#1D1D35;
  --pn-mono:'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }}
/* spec-sheet numerals: every figure a marketer compares */
.pn-score .num, .pn-stat .val, .pn-bar .v, .pn-rank, .pn-row-num b, .pn-row-time, .pn-row-spec,
.pn-list-head .n, .pn-hero-slim .p {{ font-family:var(--pn-mono); font-variant-numeric:tabular-nums; }}
.pn-score .num {{ letter-spacing:-.05em; }}
/* top band: case-mesh perforation fading in from the right + lime accent strip at the bottom */
div[class*="st-key-band"] {{ position:relative; }}
div[class*="st-key-band"]::before {{ content:""; position:absolute; top:0; bottom:0; left:-100vmax; right:-100vmax;
  pointer-events:none; animation:pnmesh 70s linear infinite;
  background-image:radial-gradient(rgba(157,246,154,.22) 1.3px, transparent 1.6px);
  background-size:13px 13px; -webkit-mask-image:linear-gradient(90deg, transparent 52%, #000 88%);
  mask-image:linear-gradient(90deg, transparent 52%, #000 88%); }}
div[class*="st-key-band"]::after {{ content:""; position:absolute; left:-100vmax; right:-100vmax; bottom:0; height:4px;
  background:var(--pn-lime); pointer-events:none; }}
div[class*="st-key-band"] > div {{ position:relative; z-index:1; }}
/* campaign home: pitch left, the brief box is the hero on the right */
div[class*="st-key-band"] .pn-hero-slim {{ display:block; padding:18px 0 8px; }}
div[class*="st-key-band"] .pn-hero-slim .t {{ font-size:clamp(1.8rem,2.8vw,2.4rem); line-height:1.12; font-weight:800;
  letter-spacing:-.03em; margin:6px 0 8px; }}
div[class*="st-key-band"] .pn-hero-slim .p {{ color:var(--pn-lime); font-size:.82rem; letter-spacing:.02em; }}
div[class*="st-key-band"] .pn-hero-slim .p span {{ opacity:.55; margin:0 4px; }}
/* home brief = search bar: no card around it, one rounded input + the CTA on the same row */
div[class*="st-key-briefbar"] div[class*="st-key-panel-campaign"] {{ background:transparent; border:none;
  box-shadow:none; padding:0; margin:6px 0 4px; max-width:980px; }}
div[class*="st-key-briefbar"] .stTextArea textarea {{ border-radius:16px !important; border:none !important;
  font-size:1.05rem; padding:14px 18px; min-height:68px; box-shadow:0 6px 18px rgba(10,30,20,.18); }}
div[class*="st-key-briefbar"] .stTextArea [data-baseweb="textarea"] {{ border:none !important; border-radius:16px !important;
  background:transparent !important; }}
div[class*="st-key-briefbar"] .stFormSubmitButton > button {{ min-height:68px; border-radius:16px; font-size:1.02rem; }}
.pn-brief-ex {{ color:#CFE9D6; font-size:.82rem; margin:2px 2px 0; }}
div[class*="st-key-band"] div[class*="st-key-panel-campaign"] .pn-kicker {{ color:var(--pn-green); }}
div[class*="st-key-band"] div[class*="st-key-panel-campaign"] .pn-subtle {{ color:{T['text_subtle']}; }}
.stFormSubmitButton > button[kind^="primary"] p, .stButton > button[kind^="primary"] p {{ font-weight:700; }}
.stFormSubmitButton > button[kind^="primary"], .stButton > button[kind^="primary"] {{
  box-shadow:inset 0 -2px 0 rgba(37,111,80,.35); }}
/* report history: compact list, one row per report */
.pn-list-head {{ display:flex; align-items:baseline; gap:12px; margin:6px 0 10px; }}
.pn-list-head .t {{ font-family:'Sora',sans-serif; font-weight:700; font-size:1.3rem; color:var(--pn-navy); }}
.pn-list-head .n {{ font-size:.78rem; color:{T['text_subtle']}; }}
div[class*="st-key-reportlist"] {{ background:#fff; border:1px solid {T['border']}; border-radius:18px;
  gap:0 !important; padding:4px 0; }}
div[class*="st-key-row-camp-"] {{ padding:12px 10px 12px 20px; border-bottom:1px solid {T['surface_alt']};
  transition:background .12s ease; border-left:3px solid transparent; }}
div[class*="st-key-row-camp-"]:last-child {{ border-bottom:none; }}
div[class*="st-key-row-camp-"]:hover {{ background:#F4FEF4; border-left-color:var(--pn-lime); }}
div[class*="st-key-row-camp-"] {{ flex-wrap:nowrap !important; }}
div[class*="st-key-row-camp-"] > div {{ flex:0 0 auto !important; width:auto !important; }}
div[class*="st-key-row-camp-"] > div:first-child {{ flex:1 1 auto !important; min-width:0; }}
.pn-row-main .nm {{ font-weight:700; color:var(--pn-navy); font-size:1rem; }}
.pn-row-main .br {{ font-size:.82rem; color:{T['text_subtle']}; white-space:nowrap; overflow:hidden;
  text-overflow:ellipsis; max-width:520px; }}
.pn-row-spec {{ width:230px; font-size:.78rem; color:{T['text_muted']}; white-space:nowrap; overflow:hidden;
  text-overflow:ellipsis; }}
.pn-row-num {{ width:108px; font-size:.82rem; color:{T['text_muted']}; }}
.pn-row-num b {{ color:var(--pn-navy); font-size:.95rem; }}
.pn-row-time {{ width:118px; font-size:.78rem; color:{T['text_subtle']}; }}
div[class*="st-key-open_"] button {{ color:var(--pn-green) !important; font-weight:700; min-height:36px; }}
div[class*="st-key-open_"] button:hover {{ color:var(--pn-navy) !important; text-decoration:none !important; }}
div[class*="st-key-delete_"] button {{ color:#A9AABC !important; min-height:36px; padding:4px 8px !important;
  border-radius:10px !important; }}
div[class*="st-key-delete_"] button:hover {{ color:#BF000F !important; background:#FDECEC !important;
  text-decoration:none !important; }}
/* creator card: dark "case" head with perforation, lime benchmark-style score, light spec body */
div[class*="st-key-card-cr-"] {{ padding-top:0 !important; overflow:hidden; border-top:1px solid {T['border']} !important;
  transition:transform .15s ease, box-shadow .15s ease, border-color .15s ease; }}
div[class*="st-key-card-cr-"]:hover {{ transform:translateY(-2px); border-color:var(--pn-green) !important;
  box-shadow:0 14px 30px rgba(29,29,53,.12); }}
div[class*="st-key-card-cr-"] .pn-card-head {{ margin:0 -20px 4px; padding:18px 20px 16px; color:var(--pn-navy);
  background-color:#F1FBF3; background-image:radial-gradient(rgba(37,111,80,.10) 1.2px, transparent 1.5px);
  background-size:11px 11px; background-position:right top; border-bottom:3px solid var(--pn-lime); }}
div[class*="st-key-card-cr-"] .pn-card-head .name {{ color:var(--pn-navy); }}
div[class*="st-key-card-cr-"] .pn-card-head .meta {{ color:{T['text_muted']}; }}
div[class*="st-key-card-cr-"] .pn-rank {{ color:var(--pn-green); font-size:.68rem; white-space:nowrap; overflow:hidden;
  text-overflow:ellipsis; margin-bottom:3px; }}
div[class*="st-key-card-cr-"] .pn-card-head .name {{ font-size:1.05rem; }}
div[class*="st-key-card-cr-"] .pn-avatar {{ border:2px solid #A8C5B9; background-color:#fff; color:var(--pn-green); }}
div[class*="st-key-card-cr-"] .pn-score {{ background:var(--pn-lime); border:none; color:var(--pn-navy);
  padding:8px 10px; border-radius:12px; flex:none; }}
div[class*="st-key-card-cr-"] .pn-score .num {{ font-size:1.7rem; }}
div[class*="st-key-card-cr-"] .pn-score .lbl {{ font-size:.58rem; }}
div[class*="st-key-card-cr-"] .pn-score .num small {{ color:var(--pn-green); }}
div[class*="st-key-card-cr-"] .pn-score .lbl {{ color:var(--pn-navy); opacity:.75; }}
/* ===== step 2: icons, tiers, boot log ===== */
.pn-ico {{ display:inline-block; vertical-align:-2px; flex:none; }}
.pn-pchip {{ gap:6px; }}
.pn-stat .k .pn-ico {{ margin-right:4px; opacity:.8; }}
.pn-rank .pn-ico {{ margin:0 3px 0 1px; }}
.pn-scorewrap {{ display:flex; align-items:stretch; gap:6px; flex:none; }}
.pn-tier {{ display:inline-flex; align-items:center; justify-content:center; width:30px; min-height:30px;
  border-radius:9px; font-family:var(--pn-mono); font-weight:700; font-size:1.05rem; line-height:1; cursor:help; }}
.pn-tier.lg {{ width:46px; font-size:1.6rem; border-radius:12px; }}
.pn-tier.t-s {{ background:var(--pn-lime); color:var(--pn-navy); box-shadow:inset 0 0 0 2px #7BE278; }}
.pn-tier.t-a {{ background:var(--pn-green); color:#fff; }}
.pn-tier.t-b {{ background:#fff; color:var(--pn-green); box-shadow:inset 0 0 0 2px var(--pn-green); }}
.pn-tier.t-c {{ background:#fff; color:#777786; box-shadow:inset 0 0 0 2px #A9AABC; }}

.pn-boot {{ background:#F1FBF3; border:1px solid #CFE9D6; border-radius:16px; padding:18px 20px; font-family:var(--pn-mono);
  font-size:.86rem; color:{T['text_muted']}; background-image:radial-gradient(rgba(37,111,80,.08) 1.1px, transparent 1.4px);
  background-size:11px 11px; border-bottom:3px solid var(--pn-lime); }}
.pn-boot .hd {{ color:var(--pn-green); font-weight:700; letter-spacing:.06em; font-size:.74rem; margin-bottom:10px; }}
.pn-boot .ln {{ display:flex; gap:12px; align-items:baseline; padding:4px 0; white-space:nowrap; }}
.pn-boot .tag {{ flex:none; width:52px; white-space:pre; }}
.pn-boot .ok .tag {{ color:var(--pn-green); font-weight:700; }}
.pn-boot .ok .s {{ color:var(--pn-navy); }}
.pn-boot .run .tag, .pn-boot .run .s {{ color:var(--pn-navy); font-weight:700; }}
.pn-boot .run {{ background:#E2F8E4; border-radius:8px; margin:0 -8px; padding:4px 8px; }}
.pn-boot .wait {{ color:#A9AABC; }}
.pn-boot .d {{ color:{T['text_muted']}; overflow:hidden; text-overflow:ellipsis; }}
.pn-boot .cur {{ color:var(--pn-green); animation:pnblink 1s steps(1) infinite; }}
@keyframes pnblink {{ 50% {{ opacity:0; }} }}
/* ===== home: centered search hero, bar with the CTA inside, report card overlapping the band ===== */
div[class*="st-key-band"] .pn-hero-slim.pn-center {{ text-align:center; padding:34px 0 14px; }}
div[class*="st-key-band"] .pn-hero-slim.pn-center .t {{ margin:0 auto 10px; max-width:900px; }}
div[class*="st-key-briefbar"] {{ max-width:580px; margin:0 auto; width:100%; transition:max-width .25s ease; }}
/* grows wider once the user is typing (or a chip filled it), and taller as the text wraps */
div[class*="st-key-briefbar"]:has(textarea:focus), div[class*="st-key-briefbar"]:has(textarea:not(:placeholder-shown)) {{
  max-width:760px; }}
div[class*="st-key-briefbar"] .stTextArea textarea {{ field-sizing:content; height:auto !important; min-height:56px !important;
  max-height:170px; overflow-y:auto; }}
div[class*="st-key-briefbar"] .stTextArea div {{ height:auto !important; }}
div[class*="st-key-briefbar"] div[class*="st-key-panel-campaign"] {{ max-width:none; margin:4px 0 0; }}
div[class*="st-key-briefbar"] [data-testid="stForm"] [data-testid="stHorizontalBlock"] {{ background:#fff;
  border-radius:18px; padding:6px; gap:6px; box-shadow:0 14px 34px rgba(8,32,20,.30); align-items:center; }}
div[class*="st-key-briefbar"] .stTextArea textarea {{ box-shadow:none !important; resize:none !important;
  padding:12px 14px !important; min-height:56px !important; font-size:1.02rem; line-height:1.45; }}
div[class*="st-key-briefbar"] .stTextArea [data-baseweb="textarea"], div[class*="st-key-briefbar"] .stTextArea
  [data-baseweb="base-input"] {{ border:none !important; background:transparent !important; box-shadow:none !important; }}
div[class*="st-key-briefbar"] .stTextArea textarea:focus {{ outline:none; }}
div[class*="st-key-briefbar"] .stTextArea textarea {{ color:var(--pn-navy) !important;
  -webkit-text-fill-color:var(--pn-navy); caret-color:var(--pn-green); }}
div[class*="st-key-briefbar"] .stTextArea textarea::placeholder {{ color:#8B8E93; -webkit-text-fill-color:#8B8E93; }}
div[class*="st-key-briefbar"] .stTextArea div {{ border-color:transparent !important; background-color:transparent !important;
  box-shadow:none !important; }}
div[class*="st-key-briefbar"] [data-testid="stForm"] [data-testid="stHorizontalBlock"]:focus-within {{
  box-shadow:0 0 0 3px var(--pn-lime), 0 14px 34px rgba(8,32,20,.30); }}
div[class*="st-key-briefbar"] .stFormSubmitButton > button {{ min-height:56px; border-radius:14px; box-shadow:none; }}
div[class*="st-key-briefchips"] {{ margin:10px 0 78px; align-items:center; flex-wrap:nowrap !important;
  width:max-content !important; max-width:none !important; position:relative; left:50%; transform:translateX(-50%); }}
div[class*="st-key-briefbar"] [data-testid="stForm"] [data-testid="stHorizontalBlock"] {{ flex-wrap:nowrap !important; }}
div[class*="st-key-briefbar"] [data-testid="stForm"] [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:first-child {{
  flex:1 1 0 !important; width:auto !important; min-width:0 !important; }}
div[class*="st-key-briefbar"] .stTextArea, div[class*="st-key-briefbar"] .stTextArea textarea {{ width:100% !important;
  min-width:0 !important; }}
div[class*="st-key-briefbar"] [data-testid="stForm"] [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:last-child {{
  flex:0 0 136px !important; width:136px !important; min-width:136px; }}
div[class*="st-key-briefbar"] [data-testid="InputInstructions"] {{ display:none !important; }}
div[class*="st-key-briefchips"] .pn-brief-ex {{ margin:0 2px 0 0; }}
div[class*="st-key-briefchips"] .stButton button {{ min-height:30px !important; padding:3px 12px !important;
  border-radius:999px !important; background:rgba(255,255,255,.10) !important; border:1px solid rgba(255,255,255,.32)
  !important; color:#fff !important; box-shadow:none !important; }}
div[class*="st-key-briefchips"] .stButton button p {{ font-size:.82rem; font-weight:600; color:inherit; }}
div[class*="st-key-briefchips"] .stButton button:hover {{ background:rgba(157,246,154,.18) !important;
  border-color:var(--pn-lime) !important; color:var(--pn-lime) !important; }}
/* mesh fades in from both sides on the centered home */
div[class*="st-key-band"]:has(div[class*="st-key-briefbar"])::before {{
  -webkit-mask-image:linear-gradient(90deg, #000 0%, transparent 32%, transparent 68%, #000 100%);
  mask-image:linear-gradient(90deg, #000 0%, transparent 32%, transparent 68%, #000 100%); }}
/* report card lifts over the band's bottom edge */
div[class*="st-key-homecard"], div[class*="st-key-histcard"] {{ position:relative; background:#fff; border-radius:22px;
  padding:20px 22px 14px; box-shadow:0 16px 40px rgba(29,29,53,.10); border:1px solid {T['border']}; }}
div[class*="st-key-homecard"] {{ z-index:2; margin-top:-88px; }}
div[class*="st-key-histcard"] {{ margin-top:18px; }}
div[class*="st-key-homecard"] div[class*="st-key-reportlist"], div[class*="st-key-histcard"] div[class*="st-key-reportlist"] {{
  border:none; border-radius:0; padding:0; }}
div[class*="st-key-homecard"] div[class*="st-key-row-camp-"], div[class*="st-key-histcard"] div[class*="st-key-row-camp-"] {{
  border-radius:12px; }}
/* home only: brand mint washes down from the band into the page */
[data-testid="stMain"]:has(div[class*="st-key-homecard"]) {{
  background:linear-gradient(180deg, #EBFDEB 0px, #F2FBF4 420px, {T['bg']} 900px); }}
@media (max-width:1240px) {{
 .pn-row-spec {{ width:170px; }} .pn-row-num {{ width:92px; }} .pn-row-time {{ width:104px; }}
 .pn-row-main .br {{ max-width:340px; }}
}}
@media (max-width:980px) {{ .pn-row-main .br, .pn-row-spec {{ display:none; }} }}
/* ===== step 3: rings, sparklines, entrance and feedback motion (first render only; off with reduced motion) ===== */
@property --pp {{ syntax:'<number>'; inherits:false; initial-value:0; }}
.pn-ring {{ --pp:var(--p); width:74px; height:74px; border-radius:50%; flex:none; display:grid; place-items:center;
  background:conic-gradient(var(--pn-green) calc(var(--pp) * 1%), #D9EFE0 0);
  animation:pnring 1s cubic-bezier(.2,.8,.2,1) both; }}
@keyframes pnring {{ from {{ --pp:0; }} }}
.pn-ring-in {{ width:60px; height:60px; border-radius:50%; background:#fff; display:flex; flex-direction:column;
  align-items:center; justify-content:center; line-height:1; }}
.pn-ring .num {{ font-family:var(--pn-mono); font-weight:700; font-size:1.45rem; color:var(--pn-navy); letter-spacing:-.04em; }}
.pn-ring .lbl {{ font-size:.44rem; font-weight:700; letter-spacing:.12em; color:var(--pn-green); text-transform:uppercase;
  margin-top:3px; }}
.pn-spark {{ margin:2px 0 8px; }}
.pn-spark .k {{ font-size:.64rem; font-weight:700; letter-spacing:.07em; text-transform:uppercase; color:{T['text_muted']};
  margin-bottom:4px; }}
.pn-spark svg {{ width:100%; height:26px; display:block; }}
.pn-spark rect {{ fill:#A8C5B9; transform-origin:bottom; transform-box:fill-box; animation:pnbar .6s ease-out both; }}
.pn-spark rect.pk {{ fill:var(--pn-green); }}
.pn-spark rect:nth-child(2) {{ animation-delay:.05s; }} .pn-spark rect:nth-child(3) {{ animation-delay:.1s; }}
.pn-spark rect:nth-child(4) {{ animation-delay:.15s; }} .pn-spark rect:nth-child(5) {{ animation-delay:.2s; }}
@keyframes pnbar {{ from {{ transform:scaleY(0); }} }}
/* cards rise in, column by column */
div[class*="st-key-card-cr-"] {{ animation:pnrise .38s ease-out both; }}
[data-testid="stColumn"]:nth-child(2) div[class*="st-key-card-cr-"] {{ animation-delay:.07s; }}
[data-testid="stColumn"]:nth-child(3) div[class*="st-key-card-cr-"] {{ animation-delay:.14s; }}
@keyframes pnrise {{ from {{ opacity:0; transform:translateY(10px); }} }}
/* score bars grow from zero */
.pn-bar .fill {{ transform-origin:left; animation:pngrow .7s cubic-bezier(.2,.8,.2,1) both; }}
@keyframes pngrow {{ from {{ transform:scaleX(0); }} }}
/* S tier: one light sweep, once */
.pn-tier.t-s {{ position:relative; overflow:hidden; }}
.pn-tier.t-s::after {{ content:""; position:absolute; inset:0; transform:translateX(-130%);
  background:linear-gradient(110deg, transparent 25%, rgba(255,255,255,.85) 50%, transparent 75%);
  animation:pnsheen 1.1s .45s ease-out 1 both; }}
@keyframes pnsheen {{ to {{ transform:translateX(130%); }} }}
/* shortlist count bumps when it changes (its container is re-keyed by count) */
div[class*="st-key-cart"] button {{ animation:pnbump .45s ease-out; }}
@keyframes pnbump {{ 30% {{ transform:scale(1.08); }} }}
/* tactile buttons */
.stButton button:active, .stFormSubmitButton button:active, .stDownloadButton button:active {{
  transform:translateY(1px); }}
.stButton > button[kind^="primary"]:hover, .stFormSubmitButton > button[kind^="primary"]:hover {{ filter:brightness(1.04); }}
/* home search bar: lift on focus, CTA idles until there is text, rotating example hint while empty */
div[class*="st-key-briefbar"] [data-testid="stForm"] [data-testid="stHorizontalBlock"] {{
  transition:transform .2s ease, box-shadow .2s ease; }}
div[class*="st-key-briefbar"] [data-testid="stForm"] [data-testid="stHorizontalBlock"]:focus-within {{
  transform:translateY(-2px); }}
div[class*="st-key-briefbar"]:has(textarea:placeholder-shown) .stFormSubmitButton button {{ opacity:.55;
  filter:saturate(.6); }}
div[class*="st-key-briefbar"]:has(textarea:not(:placeholder-shown)) .stFormSubmitButton button {{
  animation:pnready .5s ease-out 1; }}
@keyframes pnready {{ 40% {{ transform:scale(1.05); }} }}
/* card head: ring replaces the lime block */
div[class*="st-key-card-cr-"] .pn-scorewrap {{ align-items:center; }}
@media (prefers-reduced-motion:reduce) {{ .pn-ring, .pn-spark rect, .pn-bar .fill, div[class*="st-key-card-cr-"],
  .pn-tier.t-s::after, div[class*="st-key-cart"] button {{ animation:none !important; }}
}}
@keyframes pnmesh {{ to {{ background-position:130px 65px; }} }}
@media (prefers-reduced-motion:reduce) {{ div[class*="st-key-band"]::before {{ animation:none !important; }} }}
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


def confidence_badge(conf: float, label: str, show_pct: bool = True, basis: str = "") -> str:
    """With `basis` (e.g. "2 videos · 0 comments") the evidence is shown instead of the percentage."""
    text, fg, bg = CONFIDENCE_STYLE[label]
    val = f" · {esc(basis).upper()}" if basis else (f" · {conf * 100:.0f}%" if show_pct else "")
    return (f'<span class="pn-badge" style="color:{fg};background:{bg}" title="Data confidence: how much data '
            f'backs this score. Separate from the campaign score."><span class="dot" style="background:{fg}"></span>'
            f'{text}{val}</span>')


def gem_badge() -> str:
    return (f'<span class="pn-badge" style="color:{T["gem"]};background:{T["gem_soft"]}" title="Hidden gem: '
            f'campaign fit ≥ 75, campaign score at or above the median, and fewer subscribers than the median '
            f'ranked creator">◆ HIDDEN GEM</span>')


def budget_badge() -> str:
    return (f'<span class="pn-badge" style="color:{T["warn"]};background:{T["warn_soft"]}" title="Cost proxy is above '
            f'the budget per video set under Ranking priorities. The score is unchanged.">OVER BUDGET</span>')


def score_block(score: int, label: str = "Campaign score", xl: bool = False) -> str:
    """The TOPSIS score: relative to the creators in this campaign (tooltip says so)."""
    return (f'<div class="pn-score{" xl" if xl else ""}" title="Campaign score: ranking against the other creators '
            f'in this campaign (AHP-weighted TOPSIS)"><div class="num">{score}<small>/100</small></div>'
            f'<div class="lbl">{esc(label)}</div></div>')


def metric_bar(label: str, value: float | None, accent: bool = False, display: str | None = None) -> str:
    na = value is None  # no data is shown as such, never as an empty (zero-looking) bar
    v = 0 if na else max(0.0, min(100.0, value))
    shown = "No data" if na else (display if display is not None else f"{value:.0f}")
    cls = (" accent" if accent else "") + (" na" if na else "")
    return (f'<div class="pn-bar{cls}"><span class="l">{esc(label)}</span>'
            f'<span class="v">{esc(shown)}</span><div class="track"><div class="fill" style="width:{v:.0f}%"></div>'
            f'</div></div>')


def stat_tile(k: str, v: str, hint: str = "", ico: str | None = None) -> str:
    """Small key figure (label + value), used on creator cards and the analysis header."""
    title = f' title="{esc(hint)}"' if hint else ""
    lead = icon(ico) if ico else ""
    return f'<div class="pn-stat"{title}><div class="k">{lead}{esc(k)}</div><div class="val">{esc(v)}</div></div>'


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


# ------------------------------------------------------------------ step 2: icons, tiers, boot-log loader
PLATFORM_SVG = {  # simplified platform glyphs (inline, no network)
    "youtube": '<rect x="2" y="5" width="20" height="14" rx="4" fill="#FF0033"/><path d="M10 9l5 3-5 3z" fill="#fff"/>',
    "tiktok": '<path d="M14.5 3h2.6a4.4 4.4 0 0 0 3.4 3.4V9a7 7 0 0 1-3.4-1v6.6A5.6 5.6 0 1 1 11.5 9v2.8a2.9 2.9 0 1 0 '
              '3 2.9z" fill="#111"/>',
    "instagram": '<rect x="3" y="3" width="18" height="18" rx="5" fill="none" stroke="#E1306C" stroke-width="2"/>'
                 '<circle cx="12" cy="12" r="4" fill="none" stroke="#E1306C" stroke-width="2"/>'
                 '<circle cx="17.3" cy="6.7" r="1.3" fill="#E1306C"/>',
    "twitch": '<path d="M4 3h16v11l-4 4h-4l-3 3v-3H4z" fill="#9146FF"/><path d="M11 7h1.8v4H11zm4 0h1.8v4H15z" fill="#fff"/>',
    "x": '<path d="M4 4l16 16M20 4L4 20" stroke="#111" stroke-width="2.4" stroke-linecap="round"/>',
    "facebook": '<circle cx="12" cy="12" r="10" fill="#1877F2"/><path d="M13 8h2V5.5h-2.2C10.7 5.5 10 7 10 8.7V10H8v2.6h2V19'
                'h3v-6.4h2.2l.4-2.6H13V9c0-.6.3-1 1-1z" fill="#fff"/>',
}
PLATFORM_NAME = {"youtube": "YouTube", "tiktok": "TikTok", "instagram": "Instagram", "twitch": "Twitch", "x": "X",
                 "facebook": "Facebook"}
ICON_SVG = {  # outline metric icons, drawn in currentColor
    "users": '<path d="M16 19v-1a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v1"/><circle cx="9" cy="7" r="3.5"/>'
             '<path d="M22 19v-1a4 4 0 0 0-3-3.9M16 3.3a3.5 3.5 0 0 1 0 6.9"/>',
    "eye": '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    "euro": '<path d="M17 6.5A6.5 6.5 0 1 0 17 17.5M4 10h9M4 14h9"/>',
    "pulse": '<path d="M2 12h4l3-8 4 16 3-8h6"/>',
}


def platform_icon(platform: str, size: int = 14) -> str:
    body = PLATFORM_SVG.get(platform.lower())
    if not body:
        return ""
    return f'<svg class="pn-ico" width="{size}" height="{size}" viewBox="0 0 24 24" aria-hidden="true">{body}</svg>'


def platform_chip(platform: str, handle: str) -> str:
    name = PLATFORM_NAME.get(platform.lower(), platform)
    return (f'<span class="pn-chip pn-pchip" title="{esc(name)}">{platform_icon(platform)}'
            f'<span>@{esc(handle)}</span></span>')


def icon(name: str, size: int = 12) -> str:
    return (f'<svg class="pn-ico" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{ICON_SVG[name]}</svg>')


TIER_RULE = ("Tier within this campaign, by campaign score: S = top 15%, A = next 25%, B = next 35%, C = rest. "
             "Relative to these candidates, not an absolute quality grade.")


def tier_of(rank: int, n: int) -> str:
    """Percentile tier from the deterministic ranking (never from an AI judgement)."""
    for tier, share in (("S", .15), ("A", .40), ("B", .75)):
        if rank <= max(1, round(share * n)):
            return tier
    return "C"


def tier_badge(tier: str, large: bool = False) -> str:
    return (f'<span class="pn-tier t-{tier.lower()}{" lg" if large else ""}" title="{esc(TIER_RULE)}" '
            f'aria-label="Tier {tier}">{tier}</span>')


def boot_log(stages: list[str], current: int, detail: str = "") -> str:
    """Staged loader styled like a PC's power-on self test; every line is a real pipeline stage."""
    lines = []
    for i, s in enumerate(stages):
        if i < current:
            tag, cls = "[ OK ]", "ok"
        elif i == current:
            tag, cls = "[ .. ]", "run"
        else:
            tag, cls = "[    ]", "wait"
        d = f'<span class="d">{esc(detail)}</span>' if i == current and detail else ""
        cur = '<span class="cur">▌</span>' if i == current else ""
        lines.append(f'<div class="ln {cls}"><span class="tag">{tag}</span><span class="s">{esc(s)}</span>{d}{cur}</div>')
    done = current >= len(stages)
    head = "PRENEW CREATOR INTELLIGENCE · " + ("DISCOVERY COMPLETE" if done else "RUNNING DISCOVERY")
    return f'<div class="pn-boot"><div class="hd">{head}</div>{"".join(lines)}</div>'


# ------------------------------------------------------------------ step 3: meaningful visuals + restrained motion
def score_ring(score: int, label: str = "Campaign score") -> str:
    """Campaign score as a benchmark-style ring (fills once on first render). Keeps the .num markup."""
    s = max(0, min(100, int(score)))
    return (f'<div class="pn-ring" style="--p:{s}" title="Campaign score: ranking against the other creators in this '
            f'campaign (AHP-weighted TOPSIS)"><div class="pn-ring-in"><div class="num">{s}</div>'
            f'<div class="lbl">{esc(label)}</div></div></div>')


def views_sparkline(views: list[int | None], label: str = "Views · last relevant videos") -> str:
    """Tiny bar chart of real per-video views (oldest → newest): consistent, spiky or declining at a glance."""
    vals = [v for v in views if v is not None]
    if len(vals) < 2:
        return ""
    top = max(vals) or 1
    n = len(vals)
    w, h, gap = 100.0 / n, 26, 2.2
    bars = []
    for i, v in enumerate(vals):
        bh = max(2.0, h * v / top)
        cls = "pk" if v == top else ""
        bars.append(f'<rect class="{cls}" x="{i * w + gap / 2:.2f}" y="{h - bh:.2f}" width="{w - gap:.2f}" '
                    f'height="{bh:.2f}" rx="1.5"><title>{fmt_count(v)} views</title></rect>')
    return (f'<div class="pn-spark"><div class="k">{esc(label)}</div>'
            f'<svg viewBox="0 0 100 {h}" preserveAspectRatio="none" role="img" aria-label="{esc(label)}">'
            f'{"".join(bars)}</svg></div>')
