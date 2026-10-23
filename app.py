"""ReturnLens dashboard:  streamlit run app.py   (then drag & drop review CSVs at the top)"""

import html
import json
import os
import time
from datetime import datetime

import pandas as pd
import plotly.express as px
import streamlit as st
from dotenv import set_key

import analyze
import fetchers
import links
import llm
import uploads
from reasons import REASONS, RECOMMENDATIONS

APP_NAME = "ReturnLens"  # the app name and tagline live here only
APP_TAGLINE = "See why customers return — and what to fix first"
PRODUCT_NAME = "WaterScience CLEO Shower & Tap Filter"
TAGGED_FILE = analyze.OUTPUT_FILE  # same paths analyze.py writes to
LOG_FILE = analyze.LOG_FILE
INSIGHTS_FILE = analyze.INSIGHTS_FILE
PLATFORMS = list(analyze.PLATFORM_DIRS)  # ["amazon", "flipkart", "other"], the folder names in data/
ENV_FILE = ".env"
MODE_LABELS = {"smart": "Smart (recommended)", "full": "Full AI", "rules": "Rules only"}
# Columns added by newer versions of analyze.py; filled with "" when reading older results.
NEW_COLUMNS = ["product_id", "rule_reasons_only", "ai_reasons", "primary_reason", "sentiment", "severity", "language"]

# Colours: one blue for single-series charts, a fixed order for multi-series ones
# (checked for colour-blind safety), and light->dark blues for Cheap->Expensive.
BLUE = "#2a78d6"
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]
COST_COLORS = {"Cheap": "#86b6ef", "Medium": "#2a78d6", "Expensive": "#104281", "Unknown": "#b0b0b0"}

st.set_page_config(page_title=APP_NAME, page_icon="🔍", layout="wide")

import style  # visual styling only (style.py); remove these two lines for the plain look
style.apply()


def file_mtime(path):
    """Last-modified time of a file, or 0 if it does not exist."""
    return os.path.getmtime(path) if os.path.exists(path) else 0


def read_json(path):
    """A JSON file as a dict, or {} if it is missing or broken."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


@st.cache_data
def load_data(tagged_mtime, insights_mtime, log_mtime):
    """Load tagged reviews, insights and the run log.

    The mtimes are not used inside; they are part of the cache key, so whenever
    analyze.py rewrites a file, Streamlit loads fresh data automatically.
    Note: no leading underscore, because Streamlit ignores _arguments when caching.
    """
    tagged = pd.read_csv(TAGGED_FILE, dtype={"review_id": str}, keep_default_na=False)
    for col in NEW_COLUMNS:
        if col not in tagged.columns:
            tagged[col] = "unknown" if col == "product_id" else ""
    return tagged, read_json(INSIGHTS_FILE), read_json(LOG_FILE)


def pretty(reason):
    """Turn 'fit_compatibility' into 'Fit compatibility' for display."""
    return str(reason).replace("_", " ").capitalize()


def nice_time(iso):
    """'2026-10-23T16:15:00' -> '23 Oct 2026, 4:15 PM'."""
    t = datetime.fromisoformat(iso)
    return f"{t.day} {t:%b %Y}, {t:%I:%M %p}".replace(", 0", ", ")


def style_chart(fig, height=380):
    """Set the chart height; font, colours, grid and legend come from the theme in style.py."""
    fig.update_layout(height=height, legend_title_text="", margin=dict(r=48))  # room for "38%" labels at the end
    fig.update_traces(cliponaxis=False, selector=dict(type="bar"))  # bar labels may draw past the plot edge
    return fig


# ------------------------------------------------------------------ small visual helpers (HTML + CSS classes from style.py)
def section(title, caption, anchor=None):
    """A section header with one muted line underneath explaining what it shows."""
    st.header(title, anchor=anchor)
    st.markdown(f'<div class="rl-section-caption">{caption}</div>', unsafe_allow_html=True)


def empty_card(title, text="", icon="📭"):
    """A friendly placeholder card instead of an empty chart."""
    st.markdown(f'<div class="rl-empty"><div class="rl-empty-icon">{icon}</div>'
                f'<div class="rl-empty-title">{title}</div>{text}</div>', unsafe_allow_html=True)


def header_block():
    """Top of the page: name, tagline, product pill and 'Last analysis' on the right."""
    run_at = read_json(LOG_FILE).get("run_at")
    last = nice_time(run_at) if run_at else "not run yet"
    st.markdown(
        f'<div class="rl-header"><div>'
        f'<div class="rl-title">🔍 {APP_NAME}</div>'
        f'<div class="rl-tagline">{html.escape(APP_TAGLINE)}</div>'
        f'<span class="rl-pill">💧 {html.escape(PRODUCT_NAME)}</span></div>'
        f'<div class="rl-meta">Built for <b>WaterScience</b><br>Last analysis: <b>{last}</b></div></div>',
        unsafe_allow_html=True)


def sidebar_filters(df):
    """Draw the sidebar filters and return only the matching rows."""
    st.sidebar.header("Filters")
    counts = df["platform"].value_counts()
    present = [p for p in PLATFORMS if p in counts.index]
    everything = "Both" if len(present) <= 2 else "All"
    choice = st.sidebar.radio("Platform", [everything, *present],
                              format_func=lambda p: p if p == everything else f"{p.capitalize()} ({counts.get(p, 0)})")
    on_platform = pd.Series(True, index=df.index) if choice == everything else df["platform"] == choice
    chosen_ratings = st.sidebar.multiselect("Rating (★)", [1, 2, 3, 4, 5], default=[1, 2, 3, 4, 5])

    months = sorted(m for m in df["month"].unique() if m != "unknown")
    if len(months) >= 2:
        start, end = st.sidebar.select_slider("Date range", options=months, value=(months[0], months[-1]))
    else:
        start, end = (months[0], months[0]) if months else ("", "")
    keep_unknown = st.sidebar.checkbox("Include reviews with unknown date", value=True)
    verified_only = st.sidebar.checkbox("Verified purchases only", value=False)

    in_range = df["month"].between(start, end) | (keep_unknown & (df["month"] == "unknown"))
    mask = on_platform & df["rating"].isin(chosen_ratings) & in_range
    if verified_only:
        mask &= df["verified"].astype(str).str.lower() == "true"
    return df[mask]


def product_filter(df):
    """Sidebar 'Product' dropdown: All products, or one product_id. Filters the whole dashboard."""
    products = sorted(df.groupby(["platform", "product_id"]).size().index)
    labels = {p: f"{p[1]} ({p[0].capitalize()})" for p in products}
    choice = st.sidebar.selectbox("Product", ["All products", *products],
                                  format_func=lambda p: p if p == "All products" else labels[p])
    if choice == "All products":
        return df
    return df[(df["platform"] == choice[0]) & (df["product_id"] == choice[1])]


def build_evidence(rows):
    """Pick one short quote for a reason: the AI's evidence quote if any, else the shortest review."""
    quotes = rows.loc[rows["evidence_quote"] != "", "evidence_quote"]
    if not quotes.empty:
        return quotes.iloc[0]
    text = rows.loc[rows["raw_text"].str.len().idxmin(), "raw_text"]
    return text if len(text) <= 110 else text[:107] + "..."


