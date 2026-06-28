"""Build the benchmark corpus: bills that have a real CRS summary AND retrievable text.

Reuses the project author's congress-mcp client (CongressClient/Config) as the data layer.
Strategy: page the `summaries/{congress}/{type}` listing — every record already carries a
CRS-authored summary plus a bill reference — dedupe to the latest summary per bill, then
download each bill's full legislative text. Saves one JSON per bill to data/bills/.
"""
from __future__ import annotations

import argparse
import asyncio
import itertools
import sys
import time
from collections import defaultdict
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

try:
    from congress_mcp.client import CongressClient
    from congress_mcp.config import Config
except ImportError:
    sys.exit(
        "congress-mcp not installed. Run: pip install -r requirements.txt\n"
        "(installs git+https://github.com/nawagner/congress-mcp.git)"
    )

# type code -> congress.gov URL slug for human-facing links
_TYPE_SLUG = {
    "hr": "house-bill",
    "s": "senate-bill",
    "hjres": "house-joint-resolution",
    "sjres": "senate-joint-resolution",
    "hconres": "house-concurrent-resolution",
    "sconres": "senate-concurrent-resolution",
    "hres": "house-resolution",
    "sres": "senate-resolution",
}


def congress_gov_url(congress: int, btype: str, number: int) -> str:
    slug = _TYPE_SLUG.get(btype.lower(), "house-bill")
    return f"https://www.congress.gov/bill/{congress}th-congress/{slug}/{number}"


async def collect_summaries(client: CongressClient, congress: int, btypes: list[str],
                            pool: int, from_date: str, to_date: str):
    """Return {bill_key: latest_summary_record} across the requested bill types.

    The summaries listing defaults to a narrow recent window, so we pass an explicit
    date range spanning the whole Congress and sort by most recently updated.
    """
    params = {"fromDateTime": from_date, "toDateTime": to_date, "sort": "updateDate desc"}
    by_bill: dict[str, dict] = {}
    versions: dict[str, int] = defaultdict(int)
    for btype in btypes:
        resp = await client.get_all(f"summaries/{congress}/{btype}", params=params, max_results=pool)
        for s in resp.get("results", []):
            bill = s.get("bill") or {}
            num = bill.get("number")
            btype_actual = (bill.get("type") or btype).lower()
            if num is None:
                continue
            key = C.bill_id(congress, btype_actual, num)
            versions[key] += 1
            prev = by_bill.get(key)
            # keep the summary with the latest actionDate (most recent CRS write-up)
            if prev is None or (s.get("actionDate", "") > prev.get("actionDate", "")):
                by_bill[key] = s
    return by_bill, dict(versions)


async def fetch_action_counts(client: CongressClient, congress: int,
                              keys_bills: dict[str, dict], max_concurrent: int = 20) -> dict[str, int]:
    """For each bill, the total number of legislative actions (a signal of activity)."""
    sem = asyncio.Semaphore(max_concurrent)

    async def one(key: str, btype: str, number) -> tuple[str, int]:
        async with sem:
            try:
                r = await client.get(f"bill/{congress}/{btype}/{number}/actions", limit=1)
                return key, int(r.get("pagination", {}).get("count", 0) or 0)
            except Exception:  # noqa: BLE001
                return key, 0

    tasks = [one(k, (s["bill"].get("type") or "").lower(), s["bill"].get("number"))
             for k, s in keys_bills.items()]
    return dict(await asyncio.gather(*tasks))


async def fetch_bill_text(client: CongressClient, http: httpx.AsyncClient,
                          congress: int, btype: str, number: int) -> tuple[str, str]:
    """Return (plain_text, source_url) for the latest text version, or ('', '')."""
    try:
        resp = await client.get(f"bill/{congress}/{btype}/{number}/text")
    except (httpx.HTTPError, Exception):
        return "", ""
    versions = resp.get("textVersions") or []
    if not versions:
        return "", ""
    # latest version: prefer the one with the most recent date, fall back to last listed
    versions = sorted(versions, key=lambda v: v.get("date") or "", reverse=True)
    for version in versions:
        formats = version.get("formats") or []
        url = None
        for want in ("Formatted Text", "Formatted XML"):
            for fmt in formats:
                if fmt.get("type") == want and fmt.get("url"):
                    url = fmt["url"]
                    break
            if url:
                break
        if not url:
            continue
        try:
            r = await http.get(url, timeout=90, follow_redirects=True)
            r.raise_for_status()
        except (httpx.HTTPError, Exception):
            continue
        text = C.html_to_text(r.text)
        if text:
            return text, url
    return "", ""


