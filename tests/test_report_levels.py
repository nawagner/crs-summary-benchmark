"""Tests for src/report.py's per-candidate entry construction (issue #10 reading levels
+ issue #7 strategy/n_chunks pass-through + FK grade). Exercises `candidate_entry`
directly with fixture summary/score records written into a tmp SUMMARIES_DIR, rather
than driving `main()` end-to-end."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import common as C  # noqa: E402
import report as R  # noqa: E402
import summarize as S  # noqa: E402

SLUG = "test__model"
MODEL_ID = "test/model"
BILL_ID = "119-hr-1"


def _bill(**over):
    b = {"bill_id": BILL_ID, "crs_summary": "The bill does things.", "title": "Test Act"}
    b.update(over)
    return b


def _score(**over):
    s = {"meets_standard": True, "n_passed": 3, "n_applicable": 3,
         "verdicts": {"c1": {"applicable": True, "pass": True}}}
    s.update(over)
    return s


def _cfg(**over):
    cfg = {"prompts": {"summarize": "prompts/summarize.txt"}}
    cfg.update(over)
    return cfg


def _levels_cfg():
    return _cfg(reading_levels=[
        {"id": "eli5", "label": "Grade 5", "prompt": "prompts/summarize_eli5.txt",
         "target_fk_grade": 5},
        {"id": "general", "label": "General public", "prompt": "prompts/summarize.txt",
         "default": True, "target_fk_grade": 11},
    ])


def _write_default_summary(tmp_path, monkeypatch, **rec_over):
    monkeypatch.setattr(C, "SUMMARIES_DIR", tmp_path)
    rec = {"summary": "The cat sat on the mat. The dog ran to the park.",
           "cost_usd": 0.001, "latency_s": 1.2}
    rec.update(rec_over)
    C.write_json(tmp_path / SLUG / f"{BILL_ID}.json", rec)
    return rec


# ------------------------------------------------------------- default-only candidate
def test_default_summary_only_has_fk_grade_no_levels(tmp_path, monkeypatch):
    _write_default_summary(tmp_path, monkeypatch)
    cfg = _cfg()  # no reading_levels configured
    levels = S.reading_levels(cfg)
    entry = R.candidate_entry(SLUG, MODEL_ID, _bill(), _score(), cfg, levels)

    assert "fk_grade" in entry
    assert entry["fk_grade"] is not None
    assert "levels" not in entry
    assert entry["summary"] == "The cat sat on the mat. The dog ran to the park."


# -------------------------------------------------------------------- eli5 level file
def test_eli5_level_file_present_populates_levels(tmp_path, monkeypatch):
    _write_default_summary(tmp_path, monkeypatch)
    cfg = _levels_cfg()
    levels = S.reading_levels(cfg)
    eli5_level = next(lvl for lvl in levels if lvl["id"] == "eli5")
    eli5_path = S.summary_path(SLUG, BILL_ID, eli5_level)
    C.write_json(eli5_path, {"summary": "The bill helps kids. It is easy to read.",
                             "cost_usd": 0.0005, "latency_s": 0.8})

    entry = R.candidate_entry(SLUG, MODEL_ID, _bill(), _score(), cfg, levels)

    assert "levels" in entry
    assert set(entry["levels"].keys()) == {"eli5"}
    lvl_out = entry["levels"]["eli5"]
    assert lvl_out["summary"] == "The bill helps kids. It is easy to read."
    assert lvl_out["fk_grade"] is not None
    assert lvl_out["cost_usd"] == 0.0005
    assert lvl_out["latency_s"] == 0.8
    # the default level itself must never show up inside "levels"
    assert "general" not in entry["levels"]


def test_missing_level_file_omits_levels_key(tmp_path, monkeypatch):
    _write_default_summary(tmp_path, monkeypatch)
    cfg = _levels_cfg()
    levels = S.reading_levels(cfg)
    # no eli5 file written
    entry = R.candidate_entry(SLUG, MODEL_ID, _bill(), _score(), cfg, levels)
    assert "levels" not in entry


def test_level_file_with_empty_summary_excluded(tmp_path, monkeypatch):
    _write_default_summary(tmp_path, monkeypatch)
    cfg = _levels_cfg()
    levels = S.reading_levels(cfg)
    eli5_level = next(lvl for lvl in levels if lvl["id"] == "eli5")
    C.write_json(S.summary_path(SLUG, BILL_ID, eli5_level),
                 {"summary": "", "cost_usd": 0.0, "latency_s": 0.0})

    entry = R.candidate_entry(SLUG, MODEL_ID, _bill(), _score(), cfg, levels)
    assert "levels" not in entry


# ---------------------------------------------------------------------- crs_reference
def test_crs_reference_has_fk_grade_never_levels(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "SUMMARIES_DIR", tmp_path)  # no summary file for crs_reference
    cfg = _levels_cfg()
    levels = S.reading_levels(cfg)
    bill = _bill(crs_summary="The Secretary shall report annually. Funds are authorized.")

    entry = R.candidate_entry(C.CRS_REFERENCE, None, bill, _score(), cfg, levels)

    assert entry["summary"] == bill["crs_summary"]
    assert "fk_grade" in entry
    assert entry["fk_grade"] is not None
    assert "levels" not in entry


# --------------------------------------------------------- strategy / n_chunks pass-through
def test_strategy_and_n_chunks_pass_through_when_present(tmp_path, monkeypatch):
    _write_default_summary(tmp_path, monkeypatch, strategy="map_reduce", n_chunks=23)
    cfg = _cfg()
    levels = S.reading_levels(cfg)
    entry = R.candidate_entry(SLUG, MODEL_ID, _bill(), _score(), cfg, levels)

    assert entry["strategy"] == "map_reduce"
    assert entry["n_chunks"] == 23


def test_strategy_and_n_chunks_absent_in_old_style_record(tmp_path, monkeypatch):
    _write_default_summary(tmp_path, monkeypatch)  # no strategy/n_chunks keys
    cfg = _cfg()
    levels = S.reading_levels(cfg)
    entry = R.candidate_entry(SLUG, MODEL_ID, _bill(), _score(), cfg, levels)

    assert "strategy" not in entry
    assert "n_chunks" not in entry


# ------------------------------------------------------------------------- fk_grade
def test_fk_grade_of_empty_summary_is_none(tmp_path, monkeypatch):
    _write_default_summary(tmp_path, monkeypatch, summary="")
    cfg = _cfg()
    levels = S.reading_levels(cfg)
    entry = R.candidate_entry(SLUG, MODEL_ID, _bill(), _score(), cfg, levels)

    assert entry["summary"] == ""
    assert entry["fk_grade"] is None


def test_gen_meta_passes_through_strategy_n_chunks(tmp_path, monkeypatch):
    _write_default_summary(tmp_path, monkeypatch, strategy="single")
    meta = R.gen_meta(SLUG, BILL_ID)
    assert meta["strategy"] == "single"
    assert "n_chunks" not in meta


# ---- pavement plot domain robustness (outlier-resistant axis) ----
def _pav_bills(lengths_by_cid):
    """Build minimal bills_out where each candidate has one bill per length given."""
    bills = []
    n = max(len(v) for v in lengths_by_cid.values())
    for i in range(n):
        cands = {}
        for cid, lens in lengths_by_cid.items():
            if i < len(lens):
                cands[cid] = {"summary": "w " * lens[i]}
        bills.append({"candidates": cands})
    return bills


def test_pavement_domain_ignores_giant_outlier():
    # one 16k-word CRS summary must not blow out the shared axis or crowd the ticks.
    # Realistic sample size (like a real dataset), so the p90 domain excludes the outlier.
    ordered = [{"id": "anthropic__m", "label": "A", "is_human": False},
               {"id": "crs_reference", "label": "CRS", "is_human": True}]
    lengths = {"anthropic__m": [300, 340, 380, 420, 460, 500, 540, 580, 600, 320],
               "crs_reference": [80, 110, 140, 180, 220, 260, 300, 340, 370, 16530]}
    html = R.build_pavement_html(ordered, _pav_bills(lengths))
    # axis clipped to a robust ~p90 value, not 16k; few ticks, not ~80
    assert "axis clipped at" in html
    assert "16,530" not in html.split("words per summary")[1]  # not a tick label
    n_ticks = html.count("translateX(-50%)")
    assert n_ticks <= 8
    # the outlier lane keeps its true range + an off-scale marker
    assert "80&ndash;16,530w" in html
    assert 'class="pv-off"' in html


def test_pavement_no_clip_note_when_all_in_domain():
    ordered = [{"id": "anthropic__m", "label": "A", "is_human": False}]
    lengths = {"anthropic__m": [180, 210, 250, 300]}
    html = R.build_pavement_html(ordered, _pav_bills(lengths))
    assert "axis clipped" not in html
    assert 'class="pv-off"' not in html