def reason_set(text):
    """'leakage|fit_compatibility' -> {'leakage', 'fit_compatibility'}; 'other' or '' -> empty set."""
    return {r for r in str(text).split("|") if r and r != "other"}


# ------------------------------------------------------------------ sidebar: AI mode + keys
def ai_mode_panel():
    """'🧠 AI analysis': Smart (Gemini only for unclear reviews) or Full AI (Gemini for every 1-3★)."""
    last = read_json(LOG_FILE).get("mode")
    mode = st.sidebar.radio(
        "🧠 AI analysis", ["smart", "full"], index=1 if last == "full" else 0,
        format_func=MODE_LABELS.get,
        captions=["Rules first; Gemini only for reviews the rules can't place. Fewest calls.",
                  "Gemini reads every 1-3★ review (rules shown alongside). Best quality, more calls."])
    if not llm.api_key():
        st.sidebar.warning("No GEMINI_API_KEY: the analysis runs with keyword rules only.")
    return mode


KEY_HELP = {
    "GEMINI_API_KEY": "Free key: aistudio.google.com → Get API key.",
    "APIFY_API_TOKEN": "Optional. apify.com → Settings → API & Integrations. Shows the product-link section.",
}


def api_keys_panel():
    """'🔑 API keys': Gemini / Apify status, and fields for missing keys. Values are never shown."""
    missing = [k for k in KEY_HELP if not os.getenv(k, "").strip()]
    with st.sidebar.expander("🔑 API keys", expanded="GEMINI_API_KEY" in missing):
        st.markdown(("✅ Gemini: key set (connected)" if llm.api_key() else "⚠️ Gemini: **missing**")
                    + f"  \nModel: `{llm.model_name()}`")
        st.markdown("✅ Apify: token set" if fetchers.apify_token() else "◻️ Apify: not set (optional)")
        if not missing:
            st.caption("To change a key, edit .env and restart the app.")
            return
        with st.form("api_keys"):
            values = {}
            for key in missing:
                values[key] = st.text_input(key, type="password")
                st.caption(KEY_HELP[key])
            save = st.checkbox("Save to .env", help="Otherwise the keys are used only until the app restarts.")
            submitted = st.form_submit_button("Use these keys")
    if submitted:
        for key, value in values.items():
            if value.strip():
                os.environ[key] = value.strip()  # used right away by llm.py / fetchers.py
                if save:
                    set_key(ENV_FILE, key, value.strip())  # creates .env if needed
        st.rerun()


# ------------------------------------------------------------------ running the analysis
def analysis_steps(mode, first_number):
    """Run analyze.run_analysis() inside an open st.status, writing each numbered step."""
    number = [first_number]

    def show(step_name):
        st.write(f"{number[0]}) {step_name}")
        number[0] += 1

    summary = analyze.run_analysis(mode=mode, progress=show)
    show("Refreshing dashboard")
    return summary


