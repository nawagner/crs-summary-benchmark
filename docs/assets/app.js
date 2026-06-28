/* CRS Summary Benchmark — static site logic. Reads docs/data/results.json. */
"use strict";

/* Two datasets; users switch between them. The choice persists in localStorage. */
const DATASETS = [
  { id: "119", file: "data/results.json", short: "119th Congress",
    label: "119th Congress · 2025–26", desc: "Recent bills (mixed activity)." },
  { id: "2024", file: "data/results-2024.json", short: "2024 · high-activity",
    label: "2024 · 118th · high-activity", desc: "The 50 most legislatively-active 2024 bills whose full text fits the model input (omnibus bills excluded)." },
];
function currentDataset() {
  const id = localStorage.getItem("crs_dataset") || "119";
  return DATASETS.find((d) => d.id === id) || DATASETS[0];
}
function setDataset(id) {
  localStorage.setItem("crs_dataset", id);
  location.reload();
}
function renderDatasetToggle(elId) {
  const el = document.getElementById(elId);
  if (!el) return;
  const cur = currentDataset().id;
  el.innerHTML = `<div class="ds-toggle">` +
    DATASETS.map((d) => `<button class="${d.id === cur ? "active" : ""}" data-ds="${d.id}">${esc(d.label)}</button>`).join("") +
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
      return `<div class="bill-row" data-id="${esc(b.bill_id)}">
        <div class="meta">
          <div class="bnum">${esc(b.type.toUpperCase())} ${esc(String(b.number))} · ${esc(b.congress)}th${acts}</div>
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

  function showDetail(id) {
    const b = data.bills.find((x) => x.bill_id === id);
    if (!b) return;
    const cards = candIds.filter((m) => b.candidates[m]).map((m) => {
      const c = b.candidates[m];
      const verdicts = crits.map((cr) => verdictRow(cr, c.verdicts[cr.id])).join("");
      const meta = c.is_human ? "human baseline"
        : `${money(c.cost_usd)} · ${secs(c.latency_s)}`;
      return `<div class="summary-card ${c.is_human ? "human" : ""}">
        <header>
          <h4>${esc(c.label)}</h4>
          <span class="scorebadge ${c.meets_standard ? "meets" : "misses"}">${c.meets_standard ? "Passed all" : c.n_passed + "/" + c.n_applicable}</span>
        </header>
        <div class="body">${esc(c.summary)}</div>
        <div class="verdicts">${verdicts}<div style="margin-top:8px;color:var(--muted);font-size:12px">${meta}</div></div>
      </div>`;
    }).join("");
    detail.innerHTML = `
      <button class="detail-back">← Back to all bills</button>
      <div class="card" style="margin-top:14px">
        <div class="bnum" style="color:var(--accent);font-weight:700;font-size:13px">${esc(b.type.toUpperCase())} ${esc(String(b.number))} · ${esc(b.congress)}th Congress${b.actions_count != null ? `<span class="actions-badge">${b.actions_count} actions</span>` : ""}</div>
        <h2 style="margin:4px 0 8px">${esc(b.title || "(untitled)")}</h2>
        <p class="lede" style="margin:0">
          <a href="${esc(b.congress_gov_url)}" target="_blank" rel="noopener">View full bill text on congress.gov →</a>
          ${b.text_truncated ? ' · <span style="color:#8a5a00">bill text truncated for model input</span>' : ""}
        </p>
      </div>
      <div class="summaries-grid">${cards}</div>`;
    detail.querySelector(".detail-back").addEventListener("click", () => {
      detail.classList.add("hidden"); listWrap.classList.remove("hidden");
      window.scrollTo({ top: 0 });
    });
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
  const recent = d.months.slice(-3);
  const recentCov = recent.reduce((a, m) => a + m.coverage, 0) / recent.length;
  const fmtK = (n) => n.toLocaleString("en-US");

  meta.textContent = `${d.congress}th Congress · ${fmtK(d.sampled)} bills sampled · as of ${d.generated_at}`;
  const cards = [
    { v: pct(hr.pct), l: "of House bills have a CRS summary", sub: `${fmtK(hr.summarized)} of ${fmtK(hr.total)}` },
    { v: pct(s.pct), l: "of Senate bills have a CRS summary", sub: `${fmtK(s.summarized)} of ${fmtK(s.total)}` },
    { v: pct(recentCov), l: "for bills from the last 3 months", sub: "the active backlog" },
    { v: pct(totSum / totBills), l: "of all 119th-Congress bills covered", sub: `${fmtK(totSum)} of ${fmtK(totBills)}` },
  ];
  grid.innerHTML = cards.map((c) =>
    `<div class="stat"><div class="stat-v">${c.v}</div><div class="stat-l">${esc(c.l)}</div>` +
    `<div class="stat-sub">${esc(c.sub)}</div></div>`).join("");
}
