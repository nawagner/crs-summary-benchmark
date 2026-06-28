"""Measure how far behind CRS is on summarizing bills, and write docs/data/lag.json.

"How behind" has two parts: the backlog (what share of bills have no CRS summary yet)
and the timing lag (coverage as a function of how recently a bill was introduced). We
sample bills across the numbering range, look up each one's introduction date, and check
whether it has a CRS summary now — giving coverage by introduction month.

Note: Congress.gov `updateDate` fields are bulk-refreshed, so we measure *whether a
summary exists today* (reliable), not the exact authoring date. Usage:
    python src/analyze_lag.py [hr_sample] [s_sample]
"""
from __future__ import annotations

import asyncio
import os
import random
import sys
import time
from collections import defaultdict
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

BASE = "https://api.congress.gov/v3"
random.seed(7)


async def _get(client, key, path, **params):
    params.update(api_key=key, format="json")
    last = None
    for attempt in range(4):
        try:
            r = await client.get(f"{BASE}/{path}", params=params, timeout=40)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001  (transient network/5xx — retry with backoff)
            last = e
            await asyncio.sleep(0.5 * (attempt + 1))
    raise last


async def summarized_numbers(client, key, congress, btype, from_date, to_date):
    """Set of bill numbers of this type that have ANY CRS summary."""
    nums, offset = set(), 0
    while True:
        d = await _get(client, key, f"summaries/{congress}/{btype}", limit=250, offset=offset,
                       fromDateTime=from_date, toDateTime=to_date, sort="updateDate+desc")
        recs = d.get("summaries", [])
        for s in recs:
            n = (s.get("bill") or {}).get("number")
            if n:
                nums.add(int(n))
        count = d.get("pagination", {}).get("count", 0)
        offset += 250
        if offset >= count or not recs:
            break
    return nums


async def sample_intro_dates(client, key, congress, btype, total, k, summ):
    """Sample bill numbers, return (intro_date, has_summary) for each that resolves."""
    nums = sorted(random.sample(range(1, total + 1), min(k, total)))
    sem = asyncio.Semaphore(12)

    async def one(n):
        async with sem:
            try:
                d = await _get(client, key, f"bill/{congress}/{btype}/{n}")
                return d.get("bill", {}).get("introducedDate"), (n in summ)
            except Exception:  # noqa: BLE001
                return None, (n in summ)

    return [r for r in await asyncio.gather(*[one(n) for n in nums]) if r[0]]


async def main_async(hr_k: int, s_k: int) -> None:
    C.load_env()
    key = os.environ.get("CONGRESS_API_KEY")
    if not key:
        raise SystemExit("CONGRESS_API_KEY not set (see .env).")
    congress = C.load_config()["congress"]
    from_date = "2025-01-01T00:00:00Z"
    to_date = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    async with httpx.AsyncClient() as client:
        chambers, sample = {}, []
        for btype, k in (("hr", hr_k), ("s", s_k)):
            total = (await _get(client, key, f"bill/{congress}/{btype}", limit=1))["pagination"]["count"]
            summ = await summarized_numbers(client, key, congress, btype, from_date, to_date)
            rows = await sample_intro_dates(client, key, congress, btype, total, k, summ)
            sample += rows
            chambers[btype] = {"total": total, "summarized": len(summ),
                               "pct": round(len(summ) / total, 4)}
            print(f"{btype}: {len(summ)}/{total} summarized ({len(summ)/total*100:.0f}%), sampled {len(rows)}")

        by = defaultdict(lambda: [0, 0])
        for intro, has in sample:
            by[intro[:7]][0] += 1
            by[intro[:7]][1] += 1 if has else 0
        months = [{"month": m, "n": by[m][0], "summarized": by[m][1],
                   "coverage": round(by[m][1] / by[m][0], 4)} for m in sorted(by)]

        out = {
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
            "congress": congress,
            "sampled": len(sample),
            "chambers": chambers,
            "months": months,
        }
        C.write_json(C.DOCS_DATA / "lag.json", out)
        print(f"wrote {C.DOCS_DATA / 'lag.json'} ({len(months)} months, {len(sample)} bills sampled)")


def main() -> None:
    hr_k = int(sys.argv[1]) if len(sys.argv) > 1 else 700
    s_k = int(sys.argv[2]) if len(sys.argv) > 2 else 400
    asyncio.run(main_async(hr_k, s_k))


if __name__ == "__main__":
    main()
