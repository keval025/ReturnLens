"""Fetch reviews for one product through Apify, map them to our columns and save them.

Flow (the dashboard does these steps one by one so it can show progress):
    raw = fetch_raw(link, max_reviews=100, stars=[1, 2, 3, 4, 5])  # Apify (or saved/mock data)
    reviews = to_reviews(raw["items"], link, stars, max_reviews)    # our columns, bad rows dropped
    saved = save_reviews(reviews, link)                             # data/<platform>/<platform>_<id>.csv

Set MOCK_FETCH=1 in .env to test without calling Apify (no credits used).
"""

import glob
import json
import math
import os
import re
import time
from datetime import datetime, timedelta
from decimal import Decimal

import pandas as pd
from dotenv import load_dotenv

import analyze  # folders + the exact review_id logic used by the analysis
import uploads  # existing_ids(): reviews we already have

load_dotenv()

RAW_DIR = "data/raw"            # every Apify response is saved here as JSON
COLUMNS = ["rating", "title", "body", "date", "verified", "platform", "product_id", "product_url"]
RUN_TIMEOUT_MINUTES = 5         # stop the Apify run after this long
RECENT_HOURS = 24               # "fetched recently" = ask before paying for it again
USD_TO_INR = analyze.USD_TO_INR

STAR_NAMES = {1: "one_star", 2: "two_star", 3: "three_star", 4: "four_star", 5: "five_star"}


class FetchError(Exception):
    """A problem the user can understand and fix; the message is shown as-is."""


def mock_mode():
    """MOCK_FETCH=1 -> never call Apify; use saved or built-in fake data instead."""
    return os.getenv("MOCK_FETCH", "").strip() == "1"


def apify_token():
    """The Apify token, or "" if not set. Never print this."""
    return os.getenv("APIFY_API_TOKEN", "").strip()


# ---------------------------------------------------------------- cleaning helpers

def parse_rating(value):
    """5 / "5" / "4.0" / "5.0 out of 5 stars" -> int 1-5, or None if it isn't a valid rating."""
    match = re.search(r"\d+(?:\.\d+)?", str(value if value is not None else ""))
    if not match:
        return None
    number = float(match.group())
    return int(number) if number in (1, 2, 3, 4, 5) else None


def parse_date(text, today=None):
    """Any date format the actors use -> "14 June 2026" ("" if we can't read it).

    Handles "Reviewed in India on 14 June 2026", "2026-06-14T10:00:00Z", "Aug, 2024"
    (month only -> 1st of the month) and "3 months ago" (counted back from today).
    """
    text = str(text or "").strip()
    if text.lower().startswith("reviewed"):  # "Reviewed in India on 14 June 2026" -> "14 June 2026"
        text = re.sub(r"^.*?\bon\s+", "", text)
    if not text:
        return ""
    today = today or datetime.now()
    found = None
    for fmt in ("%d %B %Y", "%d %b %Y", "%B %d, %Y", "%b %d, %Y", "%Y-%m-%d", "%d/%m/%Y",
                "%b, %Y", "%B, %Y", "%b %Y", "%B %Y"):
        try:
            found = datetime.strptime(text, fmt)
            break
        except ValueError:
            continue
    if found is None:
        try:
            found = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            pass
    if found is None:  # relative dates: "11 days ago", "a month ago", "today"
        low = text.lower()
        match = re.match(r"(\d+|a|an)\s+(day|week|month|year)s?\s+ago", low)
        if match:
            n = 1 if match.group(1) in ("a", "an") else int(match.group(1))
            days = {"day": 1, "week": 7, "month": 30, "year": 365}[match.group(2)] * n
            found = today - timedelta(days=days)
        elif low in ("today", "just now"):
            found = today
        elif low == "yesterday":
            found = today - timedelta(days=1)
    return f"{found.day} {found:%B %Y}" if found else ""


# ---------------------------------------------------------------- actor settings + field mapping
# To swap an actor: change its ID in .env (or "default" below) and adjust build_input/map_item.

def amazon_input(link, max_reviews, stars):
    """axesso_data/amazon-reviews-scraper: 10 reviews per page, max 10 pages per entry.
    One entry per star rating gives a fair mix and lets us go past 100 reviews."""
    if max_reviews <= 10:  # "Test fetch": one page, no star filter = cheapest possible run
        return {"input": [{"asin": link["product_id"], "domainCode": "in", "sortBy": "recent", "maxPages": 1}]}
    pages = min(10, max(1, math.ceil(max_reviews / (10 * len(stars)))))
    return {"input": [{"asin": link["product_id"], "domainCode": "in", "sortBy": "recent",
                       "maxPages": pages, "filterByStar": STAR_NAMES[s]} for s in sorted(stars)]}


