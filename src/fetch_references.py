"""Fetch extra human-authored reference baselines beyond the CRS summary (issue #8 spike).

The benchmark currently compares model summaries only against the CRS summary. Two more
human baselines are worth prototyping per bill:
- committee_summary: the "Purpose and Summary" section of the bill's committee report
  (congress.gov `committee-report` endpoints; the underlying document is a govinfo CRPT
  HTML page).
- cbo_summary: the CBO cost-estimate summary for the bill (a cbo.gov publication page).

Coverage is necessarily partial: only bills reported out of committee have a committee
report, and only bills CBO scored have a cost estimate. This module is a PROTOTYPE — the
fetch flow below requires CONGRESS_API_KEY and live network access to congress.gov/
govinfo.gov/cbo.gov, none of which is available in this environment, so it has not been
live-tested. The pure extractors (`extract_purpose_and_summary`, `extract_cbo_summary`)
are the tested core; see tests/test_references.py and tests/fixtures/references/.

Output: one JSON per bill at data/references{-sfx}/<bill_id>.json (see common.DATA_REFERENCES;
reroutes with the bills dataset under CRS_DATASET). Resumable — bills that already have a
references file are skipped. A bill is skipped entirely (no file written) when BOTH
committee_summary and cbo_summary come back None.

Usage: python src/fetch_references.py [--limit N]
"""
from __future__ import annotations

import argparse
import asyncio
import html
import re
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

BASE = "https://api.congress.gov/v3"


# ============================================================================ extractors
# Pure functions, fixture-tested (tests/test_references.py). No network, no I/O.

_PURPOSE_HEADING_RE = re.compile(
    r"^(?:[IVXLCDM]+\.|\d+\.)?\s*PURPOSE AND SUMMARY\s*$", re.IGNORECASE
)
# Next-section headings known to follow "Purpose and Summary" in House/Senate committee
# reports, matched case-insensitively as a line prefix (in addition to the generic
# all-caps-short-line heuristic below).
_KNOWN_NEXT_HEADINGS = (
    "BACKGROUND AND NEED FOR",
    "HEARINGS",
    "COMMITTEE CONSIDERATION",
    "SECTION-BY-SECTION",
)
_HEADING_MAX_CHARS = 90
_HEADING_UPPER_RATIO = 0.8
_MIN_PURPOSE_BODY_CHARS = 100


def _looks_like_heading(line: str) -> bool:
    """A line of < 90 chars that reads as a section heading: either it starts with a
    known next-section title, or it's >=80% uppercase letters (centered ALL-CAPS
    headings, as govinfo CRPT text renders them)."""
    stripped = line.strip()
    if not stripped or len(stripped) >= _HEADING_MAX_CHARS:
        return False
    upper = stripped.upper()
    if any(upper.startswith(prefix) for prefix in _KNOWN_NEXT_HEADINGS):
        return True
    letters = [c for c in stripped if c.isalpha()]
    if not letters:
        return False
    return sum(1 for c in letters if c.isupper()) / len(letters) >= _HEADING_UPPER_RATIO


def extract_purpose_and_summary(report_html: str) -> str | None:
    """Pull the "Purpose and Summary" section out of a committee-report HTML page.

    Converts to text (common.html_to_text), finds a line that IS the heading
    (case-insensitively "PURPOSE AND SUMMARY", optionally prefixed with a roman-numeral
    or number label like "I."), then collects everything up to the next heading-like
    line (see `_looks_like_heading`). Returns None when the heading isn't found, or the
    captured body is under 100 chars (e.g. a false-positive match with nothing after it).
    """
    text = C.html_to_text(report_html)
    lines = text.split("\n")

    start = None
    for i, line in enumerate(lines):
        if _PURPOSE_HEADING_RE.match(line.strip()):
            start = i + 1
            break
    if start is None:
        return None

    body_lines = []
    for line in lines[start:]:
        if _looks_like_heading(line):
            break
        body_lines.append(line)

    body = re.sub(r"\s+", " ", " ".join(body_lines)).strip()
    if len(body) < _MIN_PURPOSE_BODY_CHARS:
        return None
    return body


