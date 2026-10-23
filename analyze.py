"""Clean reviews, tag return reasons (keyword rules + Gemini), write insights.

Outputs in data/output/: tagged_reviews.csv, insights.json, run_log.json, ai_cache.json

Usage:
    python analyze.py                  # Smart mode: rules first, Gemini only for unclear reviews
    python analyze.py --full           # Full AI mode: Gemini tags every 1-3★ review
    python analyze.py --rules          # keyword rules only, no Gemini calls
    python analyze.py path/to/file.csv # one specific file instead of data/amazon/ + data/flipkart/
                                       # (with no data at all, data/sample_reviews.csv is used)

From other code (the dashboard does this):
    from analyze import run_analysis
    summary = run_analysis(mode="smart")   # returns the run summary dict
"""

import glob
import hashlib
import html
import json
import os
import sys
import time
from datetime import datetime

import pandas as pd
from dotenv import load_dotenv

import llm  # the Gemini step (model name, batching, retries, prices)
from reasons import REASONS, RECOMMENDATIONS, match_reasons

load_dotenv()  # reads GEMINI_API_KEY and GEMINI_MODEL from .env if present

# ---- Settings (change here, not inside functions) ----
# One folder per platform; the folder name becomes the review's platform.
PLATFORM_DIRS = {"amazon": "data/amazon", "flipkart": "data/flipkart",
                 "other": "data/other"}  # "other" = uploads from any other site
SAMPLE_FILE = "data/sample_reviews.csv"
OUTPUT_DIR = "data/output"  # generated files only; safe to delete and re-create
OUTPUT_FILE = f"{OUTPUT_DIR}/tagged_reviews.csv"
CACHE_FILE = f"{OUTPUT_DIR}/ai_cache.json"
LOG_FILE = f"{OUTPUT_DIR}/run_log.json"
INSIGHTS_FILE = f"{OUTPUT_DIR}/insights.json"

MODES = ["smart", "full", "rules"]  # Smart (recommended), Full AI, rules only

LONG_REVIEW_WORDS = 60   # Smart mode: longer reviews get a second opinion from Gemini
USD_TO_INR = float(os.getenv("USD_TO_INR", "88"))  # assumption, not a live rate
# Optional columns: filled with "unknown" when a CSV doesn't have them (e.g. hand-collected files).
PRODUCT_COLUMNS = ["product_id", "product_url"]


class AnalysisError(Exception):
    """A problem the user can fix (e.g. no data at all). The message is shown as-is."""


# ---------------------------------------------------------------- Step 1: load + clean

def read_review_file(path):
    """Read one review CSV; return None (with a message) if it is empty or missing key columns."""
    path = path.replace("\\", "/")  # Windows glob gives 'data/amazon\x.csv'; show it one way
    try:
        # utf-8-sig strips the hidden BOM Excel adds; bad bytes become '?' instead of crashing.
        df = pd.read_csv(path, encoding="utf-8-sig", encoding_errors="replace", dtype=str)
    except pd.errors.EmptyDataError:
        print(f"  SKIP {path}: file is empty")
        return None
    except FileNotFoundError:
        print(f"  SKIP {path}: file not found")
        return None
    if "rating" not in df.columns or ("body" not in df.columns and "title" not in df.columns):
        print(f"  SKIP {path}: needs at least 'rating' and 'title'/'body' columns")
        return None
    df["source_file"] = path
    return df


def load_platform_folders():
    """Read every CSV in each platform folder and set platform from the folder name."""
    frames = []
    for platform, folder in PLATFORM_DIRS.items():
        if not os.path.isdir(folder):  # e.g. data/other/ before anything was uploaded there
            continue
        paths = sorted(glob.glob(os.path.join(folder, "*.csv")))
        if not paths:
            print(f"NOTE: {folder}/ has no CSV files -> skipping {platform}")
            continue
        for path in paths:
            df = read_review_file(path)
            if df is None:
                continue
            if "platform" in df.columns:  # the folder wins; just warn if the file disagrees
                other = df["platform"].fillna("").str.strip().str.lower()
                mismatched = int(((other != "") & (other != platform)).sum())
                if mismatched:
                    print(f"  NOTE {path}: {mismatched} rows say another platform -> set to '{platform}'")
            df["platform"] = platform
            frames.append(df)
    return frames


