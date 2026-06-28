"""Aggregate Sonnet-subagent verdict files into score files (2024 dataset).

Reads every *.json in a directory; each file is a list of
  {"bill_id": ..., "candidate": <model_slug|crs_reference>, "verdicts": {cid: {applicable,pass,why}}}.
Writes one score file per (candidate, bill) with derived fields. Run with CRS_DATASET set.
"""
from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C

JUDGED_VIA = "claude-sonnet-4-6 via parallel Claude Code subagents (web/knowledge-verified context)"


def main(indir: str) -> None:
    criteria_ids = [c["id"] for c in C.load_criteria()]
    seen = 0
    for f in sorted(glob.glob(f"{indir}/*.json")):
        try:
            entries = json.load(open(f))
        except json.JSONDecodeError:
            print(f"  WARN: could not parse {f}")
            continue
        for e in entries:
            v_in = e.get("verdicts", {})
            clean = {}
            for cid in criteria_ids:
                v = v_in.get(cid, {})
                applicable = bool(v.get("applicable", True))
                passed = bool(v.get("pass", False)) or not applicable
                clean[cid] = {"applicable": applicable, "pass": passed, "why": str(v.get("why", ""))}
            n_appl = sum(1 for v in clean.values() if v["applicable"])
            n_pass = sum(1 for v in clean.values() if v["pass"] and v["applicable"])
            rec = {
                "bill_id": e["bill_id"], "candidate": e["candidate"], "verdicts": clean,
                "n_applicable": n_appl, "n_passed": n_pass,
                "meets_standard": all(v["pass"] for v in clean.values()),
                "judge_cost_usd": 0.0, "judge_latency_s": 0.0, "judged_via": JUDGED_VIA,
            }
            C.write_json(C.SCORES_DIR / e["candidate"] / f"{e['bill_id']}.json", rec)
            seen += 1
    print(f"wrote {seen} score files into {C.SCORES_DIR}")


if __name__ == "__main__":
    main(sys.argv[1])
