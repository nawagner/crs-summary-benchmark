# CRS Summary Benchmark

A transparent, reproducible benchmark that measures how well today's frontier LLMs can summarize
U.S. legislative bills — graded against the **real summaries written by the Congressional Research
Service (CRS)**.

**Live site:** <https://nawagner.github.io/crs-summary-benchmark/>

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

- **Models (summarizers):** Claude Opus 4.8, GPT-5.5, Gemini 3.5-flash, GLM 5.2, DeepSeek V4-Pro
  (all via OpenRouter).
- **Judge:** **`anthropic/claude-opus-4.8`** (Opus 4.8) with live web search, set in `config.yaml`
  (`judge_model`). It scores every summary against the bill text and returns a pass/fail plus a
  one-line reason per criterion. (See *Caveats* for what this implies.)
- **Baseline:** the real CRS summary for each bill, graded on the same rubric (the `CRS (human)` row).
- **Headline metric:** % of summaries that pass **every applicable** binary criterion.
- Plus per-criterion pass rates, mean cost/summary, mean latency, and a summary-length distribution.

## Datasets

Two corpora, switchable with a toggle on the site:

- **119th Congress** — 50 recent bills (2025–26), sampled by most-recently-updated and interleaved
  across chambers. A mix of activity levels.
- **2024 · high-activity** — 50 bills from the 118th Congress (2024), where *"high activity"* means
  the **number of legislative actions** a bill accumulated (introduction, committee markups, floor
  votes, passage, signing, …), read from the Congress.gov `/actions` endpoint as a proxy for how
  consequential a bill was. The selected bills range **27–76 actions** each. Bills whose full text
  exceeds the model input budget (~180k chars) are skipped, so every summary is graded on text the
  models read in full (this excludes the largest omnibus appropriations bills).

## How it works

```
fetch_bills.py   Congress.gov API → data/bills/*.json   (bill text + real CRS summary)
run_models.py    each model summarizes each bill → results/summaries/<model>/*.json
evaluate.py      Opus 4.8 judge (+ web search) grades every summary AND the CRS summary
                 → results/scores/<candidate>/*.json
report.py        → results/leaderboard.md, results/scores.csv, docs/data/results.json (powers the site)
```

The Congress.gov data layer reuses the importable `CongressClient` from
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

The default (119th Congress) dataset:

```bash
python src/fetch_bills.py            # collect ~50 bills (configurable in config.yaml)
python src/run_models.py             # generate summaries with every model
python src/evaluate.py               # Opus 4.8 + web search judges every summary
python src/report.py                 # aggregate + build docs/data/results.json
```

The second (2024 high-activity) dataset uses the same scripts with two environment variables, so it
writes to parallel paths (`data/bills-2024/`, `results-2024/`, `docs/data/results-2024.json`):

```bash
export CRS_DATASET=2024 CRS_CONFIG=config-2024.yaml
python src/fetch_bills.py            # ranks 118th-Congress bills by action count
python src/run_models.py && python src/evaluate.py && python src/report.py
```

Each step is **resumable** — re-running skips work already on disk. Use `--limit N` on any step for a
quick smoke test. Preview the site locally:

```bash
python -m http.server -d docs 8000   # → http://localhost:8000
```

## The website (`docs/`, served via GitHub Pages)

- **Leaderboard** — sortable by quality, cost, and latency, plus a per-criterion pass-rate heatmap and
  a [pavement-plot](https://planspace.org/pavement/) of each summarizer's summary-length distribution.
- **Bills** — every bill, filterable by summarizer / criterion / outcome / type, with a per-bill
  drill-down showing all summaries side by side and the judge's per-criterion verdicts.
- **Methodology** — the criteria, prompts, judge, and caveats, in full.
- **Dataset toggle** — switch between the two datasets on the leaderboard and bills pages.

## Configuration

- **`config.yaml`** / **`config-2024.yaml`** — summarizer models, `judge_model`, congress, bill types,
  corpus size, text cap, and prompts for each dataset.
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

- **The judge is `anthropic/claude-opus-4.8` (Opus 4.8) with web search.** It can err, and as one of
  the summarizers it may have mild self-preference toward the Claude row. The `CRS (human)` baseline on
  the same rubric is a calibration check; the judge is one config line to swap, and a judge panel is a
  natural extension.
- **Not validated against humans.** The pass/fail verdicts have not been compared against human experts
  applying the same criteria, so there is no measure of judge↔human agreement, and close calls are
  genuinely contestable. Treat the scores as one consistent automated rater's opinion and read the
  published per-bill reasons, not just the headline.
- **The judge matters — a lot.** An early pass of the 2024 set graded by a weaker model
  (`claude-sonnet-4-6`) produced a confident but **wrong** leaderboard (it failed correct CRS claims as
  "hallucinations" and over-penalized conciseness). Re-judging with Opus corrected it. Both datasets'
  published scores use Opus 4.8 (the 2024 set was judged by Opus run as parallel
  [Claude Code](https://claude.com/claude-code) subagents to save cost; `evaluate.py` reproduces the
  same model via OpenRouter).
- **2024 vs. now.** CRS tested 2024-era models; this benchmark uses current ones, so it is not a
  like-for-like replication of the CRS result — it's a fresh, transparent measurement.
- **Sample size.** 50 bills per dataset (CRS used ~1,000). Scale up via the config files.

## License

MIT. Bill text and CRS summaries are U.S. Government works (public domain) retrieved from the
Congress.gov API.
