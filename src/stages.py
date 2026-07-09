"""Classify how far a bill advanced (introduced/committee/floor) from action text.

Used by analyze_lag.py to measure CRS summary coverage of bills that "moved" through
committee or floor action vs. all introduced bills. Classification is a pure,
pattern-table-driven lookup over Congress.gov `/actions` records — no network calls.
"""
from __future__ import annotations

import re

STAGE_INTRODUCED = "introduced"
STAGE_COMMITTEE = "committee"
STAGE_FLOOR = "floor"
STAGE_ORDER = {STAGE_INTRODUCED: 0, STAGE_COMMITTEE: 1, STAGE_FLOOR: 2}

# Ordered (compiled case-insensitive regex, stage) pairs — first match wins. Floor
# (specific) comes before committee so chamber-passage actions that happen to mention
# "referred to" (e.g. "Received in the Senate and ... referred to the Committee on
# Finance") are still classified as floor. The committee block comes next so markup /
# reporting language isn't swallowed by the generic "agreed to" floor catch-all, which
# is deliberately last among the floor patterns.
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # --- floor (specific) ---
    (re.compile(r"became public law", re.I), STAGE_FLOOR),
    (re.compile(r"signed by president", re.I), STAGE_FLOOR),
    (re.compile(r"presented to president", re.I), STAGE_FLOOR),
    (re.compile(r"vetoed", re.I), STAGE_FLOOR),
    (re.compile(r"passed veto override", re.I), STAGE_FLOOR),
    (re.compile(r"\bpassed\b", re.I), STAGE_FLOOR),
    (re.compile(r"on passage", re.I), STAGE_FLOOR),
    (re.compile(r"agreed to in (the )?(house|senate)", re.I), STAGE_FLOOR),
    (re.compile(r"considered under suspension of the rules", re.I), STAGE_FLOOR),
    (re.compile(r"motion to proceed", re.I), STAGE_FLOOR),
    (re.compile(r"cloture", re.I), STAGE_FLOOR),
    (re.compile(r"conference report", re.I), STAGE_FLOOR),
    (re.compile(r"resolving differences", re.I), STAGE_FLOOR),
    (re.compile(r"received in the (house|senate)", re.I), STAGE_FLOOR),
    (re.compile(r"considered (as )?passed", re.I), STAGE_FLOOR),
    (re.compile(r"proceeded with .* debate", re.I), STAGE_FLOOR),
    (re.compile(r"on agreeing to", re.I), STAGE_FLOOR),
    # --- committee (specific) ---
    (re.compile(r"ordered to be reported", re.I), STAGE_COMMITTEE),
    (re.compile(r"reported (by|to)", re.I), STAGE_COMMITTEE),
    (re.compile(r"committee consideration and mark-?up", re.I), STAGE_COMMITTEE),
    (re.compile(r"markup", re.I), STAGE_COMMITTEE),
    (re.compile(r"hearings held", re.I), STAGE_COMMITTEE),
    (re.compile(r"placed on .{0,40}calendar", re.I), STAGE_COMMITTEE),
    (re.compile(r"forwarded by subcommittee", re.I), STAGE_COMMITTEE),
    (re.compile(r"committee discharged", re.I), STAGE_COMMITTEE),
    (re.compile(r"discharge petition", re.I), STAGE_COMMITTEE),
    # --- floor (generic, after committee-specific so markup votes don't leak) ---
    (re.compile(r"\bagreed to\b", re.I), STAGE_FLOOR),
]

# Congress.gov `/actions` `type` field -> stage.
_TYPE_STAGE: dict[str, str] = {
    "Floor": STAGE_FLOOR,
    "Vote": STAGE_FLOOR,
    "President": STAGE_FLOOR,
    "BecameLaw": STAGE_FLOOR,
    "ResolvingDifferences": STAGE_FLOOR,
    "Veto": STAGE_FLOOR,
    "Calendars": STAGE_COMMITTEE,
    "Committee": STAGE_COMMITTEE,
    "Discharge": STAGE_COMMITTEE,
    "IntroReferral": STAGE_INTRODUCED,
}


def classify_action_text(text: str) -> str:
    """Stage implied by a single action's display text alone."""
    for pattern, stage in _PATTERNS:
        if pattern.search(text or ""):
            return stage
    return STAGE_INTRODUCED


def classify_action(action: dict) -> str:
    """Stage implied by one action record from the Congress.gov /actions endpoint.

    Combines the record's `type` field (if present) with the text; returns the higher
    of the two signals per STAGE_ORDER. Action dicts look like
    {"text": "...", "type": "Floor", "actionCode": "H37300", ...}; all keys optional.
    """
    text_stage = classify_action_text(action.get("text", ""))
    type_stage = _TYPE_STAGE.get(action.get("type", ""), STAGE_INTRODUCED)
    return max(text_stage, type_stage, key=STAGE_ORDER.__getitem__)


def classify_stage(actions: list[dict]) -> str:
    """Highest stage attained across a bill's full action list. Empty list -> introduced."""
    stage = STAGE_INTRODUCED
    for action in actions:
        stage = max(stage, classify_action(action), key=STAGE_ORDER.__getitem__)
        if stage == STAGE_FLOOR:
            break
    return stage
