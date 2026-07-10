"""Measure how far behind CRS is on summarizing bills, and write docs/data/lag.json.

"How behind" has three parts now (issue #6):
- the backlog: what share of bills have no CRS summary yet,
- the timing lag: coverage as a function of how recently a bill was introduced,
- coverage by legislative stage: CRS prioritizes bills that MOVE, so coverage is also
  measured against bills that advanced (committee action / floor consideration), not
  just against everything introduced.

Stage data comes from two layers. A full census pages the `bill/{congress}/{type}`
list endpoint (~250 bills/request) and classifies every bill's `latestAction` text with
src/stages.py — floor-stage bills are far too rare for a random sample to measure, so
the census provides exact per-stage denominators. The random sample (unchanged, for the
by-month series) additionally fetches each sampled bill's `/actions` list and classifies
the full action history; agreement between the two classifiers is reported as
`stage_agreement`, a validity check on the cheaper latestAction census.

Note: Congress.gov `updateDate` fields are bulk-refreshed, so we measure *whether a
summary exists today* (reliable), not the exact authoring date. Usage:
    python src/analyze_lag.py [hr_sample] [s_sample] [--out PATH] [--fixture DIR]
`--fixture DIR` serves API responses from DIR/responses.json (offline dry-run/tests).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
import time
from collections import defaultdict
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402
import stages  # noqa: E402

BASE = "https://api.congress.gov/v3"
ADVANCED = (stages.STAGE_COMMITTEE, stages.STAGE_FLOOR)
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


def fixture_getter(fixture_dir: Path):
    """An offline `get` that serves canned responses keyed by request path."""
    with open(fixture_dir / "responses.json") as f:
        responses = json.load(f)

    async def get(path, **params):
        if path not in responses:
            raise KeyError(f"no fixture response for {path!r}")
        return responses[path]

    return get


async def summarized_numbers(get, congress, btype, from_date, to_date):
    """Set of bill numbers of this type that have ANY CRS summary."""
    nums, offset = set(), 0
    while True:
        d = await get(f"summaries/{congress}/{btype}", limit=250, offset=offset,
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


async def census_latest_actions(get, congress, btype):
    """[(number, latest_action_text)] for EVERY bill of this type, via the list endpoint."""
    out, offset = [], 0
    while True:
        d = await get(f"bill/{congress}/{btype}", limit=250, offset=offset)
        recs = d.get("bills", [])
        for b in recs:
            n = b.get("number")
            if n is None:
                continue
            out.append((int(n), (b.get("latestAction") or {}).get("text") or ""))
        count = d.get("pagination", {}).get("count", 0)
        offset += 250
        if offset >= count or not recs:
            break
    return out


async def sample_bills(get, congress, btype, total, k, summ):
    """Sample bill numbers; per resolved bill return
    (intro_date, has_summary, stage_from_full_actions, latest_action_text)."""
    nums = sorted(random.sample(range(1, total + 1), min(k, total)))
    sem = asyncio.Semaphore(12)

    async def one(n):
        async with sem:
            try:
                d = await get(f"bill/{congress}/{btype}/{n}")
                bill = d.get("bill", {})
                intro = bill.get("introducedDate")
                latest = (bill.get("latestAction") or {}).get("text") or ""
                acts = await get(f"bill/{congress}/{btype}/{n}/actions", limit=250)
                stage = stages.classify_stage(acts.get("actions", []))
                return intro, (n in summ), stage, latest
            except Exception:  # noqa: BLE001
                return None, (n in summ), None, ""

    return [r for r in await asyncio.gather(*[one(n) for n in nums]) if r[0]]


async def main_async(hr_k: int, s_k: int, out_path: Path, get=None) -> dict:
    congress = C.load_config()["congress"]
    from_date = "2025-01-01T00:00:00Z"
    to_date = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    own_client = None
    if get is None:
        C.load_env()
        key = os.environ.get("CONGRESS_API_KEY")
        if not key:
            raise SystemExit("CONGRESS_API_KEY not set (see .env).")
        own_client = httpx.AsyncClient()

        async def get(path, **params):  # noqa: F811
            return await _get(own_client, key, path, **params)

    try:
        chambers, sample = {}, []
        stage_counts = {st: [0, 0] for st in stages.STAGE_ORDER}  # stage -> [total, summarized]
        for btype, k in (("hr", hr_k), ("s", s_k)):
            total = (await get(f"bill/{congress}/{btype}", limit=1))["pagination"]["count"]
            summ = await summarized_numbers(get, congress, btype, from_date, to_date)

            # full census: exact per-stage totals from every bill's latestAction text
            for n, latest in await census_latest_actions(get, congress, btype):
                st = stages.classify_action_text(latest)
                stage_counts[st][0] += 1
                if n in summ:
                    stage_counts[st][1] += 1

            rows = await sample_bills(get, congress, btype, total, k, summ)
            sample += rows
            chambers[btype] = {"total": total, "summarized": len(summ),
                               "pct": round(len(summ) / total, 4)}
            print(f"{btype}: {len(summ)}/{total} summarized ({len(summ)/total*100:.0f}%), sampled {len(rows)}")

        # validity check: does the cheap latestAction classifier agree with the
        # full-action-history classifier on the sampled bills?
        pairs = [(stages.classify_action_text(latest), st)
                 for _, _, st, latest in sample if st is not None and latest]
        agreement = round(sum(1 for a, b in pairs if a == b) / len(pairs), 4) if pairs else None

        by = defaultdict(lambda: [0, 0, 0, 0])  # month -> [n, summarized, advanced_n, advanced_summarized]
        for intro, has, stage, _ in sample:
            row = by[intro[:7]]
            row[0] += 1
            row[1] += 1 if has else 0
            if stage in ADVANCED:
                row[2] += 1
                row[3] += 1 if has else 0
        months = [{"month": m, "n": by[m][0], "summarized": by[m][1],
                   "coverage": round(by[m][1] / by[m][0], 4),
                   "advanced_n": by[m][2], "advanced_summarized": by[m][3],
                   "advanced_coverage": round(by[m][3] / by[m][2], 4) if by[m][2] else None}
                  for m in sorted(by)]

        out = {
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
            "congress": congress,
            "sampled": len(sample),
            "chambers": chambers,
            "stages": {st: {"total": t, "summarized": s,
                            "pct": round(s / t, 4) if t else None}
                       for st, (t, s) in stage_counts.items()},
            "stage_method": ("latestAction-text census over every hr/s bill (list endpoint), "
                             "validated against full /actions classification on the sample"),
            "stage_agreement": agreement,
            "months": months,
        }
        C.write_json(out_path, out)
        print(f"wrote {out_path} ({len(months)} months, {len(sample)} bills sampled)")
        for st in (stages.STAGE_FLOOR, stages.STAGE_COMMITTEE, stages.STAGE_INTRODUCED):
            t, s = stage_counts[st]
            pct = f"{s / t * 100:.0f}%" if t else "n/a"
            print(f"  {st}: {s}/{t} summarized ({pct})")
        return out
    finally:
        if own_client is not None:
            await own_client.aclose()


def main() -> None:
    ap = argparse.ArgumentParser(description="Measure CRS summary coverage and lag.")
    ap.add_argument("hr_sample", nargs="?", type=int, default=700)
    ap.add_argument("s_sample", nargs="?", type=int, default=400)
    ap.add_argument("--out", type=Path, default=C.DOCS_DATA / "lag.json")
    ap.add_argument("--fixture", type=Path, default=None,
                    help="serve API responses from DIR/responses.json (offline dry-run)")
    args = ap.parse_args()
    get = fixture_getter(args.fixture) if args.fixture else None
    asyncio.run(main_async(args.hr_sample, args.s_sample, args.out, get=get))


if __name__ == "__main__":
    main()
