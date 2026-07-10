"""Tests for src/build_perspectives.py: perspectives.yaml validation + compilation
(issue #9). `validate()` is exercised directly against fixture dicts; `main()` is
exercised end-to-end against a tmp yaml + tmp output path."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import build_perspectives as BP  # noqa: E402
import common as C  # noqa: E402

BILL_ID = "118-hr-2670"


def _entry(**over):
    e = {
        "name": "Rep. Mike Rogers (R-AL)",
        "stance": "sponsor",
        "excerpt": "This bill strengthens our national defense.",
        "source_url": "https://rogers.house.gov/press-release",
        "source_kind": "press_release",
        "date": "2023-06-12",
    }
    e.update(over)
    return e


def _valid_data():
    return {
        "bills": {
            BILL_ID: {
                "sponsor": [_entry()],
                "opponents": [_entry(
                    name="Rep. Jane Opponent (D-CA)",
                    stance="opponent",
                    excerpt="This bill guts oversight.",
                    source_url="https://opponent.house.gov/statement",
                    source_kind="floor_statement",
                    date="2023-06-14",
                )],
            }
        }
    }


# --------------------------------------------------------------------------- valid


def test_valid_single_bill_no_errors():
    assert BP.validate(_valid_data()) == []


def test_empty_bills_is_valid():
    assert BP.validate({"bills": {}}) == []


# ----------------------------------------------------------------------- bad bill id


def test_bad_bill_id_key():
    data = {"bills": {"not-a-valid-id": {"sponsor": [_entry()]}}}
    errors = BP.validate(data)
    assert any("bills.not-a-valid-id" in e and "does not match" in e for e in errors)


# ------------------------------------------------------------------- missing field


def test_missing_required_field():
    entry = _entry()
    del entry["source_url"]
    data = {"bills": {BILL_ID: {"sponsor": [entry]}}}
    errors = BP.validate(data)
    assert any(f"bills.{BILL_ID}.sponsor[0].source_url" in e for e in errors)


# ------------------------------------------------------------------------- enums


def test_bad_stance_value():
    data = {"bills": {BILL_ID: {"sponsor": [_entry(stance="villain")]}}}
    errors = BP.validate(data)
    assert any(f"bills.{BILL_ID}.sponsor[0].stance" in e for e in errors)


def test_bad_source_kind_value():
    data = {"bills": {BILL_ID: {"sponsor": [_entry(source_kind="tweet")]}}}
    errors = BP.validate(data)
    assert any(f"bills.{BILL_ID}.sponsor[0].source_kind" in e for e in errors)


# ------------------------------------------------------------- stance-group mismatch


def test_opponent_stance_under_sponsor_group_is_invalid():
    data = {"bills": {BILL_ID: {"sponsor": [_entry(stance="opponent")]}}}
    errors = BP.validate(data)
    assert any(
        f"bills.{BILL_ID}.sponsor[0].stance" in e and "not allowed here" in e for e in errors
    )


def test_sponsor_stance_under_opponents_group_is_invalid():
    data = {"bills": {BILL_ID: {"opponents": [_entry(stance="sponsor")]}}}
    errors = BP.validate(data)
    assert any(
        f"bills.{BILL_ID}.opponents[0].stance" in e and "not allowed here" in e for e in errors
    )


# ------------------------------------------------------------------------ excerpt


def test_excerpt_over_600_chars_is_invalid():
    data = {"bills": {BILL_ID: {"sponsor": [_entry(excerpt="x" * 601)]}}}
    errors = BP.validate(data)
    assert any(f"bills.{BILL_ID}.sponsor[0].excerpt" in e for e in errors)


def test_excerpt_exactly_600_chars_is_valid():
    data = {"bills": {BILL_ID: {"sponsor": [_entry(excerpt="x" * 600)]}}}
    assert BP.validate(data) == []


# --------------------------------------------------------------------- source_url


def test_non_http_source_url_is_invalid():
    data = {"bills": {BILL_ID: {"sponsor": [_entry(source_url="ftp://example.com/doc")]}}}
    errors = BP.validate(data)
    assert any(f"bills.{BILL_ID}.sponsor[0].source_url" in e and "not http(s)" in e for e in errors)


def test_source_url_without_netloc_is_invalid():
    data = {"bills": {BILL_ID: {"sponsor": [_entry(source_url="https://")]}}}
    errors = BP.validate(data)
    assert any(f"bills.{BILL_ID}.sponsor[0].source_url" in e for e in errors)


# ------------------------------------------------------------------------ unknown key


def test_unknown_key_on_entry_is_invalid():
    data = {"bills": {BILL_ID: {"sponsor": [_entry(quote_id="q1")]}}}
    errors = BP.validate(data)
    assert any(f"bills.{BILL_ID}.sponsor[0].quote_id" in e and "unknown key" in e for e in errors)


def test_unknown_group_key_on_bill_is_invalid():
    data = {"bills": {BILL_ID: {"neutral": [_entry()]}}}
    errors = BP.validate(data)
    assert any(f"bills.{BILL_ID}.neutral" in e and "unknown key" in e for e in errors)


# --------------------------------------------------------------------------- date


def test_bad_date_format_is_invalid():
    data = {"bills": {BILL_ID: {"sponsor": [_entry(date="06/12/2023")]}}}
    errors = BP.validate(data)
    assert any(f"bills.{BILL_ID}.sponsor[0].date" in e for e in errors)


def test_missing_date_is_valid_since_optional():
    entry = _entry()
    del entry["date"]
    data = {"bills": {BILL_ID: {"sponsor": [entry]}}}
    assert BP.validate(data) == []


# ------------------------------------------------------------------- empty group list


def test_empty_sponsor_list_is_invalid():
    data = {"bills": {BILL_ID: {"sponsor": []}}}
    errors = BP.validate(data)
    assert any(f"bills.{BILL_ID}.sponsor" in e and "non-empty list" in e for e in errors)


def test_multiple_errors_all_collected():
    data = {
        "bills": {
            "bad id": {"sponsor": [_entry(stance="nope", excerpt="")]},
        }
    }
    errors = BP.validate(data)
    # bad bill id + bad stance + empty excerpt should all be reported, not just the first
    assert len(errors) >= 3


# ----------------------------------------------------------------------------- main


def test_main_end_to_end(tmp_path, monkeypatch):
    yaml_path = tmp_path / "perspectives.yaml"
    out_dir = tmp_path / "docs_data"
    yaml_path.write_text(
        "bills:\n"
        f'  "{BILL_ID}":\n'
        "    sponsor:\n"
        '      - name: "Rep. Mike Rogers (R-AL)"\n'
        "        stance: sponsor\n"
        '        excerpt: "This bill strengthens our national defense."\n'
        '        source_url: "https://rogers.house.gov/press-release"\n'
        "        source_kind: press_release\n"
        '        date: "2023-06-12"\n'
    )
    monkeypatch.setattr(C, "ROOT", tmp_path)
    monkeypatch.setattr(C, "DOCS_DATA", out_dir)

    BP.main()

    out_path = out_dir / "perspectives.json"
    assert out_path.exists()
    data = json.loads(out_path.read_text())
    assert set(data.keys()) == {"generated_at", "bills"}
    assert data["generated_at"] is not None
    assert data["generated_at"].endswith("UTC")
    assert BILL_ID in data["bills"]
    assert data["bills"][BILL_ID]["sponsor"][0]["name"] == "Rep. Mike Rogers (R-AL)"


def test_main_empty_bills_yields_generated_at_null(tmp_path, monkeypatch):
    yaml_path = tmp_path / "perspectives.yaml"
    out_dir = tmp_path / "docs_data"
    yaml_path.write_text("bills: {}\n")
    monkeypatch.setattr(C, "ROOT", tmp_path)
    monkeypatch.setattr(C, "DOCS_DATA", out_dir)

    BP.main()

    data = json.loads((out_dir / "perspectives.json").read_text())
    assert data == {"generated_at": None, "bills": {}}
