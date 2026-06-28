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

HUMAN_LABEL = "CRS (human)"


def label_for(cand_id: str, model_id: str | None) -> str:
    if cand_id == C.CRS_REFERENCE:
        return HUMAN_LABEL
    return model_id or cand_id


def gen_meta(slug: str, bill_id: str) -> dict:
    """Cost/latency for a generated summary (empty for the human baseline)."""
    path = C.SUMMARIES_DIR / slug / f"{bill_id}.json"
    if not path.exists():
        return {}
    rec = C.read_json(path)
    return {"cost_usd": rec.get("cost_usd"), "latency_s": rec.get("latency_s"),
            "summary": rec.get("summary", "")}


def build_pavement_html(ordered, bills_out) -> str:
    """Render real pavement-library sparks of summary word counts — one row per summarizer,
    all on a shared 0-anchored domain so the lanes are directly comparable."""
    import pavement

    lengths: dict[str, list[int]] = {}
    for b in bills_out:
        for cid, c in b["candidates"].items():
            lengths.setdefault(cid, []).append(len((c.get("summary") or "").split()))
    allv = [v for vs in lengths.values() for v in vs]
    if not allv:
        return ""
    dmax = (max(allv) // 50 + 1) * 50

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
        s = pavement.svg.spark(vals, domain=(0, dmax), bins=8, color=color,
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
        rows.append(
            f'<tr><td class="pv-lbl"><span class="pv-dot" style="background:{col}"></span>{a["label"]}{human}'
            f'<br><span class="pv-sub" style="color:{col}">med {sv[len(sv)//2]}w</span></td>'
            f'<td class="pv-spark">{spark(vals, col)}</td>'
            f'<td class="pv-rng">{sv[0]}&ndash;{sv[-1]}w</td></tr>'
        )
    step = 150 if dmax <= 750 else 200
    ticks = "".join(
        f'<span style="position:absolute;left:{v / dmax * 100:.2f}%;transform:translateX(-50%)">{v}</span>'
        for v in range(0, dmax + 1, step)
    )
    axis = (f'<tr><td></td><td class="pv-axis"><div style="position:relative;height:1.1em">{ticks}</div>'
            f'<div class="pv-axis-lab">words per summary</div></td><td></td></tr>')
    style = (
        "<style>"
        ".pv-table{border-collapse:collapse;width:100%;table-layout:fixed}"
        ".pv-table td{padding:5px 0;vertical-align:middle}"
        ".pv-lbl{width:182px;text-align:right;padding-right:14px;font-weight:600;font-size:13px;line-height:1.25}"
        ".pv-dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:6px;vertical-align:baseline}"
        ".pv-sub{font-weight:600;font-size:11px}"
        ".pv-h{font-size:10px;font-weight:700;color:#1f4e79;background:#eaf1f8;border:1px solid #cfe0ef;padding:0 6px;border-radius:99px}"
        ".pv-spark{padding:0 4px}"
        ".pv-rng{width:92px;padding-left:12px;font-size:11.5px;color:#5d6775;white-space:nowrap}"
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
    bills_out = []
    for bill in bills:
        cand_out = {}
        for slug, model_id in candidates:
            sp = C.SCORES_DIR / slug / f"{bill['bill_id']}.json"
            if not sp.exists():
                continue
            score = C.read_json(sp)
            meta = gen_meta(slug, bill["bill_id"])
            summary = bill["crs_summary"] if slug == C.CRS_REFERENCE else meta.get("summary", "")
            cand_out[slug] = {
                "label": label_for(slug, model_id),
                "is_human": slug == C.CRS_REFERENCE,
                "summary": summary,
                "meets_standard": score["meets_standard"],
                "n_passed": score["n_passed"],
                "n_applicable": score["n_applicable"],
                "verdicts": score["verdicts"],
                "cost_usd": meta.get("cost_usd"),
                "latency_s": meta.get("latency_s"),
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
    C.write_json(C.RESULTS_JSON, results)

    print(f"Wrote {C.RESULTS / 'leaderboard.md'}, {C.RESULTS / 'scores.csv'}, {C.RESULTS_JSON}")
    print()
    print((C.RESULTS / "leaderboard.md").read_text())


if __name__ == "__main__":
    main()