def finish(title, summary, started, **extra):
    """Clear the cache, keep the summary for the green box, and reload the page."""
    st.cache_data.clear()  # make every chart read the new results
    st.session_state["last_run"] = {"title": title, "summary": summary,
                                    "total_seconds": round(time.time() - started, 1), **extra}
    st.rerun()  # start the page again from the top, now with the new data


def rerun_analysis(title, mode):
    """Re-run on the files already saved (Re-run button, after deleting a file)."""
    started = time.time()
    with st.status("Analyzing reviews…", expanded=True) as status:
        try:
            summary = analysis_steps(mode, 1)
            status.update(label="Done", state="complete")
        except analyze.AnalysisError as e:
            status.update(label="Stopped", state="error")
            st.error(f"The analysis stopped: {e}")
            return
        except Exception as e:  # show any other problem in plain words instead of crashing the page
            status.update(label="Stopped", state="error")
            st.error(f"Something went wrong while analysing the reviews: {type(e).__name__}: {e}")
            return
    finish(title, summary, started)


def data_files_panel():
    """'📁 Data files': tick files, then ONE 'Delete selected' button with a confirmation step."""
    with st.sidebar.expander("📁 Data files"):
        files = uploads.list_data_files()
        if not files:
            st.caption("No data files yet — upload a CSV above.")
            return
        # Widget keys include a counter, so after a delete every checkbox starts unticked again.
        round_ = st.session_state.get("files_round", 0)
        pick_keys = {f["path"]: f"pick_{round_}_{f['path']}" for f in files}

        def select_all():  # runs when "Select all" changes: tick / untick every file
            for key in pick_keys.values():
                st.session_state[key] = st.session_state[f"select_all_{round_}"]

        st.checkbox("Select all", key=f"select_all_{round_}", on_change=select_all)
        for f in files:
            rows = "unreadable" if f["rows"] is None else f"{f['rows']} rows"
            st.checkbox(f"{f['name']} · {f['platform']} · {rows}", key=pick_keys[f["path"]])
        selected = [f for f in files if st.session_state.get(pick_keys[f["path"]])]

        if st.button(f"🗑️ Delete selected ({len(selected)})", disabled=not selected):
            st.session_state["confirm_delete"] = [f["path"] for f in selected]  # ask first, delete later

        pending = st.session_state.get("confirm_delete")
        if pending:
            st.warning("Delete these files?\n\n" + "\n".join(f"- {os.path.basename(p)}" for p in pending))
            yes, cancel = st.columns(2)
            if yes.button("Yes, delete", type="primary"):
                deleted = []
                for path in pending:
                    try:
                        uploads.delete_data_file(path)
                        deleted.append(path)
                    except (OSError, ValueError) as e:
                        st.error(f"Could not delete {os.path.basename(path)}: {e}")
                st.session_state.pop("confirm_delete")
                st.session_state["files_round"] = round_ + 1  # fresh, unticked checkboxes
                if deleted:  # re-run the analysis (main page) with the current AI mode, then reload
                    st.session_state["pending_rerun"] = f"Deleted {len(deleted)} file(s)"
            if cancel.button("Cancel"):
                st.session_state.pop("confirm_delete")
                st.rerun()


# ------------------------------------------------------------------ main: add data
def upload_section(mode):
    """'📂 Add customer reviews': drag & drop CSVs -> check + preview -> Analyze reviews."""
    section("📂 Add customer reviews", "Drop review CSVs, check the preview, then click 🔍 Analyze reviews.",
            anchor="add-customer-reviews")
    st.caption("Drop one or more CSV files. Names starting with **amazon** / **flipkart** go to that platform; "
               "for other names you pick the platform. Only **rating** and **body** are required, and common "
               "column names (review, text, stars, score, heading…) are recognised automatically.")
    st.download_button("📥 Download CSV template", uploads.TEMPLATE_CSV, file_name="reviews_template.csv",
                       mime="text/csv")  # own line, so the label is never cut off
    # Changing the key after a successful save gives a fresh, empty uploader.
    files = st.file_uploader("Review CSV files", type="csv", accept_multiple_files=True,
                             key=f"uploader_{st.session_state.get('upload_round', 0)}")
    ready = []  # (check, replace) for every file that passed the checks
    for i, file in enumerate(files or []):
        data = file.getvalue()
        check = uploads.check_upload(file.name, data)
        if check["error"]:
            st.error(f"**{file.name}** — {check['error']}")
            continue
        if check["needs_platform"]:
            choice = st.selectbox(f"**{file.name}**: which platform are these reviews from?", PLATFORMS,
                                  index=None, placeholder="Choose a platform…", format_func=str.capitalize,
                                  key=f"platform_{i}_{file.name}")
            if choice is None:
                continue  # not ready until a platform is chosen
            check = uploads.check_upload(file.name, data, platform=choice)
        for warning in check["warnings"]:
            st.warning(f"**{file.name}** — {warning}")
        if check["renamed"]:
            st.caption(f"**{file.name}** columns matched: "
                       + ", ".join(f"{old} → {new}" for old, new in check["renamed"].items()))
        replace = False
        if uploads.name_taken(check):
            choice = st.radio(f"**{check['name']}** already exists in data/{check['platform']}/",
                              ["Save as next number", "Replace existing file"], key=f"conflict_{i}_{file.name}")
            replace = choice == "Replace existing file"
        st.dataframe(pd.DataFrame([uploads.preview(check, replace)]), hide_index=True)
        st.dataframe(uploads.first_rows(check), hide_index=True)  # first 3 rows, as they will be saved
        ready.append((check, replace))

    if st.button("🔍 Analyze reviews", type="primary", disabled=not ready):
        started = time.time()
        with st.status("Analyzing reviews…", expanded=True) as status:
            try:
                st.write("1) Saving files…")
                saved = [uploads.save_upload(check, replace) for check, replace in ready]
                st.session_state["upload_round"] = st.session_state.get("upload_round", 0) + 1  # empty the uploader
                summary = analysis_steps(mode, 2)
                status.update(label="Done", state="complete")
            except OSError as e:
                status.update(label="Stopped", state="error")
                st.error(f"Could not save a file ({e}). Nothing was analysed.")
                return
            except analyze.AnalysisError as e:
                status.update(label="Stopped", state="error")
                st.error(f"The analysis stopped: {e}")
                return
            except Exception as e:
                status.update(label="Stopped", state="error")
                st.error(f"Something went wrong while analysing the reviews: {type(e).__name__}: {e}")
                return
        finish("Files saved and analysis finished", summary, started, files_added=saved)


