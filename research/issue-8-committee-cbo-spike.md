# Issue #8 spike: committee & CBO summaries as additional reference baselines

**Question:** can committee-report summaries and CBO cost-estimate summaries join the CRS summary
as human reference points on the bill pages — and eventually as judged baselines?

**Answer: yes, feasibly, as unscored reference cards first.** The data paths exist and are
API-discoverable per bill; coverage is partial by construction (only reported / scored bills), so
these are drill-down references and comparison views, not leaderboard rows. Tooling shipped with
this spike: `src/fetch_references.py` (prototype fetcher + fixture-tested extractors),
`report.py` support for unscored reference candidates, and reference-card rendering on the site.

## Data paths (verified against the Congress.gov API docs)

- **Bill detail** `GET /v3/bill/{congress}/{type}/{number}` includes both hooks:
  - `committeeReports`: list of `{citation, url}` where `url` points directly at the API's
    committee-report resource (e.g. `.../committee-report/117/HRPT/89`).
  - `cboCostEstimates`: list of `{pubDate, title, url, description}` where `url` is the cbo.gov
    publication page (e.g. `https://www.cbo.gov/publication/57356`).
- **Committee report text** `GET /v3/committee-report/{congress}/{HRPT|SRPT|ERPT}/{number}/text`
  returns `textVersions` with "Formatted Text" (HTML) and PDF formats. The HTML is the
  GovInfo `CRPT` document; House reports carry a conventional **"PURPOSE AND SUMMARY"** section
  (sometimes numbered, e.g. "I. PURPOSE AND SUMMARY"), followed by predictable next headings
  ("BACKGROUND AND NEED FOR LEGISLATION", "HEARINGS", "COMMITTEE CONSIDERATION", …). Senate
  reports are less standardized (often "PURPOSE" or an untitled opening) — expect lower extraction
  yield there.
- **CBO** has no clean API. The cbo.gov publication page carries a usable summary in its
  `description`/`og:description` meta tags, with the page body (lede paragraphs) as fallback;
  the PDF is not needed for a summary-length excerpt.

## Extraction reliability

`src/fetch_references.py` ships two pure, fixture-tested extractors:
- `extract_purpose_and_summary(report_html)` — heading-anchored slice over `html_to_text` output,
  terminated by the next heading-like line; returns None below a 100-char floor.
- `extract_cbo_summary(page_html)` — meta-description first, lede-paragraph fallback.
See `tests/test_references.py` (fixtures under `tests/fixtures/references/`) for the exact
behavior contract. The committee-report fixtures are **real GovInfo pages** (H. Rept. 118-50
with a Purpose and Summary section; H. Rept. 118-1 without one, correctly yielding None). The
CBO fixtures are synthetic replicas of cbo.gov's markup: **cbo.gov serves a DataDome bot
challenge (HTTP 403) to non-browser clients**, so the fetcher's CBO leg may need a browser-like
client or manual download — validate against a real page as a first live-run step. Residual risk
otherwise sits in GovInfo markup drift and Senate-report heterogeneity — both contained because
the fetcher records source URLs, so every extraction is auditable.

## Coverage expectations

Only bills that were **reported** have committee reports, and only bills that were **scored** have
CBO estimates. In the 119th 50-bill set (mixed activity) expect low coverage; in the 2024
high-activity set (bills selected for movement) expect substantially higher. The fetcher prints an
exact per-corpus tally (`committee: X/50, cbo: Y/50`) — run it once per dataset to replace these
expectations with numbers:

```bash
python src/fetch_references.py                                     # 119th
CRS_DATASET=2024 CRS_CONFIG=config-2024.yaml python src/fetch_references.py
```

## What ships now vs. later

- **Now (this spike):** unscored reference cards. `report.py` emits `committee_reference` /
  `cbo_reference` entries in `bills[].candidates` (with `reference_kind`, `source_url`, `fk_grade`,
  and no `verdicts`); the bill drill-down renders them as "reference — not judged" cards. They are
  excluded from the leaderboard, filters, and pass/fail dots.
- **Deferred (recommendation):** judging them. The existing rubric would work unchanged — they'd be
  graded against the bill text exactly like `crs_reference` — and that comparison (committee's own
  framing vs. CRS neutrality, e.g. on `objective_tone`) is genuinely interesting. Judge cost is the
  only reason to defer: it adds up to two more candidates × bills × judge calls. If pursued, add
  them to the `candidates` list in `evaluate.py` behind a config flag, and give them their own
  leaderboard section (their partial coverage makes the headline metric non-comparable).

## Go/no-go recommendation

**Go**, scoped: run the fetcher on the 2024 set first (highest expected coverage), eyeball the
extracted sections against their source URLs for ~10 bills, then commit the references dir and
regenerate `results-2024.json`. Revisit judged baselines only after the extraction quality is
spot-checked. GovInfo-direct fetching (bypassing the API's committee-report hop) is not needed —
the API route is simpler and carries the citation metadata.
