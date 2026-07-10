/* CRS Summary Benchmark — static site logic. Reads docs/data/results.json. */
"use strict";

/* Three datasets; users switch between them. The choice persists in localStorage.
   The "priority" (long-bill) dataset file may not exist yet — see the availability
   probe in renderDatasetToggle below, which only renders buttons for datasets whose
   file actually resolves. */
const DATASETS = [
  { id: "119", file: "data/results.json", short: "119th Congress",
    label: "119th Congress · 2025–26", desc: "Recent bills (mixed activity)." },
  { id: "2024", file: "data/results-2024.json", short: "2024 · high-activity",
    label: "2024 · 118th · high-activity", desc: "The 50 most legislatively-active 2024 bills whose full text fits the model input (omnibus bills excluded)." },
  { id: "priority", file: "data/results-priority.json", short: "Priority · long bills",
    label: "118th · appropriations & NDAA", desc: "Appropriations, the NDAA, and other very large bills — summarized hierarchically (map-reduce over structure-aware chunks); judging grounded in a section index plus the sections most relevant to each summary." },
];
function currentDataset() {
  const id = localStorage.getItem("crs_dataset") || "119";
  return DATASETS.find((d) => d.id === id) || DATASETS[0];
}
function setDataset(id) {
  localStorage.setItem("crs_dataset", id);
  location.reload();
}
/* Probe each dataset file once with HEAD and cache the result on window so index.html
   and bills.html don't each re-probe more than once per page load. */
function probeDatasets() {
  if (window.__crsDatasetProbe) return window.__crsDatasetProbe;
  window.__crsDatasetProbe = Promise.all(
    DATASETS.map((d) =>
      fetch(d.file, { method: "HEAD", cache: "no-store" })
        .then((res) => [d.id, res.ok])
        .catch(() => [d.id, false])
    )
  ).then((pairs) => Object.fromEntries(pairs));
  return window.__crsDatasetProbe;
}
async function renderDatasetToggle(elId) {
  const el = document.getElementById(elId);
  if (!el) return;
  const cur = currentDataset().id;
  const avail = await probeDatasets();
  if (avail[cur] === false) {
    // the persisted choice no longer resolves (e.g. priority dataset not generated yet) — fall back
    localStorage.setItem("crs_dataset", "119");
    location.reload();
    return;
  }
  const visible = DATASETS.filter((d) => d.id === cur || avail[d.id]);
  el.innerHTML = `<div class="ds-toggle">` +
    visible.map((d) => `<button class="${d.id === cur ? "active" : ""}" data-ds="${d.id}">${esc(d.label)}</button>`).join("") +
    `</div><p class="ds-desc">${esc(currentDataset().desc)}</p>`;
  el.querySelectorAll("[data-ds]").forEach((b) =>
    b.addEventListener("click", () => { if (b.dataset.ds !== cur) setDataset(b.dataset.ds); }));
}

const pct = (x) => (x == null ? "—" : Math.round(x * 100) + "%");
const money = (x) => (x == null ? "—" : "$" + x.toFixed(4));
const secs = (x) => (x == null ? "—" : x.toFixed(1) + "s");
const esc = (s) =>
  String(s == null ? "" : s).replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

function heatColor(rate) {
  if (rate == null) return "#eef0f3";
  // red (0) -> yellow (.5) -> green (1)
  const h = 0 + 120 * rate; // 0=red,120=green
  return `hsl(${h}, 62%, ${92 - rate * 14}%)`;
}

async function loadData() {
  const res = await fetch(currentDataset().file, { cache: "no-store" });
  if (!res.ok) throw new Error(currentDataset().file + " not found (run report.py for this dataset)");
  return res.json();
}

function fail(el, err) {
  el.innerHTML = `<div class="empty">Could not load results: ${esc(err.message)}</div>`;
}

/* Perspectives (issue #9): lazily fetched once per page and cached (including a
   null marker on any failure — missing file, non-2xx, bad JSON) so the bill
   detail panel can re-render on every reading-level toggle without re-fetching. */
let perspectivesPromise = null;
function loadPerspectives() {
  if (!perspectivesPromise) {
    perspectivesPromise = fetch("data/perspectives.json", { cache: "no-store" })
      .then((res) => (res.ok ? res.json() : null))
      .catch(() => null);
  }
  return perspectivesPromise;
}