UPLOAD_INSTEAD = "[⬆ Use the CSV upload instead](#add-customer-reviews) — it works without Apify."


def link_section(mode):
    """'🔗 Fetch from product link': only shown when APIFY_API_TOKEN is set."""
    if not (fetchers.apify_token() or fetchers.mock_mode()):
        st.caption("Optional: add APIFY_API_TOKEN to .env to fetch reviews from a product link.")
        return
    with st.expander("🔗 Fetch from product link (optional, uses Apify credits)"):
        url = st.text_input("Paste Amazon.in or Flipkart product link",
                            placeholder="https://www.amazon.in/dp/B0... or https://www.flipkart.com/.../p/itm...?pid=...")
        link = None
        if url.strip():
            try:
                link = links.parse_link(url)
                st.success(f"Detected: **{link['platform'].capitalize()}** · product ID **{link['product_id']}**")
            except links.LinkError as e:
                st.error(str(e))
        max_reviews = st.slider("Max reviews", 50, 500, 50, step=10)
        if fetchers.mock_mode():
            st.info("MOCK_FETCH=1: uses saved data in data/raw/ or built-in fake reviews. No Apify call.")

        use_saved = False
        if link and not fetchers.mock_mode() and fetchers.recent_fetch(link["platform"], link["product_id"]):
            choice = st.radio("This product was fetched in the last 24 hours. "
                              "Use saved data (free) or fetch again (uses Apify credits)?",
                              ["Use saved data (free)", "Fetch again (uses Apify credits)"])
            use_saved = choice.startswith("Use saved")
        if link:
            cost = 0.0 if (use_saved or fetchers.mock_mode()) else fetchers.estimate_cost_usd(link["platform"], max_reviews)
            st.caption(f"Estimate: up to {max_reviews} reviews · Apify ≈ ${cost:.2f} "
                       f"(≈ ₹{cost * analyze.USD_TO_INR:.0f}) · Gemini ₹0 (free tier)")

        test_col, run_col = st.columns(2)
        test = test_col.button("Test fetch (5 reviews)", disabled=not link)
        run = run_col.button("🚀 Fetch & analyze", type="primary", disabled=not link)
    if test:
        try:
            with st.spinner("Fetching 5 reviews from Apify…"):
                raw = fetchers.fetch_raw(link, 5, use_saved=use_saved, save=False)
                reviews = fetchers.to_reviews(raw["items"], link, max_reviews=5)
            st.success(f"Apify works: got {len(reviews)} reviews ({raw['source']}). Click 🚀 Fetch & analyze for more.")
            st.dataframe(reviews[["rating", "title", "body", "date"]], hide_index=True)
        except fetchers.FetchError as e:
            st.warning(f"{e}\n\n{UPLOAD_INSTEAD}")
        except Exception as e:  # never let Apify problems break the page
            st.warning(f"Apify test failed ({type(e).__name__}).\n\n{UPLOAD_INSTEAD}")
    if run:
        run_link_flow(link, max_reviews, use_saved, mode)


