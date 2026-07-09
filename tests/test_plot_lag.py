"""Tests for src/plot_lag.py: rendering docs/data/lag.json to a chart PNG.

Covers both the old lag.json shape (no advanced-bills fields) and the new shape
(additive advanced_n/advanced_summarized/advanced_coverage per month, plus a
top-level stages block) — the old shape must render identically to today's
chart (no overlay line, no legend), and the new shape must draw the rust-colored
"advanced" overlay line only for months with enough advanced bills.
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
    for i, m in enumerate(d["months"]):
        # alternate between well-sampled months (>= threshold) and thin months
        # (below the ADVANCED_MIN_N threshold) so the overlay line has a gap.
        advanced_n = 25 if i % 2 == 0 else 3
        advanced_summarized = int(advanced_n * (0.4 + 0.03 * i))
        m["advanced_n"] = advanced_n
        m["advanced_summarized"] = advanced_summarized
        m["advanced_coverage"] = advanced_summarized / advanced_n
    d["stages"] = {
        "floor": {"total": 300, "summarized": 291, "pct": 0.97},
        "committee": {"total": 900, "summarized": 500, "pct": 0.556},
        "introduced": {"total": 9000, "summarized": 2700, "pct": 0.3},
    }
    return d


def test_old_shape_renders_png_without_error(tmp_path: Path) -> None:
    data_path = tmp_path / "lag.json"
    out_path = tmp_path / "crs-lag.png"
    data_path.write_text(json.dumps(_old_shape_lag()))

    fig = P.main(data_path, out_path)

    assert out_path.exists()
    assert out_path.stat().st_size > 0
    assert fig is not None


def test_old_shape_has_no_advanced_overlay_or_legend(tmp_path: Path) -> None:
    data_path = tmp_path / "lag.json"
    out_path = tmp_path / "crs-lag.png"
    data_path.write_text(json.dumps(_old_shape_lag()))

    fig = P.main(data_path, out_path)
    ax = fig.axes[0]

    # bars are patches, not Line2D objects, so an unmodified chart has no lines
    assert len(ax.lines) == 0
    assert ax.get_legend() is None


def test_new_shape_renders_png_without_error(tmp_path: Path) -> None:
    data_path = tmp_path / "lag.json"
    out_path = tmp_path / "crs-lag.png"
    data_path.write_text(json.dumps(_new_shape_lag()))

    fig = P.main(data_path, out_path)

    assert out_path.exists()
    assert out_path.stat().st_size > 0


def test_new_shape_draws_advanced_overlay_line(tmp_path: Path) -> None:
    data_path = tmp_path / "lag.json"
    out_path = tmp_path / "crs-lag.png"
    data_path.write_text(json.dumps(_new_shape_lag()))

    fig = P.main(data_path, out_path)
    ax = fig.axes[0]

    assert len(ax.lines) == 1
    line = ax.lines[0]
    assert line.get_color() == P.ADVANCED_COLOR
    assert ax.get_legend() is not None


def test_new_shape_overlay_has_gaps_below_threshold(tmp_path: Path) -> None:
    data_path = tmp_path / "lag.json"
    out_path = tmp_path / "crs-lag.png"
    d = _new_shape_lag()
    data_path.write_text(json.dumps(d))

    fig = P.main(data_path, out_path)
    line = fig.axes[0].lines[0]
    ydata = line.get_ydata()

    # months with advanced_n below ADVANCED_MIN_N (the odd-indexed months, set to
    # 3 above) should be NaN gaps rather than interpolated values.
    for i, m in enumerate(d["months"]):
        if m["advanced_n"] < P.ADVANCED_MIN_N:
            assert ydata[i] != ydata[i]  # NaN != NaN
        else:
            assert ydata[i] == pytest.approx(m["advanced_coverage"] * 100)

