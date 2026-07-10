"""Tests for src/fetch_references.py's pure extractors (issue #8 research spike).

fetch_references.py has two halves: a network-dependent fetch flow (congress.gov +
govinfo.gov + cbo.gov, gated on CONGRESS_API_KEY) that cannot be exercised in this
environment, and the pure extractors below, which are the tested core.

Fixtures under tests/fixtures/references/:
- crpt_118hrpt50.html / crpt_118hrpt1_no_heading.html: REAL govinfo.gov committee-report
  pages, fetched live for this test suite (House Report 118-50 has a "Purpose and
  Summary" section; House Report 118-1, a Rules Committee report, does not).
- cbo_*.html: cbo.gov blocks non-browser HTTP clients from this environment (Datadome
  bot-check, 403), so these are SYNTHETIC fixtures modeled on real cbo.gov publication-page
  markup (Drupal `field field-name-body` wrappers, `<meta name="description">`/
  `og:description`). Swapping in a live capture (e.g. via a real browser session) is a
  maintainer follow-up; see the docstring in cbo_meta_description.html.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import fetch_references as F  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures" / "references"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text()


# --------------------------------------------------------------- extract_purpose_and_summary
def test_extract_purpose_and_summary_finds_section():
    text = F.extract_purpose_and_summary(_read("crpt_118hrpt50.html"))
    assert text is not None
    assert "National Institute of Standards and Technology" in text
    assert "novel synthetic opioids" in text
    # must stop before the next section's heading, not swallow it
    assert "BACKGROUND AND NEED FOR LEGISLATION" not in text
    assert "LEGISLATIVE HISTORY" not in text


def test_extract_purpose_and_summary_none_when_heading_missing():
    # House Report 118-1 is a Rules Committee report (a "providing for consideration
    # of..." report) with no "Purpose and Summary" section at all.
    assert F.extract_purpose_and_summary(_read("crpt_118hrpt1_no_heading.html")) is None


def test_extract_purpose_and_summary_none_on_empty_input():
    assert F.extract_purpose_and_summary("") is None
    assert F.extract_purpose_and_summary("<html><body>no headings here</body></html>") is None


def test_extract_purpose_and_summary_none_when_body_too_short():
    # heading present but immediately followed by another heading -- captured body
    # is empty/short, so this should not be treated as a real match.
    html_doc = (
        "<html><body><pre>\n"
        "                          PURPOSE AND SUMMARY\n\n"
        "    Too short.\n\n"
        "                  BACKGROUND AND NEED FOR LEGISLATION\n"
        "    Lots more text follows here that is irrelevant to this test case.\n"
        "</pre></body></html>"
    )
    assert F.extract_purpose_and_summary(html_doc) is None


def test_extract_purpose_and_summary_allows_numbered_prefix():
    body = "This bill would do something important. " * 5
    html_doc = (
        "<html><body><pre>\n"
        f"I. PURPOSE AND SUMMARY\n\n{body}\n\n"
        "II. BACKGROUND AND NEED FOR LEGISLATION\n"
        "    More text that must not appear in the extracted result at all.\n"
        "</pre></body></html>"
    )
    text = F.extract_purpose_and_summary(html_doc)
    assert text is not None
    assert "something important" in text
    assert "BACKGROUND" not in text


# ------------------------------------------------------------------------ extract_cbo_summary
def test_extract_cbo_summary_meta_description():
    text = F.extract_cbo_summary(_read("cbo_meta_description.html"))
    assert text is not None
    assert len(text) >= 100
    assert "H.R. 1234" in text
    assert "$2.3 billion" in text
    # meta name="description" takes priority over og:description
    assert "og fallback" not in text


def test_extract_cbo_summary_fallback_paragraphs():
    text = F.extract_cbo_summary(_read("cbo_fallback_paragraphs.html"))
    assert text is not None
    assert "rural broadband" in text
    assert "pay-as-you-go" in text
    # nav chrome and short blocks (date, JCX id) must be excluded
    assert "Skip to main content" not in text
    assert "Menu" not in text
    assert "JCX-12-25" not in text


def test_extract_cbo_summary_none_on_nav_only_page():
    assert F.extract_cbo_summary(_read("cbo_nav_only.html")) is None


def test_extract_cbo_summary_none_on_empty_input():
    assert F.extract_cbo_summary("") is None
    assert F.extract_cbo_summary("<html><body></body></html>") is None


def test_extract_cbo_summary_short_meta_description_falls_through():
    # a meta description under 100 chars should not be returned outright -- it should
    # fall through to the paragraph-based extraction (which here finds nothing).
    html_doc = (
        '<html><head><meta name="description" content="Too short."></head>'
        "<body><p>Nav only.</p></body></html>"
    )
    assert F.extract_cbo_summary(html_doc) is None


# --------------------------------------------------------------------- parse_committee_citation
def test_parse_committee_citation_house_and_senate():
    assert F.parse_committee_citation("H. Rept. 118-125") == (118, "hrpt", 125)
    assert F.parse_committee_citation("S. Rept. 118-45") == (118, "srpt", 45)


def test_parse_committee_citation_unparseable_returns_none():
    assert F.parse_committee_citation("") is None
    assert F.parse_committee_citation("some garbage string") is None
