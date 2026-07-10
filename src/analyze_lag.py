"""Measure how far behind CRS is on summarizing bills, and write docs/data/lag.json.

"How behind" has three parts now (issue #6):
- the backlog: what share of bills have no CRS summary yet,
- monthly volume: how many bills are introduced each month and how many are summarized —
  the FULL population, every bill placed by its exact introducedDate,
- coverage by legislative stage: CRS prioritizes bills that MOVE, so coverage is also
  measured against bills that advanced (committee action / floor consideration), not
  just against everything introduced.

All three are exact, not sampled. A full census pages the `bill/{congress}/{type}` list
endpoint and classifies every bill's `latestAction` text with src/stages.py for the
per-stage totals. Monthly volume reads every bill's exact `introducedDate` from the
GovInfo BILLSTATUS bulk data (one small zip per chamber). A small random sample survives
only as QA: it fetches each sampled bill's `/actions` history and reports `stage_agreement`
— how often the cheap latestAction classifier matches the full-history one — and feeds no
displayed count.

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
import re
import sys
import tempfile
import time
import zipfile
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
    (number, intro_date, has_summary, stage_from_full_actions, latest_action_text)."""
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
                return n, intro, (n in summ), stage, latest
            except Exception:  # noqa: BLE001
                return n, None, (n in summ), None, ""

    return [r for r in await asyncio.gather(*[one(n) for n in nums]) if r[1]]


GOVINFO_ZIP = ("https://www.govinfo.gov/bulkdata/BILLSTATUS/"
               "{congress}/{btype}/BILLSTATUS-{congress}-{btype}.zip")
_INTRO_RE = re.compile(rb"<introducedDate>(\d{4}-\d{2}-\d{2})</introducedDate>")


async def govinfo_intro_months(client, congress, btype):
    """{bill_number: 'YYYY-MM'} for EVERY bill of this type, parsed from the GovInfo
    BILLSTATUS bulk zip — the exact introduced date for the full population, no sampling
    and no interpolation. The bill number comes from each entry's filename; the date from
    its <introducedDate>."""
    url = GOVINFO_ZIP.format(congress=congress, btype=btype)
    r = await client.get(url, timeout=300, follow_redirects=True)
    r.raise_for_status()
    name_re = re.compile((rf"BILLSTATUS-{congress}{btype}(\d+)\.xml$").encode())
    out: dict[int, str] = {}
    with tempfile.NamedTemporaryFile(suffix=".zip") as tf:
        tf.write(r.content)
        tf.flush()
        with zipfile.ZipFile(tf.name) as z:
            for name in z.namelist():
                m = name_re.search(name.encode())
                if not m:
                    continue
                dm = _INTRO_RE.search(z.read(name))
                if dm:
                    out[int(m.group(1))] = dm.group(1).decode()[:7]
    return out


async def api_intro_months(get, congress, btype, numbers):
    """Exact introduced month per bill from the bill endpoint — the offline/fixture path
    (when the GovInfo bulk download isn't available, e.g. tests). Still exact, no sampling."""
    out: dict[int, str] = {}
    for n in numbers:
        try:
            d = await get(f"bill/{congress}/{btype}/{n}")
            intro = d.get("bill", {}).get("introducedDate")
            if intro:
                out[n] = intro[:7]
        except Exception:  # noqa: BLE001
            pass
    return out


async def main_async(hr_k: int, s_k: int, out_path: Path, get=None) -> dict:
    congress = C.load_config()["congress"]
    from_date = "2025-01-01T00:00:00Z"
    to_date = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    own_client = None
    live = get is None  # live run downloads GovInfo bulk data; fixture path stays offline
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
        volume = defaultdict(lambda: [0, 0])  # month -> [total_bills, summarized_bills]
        for btype, k in (("hr", hr_k), ("s", s_k)):
            total = (await get(f"bill/{congress}/{btype}", limit=1))["pagination"]["count"]
            summ = await summarized_numbers(get, congress, btype, from_date, to_date)

            census = await census_latest_actions(get, congress, btype)
            # full census: exact per-stage totals from every bill's latestAction text
            for n, latest in census:
                st = stages.classify_action_text(latest)
                stage_counts[st][0] += 1
                if n in summ:
                    stage_counts[st][1] += 1

            rows = await sample_bills(get, congress, btype, total, k, summ)
            sample += rows

            # exact monthly VOLUME: the true introduced date of EVERY bill — the full
            # population, no sampling and no interpolation. Live runs read the GovInfo
            # BILLSTATUS bulk zip; the offline/fixture path reads the bill endpoint.
            if live:
                intro_months = await govinfo_intro_months(own_client, congress, btype)
            else:
                intro_months = await api_intro_months(get, congress, btype,
                                                      [n for n, _ in census])
            for n, month in intro_months.items():
                volume[month][0] += 1
                if n in summ:
                    volume[month][1] += 1

            chambers[btype] = {"total": total, "summarized": len(summ),
                               "pct": round(len(summ) / total, 4)}
            print(f"{btype}: {len(summ)}/{total} summarized ({len(summ)/total*100:.0f}%), sampled {len(rows)}")

        # QA only: does the cheap latestAction stage classifier (used for the full census)
        # agree with the full-action-history classifier on a sample of bills? This is a
        # classifier validity check — it does not feed any displayed count.
        pairs = [(stages.classify_action_text(latest), st)
                 for _, _, _, st, latest in sample if st is not None and latest]
        agreement = round(sum(1 for a, b in pairs if a == b) / len(pairs), 4) if pairs else None

        # monthly series is the exact full population: total introduced and summarized per
        # month, every bill placed by its real introducedDate.
        months = [{"month": m, "volume_total": volume[m][0], "volume_summarized": volume[m][1]}
                  for m in sorted(volume)]

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
            "volume_method": ("exact introducedDate for every House and Senate bill "
                              "(GovInfo BILLSTATUS bulk data); full population, no sampling "
                              "or interpolation"),
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
