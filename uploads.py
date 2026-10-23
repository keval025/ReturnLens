"""Helpers for the dashboard's "Add customer reviews" section: check, preview, save and delete review CSVs.

Kept out of app.py so this logic can be tested without starting Streamlit.
"""

import glob
import io
import os
import re

import pandas as pd

import analyze  # folder settings + the exact review_id logic used by the analysis

REQUIRED_COLUMNS = ["rating", "body"]
OPTIONAL_COLUMNS = ["title", "date", "verified", "platform", "product_id", "product_url"]
PLATFORMS = list(analyze.PLATFORM_DIRS)  # ["amazon", "flipkart", "other"]
LARGE_FILE_MB = 5

# Other common column names -> our names (compared after lowercasing, spaces -> "_").
COLUMN_ALIASES = {
    "review": "body", "review_text": "body", "review_body": "body", "comment": "body", "comments": "body",
    "text": "body", "content": "body", "review_content": "body",
    "stars": "rating", "star": "rating", "score": "rating", "star_rating": "rating", "rating_value": "rating",
    "review_rating": "rating",
    "review_title": "title", "heading": "title", "headline": "title", "summary": "title",
    "review_date": "date", "date_posted": "date", "reviewed_on": "date",
    "verified_purchase": "verified", "certified_buyer": "verified", "is_verified": "verified",
}

TEMPLATE_CSV = (
    "rating,title,body,date,verified\n"
    "1,Leaking from day one,Water leaks from the top joint even after using teflon tape.,14 June 2026,true\n"
    "3,Theek hai,Filter accha hai lekin cartridge 1 mahine mein khatam ho gaya.,2 July 2026,true\n"
    "5,Great product,Easy to install and my hair feels softer.,20 August 2026,false\n"
)
EXPECTED_FORMAT = ("Expected format (download the template above):\n\n"
                   "`rating,title,body,date,verified`\n\n"
                   "`1,Leaking from day one,Water leaks from the top joint.,14 June 2026,true`\n\n"
                   "Only **rating** and **body** are required.")


def platform_from_name(filename):
    """'Amazon_03.csv' -> 'amazon'. Returns None if the name starts with no known platform."""
    name = os.path.basename(filename).lower()
    for platform in PLATFORMS:
        if name.startswith(platform):
            return platform
    return None


def map_columns(df):
    """Rename known alternative column names to ours. Returns (df, {old name: new name})."""
    renamed = {}
    taken = set()
    for col in df.columns:
        key = re.sub(r"[\s\-]+", "_", str(col).strip().lower())
        new = key if key in REQUIRED_COLUMNS + OPTIONAL_COLUMNS else COLUMN_ALIASES.get(key)
        if new and new not in taken:  # first matching column wins (e.g. keep 'body' over 'text')
            taken.add(new)
            if new != col:
                renamed[col] = new
    keep = [c for c in df.columns if c in renamed or c in taken]
    return df[keep].rename(columns=renamed), renamed


def clean_rating(values):
    """'4.0' / '4 out of 5 stars' / '5★' -> '4' / '4' / '5'; anything else -> '' (skipped later)."""
    def one(value):
        match = re.search(r"\d+(?:\.\d+)?", str(value))
        number = float(match.group()) if match else 0
        return str(int(number)) if number in (1, 2, 3, 4, 5) else ""
    return values.map(one)


def check_upload(filename, data, platform=None):
    """Check one uploaded file. Returns a dict; if "error" is set, the file must not be saved.

    platform: the user's choice for files whose name doesn't start with amazon/flipkart/other.
    """
    from_name = platform_from_name(filename)
    result = {"name": os.path.basename(filename), "platform": from_name or platform,
              "needs_platform": from_name is None, "df": None, "renamed": {},
              "encoding": None, "warnings": [], "error": None}

    if not data.strip():
        result["error"] = "The file is empty."
        return result
    if len(data) > LARGE_FILE_MB * 1024 * 1024:
        result["warnings"].append(f"Large file ({len(data) / 1024 / 1024:.1f} MB): the analysis may take a while.")

    # Browser downloads are usually UTF-8 (sometimes with a BOM); older Excel files can be Latin-1.
    try:
        text, result["encoding"] = data.decode("utf-8-sig"), "utf-8"
    except UnicodeDecodeError:
        text, result["encoding"] = data.decode("latin-1"), "latin-1"  # latin-1 can decode any bytes
        result["warnings"].append("Not UTF-8: read as Latin-1 and saved as UTF-8. "
                                  "Check that special characters look right.")

    try:
        raw = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False)
    except (pd.errors.EmptyDataError, pd.errors.ParserError) as e:
        result["error"] = f"Could not read it as a CSV ({e}).\n\n{EXPECTED_FORMAT}"
        return result

    df, result["renamed"] = map_columns(raw)
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        result["error"] = (f"Missing column(s): **{', '.join(missing)}**. "
                           f"The file has: {', '.join(map(str, raw.columns))}.\n\n{EXPECTED_FORMAT}")
        return result
    if df.empty:
        result["error"] = "The file has column names but no reviews."
        return result

    for col in ["title", "date", "verified"]:  # optional columns -> empty if missing
        if col not in df.columns:
            df[col] = ""
    df["rating"] = clean_rating(df["rating"])
    bad = int((df["rating"] == "").sum())
    if bad:
        result["warnings"].append(f"{bad} rows have no valid 1-5 rating and will be skipped.")
    columns = ["rating", "title", "body", "date", "verified"] + [c for c in OPTIONAL_COLUMNS[3:] if c in df.columns]
    result["df"] = df[columns]
    return result


