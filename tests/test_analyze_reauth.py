"""End-to-end shape test for analyze_reauth.py against a canned fixture tree (offline).

The fixture is a miniature corpus (4 bills, one per category; 2 candidates) with
hand-written verdicts that plant: one failing amendatory verdict (model fails
changes_to_existing_law on the reauthorization bill), one within-bill applicability
disagreement (effective_dates applicable for the model but not for crs_reference on the
same bill), and one CRS-baseline failure on the appropriations bill.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import analyze_reauth  # noqa: E402
import common as C  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures" / "reauth"
MINI = [("-mini", "mini", "Mini corpus")]


def run():
    return analyze_reauth.main_analysis(datasets=MINI, root=FIXTURES)


def test_categories_and_bill_audit_rows():
    out = run()
    ds = out["datasets"]["mini"]
    assert ds["n_bills"] == 4
    cats = {b["bill_id"]: b["category"] for b in ds["bills"]}
    assert cats == {
        "900-hjres-1": "cra_disapproval",
        "900-hr-2": "appropriations",
        "900-hr-3": "amendatory",
        "900-s-4": "standalone",
    }
    hr3 = next(b for b in ds["bills"] if b["bill_id"] == "900-hr-3")
    assert hr3["title_reauth"] is True and hr3["amend_count"] >= 2
    assert ds["groups"]["amendatory"]["title_reauth_bill_ids"] == ["900-hr-3"]


def test_group_counts_are_source_of_truth():
    out = run()
    groups = out["datasets"]["mini"]["groups"]
    amend = groups["amendatory"]["candidates"]
    assert amend["model__x"]["n_bills"] == 1
    assert amend["model__x"]["meets_standard_count"] == 0  # the planted failure
    assert amend["crs_reference"]["meets_standard_count"] == 1
    assert amend["model__x"]["per_criterion"]["changes_to_existing_law"] == {
        "n_applicable": 1, "n_passed": 0}
    # standalone: criterion not applicable, so it accrues no applicable counts
    stand = groups["standalone"]["candidates"]["model__x"]
    assert stand["per_criterion"]["changes_to_existing_law"] == {
        "n_applicable": 0, "n_passed": 0}
    assert out["criteria_ids"] == ["accurate", "changes_to_existing_law",
                                   "effective_dates", "concise"]


def test_reliability_metrics():
    out = run()
    rel = out["datasets"]["mini"]["reliability"]["per_category"]
    # judge harshness proxy: CRS gold summary failed on the appropriations bill
    assert rel["appropriations"]["crs_pass"] == {"count": 0, "n": 1}
    assert rel["amendatory"]["crs_pass"] == {"count": 1, "n": 1}
    # changes_to_existing_law applicable for both candidates on the amendatory bill
    assert rel["amendatory"]["changes_applicable"] == {"count": 2, "n": 2}
    assert rel["standalone"]["changes_applicable"] == {"count": 0, "n": 2}
    # the planted within-bill applicability disagreement on effective_dates
    assert rel["amendatory"]["applicability_disagreement"]["effective_dates"] == {
        "bills_with_disagreement": 1, "n_bills": 1}
    assert rel["amendatory"]["applicability_disagreement"]["changes_to_existing_law"] == {
        "bills_with_disagreement": 0, "n_bills": 1}


def test_failure_examples_verbatim():
    out = run()
    fx = out["failure_examples"]
    assert fx["n_total"] == 1  # only amendatory-bill failures are collected
    item = fx["items"][0]
    assert item["dataset"] == "mini"
    assert item["bill_id"] == "900-hr-3"
    assert item["candidate"] == "model/x"
    assert item["criterion"] == "changes_to_existing_law"
    assert item["why"] == ("Describes the striking mechanics but not the "
                           "substantive effect of the amendment.")


def test_pooled_and_json_roundtrip(tmp_path):
    out = run()
    assert out["pooled"]["n_bills"] == 4
    assert out["pooled"]["groups"]["amendatory"]["n_bills"] == 1
    assert "caveat" in out["pooled"]
    assert out["method"]["density_threshold_per_1k_chars"] == 0.5
    path = tmp_path / "reauth.json"
    C.write_json(path, out)
    assert C.read_json(path) == out
