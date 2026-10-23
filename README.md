# 🔍 ReturnLens

**See why customers return — and what to fix first.**

Built for WaterScience to answer one question: *why are customers returning the CLEO Shower & Tap Filter, and what should we do about it?*

Drop review CSVs into the dashboard, click one button, and ReturnLens tags every 1-3★ review with return reasons. Keyword rules handle the obvious ones, and Google Gemini handles the rest. Gemini then writes an **AI Insights** summary: top issues, recommended fixes, owners and quick wins. Charts show top reasons, severity, monthly trend, real quotes and who owns each fix.

---

## Quick start

```bash
pip install -r requirements.txt
cp .env.example .env          # then add GEMINI_API_KEY (free: aistudio.google.com -> Get API key)
streamlit run app.py          # opens http://localhost:8501
```

Then, at the top of the page, **drag & drop your review CSVs → click 🔍 Analyze reviews**.

- **No Gemini key?** The app still works: keyword rules only, with a yellow warning. You can also type the key into **🔑 API keys** in the sidebar (tick *Save to .env* to keep it). Keys are never shown or logged, and `.env` is in `.gitignore`.
- **Terminal only:** `python analyze.py` (Smart), `python analyze.py --full` (Full AI), `python analyze.py --rules`, then `python eval.py`.

## CSV format

Click **📥 Download CSV template** in the app, or use:

```
rating,title,body,date,verified
1,Leaking from day one,Water leaks from the top joint even after using teflon tape.,14 June 2026,true
```

