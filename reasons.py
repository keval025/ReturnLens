"""Return-reason definitions, keyword rules and editable recommendations.

Everything a non-coder might want to tweak (keywords, owners, fixes) lives here,
so the logic in analyze.py and app.py never needs to change.
"""

import re

# Each reason: keywords to match, who owns the fix, a short description,
# and how expensive the fix usually is (used in the dashboard's fix-owner view).
# Keywords are matched as whole words/phrases, case-insensitive.
# Hinglish phrases are included because many Indian reviews mix languages.
REASONS = {
    "fit_compatibility": {
        "keywords": ["fit", "fits", "fitting", "thread", "threads", "size", "adapter",
                     "doesn't fit", "does not fit", "not fitting", "fit nahi",
                     "compatible", "incompatible"],
        "owner": "Product",
        "fix_cost": "Expensive",
        "description": "Filter does not fit the customer's tap or shower arm.",
    },
    "leakage": {
        "keywords": ["leak", "leaks", "leaking", "leakage", "drip", "dripping",
                     "water coming out", "joint", "leak ho", "paani nikal"],
        "owner": "Product/QC",
        "fix_cost": "Expensive",
        "description": "Water leaks from joints or the housing.",
    },
    "no_visible_result": {
        "keywords": ["no difference", "no change", "same hair fall", "useless",
                     "not working", "doesn't work", "does not work", "waste of money",
                     "no effect", "koi fark nahi", "bekar", "bakwas", "no use",
                     "hardness still", "still present", "not reducing", "not upto mark",
                     "no improvement", "will reduce within"],
        # Hair/skin words only count when the same sentence says nothing got better.
        "paired_keywords": {
            "terms": ["hair fall", "hairfall", "dry skin", "dandruff", "itchy scalp"],
            "with": ["still", "same", "no change", "increased"],
        },
        "owner": "Listing/Expectations",
        "fix_cost": "Cheap",
        "description": "Customer saw no improvement in hair, skin or scaling.",
    },
    "low_pressure": {
        "keywords": ["low pressure", "pressure dropped", "less flow", "flow reduced",
                     "water flow is slow"],
        "owner": "Product",
        "fix_cost": "Expensive",
        "description": "Water pressure or flow drops after installing.",
    },
    "cartridge_life_cost": {
        "keywords": ["cartridge", "refill", "refills", "replace", "replacement cartridge",
                     "finished", "1 month", "one month", "2 months", "expensive refill",
                     "exhausted"],
        "owner": "Pricing/Retention",
        "fix_cost": "Medium",
        "description": "Cartridge runs out quickly or refills cost too much.",
    },
    "damaged_in_transit": {
        "keywords": ["arrived broken", "damaged box", "missing", "received damaged"],
        "owner": "Packaging/Logistics",
        "fix_cost": "Medium",
        "description": "Product arrived broken or damaged, or with parts missing.",
    },
    "build_quality_durability": {
        "keywords": ["poor quality", "bad quality", "build quality", "quality is poor",
                     "broke", "broken after", "snapped", "weak", "cracked after",
                     "stopped working", "brocken"],
        "owner": "Product/QC",
        "fix_cost": "Expensive",
        "description": "Product feels cheap, or cracks, snaps or stops working after some use.",
    },
    "price_value": {
        "keywords": ["not worth", "overpriced", "waste of money", "too costly", "expensive"],
        "owner": "Pricing",
        "fix_cost": "Medium",
        "description": "Customer feels the product is not worth its price.",
    },
    "installation_difficulty": {
        "keywords": ["install", "installation", "installing", "plumber",
                     "difficult to fit", "instructions", "manual", "hard to fix"],
        "owner": "Instructions",
        "fix_cost": "Cheap",
        "description": "Customer struggled to install it or needed a plumber.",
    },
    "wrong_expectation": {
        "keywords": ["drinking", "ro", "purifier", "tds", "drinkable",
                     "drinking water"],
        "owner": "Listing",
        "fix_cost": "Cheap",
        "description": "Customer expected drinking-water purification (RO/TDS).",
    },
    "delivery_service": {
        "keywords": ["late", "delivery", "delivered", "refund", "customer care",
                     "no response", "customer service", "support"],
        "owner": "Ops/Service",
        "fix_cost": "Medium",
        "description": "Late delivery, refund trouble or no response from support.",
    },
    "other": {
        "keywords": [],  # never matched by rules; used as a fallback label
        "owner": "Unassigned",
        "fix_cost": "Unknown",
        "description": "Anything that does not fit the reasons above.",
    },
}

# The exact list the AI is allowed to choose from (also used to validate its answers).
ALLOWED_REASONS = list(REASONS.keys())


def _build_pattern(keywords):
    """Turn a keyword list into one regex that matches whole words only."""
    # \b = word boundary, so "ro" does not match "product" and "late" not "plate".
    escaped = [re.escape(k) for k in keywords]
    return re.compile(r"\b(" + "|".join(escaped) + r")\b", re.IGNORECASE)


