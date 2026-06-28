"""Aggregate scores into a leaderboard, a CSV, and the website's results.json.

Reports per candidate (5 models + the CRS human baseline): % of summaries meeting ALL
applicable criteria (headline), per-criterion pass rate, and mean cost + latency.
"""
from __future__ import annotations

import csv
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

HUMAN_LABEL = "CRS (human)"


def label_for(cand_id: str, model_id: str | None) -> str:
    if cand_id == C.CRS_REFERENCE:
        return HUMAN_LABEL
    return model_id or cand_id


def gen_meta(slug: str, bill_id: str) -> dict:
    """Cost/latency for a generated summary (empty for the human baseline)."""
    path = C.SUMMARIES_DIR / slug / f"{bill_id}.json"
    if not path.exists():
        return {}
    rec = C.read_json(path)
    return {"cost_usd": rec.get("cost_usd"), "latency_s": rec.get("latency_s"),
            "summary": rec.get("summary", "")}


def main() -> None:
    cfg = C.load_config()
    criteria = C.load_criteria()
    criteria_ids = [c["id"] for c in criteria]
    model_ids = cfg["models"]
    candidates = [(C.model_slug(m), m) for m in model_ids] + [(C.CRS_REFERENCE, None)]

    bills = [C.read_json(p) for p in C.list_bill_files()]
    bills.sort(key=lambda b: (b["type"], int(b["number"])))

    # ---- aggregate per candidate ----
    agg = {}
    for slug, model_id in candidates:
        per_crit_pass = {cid: 0 for cid in criteria_ids}
        per_crit_appl = {cid: 0 for cid in criteria_ids}
        meets = n = 0
        costs, lats = [], []
        for bill in bills:
            sp = C.SCORES_DIR / slug / f"{bill['bill_id']}.json"
            if not sp.exists():
                continue
            score = C.read_json(sp)
            n += 1
            meets += 1 if score["meets_standard"] else 0
            for cid, v in score["verdicts"].items():
                if v["applicable"]:
                    per_crit_appl[cid] += 1
                    if v["pass"]:
                        per_crit_pass[cid] += 1
            meta = gen_meta(slug, bill["bill_id"])
            if meta.get("cost_usd") is not None:
                costs.append(meta["cost_usd"])
            if meta.get("latency_s") is not None:
                lats.append(meta["latency_s"])
        agg[slug] = {
            "id": slug,
            "model": model_id,
            "label": label_for(slug, model_id),
            "is_human": slug == C.CRS_REFERENCE,
            "n_bills": n,
            "meets_standard_rate": (meets / n) if n else 0.0,
            "meets_standard_count": meets,
            "per_criterion": {cid: (per_crit_pass[cid] / per_crit_appl[cid] if per_crit_appl[cid] else None)
                              for cid in criteria_ids},
            "mean_cost_usd": (sum(costs) / len(costs)) if costs else None,
            "mean_latency_s": (sum(lats) / len(lats)) if lats else None,
            "total_cost_usd": sum(costs) if costs else None,
        }

    ordered = sorted(agg.values(), key=lambda a: (a["is_human"], -a["meets_standard_rate"]))

    # ---- leaderboard.md ----
    C.RESULTS.mkdir(parents=True, exist_ok=True)
    judged_n = max((a["n_bills"] for a in ordered), default=0)
    lines = ["# Leaderboard", "",
             f"Bills judged: {judged_n}  |  Judge: `{cfg['judge_model']}`", "",
             "| Summarizer | Passes all criteria | Mean cost/summary | Mean latency |",
             "|---|---|---|---|"]
    for a in ordered:
        cost = f"${a['mean_cost_usd']:.4f}" if a["mean_cost_usd"] is not None else "—"
        lat = f"{a['mean_latency_s']:.1f}s" if a["mean_latency_s"] is not None else "—"
        lines.append(f"| {a['label']} | {a['meets_standard_rate']*100:.0f}% "
                     f"({a['meets_standard_count']}/{a['n_bills']}) | {cost} | {lat} |")
    lines += ["", "## Per-criterion pass rate", "",
              "| Summarizer | " + " | ".join(c["name"] for c in criteria) + " |",
              "|---|" + "---|" * len(criteria)]
    for a in ordered:
        cells = []
        for cid in criteria_ids:
            r = a["per_criterion"][cid]
            cells.append(f"{r*100:.0f}%" if r is not None else "—")
        lines.append(f"| {a['label']} | " + " | ".join(cells) + " |")
    (C.RESULTS / "leaderboard.md").write_text("\n".join(lines) + "\n")

    # ---- scores.csv ----
    with open(C.RESULTS / "scores.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["candidate", "meets_standard_rate", "meets_standard_count", "n_bills",
                    "mean_cost_usd", "mean_latency_s"] + criteria_ids)
        for a in ordered:
            w.writerow([a["label"], f"{a['meets_standard_rate']:.4f}", a["meets_standard_count"],
                        a["n_bills"], a["mean_cost_usd"], a["mean_latency_s"]]
                       + [a["per_criterion"][cid] for cid in criteria_ids])

    # ---- docs/data/results.json (self-contained for the website) ----
    bills_out = []
    for bill in bills:
        cand_out = {}
        for slug, model_id in candidates:
            sp = C.SCORES_DIR / slug / f"{bill['bill_id']}.json"
            if not sp.exists():
                continue
            score = C.read_json(sp)
            meta = gen_meta(slug, bill["bill_id"])
            summary = bill["crs_summary"] if slug == C.CRS_REFERENCE else meta.get("summary", "")
            cand_out[slug] = {
                "label": label_for(slug, model_id),
                "is_human": slug == C.CRS_REFERENCE,
                "summary": summary,
                "meets_standard": score["meets_standard"],
                "n_passed": score["n_passed"],
                "n_applicable": score["n_applicable"],
                "verdicts": score["verdicts"],
                "cost_usd": meta.get("cost_usd"),
                "latency_s": meta.get("latency_s"),
            }
        bills_out.append({
            "bill_id": bill["bill_id"],
            "congress": bill["congress"],
            "type": bill["type"],
            "number": bill["number"],
            "title": bill["title"],
            "congress_gov_url": bill["congress_gov_url"],
            "crs_version_code": bill.get("crs_version_code", ""),
            "bill_text_chars": bill.get("bill_text_chars"),
            "text_truncated": bill.get("text_truncated", False),
            "candidates": cand_out,
        })

    results = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        "judge_model": cfg["judge_model"],
        "congress": cfg["congress"],
        "model_ids": model_ids,
        "criteria": [{"id": c["id"], "name": c["name"], "description": c["description"],
                      "applicability": c.get("applicability", "always")} for c in criteria],
        "leaderboard": ordered,
        "bills": bills_out,
        "prompts": {
            "summarize": C.read_prompt(cfg["prompts"]["summarize"]),
            "judge": C.read_prompt(cfg["prompts"]["judge"]),
        },
    }
    C.write_json(C.DOCS_DATA / "results.json", results)

    print("Wrote results/leaderboard.md, results/scores.csv, docs/data/results.json")
    print()
    print((C.RESULTS / "leaderboard.md").read_text())


if __name__ == "__main__":
    main()
