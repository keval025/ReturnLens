"""Google Gemini: (1) tag reviews with return reasons, (2) write the AI Insights summary.

Only this file talks to Gemini. analyze.py calls:
    classify_batch(reviews, allowed_reasons)   # up to 20 reviews per call
    generate_insights(aggregates, allowed_reasons)  # ONE call per analysis, numbers only
Get a free key at https://aistudio.google.com (Get API key) and put it in .env as GEMINI_API_KEY.
"""

import json
import os
import re
import time

from dotenv import load_dotenv

load_dotenv()  # reads GEMINI_API_KEY / GEMINI_MODEL from .env if present

# ---- Settings ----
# Free-tier Flash model. Measured on 23 Oct 2026 with this app: gemini-3.5-flash-lite answered every call
# (Smart run 30 s, Full AI 89 s, 97% on the hand labels); gemini-3.8-flash took ~2 min per call or timed out
# (high demand); gemini-3.6-flash was often "busy" (503) and allows only 20 requests/day on the free tier.
# Google's docs recommend 3.5 Flash-Lite or 3.8 Flash for new projects. Switch any time with GEMINI_MODEL in .env.
DEFAULT_MODEL = "gemini-3.5-flash-lite"
BATCH_SIZE = 20                     # reviews per call: the instructions are sent once per batch, not per review
PAUSE_BETWEEN_BATCHES = 1           # seconds; keeps us under the free-tier requests-per-minute limit
RATE_LIMIT_WAITS = [2, 5, 10]       # seconds to wait before each retry after a 429 / quota / "busy" error
REQUEST_TIMEOUT = 90                # seconds; a stuck call is stopped and retried instead of hanging the app
THINKING_LEVEL = "low"              # tagging is simple; little thinking = fewer tokens and faster answers

# Free tier costs nothing. Paid-tier prices (USD per 1M tokens, gemini-3.5-flash-lite, Oct 2026;
# gemini-3.8-flash is 0.75 / 3.75) are kept so the run log can show what a run would cost on the paid tier.
FREE_TIER = os.getenv("GEMINI_FREE_TIER", "1") != "0"
PRICE_INPUT_PER_M = float(os.getenv("GEMINI_PRICE_INPUT_PER_M", "0.30"))
PRICE_OUTPUT_PER_M = float(os.getenv("GEMINI_PRICE_OUTPUT_PER_M", "2.50"))

# Allowed values for the extra fields Gemini fills in.
SENTIMENTS = ["negative", "mixed", "positive"]
LANGUAGES = ["english", "hindi", "hinglish"]
OWNERS = ["Product/QC", "Listing", "Packaging", "Instructions", "Ops/Service", "Pricing"]
EFFORTS = ["Low", "Med", "High"]


class GeminiKeyError(Exception):
    """The API key is missing or was rejected; no point trying more calls."""


class GeminiQuotaError(Exception):
    """The free-tier DAILY request limit for this model is used up; no point trying more calls today."""


def model_name():
    """The Gemini model to use (GEMINI_MODEL in .env, or the default)."""
    return os.getenv("GEMINI_MODEL", "").strip() or DEFAULT_MODEL


def api_key():
    """The Gemini API key, or "" if it is not set. Never print this."""
    return os.getenv("GEMINI_API_KEY", "").strip()


def make_client():
    """Create the Gemini client. Imported here so rules-only runs work without the package."""
    from google import genai
    from google.genai import types
    return genai.Client(api_key=api_key(), http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT * 1000))  # ms


def cost_usd(input_tokens, output_tokens):
    """(cost actually paid, cost if this were the paid tier), both in USD."""
    paid = (input_tokens * PRICE_INPUT_PER_M + output_tokens * PRICE_OUTPUT_PER_M) / 1e6
    return (0.0 if FREE_TIER else paid), paid


# ---------------------------------------------------------------- prompts + schemas

def classify_prompt(allowed_reasons):
    """Instructions for tagging. allowed_reasons = {name: one-line meaning}."""
    reason_lines = "\n".join(f"- {name}: {meaning}" for name, meaning in allowed_reasons.items())
    return (
        "You tag customer reviews of a hard-water shower & tap filter sold in India "
        "with the reasons the customer was unhappy.\n"
        "Reviews may be in English, Hindi (Devanagari) or Hinglish (Hindi in Latin letters).\n\n"
        f"Allowed reasons (use these exact names, nothing else):\n{reason_lines}\n\n"
        "For every review return:\n"
        "- reasons: every allowed reason that applies (a review can have more than one); "
        "[\"other\"] if none fit.\n"
        "- primary_reason: the single most important one of those reasons.\n"
        "- sentiment: negative, mixed or positive.\n"
        "- severity: 1 = mild annoyance, 2 = clear problem, 3 = product broke, leaked, arrived damaged or is unsafe.\n"
        "- evidence_quote: an exact short quote (at most 15 words) copied from the title or body, "
        "in its original language. Do not translate or paraphrase.\n"
        "- language: english, hindi or hinglish.\n"
        "- confidence: 0 to 1.\n"
        "Return exactly one object per review_id you were given."
    )