- **Required:** `rating` (1-5; `4.0` and `4 out of 5 stars` also work) and `body`.
- **Optional:** `title`, `date`, `verified`, `platform`, `product_id`.
- **Other column names are recognised automatically:** `review` / `review_text` / `comment` / `text` → body, `stars` / `score` → rating, `review_title` / `heading` → title, `review_date` → date.
- **File name decides the platform:** `amazon_….csv` → Amazon, `flipkart_….csv` → Flipkart. For any other name, the app asks you to pick Amazon / Flipkart / Other and saves it as `<platform>_<next number>.csv`.
- **Encoding:** UTF-8 (with or without Excel's BOM). Latin-1 files are converted.
- **Before saving**, each file shows rows, rating mix, how many reviews are **new** vs already in your data, and its first 3 rows. If the name already exists you choose *Save as next number* (default) or *Replace*.
- Files are written to a temporary `.part` file and renamed when complete, so a failed save never leaves a half-written file.

### Collecting reviews with the browser helper

`tools/review_helper.js` downloads the reviews **visible on an Amazon.in review page** as a CSV in the template format:

1. Open the product on amazon.in → *See more reviews*.
2. Press **F12** → **Console**, paste the whole file, press Enter. `amazon_<date>.csv` downloads.
3. Go to the next page of reviews and repeat. Then drop all the files into the app (duplicates are removed).

It reads only the page you are looking at; it doesn't browse on its own. It relies on Amazon's page markup (`data-hook="review"`), which can change. For Flipkart, copy reviews into the template by hand.

## AI analysis: Smart vs Full AI

Pick the mode in the sidebar (**🧠 AI analysis**):

| | Smart (recommended) | Full AI |
|---|---|---|
| What Gemini reads | Only 1-3★ reviews the rules can't place, or long (> 60 words) reviews with 0-1 rule matches | Every 1-3★ review (rules kept alongside for comparison) |
| Real run, 179 reviews (115 negative) | 38 reviews → 2 + 1 insights = **3 calls**, 5,325 in / 4,592 out tokens, **30 s** | 115 reviews → 6 + 1 = **7 calls**, 11,177 in / 12,395 out tokens, **89 s** |
| Per 100 reviews (scaled) | ~21 to Gemini, ~2 calls, ~17 s | ~64 to Gemini, ~4-5 calls, ~50 s |
| Cost | **₹0** on the free tier (paid tier would be ≈ ₹0.64 / 100 reviews) | **₹0** on the free tier (paid ≈ ₹1.69 / 100 reviews) |
| Accuracy on 30 hand labels | **83%** (rules alone: 73%) | **97%** (single primary reason: 90%) |

Measured on 23 Oct 2026 with `gemini-3.5-flash-lite`. All three modes predict about the same number of reasons per review (1.4-1.5), so the gain is not from guessing more labels.

How the Gemini step works (`llm.py`):
- **Per review** Gemini returns: reasons (only from the list in `reasons.py`), primary_reason, sentiment, severity (1-3; 3 = broke / leaked / damaged / unsafe), an exact evidence quote, language (english / hindi / hinglish) and confidence.
- **Calls**: 20 reviews per call, temperature 0, structured JSON (`response_schema`). A reason not on the list becomes `other`, and a "quote" that isn't really in the review is blanked. Bad JSON gets one retry, then the reviews are marked `needs_manual_review` and retried on the next run.
- **Insights**: ONE extra call per analysis. It sees only aggregated numbers (reason counts and % per platform, 3 quotes per reason, rating mix, monthly trend, severity counts), not all reviews. The result is saved to `data/output/insights.json`. If Gemini is off or fails, the insights are built from `RECOMMENDATIONS` in `reasons.py`.
- **Caching**: answers are cached in `data/output/ai_cache.json` by review_id + model + mode, and the insights are reused while the numbers don't change. **A re-run makes 0 calls.**
- **Free-tier limits**: on 429 / "model busy" (503) it waits 2 s, 5 s, 10 s and retries. Each call times out after 90 s. When the **daily** limit is reached it stops at once with a clear message, keeps the rules' tags, and retries those reviews on the next run.
- **Model**: `gemini-3.5-flash-lite` by default. To switch, set `GEMINI_MODEL=` in `.env`. During testing, `gemini-3.8-flash` (the newest Flash) took about 2 minutes per call or timed out, and `gemini-3.6-flash` was often "busy" and allows only **20 requests per day** on the free tier. Each model has its own daily limit.

`eval.py` prints accuracy for **rules only, Smart and Full AI side by side**, with per-reason precision/recall and every mismatch. It rebuilds the Smart and Full predictions from the cache, so it never calls Gemini. Run `python analyze.py` and `python analyze.py --full` once first.

## Optional: fetch reviews from a product link (Apify)

Only shown when `APIFY_API_TOKEN` is in `.env` (and `pip install apify-client`). Otherwise a small grey note says so.

Paste an Amazon.in or Flipkart link → the app detects platform + product ID → **Test fetch (5 reviews)** to check it works cheaply → **🚀 Fetch & analyze** runs the same analysis as the CSV flow. Raw responses are saved in `data/raw/`, and a product fetched in the last 24 h can be re-used for free.

**Why it may fail:** Amazon actively blocks scrapers. In testing, three Amazon fetches through Apify returned **0 reviews**. Third-party actors also break when a site changes its layout, and they need Apify credits. When it fails, the app shows a friendly message and a link back to the CSV upload. It never crashes or blocks the CSV flow.

## Data source

Reviews are collected manually (or with the browser helper) from public Amazon.in / Flipkart review pages, or optionally fetched through Apify. Automated scraping can conflict with a marketplace's terms of use, so treat it as a prototype data source. **In production, WaterScience would use Amazon Seller Central and Flipkart Seller Hub return reports.** They are first-party, free, and more accurate, with real return reason codes and the true return volume.

## Dashboard

- **Top of page**: 📂 Add customer reviews (upload, template, preview, 🔍 Analyze reviews with step-by-step progress), then the optional product-link section.
- **🧠 AI Insights**: executive summary, top issues (% + quote), quick wins, recommendations table (fix, owner, effort, impact, metric). The badge says whether Gemini or the fallback wrote it. Insights cover all data, so the sidebar filters don't change them.
- **Charts**: top reasons, **severity** (severity-3 complaints per reason), **Rules vs Gemini** (Full AI mode only: agreement and examples where Gemini found a reason the rules missed), monthly trend, drill-down quotes, fix owner, recommendations.
- **Sidebar**: 🧠 AI analysis mode, 🔁 Re-run analysis, 📁 Data files (row counts; 🗑️ delete after ticking *Yes, delete*, then re-run), 🔑 API keys, and filters (product, platform Amazon / Flipkart / Both, rating, dates, verified).
- **Footer**: AI model · mode · calls · tokens · cost ₹0 · last run time.

The data loader is cached on the modification times of `tagged_reviews.csv`, `insights.json` and `run_log.json`, so the dashboard refreshes by itself after every run.

## Folder structure

```
data/
├── amazon/  flipkart/  other/   # your review CSVs (one folder per platform)
├── raw/                 # raw Apify responses (optional feature; not in git)
├── output/              # generated; safe to delete
│   ├── tagged_reviews.csv  insights.json  run_log.json  ai_cache.json
├── manual_labels.csv    # 30 hand labels used by eval.py
├── reason_list.txt      # allowed reason names for labelling
├── dummy_returns.csv    # 50 DUMMY return records (not used by the dashboard any more)
└── sample_reviews.csv   # synthetic demo reviews, used only when no real data exists
tools/review_helper.js   # browser helper for collecting Amazon reviews
tests/test_links.py      # link-parsing tests
```

| File | What it does |
|---|---|
| `app.py` | Streamlit dashboard |
| `analyze.py` | Load → clean → keyword rules → Gemini tagging → insights → outputs. `run_analysis(mode=...)` returns the summary. |
| `llm.py` | Gemini: prompts, JSON schemas, `classify_batch()`, `generate_insights()`, retries, limits, prices |
| `uploads.py` | Check, map columns, preview, save and delete uploaded CSVs |
| `reasons.py` | The 12 return reasons: keywords, owner, fix cost, recommendations |
| `eval.py` | Rules only vs Smart vs Full AI accuracy, precision/recall, mismatches |
| `fetchers.py`, `links.py` | Optional Apify product-link fetching |

## What breaks at 10x (and how to handle it)

| What breaks | Why | How to handle it |
|---|---|---|
| Gemini free-tier limits | Requests per day are capped per model (20/day for gemini-3.6-flash). 10x reviews ≈ 10x calls in Full AI | Use Smart mode (≈ 3x fewer calls). The cache means each review is only paid for once. Spread runs over days, or move to the paid tier (≈ ₹0.6-1.7 per 100 reviews, logged as `cost_if_paid_tier_usd`). For big backlogs, Gemini's Batch API or a slow background queue. |
| Slow / busy model | Free-tier models get "high demand" errors at peak times | Already handled: timeouts, 2/5/10 s retries, `needs_manual_review` + retry next run. Switch `GEMINI_MODEL` if one model stays busy. |
| Manual collection | 10x reviews = hours of copy-paste | Browser helper for now; Seller Central / Seller Hub reports in production. |
| Apify / scrapers | Cost per review grows linearly, and Amazon blocks scrapers | Keep it optional. Use first-party reports instead. |
| Keyword drift | New complaints ("smell", "rust") match no keyword | Full AI's *Rules vs Gemini* panel shows what the rules miss, so add those keywords and re-run `eval.py`. |
| One CSV / one machine | Fine for thousands of rows, slow for millions | Small database + scheduled incremental job. Not needed today. |

## Assumptions and known limits

- **Reviews ≠ returns.** Most people who return a product never write a review. Reviews explain *why*, while return reports give the true *volume*.
- **Negativity bias.** Unhappy customers write more reviews, so shares are shares of *complaints*.
- **Small sample** (179 reviews, 115 negative): one review moves a percentage by about a point. Treat monthly spikes as leads to check.
- **Accuracy is measured on only 30 hand labels.** 97% vs 83% is a difference of 4 reviews, so re-check after adding more labels. Each review has one hand label, so precision is a lower bound.
- **Severity, sentiment and language come from Gemini.** In Smart mode, reviews tagged by rules alone have none. Full AI rates every review.
- **AI Insights** use only the aggregated numbers and a few quotes; Gemini is told not to invent numbers, but read them as a draft for a human to check.
- **Same text, two platforms**: a review copied word-for-word to both platforms shares one review_id.
- **USD→INR = 88** is a fixed assumption (`USD_TO_INR` in `.env`).

## Next steps

1. Label 30-50 more reviews (including Hindi/Hinglish) and re-run `eval.py`.
2. Add the keywords that *Rules vs Gemini* shows the rules missing.
3. Ship the quick wins from AI Insights first, then re-run monthly and track each success metric.
4. Plug in real Seller Central / Seller Hub return reports to compare actual return reasons with review reasons.
