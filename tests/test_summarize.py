"""Stub-client tests for summarize.py: reading levels, paths, single-shot vs map-reduce
dispatch, chunk-note caching/resume, and cost accounting. No network."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import common as C  # noqa: E402
import summarize as S  # noqa: E402

PRICING = {"test/model": (0.000001, 0.000002)}


class StubClient:
    """OpenAI-SDK-shaped stub: records prompts, returns canned text."""

    def __init__(self, reply="STUB SUMMARY"):
        self.prompts: list[str] = []
        self.reply = reply
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, model, messages, temperature, max_tokens):
        self.prompts.append(messages[0]["content"])
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.reply))],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=50),
        )


def _cfg(**over):
    cfg = {"prompts": {"summarize": "prompts/summarize.txt"},
           "summarize_temperature": 0.3, "max_output_tokens": 1000}
    cfg.update(over)
    return cfg


def _bill(text="A short bill about apples.", bid="119-hr-1"):
    return {"bill_id": bid, "title": "Apple Act", "bill_text": text}


# ---------------------------------------------------------------- reading levels
def test_reading_levels_default_fallback():
    levels = S.reading_levels(_cfg())
    assert len(levels) == 1
    assert levels[0]["id"] == "general" and levels[0]["default"]
    assert levels[0]["prompt"] == "prompts/summarize.txt"


def test_reading_levels_configured():
    cfg = _cfg(reading_levels=[
        {"id": "eli5", "label": "Grade 5", "prompt": "prompts/summarize_eli5.txt"},
        {"id": "general", "label": "General", "prompt": "prompts/summarize.txt", "default": True},
    ])
    levels = S.reading_levels(cfg)
    assert [lvl["id"] for lvl in levels] == ["eli5", "general"]
    assert S.default_level(levels)["id"] == "general"


def test_reading_levels_first_becomes_default_when_none_marked():
    cfg = _cfg(reading_levels=[{"id": "a", "prompt": "p"}, {"id": "b", "prompt": "p"}])
    assert S.default_level(S.reading_levels(cfg))["id"] == "a"


def test_summary_path_default_is_backcompat(monkeypatch, tmp_path):
    monkeypatch.setattr(C, "SUMMARIES_DIR", tmp_path)
    dflt = {"id": "general", "default": True}
    other = {"id": "eli5"}
    assert S.summary_path("m", "119-hr-1", dflt) == tmp_path / "m" / "119-hr-1.json"
    assert S.summary_path("m", "119-hr-1", other) == tmp_path / "m" / "119-hr-1.eli5.json"


# ------------------------------------------------------------------- single-shot
def test_single_shot(monkeypatch, tmp_path):
    monkeypatch.setattr(C, "SUMMARIES_DIR", tmp_path)
    client = StubClient()
    level = S.reading_levels(_cfg())[0]
    rec = S.generate_summary(client, "test/model", _bill(), level, _cfg(), PRICING)
    assert rec["ok"] and rec["summary"] == "STUB SUMMARY"
    assert rec["strategy"] == "single" and rec["level"] == "general"
    assert "A short bill about apples." in client.prompts[0]
    assert rec["cost_usd"] == round(100 * 0.000001 + 50 * 0.000002, 6)
    assert rec["total_tokens"] == 150


# -------------------------------------------------------------------- map-reduce
def _long_bill():
    parts = []
    for d in "AB":
        parts.append(f"DIVISION {d}--THINGS {d}\n")
        for s in range(1, 4):
            parts.append(f"SEC. {s}0{1 if d == 'A' else 2}. Provision.\n" + ("word " * 120) + "\n")
    return _bill(text="".join(parts), bid="118-hr-9999")


def test_map_reduce_chunks_cached_and_resumable(monkeypatch, tmp_path):
    monkeypatch.setattr(C, "SUMMARIES_DIR", tmp_path)
    cfg = _cfg(single_shot_char_cap=500, chunk_char_budget=1200,
               merge_summary_words="300-700")
    bill = _long_bill()
    level = S.reading_levels(cfg)[0]

    client = StubClient()
    rec = S.generate_summary(client, "test/model", bill, level, cfg, PRICING)
    assert rec["ok"] and rec["strategy"] == "map_reduce"
    n = rec["n_chunks"]
    assert n >= 2
    assert len(client.prompts) == n + 1  # n map calls + 1 merge
    assert "300-700" in client.prompts[-1]  # word target reaches the merge prompt

    cache = C.read_json(S.chunk_cache_path("test__model", bill["bill_id"]))
    assert cache["n_chunks"] == n and len(cache["notes"]) == n

    # a second level reuses every cached chunk note: only the merge call happens,
    # but the record still carries the full (cached) map cost.
    client2 = StubClient()
    level2 = {"id": "eli5", "prompt": "prompts/summarize_eli5.txt"}
    rec2 = S.generate_summary(client2, "test/model", bill, level2, cfg, PRICING)
    assert len(client2.prompts) == 1
    assert rec2["level"] == "eli5" and rec2["strategy"] == "map_reduce"
    assert rec2["prompt_tokens"] == rec["prompt_tokens"]


def test_map_reduce_partial_resume(monkeypatch, tmp_path):
    monkeypatch.setattr(C, "SUMMARIES_DIR", tmp_path)
    cfg = _cfg(single_shot_char_cap=500, chunk_char_budget=1200)
    bill = _long_bill()
    level = S.reading_levels(cfg)[0]

    # first run to learn the chunk count, then delete one note to simulate a crash
    probe = StubClient()
    n = S.generate_summary(probe, "test/model", bill, level, cfg, PRICING)["n_chunks"]
    cache_path = S.chunk_cache_path("test__model", bill["bill_id"])
    cache = C.read_json(cache_path)
    del cache["notes"]["0"]
    C.write_json(cache_path, cache)

    client = StubClient()
    rec = S.generate_summary(client, "test/model", bill, level, cfg, PRICING)
    assert rec["ok"]
    assert len(client.prompts) == 2  # the one missing chunk + the merge
    assert C.read_json(cache_path)["n_chunks"] == n