def classify_schema(allowed_reasons):
    """JSON schema for tagging: a list with one object per review."""
    reason = {"type": "STRING", "enum": list(allowed_reasons)}
    return {
        "type": "ARRAY",
        "items": {
            "type": "OBJECT",
            "properties": {
                "review_id": {"type": "STRING"},
                "reasons": {"type": "ARRAY", "items": reason},
                "primary_reason": reason,
                "sentiment": {"type": "STRING", "enum": SENTIMENTS},
                "severity": {"type": "INTEGER"},
                "evidence_quote": {"type": "STRING"},
                "language": {"type": "STRING", "enum": LANGUAGES},
                "confidence": {"type": "NUMBER"},
            },
            "required": ["review_id", "reasons", "primary_reason", "sentiment", "severity",
                         "evidence_quote", "language", "confidence"],
        },
    }


def insights_prompt(allowed_reasons):
    """Instructions for the one summary call. Gemini sees numbers and a few quotes, not all reviews."""
    reason_lines = "\n".join(f"- {name}: {meaning}" for name, meaning in allowed_reasons.items())
    return (
        "You are a product-quality analyst for WaterScience, which sells the CLEO hard-water shower & tap "
        "filter on Amazon.in and Flipkart. You get aggregated numbers from tagged 1-3 star reviews "
        "(reason counts and % per platform, top quotes per reason, rating distribution, monthly trend, "
        "count of severity-3 complaints).\n\n"
        f"Reason meanings:\n{reason_lines}\n\n"
        "Write for a busy product manager. Use ONLY the numbers given; never invent numbers. "
        "evidence_pct must be the % of negative reviews given for that reason. example_quote must be copied "
        "from the quotes given.\n"
        "- executive_summary: exactly 3 sentences.\n"
        "- top_issues: the 3-5 biggest problems, biggest first.\n"
        f"- recommendations: concrete fixes; owner is one of {', '.join(OWNERS)}; effort is Low, Med or High.\n"
        "- quick_wins: cheap listing / packaging / instruction fixes that can ship this week.\n"
        "- risks_or_gaps: limits of this data (sample size, reviews are not returns, etc.)."
    )


def insights_schema(allowed_reasons):
    """JSON schema for the insights call."""
    return {
        "type": "OBJECT",
        "properties": {
            "executive_summary": {"type": "STRING"},
            "top_issues": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
                "reason": {"type": "STRING", "enum": list(allowed_reasons)},
                "why": {"type": "STRING"},
                "evidence_pct": {"type": "NUMBER"},
                "example_quote": {"type": "STRING"},
            }, "required": ["reason", "why", "evidence_pct", "example_quote"]}},
            "recommendations": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
                "fix": {"type": "STRING"},
                "owner": {"type": "STRING", "enum": OWNERS},
                "effort": {"type": "STRING", "enum": EFFORTS},
                "expected_impact": {"type": "STRING"},
                "success_metric": {"type": "STRING"},
            }, "required": ["fix", "owner", "effort", "expected_impact", "success_metric"]}},
            "quick_wins": {"type": "ARRAY", "items": {"type": "STRING"}},
            "risks_or_gaps": {"type": "ARRAY", "items": {"type": "STRING"}},
        },
        "required": ["executive_summary", "top_issues", "recommendations", "quick_wins", "risks_or_gaps"],
    }


def make_config(system_prompt, schema):
    """Structured JSON output, temperature 0 (same answer every time), low thinking."""
    from google.genai import types
    return types.GenerateContentConfig(
        system_instruction=system_prompt,
        response_mime_type="application/json",
        response_schema=schema,
        temperature=0,
        thinking_config=types.ThinkingConfig(thinking_level=THINKING_LEVEL),
    )


# ---------------------------------------------------------------- calling Gemini

def error_text(e):
    """Status code + message of an API error, for matching (the key is never part of it)."""
    return f"{getattr(e, 'code', '')} {getattr(e, 'status', '')} {e}"


def is_rate_limit(e):
    """True for 'too many requests' / 'quota exceeded' / 'model busy' errors, which are worth waiting for."""
    text = error_text(e)
    return getattr(e, "code", None) in (429, 503) or "RESOURCE_EXHAUSTED" in text or "UNAVAILABLE" in text


