"""Tests for src/chunking.py: structure-aware splitting for hierarchical summarization.

Budget convention under test: `pack_chunks`'s budget_chars bounds the sum of each
chunk's SECTION text (excluding the breadcrumb header line prepended to Chunk.text).
See the module docstring in src/chunking.py for the rationale.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import chunking as CH  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


# ------------------------------------------------------------------------- fixtures

def _sec(num: int, title: str, body_paragraphs: list[str]) -> str:
    return f"SEC. {num}. {title}.\n" + "\n\n".join(body_paragraphs) + "\n\n"


def build_omnibus(giant_paragraph_count: int = 40) -> str:
    """A synthetic omnibus: preamble, 2 divisions x 2 titles x 4 sections, one giant
    section with paragraph breaks, and assorted dollar amounts."""
    preamble = (
        "[Congressional Bills 119th Congress]\n"
        "A BILL\nMaking omnibus appropriations for the fiscal year, and for other "
        "purposes.\n\nBe it enacted by the Senate and House of Representatives of the "
        "United States of America in Congress assembled,\n\n"
    )
    parts = [preamble]
    for div_letter in ("A", "B"):
        div_name = f"DIVISION {div_letter}--COMMERCE, JUSTICE, SCIENCE, AND RELATED AGENCIES"
        parts.append(div_name + "\n\n")
        for title_num in ("I", "II"):
            parts.append(f"TITLE {title_num}--GENERAL PROVISIONS\n\n")
            for sec_num in range(1, 5):
                global_num = (100 if title_num == "I" else 200) + sec_num
                if div_letter == "A" and title_num == "I" and sec_num == 1:
                    # the giant section: many paragraphs, pushes it over any small budget
                    paragraphs = [
                        f"Paragraph {i} of the giant section discusses appropriations "
                        f"and program details at some length so that this section on "
                        f"its own comfortably exceeds a small test budget of 2000 "
                        f"characters when combined with its many siblings below."
                        for i in range(giant_paragraph_count)
                    ]
                    parts.append(_sec(global_num, "Giant appropriations section", paragraphs))
                elif sec_num == 2:
                    parts.append(_sec(
                        global_num,
                        "Funding levels",
                        [f"There is appropriated $1,500,000,000 for necessary expenses.",
                         f"An additional $25 million is provided for related programs."],
                    ))
                else:
                    parts.append(_sec(
                        global_num,
                        f"Provision {sec_num}",
                        [f"This section makes a minor technical amendment number {sec_num}."],
                    ))
    return "".join(parts)


OMNIBUS = build_omnibus()


# --------------------------------------------------------------------- split_sections

def _assert_partition(sections: list[CH.Section], text: str) -> None:
    assert sections, "expected at least one section"
    assert sections[0].start == 0
    assert sections[-1].end == len(text)
    for i in range(len(sections) - 1):
        assert sections[i].end == sections[i + 1].start, (
            f"gap/overlap between section {i} ({sections[i].end}) and "
            f"{i + 1} ({sections[i + 1].start})"
        )
    for sec in sections:
        assert sec.end - sec.start == len(sec.text)
        assert text[sec.start:sec.end] == sec.text


def test_split_sections_partition_invariant_synthetic():
    sections = CH.split_sections(OMNIBUS)
    _assert_partition(sections, OMNIBUS)


def test_split_sections_reconstruction_synthetic():
    sections = CH.split_sections(OMNIBUS)
    assert "".join(s.text for s in sections) == OMNIBUS


def test_split_sections_preamble_present_and_first():
    sections = CH.split_sections(OMNIBUS)
    assert sections[0].heading_path == ("PREAMBLE",)
    assert sections[0].start == 0


def test_split_sections_hierarchy_paths():
    sections = CH.split_sections(OMNIBUS)
    division_sections = [s for s in sections if len(s.heading_path) == 1 and s.heading_path != ("PREAMBLE",)]
    assert any(p.heading_path[0].startswith("DIVISION A--") for p in division_sections)

    sec_sections = [s for s in sections if len(s.heading_path) == 3]
    assert sec_sections, "expected fully-nested SEC. sections"
    example = sec_sections[0]
    assert example.heading_path[0].startswith("DIVISION")
    assert example.heading_path[1].startswith("TITLE")
    assert example.heading_path[2].startswith("SEC.")


def test_split_sections_no_headings_single_preamble():
    text = "Just some plain text with no structural headings at all, over multiple " \
           "sentences and lines.\nSecond line.\nThird line."
    sections = CH.split_sections(text)
    assert len(sections) == 1
    assert sections[0].heading_path == ("PREAMBLE",)
    assert sections[0].start == 0 and sections[0].end == len(text)
    assert sections[0].text == text


def test_split_sections_ignores_midsentence_and_overlong_title_lines():
    long_title_line = "TITLE " + "IX" + " " + ("x" * 250)  # over the 200-char guard
    text = (
        "This bill was passed pursuant to Section 5 of the prior Act, which does "
        "not start a new heading because it is mid-sentence.\n"
        f"{long_title_line}\n"
        "More body text follows.\n"
    )
    sections = CH.split_sections(text)
    # nothing should be recognized as a heading: single PREAMBLE section
    assert len(sections) == 1
    assert sections[0].heading_path == ("PREAMBLE",)
    _assert_partition(sections, text)


# ------------------------------------------------------------------------ pack_chunks

def test_pack_chunks_budget_respected_and_order_preserved_synthetic():
    sections = CH.split_sections(OMNIBUS)
    budget = 2000
    chunks = CH.pack_chunks(sections, budget_chars=budget)
    assert chunks, "expected at least one chunk"

    for chunk in chunks:
        section_text_len = sum(len(s.text) for s in chunk.sections)
        assert section_text_len <= budget

    # breadcrumb is the first line of each chunk's text
    for chunk in chunks:
        first_line = chunk.text.splitlines()[0]
        assert first_line.startswith("[Part ")
        assert chunk.text.startswith(first_line + "\n")

    # order preserved: sections' start offsets are non-decreasing across chunks
    starts = [s.start for chunk in chunks for s in chunk.sections]
    assert starts == sorted(starts)


def test_pack_chunks_reconstruction_synthetic():
    sections = CH.split_sections(OMNIBUS)
    chunks = CH.pack_chunks(sections, budget_chars=2000)
    reconstructed = "".join(s.text for chunk in chunks for s in chunk.sections)
    assert reconstructed == OMNIBUS


def test_pack_chunks_giant_section_split_into_multiple_chunks_each_within_budget():
    sections = CH.split_sections(OMNIBUS)
    budget = 2000
    giant = next(s for s in sections if "Giant appropriations section" in s.text)
    assert len(giant.text) > budget, "fixture's giant section must exceed the test budget"

    chunks = CH.pack_chunks(sections, budget_chars=budget)
    giant_chunks = [c for c in chunks if c.heading_from == giant.heading_path]
    assert len(giant_chunks) > 1, "the oversized section should be split across multiple chunks"
    for c in giant_chunks:
        assert len(c.sections) == 1, "each split piece of an oversized section is its own chunk"
        assert sum(len(s.text) for s in c.sections) <= budget

    # the pieces, concatenated in order, reproduce the giant section's original text
    rebuilt = "".join(s.text for c in giant_chunks for s in c.sections)
    assert rebuilt == giant.text


def test_pack_chunks_large_budget_single_chunk():
    sections = CH.split_sections(OMNIBUS)
    chunks = CH.pack_chunks(sections, budget_chars=len(OMNIBUS) + 1000)
    assert len(chunks) == 1
    assert chunks[0].heading_from == sections[0].heading_path
    assert chunks[0].heading_to == sections[-1].heading_path


def test_pack_chunks_empty_sections():
    assert CH.pack_chunks([], budget_chars=1000) == []


# ---------------------------------------------------------------------- section_index

def test_section_index_contains_division_and_title_headings():
    sections = CH.split_sections(OMNIBUS)
    index = CH.section_index(sections)
    assert "DIVISION A--COMMERCE, JUSTICE, SCIENCE, AND RELATED AGENCIES" in index
    assert "TITLE I--GENERAL PROVISIONS" in index


def test_section_index_contains_top_dollar_amount():
    sections = CH.split_sections(OMNIBUS)
    index = CH.section_index(sections)
    assert "$1,500,000,000" in index


def test_section_index_small_max_chars_triggers_fallback():
    sections = CH.split_sections(OMNIBUS)
    full = CH.section_index(sections)
    small = CH.section_index(sections, max_chars=300)
    assert len(small) <= 300
    assert len(small) < len(full)
    assert "(section-level detail omitted)" in small or small.endswith("(truncated)")


def test_section_index_skips_short_preamble():
    text = "A BILL\n\nSEC. 1. SHORT TITLE.\nThis Act may be cited as the Example Act.\n"
    sections = CH.split_sections(text)
    assert sections[0].heading_path == ("PREAMBLE",)
    assert len(sections[0].text) < 200
    index = CH.section_index(sections)
    assert "PREAMBLE" not in index


# ------------------------------------------------------------------ real-data fixture

def _largest_bill_text() -> str:
    bills_dir = ROOT / "data" / "bills"
    if not bills_dir.exists():
        pytest.skip(f"{bills_dir} not present")
    files = sorted(bills_dir.glob("*.json"))
    if not files:
        pytest.skip(f"no bill files in {bills_dir}")
    best_text = ""
    best_chars = -1
    for f in files:
        with open(f) as fh:
            data = json.load(fh)
        chars = data.get("bill_text_chars") or len(data.get("bill_text", ""))
        if chars > best_chars:
            best_chars = chars
            best_text = data.get("bill_text", "")
    if not best_text:
        pytest.skip("largest bill has no bill_text")
    return best_text


def test_real_bill_partition_and_reconstruction():
    text = _largest_bill_text()
    sections = CH.split_sections(text)
    _assert_partition(sections, text)
    assert "".join(s.text for s in sections) == text


def test_real_bill_pack_chunks_budget_and_reconstruction():
    text = _largest_bill_text()
    sections = CH.split_sections(text)
    budget = 20000
    chunks = CH.pack_chunks(sections, budget_chars=budget)
    assert chunks
    for chunk in chunks:
        assert sum(len(s.text) for s in chunk.sections) <= budget
        first_line = chunk.text.splitlines()[0]
        assert first_line.startswith("[Part ")
    reconstructed = "".join(s.text for chunk in chunks for s in chunk.sections)
    assert reconstructed == text


def test_real_bill_section_index_respects_max_chars():
    text = _largest_bill_text()
    sections = CH.split_sections(text)
    index = CH.section_index(sections, max_chars=300)
    assert len(index) <= 300
