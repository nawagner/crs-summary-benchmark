"""Pure-Python Flesch-Kincaid grade-level scoring for bill summaries (no deps)."""
from __future__ import annotations

import re

_VOWELS = "aeiou"

# Abbreviations (and initialisms) whose trailing period must not be treated as a
# sentence terminator. Matched case-insensitively on a word boundary; the pattern
# covers the internal periods of things like "U.S." and "i.e." as a single token.
_ABBREV_RE = re.compile(
    r"\b(?:U\.S|U\.K|i\.e|e\.g|Sec|No|Mr|Mrs|Ms|Dr|Prof|Gov|Rep|Sen|Rev|Gen|Col|"
    r"Capt|Lt|Jr|Sr|St|vs|al|etc|Fig|Vol|pp|cf)\.",
    re.IGNORECASE,
)
_DECIMAL_RE = re.compile(r"(?<=\d)\.(?=\d)")
_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_ALNUM_RE = re.compile(r"[A-Za-z0-9]")
_NON_ALPHA_RE = re.compile(r"[^A-Za-z]")

# Placeholder for a period that must survive tokenization without ending a sentence.
_GUARD = "\x00"


def split_sentences(text: str) -> list[str]:
    """Split prose into sentences. Handle terminators . ! ? and common abbreviations
    (e.g., U.S., Sec., No., Mr., Dr., i.e., e.g., etc.) and decimal numbers (3.5) and
    section citations (Sec. 101) without splitting. Discard empty fragments."""
    text = text.strip()
    if not text:
        return []

    guarded = _ABBREV_RE.sub(lambda m: m.group(0)[:-1] + _GUARD, text)
    guarded = _DECIMAL_RE.sub(_GUARD, guarded)

    sentences = []
    for chunk in _SPLIT_RE.split(guarded):
        chunk = chunk.replace(_GUARD, ".").strip()
        if chunk:
            sentences.append(chunk)
    return sentences


def count_syllables(word: str) -> int:
    """Heuristic syllable count, minimum 1. Vowel-group counting with standard
    corrections: silent trailing 'e' (but not -le after a consonant, e.g. 'table'),
    'y' as vowel when not word-initial, common suffix adjustments (-es/-ed where silent),
    handle non-alpha input by stripping to letters first."""
    letters = _NON_ALPHA_RE.sub("", word).lower()
    if not letters:
        return 1

    is_vowel = [
        (ch in _VOWELS) or (ch == "y" and i > 0) for i, ch in enumerate(letters)
    ]
    count = 0
    for i, vowel in enumerate(is_vowel):
        if vowel and not (i > 0 and is_vowel[i - 1]):
            count += 1

    if letters.endswith("e"):
        # "table" keeps its vowel-group count; "whale" loses the silent e.
        if letters.endswith("le") and len(letters) > 2 and letters[-3] not in _VOWELS:
            pass
        else:
            count -= 1
    elif letters.endswith("ed") and not letters.endswith(("ted", "ded")):
        count -= 1  # "walked" (silent) vs. "wanted" (pronounced)
    elif letters.endswith("es") and not letters.endswith(
        ("ses", "zes", "ches", "shes", "xes")
    ):
        count -= 1  # "makes" (silent) vs. "boxes" (pronounced)

    return max(count, 1)


def fk_grade(text: str) -> float | None:
    """Flesch-Kincaid grade: 0.39*(words/sentences) + 11.8*(syllables/words) - 15.59.
    Words = whitespace tokens containing at least one letter or digit; numbers/acronyms
    count their spoken-ish syllables cheaply — a letterless token (pure digits/symbols,
    e.g. "3.5" or "42") is counted as 2 syllables rather than run through the
    letter-based counter. Round to 1 decimal. Return None for empty/whitespace-only
    text or zero sentences/words."""
    if not text or not text.strip():
        return None

    sentences = split_sentences(text)
    if not sentences:
        return None

    tokens = [tok for tok in text.split() if _ALNUM_RE.search(tok)]
    if not tokens:
        return None

    total_syllables = 0
    for tok in tokens:
        letters_only = _NON_ALPHA_RE.sub("", tok)
        total_syllables += count_syllables(letters_only) if letters_only else 2

    words = len(tokens)
    num_sentences = len(sentences)
    score = 0.39 * (words / num_sentences) + 11.8 * (total_syllables / words) - 15.59
    return round(score, 1)
