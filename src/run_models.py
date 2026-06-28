"""Generate a summary of each bill with each model under test, via OpenRouter.

Saves one JSON per (model, bill) with the summary text plus latency, token counts,
and USD cost. Resumable: existing results are skipped.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402


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


def summarize_one(client, model: str, prompt: str, cfg: dict, pricing) -> dict:
    t0 = time.time()
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=cfg.get("summarize_temperature", 0.3),
        max_tokens=cfg.get("max_output_tokens", 1500),
    )
    latency = time.time() - t0
    summary = (resp.choices[0].message.content or "").strip()
    usage = resp.usage
    pt = getattr(usage, "prompt_tokens", 0) or 0
    ct = getattr(usage, "completion_tokens", 0) or 0
    pp, cp = pricing.get(model, (0.0, 0.0))
    cost = pt * pp + ct * cp
    return {
        "summary": summary,
        "prompt_tokens": pt,
        "completion_tokens": ct,
        "total_tokens": pt + ct,
        "cost_usd": round(cost, 6),
        "latency_s": round(latency, 3),
        "ok": bool(summary),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate model summaries via OpenRouter.")
    ap.add_argument("--limit", type=int, default=None, help="only the first N bills")
    ap.add_argument("--models", type=str, default=None, help="comma-separated model override")
    ap.add_argument("--retries", type=int, default=2)
    args = ap.parse_args()

    C.load_env()
    cfg = C.load_config()
    models = args.models.split(",") if args.models else cfg["models"]
    template = C.read_prompt(cfg["prompts"]["summarize"])
    client = C.openrouter_client()
    pricing = load_pricing()

    bills = C.list_bill_files()
    if args.limit:
        bills = bills[: args.limit]
    if not bills:
        sys.exit("No bills found. Run fetch_bills.py first.")

    print(f"{len(bills)} bills x {len(models)} models")
    for model in models:
        slug = C.model_slug(model)
        ok = 0
        for bf in bills:
            bill = C.read_json(bf)
            out_path = C.SUMMARIES_DIR / slug / f"{bill['bill_id']}.json"
            if out_path.exists():
                ok += 1
                continue
            prompt = template.replace("{bill_text}", bill["bill_text"])
            result = None
            for attempt in range(args.retries + 1):
                try:
                    result = summarize_one(client, model, prompt, cfg, pricing)
                    break
                except Exception as e:  # noqa: BLE001
                    if attempt < args.retries:
                        time.sleep(2 * (attempt + 1))
                    else:
                        result = {"summary": "", "ok": False, "error": str(e),
                                  "cost_usd": 0, "latency_s": 0,
                                  "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            record = {"bill_id": bill["bill_id"], "model": model, **result}
            C.write_json(out_path, record)
            if result.get("ok"):
                ok += 1
                print(f"  {model} {bill['bill_id']}: "
                      f"{result['latency_s']}s ${result['cost_usd']:.4f}")
            else:
                print(f"  {model} {bill['bill_id']}: FAILED {result.get('error','')[:80]}")
        print(f"{model}: {ok}/{len(bills)} done")


if __name__ == "__main__":
    main()
