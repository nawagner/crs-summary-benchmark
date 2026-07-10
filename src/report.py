"""Aggregate scores into a leaderboard, a CSV, and the website's results.json.

Reports per candidate (5 models + the CRS human baseline): % of summaries meeting ALL
applicable criteria (headline), per-criterion pass rate, and mean cost + latency.
"""
from __future__ import annotations

import csv
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402
import readability  # noqa: E402
import summarize as S  # noqa: E402

HUMAN_LABEL = "CRS (human)"


def label_for(cand_id: str, model_id: str | None) -> str:
    if cand_id == C.CRS_REFERENCE:
        return HUMAN_LABEL
    return model_id or cand_id


def gen_meta(slug: str, bill_id: str) -> dict:
    """Cost/latency for a generated summary (empty for the human baseline). Also passes
    through `strategy`/`n_chunks` (issue #7) when present in the record; old-style
    records without them simply omit the keys."""
    path = C.SUMMARIES_DIR / slug / f"{bill_id}.json"
    if not path.exists():
        return {}
    rec = C.read_json(path)
    meta = {"cost_usd": rec.get("cost_usd"), "latency_s": rec.get("latency_s"),
            "summary": rec.get("summary", "")}
    if "strategy" in rec:
        meta["strategy"] = rec["strategy"]
    if "n_chunks" in rec:
        meta["n_chunks"] = rec["n_chunks"]
    return meta


def candidate_entry(slug: str, model_id: str | None, bill: dict, score: dict,
                     cfg: dict, levels: list[dict]) -> dict:
    """Build one candidate's entry within a bill's `candidates` dict: score summary,
    generation meta, FK grade, and (for model candidates) any non-default reading-level
    summaries that have been generated so far."""
    meta = gen_meta(slug, bill["bill_id"])
    summary = bill["crs_summary"] if slug == C.CRS_REFERENCE else meta.get("summary", "")
    entry = {
        "label": label_for(slug, model_id),
        "is_human": slug == C.CRS_REFERENCE,
        "summary": summary,
        "meets_standard": score["meets_standard"],
        "n_passed": score["n_passed"],
        "n_applicable": score["n_applicable"],
        "verdicts": score["verdicts"],
        "cost_usd": meta.get("cost_usd"),
        "latency_s": meta.get("latency_s"),
        "fk_grade": readability.fk_grade(summary),
    }
    if "strategy" in meta:
        entry["strategy"] = meta["strategy"]
    if "n_chunks" in meta:
        entry["n_chunks"] = meta["n_chunks"]
    if score.get("judge_grounding"):
        entry["judge_grounding"] = score["judge_grounding"]

    if slug != C.CRS_REFERENCE:
        levels_out = {}
        for lvl in levels:
            if lvl.get("default"):
                continue
            lvl_path = S.summary_path(slug, bill["bill_id"], lvl)
            if not lvl_path.exists():
                continue
            lvl_rec = C.read_json(lvl_path)
            lvl_summary = lvl_rec.get("summary", "")
            if not lvl_summary:
                continue
            levels_out[lvl["id"]] = {
                "summary": lvl_summary,
                "fk_grade": readability.fk_grade(lvl_summary),
                "cost_usd": lvl_rec.get("cost_usd"),
                "latency_s": lvl_rec.get("latency_s"),
            }
        if levels_out:
            entry["levels"] = levels_out

    return entry


def _nice_ceil(x: float) -> int:
    """Round x up to a clean 1/2/5 x 10^k value for a readable axis maximum."""
    if x <= 0:
        return 50
    import math
    mag = 10 ** math.floor(math.log10(x))
    for m in (1, 2, 2.5, 5, 10):
        if x <= m * mag:
            return int(m * mag)
    return int(10 * mag)


