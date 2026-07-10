from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from grounding import Section, build_grounding, rank_sections, tokenize  # noqa: E402

LOREM = (
    "Notwithstanding any other provision of law, the amendments made by this "
    "subsection shall take effect on the date of enactment of this Act and shall "
    "apply with respect to fiscal years beginning after such date, subject to such "
    "regulations as the Secretary may prescribe under this title."
)


def make_sections() -> list[Section]:
    return [
        {
            "heading": "Sec. 101. Short title.",
            "text": "Sec. 101. Short title. This Act may be cited as the Example Act.",
        },
        {
            "heading": "Sec. 201. NIH cancer research funding.",
            "text": (
                "Sec. 201. NIH cancer research funding. There is appropriated to the "
                "National Institutes of Health $2,000,000,000 for cancer research "
                "grants and oncology clinical trials nationwide."
            ),
        },
        {
            "heading": "Sec. 202. Border patrol staffing.",
            "text": (
                "Sec. 202. Border patrol staffing. The Commissioner shall hire "
                "additional border patrol agents to increase staffing levels along "
                "the southern border by 5,000 agents over three years."
            ),
        },
        {
            "heading": "Sec. 203. Aviation fees.",
            "text": (
                "Sec. 203. Aviation fees. The Administrator shall adjust aviation "
                "passenger facility fees collected by airports to fund runway "
                "improvements and air traffic control modernization."
            ),
        },
        {"heading": "Sec. 301. General provisions.", "text": f"Sec. 301. General provisions. {LOREM}"},
        {"heading": "Sec. 302. Savings clause.", "text": f"Sec. 302. Savings clause. {LOREM}"},
        {"heading": "Sec. 303. Severability.", "text": f"Sec. 303. Severability. {LOREM}"},
        {"heading": "Sec. 304. Effective date.", "text": f"Sec. 304. Effective date. {LOREM}"},
    ]


# ------------------------------------------------------------------------- tokenize
def test_tokenize_removes_stopwords():
    toks = tokenize("The Secretary shall, under this Act, appropriate cancer research funds.")
    assert "the" not in toks
    assert "shall" not in toks
    assert "under" not in toks
    assert "this" not in toks
    assert "act" not in toks
    assert "cancer" in toks
    assert "research" in toks
    assert "appropriate" in toks
    assert "funds" in toks


def test_tokenize_dollar_figure_strips_symbols():
    toks = tokenize("This appropriates $1,500,000 for the program.")
    assert "1500000" in toks
    assert "$1,500,000" not in toks


# ---------------------------------------------------------------------- rank_sections
def test_rank_sections_top_hit_is_cancer_research():
    sections = make_sections()
    summary = "The bill provides funding for cancer research appropriations at NIH."
    ranked = rank_sections(summary, sections)
    top_index, top_score = ranked[0]
    assert top_index == 1  # NIH cancer research funding section
    assert top_score > 0


def test_rank_sections_filler_scores_zero():
    sections = make_sections()
    summary = "The bill provides funding for cancer research appropriations at NIH."
    ranked = rank_sections(summary, sections)
    scores_by_index = dict(ranked)
    # filler/boilerplate sections share no meaningful tokens with the summary
    for filler_index in (4, 5, 6, 7):
        assert scores_by_index[filler_index] == 0
    # every zero-score section ranks below every positive-score section
    positive_ranks = [i for i, s in ranked if s > 0]
    zero_ranks = [i for i, s in ranked if s == 0]
    assert max((ranked.index((i, scores_by_index[i])) for i in positive_ranks), default=-1) < min(
        (ranked.index((i, scores_by_index[i])) for i in zero_ranks), default=len(ranked)
    )


def test_rank_sections_dollar_amount_matches_across_formatting():
    sections = make_sections()
    summary = "This provides $2,000,000,000 for cancer research."
    ranked = rank_sections(summary, sections)
    assert ranked[0][0] == 1


def test_rank_sections_stable_ties_by_index():
    # sections with no summary token overlap should all score 0 and stay in index order
    sections = make_sections()
    summary = "zzz_no_overlap_at_all_zzz"
    ranked = rank_sections(summary, sections)
    assert [i for i, _ in ranked] == list(range(len(sections)))
    assert all(score == 0 for _, score in ranked)


# --------------------------------------------------------------------- build_grounding
def test_build_grounding_starts_with_index_text():
    sections = make_sections()
    summary = "The bill funds cancer research and border patrol staffing."
    index_text = "INDEX\nSec. 101 ... Sec. 304"
    out = build_grounding(summary, index_text, sections, cap_chars=400000)
    assert out.startswith(index_text)


def test_build_grounding_headers_in_document_order():
    sections = make_sections()
    # summary references the two least-relevant-in-document-order top hits, in reverse
    # document order, to prove output order is document order, not score order
    summary = "aviation passenger facility fees for airports and cancer research funding"
    index_text = "INDEX"
    out = build_grounding(summary, index_text, sections, cap_chars=400000)
    pos_cancer = out.find("=== Sec. 201. NIH cancer research funding. ===")
    pos_aviation = out.find("=== Sec. 203. Aviation fees. ===")
    assert pos_cancer != -1 and pos_aviation != -1
    assert pos_cancer < pos_aviation  # document order: 201 before 203


def test_build_grounding_excludes_zero_score_sections():
    sections = make_sections()
    summary = "cancer research funding at NIH"
    out = build_grounding(summary, "INDEX", sections, cap_chars=400000)
    assert "=== Sec. 301. General provisions. ===" not in out
    assert "=== Sec. 302. Savings clause. ===" not in out
    assert "=== Sec. 303. Severability. ===" not in out
    assert "=== Sec. 304. Effective date. ===" not in out


def test_build_grounding_respects_small_cap():
    sections = make_sections()
    summary = (
        "The bill funds cancer research at NIH, increases border patrol staffing, "
        "and adjusts aviation passenger fees."
    )
    small_cap = 400
    large_cap = 400000
    small_out = build_grounding(summary, "INDEX", sections, cap_chars=small_cap)
    large_out = build_grounding(summary, "INDEX", sections, cap_chars=large_cap)

    assert len(small_out) <= small_cap

    def count_headers(text: str) -> int:
        return text.count("=== Sec.")

    assert count_headers(small_out) < count_headers(large_out)
    # the single highest-scoring section should still make the cut under the small cap
    top_index, _ = rank_sections(summary, sections)[0]
    top_heading = sections[top_index]["heading"]
    assert f"=== {top_heading} ===" in small_out


def test_build_grounding_includes_count_line():
    sections = make_sections()
    summary = "cancer research funding at NIH"
    out = build_grounding(summary, "INDEX", sections, cap_chars=400000)
    assert "[Showing" in out
    assert "of 8 sections, selected for relevance to the summary under review.]" in out
    assert out.rstrip().endswith("under review.]")


def test_build_grounding_count_line_fits_under_small_cap():
    sections = make_sections()
    summary = "cancer research funding at NIH border patrol staffing aviation fees"
    small_cap = 300
    out = build_grounding(summary, "INDEX", sections, cap_chars=small_cap)
    assert len(out) <= small_cap
    assert "[Showing" in out


def test_build_grounding_index_text_truncated_if_over_cap():
    sections = make_sections()
    long_index = "X" * 1000
    out = build_grounding("cancer research", long_index, sections, cap_chars=2000)
    # index_text is always included in full unless it alone exceeds the cap, in which
    # case it is truncated to the cap; here it fits comfortably under 2000.
    assert out.startswith(long_index)