async def main_async(args) -> None:
    C.load_env()
    cfg = C.load_config()
    congress = cfg["congress"]
    btypes = cfg["bill_types"]
    target = args.limit or cfg["target_bills"]
    cap = cfg["text_char_cap"]

    config = Config.from_env()  # reads CONGRESS_API_KEY
    C.DATA_BILLS.mkdir(parents=True, exist_ok=True)

    existing = {p.stem for p in C.list_bill_files()}
    print(f"Have {len(existing)} bills already; target {target}.")

    by_activity = bool(cfg.get("select_by_activity"))
    # pull a generous pool of summaries so we can skip bills whose text is unavailable
    pool = max(cfg.get("activity_pool_cap", 320) * 5, 1500) if by_activity else max(target * 6, 120)
    from_date = cfg.get("summary_from_date", "2025-01-03T00:00:00Z")
    to_date = cfg.get("summary_to_date") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    activity: dict[str, int] = {}

    async with CongressClient(config) as client:
        async with httpx.AsyncClient(headers={"User-Agent": "crs-summary-benchmark"}) as http:
            by_bill, versions = await collect_summaries(client, congress, btypes, pool, from_date, to_date)
            print(f"Found {len(by_bill)} bills with CRS summaries across {btypes}.")

            if by_activity:
                # candidate pool = bills with the most CRS-summary versions (those that advanced),
                # then rank those by total legislative actions and select the most active.
                pool_cap = cfg.get("activity_pool_cap", 320)
                cand = sorted(by_bill, key=lambda k: versions.get(k, 1), reverse=True)[:pool_cap]
                print(f"Fetching action counts for top {len(cand)} candidates by summary versions…")
                activity = await fetch_action_counts(client, congress, {k: by_bill[k] for k in cand})
                cand.sort(key=lambda k: activity.get(k, 0), reverse=True)
                ordered = [(k, by_bill[k]) for k in cand]
                print(f"Most active: {[(k, activity[k]) for k in cand[:5]]}")
            else:
                # interleave across bill types (round-robin) so the corpus mixes chambers
                by_type: dict[str, list] = defaultdict(list)
                for key, s in by_bill.items():
                    by_type[(s["bill"].get("type") or "").lower()].append((key, s))
                ordered = [x for tup in itertools.zip_longest(*(by_type[t] for t in btypes if by_type[t]))
                           for x in tup if x]

            saved = len(existing)
            for key, s in ordered:
                if saved >= target:
                    break
                if key in existing:
                    continue
                bill = s["bill"]
                btype = (bill.get("type") or "").lower()
                number = bill.get("number")
                crs_summary = C.html_to_text(s.get("text", ""))
                if not crs_summary:
                    continue
                try:
                    text, text_url = await fetch_bill_text(client, http, congress, btype, number)
                except Exception as e:  # noqa: BLE001
                    print(f"  skip {key}: text fetch error ({str(e)[:50]})")
                    continue
                if not text:
                    print(f"  skip {key}: no retrievable text")
                    continue
                truncated = len(text) > cap
                if truncated and by_activity:
                    # keep the activity set fully readable: skip oversize bills (e.g. omnibus
                    # appropriations) rather than feed models only a truncated slice.
                    print(f"  skip {key}: full text {len(text)} chars > cap (kept fully-readable)")
                    continue
                if truncated:
                    text = text[:cap]

                record = {
                    "bill_id": key,
                    "congress": congress,
                    "type": btype,
                    "number": number,
                    "title": bill.get("title", ""),
                    "origin_chamber": bill.get("originChamber", ""),
                    "congress_gov_url": congress_gov_url(congress, btype, number),
                    "crs_summary": crs_summary,
                    "crs_version_code": s.get("versionCode", ""),
                    "crs_action_date": s.get("actionDate", ""),
                    "crs_action_desc": s.get("actionDesc", ""),
                    "bill_text": text,
                    "bill_text_chars": len(text),
                    "bill_text_tokens_est": C.estimate_tokens(text),
                    "text_truncated": truncated,
                    "text_url": text_url,
                    "actions_count": activity.get(key),
                    "summary_versions": versions.get(key),
                }
                C.write_json(C.DATA_BILLS / f"{key}.json", record)
                saved += 1
                flag = " [truncated]" if truncated else ""
                act = f" ({activity[key]} actions)" if key in activity else ""
                print(f"  [{saved}/{target}] saved {key}{act}: {bill.get('title','')[:64]}{flag}")

    print(f"Done. {len(C.list_bill_files())} bills in {C.DATA_BILLS}.")


def main() -> None:
    ap = argparse.ArgumentParser(description="Fetch bills with CRS summaries + text.")
    ap.add_argument("--limit", type=int, default=None, help="override target bill count")
    asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    main()