def target_path(check, replace):
    """Where the file will be saved.

    Named amazon_/flipkart_ files keep their name (or get the next number if it's taken);
    other names become <platform>_<next number>.csv.
    """
    folder = analyze.PLATFORM_DIRS[check["platform"]]
    if check["needs_platform"]:
        return f"{folder}/{next_free_name(folder, check['platform'])}"
    name = os.path.splitext(check["name"])[0] + ".csv"  # 'x.CSV' -> 'x.csv' so analyze.py finds it
    if replace or not os.path.exists(os.path.join(folder, name)):
        return f"{folder}/{name}"
    return f"{folder}/{next_free_name(folder, check['platform'])}"


def name_taken(check):
    """True if a file with this name is already in the platform folder."""
    if check["needs_platform"]:
        return False  # these always get a new numbered name
    folder = analyze.PLATFORM_DIRS[check["platform"]]
    return os.path.exists(os.path.join(folder, os.path.splitext(check["name"])[0] + ".csv"))


def next_free_name(folder, platform):
    """Next unused '<platform>_NN.csv', e.g. amazon_03.csv when amazon_01 and amazon_02 exist."""
    files = os.listdir(folder) if os.path.isdir(folder) else []
    taken = [int(m.group(1)) for f in files if (m := re.fullmatch(rf"{platform}_(\d+)\.csv", f, re.IGNORECASE))]
    number = max(taken, default=0) + 1
    while os.path.exists(os.path.join(folder, f"{platform}_{number:02d}.csv")):
        number += 1
    return f"{platform}_{number:02d}.csv"


def existing_ids(platform, skip_path=None):
    """review_ids already in this platform's folder (ignoring skip_path, the file being replaced)."""
    ids = set()
    for path in glob.glob(os.path.join(analyze.PLATFORM_DIRS[platform], "*.csv")):
        if skip_path and os.path.normcase(os.path.abspath(path)) == os.path.normcase(os.path.abspath(skip_path)):
            continue
        try:
            df = pd.read_csv(path, dtype=str, encoding="utf-8-sig", encoding_errors="replace",
                             keep_default_na=False)
        except (pd.errors.EmptyDataError, pd.errors.ParserError):
            continue
        ids.update(analyze.review_ids(df))
    return ids


def preview(check, replace):
    """One table row for the file: platform, rows, rating mix, and new vs already-known reviews."""
    df = check["df"]
    ratings = pd.to_numeric(df["rating"], errors="coerce")
    row = {"File": check["name"], "Platform": check["platform"].capitalize(), "Rows": len(df)}
    for star in range(1, 6):
        row[f"{star}★"] = int((ratings == star).sum())

    # Count only rows the analysis will keep (valid rating, some text), each review once.
    usable = ratings.isin([1, 2, 3, 4, 5]) & ((df["title"].str.strip() != "") | (df["body"].str.strip() != ""))
    ids = set(analyze.review_ids(df[usable]))
    skip = target_path(check, replace) if replace else None
    row["New"] = len(ids - existing_ids(check["platform"], skip))
    row["Already have / skipped"] = len(df) - row["New"]
    row["Saved as"] = os.path.basename(target_path(check, replace))
    return row


def first_rows(check, n=3):
    """The first rows of the file, as they will be saved (after renaming columns)."""
    return check["df"].head(n)


def save_upload(check, replace):
    """Save the file into its platform folder without ever leaving a half-written file.

    Saves the cleaned columns (rating, title, body, date, verified, ...) as UTF-8.
    Writes to '<name>.part' first, then renames it in one step. Returns the saved path.
    """
    path = target_path(check, replace)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp = path + ".part"  # not '*.csv', so analyze.py never reads a half-written file
    try:
        check["df"].to_csv(temp, index=False, encoding="utf-8")
        os.replace(temp, path)  # atomic: the .csv is either the old file or the complete new one
    finally:
        if os.path.exists(temp):
            os.remove(temp)
    return path


def list_data_files():
    """Every CSV in the platform folders, with its row count (None if unreadable)."""
    files = []
    for platform, folder in analyze.PLATFORM_DIRS.items():
        for path in sorted(glob.glob(os.path.join(folder, "*.csv"))):
            try:
                rows = len(pd.read_csv(path, dtype=str, encoding="utf-8-sig", encoding_errors="replace"))
            except (pd.errors.EmptyDataError, pd.errors.ParserError):
                rows = None
            files.append({"platform": platform, "path": path.replace("\\", "/"),
                          "name": os.path.basename(path), "rows": rows})
    return files


def delete_data_file(path):
    """Delete one review CSV, but only if it really is inside a platform folder."""
    folders = [os.path.abspath(f) for f in analyze.PLATFORM_DIRS.values()]
    if os.path.dirname(os.path.abspath(path)) not in folders or not path.lower().endswith(".csv"):
        raise ValueError(f"Refusing to delete {path}: not a review CSV in a platform folder.")
    os.remove(path)
