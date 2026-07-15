"""Render docs/data/reauth.json as chart images (issue #14).

Two charts, matching the site's house style (see plot_lag.py):
- docs/assets/reauth-pass-rates.png      — meets-standard rate by bill category, one
                                            panel per dataset, models (pooled) vs the
                                            human CRS baseline, every bar labeled
                                            "x of y" (n is small — counts are the point).
- docs/assets/reauth-failure-criteria.png — which criteria fail on amendatory bills,
                                            models vs human, exact counts from the
                                            per-criterion aggregates (not the capped
                                            example list). This is the failure-mode
                                            picture: do amendatory bills fail on
                                            amendment-tracing or on something else?

Run after analyze_reauth.py. Keeps the website dependency-free: matplotlib draws the
charts here, the site just shows the PNGs.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from matplotlib.patches import FancyBboxPatch  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

INK = "#1a2230"
MUTED = "#6b7480"
FAINT = "#9aa3af"
GRID = "#eaedf1"
MODELS_BLUE = "#2b6aa3"
HUMAN_AMBER = "#c47a2c"  # validated pair: CVD ΔE 21.6 vs the blue on white

CAT_LABELS = [
    ("amendatory", "Amendatory / reauthorization"),
    ("appropriations", "Appropriations"),
    ("cra_disapproval", "CRA disapproval"),
    ("standalone", "Standalone"),
]

for fam in ("Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"):
    if any(fam in f.name for f in font_manager.fontManager.ttflist):
        plt.rcParams["font.family"] = fam
        break
plt.rcParams["font.weight"] = "regular"


def _pooled_models(cands: dict) -> tuple[int, int]:
    """(meets_standard_count, n) summed over the non-human candidates."""
    count = n = 0
    for c in cands.values():
        if not c["is_human"]:
            count += c["meets_standard_count"]
            n += c["n_bills"]
    return count, n


def _hbar(ax, y, pct, color, height=0.30):
    ax.add_patch(FancyBboxPatch(
        (0, y - height / 2), max(pct, 0.6), height,
        boxstyle="round,pad=0,rounding_size=0.08",
        mutation_aspect=0.5, linewidth=0, facecolor=color, zorder=3))


def plot_pass_rates(d: dict, out_path: Path) -> plt.Figure:
    datasets = list(d["datasets"].items())
    rows_per = [sum(1 for k, _ in CAT_LABELS if k in ds["groups"]) for _, ds in datasets]
    fig, axes = plt.subplots(
        len(datasets), 1, figsize=(9.6, 1.15 * sum(rows_per) + 1.6), dpi=200,
        gridspec_kw={"height_ratios": rows_per, "hspace": 0.55})
    fig.patch.set_facecolor("white")

    for ax, (ds_id, ds) in zip(axes, datasets):
        ax.set_facecolor("white")
        cats = [(k, lbl) for k, lbl in CAT_LABELS if k in ds["groups"]]
        ys = list(range(len(cats)))[::-1]
        for y, (cat, lbl) in zip(ys, cats):
            g = ds["groups"][cat]
            mc, mn = _pooled_models(g["candidates"])
            crs = g["candidates"].get("crs_reference")
            for dy, color, count, n, name in (
                (0.19, MODELS_BLUE, mc, mn, "models"),
                (-0.19, HUMAN_AMBER,
                 crs["meets_standard_count"] if crs else 0,
                 crs["n_bills"] if crs else 0, "CRS"),
            ):
                if not n:
                    continue
                pct = count / n * 100
                _hbar(ax, y + dy, pct, color)
                ax.text(pct + 1.5, y + dy, f"{count} of {n}", ha="left", va="center",
                        fontsize=8, color=MUTED, clip_on=False)
        ax.set_xlim(0, 100)
        ax.set_ylim(-0.6, len(cats) - 0.4)
        ax.set_yticks(ys)
        ax.set_yticklabels([lbl for _, lbl in cats], fontsize=10.5, color=INK)
        ax.set_xticks([0, 25, 50, 75, 100])
        ax.set_xticklabels(["0", "25", "50", "75", "100%"], fontsize=8.5, color=FAINT)
        ax.tick_params(length=0)
        ax.grid(axis="x", color=GRID, linewidth=1.1, zorder=0)
        for spine in ax.spines.values():
            spine.set_visible(False)
        ax.set_title(f"{ds['label']}  ·  {ds['n_bills']} bills", loc="left",
                     fontsize=10.5, color=INK, pad=8)

    handles = [plt.Rectangle((0, 0), 1, 1, color=MODELS_BLUE),
               plt.Rectangle((0, 0), 1, 1, color=HUMAN_AMBER)]
    fig.legend(handles, ["all 5 models, pooled", "CRS (human)"], loc="upper right",
               frameon=False, fontsize=9, labelcolor=MUTED, ncol=2,
               bbox_to_anchor=(0.98, 0.995))
    fig.text(0.065, 0.008, "share of summaries passing every applicable criterion · "
             f"as of {d.get('generated_at', '').split(' ')[0]}",
             fontsize=8.5, color=FAINT)
    fig.subplots_adjust(top=0.93, bottom=0.06, left=0.24, right=0.90)
    fig.savefig(out_path, facecolor="white")
    print(f"wrote {out_path}")
    return fig


def _amendatory_failures(d: dict) -> tuple[dict, dict]:
    """Exact per-criterion failing-verdict counts on amendatory bills, pooled across
    datasets, split models vs human. From the aggregates, not the capped example list."""
    models: dict[str, int] = {}
    human: dict[str, int] = {}
    for ds in d["datasets"].values():
        g = ds["groups"].get("amendatory")
        if not g:
            continue
        for cand in g["candidates"].values():
            sink = human if cand["is_human"] else models
            for cid, pc in cand["per_criterion"].items():
                fails = pc["n_applicable"] - pc["n_passed"]
                if fails:
                    sink[cid] = sink.get(cid, 0) + fails
    return models, human


def plot_failure_criteria(d: dict, out_path: Path) -> plt.Figure | None:
    models, human = _amendatory_failures(d)
    cids = sorted(set(models) | set(human),
                  key=lambda c: -(models.get(c, 0) + human.get(c, 0)))
    if not cids:
        return None
    fig, ax = plt.subplots(figsize=(9.6, 0.5 * len(cids) + 1.7), dpi=200)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    ys = list(range(len(cids)))[::-1]
    xmax = max(models.get(c, 0) + human.get(c, 0) for c in cids)
    for y, cid in zip(ys, cids):
        m, h = models.get(cid, 0), human.get(cid, 0)
        ax.barh(y, m, height=0.56, color=MODELS_BLUE, zorder=3)
        # 2px-equivalent gap between stacked segments via a thin white edge
        ax.barh(y, h, left=m, height=0.56, color=HUMAN_AMBER, zorder=3,
                edgecolor="white", linewidth=0.8)
        ax.text(m + h + xmax * 0.015, y, str(m + h), ha="left", va="center",
                fontsize=9.5, color=INK, fontweight="bold")

    ax.set_yticks(ys)
    ax.set_yticklabels([c.replace("_", " ") for c in cids], fontsize=10.5, color=INK)
    ax.set_xlim(0, xmax * 1.12)
    ax.set_ylim(-0.6, len(cids) - 0.4)
    ax.xaxis.set_major_locator(plt.MaxNLocator(integer=True))
    ax.tick_params(length=0, labelsize=8.5, labelcolor=FAINT)
    ax.grid(axis="x", color=GRID, linewidth=1.1, zorder=0)
    for spine in ax.spines.values():
        spine.set_visible(False)

    handles = [plt.Rectangle((0, 0), 1, 1, color=MODELS_BLUE),
               plt.Rectangle((0, 0), 1, 1, color=HUMAN_AMBER)]
    ax.legend(handles, ["models", "CRS (human)"], loc="lower right", frameon=False,
              fontsize=9, labelcolor=MUTED)
    ax.set_title("Failing verdicts on amendatory bills, by criterion — all datasets",
                 loc="left", fontsize=10.5, color=INK, pad=10)
    fig.text(0.065, 0.015, "count of applicable-and-failed judge verdicts · "
             f"as of {d.get('generated_at', '').split(' ')[0]}",
             fontsize=8.5, color=FAINT)
    fig.subplots_adjust(top=0.88, bottom=0.14, left=0.24, right=0.97)
    fig.savefig(out_path, facecolor="white")
    print(f"wrote {out_path}")
    return fig


def main(data_path: Path | None = None, rates_out: Path | None = None,
         failures_out: Path | None = None) -> None:
    data_path = data_path or C.DOCS_DATA / "reauth.json"
    rates_out = rates_out or C.ROOT / "docs" / "assets" / "reauth-pass-rates.png"
    failures_out = failures_out or C.ROOT / "docs" / "assets" / "reauth-failure-criteria.png"
    d = json.load(open(data_path))
    plot_pass_rates(d, rates_out)
    plot_failure_criteria(d, failures_out)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=None)
    parser.add_argument("--rates-out", type=Path, default=None)
    parser.add_argument("--failures-out", type=Path, default=None)
    args = parser.parse_args()
    main(args.data, args.rates_out, args.failures_out)