def load_reviews(input_path=None):
    """Return (reviews, data_source): one given file, the platform folders, or the sample."""
    if input_path:
        df = read_review_file(input_path)
        if df is None:
            raise AnalysisError(f"could not use {input_path}")
        if "platform" not in df.columns:
            df["platform"] = "unknown"
        return df, "file"
    frames = load_platform_folders()
    if frames and sum(len(f) for f in frames) > 0:
        return pd.concat(frames, ignore_index=True), "real"
    folders = " and ".join(f"{d}/" for d in PLATFORM_DIRS.values())
    print(f"\nWARNING: no reviews found in {folders}.\n"
          f"         Using SYNTHETIC demo data from {SAMPLE_FILE} instead.\n"
          f"         To use real data, put a CSV in one of those folders and run again.\n")
    df = read_review_file(SAMPLE_FILE)
    if df is None:
        raise AnalysisError("no real reviews and no sample file. Add a CSV to data/amazon/ or data/flipkart/.")
    return df, "sample"


def make_review_id(title, body):
    """Build a stable ID from the review text, so the cache survives row reordering."""
    return "R" + hashlib.md5(f"{title}|{body}".encode("utf-8")).hexdigest()[:8]


def clean_text(values):
    """Decode HTML entities ("don&rsquo;t" -> "don’t", "&amp;" -> "&") and trim spaces."""
    return values.fillna("").astype(str).map(html.unescape).str.strip()


def review_ids(df):
    """The review_id of every row, made exactly as clean_reviews() makes it."""
    title = clean_text(df["title"]) if "title" in df.columns else pd.Series("", index=df.index)
    body = clean_text(df["body"]) if "body" in df.columns else pd.Series("", index=df.index)
    return [make_review_id(t, b) for t, b in zip(title, body)]


def parse_month(date_text):
    """Turn '12 August 2026' (or similar) into '2026-08'; return 'unknown' if unparseable."""
    for fmt in ("%d %B %Y", "%d %b %Y", "%Y-%m-%d", "%d/%m/%Y",
                "%B %d, %Y", "%b %d, %Y", "%d-%m-%Y", "%b %Y", "%B %Y", "%b, %Y", "%B, %Y"):
        try:
            return datetime.strptime(str(date_text).strip(), fmt).strftime("%Y-%m")
        except ValueError:
            continue
    return "unknown"


