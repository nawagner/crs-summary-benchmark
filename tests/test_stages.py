"""Tests for src/stages.py: bill-progress classification from action records."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import stages as S  # noqa: E402

FLOOR_TEXTS = [
    "Passed/agreed to in House: On motion to suspend the rules and pass the bill "
    "Agreed to by the Yeas and Nays: (2/3 required): 402 - 7 (Roll no. 25).",
    "Passed Senate with an amendment by Unanimous Consent.",
    "Considered under suspension of the rules. (consideration: CR H2289-2292)",
    "On passage Passed by the Yeas and Nays: 217 - 215 (Roll no. 49).",
    "Motion to proceed to consideration of measure agreed to in Senate by Yea-Nay "
    "Vote. 68 - 32. Record Vote Number: 12.",
    "Cloture on the motion to proceed to the measure invoked in Senate by Yea-Nay "
    "Vote. 60 - 40.",
    "Became Public Law No: 118-42.",
    "Signed by President.",
    "Presented to President.",
    "Received in the Senate and Read twice and referred to the Committee on Finance.",
    "Conference report H. Rept. 118-301 filed.",
    "Pursuant to the provisions of H. Res. 966, H.R. 2882 is considered passed House.",
    "Motion to reconsider laid on the table Agreed to without objection.",
]

COMMITTEE_TEXTS = [
    "Ordered to be Reported (Amended) by the Yeas and Nays: 32 - 26.",
    "Reported by the Committee on Armed Services. H. Rept. 118-125.",
    "Committee Consideration and Mark-up Session Held.",
    "Subcommittee Hearings Held.",
    "Placed on the Union Calendar, Calendar No. 297.",
    "Placed on Senate Legislative Calendar under General Orders. Calendar No. 43.",
    "Committee on Homeland Security and Governmental Affairs. Reported by Senator "
    "Peters with an amendment. With written report No. 118-97.",
    "Committee on Finance. Committee discharged by Unanimous Consent.",
    "Forwarded by Subcommittee to Full Committee by voice vote.",
]

INTRODUCED_TEXTS = [
    "Introduced in House",
    "Referred to the House Committee on the Judiciary.",
    "Referred to the Subcommittee on Crime and Federal Government Surveillance.",
    "Read twice and referred to the Committee on Finance.",
    "Sponsor introductory remarks on measure. (CR S1234)",
    "",
]


@pytest.mark.parametrize("text", FLOOR_TEXTS)
def test_classify_action_text_floor(text: str) -> None:
    assert S.classify_action_text(text) == S.STAGE_FLOOR


@pytest.mark.parametrize("text", COMMITTEE_TEXTS)
def test_classify_action_text_committee(text: str) -> None:
    assert S.classify_action_text(text) == S.STAGE_COMMITTEE


@pytest.mark.parametrize("text", INTRODUCED_TEXTS)
def test_classify_action_text_introduced(text: str) -> None:
    assert S.classify_action_text(text) == S.STAGE_INTRODUCED


def test_classify_action_text_floor_via_proceeded_with_debate() -> None:
    text = "The House proceeded with one hour of debate on H.R. 82."
    assert S.classify_action_text(text) == S.STAGE_FLOOR


# --------------------------------------------------------------- classify_action

def test_classify_action_text_and_type_agree_floor() -> None:
    action = {
        "text": "The House proceeded with one hour of debate on H.R. 82.",
        "type": "Floor",
    }
    assert S.classify_action(action) == S.STAGE_FLOOR


def test_classify_action_uses_higher_of_text_and_type_type_wins() -> None:
    # Text alone reads as mere referral (introduced), but the type field says floor.
    action = {"text": "Referred to the Committee on Finance.", "type": "Floor"}
    assert S.classify_action(action) == S.STAGE_FLOOR


def test_classify_action_uses_higher_of_text_and_type_text_wins() -> None:
    # Type field is a lower/unknown signal, but the text clearly signals floor passage.
    action = {"text": "On passage Passed by the Yeas and Nays: 217 - 215.", "type": "IntroReferral"}
    assert S.classify_action(action) == S.STAGE_FLOOR


def test_classify_action_committee_type() -> None:
    action = {"text": "Some routine notation.", "type": "Committee"}
    assert S.classify_action(action) == S.STAGE_COMMITTEE


def test_classify_action_unknown_type_falls_back_to_text() -> None:
    action = {"text": "Subcommittee Hearings Held.", "type": "SomethingUnrecognized"}
    assert S.classify_action(action) == S.STAGE_COMMITTEE


def test_classify_action_missing_keys_default_introduced() -> None:
    assert S.classify_action({}) == S.STAGE_INTRODUCED


def test_classify_action_ignores_extra_keys() -> None:
    action = {
        "text": "Ordered to be Reported (Amended) by the Yeas and Nays: 32 - 26.",
        "type": "Committee",
        "actionCode": "H37300",
        "actionDate": "2025-03-01",
    }
    assert S.classify_action(action) == S.STAGE_COMMITTEE


# ---------------------------------------------------------------- classify_stage

def test_classify_stage_empty_list_is_introduced() -> None:
    assert S.classify_stage([]) == S.STAGE_INTRODUCED


def test_classify_stage_only_referrals_is_introduced() -> None:
    actions = [
        {"text": "Introduced in House", "type": "IntroReferral"},
        {"text": "Referred to the House Committee on the Judiciary.", "type": "IntroReferral"},
    ]
    assert S.classify_stage(actions) == S.STAGE_INTRODUCED


def test_classify_stage_referral_plus_hearing_is_committee() -> None:
    actions = [
        {"text": "Introduced in House", "type": "IntroReferral"},
        {"text": "Referred to the House Committee on the Judiciary.", "type": "IntroReferral"},
        {"text": "Subcommittee Hearings Held.", "type": "Committee"},
    ]
    assert S.classify_stage(actions) == S.STAGE_COMMITTEE


def test_classify_stage_referral_hearing_and_passage_is_floor() -> None:
    actions = [
        {"text": "Introduced in House", "type": "IntroReferral"},
        {"text": "Referred to the House Committee on the Judiciary.", "type": "IntroReferral"},
        {"text": "Subcommittee Hearings Held.", "type": "Committee"},
        {"text": "On passage Passed by the Yeas and Nays: 217 - 215 (Roll no. 49).", "type": "Floor"},
    ]
    assert S.classify_stage(actions) == S.STAGE_FLOOR


def test_classify_stage_highest_wins_regardless_of_order() -> None:
    # Floor action appears first, then later (weaker) actions shouldn't downgrade it.
    actions = [
        {"text": "On passage Passed by the Yeas and Nays: 217 - 215 (Roll no. 49).", "type": "Floor"},
        {"text": "Held at the desk.", "type": "IntroReferral"},
    ]
    assert S.classify_stage(actions) == S.STAGE_FLOOR


# ------------------------------------------------------------------------- misc

def test_stage_order_is_monotonic() -> None:
    assert S.STAGE_ORDER[S.STAGE_INTRODUCED] < S.STAGE_ORDER[S.STAGE_COMMITTEE] < S.STAGE_ORDER[S.STAGE_FLOOR]
