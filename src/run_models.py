"""Generate a summary of each bill with each model under test, via OpenRouter.

Saves one JSON per (model, bill, reading level) with the summary text plus latency,
token counts, and USD cost. Resumable: existing results are skipped. The generation
itself (single-shot vs map-reduce for long bills, reading-level prompt selection)
lives in summarize.py.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402
import summarize as S  # noqa: E402


def load_pricing() -> dict[str, tuple[float, float]]:
    """Map OpenRouter model id -> (usd_per_prompt_token, usd_per_completion_token)."""
    key = C.require_openrouter_key()
    r = httpx.get(
        f"{C.OPENROUTER_BASE_URL}/models",
        headers={"Authorization": f"Bearer {key}"},
        timeout=30,
    )
    r.raise_for_status()
    pricing: dict[str, tuple[float, float]] = {}
    for m in r.json().get("data", []):
        p = m.get("pricing") or {}
        try:
            pricing[m["id"]] = (float(p.get("prompt", 0)), float(p.get("completion", 0)))
        except (TypeError, ValueError):
            pricing[m["id"]] = (0.0, 0.0)
    return pricing


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate model summaries via OpenRouter.")
    ap.add_argument("--limit", type=int, default=None, help="only the first N bills")
    ap.add_argument("--models", type=str, default=None, help="comma-separated model override")
    ap.add_argument("--levels", type=str, default=None,
                    help="comma-separated reading-level ids (default: all configured)")
    ap.add_argument("--retries", type=int, default=2)
    args = ap.parse_args()

    C.load_env()
    cfg = C.load_config()
    models = args.models.split(",") if args.models else cfg["models"]
    levels = S.reading_levels(cfg)
    if args.levels:
        want = {x.strip() for x in args.levels.split(",")}
        unknown = want - {lvl["id"] for lvl in levels}
        if unknown:
            sys.exit(f"Unknown reading level(s) {sorted(unknown)}; "
                     f"configured: {[lvl['id'] for lvl in levels]}")
        levels = [lvl for lvl in levels if lvl["id"] in want]
    client = C.openrouter_client()
    pricing = load_pricing()

    bills = C.list_bill_files()
    if args.limit:
        bills = bills[: args.limit]
    if not bills:
        sys.exit("No bills found. Run fetch_bills.py first.")

    print(f"{len(bills)} bills x {len(models)} models x {len(levels)} level(s)")
    for model in models:
        slug = C.model_slug(model)
        ok = 0
        goal = len(bills) * len(levels)
        for bf in bills:
            bill = C.read_json(bf)
            for level in levels:
                out_path = S.summary_path(slug, bill["bill_id"], level)
                if out_path.exists():
                    ok += 1
                    continue
                try:
                    result = S.generate_summary(client, model, bill, level, cfg, pricing,
                                                retries=args.retries)
                except Exception as e:  # noqa: BLE001
                    result = {"summary": "", "ok": False, "error": str(e),
                              "cost_usd": 0, "latency_s": 0, "level": level["id"],
                              "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
                record = {"bill_id": bill["bill_id"], "model": model, **result}
                C.write_json(out_path, record)
                tag = "" if level.get("default") else f" [{level['id']}]"
                if result.get("ok"):
                    ok += 1
                    extra = (f" map-reduce/{result['n_chunks']}ch"
                             if result.get("strategy") == "map_reduce" else "")
                    print(f"  {model} {bill['bill_id']}{tag}: "
                          f"{result['latency_s']}s ${result['cost_usd']:.4f}{extra}")
                else:
                    print(f"  {model} {bill['bill_id']}{tag}: FAILED {result.get('error','')[:80]}")
        print(f"{model}: {ok}/{goal} done")


if __name__ == "__main__":
    main()
