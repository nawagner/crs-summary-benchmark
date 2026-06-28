"""Write CRS-baseline score files from verdicts produced by the Claude Code Opus judge.

Used to finish the CRS-reference judgments after the OpenRouter account ran out of credits.
Same model (Opus 4.8) and same web-verified-context method, just a different access path —
disclosed in the methodology. Input JSON shape:
  { "<bill_id>": { "<criterion_id>": {"applicable": bool, "pass": bool, "why": "..."}, ... }, ... }
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C

JUDGED_VIA = "claude-code Opus 4.8 (web-verified); OpenRouter credits exhausted"


def main(path: str) -> None:
    criteria_ids = [c["id"] for c in C.load_criteria()]
    verdicts_by_bill = json.load(open(path))
    written = 0
    for bill_id, verdicts in verdicts_by_bill.items():
        clean = {}
        for cid in criteria_ids:
            v = verdicts.get(cid, {})
            applicable = bool(v.get("applicable", True))
            passed = bool(v.get("pass", False)) or not applicable
            clean[cid] = {"applicable": applicable, "pass": passed, "why": str(v.get("why", ""))}
        n_appl = sum(1 for v in clean.values() if v["applicable"])
        n_pass = sum(1 for v in clean.values() if v["pass"] and v["applicable"])
        rec = {
            "bill_id": bill_id, "candidate": C.CRS_REFERENCE, "verdicts": clean,
            "n_applicable": n_appl, "n_passed": n_pass,
            "meets_standard": all(v["pass"] for v in clean.values()),
            "judge_cost_usd": 0.0, "judge_latency_s": 0.0, "judged_via": JUDGED_VIA,
        }
        C.write_json(C.SCORES_DIR / C.CRS_REFERENCE / f"{bill_id}.json", rec)
        written += 1
    print(f"wrote {written} CRS verdict files")


if __name__ == "__main__":
    main(sys.argv[1])