def run_link_flow(link, max_reviews, use_saved, mode):
    """Fetch -> save -> the same analysis as the CSV upload, each step shown."""
    started = time.time()
    with st.status("Fetching and analyzing…", expanded=True) as status:
        try:
            st.write("1) Fetching reviews from Apify…")
            raw = fetchers.fetch_raw(link, max_reviews, use_saved=use_saved)
            reviews = fetchers.to_reviews(raw["items"], link, max_reviews=max_reviews)
            saved = fetchers.save_reviews(reviews, link)
            st.write(f"   → {saved['fetched']} reviews, {saved['new']} new, saved to {saved['path']}")
            summary = analysis_steps(mode, 2)
            status.update(label="Done", state="complete")
        except fetchers.FetchError as e:
            status.update(label="Apify didn't work this time", state="error")
            st.warning(f"{e}\n\n{UPLOAD_INSTEAD}")
            return
        except analyze.AnalysisError as e:
            status.update(label="Stopped", state="error")
            st.error(f"The analysis stopped: {e}")
            return
        except Exception as e:  # anything unexpected: plain message, no crash
            status.update(label="Stopped", state="error")
            st.warning(f"Something went wrong ({type(e).__name__}: {e}).\n\n{UPLOAD_INSTEAD}")
            return
    fetch = {**saved, "source": raw["source"], "apify_cost_usd": raw["apify_cost_usd"],
             "estimate_usd": raw["estimate_usd"], "product": f"{link['platform']} {link['product_id']}"}
    finish("Reviews fetched and analysed", summary, started, fetch=fetch)


def show_last_run():
    """Green summary box after analyze / fetch / re-run / delete (shown once, after the reload)."""
    info = st.session_state.pop("last_run", None)
    if not info:
        return
    s = info["summary"]
    lines = [f"**✅ {info['title']}** · mode: {MODE_LABELS.get(s.get('mode'), s.get('mode'))}"]
    fetch = info.get("fetch")
    if fetch:
        source = {"apify": "from Apify", "saved": "from saved data (free)", "mock": "MOCK data"}[fetch["source"]]
        cost = fetch["apify_cost_usd"]
        lines.append(f"Product {fetch['product']}: {fetch['fetched']} reviews {source} · new: {fetch['new']} · "
                     f"already had: {fetch['already_had']}"
                     + (f" · Apify cost ${cost:.3f}" if cost is not None else ""))
    if info.get("files_added"):
        lines.append("Files added: " + ", ".join(info["files_added"]))
    stars = {}
    for per_platform in s.get("rating_distribution", {}).values():
        for star, n in per_platform.items():
            stars[star] = stars.get(star, 0) + n
    lines += [
        "Reviews: " + ", ".join(f"{p.capitalize()} {n}" for p, n in s.get("reviews_by_platform", {}).items())
        + f" · duplicates skipped: {s['dropped_duplicates']} · final total: {s['reviews_clean']}",
        "Ratings: " + " · ".join(f"{k}★ {stars[k]}" for k in sorted(stars)),
        f"Tagged by rules: {s['pct_tagged_by_rules_only']}% · by Gemini: {s['pct_tagged_with_ai']}% · "
        f"in 'other': {s['pct_other']}%",
        f"Gemini: {s.get('tagging_calls', 0)} tagging + {s.get('insights_calls', 0)} insights calls · "
        f"{s.get('input_tokens', 0):,} in / {s.get('output_tokens', 0):,} out tokens · "
        f"{s.get('ai_cache_hits', 0)} from cache · cost ₹{s['cost_inr']:.2f}",
        f"Total time: {info.get('total_seconds', s['run_seconds'])}s",
    ]
    st.success("  \n".join(lines))  # two spaces + newline = line break in Streamlit markdown
    if s.get("ai_enabled") is False:
        st.warning(f"Gemini was not used ({s.get('ai_note') or 'AI off'}): rules only, so reviews the rules "
                   "can't match are labelled 'other'.")
    if s.get("needs_manual_review"):
        st.warning(f"{s['needs_manual_review']} reviews need a manual look (Gemini could not answer). "
                   "They will be retried on the next run.")


def insights_section(insights):
    """'🧠 AI Insights' from data/output/insights.json (written by analyze.py)."""
    section("🧠 AI Insights", "Gemini's summary of the biggest problems and what to fix first.")
    if not insights:
        empty_card("No insights yet", "Click 🔍 Analyze reviews or 🔁 Re-run analysis.", "🧠")
        return
    when = nice_time(insights["generated_at"]) if insights.get("generated_at") else ""
    if insights.get("source") == "gemini":
        st.caption(f"✨ Generated by Gemini {insights.get('model')} · {when} · "
                   f"{MODE_LABELS.get(insights.get('mode'), '')} mode · covers all platforms (filters don't apply)")
    else:
        st.caption(f"Built from the recommendations in reasons.py (Gemini not used: {insights.get('note')}) · {when}")
    st.info(insights["executive_summary"])

    issues = pd.DataFrame([{"Issue": pretty(i["reason"]), "Why customers are unhappy": i["why"],
                            "% of 1-3★": i["evidence_pct"], "Example quote": i["example_quote"]}
                           for i in insights.get("top_issues", [])])
    if not issues.empty:
        st.subheader("Top issues")
        st.dataframe(issues, hide_index=True, width="stretch",
                     column_config={"% of 1-3★": st.column_config.NumberColumn(format="%.1f%%")})
    if insights.get("quick_wins"):
        st.success("**⚡ Quick wins (cheap, can ship this week)**\n" +
                   "\n".join(f"- {w}" for w in insights["quick_wins"]))
    recs = pd.DataFrame([{"Fix": r["fix"], "Owner": r["owner"], "Effort": r["effort"],
                          "Expected impact": r["expected_impact"], "Success metric": r["success_metric"]}
                         for r in insights.get("recommendations", [])])
    if not recs.empty:
        st.subheader("Recommended actions")
        st.dataframe(recs, hide_index=True, width="stretch")
    if insights.get("risks_or_gaps"):
        with st.expander("Risks & gaps in this analysis"):
            st.markdown("\n".join(f"- {r}" for r in insights["risks_or_gaps"]))