def is_key_problem(e):
    """True if Google rejected the API key (retrying will not help)."""
    text = error_text(e)
    return getattr(e, "code", None) in (401, 403) or "API_KEY_INVALID" in text or "API key not valid" in text


def quota_details(e):
    """From a 429 error: (is it the DAILY limit?, the limit number, seconds Google asks us to wait)."""
    details = getattr(e, "details", None)
    error = details.get("error", details) if isinstance(details, dict) else {}
    daily, limit, delay = False, None, None
    for item in error.get("details", []) if isinstance(error, dict) else []:
        for v in item.get("violations", []):
            if "PerDay" in str(v.get("quotaId", "")):
                daily, limit = True, v.get("quotaValue")
        if str(item.get("retryDelay", "")).endswith("s"):  # e.g. "37s"
            try:
                delay = float(item["retryDelay"][:-1])
            except ValueError:
                pass
    return daily, limit, delay


def generate(client, contents, config, sleep=time.sleep):
    """One Gemini call. On a rate limit / busy model, wait 2s, 5s, 10s and retry (3 retries), then give up."""
    for wait in [*RATE_LIMIT_WAITS, None]:
        try:
            return client.models.generate_content(model=model_name(), contents=contents, config=config)
        except Exception as e:
            if is_key_problem(e):
                raise GeminiKeyError("Gemini rejected the API key. Check GEMINI_API_KEY.") from None
            daily, limit, delay = quota_details(e) if getattr(e, "code", None) == 429 else (False, None, None)
            if daily:  # waiting seconds won't help: stop until the quota resets
                hours = f", resets in about {delay / 3600:.0f} h" if delay else ""
                raise GeminiQuotaError(f"Gemini free-tier daily limit reached for {model_name()} "
                                       f"({limit or '?'} requests/day{hours}). Try later, or set another "
                                       "GEMINI_MODEL in .env (each model has its own limit).") from None
            if is_rate_limit(e) and wait is not None:
                if delay and delay <= 60:  # per-minute limit: wait as long as Google asks
                    wait = max(wait, delay)
                print(f"  Gemini busy / rate limit hit -> waiting {wait}s, then retrying")
                sleep(wait)
                continue
            raise


def call_json(client, contents, config, parse, usage, sleep=time.sleep):
    """Call Gemini and parse the JSON answer; one retry if the call or the JSON fails.

    Returns parse(text), or None if both attempts failed. Adds calls/tokens to `usage`.
    """
    for attempt in (1, 2):  # first try + one retry
        try:
            response = generate(client, contents, config, sleep=sleep)
        except (GeminiKeyError, GeminiQuotaError):
            raise  # stop everything: retrying won't help
        except Exception as e:  # network trouble, server error, rate limit after 3 retries...
            print(f"  Gemini call failed on attempt {attempt}: {type(e).__name__}: {str(e)[:150]}")
            continue
        usage["calls"] = usage.get("calls", 0) + 1
        meta = getattr(response, "usage_metadata", None)
        usage["input_tokens"] = usage.get("input_tokens", 0) + (getattr(meta, "prompt_token_count", 0) or 0)
        # Thinking tokens are billed as output on the paid tier, so count them as output.
        usage["output_tokens"] = (usage.get("output_tokens", 0) + (getattr(meta, "candidates_token_count", 0) or 0)
                                  + (getattr(meta, "thoughts_token_count", 0) or 0))
        try:
            return parse(response.text)
        except (ValueError, TypeError, AttributeError) as e:  # json errors are ValueErrors
            print(f"  Gemini gave unusable JSON on attempt {attempt} ({str(e)[:100]})")
    return None


# ---------------------------------------------------------------- 1. tagging reviews

def squash(text):
    """Lowercase and collapse spaces, to check that a quote really appears in the review."""
    return re.sub(r"\s+", " ", str(text)).strip().lower()


def pick(value, allowed, default):
    """value if it is one of `allowed` (case-insensitive), else default."""
    for option in allowed:
        if str(value).strip().lower() == option.lower():
            return option
    return default


