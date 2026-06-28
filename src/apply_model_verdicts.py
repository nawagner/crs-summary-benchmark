"""Write model-summary score files from verdicts produced by the Claude Code Opus judge.

Used to judge the re-generated (de-truncated) summaries after the OpenRouter run; same
model (Opus 4.8) and web-verified method, disclosed in the methodology. Input JSON is a
list of {model, bill_id, verdicts:{criterion_id:{applicable,pass,why}}}.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C

JUDGED_VIA = "claude-code Opus 4.8 (web-verified); re-judged after de-truncation re-run"


def main(path: str) -> None:
    criteria_ids = [c["id"] for c in C.load_criteria()]
    entries = json.load(open(path))
    written = 0
    for e in entries:
        model, bill_id, verdicts = e["model"], e["bill_id"], e["verdicts"]
        clean = {}
        for cid in criteria_ids:
            v = verdicts.get(cid, {})
            applicable = bool(v.get("applicable", True))
            passed = bool(v.get("pass", False)) or not applicable
            clean[cid] = {"applicable": applicable, "pass": passed, "why": str(v.get("why", ""))}
        n_appl = sum(1 for v in clean.values() if v["applicable"])
        n_pass = sum(1 for v in clean.values() if v["pass"] and v["applicable"])
        rec = {
            "bill_id": bill_id, "candidate": model, "verdicts": clean,
            "n_applicable": n_appl, "n_passed": n_pass,
            "meets_standard": all(v["pass"] for v in clean.values()),
            "judge_cost_usd": 0.0, "judge_latency_s": 0.0, "judged_via": JUDGED_VIA,
        }
        C.write_json(C.SCORES_DIR / model / f"{bill_id}.json", rec)
        written += 1
    print(f"wrote {written} model verdict files")


if __name__ == "__main__":
    main(sys.argv[1])
