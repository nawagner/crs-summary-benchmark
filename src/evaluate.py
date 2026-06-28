"""Judge every candidate summary against the binary criteria.

Candidates = the models under test PLUS `crs_reference` (the real CRS summary), so the
human baseline is graded on the identical rubric. The judge grounds on the BILL TEXT, so
the CRS summary is evaluated against the source rather than against itself. Resumable.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402
from run_models import load_pricing  # noqa: E402


def criteria_block(criteria: list[dict]) -> str:
    lines = []
    for c in criteria:
        kind = "always" if c.get("applicability") == "always" else "conditional"
        lines.append(f"- {c['id']} ({kind}): {c['description']}")
    return "\n".join(lines)


def parse_verdicts(raw: str, criteria_ids: list[str]) -> dict:
    """Extract the verdicts object from the judge's response, tolerating fences/extra text."""
    text = raw.strip()
    text = re.sub(r"^```(json)?|```$", "", text, flags=re.MULTILINE).strip()
    obj = None
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            obj = json.loads(m.group(0))
    if obj is None:
        raise ValueError("could not parse judge JSON")
    verdicts = obj.get("verdicts", obj)
    clean = {}
    for cid in criteria_ids:
        v = verdicts.get(cid, {}) if isinstance(verdicts, dict) else {}
        applicable = bool(v.get("applicable", True))
        passed = bool(v.get("pass", False)) or not applicable
        clean[cid] = {"applicable": applicable, "pass": passed, "why": str(v.get("why", ""))}
    return clean


def judge_one(client, judge_model: str, prompt: str, cfg: dict, pricing, criteria_ids) -> dict:
    t0 = time.time()
    kwargs = dict(
        model=judge_model,
        messages=[{"role": "user", "content": prompt}],
        temperature=cfg.get("judge_temperature", 0),
    )
    extra_body = None
    if cfg.get("judge_web_search"):
        extra_body = {"plugins": [{"id": "web",
                                   "max_results": cfg.get("judge_web_max_results", 3)}]}
    try:
        resp = client.chat.completions.create(
            response_format={"type": "json_object"}, extra_body=extra_body, **kwargs)
    except Exception:
        resp = client.chat.completions.create(extra_body=extra_body, **kwargs)
    latency = time.time() - t0
    raw = resp.choices[0].message.content or ""
    verdicts = parse_verdicts(raw, criteria_ids)
    usage = resp.usage
    pt = getattr(usage, "prompt_tokens", 0) or 0
    ct = getattr(usage, "completion_tokens", 0) or 0
    pp, cp = pricing.get(judge_model, (0.0, 0.0))
    n_applicable = sum(1 for v in verdicts.values() if v["applicable"])
    n_passed = sum(1 for v in verdicts.values() if v["pass"] and v["applicable"])
    return {
        "verdicts": verdicts,
        "n_applicable": n_applicable,
        "n_passed": n_passed,
        "meets_standard": all(v["pass"] for v in verdicts.values()),
        "judge_cost_usd": round(pt * pp + ct * cp, 6),
        "judge_latency_s": round(latency, 3),
    }


def candidate_summary(bill: dict, slug_or_ref: str) -> str | None:
    if slug_or_ref == C.CRS_REFERENCE:
        return bill.get("crs_summary") or None
    path = C.SUMMARIES_DIR / slug_or_ref / f"{bill['bill_id']}.json"
    if not path.exists():
        return None
    rec = C.read_json(path)
    return rec.get("summary") or None


def main() -> None:
    ap = argparse.ArgumentParser(description="Judge candidate summaries on binary criteria.")
    ap.add_argument("--limit", type=int, default=None, help="only the first N bills")
    ap.add_argument("--retries", type=int, default=2)
    args = ap.parse_args()

    C.load_env()
    cfg = C.load_config()
    criteria = C.load_criteria()
    criteria_ids = [c["id"] for c in criteria]
    template = C.read_prompt(cfg["prompts"]["judge"])
    cblock = criteria_block(criteria)
    judge_model = cfg["judge_model"]
    client = C.openrouter_client()
    pricing = load_pricing()

    candidates = [C.model_slug(m) for m in cfg["models"]] + [C.CRS_REFERENCE]

    bills = C.list_bill_files()
    if args.limit:
        bills = bills[: args.limit]
    if not bills:
        sys.exit("No bills found. Run fetch_bills.py first.")

    print(f"Judge: {judge_model} | {len(bills)} bills x {len(candidates)} candidates")
    for cand in candidates:
        done = 0
        for bf in bills:
            bill = C.read_json(bf)
            out_path = C.SCORES_DIR / cand / f"{bill['bill_id']}.json"
            if out_path.exists():
                done += 1
                continue
            summary = candidate_summary(bill, cand)
            if not summary:
                continue
            prompt = (template
                      .replace("{criteria_block}", cblock)
                      .replace("{bill_text}", bill["bill_text"])
                      .replace("{summary}", summary))
            result = None
            for attempt in range(args.retries + 1):
                try:
                    result = judge_one(client, judge_model, prompt, cfg, pricing, criteria_ids)
                    break
                except Exception as e:  # noqa: BLE001
                    if attempt < args.retries:
                        time.sleep(2 * (attempt + 1))
                    else:
                        print(f"  {cand} {bill['bill_id']}: FAILED {str(e)[:80]}")
            if result is None:
                continue
            C.write_json(out_path, {"bill_id": bill["bill_id"], "candidate": cand, **result})
            done += 1
            print(f"  {cand} {bill['bill_id']}: "
                  f"{result['n_passed']}/{result['n_applicable']} pass"
                  f"{'  ★meets' if result['meets_standard'] else ''}")
        print(f"{cand}: {done}/{len(bills)} judged")


if __name__ == "__main__":
    main()
