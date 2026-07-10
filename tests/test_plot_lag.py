"""Tests for src/plot_lag.py: rendering docs/data/lag.json to chart PNGs.

The monthly chart is always just clean coverage-by-month bars (no overlay line).
The stage-coverage story lives in a SEPARATE horizontal bar chart drawn from the
`stages` census block — produced only when that block is present, so old lag.json
files (without stages) still render the monthly chart and simply skip the second one.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import plot_lag as P  # noqa: E402

MONTHS = [f"2025-{m:02d}" for m in range(1, 13)]


def _old_shape_lag() -> dict:
    months = []
    for i, month in enumerate(MONTHS):
        n = 700 + i * 5
        summarized = int(n * (0.1 + 0.05 * i))
        months.append({
            "month": month,
            "n": n,
            "summarized": summarized,
            "coverage": summarized / n,
        })
    return {
        "generated_at": "2026-06-01 00:00:00 UTC",
        "congress": 119,
        "sampled": 1100,
        "chambers": {
            "hr": {"total": 9000, "summarized": 2700, "pct": 0.3},
            "s": {"total": 4600, "summarized": 1500, "pct": 0.326},
        },
        "months": months,
    }


def _new_shape_lag() -> dict:
    d = _old_shape_lag()
    d["stages"] = {
        "floor": {"total": 631, "summarized": 601, "pct": 0.952},
        "committee": {"total": 1336, "summarized": 471, "pct": 0.352},
        "introduced": {"total": 12562, "summarized": 3174, "pct": 0.253},
    }
    return d


def test_monthly_chart_renders_without_error(tmp_path: Path) -> None:
    data_path = tmp_path / "lag.json"
    out_path = tmp_path / "crs-lag.png"
    data_path.write_text(json.dumps(_old_shape_lag()))

    fig = P.main(data_path, out_path)

    assert out_path.exists()
    assert out_path.stat().st_size > 0
    assert fig is not None


def test_monthly_chart_has_no_overlay_line_or_legend(tmp_path: Path) -> None:
    # the monthly chart is always clean bars — no overlay line, regardless of
    # whether the data carries the stages block.
    for shape in (_old_shape_lag(), _new_shape_lag()):
        data_path = tmp_path / "lag.json"
        out_path = tmp_path / "crs-lag.png"
        data_path.write_text(json.dumps(shape))

        fig = P.main(data_path, out_path)
        ax = fig.axes[0]

        assert len(ax.lines) == 0
        assert ax.get_legend() is None


def test_old_shape_writes_no_stages_chart(tmp_path: Path) -> None:
    data_path = tmp_path / "lag.json"
    out_path = tmp_path / "crs-lag.png"
    stages_out = tmp_path / "crs-lag-stages.png"
    data_path.write_text(json.dumps(_old_shape_lag()))

    P.main(data_path, out_path, stages_out)

    assert out_path.exists()          # monthly chart still drawn
    assert not stages_out.exists()    # no stages block -> no second chart


def test_new_shape_writes_stages_chart(tmp_path: Path) -> None:
    data_path = tmp_path / "lag.json"
    out_path = tmp_path / "crs-lag.png"
    stages_out = tmp_path / "crs-lag-stages.png"
    data_path.write_text(json.dumps(_new_shape_lag()))

    P.main(data_path, out_path, stages_out)

    assert stages_out.exists()
    assert stages_out.stat().st_size > 0


def test_plot_stages_bars_match_census(tmp_path: Path) -> None:
    stages_out = tmp_path / "crs-lag-stages.png"
    d = _new_shape_lag()

    fig = P.plot_stages(d, stages_out)
    ax = fig.axes[0]

    # one rounded bar patch per stage, widths proportional to pct
    from matplotlib.patches import FancyBboxPatch
    widths = sorted(p.get_width() for p in ax.patches if isinstance(p, FancyBboxPatch))
    assert widths == pytest.approx([25.3, 35.2, 95.2], abs=0.5)


def test_plot_stages_returns_none_without_stages(tmp_path: Path) -> None:
    assert P.plot_stages(_old_shape_lag(), tmp_path / "x.png") is None
    assert not (tmp_path / "x.png").exists()