def clean_reviews(df):
    """Fix types, drop empty/duplicate rows, add review_id, month and text columns."""
    stats = {"reviews_raw": len(df)}

    # Make sure every expected column exists, even if the CSV left one out.
    for col, default in [("title", ""), ("body", ""), ("date", ""),
                         ("verified", "false"), ("platform", "unknown")]:
        if col not in df.columns:
            df[col] = default
    for col in PRODUCT_COLUMNS:  # optional: product_id / product_url -> "unknown" if missing or blank
        if col not in df.columns:
            df[col] = "unknown"
        df[col] = df[col].fillna("").astype(str).str.strip().replace("", "unknown")
    # Decode HTML entities from copy-pasted pages before anything else,
    # so duplicates, review_ids and keyword matching all see the real text.
    df["title"] = clean_text(df["title"])
    df["body"] = clean_text(df["body"])
    df["platform"] = df["platform"].fillna("unknown").astype(str).str.strip().str.lower()

    # Ratings must be whole numbers 1-5; anything else is dropped.
    df["rating"] = pd.to_numeric(df["rating"], errors="coerce")
    bad_rating = ~df["rating"].isin([1, 2, 3, 4, 5])
    stats["dropped_bad_rating"] = int(bad_rating.sum())
    df = df[~bad_rating].copy()
    df["rating"] = df["rating"].astype(int)

    # Drop reviews with no words at all.
    empty = (df["title"] == "") & (df["body"] == "")
    stats["dropped_empty"] = int(empty.sum())
    df = df[~empty]

    # Drop exact duplicates (same title + body, ignoring case) within each platform.
    # The same text on Amazon AND Flipkart is kept twice: they are separate reviews.
    # Rows with a known product_id are checked first, so a fetched copy wins over a hand-copied one.
    order = df["product_id"].eq("unknown").sort_values(kind="stable").index
    dup = (df.loc[order].assign(_k=(df["title"] + "|" + df["body"]).str.lower())
           .duplicated(["platform", "_k"]).reindex(df.index))
    stats["dropped_duplicates"] = int(dup.sum())
    stats["duplicates_by_platform"] = df.loc[dup, "platform"].value_counts().to_dict()
    df = df[~dup].copy()

    # Add review_id where missing.
    if "review_id" not in df.columns:
        df["review_id"] = ""
    df["review_id"] = df["review_id"].fillna("").astype(str)
    missing_id = df["review_id"].str.strip() == ""
    df.loc[missing_id, "review_id"] = [make_review_id(t, b) for t, b in
                                       zip(df.loc[missing_id, "title"], df.loc[missing_id, "body"])]

    df["month"] = df["date"].apply(parse_month)
    stats["unknown_dates"] = int((df["month"] == "unknown").sum())
    df["verified"] = df["verified"].astype(str).str.strip().str.lower().isin(["true", "yes", "1", "y"])

    # review_id is a hash of title+body, so the same text on two platforms gets the same ID.
    shared = int(df["review_id"].duplicated().sum())
    if shared:
        print(f"NOTE: {shared} reviews appear word-for-word on more than one platform "
              "(they share a review_id, so eval.py and the AI cache treat them as one).")

    # raw_text keeps original casing (for quotes); text is lowercase (for matching).
    # Python strings are Unicode, so Hindi/Devanagari passes through untouched.
    df["raw_text"] = (df["title"] + ". " + df["body"]).str.strip(". ")
    df["text"] = df["raw_text"].str.lower()
    df["word_count"] = df["raw_text"].str.split().str.len()

    stats["reviews_clean"] = len(df)
    return df.reset_index(drop=True), stats


# ---------------------------------------------------------------- Step 2: rules

def needs_ai(rule_reasons, word_count):
    """Smart mode: send a negative review to Gemini if no rule matched, or it is long + unclear."""
    return len(rule_reasons) == 0 or (word_count > LONG_REVIEW_WORDS and len(rule_reasons) <= 1)


def send_to_gemini(rule_reasons, word_count, mode):
    """Which 1-3★ reviews Gemini sees: Smart = only unclear ones, Full AI = all of them."""
    return mode == "full" or (mode == "smart" and needs_ai(rule_reasons, word_count))


# ---------------------------------------------------------------- Step 3: Gemini tagging

def load_cache():
    """Read saved Gemini results so re-runs do not call the API for the same review twice."""
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            print("WARNING: ai_cache.json is unreadable, starting with an empty cache")
    return {}


def save_cache(cache):
    """Write the cache to disk (UTF-8, so Hindi quotes stay readable)."""
    write_json(CACHE_FILE, cache)