def build_pavement_html(ordered, bills_out) -> str:
    """Render real pavement-library sparks of summary word counts — one row per summarizer,
    on a shared, outlier-robust domain so the lanes are directly comparable.

    The domain is anchored to the 90th percentile (not the raw max): a single giant
    outlier — e.g. a 16k-word CRS summary of an omnibus appropriations bill — would
    otherwise flatten every other lane and crowd the axis with unreadable ticks. Sparks
    are clipped to that domain; the range column always shows each lane's TRUE span, and
    lanes that run off-scale are marked so nothing is hidden.
    """
    import pavement

    ordered_ids = {a["id"] for a in ordered}  # unscored reference lanes must not skew the axis
    lengths: dict[str, list[int]] = {}
    for b in bills_out:
        for cid, c in b["candidates"].items():
            if cid in ordered_ids:
                lengths.setdefault(cid, []).append(len((c.get("summary") or "").split()))
    allv = sorted(v for vs in lengths.values() for v in vs)
    if not allv:
        return ""
    # A few extreme outliers — e.g. CRS summaries of thousand-page omnibus appropriations
    # bills, 5-25x a normal summary — would compress every real lane if the axis had to
    # reach them. Fit all in-family values on the axis (so the right edge is the largest
    # *real* summary, not an arbitrary clip), and push only the extreme outliers off-scale,
    # where the range column still reports each lane's true span. Threshold: a generous
    # multiple of the 90th percentile, so genuine long summaries stay on-axis.
    p90 = allv[min(len(allv) - 1, int(0.90 * len(allv)))]
    inliers = [v for v in allv if v <= 2.5 * p90]
    dmax = max(_nice_ceil(inliers[-1] if inliers else allv[-1]), 200)
    n_off = sum(1 for v in allv if v > dmax)  # values pushed off the right edge

    # one distinct hue per summarizer (matched by id substring, with a fallback cycle)
    PALETTE = {"anthropic": "#c0392b", "openai": "#1a7f4b", "google": "#1f6fb2",
               "z-ai": "#7a4ad1", "deepseek": "#e08a00", "crs_reference": "#44505f"}
    FALLBACK = ["#c0392b", "#1a7f4b", "#1f6fb2", "#7a4ad1", "#e08a00", "#0e8a8a", "#b5179e", "#44505f"]

    def color_for(cid, idx):
        for k, v in PALETTE.items():
            if k in cid:
                return v
        return FALLBACK[idx % len(FALLBACK)]

    def spark(vals, color):
        # clip to the shared domain so one lane's outliers don't flatten the rest;
        # clipped values pile at the right edge (the range column shows the true span).
        cvals = [min(v, dmax) for v in vals]
        s = pavement.svg.spark(cvals, domain=(0, dmax), bins=8, color=color,
                               fill_alpha=0.32, line_color=color, height="30px", hover=True)
        return s.replace("width:auto", "width:100%").replace("height:1em", "height:30px")

    rows = []
    for i, a in enumerate(ordered):
        vals = lengths.get(a["id"], [])
        if not vals:
            continue
        sv = sorted(vals)
        col = color_for(a["id"], i)
        human = ' <span class="pv-h">human</span>' if a.get("is_human") else ""
        off = ' <span class="pv-off" title="longest summary runs past the axis">&rsaquo;</span>' \
            if sv[-1] > dmax else ""
        rows.append(
            f'<tr><td class="pv-lbl"><span class="pv-dot" style="background:{col}"></span>{a["label"]}{human}'
            f'<br><span class="pv-sub" style="color:{col}">med {sv[len(sv)//2]:,}w</span></td>'
            f'<td class="pv-spark">{spark(vals, col)}</td>'
            f'<td class="pv-rng">{sv[0]:,}&ndash;{sv[-1]:,}w{off}</td></tr>'
        )
    step = _nice_ceil(dmax / 5)  # ~5-6 evenly spaced ticks, never crowded
    ticks = "".join(
        f'<span style="position:absolute;left:{v / dmax * 100:.2f}%;transform:translateX(-50%)">{v:,}</span>'
        for v in range(0, dmax + 1, step)
    )
    note = (f" &middot; {n_off} outlier summar{'y' if n_off == 1 else 'ies'} above {dmax:,}w "
            f"omitted from the axis; true spans at right" if n_off else "")
    axis = (f'<tr><td></td><td class="pv-axis"><div style="position:relative;height:1.1em">{ticks}</div>'
            f'<div class="pv-axis-lab">words per summary{note}</div></td><td></td></tr>')
    style = (
        "<style>"
        ".pv-table{border-collapse:collapse;width:100%;table-layout:fixed}"
        ".pv-table td{padding:5px 0;vertical-align:middle}"
        ".pv-lbl{width:182px;text-align:right;padding-right:14px;font-weight:600;font-size:13px;line-height:1.25}"
        ".pv-dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:6px;vertical-align:baseline}"
        ".pv-sub{font-weight:600;font-size:11px}"
        ".pv-h{font-size:10px;font-weight:700;color:#1f4e79;background:#eaf1f8;border:1px solid #cfe0ef;padding:0 6px;border-radius:99px}"
        ".pv-spark{padding:0 4px}"
        ".pv-rng{width:104px;padding-left:12px;font-size:11.5px;color:#5d6775;white-space:nowrap}"
        ".pv-off{color:#c0392b;font-weight:700}"
        ".pv-axis{font-size:11.5px;color:#5d6775;padding:6px 4px 0}"
        ".pv-axis-lab{text-align:center;margin-top:13px;color:#5d6775}"
        "</style>"
    )
    return style + f'<table class="pv-table">{"".join(rows)}{axis}</table>'