def map_amazon(item):
    """Amazon actor output -> our columns."""
    return {"rating": parse_rating(item.get("rating")), "title": item.get("title"),
            "body": item.get("text"), "date": parse_date(item.get("date")),
            "verified": bool(item.get("verified"))}


def flipkart_input(link, max_reviews, stars):
    """khadinakbar/flipkart-reviews-scraper has no star filter: if only 1-3 stars are wanted,
    ask for the most negative reviews first; the star filter is applied after fetching."""
    negative_only = set(stars) <= {1, 2, 3}
    return {"productUrls": [link["clean_url"]], "maxReviews": max_reviews,
            "sortBy": "NEGATIVE_FIRST" if negative_only else "MOST_RECENT",
            "includeProductSummary": False}


def map_flipkart(item):
    """Flipkart actor output -> our columns (product-summary rows are skipped)."""
    if item.get("recordType", "review") != "review":
        return None
    return {"rating": parse_rating(item.get("rating")), "title": item.get("title"),
            "body": item.get("body"), "date": parse_date(item.get("reviewDate")),
            "verified": bool(item.get("isVerifiedPurchase"))}


ACTORS = {
    "amazon": {"env": "APIFY_AMAZON_ACTOR", "default": "axesso_data/amazon-reviews-scraper",
               "price_per_1000_usd": 0.90,  # from the actor's Apify page (Oct 2026); edit if it changes
               "build_input": amazon_input, "map_item": map_amazon},
    "flipkart": {"env": "APIFY_FLIPKART_ACTOR", "default": "khadinakbar/flipkart-reviews-scraper",
                 "price_per_1000_usd": 4.00,  # "from $4.00 / 1,000 reviews" + a small start fee per run
                 "build_input": flipkart_input, "map_item": map_flipkart},
}


def actor_id(platform):
    """The Apify actor to use for a platform (.env value, or the default)."""
    actor = ACTORS[platform]
    return os.getenv(actor["env"], "").strip() or actor["default"]


def estimate_cost_usd(platform, max_reviews):
    """Rough Apify cost before running: reviews x price per review."""
    return max_reviews * ACTORS[platform]["price_per_1000_usd"] / 1000


# ---------------------------------------------------------------- saved raw responses

def raw_files(platform, product_id):
    """Saved raw responses for this product, newest first."""
    paths = glob.glob(os.path.join(RAW_DIR, f"{platform}_{product_id}_*.json"))
    return sorted(paths, key=os.path.getmtime, reverse=True)


def recent_fetch(platform, product_id, hours=RECENT_HOURS):
    """Path of a raw response saved in the last `hours` hours, or None."""
    for path in raw_files(platform, product_id):
        if time.time() - os.path.getmtime(path) < hours * 3600:
            return path
        break  # newest is already too old
    return None


