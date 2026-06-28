"""Build per-batch judging packets for the Sonnet subagent judges (2024 dataset).

Each packet is a JSON file holding several bills; for each bill: the bill text, the CRS
summary, and every candidate summary (5 models + crs_reference) to be graded. Run with
CRS_DATASET set so paths resolve to the right corpus/results.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C

BATCHES = int(sys.argv[1]) if len(sys.argv) > 1 else 10
OUT_DIR = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("/tmp/crs_judge_packets")
TEXT_EXCERPT = 200000  # give judges the FULL bill text (this set's bills are all <180k)


def main() -> None:
    cfg = C.load_config()
    models = [C.model_slug(m) for m in cfg["models"]]
    candidates = models + [C.CRS_REFERENCE]
    bills = [C.read_json(p) for p in C.list_bill_files()]
    bills.sort(key=lambda b: (b["type"], int(b["number"])))

    items = []
    for b in bills:
        cands = {}
        for slug in candidates:
            if slug == C.CRS_REFERENCE:
                cands[slug] = b["crs_summary"]
            else:
                sp = C.SUMMARIES_DIR / slug / f"{b['bill_id']}.json"
                if sp.exists():
                    cands[slug] = C.read_json(sp).get("summary", "")
        items.append({
            "bill_id": b["bill_id"],
            "title": b["title"],
            "bill_text": b["bill_text"][:TEXT_EXCERPT],
            "crs_summary": b["crs_summary"],
            "candidates": cands,
        })

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # round-robin into BATCHES so each is a similar size
    batches: list[list] = [[] for _ in range(BATCHES)]
    for i, it in enumerate(items):
        batches[i % BATCHES].append(it)
    for i, batch in enumerate(batches):
        if batch:
            C.write_json(OUT_DIR / f"packet_{i:02d}.json", batch)
    nonempty = [i for i, b in enumerate(batches) if b]
    print(f"wrote {len(nonempty)} packets to {OUT_DIR} ({len(items)} bills, "
          f"{len(candidates)} candidates each)")
    print("packet sizes:", [len(b) for b in batches if b])


if __name__ == "__main__":
    main()