def main() -> None:
    cfg = C.load_config()
    criteria = C.load_criteria()
    criteria_ids = [c["id"] for c in criteria]
    model_ids = cfg["models"]
    candidates = [(C.model_slug(m), m) for m in model_ids] + [(C.CRS_REFERENCE, None)]

    bills = [C.read_json(p) for p in C.list_bill_files()]
    bills.sort(key=lambda b: (b["type"], int(b["number"])))

    # ---- aggregate per candidate ----
    agg = {}
    for slug, model_id in candidates:
        per_crit_pass = {cid: 0 for cid in criteria_ids}
        per_crit_appl = {cid: 0 for cid in criteria_ids}
        meets = n = 0
        costs, lats = [], []
        for bill in bills:
            sp = C.SCORES_DIR / slug / f"{bill['bill_id']}.json"
            if not sp.exists():
                continue
            score = C.read_json(sp)
            n += 1
            meets += 1 if score["meets_standard"] else 0
            for cid, v in score["verdicts"].items():
                if v["applicable"]:
                    per_crit_appl[cid] += 1
                    if v["pass"]:
                        per_crit_pass[cid] += 1
            meta = gen_meta(slug, bill["bill_id"])
            if meta.get("cost_usd") is not None:
                costs.append(meta["cost_usd"])
            if meta.get("latency_s") is not None:
                lats.append(meta["latency_s"])
        agg[slug] = {
            "id": slug,
            "model": model_id,
            "label": label_for(slug, model_id),
            "is_human": slug == C.CRS_REFERENCE,
            "n_bills": n,
            "meets_standard_rate": (meets / n) if n else 0.0,
            "meets_standard_count": meets,
            "per_criterion": {cid: (per_crit_pass[cid] / per_crit_appl[cid] if per_crit_appl[cid] else None)
                              for cid in criteria_ids},
            "mean_cost_usd": (sum(costs) / len(costs)) if costs else None,
            "mean_latency_s": (sum(lats) / len(lats)) if lats else None,
            "total_cost_usd": sum(costs) if costs else None,
        }

    ordered = sorted(agg.values(), key=lambda a: (a["is_human"], -a["meets_standard_rate"]))

    # ---- leaderboard.md ----
    C.RESULTS.mkdir(parents=True, exist_ok=True)
    judged_n = max((a["n_bills"] for a in ordered), default=0)
    lines = ["# Leaderboard", "",
             f"Bills judged: {judged_n}  |  Judge: `{cfg['judge_model']}`", "",
             "| Summarizer | Passes all criteria | Mean cost/summary | Mean latency |",
             "|---|---|---|---|"]
    for a in ordered:
        cost = f"${a['mean_cost_usd']:.4f}" if a["mean_cost_usd"] is not None else "—"
        lat = f"{a['mean_latency_s']:.1f}s" if a["mean_latency_s"] is not None else "—"
        lines.append(f"| {a['label']} | {a['meets_standard_rate']*100:.0f}% "
                     f"({a['meets_standard_count']}/{a['n_bills']}) | {cost} | {lat} |")
    lines += ["", "## Per-criterion pass rate", "",
              "| Summarizer | " + " | ".join(c["name"] for c in criteria) + " |",
              "|---|" + "---|" * len(criteria)]
    for a in ordered:
        cells = []
        for cid in criteria_ids:
            r = a["per_criterion"][cid]
            cells.append(f"{r*100:.0f}%" if r is not None else "—")
        lines.append(f"| {a['label']} | " + " | ".join(cells) + " |")
    (C.RESULTS / "leaderboard.md").write_text("\n".join(lines) + "\n")

    # ---- scores.csv ----
    with open(C.RESULTS / "scores.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["candidate", "meets_standard_rate", "meets_standard_count", "n_bills",
                    "mean_cost_usd", "mean_latency_s"] + criteria_ids)
        for a in ordered:
            w.writerow([a["label"], f"{a['meets_standard_rate']:.4f}", a["meets_standard_count"],
                        a["n_bills"], a["mean_cost_usd"], a["mean_latency_s"]]
                       + [a["per_criterion"][cid] for cid in criteria_ids])

    # ---- docs/data/results.json (self-contained for the website) ----
    levels = S.reading_levels(cfg)
    bills_out = []
    for bill in bills:
        cand_out = {}
        for slug, model_id in candidates:
            sp = C.SCORES_DIR / slug / f"{bill['bill_id']}.json"
            if not sp.exists():
                continue
            score = C.read_json(sp)
            cand_out[slug] = candidate_entry(slug, model_id, bill, score, cfg, levels)
        # unscored human reference baselines (issue #8): shown as cards, never judged,
        # never on the leaderboard — detectable by the absence of `verdicts`.
        ref_path = C.DATA_REFERENCES / f"{bill['bill_id']}.json"
        if ref_path.exists():
            ref = C.read_json(ref_path)
            for kind, label in (("committee", "Committee report"), ("cbo", "CBO cost estimate")):
                ref_summary = (ref.get(f"{kind}_summary") or "").strip()
                if not ref_summary:
                    continue
                cand_out[f"{kind}_reference"] = {
                    "label": label,
                    "is_human": True,
                    "reference_kind": kind,
                    "summary": ref_summary,
                    "source_url": ref.get(f"{kind}_url"),
                    "fk_grade": readability.fk_grade(ref_summary),
                }
        bills_out.append({
            "bill_id": bill["bill_id"],
            "congress": bill["congress"],
            "type": bill["type"],
            "number": bill["number"],
            "title": bill["title"],
            "congress_gov_url": bill["congress_gov_url"],
            "crs_version_code": bill.get("crs_version_code", ""),
            "bill_text_chars": bill.get("bill_text_chars"),
            "text_truncated": bill.get("text_truncated", False),
            "long_text": bill.get("long_text", False),
            "actions_count": bill.get("actions_count"),
            "candidates": cand_out,
        })

    results = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        "dataset_id": C.DATASET or "119",
        "judge_model": cfg["judge_model"],
        "congress": cfg["congress"],
        "model_ids": model_ids,
        "criteria": [{"id": c["id"], "name": c["name"], "description": c["description"],
                      "applicability": c.get("applicability", "always")} for c in criteria],
        "leaderboard": ordered,
        "pavement_html": build_pavement_html(ordered, bills_out),
        "bills": bills_out,
        "prompts": {
            "summarize": C.read_prompt(cfg["prompts"]["summarize"]),
            "judge": C.read_prompt(cfg["prompts"]["judge"]),
        },
    }
    if cfg.get("reading_levels"):
        results["reading_levels"] = [
            {"id": lvl["id"], "label": lvl["label"],
             **({"default": True} if lvl.get("default") else {}),
             **({"target_fk_grade": lvl["target_fk_grade"]} if "target_fk_grade" in lvl else {})}
            for lvl in levels
        ]
        results["prompts"]["summarize_levels"] = {
            lvl["id"]: C.read_prompt(lvl["prompt"]) for lvl in levels
        }
    C.write_json(C.RESULTS_JSON, results)

    print(f"Wrote {C.RESULTS / 'leaderboard.md'}, {C.RESULTS / 'scores.csv'}, {C.RESULTS_JSON}")
    print()
    print((C.RESULTS / "leaderboard.md").read_text())


if __name__ == "__main__":
    main()
