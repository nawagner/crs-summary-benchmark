# Changelog

All notable changes to this project are documented here. Dates are UTC.

## [Unreleased] — branch `claude/open-issues-plan-8syw76` (PR #11)

Closes the five open enhancement issues (#6–#10). Every feature ships with **real
generated data**, not just code: the lag census was run against the live Congress.gov
API, committee references were fetched, and ~1,000 reading-level summaries plus a fully
judged 10-bill priority dataset were generated via OpenRouter. Total generation spend
**~$54** (reading levels ~$12, priority-set summaries ~$42); all judging was free via
parallel Claude Code subagents. 137 offline tests pass (`pytest tests/`).

### #6 — Lag page: CRS coverage by legislative stage

- **New `src/stages.py`**: pure, pattern-table classifier of a bill's furthest stage
  (`introduced` / `committee` / `floor`) from Congress.gov action records.
- `src/analyze_lag.py` now runs a **full census** of every 119th-Congress House/Senate
  bill's latest action (the random sample alone was too sparse to measure floor bills),
  plus per-stage coverage and a census-vs-full-history classifier agreement check. Added
  `--out` and an offline `--fixture` dry-run mode.
- `src/plot_lag.py` overlays an "advanced bills" coverage line on the monthly chart;
  `docs/lag.html` and `initLag()` reframe the headline around coverage of bills that move.
- **Real data (live API, 1,098 bills sampled):** floor **95.2%** (601/631), committee
  **35.2%** (471/1,336), introduced-only **25.3%** (3,174/12,562); classifiers agree
  **91.6%**. Confirms the issue's thesis: the ~29% overall figure is dominated by bills
  that never advance.

### #7 — Priority dataset: appropriations / NDAA-scale long bills

- **New `src/chunking.py`**: structure-aware splitting (DIVISION > TITLE > SECTION) into
  model-sized chunks with a lossless partition invariant and a deterministic section index.
- **New `src/grounding.py`**: lexical retrieval that grounds the judge on long bills with
  the section index plus the summary-relevant excerpts (packed under `judge_text_char_cap`).
- **New `config-priority.yaml`** (`CRS_DATASET=priority`): an audited list of 118th-Congress
  appropriations bills, both NDAAs, the FRA, and the security supplemental. `fetch_bills.py`
  gains `select_by_priority` + `allow_long_text` (stores full text instead of skipping
  over-cap bills). Map/merge prompts (`prompts/summarize_chunk.txt`, `summarize_merge.txt`)
  and a long-bill judge prompt (`prompts/judge_long.txt`).
- `src/summarize.py` dispatches single-shot vs. map-reduce; `evaluate.py` gains
  `grounded_bill_text()`; `build_judge_packets.py` / `apply_subagent_verdicts.py` carry
  `judge_grounding` through the free subagent-judging path.
- **Real data:** 10 bills (up to 2.9M chars) × 5 models generated (35 map-reduce, 15
  single-shot), then **judged by 10 parallel Opus Claude Code subagents** — sectional
  grounding for the 4 largest bills, full text otherwise. Unlike the near-100% scores on
  the smaller datasets, this produced a **differentiated leaderboard**: opus/gpt-5.5 80%,
  gemini/glm 60%, deepseek 50%, and the human CRS reference itself only 60% — because
  missing whole divisions of an omnibus is a real, common failure at this scale.
  Concrete judge findings: all 5 models state the NDAA authorizes 13 Virginia-class
  submarines (enacted: 10); DeepSeek fabricated Section 8062 of the DOD appropriations
  bill; all 5 models omit the Ukraine/Indo-Pacific/TikTok divisions of the security
  supplemental that only CRS covers.

### #8 — Committee & CBO reference baselines (research spike)

- **Research memo** at `research/issue-8-committee-cbo-spike.md`.
- **New `src/fetch_references.py`**: fetches committee-report "Purpose and Summary"
  sections and CBO cost-estimate summaries; fixture-tested pure extractors.
- `report.py` emits **unscored** reference candidates (no `verdicts`); `showDetail()`
  renders them as "reference — not judged" cards, excluded from the leaderboard/filters.
- **Real data:** **19** real committee-report cards fetched for the 2024 set. CBO
  remained 0/50 — cbo.gov serves a DataDome bot-block (403) to the fetcher (documented).

### #9 — Perspectives: sponsor vs. opponent framing

- **New `src/build_perspectives.py`** validates/compiles hand-curated `perspectives.yaml`
  → `docs/data/perspectives.json`; `showDetail()` shows a two-column panel under a banner
  marking it partisan framing outside the graded benchmark (verbatim quotes + links only).
- **Real data:** 12 independently source-verified quotes across 3 bills — KOSA
  (`118-s-2073`), the DC criminal-code disapproval (`118-hjres-26`), and FY25 MilCon-VA
  (`118-hr-8580`).

### #10 — Adjustable reading levels (ELI5 ↔ JD)

- **New `src/readability.py`**: pure-Python Flesch-Kincaid grading. **New `src/summarize.py`**
  factored out of `run_models.py` (the seam #7's map-reduce plugs into). `run_models.py`
  gains `--levels`; new `prompts/summarize_eli5.txt` / `summarize_expert.txt`.
- `report.py` emits `fk_grade` for every summary (incl. CRS) plus per-level variants;
  `showDetail()` gains a reading-level toggle + FK chips; methodology page lists the prompts.
- Only the default level is judged; FK-vs-target is the objective proxy.
- **Real data:** 500 summaries per dataset (5 models × 2 extra levels × 50 bills) for both
  the 119th and 2024 corpora — every candidate now has real ELI5 and Expert variants with
  genuine FK-grade separation (~grade 5–8 vs. ~13–14).

### Testing & docs

- New `tests/` suite (10 modules) covering the classifier, chunking (lossless coverage),
  grounding, readability, extractors, perspectives validator, and stub-client map-reduce
  with resume; fixtures for committee reports, CBO pages, and API responses.
- README "Regenerating data (requires API keys)" runbook; `requirements-dev.txt`.

### Data-integrity notes (surfaced during the real-data runs)

- **`118-s-2073` vehicle text-swap:** its `bill_text`/title are the KOSA substitute, but
  its `crs_summary` and committee report are the *original* "Eliminate Useless Reports
  Act" vehicle. The mismatched committee reference was excluded; the stale `crs_summary`
  is a pre-existing corpus quirk flagged for the maintainer.
- **`118-hr-5009` / `118-hr-815` CRS mismatches:** noted by the judges (an NDAA whose CRS
  baseline describes the WILD Act; a supplemental whose CRS text differs) — flagged, not
  silently corrected.
- **One judge hallucination caught:** the first pass on `118-hr-815` falsely claimed its
  packet text was the NDAA; verified false by direct inspection, discarded, and re-judged.
