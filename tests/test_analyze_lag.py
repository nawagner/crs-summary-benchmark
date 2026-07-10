"""End-to-end shape test for analyze_lag.py against a canned API fixture (offline)."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import analyze_lag  # noqa: E402
import common as C  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures" / "lag_api"


def run(tmp_path):
    out_path = tmp_path / "lag.json"
    get = analyze_lag.fixture_getter(FIXTURES)
    # sample sizes >= population so every fixture bill is sampled deterministically
    out = asyncio.run(analyze_lag.main_async(700, 400, out_path, get=get))
    return out, out_path


def test_output_shape_and_backcompat_keys(tmp_path, monkeypatch):
    monkeypatch.delenv("CRS_CONFIG", raising=False)
    out, out_path = run(tmp_path)
    assert out_path.exists() and C.read_json(out_path) == out
    for key in ("generated_at", "congress", "sampled", "chambers", "months"):
        assert key in out  # the pre-#6 consumer contract still holds
    assert out["congress"] == 119
    assert out["chambers"]["hr"] == {"total": 6, "summarized": 4, "pct": round(4 / 6, 4)}
    assert out["chambers"]["s"] == {"total": 4, "summarized": 2, "pct": 0.5}


def test_stage_census(tmp_path, monkeypatch):
    monkeypatch.delenv("CRS_CONFIG", raising=False)
    out, _ = run(tmp_path)
    # floor: hr1 (law), hr6 (passed), s3 (passed) — all summarized
    assert out["stages"]["floor"] == {"total": 3, "summarized": 3, "pct": 1.0}
    # committee: hr3 (reported), hr5 (calendar), s2 (ordered reported) — hr5 unsummarized
    assert out["stages"]["committee"] == {"total": 3, "summarized": 2, "pct": round(2 / 3, 4)}
    # introduced: hr2, hr4, s1, s4 — only hr4 summarized
    assert out["stages"]["introduced"] == {"total": 4, "summarized": 1, "pct": 0.25}
    assert "stage_method" in out


def test_stage_agreement_flags_latestaction_blindspots(tmp_path, monkeypatch):
    monkeypatch.delenv("CRS_CONFIG", raising=False)
    out, _ = run(tmp_path)
    # hr4's latestAction is a mere subcommittee referral, but its action history shows
    # hearings held — 1 disagreement out of 10 sampled bills.
    assert out["stage_agreement"] == 0.9


def test_monthly_advanced_coverage(tmp_path, monkeypatch):
    monkeypatch.delenv("CRS_CONFIG", raising=False)
    out, _ = run(tmp_path)
    months = {m["month"]: m for m in out["months"]}
    jan, feb, mar = months["2025-01"], months["2025-02"], months["2025-03"]
    assert (jan["n"], jan["summarized"], jan["advanced_n"], jan["advanced_summarized"]) == (3, 1, 1, 1)
    assert jan["advanced_coverage"] == 1.0
    # hr4 counts as advanced here via its full action history (hearings held)
    assert (feb["n"], feb["summarized"], feb["advanced_n"], feb["advanced_summarized"]) == (3, 3, 3, 3)
    assert (mar["n"], mar["summarized"], mar["advanced_n"], mar["advanced_summarized"]) == (4, 2, 3, 2)
    assert mar["advanced_coverage"] == round(2 / 3, 4)


def test_monthly_volume_from_full_census(tmp_path, monkeypatch):
    monkeypatch.delenv("CRS_CONFIG", raising=False)
    out, _ = run(tmp_path)
    months = {m["month"]: m for m in out["months"]}
    # every censused bill placed on the timeline via the number->month curve; in this
    # fixture the sample covers the whole population, so volume == census counts.
    assert (months["2025-01"]["volume_total"], months["2025-01"]["volume_summarized"]) == (3, 1)
    assert (months["2025-02"]["volume_total"], months["2025-02"]["volume_summarized"]) == (3, 3)
    assert (months["2025-03"]["volume_total"], months["2025-03"]["volume_summarized"]) == (4, 2)
    # volume is the full population (10 bills), not the sample
    assert sum(m["volume_total"] for m in out["months"]) == 10
    assert sum(m["volume_summarized"] for m in out["months"]) == 6
    assert "volume_method" in out
