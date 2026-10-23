"""Visual styling for the ReturnLens dashboard (looks only, no logic).

This is the only place with CSS. Used from app.py with two lines, right after st.set_page_config(...):
    import style
    style.apply()
Delete those two lines to go back to the plain Streamlit look.
"""

import plotly.graph_objects as go
import plotly.io as pio
import streamlit as st

# Brand palette (matches the colours already used in app.py)
NAVY = "#0b2545"
BLUE = "#2a78d6"
BLUE_DARK = "#104281"
BLUE_SOFT = "#eef5fd"
BORDER = "#d9e6f5"
MUTED = "#5b6b82"
SERIES = [BLUE, "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]  # chart colour order

CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

/* ---------- base ---------- */
html, body, [class*="css"], .stMarkdown, .stText, button, input, textarea, select {{
    font-family: 'Inter', system-ui, -apple-system, 'Segoe UI', sans-serif !important;
}}
[data-testid="stAppViewContainer"] {{
    background: linear-gradient(180deg, #eaf3fd 0%, #f7fbff 220px, #ffffff 420px);
}}
[data-testid="stHeader"] {{ background: transparent; }}
.block-container {{ padding-top: 2rem; padding-bottom: 4rem; max-width: 1280px; }}

/* ---------- app header block (top of the main page) ---------- */
.rl-header {{
    display: flex; justify-content: space-between; align-items: flex-end; flex-wrap: wrap; gap: 12px;
    padding: 4px 0 18px; margin-bottom: 8px; border-bottom: 1px solid {BORDER};
}}
.rl-title {{ font-size: 2.4rem; font-weight: 700; color: {NAVY}; letter-spacing: -0.03em; line-height: 1.1; }}
.rl-tagline {{ color: {MUTED}; font-size: 1.02rem; margin: 6px 0 10px; }}
.rl-pill {{
    display: inline-block; background: #ffffff; color: {BLUE_DARK}; border: 1px solid {BORDER};
    border-radius: 999px; padding: 4px 12px; font-size: .82rem; font-weight: 600;
    box-shadow: 0 1px 2px rgba(11,37,69,.05);
}}
.rl-meta {{ color: {MUTED}; font-size: .82rem; text-align: right; line-height: 1.5; }}
.rl-meta b {{ color: {NAVY}; font-weight: 600; }}

/* ---------- headings ---------- */
h1 {{
    color: {NAVY}; font-weight: 700 !important; letter-spacing: -0.02em;
    font-size: 2.3rem !important; margin-bottom: 0 !important;
}}
h2 {{
    color: {NAVY}; font-weight: 700 !important; font-size: 1.35rem !important;
    letter-spacing: -0.01em; margin-top: 2.2rem !important;
    padding-bottom: .45rem; border-bottom: 1px solid {BORDER};
}}
h3 {{ color: {NAVY}; font-weight: 600 !important; font-size: 1.05rem !important; }}
[data-testid="stHeaderActionElements"], h1 a, h2 a, h3 a {{ display: none !important; }}  /* hide anchor-link icons */
.rl-section-caption {{ color: {MUTED}; font-size: .9rem; margin: -.35rem 0 .9rem; }}

/* ---------- metric cards ---------- */
[data-testid="stMetric"] {{
    background: #ffffff; border: 1px solid {BORDER}; border-radius: 14px;
    padding: 14px 16px; box-shadow: 0 1px 2px rgba(11,37,69,.04), 0 4px 14px rgba(11,37,69,.05);
}}
[data-testid="stMetricLabel"] p {{
    color: {MUTED} !important; font-size: .74rem !important; font-weight: 600 !important;
    text-transform: uppercase; letter-spacing: .05em;
}}
[data-testid="stMetricValue"] {{ color: {NAVY}; font-weight: 700; font-size: 1.7rem !important; }}

/* ---------- buttons ---------- */
.stButton > button, .stDownloadButton > button, [data-testid="stFormSubmitButton"] > button {{
    border-radius: 10px; font-weight: 600; padding: .5rem 1.1rem;
    border: 1px solid {BORDER}; transition: all .15s ease;
}}
.stButton > button:hover, .stDownloadButton > button:hover, [data-testid="stFormSubmitButton"] > button:hover {{
    border-color: {BLUE}; color: {BLUE}; transform: translateY(-1px);
}}
button[kind="primary"], [data-testid="stBaseButton-primary"] {{
    background: linear-gradient(135deg, {BLUE} 0%, {BLUE_DARK} 100%) !important;
    color: #fff !important; border: none !important;
    box-shadow: 0 4px 14px rgba(42,120,214,.30);
}}
button[kind="primary"]:hover, [data-testid="stBaseButton-primary"]:hover {{
    box-shadow: 0 6px 18px rgba(42,120,214,.40); color: #fff !important;
}}
button:disabled {{ opacity: .5; transform: none !important; box-shadow: none !important; cursor: not-allowed; }}

/* ---------- file uploader ---------- */
[data-testid="stFileUploaderDropzone"] {{
    background: {BLUE_SOFT}; border: 2px dashed #9cc2ee; border-radius: 14px; padding: 1.4rem;
    transition: all .15s ease;
}}
[data-testid="stFileUploaderDropzone"]:hover {{ border-color: {BLUE}; background: #e3effc; }}

/* ---------- expanders, tabs, status, alerts ---------- */
[data-testid="stExpander"] details {{
    border: 1px solid {BORDER} !important; border-radius: 12px !important; background: #ffffff;
}}
[data-testid="stExpander"] summary {{ font-weight: 600; color: {NAVY}; }}
[data-testid="stTabs"] [data-baseweb="tab-list"] {{ gap: 6px; border-bottom: 1px solid {BORDER}; }}
[data-testid="stTabs"] [data-baseweb="tab"] {{
    border-radius: 10px 10px 0 0; padding: 8px 16px; font-weight: 600;
}}
[data-testid="stTabs"] [aria-selected="true"] {{ background: {BLUE_SOFT}; color: {BLUE}; }}
[data-testid="stAlert"] {{ border-radius: 12px; }}
[data-testid="stStatusWidget"], [data-testid="stStatus"] {{ border-radius: 12px; }}

/* ---------- tables & charts ---------- */
[data-testid="stDataFrame"], [data-testid="stTable"] {{
    border: 1px solid {BORDER}; border-radius: 12px; overflow: hidden;
}}
[data-testid="stPlotlyChart"] {{
    background: #ffffff; border: 1px solid {BORDER}; border-radius: 14px; padding: 8px;
    box-shadow: 0 1px 2px rgba(11,37,69,.04), 0 4px 14px rgba(11,37,69,.04);
}}

/* ---------- review quotes (drill-down) ---------- */
.rl-quote {{
    background: #ffffff; border: 1px solid {BORDER}; border-left: 4px solid {BLUE};
    border-radius: 0 12px 12px 0; padding: .8rem 1rem; margin: .6rem 0; color: {NAVY};
    box-shadow: 0 1px 2px rgba(11,37,69,.04); line-height: 1.5;
}}
.rl-quote-meta {{ color: {MUTED}; font-size: .8rem; margin-top: .45rem; }}

/* ---------- empty states ---------- */
.rl-empty {{
    background: #ffffff; border: 1px dashed #b9d3f1; border-radius: 14px; padding: 2rem 1.5rem;
    text-align: center; color: {MUTED}; margin: 1rem 0;
}}
.rl-empty-icon {{ font-size: 2rem; }}
.rl-empty-title {{ color: {NAVY}; font-weight: 600; font-size: 1.05rem; margin: .4rem 0 .2rem; }}

/* ---------- inputs ---------- */
[data-baseweb="input"], [data-baseweb="select"] > div, textarea {{ border-radius: 10px !important; }}

/* ---------- sidebar ---------- */
[data-testid="stSidebar"] {{ background: #f3f8fe; border-right: 1px solid {BORDER}; }}
[data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3 {{
    border-bottom: none; margin-top: .6rem !important; font-size: 1rem !important; padding-bottom: 0;
}}
[data-testid="stSidebar"] [data-testid="stExpander"] details {{ background: #ffffff; }}
.rl-side-brand {{ font-weight: 700; color: {NAVY}; font-size: 1.15rem; letter-spacing: -0.01em; }}
.rl-side-brand span {{ display: block; color: {MUTED}; font-weight: 400; font-size: .78rem; letter-spacing: 0; }}

/* ---------- misc ---------- */
hr {{ border-color: {BORDER} !important; }}
#MainMenu {{ visibility: visible; }}
footer {{ visibility: hidden; }}
</style>
"""


def _plotly_theme():
    """A clean chart style for every Plotly chart; charts that set their own colours keep them."""
    template = go.layout.Template(pio.templates["plotly_white"])
    template.layout.update(
        font=dict(family="Inter, system-ui, sans-serif", size=13, color=NAVY),
        paper_bgcolor="rgba(0,0,0,0)",   # transparent: the white chart card shows through
        plot_bgcolor="#ffffff",          # plot area white, so the page's blue tint doesn't show inside the card
        colorway=SERIES,
        margin=dict(l=10, r=10, t=40, b=10),
        hoverlabel=dict(bgcolor="#ffffff", bordercolor=BORDER, font=dict(color=NAVY)),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0, title_text=""),
        xaxis=dict(gridcolor="#eef2f7", zerolinecolor="#e3e9f2", linecolor=BORDER),
        yaxis=dict(gridcolor="#eef2f7", zerolinecolor="#e3e9f2", linecolor=BORDER),
    )
    pio.templates["returnlens"] = template
    pio.templates.default = "returnlens"


def apply():
    """Inject the CSS and set the chart theme. Call once, after st.set_page_config."""
    st.markdown(CSS, unsafe_allow_html=True)
    _plotly_theme()
