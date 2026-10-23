"""Compare the tool's tags with hand labels: rules only vs Smart vs Full AI, side by side.

Run after analyze.py:
    python analyze.py          # fills the Gemini cache for Smart mode
    python analyze.py --full   # fills the Gemini cache for Full AI mode
    python eval.py             # never calls Gemini: uses the cached answers in data/output/ai_cache.json
"""

import sys

import pandas as pd

import analyze
import llm
from reasons import ALLOWED_REASONS

LABELS_FILE = "data/manual_labels.csv"
TAGGED_FILE = analyze.OUTPUT_FILE
MODES = {"rules": "Rules only", "smart": "Smart", "full": "Full AI"}

# The Windows terminal often cannot print Hindi; replace unknown characters instead of crashing.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def load_joined():
    """Join manual labels to the analysed reviews on review_id."""
    # utf-8-sig strips the hidden BOM Excel adds when you save the CSV.
    labels = pd.read_csv(LABELS_FILE, dtype=str, encoding="utf-8-sig", keep_default_na=False)
    labels = labels[labels["true_reason"].str.strip() != ""]  # skip rows not labelled yet
    if labels.empty:
        sys.exit(f"No labels yet: fill the true_reason column in {LABELS_FILE} first.")
    tagged = pd.read_csv(TAGGED_FILE, dtype=str, keep_default_na=False)
    if "rule_reasons_only" not in tagged.columns:
        sys.exit("Results are from an older analyze.py. Run python analyze.py first.")
    tagged = tagged.drop_duplicates("review_id")  # same text on two platforms = one review here
    joined = labels.merge(tagged[["review_id", "rule_reasons_only", "word_count", "raw_text"]],
                          on="review_id", how="left")
    missing = joined["rule_reasons_only"].isna()
    if missing.any():
        print(f"WARNING: {missing.sum()} labelled review_ids not found in {TAGGED_FILE} (skipped):",
              ", ".join(joined.loc[missing, "review_id"]))
    return joined[~missing].copy()


def predict(df, cache, model):
    """Predicted reasons per mode, rebuilt from the rules + cached Gemini answers (no API calls).

    Returns ({mode: list of reason-lists}, {mode: (answers found, answers needed)}).
    """
    preds = {mode: [] for mode in MODES}
    coverage = {mode: [0, 0] for mode in MODES}
    for row in df.itertuples():
        rule = [] if row.rule_reasons_only == "other" else row.rule_reasons_only.split("|")
        preds["rules"].append(analyze.final_reasons(rule, None, "rules"))
        for mode in ("smart", "full"):
            if not analyze.send_to_gemini(rule, int(row.word_count), mode):
                preds[mode].append(analyze.final_reasons(rule, None, mode))  # rules were enough
                continue
            answer = cache.get(analyze.cache_key(row.review_id, model, mode))
            coverage[mode][1] += 1
            coverage[mode][0] += answer is not None
            preds[mode].append(analyze.final_reasons(rule, answer["reasons"] if answer else None, mode))
    return preds, coverage


def precision_recall(true, pred, reason):
    """precision = of reviews tagged X, how many really were X; recall = of real X, how many we tagged."""
    tagged = pd.Series([reason in p for p in pred], index=true.index)
    actual = true == reason
    tp = (tagged & actual).sum()
    precision = tp / tagged.sum() if tagged.any() else float("nan")
    recall = tp / actual.sum() if actual.any() else float("nan")
    return precision, recall, tagged.any() or actual.any()


def pct(value):
    return "   -" if pd.isna(value) else f"{value:>4.0%}"


def main():
    df = load_joined()
    if df.empty:
        sys.exit("No overlapping reviews to evaluate. Run analyze.py first.")
    model = llm.model_name()
    preds, coverage = predict(df, analyze.load_cache(), model)
    true = df["true_reason"]
    correct = {m: pd.Series([t in p for t, p in zip(true, preds[m])], index=df.index) for m in MODES}

    # ---- 1. accuracy side by side
    print(f"\nAccuracy on {len(df)} hand-labelled reviews (model {model})")
    print("(correct = the manual reason is among the predicted reasons)\n")
    print(f"{'mode':<12}{'accuracy':>10}{'':>9}  Gemini answers used")
    for mode, name in MODES.items():
        found, needed = coverage[mode]
        gemini = "-" if mode == "rules" else f"{found}/{needed} from cache"
        print(f"{name:<12}{correct[mode].mean():>10.0%}{f'({correct[mode].sum()}/{len(df)})':>9}  {gemini}")
    for mode in ("smart", "full"):
        found, needed = coverage[mode]
        if found < needed:
            flag = " --full" if mode == "full" else ""
            print(f"  NOTE: {needed - found} reviews have no cached {MODES[mode]} answer, so they count as "
                  f"rules-only. Run: python analyze.py{flag}")

    # ---- 2. per-reason precision / recall
    print(f"\n{'reason':<26}" + "".join(f"{name:>16}" for name in MODES.values()) + f"{'support':>9}")
    print(f"{'':<26}" + "".join(f"{'prec  rec':>16}" for _ in MODES))
    for reason in ALLOWED_REASONS:
        cells, present = "", False
        for mode in MODES:
            p, r, shown = precision_recall(true, preds[mode], reason)
            present |= shown
            cells += f"{pct(p):>11} {pct(r)}"
        if present:
            print(f"{reason:<26}{cells}{(true == reason).sum():>9}")
    print("\nNote: each review has ONE manual label but may get several predicted reasons,")
    print("so a correct secondary reason counts against precision. Read precision as a lower bound.")

    # ---- 3. mismatches: any review that at least one mode got wrong
    wrong = ~(correct["rules"] & correct["smart"] & correct["full"])
    print(f"\n--- {wrong.sum()} reviews at least one mode got wrong (✓ = right, ✗ = wrong) ---")
    for i in df.index[wrong]:
        row = df.loc[i]
        marks = "  ".join(f"{MODES[m]} {'✓' if correct[m][i] else '✗'} {'|'.join(preds[m][df.index.get_loc(i)])}"
                          for m in MODES)
        print(f"[{row.review_id}] true={row.true_reason}")
        print(f"    {marks}")
        print(f"    {row.raw_text[:140]}")


if __name__ == "__main__":
    main()