def parse_classification(text, reviews, allowed_reasons):
    """Validate the tagging JSON. Returns {review_id: result}. Raises ValueError if unusable."""
    data = json.loads(text or "")
    if not isinstance(data, list):
        raise ValueError("expected a JSON list")
    texts = {r["review_id"]: f"{r.get('title', '')} {r.get('body', '')}" for r in reviews}

    results = {}
    for item in data:
        if not isinstance(item, dict) or item.get("review_id") not in texts:
            continue  # ignore anything we did not ask about
        reasons = item.get("reasons") or []
        if not isinstance(reasons, list):
            reasons = [reasons]
        # Any reason outside our list becomes "other" (the model must not invent labels).
        reasons = sorted({r if r in allowed_reasons else "other" for r in reasons}) or ["other"]
        if len(reasons) > 1 and "other" in reasons:
            reasons.remove("other")  # "other" only makes sense when nothing else applies
        primary = item.get("primary_reason")
        try:
            severity = min(max(int(item.get("severity", 2)), 1), 3)
        except (TypeError, ValueError):
            severity = 2
        try:
            confidence = min(max(float(item.get("confidence", 0.5)), 0.0), 1.0)
        except (TypeError, ValueError):
            confidence = 0.5
        quote = str(item.get("evidence_quote", "")).strip()[:200]
        if quote and squash(quote) not in squash(texts[item["review_id"]]):
            quote = ""  # not an exact quote from the review -> don't show it as evidence
        results[item["review_id"]] = {
            "review_id": item["review_id"],
            "reasons": reasons,
            "primary_reason": primary if primary in reasons else reasons[0],
            "sentiment": pick(item.get("sentiment"), SENTIMENTS, "negative"),
            "severity": severity,
            "evidence_quote": quote,
            "language": pick(item.get("language"), LANGUAGES, "english"),
            "confidence": round(confidence, 2),
        }
    if not results:
        raise ValueError("none of the requested review_ids came back")
    return results


def classify_batch(reviews, allowed_reasons, client=None, usage=None, sleep=time.sleep):
    """Tag up to 20 reviews with Gemini.

    reviews:         [{"review_id", "rating", "title", "body"}, ...]
    allowed_reasons: {reason_name: one-line meaning} (from reasons.py)
    usage:           optional dict; calls and tokens are added to it
    Returns one dict per review with reasons, primary_reason, sentiment, severity,
    evidence_quote, language, confidence. If Gemini fails twice, those reviews get
    reasons=[] and needs_manual_review=True (never crashes).
    Raises GeminiKeyError only if the key is rejected.
    """
    usage = usage if usage is not None else {}
    client = client or make_client()
    config = make_config(classify_prompt(allowed_reasons), classify_schema(allowed_reasons))
    payload = json.dumps([{"review_id": r["review_id"], "rating": int(r["rating"]),
                           "title": r.get("title", ""), "body": r.get("body", "")} for r in reviews],
                         ensure_ascii=False)
    results = call_json(client, payload, config,
                        lambda text: parse_classification(text, reviews, allowed_reasons), usage, sleep) or {}
    # Every review gets an answer; ones Gemini skipped (or a failed batch) need a human look.
    return [results.get(r["review_id"]) or
            {"review_id": r["review_id"], "reasons": [], "needs_manual_review": True}
            for r in reviews]


# ---------------------------------------------------------------- 2. insights summary

def parse_insights(text, allowed_reasons):
    """Validate the insights JSON. Returns a clean dict. Raises ValueError if unusable."""
    data = json.loads(text or "")
    if not isinstance(data, dict) or not str(data.get("executive_summary", "")).strip():
        raise ValueError("no executive_summary")

    def rows(key, fields):
        return [{f: item.get(f, "") for f in fields}
                for item in data.get(key) or [] if isinstance(item, dict)]

    issues = rows("top_issues", ["reason", "why", "evidence_pct", "example_quote"])
    for issue in issues:
        issue["reason"] = issue["reason"] if issue["reason"] in allowed_reasons else "other"
        try:
            issue["evidence_pct"] = round(float(issue["evidence_pct"]), 1)
        except (TypeError, ValueError):
            issue["evidence_pct"] = None
    recs = rows("recommendations", ["fix", "owner", "effort", "expected_impact", "success_metric"])
    for rec in recs:
        rec["effort"] = pick(str(rec["effort"]).replace("Medium", "Med"), EFFORTS, "Med")
    if not recs:
        raise ValueError("no recommendations")
    return {
        "executive_summary": str(data["executive_summary"]).strip(),
        "top_issues": issues,
        "recommendations": recs,
        "quick_wins": [str(x) for x in data.get("quick_wins") or []],
        "risks_or_gaps": [str(x) for x in data.get("risks_or_gaps") or []],
    }


def generate_insights(aggregates, allowed_reasons, client=None, usage=None, sleep=time.sleep):
    """ONE Gemini call that turns the aggregated numbers into a summary + recommendations.

    Returns the insights dict, or None if Gemini failed twice (the caller then uses
    the RECOMMENDATIONS in reasons.py). Raises GeminiKeyError if the key is rejected.
    """
    usage = usage if usage is not None else {}
    client = client or make_client()
    config = make_config(insights_prompt(allowed_reasons), insights_schema(allowed_reasons))
    payload = json.dumps(aggregates, ensure_ascii=False)
    return call_json(client, payload, config, lambda text: parse_insights(text, allowed_reasons), usage, sleep)