# ------------------------------------------------------------------ page: sidebar controls
st.sidebar.markdown(f'<div class="rl-side-brand">🔍 {APP_NAME}<span>{html.escape(APP_TAGLINE)}</span></div>',
                    unsafe_allow_html=True)
mode = ai_mode_panel()
if st.sidebar.button("🔁 Re-run analysis", help="Analyse the files already saved, with the mode above"):
    st.session_state["pending_rerun"] = "Analysis re-run on the existing files"
data_files_panel()
api_keys_panel()
if st.sidebar.button("🔄 Reload data", help="Clear the cache and read data/output/ again"):
    load_data.clear()
st.sidebar.divider()

# ------------------------------------------------------------------ page: add data
header_block()
show_last_run()
upload_section(mode)
link_section(mode)
if "pending_rerun" in st.session_state:  # Re-run / Delete were clicked in the sidebar
    rerun_analysis(st.session_state.pop("pending_rerun"), mode)
st.divider()

# ------------------------------------------------------------------ load
if not os.path.exists(TAGGED_FILE):
    empty_card("No reviews yet — upload a CSV above to get started",
               "Drop your review files in 📂 Add customer reviews and click 🔍 Analyze reviews.", "📂")
    st.stop()

tagged, insights, log = load_data(file_mtime(TAGGED_FILE), file_mtime(INSIGHTS_FILE), file_mtime(LOG_FILE))
tagged = product_filter(tagged)
df = sidebar_filters(tagged)
neg = df[df["rating"] <= 3]
# One row per (review, reason), because a review can have several reasons.
exploded = neg.assign(reason=neg["reasons"].str.split("|")).explode("reason")

# ------------------------------------------------------------------ 1. header
by_platform = tagged["platform"].value_counts()
c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("Amazon", by_platform.get("amazon", 0))
c2.metric("Flipkart", by_platform.get("flipkart", 0))
c3.metric("Reviews shown", len(df))
c4.metric("Negative share", f"{len(neg) / len(df):.0%}" if len(df) else "–")
c5.metric("Avg rating", f"{df['rating'].mean():.2f}★" if len(df) else "–")
c6.metric("1–3★ tagged", len(neg))

if log.get("data_source") == "sample":
    st.warning(f"Showing SYNTHETIC sample reviews (N = {len(tagged)}), written to look realistic. "
               "Upload your review CSVs above to analyse real data.")
else:
    counts = ", ".join(f"{p.capitalize()} {by_platform.get(p, 0)}" for p in PLATFORMS if by_platform.get(p, 0))
    st.info(f"Real data: {counts} · uploaded CSVs" +
            (" and Apify fetches." if (tagged["product_id"] != "unknown").any() else "."))

# ------------------------------------------------------------------ 2. AI insights
insights_section(insights)

if neg.empty:
    empty_card("No 1–3★ reviews match the current filters",
               "Try widening the platform, rating or date filters in the sidebar.", "🔎")
    st.stop()

# ------------------------------------------------------------------ 3. top reasons
section("📊 Top return reasons", "Share of 1–3★ reviews mentioning each reason.")
share = (exploded["reason"].value_counts() / len(neg) * 100).round(1)
share_df = share.rename("share").reset_index().rename(columns={"index": "reason"})
share_df["label"] = share_df["reason"].apply(pretty)
fig = px.bar(share_df.sort_values("share"), x="share", y="label", orientation="h",
             text=share_df.sort_values("share")["share"].map(lambda v: f"{v:.0f}%"),
             color_discrete_sequence=[BLUE],
             labels={"share": "% of 1-3★ reviews", "label": ""})
fig.update_traces(textposition="outside", hovertemplate="%{y}: %{x:.1f}% of 1-3★ reviews<extra></extra>")
st.plotly_chart(style_chart(fig, height=60 + 38 * len(share_df)), width="stretch", theme=None)
st.caption(f"Share of {len(neg)} negative reviews mentioning each reason. "
           "Bars can add up to more than 100% because one review can give several reasons.")

# ------------------------------------------------------------------ 4. severity
section("⚠️ Severity", "Which reasons include the most serious complaints: product broke, leaked, arrived damaged or is unsafe.")
rated = neg["severity"].astype(str).isin(["1", "2", "3"])
severe = exploded[exploded["severity"].astype(str) == "3"]
if severe.empty:
    empty_card("No severity-3 complaints in the current view",
               "Severity is rated by Gemini; run the analysis with a GEMINI_API_KEY (Full AI rates every review).",
               "✅")