def load_raw(path):
    """Read a saved raw response; returns the list of items."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data["items"] if isinstance(data, dict) else data


def save_raw(link, items, run_input, extra):
    """Save the Apify response exactly as received (plus what we asked for) to data/raw/."""
    os.makedirs(RAW_DIR, exist_ok=True)
    path = f"{RAW_DIR}/{link['platform']}_{link['product_id']}_{datetime.now():%Y%m%d-%H%M}.json"
    record = {"fetched_at": datetime.now().isoformat(timespec="seconds"), "link": link,
              "actor": actor_id(link["platform"]), "run_input": run_input, **extra, "items": items}
    write_atomic(path, json.dumps(record, ensure_ascii=False, indent=1))
    return path


def write_atomic(path, text):
    """Write to '<path>.part', then rename: the real file is never half-written."""
    temp = path + ".part"
    try:
        with open(temp, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.remove(temp)


# ---------------------------------------------------------------- calling Apify

def explain_apify_error(e, actor):
    """Turn an Apify API error into a plain-English message (never includes the token)."""
    code = getattr(e, "status_code", None)
    if code == 401:
        return "Apify rejected the API token. Check APIFY_API_TOKEN (apify.com -> Settings -> API & Integrations)."
    if code == 404:
        return f"Apify actor '{actor}' was not found. Check the actor ID in .env."
    if code == 429:
        return "Apify says too many requests (rate limit). Wait a minute and try again."
    if code in (402, 403):
        return (f"Apify refused to run '{actor}': {getattr(e, 'message', e)}. "
                "You may be out of credits, or need to start a trial/rent the actor on its Apify page.")
    return f"Apify error ({code}): {getattr(e, 'message', e)}"


def call_apify(platform, run_input, estimate_usd):
    """Run the actor and wait (max 5 minutes). Returns (items, apify_cost_usd or None)."""
    if not apify_token():
        raise FetchError("No APIFY_API_TOKEN. Add it in the sidebar (🔑 API keys) or in .env.")
    try:  # optional package: only needed for product links
        from apify_client import ApifyClient
        from apify_client.errors import ApifyApiError
    except ImportError:
        raise FetchError("The apify-client package is not installed. Run: pip install apify-client") from None

    actor = actor_id(platform)
    client = ApifyClient(apify_token())
    try:
        run = client.actor(actor).call(
            run_input=run_input,
            run_timeout=timedelta(minutes=RUN_TIMEOUT_MINUTES),     # Apify stops the run after 5 min
            wait_duration=timedelta(minutes=RUN_TIMEOUT_MINUTES + 1),
            max_total_charge_usd=Decimal(str(round(estimate_usd * 2 + 0.10, 2))),  # safety cap on spend
            logger=None,  # don't stream the actor's log into our terminal
        )
        if run is None:
            raise FetchError(f"Apify did not finish within {RUN_TIMEOUT_MINUTES} minutes. Try fewer reviews.")
        status = str(getattr(run.status, "value", run.status))
        if status == "TIMED-OUT":
            raise FetchError(f"The Apify run took longer than {RUN_TIMEOUT_MINUTES} minutes and was stopped. "
                             "Try fewer reviews.")
        if status != "SUCCEEDED":
            raise FetchError(f"The Apify run ended with status {status}: {run.status_message or 'no details'}. "
                             "The actor may be blocked by the site right now; try again later.")
        items = client.dataset(run.default_dataset_id).list_items(clean=True).items
    except ApifyApiError as e:
        raise FetchError(explain_apify_error(e, actor)) from None
    except FetchError:
        raise
    except Exception as e:  # network down, DNS, proxy... (message never contains the token)
        raise FetchError(f"Could not reach Apify ({type(e).__name__}). Check your internet connection "
                         "and try again.") from None
    cost = getattr(run, "usage_total_usd", None)
    return items, (float(cost) if cost is not None else None)


# ---------------------------------------------------------------- the three steps

def fetch_raw(link, max_reviews=100, stars=(1, 2, 3, 4, 5), use_saved=False, save=True):
    """Step 1: get the raw items.

    link:      the dict from links.parse_link()
    use_saved: True -> reuse the newest saved response (free) instead of calling Apify
    save:      False -> don't keep the response in data/raw/ (used by "Test fetch")
    Returns {"items", "source": "apify"|"saved"|"mock", "raw_path", "apify_cost_usd", "estimate_usd"}.
    """
    platform, stars = link["platform"], sorted(stars)
    run_input = ACTORS[platform]["build_input"](link, max_reviews, stars)
    estimate = estimate_cost_usd(platform, max_reviews)
    result = {"apify_cost_usd": None, "estimate_usd": estimate, "raw_path": None}

    saved = raw_files(platform, link["product_id"])
    if mock_mode() or use_saved:
        if saved:
            result.update(items=load_raw(saved[0]), source="saved", raw_path=saved[0].replace("\\", "/"))
        elif mock_mode():
            result.update(items=fake_items(platform), source="mock")
        else:
            raise FetchError("No saved data for this product yet. Fetch it from Apify first.")
        result["estimate_usd"] = 0.0  # nothing was paid
        return result

    items, cost = call_apify(platform, run_input, estimate)
    path = save_raw(link, items, run_input, {"apify_cost_usd": cost}) if save else None
    result.update(items=items, source="apify", raw_path=path, apify_cost_usd=cost)
    return result


def to_reviews(items, link, stars=(1, 2, 3, 4, 5), max_reviews=None):
    """Step 2a: actor items -> DataFrame with our columns. Drops rows with no rating or no text."""
    map_item = ACTORS[link["platform"]]["map_item"]
    rows = [r for r in (map_item(item) for item in items if isinstance(item, dict)) if r]
    df = pd.DataFrame(rows, columns=["rating", "title", "body", "date", "verified"])
    for col in ["title", "body"]:
        df[col] = analyze.clean_text(df[col])  # decode &amp; etc. and trim
    df = df[df["rating"].notna() & ((df["title"] != "") | (df["body"] != ""))]
    df = df[df["rating"].astype(int).isin(stars)]  # Flipkart can't filter by star, so filter here
    df = df.assign(rating=df["rating"].astype(int), platform=link["platform"],
                   product_id=link["product_id"], product_url=link["clean_url"])
    df = df[~pd.Series(analyze.review_ids(df), index=df.index).duplicated()]  # same review twice in one fetch
    if max_reviews:
        df = df.head(max_reviews)
    if df.empty:
        raise FetchError("Got 0 usable reviews for this product. Check the link, pick more star ratings, "
                         "or try again later (the scraper may be blocked by the site).")
    return df[COLUMNS].reset_index(drop=True)


def save_reviews(reviews, link):
    """Step 2b: merge into data/<platform>/<platform>_<product_id>.csv without duplicates.

    Returns {"path", "fetched", "new", "already_had", "file_rows"}.
    "new" = not in ANY file of this platform yet (same review_id as the analysis uses).
    """
    platform = link["platform"]
    folder = analyze.PLATFORM_DIRS[platform]
    safe_id = re.sub(r"[^A-Za-z0-9_-]", "", link["product_id"])
    path = f"{folder}/{platform}_{safe_id}.csv"
    os.makedirs(folder, exist_ok=True)

    known = uploads.existing_ids(platform)
    ids = analyze.review_ids(reviews)
    new = sum(1 for rid in ids if rid not in known)

    merged = reviews
    if os.path.exists(path):  # keep the old rows, add only reviews this file doesn't have yet
        old = pd.read_csv(path, dtype=str, encoding="utf-8-sig", keep_default_na=False)
        merged = pd.concat([old, reviews.astype(str)], ignore_index=True)
        merged = merged[~pd.Series(analyze.review_ids(merged)).duplicated().values]
    write_atomic(path, merged.to_csv(index=False))
    return {"path": path, "fetched": len(reviews), "new": new, "already_had": len(reviews) - new,
            "file_rows": len(merged)}


# ---------------------------------------------------------------- built-in fake data (MOCK_FETCH=1)

def fake_items(platform):
    """A small fake response in each actor's real output format, for testing without Apify.
    Titles start with [MOCK] so fake reviews are easy to spot (and delete) later."""
    if platform == "amazon":
        return [
            {"statusCode": 200, "reviewId": "M1", "rating": "1.0 out of 5 stars", "title": "[MOCK] Leaks from day one",
             "text": "Water keeps leaking from the joint even after using teflon tape.",
             "date": "Reviewed in India on 14 June 2026", "verified": True},
            {"statusCode": 200, "reviewId": "M2", "rating": "2.0 out of 5 stars", "title": "[MOCK] Hair fall same",
             "text": "Used for 2 months, hair fall still same and no change in skin dryness.",
             "date": "Reviewed in India on 2 July 2026", "verified": True},
            {"statusCode": 200, "reviewId": "M3", "rating": "1.0 out of 5 stars", "title": "[MOCK] बेकार",
             "text": "पानी में कोई फर्क नहीं पड़ा, पैसे बर्बाद हो गए",
             "date": "Reviewed in India on 20 August 2026", "verified": False},
            {"statusCode": 200, "reviewId": "M4", "rating": "3.0 out of 5 stars", "title": "[MOCK] Theek hai",
             "text": "Filter accha hai lekin cartridge jaldi khatam ho jata hai, 1 mahine mein.",
             "date": "Reviewed in India on 1 September 2026", "verified": True},
            {"statusCode": 200, "reviewId": "M5", "rating": "5.0 out of 5 stars", "title": "[MOCK] Great",
             "text": "Easy to install and my hair feels softer.",
             "date": "Reviewed in India on 5 October 2026", "verified": True},
            {"statusCode": 200, "reviewId": "M6", "rating": "", "title": "[MOCK] no rating", "text": "Dropped: no rating.",
             "date": "Reviewed in India on 5 October 2026", "verified": True},
            {"statusCode": 404, "statusMessage": "NOT_FOUND"},  # a "penalty" row: no rating, no text -> dropped
        ]
    return [
        {"recordType": "product_summary", "productKey": "MOCK", "totalReviews": 6},  # skipped
        {"recordType": "review", "rating": 1, "title": "[MOCK] Not fitting",
         "body": "Does not fit my tap, adapter missing.", "isVerifiedPurchase": True, "reviewDate": "Aug, 2026"},
        {"recordType": "review", "rating": 2, "title": "[MOCK] Pressure kam",
         "body": "After installing, water flow is slow and pressure dropped.", "isVerifiedPurchase": True,
         "reviewDate": "3 months ago"},
        {"recordType": "review", "rating": 3, "title": "[MOCK] Okay product",
         "body": "Kuch khaas fark nahi pada baalon mein, but build is fine.", "isVerifiedPurchase": False,
         "reviewDate": "11 days ago"},
        {"recordType": "review", "rating": 1, "title": "[MOCK] Broken box",
         "body": "Received a damaged product, the housing was cracked in the box.", "isVerifiedPurchase": True,
         "reviewDate": "Sep, 2026"},
        {"recordType": "review", "rating": 4, "title": "[MOCK] Good",
         "body": "Works fine &amp; looks nice.", "isVerifiedPurchase": True, "reviewDate": "Oct, 2026"},
        {"recordType": "review", "rating": 2, "title": "", "body": "", "isVerifiedPurchase": True,
         "reviewDate": "Oct, 2026"},  # no text -> dropped
    ]
