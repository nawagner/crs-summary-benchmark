"""Tests for src/reauth.py: amendatory/reauthorization bill classification."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import reauth as R  # noqa: E402


def bill(title="A bill", btype="hr", text=""):
    return {"title": title, "type": btype, "bill_text": text}


# Text with exactly `n` amendatory operations padded to `chars` total length.
def amendy(n, chars):
    body = " ".join("is amended" for _ in range(n)) + " "
    return body + "x" * max(chars - len(body), 0)


def test_amend_stats_empty():
    assert R.amend_stats("") == (0, 0.0)
    assert R.amend_stats(None if False else "") == (0, 0.0)


def test_amend_stats_counts_all_patterns():
    text = ("Section 1 is amended by striking 'a' and by inserting 'b'. "
            "Sections 2 and 3 are amended. Section 4 is repealed.")
    count, density = R.amend_stats(text)
    assert count == 5
    assert density == round(5 / (len(text) / 1000), 3)


@pytest.mark.parametrize(
    "title,btype,expected",
    [
        ("Providing for congressional disapproval under chapter 8 of title 5, "
         "United States Code, of the rule submitted by the Department of Energy",
         "hjres", R.CAT_CRA),
        ("Disapproving the action of the District of Columbia Council",
         "sjres", R.CAT_CRA),
    ],
)
def test_cra_disapproval_wins_at_zero_density(title, btype, expected):
    assert R.classify_bill(bill(title, btype))["category"] == expected


def test_cra_pattern_requires_joint_resolution_type():
    # A plain hr bill about disapproval language is not a CRA resolution.
    b = bill("Providing for congressional disapproval of the rule", "hr")
    assert R.classify_bill(b)["category"] == R.CAT_STANDALONE


def test_appropriations_title_beats_high_amend_count_at_low_density():
    # Omnibus rider divisions push raw counts up while density stays low; the
    # appropriations title must win regardless of the count.
    text = amendy(100, 1_000_000)  # density 0.1, count 100
    b = bill("Consolidated Appropriations Act, 2024", "hr", text)
    got = R.classify_bill(b)
    assert got["category"] == R.CAT_APPROPS
    assert got["amend_count"] == 100


@pytest.mark.parametrize(
    "title",
    [
        "Making emergency supplemental appropriations for the fiscal year 2024",
        "Continuing Appropriations and Other Matters Act, 2024",
        "Military Construction, Veterans Affairs Appropriations Act, 2025",
    ],
)
def test_appropriations_title_variants(title):
    assert R.classify_bill(bill(title, "hr"))["category"] == R.CAT_APPROPS


def test_reauth_title_with_low_density_is_amendatory():
    b = bill("Firefighter Cancer Registry Reauthorization Act of 2023", "hr",
             "x" * 5000)  # zero amendatory operations
    got = R.classify_bill(b)
    assert got["category"] == R.CAT_AMENDATORY
    assert got["title_reauth"] is True


def test_ndaa_style_lands_amendatory_via_density():
    # No reauth/approps title match; density 0.6 clears the threshold.
    text = amendy(60, 100_000)
    got = R.classify_bill(bill(
        "National Defense Authorization Act for Fiscal Year 2024", "hr", text))
    assert got["category"] == R.CAT_AMENDATORY
    assert got["title_reauth"] is False


def test_density_threshold_boundary():
    below = amendy(49, 100_000)   # density 0.49
    at = amendy(50, 100_000)      # density 0.50
    assert R.classify_bill(bill(text=below))["category"] == R.CAT_STANDALONE
    assert R.classify_bill(bill(text=at))["category"] == R.CAT_AMENDATORY


def test_standalone_default_and_missing_fields():
    assert R.classify_bill({})["category"] == R.CAT_STANDALONE
    got = R.classify_bill(bill("A bill to establish a commission", "s",
                               "self-contained provisions only"))
    assert got == {"category": R.CAT_STANDALONE, "title_reauth": False,
                   "amend_count": 0, "amend_density": 0.0}