def write_json(path, data):
    """Write JSON via a temporary file + rename, so the file is never half-written."""
    temp = path + ".part"
    with open(temp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(temp, path)


def cache_key(review_id, model, mode):
    """Results are saved per review, model AND mode, so changing either asks Gemini fresh."""
    return f"{review_id}|{model}|{mode}"


def allowed_reasons():
    """{reason: one-line meaning} from reasons.py, given to Gemini in every prompt."""
    return {name: info["description"] for name, info in REASONS.items()}


def tag_with_ai(to_send, cache, log, mode):
    """Tag reviews with Gemini; returns {review_id: result or None}.

    None means Gemini could not answer: the review is marked needs_manual_review
    and is not cached, so the next run tries it again.
    """
    model = llm.model_name()
    if mode == "rules":
        log["ai_enabled"], log["ai_note"] = False, "rules-only mode"
        return {}
    results = {rid: cache[cache_key(rid, model, mode)] for rid in to_send["review_id"]
               if cache_key(rid, model, mode) in cache}
    log["ai_cache_hits"] = len(results)
    pending = to_send[~to_send["review_id"].isin(results.keys())]
    log["ai_enabled"] = True
    if pending.empty:
        return results

    if not llm.api_key():
        print(f"WARNING: GEMINI_API_KEY not set -> skipping Gemini for {len(pending)} reviews. "
              "Results are rules-only; unmatched reviews are labelled 'other'.")
        log["ai_enabled"], log["ai_note"] = bool(results), "no GEMINI_API_KEY"
        return results

    client = llm.make_client()
    for start in range(0, len(pending), llm.BATCH_SIZE):
        if start:
            time.sleep(llm.PAUSE_BETWEEN_BATCHES)  # stay under the free-tier per-minute limit
        batch = pending.iloc[start:start + llm.BATCH_SIZE]
        reviews = [{"review_id": r.review_id, "rating": r.rating, "title": r.title, "body": r.body}
                   for r in batch.itertuples()]
        try:
            answers = llm.classify_batch(reviews, allowed_reasons(), client=client, usage=log["usage"])
        except (llm.GeminiKeyError, llm.GeminiQuotaError) as e:
            print(f"WARNING: {e} -> stopping Gemini, keeping rules-only results for the rest")
            log["ai_note"] = str(e)
            log["ai_enabled"] = any(r is not None for r in results.values())  # False if Gemini answered nothing
            for rid in pending["review_id"]:  # not reached -> needs_manual_review, retried next run
                results.setdefault(rid, None)
            break
        for answer in answers:
            rid = answer["review_id"]
            if answer.get("needs_manual_review"):
                results[rid] = None  # not cached -> retried on the next run
            else:
                results[rid] = answer
                cache[cache_key(rid, model, mode)] = {**answer, "model": model, "mode": mode}
        save_cache(cache)  # save after every batch so a crash loses at most one batch
        print(f"  Gemini batch {start // llm.BATCH_SIZE + 1}: {len(batch)} reviews")
    return results


# ---------------------------------------------------------------- Put it together

def final_reasons(rule, ai_reasons, mode):
    """The reasons we report for one review.

    Smart:   rules + Gemini combined (Gemini only saw unclear reviews).
    Full AI: Gemini's reasons (the rules are kept in rule_reasons_only for comparison).
    No Gemini answer: the rules, or "other" if no rule matched.
    """
    if ai_reasons is None:
        return list(rule) or ["other"]
    if mode == "full":
        return list(ai_reasons) or ["other"]
    merged = sorted(set(rule) | set(ai_reasons))
    if len(merged) > 1 and "other" in merged:
        merged.remove("other")  # "other" only makes sense when nothing else applies
    return merged


def combine(row, ai_results, mode):
    """Build the output columns for one review from its rule reasons and Gemini's answer."""
    blank = {"reasons": "", "tag_source": "not_tagged_4_5_star", "confidence": "", "evidence_quote": "",
             "rule_reasons_only": "", "ai_reasons": "", "primary_reason": "", "sentiment": "",
             "severity": "", "language": ""}
    if row["rating"] > 3:
        return pd.Series(blank)
    rule = row["rule_reasons"]
    out = {**blank, "rule_reasons_only": "|".join(rule) or "other"}  # what rules alone would say

    if row["review_id"] not in ai_results:  # rules were enough (Smart), or Gemini was off
        reasons = final_reasons(rule, None, mode)
        return pd.Series({**out, "reasons": "|".join(reasons), "primary_reason": reasons[0],
                          "tag_source": "rule" if rule else "no_match", "confidence": "rule" if rule else ""})
    ai = ai_results[row["review_id"]]
    if ai is None:  # Gemini failed for this review
        reasons = final_reasons(rule, None, mode)
        return pd.Series({**out, "reasons": "|".join(reasons), "primary_reason": reasons[0],
                          "tag_source": "needs_manual_review"})
    reasons = final_reasons(rule, ai["reasons"], mode)
    source = "ai" if (mode == "full" or not rule) else "rule+ai"
    primary = ai.get("primary_reason") if ai.get("primary_reason") in reasons else reasons[0]
    return pd.Series({**out, "reasons": "|".join(reasons), "tag_source": source,
                      "confidence": ai.get("confidence", ""), "evidence_quote": ai.get("evidence_quote", ""),
                      "ai_reasons": "|".join(ai["reasons"]), "primary_reason": primary,
                      "sentiment": ai.get("sentiment", ""), "severity": ai.get("severity", ""),
                      "language": ai.get("language", "")})


def percent(part, total):
    """Return part/total as a percentage rounded to 1 decimal (0 if total is 0)."""
    return round(100 * part / total, 1) if total else 0.0


# ---------------------------------------------------------------- Step 4: insights

def short_quote(text, limit=160):
    """Cut a review to a readable length for the insights prompt."""
    text = str(text).strip()
    return text if len(text) <= limit else text[:limit - 3] + "..."


def build_aggregates(df):
    """Numbers (and a few short quotes) for the insights call -- NOT the full reviews."""
    neg = df[df["rating"] <= 3]
    exploded = neg.assign(reason=neg["reasons"].str.split("|")).explode("reason")

    def reason_table(rows, n):
        return {r: {"count": int(c), "pct": percent(c, n)} for r, c in rows["reason"].value_counts().items()}

    quotes = {}
    for reason, rows in exploded.groupby("reason"):
        # Gemini's evidence quotes first, then the shortest reviews (short = easy to read).
        rows = rows.assign(_has=rows["evidence_quote"].astype(str) != "", _len=rows["raw_text"].str.len())
        rows = rows.sort_values(["_has", "_len"], ascending=[False, True]).head(3)
        quotes[reason] = [short_quote(q if q else t) for q, t in zip(rows["evidence_quote"], rows["raw_text"])]

    dated = exploded[exploded["month"] != "unknown"]
    top5 = exploded["reason"].value_counts().index[:5]
    months = sorted(dated["month"].unique())[-12:]  # last 12 months is enough for a trend
    trend = {m: {r: int(c) for r, c in dated[(dated["month"] == m) & dated["reason"].isin(top5)]
                 ["reason"].value_counts().items()} for m in months}
    severe = exploded[exploded["severity"].astype(str) == "3"]["reason"].value_counts()
    return {
        "product": "WaterScience CLEO Shower & Tap Filter",
        "total_reviews": int(len(df)),
        "negative_reviews_1_to_3_star": int(len(neg)),
        "reasons_all_platforms": reason_table(exploded, len(neg)),
        "reasons_by_platform": {p: {"negative_reviews": int(len(g)),
                                    "reasons": reason_table(exploded[exploded["platform"] == p], len(g))}
                                for p, g in neg.groupby("platform")},
        "top_quotes_per_reason": quotes,
        "rating_distribution": {p: {str(r): int(n) for r, n in g["rating"].value_counts().sort_index().items()}
                                for p, g in df.groupby("platform")},
        "trend_by_month_top5": trend,
        "severity_3_count_by_reason": {r: int(c) for r, c in severe.items()},
    }


def fallback_insights(agg, note):
    """Insights without Gemini: built from REASONS and the editable RECOMMENDATIONS in reasons.py."""
    reasons = agg["reasons_all_platforms"]
    top = [r for r in reasons if r != "other"][:5] or list(reasons)[:5]
    n = agg["negative_reviews_1_to_3_star"]
    named = ", ".join(f"{r.replace('_', ' ')} ({reasons[r]['pct']}%)" for r in top[:3])
    recs = [{"fix": RECOMMENDATIONS.get(r, RECOMMENDATIONS["other"])["fix"],
             "owner": REASONS[r]["owner"],
             "effort": RECOMMENDATIONS.get(r, RECOMMENDATIONS["other"])["effort"],
             "expected_impact": f"Targets {reasons[r]['pct']}% of negative reviews",
             "success_metric": RECOMMENDATIONS.get(r, RECOMMENDATIONS["other"])["metric"]} for r in top]
    return {
        "executive_summary": (f"{n} negative (1-3★) reviews were analysed. The biggest reasons are {named}. "
                              "The fixes below come from the editable RECOMMENDATIONS in reasons.py."),
        "top_issues": [{"reason": r, "why": REASONS[r]["description"], "evidence_pct": reasons[r]["pct"],
                        "example_quote": (agg["top_quotes_per_reason"].get(r) or [""])[0]} for r in top],
        "recommendations": recs,
        "quick_wins": [rec["fix"] for r, rec in zip(top, recs) if REASONS[r]["fix_cost"] == "Cheap"],
        "risks_or_gaps": ["Reviews are not returns: most people who return a product never write a review.",
                          f"Small sample ({n} negative reviews): one review moves a percentage noticeably.",
                          f"Written from templates, not by Gemini ({note})."],
    }


def make_insights(df, mode, log):
    """ONE Gemini call to summarise the results (skipped if the numbers haven't changed).

    Falls back to reasons.py RECOMMENDATIONS if Gemini is off or fails. Saves insights.json.
    """
    agg = build_aggregates(df)
    model = llm.model_name()
    key = hashlib.md5((json.dumps(agg, sort_keys=True, ensure_ascii=False) + model).encode("utf-8")).hexdigest()

    # Same numbers as last time -> reuse the saved Gemini insights (0 calls).
    try:
        with open(INSIGHTS_FILE, encoding="utf-8") as f:
            old = json.load(f)
        if old.get("aggregates_hash") == key and old.get("source") == "gemini":
            log["insights_source"] = "gemini (cached)"
            return old
    except (OSError, json.JSONDecodeError):
        pass

    insights, note = None, ""
    if mode == "rules":
        note = "rules-only mode"
    elif not llm.api_key():
        note = "no GEMINI_API_KEY"
    elif "daily limit" in log.get("ai_note", ""):
        note = log["ai_note"]  # tagging already hit today's limit; this call would fail too
    else:
        try:
            insights = llm.generate_insights(agg, allowed_reasons(), usage=log["usage"])
            note = "" if insights else "Gemini call failed"
        except (llm.GeminiKeyError, llm.GeminiQuotaError) as e:
            note = str(e)
    source = "gemini" if insights else "fallback"
    if not insights:
        print(f"NOTE: insights built from reasons.py RECOMMENDATIONS ({note})")
        insights = fallback_insights(agg, note)
    result = {"source": source, "note": note, "model": model if source == "gemini" else "",
              "mode": mode, "generated_at": datetime.now().isoformat(timespec="seconds"),
              "aggregates_hash": key, **insights}
    write_json(INSIGHTS_FILE, result)
    log["insights_source"] = source
    return result


# ---------------------------------------------------------------- run everything

def run_analysis(input_path=None, mode="smart", progress=None):
    """Run the whole pipeline, save the outputs, print a summary and return it as a dict.

    input_path: one CSV to analyse instead of the platform folders (optional).
    mode:       "smart" (rules first, Gemini for unclear reviews), "full" (Gemini tags every
                1-3★ review) or "rules" (no Gemini at all).
    progress:   optional function called with a short step name, e.g. progress("Cleaning…").
    Raises AnalysisError with a readable message if there is nothing to analyse.
    """
    if mode not in MODES:
        raise AnalysisError(f"unknown mode '{mode}' (use one of: {', '.join(MODES)})")
    step = progress or (lambda name: None)
    started = time.time()
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    step("Cleaning…")
    df, data_source = load_reviews(input_path)
    rows_per_file = df.groupby(["platform", "source_file"], sort=False).size()
    df, stats = clean_reviews(df)
    log = {"run_at": datetime.now().isoformat(timespec="seconds"), "data_source": data_source,
           "input_files": sorted(df["source_file"].unique().tolist()),
           "mode": mode, "provider": "gemini", "model": llm.model_name(), **stats,
           "reviews_by_platform": df["platform"].value_counts().to_dict(),
           "ai_enabled": None, "ai_note": "", "ai_cache_hits": 0, "usage": {}}

    step("Keyword rules…")
    negative = df["rating"] <= 3
    df["rule_reasons"] = [match_reasons(t) if neg else [] for t, neg in zip(df["text"], negative)]
    send_mask = pd.Series([neg and send_to_gemini(r, w, mode) for neg, r, w in
                           zip(negative, df["rule_reasons"], df["word_count"])], index=df.index)

    step("Gemini analysis…")
    ai_results = tag_with_ai(df[send_mask], load_cache(), log, mode)
    tagging = dict(log["usage"])  # calls/tokens for tagging only (before the insights call)
    df = pd.concat([df, df.apply(combine, axis=1, ai_results=ai_results, mode=mode)], axis=1)
    df = df.drop(columns=["rule_reasons", "text"])
    df.to_csv(OUTPUT_FILE + ".part", index=False, encoding="utf-8")
    os.replace(OUTPUT_FILE + ".part", OUTPUT_FILE)  # never leave a half-written results file

    step("Building insights…")
    make_insights(df, mode, log)

    # ---- Run log ----
    usage = log.pop("usage")
    n_neg = int(negative.sum())
    src = df.loc[negative, "tag_source"]
    cost_usd, paid_usd = llm.cost_usd(usage.get("input_tokens", 0), usage.get("output_tokens", 0))
    log.update({
        "api_calls": usage.get("calls", 0),  # tagging + insights
        "tagging_calls": tagging.get("calls", 0),
        "insights_calls": usage.get("calls", 0) - tagging.get("calls", 0),
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "negative_reviews": n_neg,
        "positive_reviews": int((~negative).sum()),
        "sent_to_ai": int(send_mask.sum()),
        "pct_tagged_by_rules_only": percent((src == "rule").sum(), n_neg),
        "pct_tagged_with_ai": percent(src.isin(["ai", "rule+ai"]).sum(), n_neg),
        "pct_no_match": percent((src == "no_match").sum(), n_neg),
        "pct_other": percent((df.loc[negative, "reasons"] == "other").sum(), n_neg),
        "needs_manual_review": int((src == "needs_manual_review").sum()),
        "free_tier": llm.FREE_TIER,
        "cost_usd": round(cost_usd, 5),
        "cost_inr": round(cost_usd * USD_TO_INR, 3),
        "cost_if_paid_tier_usd": round(paid_usd, 5),
        # {"amazon|B0XXXXXXXX": 120, "flipkart|unknown": 99} -- reviews per product
        "products": {f"{plat}|{pid}": int(n) for (plat, pid), n in
                     df.groupby(["platform", "product_id"]).size().items()},
        # {"amazon": {"data/amazon/amazon_01.csv": 60, ...}, ...} -- rows read from each file
        "files_read": {p: {f: int(n) for f, n in rows_per_file[p].items()}
                       for p in rows_per_file.index.get_level_values(0).unique()},
        # {"amazon": {"1": 20, "2": 20, ...}, ...} -- after cleaning
        "rating_distribution": {p: {str(r): int(n) for r, n in g["rating"].value_counts().sort_index().items()}
                                for p, g in df.groupby("platform")},
        "run_seconds": round(time.time() - started, 2),
    })
    write_json(LOG_FILE, log)

    print_summary(df, rows_per_file, log)
    print(f"\nWrote {OUTPUT_FILE}, {INSIGHTS_FILE} and {LOG_FILE}")
    return log


def print_summary(df, rows_per_file, log):
    """Print files read, duplicates skipped and the rating mix, per platform."""
    print(f"\n=== Data summary ({log['data_source']} data) ===")
    for platform in rows_per_file.index.get_level_values(0).unique():
        files = rows_per_file[platform]
        print(f"{platform}: {len(files)} file(s)")
        for path, rows in files.items():
            print(f"    {path}: {rows} rows")
        print(f"    duplicates skipped: {log['duplicates_by_platform'].get(platform, 0)}")
    print(f"dropped (bad rating / empty text): {log['dropped_bad_rating']} / {log['dropped_empty']}")
    print(f"final rows: {len(df)}")
    print("\nRating distribution per platform:")
    print(pd.crosstab(df["platform"], df["rating"], margins=True, margins_name="total").to_string())

    print("\n=== Tagging ===")
    for key in ["mode", "negative_reviews", "sent_to_ai", "ai_enabled", "model", "ai_cache_hits",
                "tagging_calls", "insights_calls", "input_tokens", "output_tokens", "insights_source",
                "pct_tagged_by_rules_only", "pct_tagged_with_ai", "pct_no_match", "pct_other",
                "needs_manual_review", "cost_usd", "run_seconds"]:
        print(f"{key:>26}: {log.get(key)}")


if __name__ == "__main__":
    # python analyze.py [--full | --rules] [path/to/file.csv]
    args = sys.argv[1:]
    cli_mode = "full" if "--full" in args else "rules" if "--rules" in args else "smart"
    paths = [a for a in args if not a.startswith("--")]
    try:
        run_analysis(paths[0] if paths else None, mode=cli_mode)
    except AnalysisError as e:
        sys.exit(f"ERROR: {e}")
