"""Classify a bill as amendatory/reauthorization vs. other categories (issue #14).

Reauthorization bills are heavily reference-based: they extend, amend, or strike
portions of existing law rather than stating self-contained provisions, which may make
them harder to summarize (the meaning lives in the referenced statute) and harder to
judge. analyze_reauth.py uses this classifier to slice existing benchmark scores by
category. Classification is pure — title patterns plus a density of amendatory
operations in the bill text — no network calls.

Categories (first match wins):
- cra_disapproval:  hjres/sjres Congressional Review Act disapproval resolutions. They
                    contain zero amendatory language by construction (they nullify a
                    rule, not amend statute) and their formulaic CRS summaries would
                    otherwise pollute the "standalone" baseline group.
- appropriations:   titled appropriations acts. Deliberately NOT lumped with amendatory:
                    pure appropriations bills barely amend law (near-zero density), and
                    their failure mode (covering hundreds of accounts) is a different
                    phenomenon than amendment-tracing. The NDAA is not in this bucket —
                    it is an authorization bill dense with amendments and lands in
                    `amendatory` via density.
- amendatory:       a reauthorization/extension title OR amendatory-operation density
                    >= DENSITY_THRESHOLD per 1,000 chars of bill text. Calibrated on all
                    110 corpus bills: the distribution is bimodal — bills that genuinely
                    amend law sit >= ~0.5 even at NDAA scale (2.9M chars), CRA and pure
                    appropriations bills sit <= ~0.2.
- standalone:       everything else (includes lightly-amendatory bills below threshold).
"""
from __future__ import annotations

import re

CAT_CRA = "cra_disapproval"
CAT_APPROPS = "appropriations"
CAT_AMENDATORY = "amendatory"
CAT_STANDALONE = "standalone"
CATEGORY_ORDER = [CAT_CRA, CAT_APPROPS, CAT_AMENDATORY, CAT_STANDALONE]

# Amendatory operations: the drafting formulas a bill uses to modify existing law.
AMEND_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.I)
    for p in (
        r"\bis amended\b",
        r"\bare amended\b",
        r"\bby striking\b",
        r"\bby inserting\b",
        r"\bis repealed\b",
    )
]

DENSITY_THRESHOLD = 0.5  # amendatory operations per 1,000 chars of bill text

TITLE_REAUTH_RE = re.compile(r"reauthoriz|extension act of|\bto extend\b", re.I)
TITLE_APPROPS_RE = re.compile(
    r"appropriations act|making (further |emergency |supplemental )*appropriations"
    r"|continuing appropriations",
    re.I,
)
TITLE_CRA_RE = re.compile(
    r"congressional disapproval|disapproving the (rule|action)", re.I
)


def amend_stats(text: str) -> tuple[int, float]:
    """(count, density-per-1,000-chars) of amendatory operations in bill text."""
    if not text:
        return 0, 0.0
    count = sum(len(p.findall(text)) for p in AMEND_PATTERNS)
    return count, round(count / (len(text) / 1000), 3)


def classify_bill(bill: dict) -> dict:
    """Category + audit fields for one stored bill record.

    Needs `title`, `type`, and `bill_text`; all optional (missing -> standalone path).
    `title_reauth` is kept as an independent flag so consumers can drill into the
    strictly reauthorization-titled subset within `amendatory`.
    """
    title = bill.get("title") or ""
    btype = (bill.get("type") or "").lower()
    count, density = amend_stats(bill.get("bill_text") or "")
    title_reauth = bool(TITLE_REAUTH_RE.search(title))

    if btype in ("hjres", "sjres") and TITLE_CRA_RE.search(title):
        category = CAT_CRA
    elif TITLE_APPROPS_RE.search(title):
        category = CAT_APPROPS
    elif title_reauth or density >= DENSITY_THRESHOLD:
        category = CAT_AMENDATORY
    else:
        category = CAT_STANDALONE

    return {
        "category": category,
        "title_reauth": title_reauth,
        "amend_count": count,
        "amend_density": density,
    }