else:
    sev = severe["reason"].value_counts().rename("reviews").reset_index()
    sev["label"] = sev["reason"].apply(pretty)
    fig = px.bar(sev.sort_values("reviews"), x="reviews", y="label", orientation="h", text="reviews",
                 color_discrete_sequence=[SERIES[1]], labels={"reviews": "Severity-3 reviews", "label": ""})
    fig.update_traces(textposition="outside", hovertemplate="%{y}: %{x} severity-3 reviews<extra></extra>")
    fig.update_layout(xaxis=dict(dtick=1 if sev["reviews"].max() <= 10 else None))
    st.plotly_chart(style_chart(fig, height=60 + 38 * len(sev)), width="stretch", theme=None)
st.caption(f"Severity 3 = the product broke, leaked, arrived damaged or is unsafe (rated by Gemini). "
           f"{rated.sum()} of {len(neg)} negative reviews have a severity"
           + (" (in Smart mode, reviews tagged by rules alone have none)." if rated.sum() < len(neg) else "."))

# ------------------------------------------------------------------ 5. rules vs Gemini (Full AI only)
if log.get("mode") == "full":
    section("🤖 Rules vs Gemini", "How often the keyword rules and Gemini agree, and what Gemini caught that the rules missed.")
    both = neg[neg["ai_reasons"] != ""]
    if both.empty:
        empty_card("Nothing to compare yet", "Gemini did not tag any review in this run.", "🤖")
    else:
        rule_sets = both["rule_reasons_only"].map(reason_set)
        ai_sets = both["ai_reasons"].map(reason_set)
        same = (rule_sets == ai_sets).mean()
        overlap = pd.Series([bool(r & a) or (not r and not a) for r, a in zip(rule_sets, ai_sets)]).mean()
        extra = pd.Series([bool(a - r) for r, a in zip(rule_sets, ai_sets)], index=both.index)
        missed = pd.Series([not r and bool(a) for r, a in zip(rule_sets, ai_sets)], index=both.index)
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Exactly the same reasons", f"{same:.0%}")
        m2.metric("At least one reason in common", f"{overlap:.0%}")
        m3.metric("Gemini added a reason", int(extra.sum()))
        m4.metric("Rules found nothing, Gemini did", int(missed.sum()))
        st.caption(f"Compared on {len(both)} negative reviews that Gemini tagged. 'Other' counts as no reason.")
        examples = both[extra].assign(_missed=missed[extra]).sort_values("_missed", ascending=False).head(6)
        if not examples.empty:
            st.subheader("Examples where Gemini found a reason the rules missed")
            st.dataframe(pd.DataFrame({
                "Review": examples["raw_text"].str.slice(0, 160),
                "Rules said": examples["rule_reasons_only"].map(lambda s: ", ".join(pretty(r) for r in s.split("|"))),
                "Gemini said": examples["ai_reasons"].map(lambda s: ", ".join(pretty(r) for r in s.split("|"))),
                "Evidence": examples["evidence_quote"],
            }), hide_index=True, width="stretch")

# ------------------------------------------------------------------ 6. trend
section("📈 Trend by month", "Monthly count of negative reviews for the top 5 reasons.")
dated = exploded[exploded["month"] != "unknown"]
top5 = share.index[:5].tolist()
trend = (dated[dated["reason"].isin(top5)].groupby(["month", "reason"]).size()
         .unstack(fill_value=0).reindex(columns=top5, fill_value=0))
all_months = sorted(df.loc[df["month"] != "unknown", "month"].unique())
trend = trend.reindex(all_months, fill_value=0)
trend_long = trend.reset_index().melt(id_vars="month", var_name="reason", value_name="reviews")
trend_long["reason"] = trend_long["reason"].apply(pretty)
fig = px.line(trend_long, x="month", y="reviews", color="reason", markers=True,
              color_discrete_sequence=SERIES, labels={"month": "", "reviews": "Negative reviews"})
fig.update_traces(line=dict(width=2), marker=dict(size=8))
fig.update_layout(hovermode="x unified", yaxis=dict(dtick=1, rangemode="tozero"))  # whole reviews only
st.plotly_chart(style_chart(fig), width="stretch", theme=None)
st.caption("Top 5 reasons only. A sudden spike in one reason (e.g. leakage in one month) "
           "points to a bad production batch. Monthly counts are small, so treat spikes as leads, not proof.")
with st.expander("Show trend as a table"):
    st.dataframe(trend.rename(columns=pretty), width="stretch")

