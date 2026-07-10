"""Render docs/data/lag.json as polished chart images.

Two charts, so each tells one clean story instead of overloading a single plot:
- docs/assets/crs-lag.png        — coverage by month of introduction (the timing lag),
                                    from the random sample.
- docs/assets/crs-lag-stages.png — coverage by furthest legislative stage reached
                                    (introduced / committee / floor), from the FULL
                                    census. This is the reliable "CRS covers what moves"
                                    signal; the per-month sample is too thin to show it
                                    over time, so it lives in its own bar chart.

Keeps the website dependency-free: matplotlib draws the charts here, the site just shows
the resulting PNGs. Run after analyze_lag.py.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402
from matplotlib.patches import FancyBboxPatch  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

INK = "#1a2230"
MUTED = "#6b7480"
FAINT = "#9aa3af"
GRID = "#eaedf1"
# light slate -> deep navy, so taller (better-covered) bars read darker
CMAP = LinearSegmentedColormap.from_list("crsnavy", ["#bcd0e4", "#2b6aa3", "#173a5e"])

# stage-chart rows, top to bottom (furthest-advanced first)
STAGE_ROWS = [
    ("floor", "Reached the floor"),
    ("committee", "Committee action"),
    ("introduced", "Introduced only"),
]

for fam in ("Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"):
    if any(fam in f.name for f in font_manager.fontManager.ttflist):
        plt.rcParams["font.family"] = fam
        break
plt.rcParams["font.weight"] = "regular"


def rounded_bars(ax, xs, vals, width=0.66, radius=0.10):
    for x, v in zip(xs, vals):
        if v <= 0:
            ax.add_patch(plt.Rectangle((x - width / 2, 0), width, 0.6, color="#dfe4ea", zorder=3))
            continue
        ax.add_patch(FancyBboxPatch(
            (x - width / 2, 0), width, v,
            boxstyle=f"round,pad=0,rounding_size={radius}",
            mutation_aspect=0.06, linewidth=0,
            facecolor=CMAP(min(1.0, v / 95)), zorder=3))


def plot_stages(d: dict, out_path: Path) -> plt.Figure | None:
    """Horizontal bar chart of CRS coverage by furthest legislative stage reached.

    Uses the full-census `stages` block (every hr/s bill classified), not the monthly
    sample — this is the clean "CRS covers what moves" signal. Returns None (writes
    nothing) if the data has no stages block, so old lag.json files are unaffected.
    """
    stages = d.get("stages")
    if not stages:
        return None
    rows = [(label, stages[key]) for key, label in STAGE_ROWS if stages.get(key)]
    if not rows:
        return None

    fig, ax = plt.subplots(figsize=(9.6, 2.7), dpi=200)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    ys = list(range(len(rows)))[::-1]  # first row (floor) on top
    for y, (label, s) in zip(ys, rows):
        pct = (s.get("pct") or 0) * 100
        ax.add_patch(FancyBboxPatch(
            (0, y - 0.28), max(pct, 0.4), 0.56,
            boxstyle="round,pad=0,rounding_size=0.11",
            mutation_aspect=0.5, linewidth=0,
            facecolor=CMAP(min(1.0, pct / 95)), zorder=3))
        # value + count stacked just past the bar end; clip_on=False lets the long
        # floor bar's label render into the right margin instead of being cut off.
        ax.text(pct + 2, y + 0.10, f"{round(pct)}%", ha="left", va="center",
                fontsize=13, color=INK, fontweight="bold", clip_on=False)
        summ, total = s.get("summarized", 0), s.get("total", 0)
        ax.text(pct + 2.2, y - 0.24, f"{summ:,} of {total:,}", ha="left", va="center",
                fontsize=8, color=FAINT, clip_on=False)

    ax.set_xlim(0, 100)
    ax.set_ylim(-0.6, len(rows) - 0.4)
    ax.set_yticks(ys)
    ax.set_yticklabels([label for label, _ in rows], fontsize=11, color=INK)
    ax.set_xticks([0, 25, 50, 75, 100])
    ax.set_xticklabels(["0", "25", "50", "75", "100%"], fontsize=9, color=FAINT)
    ax.tick_params(length=0)
    ax.grid(axis="x", color=GRID, linewidth=1.1, zorder=0)
    for spine in ax.spines.values():
        spine.set_visible(False)

    asof = d.get("generated_at", "").split(" ")[0]
    ax.text(0.0, -0.32,
            f"119th Congress · every House + Senate bill classified by furthest stage "
            f"reached (full census) · as of {asof}",
            transform=ax.transAxes, ha="left", va="top", fontsize=8.5, color=FAINT)

    fig.subplots_adjust(top=0.96, bottom=0.22, left=0.17, right=0.90)
    fig.savefig(out_path, facecolor="white")
    print(f"wrote {out_path}")
    return fig


SUMMARIZED_BLUE = "#2b6aa3"
UNSUMMARIZED = "#eef1f4"
UNSUMMARIZED_EDGE = "#cfd6de"


def _nice_step(ymax: int) -> int:
    for step in (25, 50, 100, 200, 250, 500, 1000, 2000, 5000):
        if ymax / step <= 6:
            return step
    return 10000


def _draw_monthly_volume(ax, months, labels, xs) -> None:
    """Stacked bars of bills introduced per month: summarized (blue) vs not yet (off-white).
    Shows both volume over time and the coverage within each month at a glance."""
    summ = [m.get("volume_summarized", 0) for m in months]
    tot = [m.get("volume_total", 0) for m in months]
    nots = [t - s for t, s in zip(tot, summ)]

    ax.bar(xs, summ, width=0.72, color=SUMMARIZED_BLUE, zorder=3, label="has a CRS summary")
    ax.bar(xs, nots, width=0.72, bottom=summ, color=UNSUMMARIZED, edgecolor=UNSUMMARIZED_EDGE,
           linewidth=0.7, zorder=3, label="no summary yet")

    ymax = max(tot) if tot else 1
    step = _nice_step(ymax)
    top = ((ymax // step) + 1) * step
    for x, t in zip(xs, tot):
        if t:
            ax.text(x, t + top * 0.012, f"{t:,}", ha="center", va="bottom",
                    fontsize=6.5, color=MUTED)
    # summarized count, in blue, just above each blue (summarized) segment
    for x, s in zip(xs, summ):
        if s:
            ax.text(x, s + top * 0.012, f"{s:,}", ha="center", va="bottom",
                    fontsize=6.5, color=SUMMARIZED_BLUE, fontweight="bold", zorder=4)

    ax.set_ylim(0, top)
    ax.set_yticks(list(range(0, top + 1, step)))
    ax.set_yticklabels([f"{v:,}" for v in range(0, top + 1, step)], fontsize=9, color=FAINT)
    ax.set_ylabel("bills introduced", fontsize=9.5, color=MUTED)
    ax.legend(loc="upper right", frameon=False, fontsize=8.5, labelcolor=MUTED,
              handlelength=1.1, borderaxespad=0.4)


def _draw_monthly_coverage(ax, months, labels, xs) -> None:
    """Legacy view for lag.json without volume fields: coverage % bars by month."""
    cov = [m["coverage"] * 100 for m in months]
    rounded_bars(ax, xs, cov)
    for x, v in zip(xs, cov):
        ax.text(x, v + 2.2, f"{round(v)}", ha="center", va="bottom", fontsize=8, color=MUTED)
    ax.set_ylim(0, 100)
    ax.set_yticks([0, 25, 50, 75, 100])
    ax.set_yticklabels(["0", "25", "50", "75", "100%"], fontsize=9.5, color=FAINT)
    recent_from = max(0, len(cov) - 6)
    ax.axvspan(recent_from - 0.5, len(cov) - 0.5, color="#f4a26122", zorder=1)
    ax.annotate("recent bills: mostly\nnot yet summarized",
                xy=(len(cov) - 2.4, 9), xytext=(len(cov) - 6.2, 62),
                fontsize=9, color="#9a5b1d", ha="left", va="center",
                arrowprops=dict(arrowstyle="-|>", color="#c47a2c", lw=1.2,
                                connectionstyle="arc3,rad=-0.2"))


def main(data_path: Path | None = None, out_path: Path | None = None,
         stages_out: Path | None = None) -> plt.Figure:
    if data_path is None:
        data_path = C.DOCS_DATA / "lag.json"
    if out_path is None:
        out_path = C.ROOT / "docs" / "assets" / "crs-lag.png"
    if stages_out is None:
        stages_out = C.ROOT / "docs" / "assets" / "crs-lag-stages.png"

    d = json.load(open(data_path))
    months = d["months"]
    labels = [datetime.strptime(m["month"], "%Y-%m").strftime("%b '%y") for m in months]
    xs = list(range(len(months)))
    has_volume = any(m.get("volume_total") for m in months)

    fig, ax = plt.subplots(figsize=(9.6, 4.6), dpi=200)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    if has_volume:
        _draw_monthly_volume(ax, months, labels, xs)
    else:
        _draw_monthly_coverage(ax, months, labels, xs)

    ax.set_xlim(-0.7, len(months) - 0.3)
    ax.set_xticks(xs)
    ax.set_xticklabels(labels, rotation=0, fontsize=8.2, color=MUTED)
    ax.tick_params(length=0)
    ax.margins(x=0)

    ax.grid(axis="y", color=GRID, linewidth=1.1, zorder=0)
    for s in ax.spines.values():
        s.set_visible(False)

    asof = d.get("generated_at", "").split(" ")[0]
    tail = "full House + Senate population" if has_volume else f"{d.get('sampled')} sampled"
    ax.text(0.5, -0.16, f"119th Congress · {tail} · as of {asof}",
            transform=ax.transAxes, ha="center", va="top", fontsize=8.5, color=FAINT)

    fig.subplots_adjust(top=0.94, bottom=0.16, left=0.075, right=0.985)
    fig.savefig(out_path, facecolor="white")
    print(f"wrote {out_path}")

    # second chart: coverage by legislative stage (from the census). No-op on old data.
    plot_stages(d, stages_out)
    return fig


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=C.DOCS_DATA / "lag.json",
                         help="path to lag.json (default: docs/data/lag.json)")
    parser.add_argument("--out", type=Path, default=C.ROOT / "docs" / "assets" / "crs-lag.png",
                         help="path to write the monthly chart PNG (default: docs/assets/crs-lag.png)")
    parser.add_argument("--stages-out", type=Path,
                         default=C.ROOT / "docs" / "assets" / "crs-lag-stages.png",
                         help="path to write the stage-coverage chart PNG")
    args = parser.parse_args()
    main(args.data, args.out, args.stages_out)
