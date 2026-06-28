# CRS Summary Benchmark

A transparent, reproducible benchmark that measures how well today's frontier LLMs can summarize
U.S. legislative bills — graded against the **real summaries written by the Congressional Research
Service (CRS)**.

## Why

In congressional testimony, CRS reported that across ~1,000 bills tested in 2024, **fewer than 3%**
of AI-generated summaries met its standards for accuracy, coherence, relevance, and objectivity. But
CRS never published the bills, the grading rubric, the prompts, or the model versions — as
[this article](https://marcidale.substack.com/p/crs-said-ai-met-its-standards-less) argues, that
makes the finding impossible to scrutinize or reproduce, especially as models improve.

This project is an open answer to that: pick real bills with genuine CRS summaries, run current
frontier models on them under a fixed prompt, and grade every summary against an **inspectable
checklist of binary criteria** derived from the CRS standards. The human CRS summaries are graded on
the same checklist as a calibration baseline. Results — quality, **cost**, and **latency** — are
published to a static site where anyone can audit every bill and verdict.

## What it measures

- **Models:** Claude Opus 4.8, GPT-5.5, Gemini 3.5-flash, GLM 5.2, DeepSeek V4-Pro (via OpenRouter).
- **Baseline:** the real CRS summary for each bill, graded on the same rubric (the `CRS (human)` row).
- **Headline metric:** % of summaries that pass **every applicable** binary criterion.
- Plus per-criterion pass rates, mean cost/summary, and mean latency.

## How it works

```
fetch_bills.py   Congress.gov API → data/bills/*.json   (bill text + real CRS summary)
run_models.py    each model summarizes each bill → results/summaries/<model>/*.json
evaluate.py      LLM judge grades every summary + the CRS summary → results/scores/<...>/*.json
report.py        → results/leaderboard.md, results/scores.csv, docs/data/results.json (powers the site)
```

The Congress.gov data layer reuses the importable client from
[`nawagner/congress-mcp`](https://github.com/nawagner/congress-mcp).

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill in both keys
```

`.env` needs two keys (it is gitignored):

- `CONGRESS_API_KEY` — free, from <https://api.congress.gov/sign-up/>
- `OPENROUTER_API_KEY` — from <https://openrouter.ai/keys>

## Run

```bash
python src/fetch_bills.py            # collect ~50 bills (configurable in config.yaml)
python src/run_models.py             # generate summaries with every model
python src/evaluate.py               # judge every summary on the binary criteria
python src/report.py                 # aggregate + build docs/data/results.json
```

Each step is **resumable** — re-running skips work already on disk. Use `--limit N` on any step for a
quick smoke test. Preview the site locally:

```bash
python -m http.server -d docs 8000   # → http://localhost:8000
```

## Configuration

- **`config.yaml`** — models, judge model, congress number, bill types, corpus size, prompts.
- **`criteria.yaml`** — the binary grading criteria. Add, remove, or reword them freely; the judge and
  the site update automatically.
- **`prompts/`** — the exact summarization and judge prompts.

## The criteria

Twelve binary pass/fail checks derived from the CRS standards — accuracy, no hallucinations, states
purpose, describes changes to existing law, exceptions/conditions, effective dates, major provisions
covered, objective tone, coherence, conciseness, correct entities, correct figures. See
[`criteria.yaml`](criteria.yaml) for the exact wording, and the site's Methodology page for the full
rubric and prompts.

## Caveats

- **LLM-as-judge.** Grading is done by a strong model, which can err and may have mild self-preference
  toward its own lab's model. The `CRS (human)` baseline on the same rubric is the calibration check; the
  judge is one config line to swap, and a judge panel is a natural extension.
- **2024 vs. now.** CRS tested 2024-era models; this benchmark uses current ones, so it is not a
  like-for-like replication of the CRS result — it's a fresh, transparent measurement.
- **Sample size.** ~50 bills by default (CRS used ~1,000). Scale up via `config.yaml`.

## License

MIT. Bill text and CRS summaries are U.S. Government works (public domain) retrieved from the
Congress.gov API.
