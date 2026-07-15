"""Slice existing benchmark scores by bill category (issue #14) → docs/data/reauth.json.

Answers three questions about reauthorization/amendatory bills — do they score
differently, what are their failure modes, and is the judge less reliable on them —
entirely offline: every bill × candidate × criterion verdict is already committed in
docs/data/results*.json, and src/reauth.py classifies bills from their stored title and
text. No API keys, no model calls.

Judge-reliability numbers are consistency/harshness proxies, not accuracy measures:
- crs_pass: how often the judge passes the human CRS gold summary, per category;
- changes_applicable: how often `changes_to_existing_law` is marked applicable — should
  approach 1.0 on amendatory bills, so classifier and judge cross-validate;
- applicability_disagreement: bills where the judge marked a conditional criterion
  applicable for some candidates but not others on the SAME bill.

Usage: python src/analyze_reauth.py [--out PATH]
"""
from __future__ import annotations

import argparse
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402
import reauth  # noqa: E402

# (path suffix, dataset id, label) — mirrors the suffix machinery in common.py, but
# built explicitly because this script spans all datasets in one run while common.py
# freezes its paths from CRS_DATASET at import time.
DATASETS: list[tuple[str, str, str]] = [
    ("", "119", "119th Congress"),
    ("-2024", "2024", "2024 · high-activity"),
    ("-priority", "priority", "118th · appropriations & NDAA"),
]

CONDITIONAL_IDS = ("changes_to_existing_law", "exceptions_conditions", "effective_dates")
MAX_FAILURE_EXAMPLES = 60


def classify_dataset(bills_dir: Path) -> dict[str, dict]:
    """{bill_id: classification} for every stored bill in one corpus."""
    out = {}
    for path in sorted(bills_dir.glob("*.json")):
        bill = C.read_json(path)
        out[bill["bill_id"]] = reauth.classify_bill(bill)
    return out


def group_stats(bills: list[dict], criteria_ids: list[str]) -> dict:
    """Aggregate one category's bills: per-candidate counts (counts are the source of
    truth — n is small — rates are derived by the consumer)."""
    candidates: dict[str, dict] = {}
    for bill in bills:
        for cand, entry in bill["candidates"].items():
            if not entry.get("verdicts"):
                continue  # unscored reference cards (issue #8) are not judged
            agg = candidates.setdefault(cand, {
                "label": entry.get("label", cand),
                "is_human": bool(entry.get("is_human")),
                "n_bills": 0,
                "meets_standard_count": 0,
                "per_criterion": {cid: {"n_applicable": 0, "n_passed": 0}
                                  for cid in criteria_ids},
            })
            agg["n_bills"] += 1
            if entry.get("meets_standard"):
                agg["meets_standard_count"] += 1
            for cid, v in (entry.get("verdicts") or {}).items():
                if cid not in agg["per_criterion"] or not v.get("applicable"):
                    continue
                agg["per_criterion"][cid]["n_applicable"] += 1
                if v.get("pass"):
                    agg["per_criterion"][cid]["n_passed"] += 1
    return candidates


def reliability_stats(bills: list[dict]) -> dict:
    """Judge consistency/harshness proxies for one category's bills."""
    crs_pass = [0, 0]  # [passed, n]
    changes_applicable = [0, 0]  # [applicable, n verdicts]
    disagreement = {cid: [0, 0] for cid in CONDITIONAL_IDS}  # [bills disagreeing, n]
    for bill in bills:
        cands = bill["candidates"]
        crs = cands.get(C.CRS_REFERENCE)
        if crs is not None:
            crs_pass[1] += 1
            if crs.get("meets_standard"):
                crs_pass[0] += 1
        for cid in CONDITIONAL_IDS:
            flags = [bool((e.get("verdicts") or {}).get(cid, {}).get("applicable"))
                     for e in cands.values() if (e.get("verdicts") or {}).get(cid)]
            if not flags:
                continue
            disagreement[cid][1] += 1
            if len(set(flags)) > 1:
                disagreement[cid][0] += 1
            if cid == "changes_to_existing_law":
                changes_applicable[0] += sum(flags)
                changes_applicable[1] += len(flags)
    return {
        "crs_pass": {"count": crs_pass[0], "n": crs_pass[1]},
        "changes_applicable": {"count": changes_applicable[0],
                               "n": changes_applicable[1]},
        "applicability_disagreement": {
            cid: {"bills_with_disagreement": d, "n_bills": n}
            for cid, (d, n) in disagreement.items()},
    }


def failure_examples(dataset_id: str, bills: list[dict], labels: dict[str, dict]) -> list[dict]:
    """Every failing applicable verdict on this dataset's amendatory bills, with the
    judge's verbatim one-line reason — the qualitative failure-mode evidence."""
    out = []
    for bill in bills:
        cls = labels[bill["bill_id"]]
        if cls["category"] != reauth.CAT_AMENDATORY:
            continue
        for cand, entry in bill["candidates"].items():
            for cid, v in (entry.get("verdicts") or {}).items():
                if v.get("applicable") and not v.get("pass"):
                    out.append({
                        "dataset": dataset_id,
                        "bill_id": bill["bill_id"],
                        "title": bill.get("title", ""),
                        "title_reauth": cls["title_reauth"],
                        "candidate": entry.get("label", cand),
                        "is_human": bool(entry.get("is_human")),
                        "criterion": cid,
                        "why": v.get("why", ""),
                    })
    return out