/* ----------------------------------------------------------- index / leaderboard */
async function initIndex() {
  renderDatasetToggle("dataset-toggle");
  const root = document.getElementById("leaderboard");
  let data;
  try { data = await loadData(); } catch (e) { return fail(root, e); }

  document.getElementById("run-meta").textContent =
    `${data.bills.length} bills · ${data.congress}th Congress · judged by ${data.judge_model} · generated ${data.generated_at}`;

  const rows = data.leaderboard;
  // headline leaderboard (sortable)
  const cols = [
    { key: "label", label: "Summarizer", num: false },
    { key: "meets_standard_rate", label: "Passes all criteria", num: true, bar: true,
      hint: "Share of summaries that pass every applicable criterion in this project's CRS-derived rubric. This is our rubric, not an official CRS determination." },
    { key: "mean_cost_usd", label: "Mean cost / summary", num: true, fmt: money },
    { key: "mean_latency_s", label: "Mean latency", num: true, fmt: secs },
  ];
  let sortKey = "meets_standard_rate", sortAsc = false;

  function draw() {
    const sorted = [...rows].sort((a, b) => {
      let av = a[sortKey], bv = b[sortKey];
      if (av == null) av = -1; if (bv == null) bv = -1;
      if (av < bv) return sortAsc ? -1 : 1;
      if (av > bv) return sortAsc ? 1 : -1;
      return 0;
    });
    const head = cols.map((c) =>
      `<th class="sortable ${c.num ? "num" : ""} ${c.key === sortKey ? "sorted " + (sortAsc ? "asc" : "") : ""}" data-k="${c.key}"${c.hint ? ` title="${esc(c.hint)}"` : ""}>${c.label}</th>`
    ).join("");
    const body = sorted.map((r) => {
      const cells = cols.map((c) => {
        if (c.key === "label")
          return `<td class="candidate-name">${esc(r.label)}${r.is_human ? '<span class="tag-human">human</span>' : ""}</td>`;
        if (c.bar) {
          const w = Math.round((r[c.key] || 0) * 100);
          return `<td class="num"><div class="bar"><span style="width:${w}%"></span><em>${pct(r[c.key])} (${r.meets_standard_count}/${r.n_bills})</em></div></td>`;
        }
        const v = c.fmt ? c.fmt(r[c.key]) : r[c.key];
        return `<td class="num">${v}</td>`;
      }).join("");
      return `<tr>${cells}</tr>`;
    }).join("");
    root.innerHTML = `<div class="table-scroll"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
    root.querySelectorAll("th.sortable").forEach((th) =>
      th.addEventListener("click", () => {
        const k = th.dataset.k;
        if (k === sortKey) sortAsc = !sortAsc; else { sortKey = k; sortAsc = false; }
        draw();
      }));
  }
  draw();

  // per-criterion heatmap
  const heat = document.getElementById("heatmap");
  const crits = data.criteria;
  const hhead = `<th>Summarizer</th>` + crits.map((c) => `<th class="num" title="${esc(c.description)}">${esc(c.name)}</th>`).join("");
  const hbody = rows.map((r) => {
    const cells = crits.map((c) => {
      const v = r.per_criterion[c.id];
      return `<td class="heat" style="background:${heatColor(v)}">${pct(v)}</td>`;
    }).join("");
    return `<tr><td class="candidate-name">${esc(r.label)}${r.is_human ? '<span class="tag-human">human</span>' : ""}</td>${cells}</tr>`;
  }).join("");
  heat.innerHTML = `<div class="table-scroll"><table><thead><tr>${hhead}</tr></thead><tbody>${hbody}</tbody></table></div>`;

  renderPavement(data);
}

/* ----------------------------------------------------------- pavement (lengths) */
/* Rendered server-side by the real `pavement` Python library (report.py) and embedded
   in results.json as a self-contained HTML/SVG string; we just inject it. */
function renderPavement(data) {
  const root = document.getElementById("pavement");
  root.innerHTML = data.pavement_html
    ? data.pavement_html
    : '<div class="empty">No summary-length data.</div>';
}

/* --------------------------------------------------------------- bills explorer */
async function initBills() {
  renderDatasetToggle("dataset-toggle");
  const root = document.getElementById("bill-list");
  let data;
  try { data = await loadData(); } catch (e) { return fail(root, e); }

  const crits = data.criteria;
  const candIds = data.leaderboard.map((r) => r.id);
  const candLabel = Object.fromEntries(data.leaderboard.map((r) => [r.id, r.label]));
  const levels = data.reading_levels || [];
  const dfltLevel = levels.find((l) => l.default) || levels[0];

  // populate filter controls
  const modelSel = document.getElementById("f-model");
  modelSel.innerHTML = `<option value="">All summarizers</option>` +
    data.leaderboard.map((r) => `<option value="${r.id}">${esc(r.label)}</option>`).join("");
  const critSel = document.getElementById("f-criterion");
  critSel.innerHTML = `<option value="">Any criterion</option>` +
    crits.map((c) => `<option value="${c.id}">${esc(c.name)}</option>`).join("");
  const typeSel = document.getElementById("f-type");
  const types = [...new Set(data.bills.map((b) => b.type))].sort();
  typeSel.innerHTML = `<option value="">All types</option>` + types.map((t) => `<option>${esc(t)}</option>`).join("");

  const state = { model: "", criterion: "", outcome: "", type: "", q: "" };
  const detail = document.getElementById("bill-detail");
  const listWrap = document.getElementById("list-wrap");

  function billMatches(b) {
    if (state.type && b.type !== state.type) return false;
    if (state.q) {
      const hay = (b.bill_id + " " + b.title).toLowerCase();
      if (!hay.includes(state.q.toLowerCase())) return false;
    }
    if (state.model || state.criterion || state.outcome) {
      const models = state.model ? [state.model] : candIds;
      let any = false;
      for (const m of models) {
        const c = b.candidates[m];
        if (!c) continue;
        if (state.criterion) {
          const v = c.verdicts[state.criterion];
          if (!v || !v.applicable) continue;
          const pass = v.pass;
          if (state.outcome === "pass" && !pass) continue;
          if (state.outcome === "fail" && pass) continue;
          any = true; break;
        } else {
          const meets = c.meets_standard;
          if (state.outcome === "pass" && !meets) continue;
          if (state.outcome === "fail" && meets) continue;
          any = true; break;
        }
      }
      if (!any) return false;
    }
    return true;
  }

  function drawList() {
    const matched = data.bills.filter(billMatches);
    document.getElementById("bill-count").textContent =
      `${matched.length} of ${data.bills.length} bills`;
    if (!matched.length) { root.innerHTML = `<div class="empty">No bills match these filters.</div>`; return; }
    root.innerHTML = matched.map((b) => {
      const dots = candIds.map((m) => {
        const c = b.candidates[m];
        if (!c) return "";
        return `<span class="dot ${c.meets_standard ? "pass" : "fail"}" title="${esc(candLabel[m])}: ${c.meets_standard ? "passed all criteria" : "missed one or more"}"></span>`;
      }).join("");
      const acts = b.actions_count != null
        ? `<span class="actions-badge" title="legislative actions — the activity signal used to pick this set">${b.actions_count} actions</span>` : "";
      const longBadge = b.long_text
        ? `<span class="long-badge" title="full text exceeds the single-prompt budget; summarized hierarchically">long bill</span>` : "";
      return `<div class="bill-row" data-id="${esc(b.bill_id)}">
        <div class="meta">
          <div class="bnum">${esc(b.type.toUpperCase())} ${esc(String(b.number))} · ${esc(b.congress)}th${acts}${longBadge}</div>
          <div class="btitle">${esc(b.title || "(untitled)")}</div>
        </div>
        <div class="mini">${dots}</div>
      </div>`;
    }).join("");
    root.querySelectorAll(".bill-row").forEach((el) =>
      el.addEventListener("click", () => showDetail(el.dataset.id)));
  }

  function verdictRow(c, v) {
    const mark = !v ? "·" : !v.applicable ? "—" : v.pass ? "✓" : "✕";
    const cls = !v ? "na" : !v.applicable ? "na" : v.pass ? "pass" : "fail";
    return `<div class="verdict"><span class="mark ${cls}">${mark}</span>
      <span class="cname">${esc(c.name)}</span>
      <span class="why">${esc(v ? (v.applicable ? v.why : "not applicable to this bill") : "")}</span></div>`;
  }

  function billHasLevelData(b) {
    return candIds.some((m) => {
      const c = b.candidates[m];
      return c && c.levels && Object.keys(c.levels).length > 0;
    });
  }

  function fkChip(fkVal, curLevel) {
    if (fkVal == null) return "";
    const target = curLevel && curLevel.target_fk_grade != null
      ? ` (target for this level: ${curLevel.target_fk_grade})` : "";
    return `<span class="fk-chip" title="Flesch-Kincaid grade level${target}">FK ${Number(fkVal).toFixed(1)}</span>`;
  }

  // Resolves which summary text / fk_grade / fallback note to show for a candidate at a level.
  function levelContent(c, curLevel, isDefault) {
    if (isDefault || !curLevel) return { text: c.summary, note: null, fk: c.fk_grade };
    const lv = c.levels && c.levels[curLevel.id];
    if (lv && lv.summary) return { text: lv.summary, note: null, fk: lv.fk_grade };
    return {
      text: c.summary,
      note: c.is_human ? "human summary — as published" : "not generated at this level",
      fk: null,
    };
  }

  // Reference candidates (issue #8) — e.g. committee_reference / cbo_reference —
  // are entries in b.candidates that never made it into the leaderboard/candIds
  // list and carry no verdicts. They have no `levels`, so unlike scored human
  // baselines their non-default-level note is a fixed "as published" caption.
  function referenceContent(c, isDefault) {
    if (isDefault) return { text: c.summary, note: null, fk: c.fk_grade };
    return { text: c.summary, note: "reference — as published", fk: null };
  }

  function perspKindLabel(kind) {
    return (
      { press_release: "press release", floor_statement: "floor statement",
        dear_colleague: "Dear Colleague letter", op_ed: "op-ed" }[kind] || kind
    );
  }

  function perspCard(e) {
    const metaBits = [esc(perspKindLabel(e.source_kind))];
    if (e.date) metaBits.push(esc(e.date));
    if (e.source_url) metaBits.push(`<a href="${esc(e.source_url)}" target="_blank" rel="noopener">source →</a>`);
    return `<div class="persp-card">
      <blockquote>${esc(e.excerpt)}</blockquote>
      <div class="persp-attr">— ${esc(e.name)}</div>
      <div class="persp-meta">${metaBits.join(" · ")}</div>
    </div>`;
  }

  function perspColumn(entries) {
    if (!entries || !entries.length) return `<div class="persp-empty">none curated yet</div>`;
    return entries.map(perspCard).join("");
  }

  let detailToken = 0;
  async function showDetail(id, levelId) {
    const token = ++detailToken;
    const b = data.bills.find((x) => x.bill_id === id);
    if (!b) return;
    const showLevelToggle = levels.length > 1 && billHasLevelData(b);
    const curLevel = levelId ? (levels.find((l) => l.id === levelId) || dfltLevel) : dfltLevel;
    const isDefault = !curLevel || curLevel === dfltLevel;

    const levelToggleHtml = showLevelToggle ? `
      <div class="lvl-row">
        <div class="ds-toggle lvl-toggle">
          ${levels.map((l) => `<button class="${curLevel && curLevel.id === l.id ? "active" : ""}" data-lvl="${esc(l.id)}">${esc(l.label)}</button>`).join("")}
        </div>
        <span class="lvl-caption">Reading level — same bills, same models, different register. Only the ${esc(dfltLevel.label)} level is judged.</span>
      </div>` : "";

    const cards = candIds.filter((m) => b.candidates[m]).map((m) => {
      const c = b.candidates[m];
      const verdicts = crits.map((cr) => verdictRow(cr, c.verdicts[cr.id])).join("");
      let meta = c.is_human ? "human baseline" : `${money(c.cost_usd)} · ${secs(c.latency_s)}`;
      if (c.strategy === "map_reduce" && c.n_chunks != null) meta += ` · map-reduce over ${c.n_chunks} chunks`;
      if (c.judge_grounding === "sectional") meta += ` · judged on section index + relevant excerpts`;
      const { text, note, fk } = levelContent(c, curLevel, isDefault);
      const noteHtml = note ? `<div class="lvl-note">${esc(note)}</div>` : "";
      const judgedNote = !isDefault
        ? `<div class="lvl-judged-note">criteria were judged at the ${esc(dfltLevel.label)} level</div>` : "";
      return `<div class="summary-card ${c.is_human ? "human" : ""}">
        <header>
          <h4>${esc(c.label)}</h4>
          <span class="header-badges">${fkChip(fk, curLevel)}<span class="scorebadge ${c.meets_standard ? "meets" : "misses"}">${c.meets_standard ? "Passed all" : c.n_passed + "/" + c.n_applicable}</span></span>
        </header>
        <div class="body">${esc(text)}</div>
        ${noteHtml}
        <div class="verdicts">${judgedNote}${verdicts}<div style="margin-top:8px;color:var(--muted);font-size:12px">${meta}</div></div>
      </div>`;
    }).join("");

    // Unscored reference cards (issue #8): candidates present on the bill but
    // absent from the leaderboard and lacking verdicts (e.g. committee_reference,
    // cbo_reference). Silently absent when report.py hasn't emitted any yet.
    const scoredIds = new Set(candIds);
    const refCards = Object.keys(b.candidates)
      .filter((k) => !scoredIds.has(k) && !b.candidates[k].verdicts)
      .map((k) => {
        const c = b.candidates[k];
        const { text, note, fk } = referenceContent(c, isDefault);
        const noteHtml = note ? `<div class="lvl-note">${esc(note)}</div>` : "";
        const sourceHtml = c.source_url
          ? `<div style="margin-top:8px;color:var(--muted);font-size:12px"><a href="${esc(c.source_url)}" target="_blank" rel="noopener">source →</a></div>` : "";
        return `<div class="summary-card reference">
          <header>
            <h4>${esc(c.label)}</h4>
            <span class="header-badges">${fkChip(fk, curLevel)}<span class="refbadge">reference — not judged</span></span>
          </header>
          <div class="body">${esc(text)}</div>
          ${noteHtml}
          ${sourceHtml}
        </div>`;
      }).join("");

    const longNote = b.long_text
      ? ' · <span style="color:#8a5a00" title="full text exceeds the single-prompt budget; summarized hierarchically">summarized via map-reduce (full text, no truncation)</span>' : "";

    // Perspectives (issue #9): fetched lazily/cached; only rendered when this
    // bill has at least one curated quote. A newer showDetail call (e.g. the
    // user clicked another bill, or the level toggle, before this resolved)
    // makes `token` stale, so we bail rather than clobber the latest render.
    const persp = await loadPerspectives();
    if (token !== detailToken) return;
    const perspEntry = persp && persp.bills ? persp.bills[b.bill_id] : null;
    const perspSponsor = (perspEntry && perspEntry.sponsor) || [];
    const perspOpponents = (perspEntry && perspEntry.opponents) || [];
    const perspectivesHtml = (perspSponsor.length || perspOpponents.length) ? `
      <h2>Perspectives</h2>
      <div class="persp-banner">Partisan framing, quoted verbatim — not part of the graded benchmark.</div>
      <div class="persp-grid">
        <div class="persp-col">
          <h3>Sponsor &amp; supporters</h3>
          ${perspColumn(perspSponsor)}
        </div>
        <div class="persp-col">
          <h3>Opponents</h3>
          ${perspColumn(perspOpponents)}
        </div>
      </div>` : "";

    detail.innerHTML = `
      <button class="detail-back">← Back to all bills</button>
      <div class="card" style="margin-top:14px">
        <div class="bnum" style="color:var(--accent);font-weight:700;font-size:13px">${esc(b.type.toUpperCase())} ${esc(String(b.number))} · ${esc(b.congress)}th Congress${b.actions_count != null ? `<span class="actions-badge">${b.actions_count} actions</span>` : ""}</div>
        <h2 style="margin:4px 0 8px">${esc(b.title || "(untitled)")}</h2>
        <p class="lede" style="margin:0">
          <a href="${esc(b.congress_gov_url)}" target="_blank" rel="noopener">View full bill text on congress.gov →</a>
          ${b.text_truncated ? ' · <span style="color:#8a5a00">bill text truncated for model input</span>' : ""}${longNote}
        </p>
      </div>
      ${levelToggleHtml}
      <div class="summaries-grid">${cards}${refCards}</div>
      ${perspectivesHtml}`;
    detail.querySelector(".detail-back").addEventListener("click", () => {
      detail.classList.add("hidden"); listWrap.classList.remove("hidden");
      window.scrollTo({ top: 0 });
    });
    detail.querySelectorAll("[data-lvl]").forEach((btn) =>
      btn.addEventListener("click", () => showDetail(id, btn.dataset.lvl)));
    listWrap.classList.add("hidden"); detail.classList.remove("hidden");
    window.scrollTo({ top: 0 });
  }

  // wire filters
  const bind = (id, key, ev = "change") =>
    document.getElementById(id).addEventListener(ev, (e) => { state[key] = e.target.value; drawList(); });
  bind("f-model", "model"); bind("f-criterion", "criterion");
  bind("f-outcome", "outcome"); bind("f-type", "type");
  bind("f-search", "q", "input");
  drawList();
}

/* -------------------------------------------------------------------- methodology */
async function initMethodology() {
  const root = document.getElementById("methodology");
  let data;
  try { data = await loadData(); } catch (e) { return fail(root, e); }
  document.getElementById("m-criteria").innerHTML = data.criteria.map((c) =>
    `<li><span class="cid">${esc(c.id)}</span><span class="ckind">${esc(c.applicability)}</span><br>
     <strong>${esc(c.name)}</strong> — ${esc(c.description)}</li>`).join("");
  document.getElementById("m-models").innerHTML =
    data.model_ids.map((m) => `<code>${esc(m)}</code>`).join(", ");
  document.getElementById("m-judge").innerHTML = `<code>${esc(data.judge_model)}</code>`;
  document.getElementById("m-summarize").textContent = data.prompts.summarize;
  document.getElementById("m-judge-prompt").textContent = data.prompts.judge;

  const levelsBox = document.getElementById("m-summarize-levels");
  if (levelsBox) {
    if (data.prompts.summarize_levels) {
      const sl = data.prompts.summarize_levels;
      const levels = data.reading_levels || [];
      const ids = levels.length ? levels.map((l) => l.id).filter((id) => sl[id]) : Object.keys(sl);
      const labelFor = (id) => (levels.find((l) => l.id === id) || {}).label || id;
      levelsBox.innerHTML =
        `<p style="color:var(--muted);font-size:13px;margin:10px 0 6px">Per-level variants of the summarization prompt:</p>` +
        ids.map((id) => `<details><summary>${esc(labelFor(id))}</summary><pre>${esc(sl[id])}</pre></details>`).join("");
    } else {
      levelsBox.innerHTML = "";
    }
  }
}

/* --------------------------------------------------------------------- CRS lag */
async function initLag() {
  const meta = document.getElementById("lag-meta");
  const grid = document.getElementById("lag-stats");
  let d;
  try { d = await (await fetch("data/lag.json", { cache: "no-store" })).json(); }
  catch (e) { return fail(grid, new Error("lag.json not found (run analyze_lag.py)")); }

  const hr = d.chambers.hr, s = d.chambers.s;
  const totBills = hr.total + s.total, totSum = hr.summarized + s.summarized;
  // recent coverage from the exact monthly volume (pooled over the last 3 months)
  const recent = d.months.slice(-3);
  const recentTot = recent.reduce((a, m) => a + (m.volume_total || 0), 0);
  const recentSum = recent.reduce((a, m) => a + (m.volume_summarized || 0), 0);
  const recentCov = recentTot ? recentSum / recentTot
    : (recent.length ? recent.reduce((a, m) => a + (m.coverage || 0), 0) / recent.length : null);
  const fmtK = (n) => n.toLocaleString("en-US");

  meta.textContent = `${d.congress}th Congress · full House + Senate population · as of ${d.generated_at}` +
    (typeof d.stage_agreement === "number"
      ? ` · stage classifiers agree on ${pct(d.stage_agreement)} of a QA sample`
      : "");

  let cards;
  if (d.stages) {
    const floor = d.stages.floor, committee = d.stages.committee;
    cards = [
      { v: pct(floor.pct), l: "of bills that reached the floor have a CRS summary",
        sub: `${fmtK(floor.summarized)} of ${fmtK(floor.total)} bills` },
      { v: pct(committee.pct), l: "of bills that advanced in committee (but not to the floor)",
        sub: `${fmtK(committee.summarized)} of ${fmtK(committee.total)} bills` },
      { v: pct((hr.summarized + s.summarized) / (hr.total + s.total)), l: "of all introduced bills — most never advance",
        sub: `${fmtK(totSum)} of ${fmtK(totBills)} bills` },
      { v: pct(recentCov), l: "for bills from the last 3 months", sub: "the active backlog" },
    ];
  } else {
    cards = [
      { v: pct(hr.pct), l: "of House bills have a CRS summary", sub: `${fmtK(hr.summarized)} of ${fmtK(hr.total)}` },
      { v: pct(s.pct), l: "of Senate bills have a CRS summary", sub: `${fmtK(s.summarized)} of ${fmtK(s.total)}` },
      { v: pct(recentCov), l: "for bills from the last 3 months", sub: "the active backlog" },
      { v: pct(totSum / totBills), l: "of all 119th-Congress bills covered", sub: `${fmtK(totSum)} of ${fmtK(totBills)}` },
    ];
  }
  grid.innerHTML = cards.map((c) =>
    `<div class="stat"><div class="stat-v">${c.v}</div><div class="stat-l">${esc(c.l)}</div>` +
    `<div class="stat-sub">${esc(c.sub)}</div></div>`).join("");
}