_META_TAG_RE = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
_ATTR_RE = re.compile(r"""([\w:-]+)\s*=\s*"([^"]*)"|([\w:-]+)\s*=\s*'([^']*)'""")
_MIN_META_DESC_CHARS = 100
_MIN_PARAGRAPH_CHARS = 120
_NAV_MARKERS = (
    "skip to main content", "menu", "sign in", "search this site",
    "toggle navigation", "main navigation", "page not found",
)


def _meta_attrs(tag: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for m in _ATTR_RE.finditer(tag):
        if m.group(1) is not None:
            attrs[m.group(1).lower()] = m.group(2)
        else:
            attrs[m.group(3).lower()] = m.group(4)
    return attrs


def _meta_content(page_html: str, value: str, attr: str) -> str | None:
    for tag in _META_TAG_RE.findall(page_html):
        attrs = _meta_attrs(tag)
        if attrs.get(attr, "").lower() == value and attrs.get("content"):
            return attrs["content"]
    return None


def extract_cbo_summary(page_html: str) -> str | None:
    """Pull a CBO cost-estimate summary out of a cbo.gov publication page.

    First tries `<meta name="description">` (falling back to `<meta property=
    "og:description">`); if that content is >= 100 chars, html-unescape and return it.
    Otherwise falls back to `common.html_to_text`'d body: the first 2 paragraphs (blocks
    split on blank lines) that are >= 120 chars each and don't look like navigation
    chrome. Returns None if nothing qualifies.
    """
    desc = _meta_content(page_html, "description", "name") or _meta_content(
        page_html, "og:description", "property"
    )
    if desc:
        desc = re.sub(r"\s+", " ", html.unescape(desc)).strip()
        if len(desc) >= _MIN_META_DESC_CHARS:
            return desc

    text = C.html_to_text(page_html)
    blocks = re.split(r"\n\s*\n", text)
    picked = []
    for block in blocks:
        collapsed = re.sub(r"\s+", " ", block).strip()
        if len(collapsed) < _MIN_PARAGRAPH_CHARS:
            continue
        if any(marker in collapsed.lower() for marker in _NAV_MARKERS):
            continue
        picked.append(collapsed)
        if len(picked) == 2:
            break
    if not picked:
        return None
    return "\n\n".join(picked)


# ================================================================================ fetch
# Network I/O. Not exercised in this environment (no CONGRESS_API_KEY / live network) --
# see module docstring.

_CITATION_RE = re.compile(r"\b(H|S)\.?\s*Rept?\.?\s*(\d+)\s*-\s*(\d+)", re.IGNORECASE)


def parse_committee_citation(citation: str) -> tuple[int, str, int] | None:
    """"H. Rept. 118-125" -> (118, "hrpt", 125); "S. Rept. 118-45" -> (118, "srpt", 45).
    None if the citation doesn't parse."""
    m = _CITATION_RE.search(citation or "")
    if not m:
        return None
    chamber = "hrpt" if m.group(1).upper() == "H" else "srpt"
    return int(m.group(2)), chamber, int(m.group(3))


async def _get(client, key, path, **params):
    params.update(api_key=key, format="json")
    last = None
    for attempt in range(4):
        try:
            r = await client.get(f"{BASE}/{path}", params=params, timeout=40)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001  (transient network/5xx -- retry with backoff)
            last = e
            await asyncio.sleep(0.5 * (attempt + 1))
    raise last


async def fetch_committee_report_text(get, http, citation: str) -> tuple[str, str]:
    """Return (report_html, source_url) for a committee report's Formatted Text, or
    ("", "") if the citation doesn't parse or no formatted text is available."""
    parsed = parse_committee_citation(citation)
    if not parsed:
        print(f"    committee report citation {citation!r} did not parse; skipping")
        return "", ""
    congress, chamber, number = parsed
    try:
        d = await get(f"committee-report/{congress}/{chamber}/{number}/text")
    except Exception as e:  # noqa: BLE001
        print(f"    committee-report/{congress}/{chamber}/{number}: fetch error ({str(e)[:60]})")
        return "", ""
    url = None
    for t in d.get("text") or []:
        if t.get("type") == "Formatted Text" and t.get("url"):
            url = t["url"]
            break
    if not url:
        return "", ""
    try:
        r = await http.get(url, timeout=60, follow_redirects=True)
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        print(f"    committee report text download error ({str(e)[:60]})")
        return "", ""
    return r.text, url


async def process_bill(get, http, bill_rec: dict) -> dict | None:
    """Fetch + extract both reference baselines for one bill. Returns None (write
    nothing) when neither a committee summary nor a CBO summary could be found."""
    bill_key = bill_rec["bill_id"]
    congress, btype, number = bill_rec["congress"], bill_rec["type"], bill_rec["number"]

    try:
        d = await get(f"bill/{congress}/{btype}/{number}")
    except Exception as e:  # noqa: BLE001
        print(f"  {bill_key}: bill fetch error ({str(e)[:60]})")
        return None
    bill = d.get("bill", {})

    committee_summary = committee_citation = committee_url = None
    reports = bill.get("committeeReports")
    if isinstance(reports, list) and reports:
        citation = reports[0].get("citation") or ""
        report_html, url = await fetch_committee_report_text(get, http, citation)
        if report_html:
            committee_summary = extract_purpose_and_summary(report_html)
            committee_citation, committee_url = citation, url
    else:
        # The research memo (issue #8) hasn't confirmed which field reliably carries
        # committee reports across bill types/vintages -- log and move on rather than
        # guess at an undocumented shape.
        print(f"  {bill_key}: no bill.committeeReports list; skipping committee report")

    cbo_summary = cbo_url = None
    estimates = bill.get("cboCostEstimates") or []
    if estimates:
        cbo_url = estimates[0].get("url")
        if cbo_url:
            try:
                r = await http.get(cbo_url, timeout=60, follow_redirects=True)
                r.raise_for_status()
                cbo_summary = extract_cbo_summary(r.text)
            except Exception as e:  # noqa: BLE001
                print(f"  {bill_key}: cbo fetch error ({str(e)[:60]})")

    if committee_summary is None and cbo_summary is None:
        return None

    return {
        "bill_id": bill_key,
        "committee_summary": committee_summary,
        "committee_citation": committee_citation,
        "committee_url": committee_url,
        "cbo_summary": cbo_summary,
        "cbo_url": cbo_url,
        "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
    }


async def main_async(limit: int | None) -> None:
    C.load_env()
    key = C.require_congress_key()
    C.DATA_REFERENCES.mkdir(parents=True, exist_ok=True)

    bill_files = C.list_bill_files()
    existing = {p.stem for p in C.DATA_REFERENCES.glob("*.json")}
    todo = [p for p in bill_files if p.stem not in existing]
    if limit:
        todo = todo[:limit]
    print(f"{len(bill_files)} scored bills, {len(existing)} already have references, "
          f"{len(todo)} to fetch.")

    committee_hits = cbo_hits = 0
    async with httpx.AsyncClient() as client:
        async with httpx.AsyncClient(headers={"User-Agent": "crs-summary-benchmark"}) as http:

            async def get(path, **params):
                return await _get(client, key, path, **params)

            for p in todo:
                rec = C.read_json(p)
                out = await process_bill(get, http, rec)
                if out is None:
                    print(f"  {rec['bill_id']}: no committee or CBO reference found; skipped")
                    continue
                if out["committee_summary"]:
                    committee_hits += 1
                if out["cbo_summary"]:
                    cbo_hits += 1
                C.write_json(C.DATA_REFERENCES / f"{rec['bill_id']}.json", out)
                print(f"  {rec['bill_id']}: committee={'yes' if out['committee_summary'] else 'no'} "
                      f"cbo={'yes' if out['cbo_summary'] else 'no'}")

    total = len(bill_files)
    print(f"Done. committee: {committee_hits}/{total} bills, cbo: {cbo_hits}/{total} bills.")


def main() -> None:
    ap = argparse.ArgumentParser(description="Fetch committee-report and CBO reference baselines.")
    ap.add_argument("--limit", type=int, default=None, help="only fetch the first N missing bills")
    args = ap.parse_args()
    asyncio.run(main_async(args.limit))


if __name__ == "__main__":
    main()
