"""Tests for src/readability.py: pure-Python Flesch-Kincaid grade-level scoring."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import readability as R  # noqa: E402


# --------------------------------------------------------------------------- fk_grade
def test_fk_grade_exact_formula_on_hand_countable_text():
    # "The cat sat. The dog ran." -> 6 words, 2 sentences, 6 syllables (all monosyllabic).
    # 0.39*(6/2) + 11.8*(6/6) - 15.59 = 0.39*3 + 11.8 - 15.59 = -2.6
    score = R.fk_grade("The cat sat. The dog ran.")
    assert score == pytest.approx(-2.6, abs=0.05)


def test_fk_grade_monotonicity_simple_vs_dense():
    simple = (
        "The cat sat on the mat. The dog ran to the park. It was a sunny day. "
        "The kids liked to play. They had a lot of fun."
    )
    legal = (
        "Notwithstanding any other provision of law, the appropriations authorized "
        "under this subparagraph shall remain available for obligation and "
        "expenditure until the Secretary determines, in consultation with the "
        "relevant congressional committees, that the underlying appropriations "
        "accounts have been fully reconciled with applicable budgetary authority."
    )
    simple_score = R.fk_grade(simple)
    legal_score = R.fk_grade(legal)
    assert simple_score is not None and legal_score is not None
    assert legal_score >= simple_score + 5


def test_fk_grade_canonical_calibration_plain_journalistic_english():
    passage = (
        "City officials said Tuesday that the new transit line will open next "
        "spring after months of delay. The project, which began three years ago, "
        "ran over budget because of rising material costs and labor shortages. "
        "Local business owners welcomed the news, saying the extra riders should "
        "help shops near the planned stations. Officials still must finish safety "
        "testing before trains can carry passengers on a regular schedule."
    )
    score = R.fk_grade(passage)
    assert score is not None
    assert 7 - 1.5 <= score <= 13 + 1.5


def test_fk_grade_empty_and_whitespace_only():
    assert R.fk_grade("") is None
    assert R.fk_grade("   ") is None


# --------------------------------------------------------------------- count_syllables
def test_count_syllables_spot_checks():
    assert R.count_syllables("cat") == 1
    assert R.count_syllables("table") == 2
    assert R.count_syllables("created") in {2, 3}
    assert R.count_syllables("appropriations") in {4, 5, 6}
    assert R.count_syllables("law") == 1
    assert R.count_syllables("the") == 1
    assert R.count_syllables("notwithstanding") in {4, 5}


# --------------------------------------------------------------------- split_sentences
def test_split_sentences_abbreviations_and_citations():
    sentences = R.split_sentences(
        "The U.S. Congress passed Sec. 3. It funds programs."
    )
    assert len(sentences) == 2


def test_split_sentences_decimal_numbers():
    sentences = R.split_sentences("Costs rose 3.5 percent. Agencies adapted.")
    assert len(sentences) == 2