# Compile the regexes once at import time instead of on every review.
PATTERNS = {name: _build_pattern(info["keywords"])
            for name, info in REASONS.items() if info["keywords"]}
PAIRED_PATTERNS = {name: (_build_pattern(info["paired_keywords"]["terms"]),
                          _build_pattern(info["paired_keywords"]["with"]))
                   for name, info in REASONS.items() if "paired_keywords" in info}


def _paired_match(text, terms, qualifiers):
    """True if one sentence contains both a term and a qualifier (e.g. 'hair fall' + 'still')."""
    return any(terms.search(s) and qualifiers.search(s) for s in re.split(r"[.!?\n]+", text))


def match_reasons(text):
    """Return a list of every reason whose keywords appear in the text."""
    if not isinstance(text, str) or not text.strip():
        return []
    return [name for name in REASONS
            if (name in PATTERNS and PATTERNS[name].search(text))
            or (name in PAIRED_PATTERNS and _paired_match(text, *PAIRED_PATTERNS[name]))]


# Amazon Seller Central return reason codes -> our reasons (used for the dummy panel).
# A code like UNWANTED_ITEM tells us nothing about *why*, so it maps to "other".
RETURN_CODE_MAP = {
    "NOT_COMPATIBLE": "fit_compatibility",
    "DEFECTIVE": "leakage",
    "QUALITY_UNACCEPTABLE": "no_visible_result",
    "NOT_AS_DESCRIBED": "wrong_expectation",
    "DAMAGED_BY_CARRIER": "damaged_in_transit",
    "MISSING_PARTS": "damaged_in_transit",
    "APPARENT_DAMAGE": "damaged_in_transit",
    "UNDELIVERABLE": "delivery_service",
    "UNWANTED_ITEM": "other",
    "ORDERED_WRONG_ITEM": "other",
}


# Recommendations shown in the dashboard. Edit freely: these are business
# judgements, not computed values. Only reasons that appear in the data are shown.
RECOMMENDATIONS = {
    "fit_compatibility": {
        "fix": "Add a 'Will it fit my tap?' image with thread sizes (1/2\" BSP) and ship a universal adapter in the box.",
        "effort": "Med",
        "metric": "Fit-related share of 1-3★ reviews falls by half within 2 months",
    },
    "leakage": {
        "fix": "Add a thicker rubber washer plus Teflon tape in the box, and run a leak test on each batch before dispatch.",
        "effort": "Med",
        "metric": "Leak mentions drop below 5% of negative reviews",
    },
    "no_visible_result": {
        "fix": "Set expectations in the listing: results take 2-4 weeks and depend on water hardness. Add a hardness test strip and a usage timeline card.",
        "effort": "Low",
        "metric": "'No difference' share falls; average rating rises 0.2★",
    },
    "low_pressure": {
        "fix": "State the expected flow rate in the listing, and test a lower-resistance filter media for the next batch.",
        "effort": "High",
        "metric": "Pressure complaints below 5% of negative reviews",
    },
    "cartridge_life_cost": {
        "fix": "Show cartridge life by water hardness (e.g. 1-2 months for borewell), and offer a refill subscription or discount pack.",
        "effort": "Med",
        "metric": "Refill repeat-purchase rate goes up; 'finished in 1 month' complaints go down",
    },
    "damaged_in_transit": {
        "fix": "Move to a stronger box with molded inserts, and add a parts checklist inside the box.",
        "effort": "Med",
        "metric": "Damaged/missing returns fall below 2% of orders",
    },
    "build_quality_durability": {
        "fix": "Replace the plastic connector and threads with brass or metal, and drop-test plus thread-torque test each batch.",
        "effort": "High",
        "metric": "Breakage/'poor quality' mentions fall below 5% of negative reviews within 3 months",
    },
    "price_value": {
        "fix": "Show cost per month of use (unit price plus refills) in the listing, and test a combo pack or lower entry price.",
        "effort": "Low",
        "metric": "'Not worth / overpriced' share of 1-3★ reviews halves",
    },
    "installation_difficulty": {
        "fix": "Add a 60-second install video (QR code on the box) and step-by-step photos in the listing.",
        "effort": "Low",
        "metric": "Installation complaints halve; fewer 'needed a plumber' mentions",
    },
    "wrong_expectation": {
        "fix": "Add 'Not for drinking water / does not reduce TDS' to the title image and the first bullet point.",
        "effort": "Low",
        "metric": "Drinking/RO/TDS mentions near zero in new reviews",
    },
    "delivery_service": {
        "fix": "Set a 24-hour reply SLA for review complaints, and audit the courier partner for late deliveries.",
        "effort": "Low",
        "metric": "Service complaints below 5%; response time under 24h",
    },
    "other": {
        "fix": "Read these reviews by hand each month; add new keywords to reasons.py if a pattern appears.",
        "effort": "Low",
        "metric": "'Other' share stays below 10%",
    },
}
