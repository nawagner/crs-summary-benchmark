"""Structure-aware chunking for hierarchical (map-reduce) bill summarization.

Bills too large to summarize in one pass (omnibus appropriations, NDAA, ...) are split
into DIVISION > TITLE > SEC. sections (`split_sections`), packed into model-sized chunks
that never split a section unless the section alone exceeds the budget (`pack_chunks`),
and reduced to a deterministic plain-text table of contents used to ground the judge
(`section_index`). Bill text is plain text produced by `common.html_to_text`.

Budget bookkeeping: `pack_chunks`'s `budget_chars` bounds the sum of the *section* text
lengths placed in a chunk. The one-line breadcrumb header prefixed onto `Chunk.text` is
bookkeeping overhead and is deliberately NOT counted against the budget (a chunk's
`sections` text always sums to <= budget_chars; `Chunk.text` itself is that plus one
short header line).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# --------------------------------------------------------------------- section split

_MAX_HEADING_LINE_LEN = 200

# Ordered (level, pattern) pairs: 0 = division, 1 = title, 2 = section. Checked in this
# order per line so a "DIVISION B--TITLE..." style line is classified as a division, not
# a (nonexistent) title match inside it.
_HEADING_PATTERNS: list[tuple[int, re.Pattern[str]]] = [
    (0, re.compile(r"DIVISION\s+[A-Z]{1,2}\b", re.IGNORECASE)),
    (1, re.compile(r"TITLE\s+[IVXLCDM]+\b", re.IGNORECASE)),
    (2, re.compile(r"SEC(?:TION)?\.?\s+\d+", re.IGNORECASE)),
]

PREAMBLE = ("PREAMBLE",)


@dataclass
class Section:
    heading_path: tuple[str, ...]  # e.g. ("DIVISION B--COMMERCE...", "TITLE III", "SEC. 101.")
    text: str  # the section's text INCLUDING its heading line
    start: int  # char offset into the original text
    end: int  # exclusive


@dataclass
class Chunk:
    text: str  # breadcrumb header line + the sections' concatenated text
    sections: list[Section]
    heading_from: tuple[str, ...]  # heading_path of first section
    heading_to: tuple[str, ...]  # heading_path of last section


def _find_headings(text: str) -> list[tuple[int, int, str]]:
    """(line_start_offset, level, heading_line_text) for each detected heading line.

    A candidate line only counts as a heading if the matched keyword starts the
    (whitespace-trimmed) line and the full line is reasonably short -- this guards
    against mid-sentence false positives (e.g. "...as required by Section 5 of..." is
    not at a line start, and long narrative lines that happen to start with a
    heading-like word are rejected by the length check).
    """
    headings: list[tuple[int, int, str]] = []
    offset = 0
    for raw_line in text.splitlines(keepends=True):
        line = raw_line.rstrip("\r\n")
        content = line.strip()
        if content and len(line) < _MAX_HEADING_LINE_LEN:
            for level, pattern in _HEADING_PATTERNS:
                if pattern.match(content):
                    headings.append((offset, level, content))
                    break
        offset += len(raw_line)
    return headings


def split_sections(text: str) -> list[Section]:
    """Split `text` into structure-aware, non-overlapping sections.

    Lossless invariant: the returned sections are in document order and their
    [start, end) spans exactly partition [0, len(text)) -- every character belongs to
    exactly one section. Text preceding the first detected heading (or the whole text,
    if no heading is found) becomes a single section with heading_path ("PREAMBLE",).
    """
    headings = _find_headings(text)
    if not headings:
        return [Section(PREAMBLE, text, 0, len(text))]

    sections: list[Section] = []
    if headings[0][0] > 0:
        end = headings[0][0]
        sections.append(Section(PREAMBLE, text[0:end], 0, end))

    path: list[str] = []
    for i, (start, level, content) in enumerate(headings):
        path = path[:level] + [content]
        end = headings[i + 1][0] if i + 1 < len(headings) else len(text)
        sections.append(Section(tuple(path), text[start:end], start, end))
    return sections


# ------------------------------------------------------------------------ chunk pack

def _split_offsets(text: str, budget: int, seps: tuple[str, ...] = ("\n\n", "\n")) -> list[tuple[int, int]]:
    """(start, end) offsets partitioning `text` into pieces of length <= budget.

    Prefers cutting on paragraph breaks, then line breaks, then falls back to a hard
    character cut. Each unit's span includes its trailing separator (except the final
    unit) so the returned offsets are contiguous and lossless: piece i's end always
    equals piece i+1's start.
    """
    n = len(text)
    if n <= budget or budget <= 0:
        return [(0, n)]
    if not seps:
        return [(i, min(i + budget, n)) for i in range(0, n, budget)]

    sep = seps[0]
    units: list[tuple[int, int]] = []
    pos = 0
    while True:
        idx = text.find(sep, pos)
        if idx == -1:
            units.append((pos, n))
            break
        units.append((pos, idx + len(sep)))
        pos = idx + len(sep)

    pieces: list[tuple[int, int]] = []
    cur_start: int | None = None
    cur_end: int | None = None
    for ustart, uend in units:
        if uend - ustart > budget:
            if cur_start is not None:
                pieces.append((cur_start, cur_end))
                cur_start = cur_end = None
            sub = _split_offsets(text[ustart:uend], budget, seps[1:])
            pieces.extend((ustart + s, ustart + e) for s, e in sub)
            continue
        if cur_start is None:
            cur_start, cur_end = ustart, uend
        elif uend - cur_start <= budget:
            cur_end = uend
        else:
            pieces.append((cur_start, cur_end))
            cur_start, cur_end = ustart, uend
    if cur_start is not None:
        pieces.append((cur_start, cur_end))
    return pieces


def _breadcrumb(index: int, total: int, heading_from: tuple[str, ...], heading_to: tuple[str, ...]) -> str:
    frm = " > ".join(heading_from) if heading_from else "(start)"
    if heading_to == heading_from:
        where = frm
    else:
        to = " > ".join(heading_to) if heading_to else "(end)"
        where = f"{frm} .. {to}"
    return f"[Part {index}/{total} · {where}]"


def pack_chunks(sections: list[Section], budget_chars: int = 120000) -> list[Chunk]:
    """Greedily pack contiguous sections into chunks of <= budget_chars section text.

    A section is never split across chunks unless it alone exceeds budget_chars, in
    which case it is cut on paragraph boundaries (see `_split_offsets`) into pieces each
    <= budget_chars, with each piece becoming its own dedicated chunk (same heading
    metadata as the original section). Chunks preserve document order; concatenating all
    chunks' section texts reproduces the full input text.
    """
    if not sections:
        return []

    groups: list[list[Section]] = []
    cur: list[Section] = []
    cur_len = 0
    for sec in sections:
        seclen = len(sec.text)
        if seclen > budget_chars:
            if cur:
                groups.append(cur)
                cur, cur_len = [], 0
            for rel_start, rel_end in _split_offsets(sec.text, budget_chars):
                piece = Section(
                    heading_path=sec.heading_path,
                    text=sec.text[rel_start:rel_end],
                    start=sec.start + rel_start,
                    end=sec.start + rel_end,
                )
                groups.append([piece])
            continue
        if cur and cur_len + seclen > budget_chars:
            groups.append(cur)
            cur, cur_len = [], 0
        cur.append(sec)
        cur_len += seclen
    if cur:
        groups.append(cur)

    total = len(groups)
    chunks: list[Chunk] = []
    for i, group in enumerate(groups, start=1):
        heading_from = group[0].heading_path
        heading_to = group[-1].heading_path
        breadcrumb = _breadcrumb(i, total, heading_from, heading_to)
        body = "".join(s.text for s in group)
        chunks.append(Chunk(
            text=breadcrumb + "\n" + body,
            sections=group,
            heading_from=heading_from,
            heading_to=heading_to,
        ))
    return chunks


# --------------------------------------------------------------------- section index

_DOLLAR_RE = re.compile(r"\$[\d,]+(?:\.\d+)?(?:\s*(?:million|billion))?", re.IGNORECASE)
_DOLLAR_VALUE_RE = re.compile(r"\$([\d,]+(?:\.\d+)?)(?:\s*(million|billion))?", re.IGNORECASE)

_PREAMBLE_MIN_CHARS = 200
_TRUNCATED_NOTE = "(truncated)"
_SECTIONS_OMITTED_NOTE = "(section-level detail omitted)"


def _dollar_value(raw: str) -> float:
    m = _DOLLAR_VALUE_RE.match(raw)
    if not m:
        return 0.0
    value = float(m.group(1).replace(",", ""))
    unit = (m.group(2) or "").lower()
    if unit == "million":
        value *= 1_000_000
    elif unit == "billion":
        value *= 1_000_000_000
    return value


def _top_dollar_amounts(text: str, limit: int = 3) -> list[str]:
    amounts = _DOLLAR_RE.findall(text)
    if not amounts:
        return []
    amounts.sort(key=_dollar_value, reverse=True)
    return amounts[:limit]


def _index_line(depth: int, label: str, text: str) -> str:
    line = ("  " * depth) + label
    amounts = _top_dollar_amounts(text)
    if amounts:
        line += " [" + ", ".join(amounts) + "]"
    return line


def _index_lines(sections: list[Section], include_section_level: bool) -> list[str]:
    lines: list[str] = []
    for sec in sections:
        depth = len(sec.heading_path) - 1
        if sec.heading_path == PREAMBLE:
            if len(sec.text) < _PREAMBLE_MIN_CHARS:
                continue
            lines.append(_index_line(0, "PREAMBLE", sec.text))
            continue
        if not include_section_level and depth >= 2:
            continue
        lines.append(_index_line(depth, sec.heading_path[-1], sec.text))
    return lines


def section_index(sections: list[Section], max_chars: int = 60000) -> str:
    """A deterministic plain-text table of contents used to ground the judge.

    One line per section, indented by hierarchy depth, showing the heading and (when
    present) up to the 3 largest dollar amounts found in that section's text. The
    PREAMBLE line is skipped when its text is under 200 chars. If the full index would
    exceed max_chars, section-level (SEC.) lines are dropped, keeping only
    division/title lines plus a "(section-level detail omitted)" note; if that is still
    too long, the result is hard-truncated to max_chars with a trailing "(truncated)"
    marker.
    """
    full = "\n".join(_index_lines(sections, include_section_level=True))
    if len(full) <= max_chars:
        return full

    reduced = "\n".join(_index_lines(sections, include_section_level=False) + [_SECTIONS_OMITTED_NOTE])
    if len(reduced) <= max_chars:
        return reduced

    suffix = "\n" + _TRUNCATED_NOTE
    cut = max(0, max_chars - len(suffix))
    return reduced[:cut] + suffix