# ------------------------------------------------------------------ 7. drill-down
section("💬 What customers said", "Real review quotes for the reason you pick.")
pick = st.selectbox("Pick a reason", share.index.tolist(), format_func=pretty)
quotes = exploded[exploded["reason"] == pick].sort_values("rating").head(5)
for row in quotes.itertuples():
    meta = f"{'★' * row.rating}{'☆' * (5 - row.rating)} · {row.date or 'no date'} · {row.platform}"
    meta += " · verified" if str(row.verified).lower() == "true" else " · not verified"
    meta += f" · tagged by {row.tag_source}"
    if str(row.severity) in ("1", "2", "3"):
        meta += f" · severity {row.severity}"
    if getattr(row, "source_file", ""):
        meta += f" · {os.path.basename(row.source_file)}"
    st.markdown(f'<div class="rl-quote">{html.escape(row.raw_text)}'
                f'<div class="rl-quote-meta">{html.escape(meta)}</div></div>', unsafe_allow_html=True)

# ------------------------------------------------------------------ 8. fix owner
section("🧩 Who owns the fix?", "Share of 1–3★ reviews by the team that owns the fix, coloured by cost to fix.")
exploded["owner"] = exploded["reason"].map(lambda r: REASONS.get(r, REASONS["other"])["owner"])
exploded["fix_cost"] = exploded["reason"].map(lambda r: REASONS.get(r, REASONS["other"])["fix_cost"])
owner_df = (exploded.groupby(["owner", "fix_cost"])["review_id"].nunique()
            .div(len(neg)).mul(100).round(1).rename("share").reset_index())
# Group bars by cost (cheap at the top), then by size, so cheap vs expensive reads at a glance.
owner_df["cost_rank"] = owner_df["fix_cost"].map({c: i for i, c in enumerate(COST_COLORS)})
owner_df = owner_df.sort_values(["cost_rank", "share"], ascending=[False, True])
fig = px.bar(owner_df, x="share", y="owner", color="fix_cost", orientation="h",
             color_discrete_map=COST_COLORS, category_orders={"fix_cost": list(COST_COLORS)},
             labels={"share": "% of 1-3★ reviews", "owner": "", "fix_cost": "Cost to fix"})
fig.update_traces(hovertemplate="%{y}: %{x:.1f}%<extra></extra>")
st.plotly_chart(style_chart(fig), width="stretch", theme=None)
cheap = owner_df.loc[owner_df["fix_cost"] == "Cheap", "share"].sum()
st.caption(f"Light bars are cheap fixes (listing text, images, instructions). They cover {cheap:.0f}% "
           "of negative-review mentions and can ship this week. Dark bars need product or QC changes.")

# ------------------------------------------------------------------ 9. recommendations
section("✅ Recommendations", "Suggested fix, owner, effort and success metric for the top reasons.")
rec_rows = []
for reason in share.index[:6]:
    rec = RECOMMENDATIONS.get(reason, RECOMMENDATIONS["other"])
    rows = exploded[exploded["reason"] == reason]
    rec_rows.append({
        "Problem": REASONS[reason]["description"] if reason in REASONS else reason,
        "Evidence": f"{share[reason]:.0f}% of 1-3★ · \"{build_evidence(rows)}\"",
        "Recommended fix": rec["fix"],
        "Owner": REASONS.get(reason, REASONS["other"])["owner"],
        "Effort": rec["effort"],
        "Success metric": rec["metric"],
    })
st.dataframe(pd.DataFrame(rec_rows), hide_index=True, width="stretch")
st.caption("Fixes, effort and metrics are editable in reasons.py → RECOMMENDATIONS.")

# ------------------------------------------------------------------ 10. footer
section("ℹ️ Run details & limitations", "Which AI ran, what it cost, and what this analysis can't tell you.")
if log:
    ai = f"Gemini {log.get('model')}" if log.get("ai_enabled") else f"off ({log.get('ai_note') or 'no key'})"
    st.markdown(f"**AI:** {ai} · **mode:** {MODE_LABELS.get(log.get('mode'), log.get('mode', '?'))} · "
                f"**calls:** {log.get('api_calls', 0)} · **tokens:** {log.get('input_tokens', 0):,} in / "
                f"{log.get('output_tokens', 0):,} out · **cost:** ₹{log.get('cost_inr', 0):.2f}".replace("₹0.00", "₹0")
                + (f" · **last run:** {nice_time(log['run_at'])}" if log.get("run_at") else ""))
    st.caption(f"Tagged by rules only: {log.get('pct_tagged_by_rules_only', 0)}% · with Gemini: "
               f"{log.get('pct_tagged_with_ai', 0)}% · 'other': {log.get('pct_other', 0)}% · "
               f"needs manual review: {log.get('needs_manual_review', 0)} · run time {log.get('run_seconds')}s · "
               f"same run on the paid tier ≈ ${log.get('cost_if_paid_tier_usd', 0):.4f}")
st.markdown("""
**Known limitations**
- Reviews are not returns: most people who return a product never write a review, and reviewers skew negative.
- Small sample, so a few reviews can move a percentage a lot.
- Keyword rules miss Devanagari Hindi and unusual phrasing; Gemini covers this (Smart: unclear reviews only).
- Severity comes from Gemini only; in Smart mode the reviews tagged by rules have no severity.
""")
st.caption(f"{APP_NAME} · {APP_TAGLINE}")
