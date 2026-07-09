"""Render docs/data/lag.json as a polished chart image (docs/assets/crs-lag.png).

Keeps the website dependency-free: matplotlib draws the chart here, the site just shows
the resulting PNG. Run after analyze_lag.py.
"""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402
from matplotlib.patches import FancyBboxPatch, Patch  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

INK = "#1a2230"
MUTED = "#6b7480"
FAINT = "#9aa3af"
GRID = "#eaedf1"
# light slate -> deep navy, so taller (better-covered) bars read darker
CMAP = LinearSegmentedColormap.from_list("crsnavy", ["#bcd0e4", "#2b6aa3", "#173a5e"])
# warm rust for the "advanced bills" overlay line, distinct from the navy bars
ADVANCED_COLOR = "#b3541e"
ADVANCED_MIN_N = 10

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


def main(data_path: Path | None = None, out_path: Path | None = None) -> plt.Figure:
    if data_path is None:
        data_path = C.DOCS_DATA / "lag.json"
    if out_path is None:
        out_path = C.ROOT / "docs" / "assets" / "crs-lag.png"

    d = json.load(open(data_path))
    months = d["months"]
    labels = [datetime.strptime(m["month"], "%Y-%m").strftime("%b '%y") for m in months]
    cov = [m["coverage"] * 100 for m in months]
    xs = list(range(len(cov)))

    # optional overlay: coverage among bills that advanced (committee/floor action).
    # only plotted for months with enough advanced bills to be meaningful; other
    # months get a NaN so the line has a gap there instead of interpolating over them.
    advanced_cov: list[float] = []
    has_advanced = False
    for m in months:
        ac = m.get("advanced_coverage")
        an = m.get("advanced_n") or 0
        if ac is not None and an >= ADVANCED_MIN_N:
            has_advanced = True
            advanced_cov.append(ac * 100)
        else:
            advanced_cov.append(math.nan)

    fig, ax = plt.subplots(figsize=(9.6, 4.6), dpi=200)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    rounded_bars(ax, xs, cov)
    for x, v in zip(xs, cov):
        ax.text(x, v + 2.2, f"{round(v)}", ha="center", va="bottom",
                fontsize=8, color=MUTED)

    if has_advanced:
        line, = ax.plot(
            xs, advanced_cov, color=ADVANCED_COLOR, linewidth=1.8,
            marker="o", markersize=4, zorder=5,
            label="bills that advanced (committee/floor)")
        bar_proxy = Patch(facecolor=CMAP(0.7), label="all introduced bills")
        ax.legend(handles=[bar_proxy, line], loc="upper left", frameon=False,
                  fontsize=7.5, labelcolor=MUTED, handlelength=1.4, borderaxespad=0.2)

    ax.set_xlim(-0.7, len(cov) - 0.3)
    ax.set_ylim(0, 100)
    ax.set_yticks([0, 25, 50, 75, 100])
    ax.set_yticklabels(["0", "25", "50", "75", "100%"], fontsize=9.5, color=FAINT)
    ax.set_xticks(xs)
    ax.set_xticklabels(labels, rotation=0, fontsize=8.2, color=MUTED)
    ax.tick_params(length=0)
    ax.margins(x=0)

    ax.grid(axis="y", color=GRID, linewidth=1.1, zorder=0)
    for s in ax.spines.values():
        s.set_visible(False)

    # callout over the recent, barely-covered stretch
    recent_from = max(0, len(cov) - 6)
    ax.axvspan(recent_from - 0.5, len(cov) - 0.5, color="#f4a26122", zorder=1)
    ax.annotate("recent bills: mostly\nnot yet summarized",
                xy=(len(cov) - 2.4, 9), xytext=(len(cov) - 6.2, 62),
                fontsize=9, color="#9a5b1d", ha="left", va="center",
                arrowprops=dict(arrowstyle="-|>", color="#c47a2c", lw=1.2,
                                connectionstyle="arc3,rad=-0.2"))

    asof = d.get("generated_at", "").split(" ")[0]
    ax.text(0.5, -0.16, f"119th Congress · House + Senate bills · {d.get('sampled')} sampled · as of {asof}",
            transform=ax.transAxes, ha="center", va="top", fontsize=8.5, color=FAINT)

    fig.subplots_adjust(top=0.94, bottom=0.16, left=0.06, right=0.985)
    fig.savefig(out_path, facecolor="white")
    print(f"wrote {out_path}")
    return fig


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=C.DOCS_DATA / "lag.json",
                         help="path to lag.json (default: docs/data/lag.json)")
    parser.add_argument("--out", type=Path, default=C.ROOT / "docs" / "assets" / "crs-lag.png",
                         help="path to write the chart PNG (default: docs/assets/crs-lag.png)")
    args = parser.parse_args()
    main(args.data, args.out)
