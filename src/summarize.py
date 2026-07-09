"""Summary-generation core shared by run_models.py.

Two concerns live here:
- Reading levels (issue #10): a bill can be summarized at several registers (ELI5 /
  general / expert), each a prompt variant configured under `reading_levels` in the
  config. Absent that key, behavior is exactly the historical single-prompt run.
- Long bills (issue #7): bills whose text exceeds `single_shot_char_cap` are summarized
  hierarchically — structure-aware chunks become dense, register-neutral working notes
  (cached per model and shared across reading levels), then one merge call writes the
  final summary in the requested register.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import common as C

DEFAULT_LEVEL_ID = "general"
DEFAULT_LEVEL_LABEL = "General public"


# ------------------------------------------------------------------ reading levels
def reading_levels(cfg: dict) -> list[dict]:
    """Configured reading levels; without `reading_levels` in the config, a single
    default level using the historical `prompts.summarize` prompt."""
    lvls = cfg.get("reading_levels")
    if not lvls:
        return [{"id": DEFAULT_LEVEL_ID, "label": DEFAULT_LEVEL_LABEL,
                 "prompt": cfg["prompts"]["summarize"], "default": True}]
    lvls = [dict(lvl) for lvl in lvls]
    if not any(lvl.get("default") for lvl in lvls):
        lvls[0]["default"] = True
    return lvls


def default_level(levels: list[dict]) -> dict:
    for lvl in levels:
        if lvl.get("default"):
            return lvl
    return levels[0]


def summary_path(slug: str, bill_id: str, level: dict) -> Path:
    """Default level keeps the historical path (back-compat + resumable); other levels
    live alongside as <bill_id>.<level_id>.json."""
    if level.get("default"):
        return C.SUMMARIES_DIR / slug / f"{bill_id}.json"
    return C.SUMMARIES_DIR / slug / f"{bill_id}.{level['id']}.json"


def chunk_cache_path(slug: str, bill_id: str) -> Path:
    return C.SUMMARIES_DIR / slug / f"{bill_id}.chunks.json"


# ------------------------------------------------------------------------ LLM call
def _call(client, model: str, prompt: str, cfg: dict, pricing,
          max_tokens: int | None = None, retries: int = 2) -> tuple[str, dict]:
    """One chat completion with retry/backoff. Returns (text, usage-meta)."""
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            t0 = time.time()
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=cfg.get("summarize_temperature", 0.3),
                max_tokens=max_tokens or cfg.get("max_output_tokens", 1500),
            )
            latency = time.time() - t0
            text = (resp.choices[0].message.content or "").strip()
            usage = resp.usage
            pt = getattr(usage, "prompt_tokens", 0) or 0
            ct = getattr(usage, "completion_tokens", 0) or 0
            pp, cp = pricing.get(model, (0.0, 0.0))
            return text, {"prompt_tokens": pt, "completion_tokens": ct,
                          "cost_usd": round(pt * pp + ct * cp, 6),
                          "latency_s": round(latency, 3)}
        except Exception as e:  # noqa: BLE001
            last = e
            if attempt < retries:
                time.sleep(2 * (attempt + 1))
    raise last  # type: ignore[misc]


def _zero_meta() -> dict:
    return {"prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0, "latency_s": 0.0}


def _add(total: dict, meta: dict) -> None:
    for k in ("prompt_tokens", "completion_tokens", "cost_usd", "latency_s"):
        total[k] += meta.get(k, 0) or 0


def _finish(summary: str, total: dict, **extra: Any) -> dict:
    return {
        "summary": summary,
        "prompt_tokens": total["prompt_tokens"],
        "completion_tokens": total["completion_tokens"],
        "total_tokens": total["prompt_tokens"] + total["completion_tokens"],
        "cost_usd": round(total["cost_usd"], 6),
        "latency_s": round(total["latency_s"], 3),
        "ok": bool(summary),
        **extra,
    }


# --------------------------------------------------------------------- generation
def generate_summary(client, model: str, bill: dict, level: dict, cfg: dict, pricing,
                     retries: int = 2) -> dict:
    """Summarize one bill at one reading level. Dispatches single-shot vs map-reduce
    on `single_shot_char_cap` (default: the corpus `text_char_cap`, i.e. 180k)."""
    text = bill["bill_text"]
    cap = int(cfg.get("single_shot_char_cap", cfg.get("text_char_cap", 180000)))
    if len(text) <= cap:
        template = C.read_prompt(level["prompt"])
        prompt = template.replace("{bill_text}", text)
        summary, meta = _call(client, model, prompt, cfg, pricing, retries=retries)
        total = _zero_meta()
        _add(total, meta)
        return _finish(summary, total, strategy="single", level=level["id"])
    return _map_reduce(client, model, bill, level, cfg, pricing, cap, retries)


def _map_reduce(client, model: str, bill: dict, level: dict, cfg: dict, pricing,
                cap: int, retries: int) -> dict:
    from chunking import pack_chunks, split_sections  # local: only long-bill datasets need it

    slug = C.model_slug(model)
    budget = int(cfg.get("chunk_char_budget", 120000))
    sections = split_sections(bill["bill_text"])
    chunks = pack_chunks(sections, budget)
    total = _zero_meta()

    # --- map: register-neutral working notes per chunk, cached for resume and shared
    # across reading levels (the register only enters at the merge step).
    cache_file = chunk_cache_path(slug, bill["bill_id"])
    cache = C.read_json(cache_file) if cache_file.exists() else {}
    if cache.get("n_chunks") != len(chunks):
        cache = {"bill_id": bill["bill_id"], "model": model,
                 "n_chunks": len(chunks), "notes": {}}
    chunk_template = C.read_prompt(cfg.get("prompts", {}).get(
        "summarize_chunk", "prompts/summarize_chunk.txt"))
    for i, ch in enumerate(chunks):
        rec = cache["notes"].get(str(i))
        if rec:
            _add(total, rec)
            continue
        prompt = (chunk_template
                  .replace("{part_i}", str(i + 1))
                  .replace("{part_n}", str(len(chunks)))
                  .replace("{bill_title}", bill.get("title", ""))
                  .replace("{heading_path}", " > ".join(ch.heading_from) or "start of bill")
                  .replace("{chunk_text}", ch.text))
        notes, meta = _call(client, model, prompt, cfg, pricing,
                            max_tokens=cfg.get("chunk_max_output_tokens", 1200),
                            retries=retries)
        cache["notes"][str(i)] = {"notes": notes, **meta}
        C.write_json(cache_file, cache)  # persist after every chunk: resume matters here
        _add(total, meta)

    labeled = [f"[Part {i + 1}/{len(chunks)} · {' > '.join(chunks[i].heading_from) or 'start'}]\n"
               f"{cache['notes'][str(i)]['notes']}" for i in range(len(chunks))]

    # --- reduce: merge notes into the final summary in this level's register. A level
    # may name its own merge prompt (`merge_prompt`); otherwise the shared default.
    merge_template = C.read_prompt(level.get("merge_prompt") or cfg.get("prompts", {}).get(
        "summarize_merge", "prompts/summarize_merge.txt"))
    word_target = str(cfg.get("merge_summary_words", "300-700"))

    def merge_call(notes_blocks: list[str], target: str) -> str:
        prompt = (merge_template
                  .replace("{bill_title}", bill.get("title", ""))
                  .replace("{word_target}", target)
                  .replace("{chunk_notes}", "\n\n".join(notes_blocks)))
        out, meta = _call(client, model, prompt, cfg, pricing, retries=retries)
        _add(total, meta)
        return out

    # recursive reduce: if the concatenated notes overflow the single-shot cap, merge
    # them in groups into intermediate digests first (rare at realistic sizes).
    while len("\n\n".join(labeled)) > cap and len(labeled) > 1:
        groups: list[list[str]] = [[]]
        size = 0
        for block in labeled:
            if size + len(block) > cap and groups[-1]:
                groups.append([])
                size = 0
            groups[-1].append(block)
            size += len(block)
        labeled = [merge_call(g, "500-900") for g in groups]

    summary = merge_call(labeled, word_target)
    return _finish(summary, total, strategy="map_reduce", n_chunks=len(chunks),
                   level=level["id"])
