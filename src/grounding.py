"""Lexical retrieval + packing of bill sections for the long-bill judge prompt (issue #7).

For very long bills (up to ~3M chars) the judge model cannot read the full text. Instead
the judge prompt receives (1) a deterministic section index of the whole bill, built
elsewhere, plus (2) the raw text of the sections most lexically relevant to the summary
being judged, packed under a char cap. This module does the lexical retrieval and
packing. Sections arrive as plain dicts (this module must not import src/chunking.py).
"""
from __future__ import annotations

import math
import re
from collections import Counter

Section = dict  # {"heading": str, "text": str} -- heading is a display string, text includes the heading line

# Small English/legalese stopword set: connective and generic-legislative words that add
# noise to lexical overlap scoring without signalling topical relevance.
STOPWORDS = {
    "the", "of", "and", "to", "in", "for", "a", "be", "is", "shall", "section", "act",
    "bill", "such", "any", "this", "that", "or", "by", "as", "with", "under", "not",
    "on", "may", "other", "secretary", "united", "states", "an", "are", "was", "were",
    "will", "from", "at", "which", "each", "all", "if", "than", "no", "it", "its",
    "who", "shall", "has", "have", "had", "been", "amended", "title", "chapter",
    "public", "law", "code",
}

_TOKEN_RE = re.compile(r"[$]?[\w,]+")


def tokenize(text: str) -> list[str]:
    """Lowercased alphanumeric tokens, with stopwords removed.

    Keeps numbers and dollar figures: "$1,500,000" -> "1500000" (strip $ and commas so
    dollar amounts match regardless of how they're formatted).
    """
    tokens: list[str] = []
    for raw in _TOKEN_RE.findall(text.lower()):
        tok = raw.replace("$", "").replace(",", "")
        tok = tok.strip("_")
        if not tok or not re.search(r"[a-z0-9]", tok):
            continue
        if tok in STOPWORDS:
            continue
        tokens.append(tok)
    return tokens


def rank_sections(summary: str, sections: list[Section]) -> list[tuple[int, float]]:
    """Score each section by lexical relevance to the summary.

    Score = sum over unique summary tokens of tf-in-section * idf, where
    idf = log(1 + N/df) (df = number of sections containing the token, N = section
    count), normalized by sqrt(section token count) so long sections don't win purely
    on length. Returns (index, score) sorted by score desc, ties broken by index asc.
    """
    n = len(sections)
    section_tokens = [tokenize(s.get("text", "")) for s in sections]
    section_counters = [Counter(toks) for toks in section_tokens]

    summary_tokens = set(tokenize(summary))

    df: dict[str, int] = {}
    for tok in summary_tokens:
        df[tok] = sum(1 for counter in section_counters if counter[tok] > 0)

    idf = {tok: math.log(1 + n / df[tok]) for tok in summary_tokens if df[tok] > 0}

    scores: list[tuple[int, float]] = []
    for i, counter in enumerate(section_counters):
        norm = math.sqrt(len(section_tokens[i])) or 1.0
        score = sum(counter[tok] * idf[tok] for tok in summary_tokens if tok in idf) / norm
        scores.append((i, score))

    scores.sort(key=lambda pair: (-pair[1], pair[0]))
    return scores


def build_grounding(
    summary: str,
    index_text: str,
    sections: list[Section],
    cap_chars: int = 400000,
) -> str:
    """The judge's bill-text replacement.

    Layout: index_text first (always included, even if it alone exceeds cap_chars --
    then it is truncated to cap_chars), then a separator line, then the top-ranked
    sections in DOCUMENT ORDER (not score order), each preceded by a line
    '=== {heading} ===', greedily added while the running total stays <= cap_chars.
    Sections with score 0 are never included. Ends with a count line noting how many of
    the bill's sections are shown (that line must also fit within cap_chars).
    """
    if len(index_text) > cap_chars:
        index_text = index_text[:cap_chars]

    header = "\n\n--- Most relevant sections (full text) ---\n"
    blocks: dict[int, str] = {}
    for i, section in enumerate(sections):
        heading = section.get("heading", "")
        blocks[i] = f"\n=== {heading} ===\n{section.get('text', '')}\n"

    # Select which sections to include in SCORE order (best first), so a tight cap keeps
    # the most relevant material even if it lives late in the document.
    total = len(index_text) + len(header)
    selected: set[int] = set()
    for i, score in rank_sections(summary, sections):
        if score <= 0:
            break
        block_len = len(blocks[i])
        if total + block_len <= cap_chars:
            selected.add(i)
            total += block_len

    # Assemble the chosen sections in DOCUMENT order for a coherent read.
    parts = [index_text, header]
    parts.extend(blocks[i] for i in sorted(selected))

    count_line = (
        f"\n[Showing {len(selected)} of {len(sections)} sections, selected for "
        "relevance to the summary under review.]"
    )
    total = sum(len(p) for p in parts)
    if total + len(count_line) > cap_chars:
        # Trim trailing content to make room; the count line always ends the output.
        overflow = total + len(count_line) - cap_chars
        parts[-1] = parts[-1][: max(0, len(parts[-1]) - overflow)]
    parts.append(count_line)

    return "".join(parts)