def main_analysis(datasets: list[tuple[str, str, str]] = DATASETS,
                  root: Path | None = None) -> dict:
    root = root or C.ROOT  # injectable so tests run against a fixture tree
    criteria_ids: list[str] = []
    result_datasets: dict[str, dict] = {}
    pooled_by_cat: dict[str, list[dict]] = defaultdict(list)
    failures: list[dict] = []
    pooled_n = 0

    for sfx, ds_id, label in datasets:
        bills_dir = root / "data" / f"bills{sfx}"
        results_path = root / "docs" / "data" / f"results{sfx}.json"
        if not bills_dir.is_dir() or not results_path.exists():
            print(f"skipping {ds_id}: missing {bills_dir} or {results_path}")
            continue
        results = C.read_json(results_path)
        if not criteria_ids:
            criteria_ids = [c["id"] for c in results["criteria"]]
        labels = classify_dataset(bills_dir)

        by_cat: dict[str, list[dict]] = defaultdict(list)
        bill_rows = []
        for bill in results["bills"]:
            cls = labels.get(bill["bill_id"])
            if cls is None:  # scored but source bill file missing — don't guess
                print(f"warning: {ds_id}/{bill['bill_id']} has no stored bill file; skipped")
                continue
            by_cat[cls["category"]].append(bill)
            pooled_by_cat[cls["category"]].append(bill)
            pooled_n += 1
            bill_rows.append({
                "bill_id": bill["bill_id"],
                "title": bill.get("title", ""),
                "category": cls["category"],
                "title_reauth": cls["title_reauth"],
                "amend_count": cls["amend_count"],
                "amend_density": cls["amend_density"],
                "bill_text_chars": bill.get("bill_text_chars"),
                "long_text": bool(bill.get("long_text")),
            })

        result_datasets[ds_id] = {
            "label": label,
            "judge_model": results.get("judge_model"),
            "n_bills": len(bill_rows),
            "bills": bill_rows,
            "groups": {
                cat: {
                    "n_bills": len(group),
                    "title_reauth_bill_ids": [
                        b["bill_id"] for b in group
                        if labels[b["bill_id"]]["title_reauth"]],
                    "candidates": group_stats(group, criteria_ids),
                }
                for cat, group in by_cat.items()
            },
            "reliability": {
                "per_category": {cat: reliability_stats(group)
                                 for cat, group in by_cat.items()},
            },
        }
        failures += failure_examples(ds_id, results["bills"], labels)
        counts = {cat: len(g) for cat, g in by_cat.items()}
        print(f"{ds_id}: {len(bill_rows)} bills — " +
              ", ".join(f"{c}={n}" for c, n in sorted(counts.items())))

    failures.sort(key=lambda f: (f["criterion"], f["bill_id"], f["candidate"]))
    return {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        "method": {
            "amend_patterns": [p.pattern for p in reauth.AMEND_PATTERNS],
            "density_threshold_per_1k_chars": reauth.DENSITY_THRESHOLD,
            "title_reauth_pattern": reauth.TITLE_REAUTH_RE.pattern,
            "title_approps_pattern": reauth.TITLE_APPROPS_RE.pattern,
            "title_cra_pattern": reauth.TITLE_CRA_RE.pattern,
            "category_order": reauth.CATEGORY_ORDER,
            "notes": ("counts are the source of truth — per-category n is small; "
                      "reliability numbers are consistency/harshness proxies, "
                      "not judge-accuracy measures"),
        },
        "criteria_ids": criteria_ids,
        "datasets": result_datasets,
        "pooled": {
            "caveat": ("pooled across datasets with different judging pipelines "
                       "(API judge with web search vs. Claude Code subagents; full "
                       "text vs. sectional grounding) and a saturated 119th set — "
                       "indicative only; per-dataset blocks are primary"),
            "n_bills": pooled_n,
            "groups": {
                cat: {"n_bills": len(group),
                      "candidates": group_stats(group, criteria_ids)}
                for cat, group in pooled_by_cat.items()
            },
        },
        "failure_examples": {
            "n_total": len(failures),
            "items": failures[:MAX_FAILURE_EXAMPLES],
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Slice benchmark scores by bill category (reauth/amendatory).")
    ap.add_argument("--out", type=Path, default=C.DOCS_DATA / "reauth.json")
    args = ap.parse_args()
    out = main_analysis()
    C.write_json(args.out, out)
    print(f"wrote {args.out} ({out['failure_examples']['n_total']} failure examples)")


if __name__ == "__main__":
    main()
