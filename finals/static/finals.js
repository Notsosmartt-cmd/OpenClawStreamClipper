/* THE FINALS Clipper — single-module UI. Polls /api/state; run/stop/results/probe.
   VOD selection mirrors the main dashboard's checkbox-table multi-select
   (vods-panel.js: click-row/checkbox toggle, header select-all, "Start (N)"). */
"use strict";

const q = (id) => document.getElementById(id);
const fmtClock = (t) => {
  t = Math.max(0, Math.round(t));
  const h = String(Math.floor(t / 3600)).padStart(2, "0");
  const m = String(Math.floor((t % 3600) / 60)).padStart(2, "0");
  const s = String(t % 60).padStart(2, "0");
  return `${h}:${m}:${s}`;
};
const fmtSize = (gb) => (gb < 1 ? `${Math.round(gb * 1024)} MB` : `${gb.toFixed(2)} GB`);
const fmtDate = (unixSec) => new Date(unixSec * 1000).toLocaleString([], { dateStyle: "short", timeStyle: "short" });

let wasRunning = false;
let allVods = [];                 // last /api/vods response
const state = { selected: new Set(), extraPaths: [] }; // selected = vod names from vods/

async function jget(url) { const r = await fetch(url); return r.json(); }
async function jpost(url, body) {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  return { status: r.status, data: await r.json().catch(() => ({})) };
}

// --- VOD library (checkbox multi-select table) ---------------------------------
async function loadVods() {
  allVods = await jget("/api/vods");
  const present = new Set(allVods.map((v) => v.name));
  for (const name of Array.from(state.selected)) if (!present.has(name)) state.selected.delete(name);
  renderVods();
}

function renderVods() {
  const tbody = q("vod-tbody");
  if (!allVods.length) {
    tbody.innerHTML = '<tr><td colspan="4" class="empty-state">No videos in vods/ — add a path below instead</td></tr>';
  } else {
    tbody.innerHTML = allVods.map((v) => {
      const checked = state.selected.has(v.name);
      return `
      <tr data-vod="${escapeAttr(v.name)}" class="${checked ? "selected" : ""}">
        <td class="chk-col"><input type="checkbox" class="vod-check" ${checked ? "checked" : ""}></td>
        <td>${escapeHtml(v.name)}</td>
        <td>${fmtSize(v.size_gb)}</td>
        <td>${fmtDate(v.mtime)}</td>
      </tr>`;
    }).join("");
  }
  for (const tr of tbody.querySelectorAll("tr[data-vod]")) {
    const name = tr.dataset.vod;
    tr.addEventListener("click", (e) => {
      if (e.target.tagName === "INPUT") return; // checkbox handles itself below
      toggleVod(name);
    });
    tr.querySelector(".vod-check").addEventListener("change", () => toggleVod(name));
  }
  syncSelectAll();
  updateSummary();
}

function toggleVod(name) {
  if (state.selected.has(name)) state.selected.delete(name);
  else state.selected.add(name);
  const tr = q("vod-tbody").querySelector(`tr[data-vod="${cssEscape(name)}"]`);
  if (tr) {
    const on = state.selected.has(name);
    tr.classList.toggle("selected", on);
    tr.querySelector(".vod-check").checked = on;
  }
  syncSelectAll();
  updateSummary();
}

function toggleAllVods(checked) {
  state.selected = checked ? new Set(allVods.map((v) => v.name)) : new Set();
  renderVods();
}

function syncSelectAll() {
  const all = q("vod-select-all");
  const total = allVods.length;
  const n = state.selected.size;
  all.checked = total > 0 && n === total;
  all.indeterminate = n > 0 && n < total;
}

function escapeHtml(s) { return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
function escapeAttr(s) { return escapeHtml(s); }
function cssEscape(s) { return window.CSS && CSS.escape ? CSS.escape(s) : s.replace(/["\\]/g, "\\$&"); }

// --- extra filesystem paths (chips) ---------------------------------------------
function addPath() {
  const inp = q("inp-path");
  const path = inp.value.trim();
  if (!path) return;
  if (!state.extraPaths.includes(path)) state.extraPaths.push(path);
  inp.value = "";
  renderExtraPaths();
  updateSummary();
}

function removePath(path) {
  state.extraPaths = state.extraPaths.filter((p) => p !== path);
  renderExtraPaths();
  updateSummary();
}

function renderExtraPaths() {
  const el = q("extra-paths");
  el.innerHTML = state.extraPaths.map((p) =>
    `<span class="chip">${escapeHtml(p)}<button data-path="${escapeAttr(p)}" title="Remove">&times;</button></span>`
  ).join("");
  for (const btn of el.querySelectorAll("button")) {
    btn.addEventListener("click", () => removePath(btn.dataset.path));
  }
}

// --- selection summary / button state -------------------------------------------
function selectedList() {
  return [...state.selected, ...state.extraPaths];
}

function updateSummary() {
  const n = selectedList().length;
  q("selected-summary").textContent = n === 0 ? "— no VODs selected"
    : `— ${n} VOD${n > 1 ? "s" : ""} selected`;
  const btn = q("btn-run");
  btn.textContent = n > 1 ? `Start scan (${n})` : "Start scan";
}

// --- run / stop ------------------------------------------------------------------
async function startRun() {
  const vods = selectedList();
  const body = {
    vods,
    pre: parseFloat(q("inp-pre").value) || 10,
    post: parseFloat(q("inp-post").value) || 5,
    end_cap: parseFloat(q("inp-endcap").value) || 6,
    fps: parseFloat(q("inp-fps").value) || 2,
    ocr: q("sel-ocr").value,
    revives: q("chk-revives").checked,
    endscreens: q("chk-endscreens").checked,
    start: q("inp-start").value.trim(),
    end: q("inp-end").value.trim(),
  };
  if (!vods.length) { q("run-msg").textContent = "Select at least one VOD, or add a path."; return; }
  if (!body.revives && !body.endscreens) { q("run-msg").textContent = "Enable at least one detector."; return; }
  q("run-msg").textContent = "starting…";
  const { status, data } = await jpost("/api/run", body);
  q("run-msg").textContent = status === 200
    ? `queued ${data.count} VOD${data.count > 1 ? "s" : ""}`
    : (data.error || `error ${status}`);
  poll();
}

async function stopRun() {
  q("run-msg").textContent = "stopping…";
  await jpost("/api/stop");
  q("run-msg").textContent = "stopped";
  poll();
}

// --- state poll --------------------------------------------------------------------
async function poll() {
  let st;
  try { st = await jget("/api/state"); } catch { return; }
  const pill = q("status-pill");
  pill.className = "pill " + (st.running ? "running" : "idle");
  pill.textContent = st.running ? `scanning ${st.vod}` : "idle";
  q("btn-run").disabled = st.running;
  q("btn-stop").disabled = !st.running;
  const p = st.progress || {};
  q("progress-bar").style.width = (p.pct != null ? p.pct : 0) + "%";
  q("progress-line").textContent = p.line || "";
  q("queue-line").textContent = (p.queue_total > 1)
    ? `Queue: VOD ${p.queue_index + 1}/${p.queue_total} — ${(p.queue || []).join(", ")}`
    : "";
  if (st.running || wasRunning) {
    const log = await fetch("/api/log?tail=250").then((r) => r.text()).catch(() => "");
    const el = q("log");
    const stick = el.scrollTop + el.clientHeight >= el.scrollHeight - 8;
    el.textContent = log || "—";
    if (stick) el.scrollTop = el.scrollHeight;
  }
  if (wasRunning && !st.running) loadResults(); // run just finished
  wasRunning = st.running;
}

// --- results --------------------------------------------------------------------
async function loadResults() {
  const runs = await jget("/api/results");
  const sel = q("sel-run");
  const prev = sel.value;
  sel.innerHTML = "";
  for (const r of runs) {
    const o = document.createElement("option");
    o.value = r.stem;
    o.textContent = `${r.stem}  (${r.clips} clips)`;
    sel.appendChild(o);
  }
  if (runs.length) {
    sel.value = runs.some((r) => r.stem === prev) ? prev : runs[0].stem;
    loadRunDetail();
  } else {
    q("results-summary").textContent = "no runs yet";
    q("clips-table").querySelector("tbody").innerHTML = "";
  }
}

async function loadRunDetail() {
  const stem = q("sel-run").value;
  if (!stem) return;
  const d = await jget("/api/results/" + encodeURIComponent(stem));
  const revives = (d.revive_events || []).length;
  const ends = (d.end_spans || []).length;
  const holds = d.revive_holds || 0;
  q("results-summary").textContent =
    `${revives} revive event(s), ${ends} end-screen span(s)` +
    (holds ? `, ${holds} hold-prompt sighting(s)` : "") +
    ((d.stats && d.stats.truncated) ? " — TRUNCATED by time bound" : "");
  const sizes = {};
  for (const f of d.files || []) sizes[f.file] = f.size_mb;
  const tbody = q("clips-table").querySelector("tbody");
  tbody.innerHTML = "";
  const clips = d.clips && d.clips.length ? d.clips
    : (d.files || []).map((f) => ({ file: f.file, type: "?", start: null, dur: null, names: [] }));
  for (const c of clips) {
    const tr = document.createElement("tr");
    const isRev = c.type === "revive";
    tr.innerHTML =
      `<td class="mono">${c.file}</td>` +
      `<td class="${isRev ? "type-revive" : "type-end"}">${c.type}</td>` +
      `<td class="mono">${c.start != null ? fmtClock(c.start) : ""}</td>` +
      `<td>${c.dur != null ? c.dur.toFixed(1) + "s" : ""}</td>` +
      `<td>${sizes[c.file] != null ? sizes[c.file] + " MB" : ""}</td>` +
      `<td>${(c.names || []).join(", ")}</td>`;
    tr.addEventListener("click", () => {
      const v = q("player");
      v.style.display = "block";
      v.src = `/clips/${encodeURIComponent(stem)}/${encodeURIComponent(c.file)}`;
      v.play().catch(() => {});
    });
    tbody.appendChild(tr);
  }
}

// --- probe (single VOD — uses the first item in the current selection) -----------
function firstSelectedVod() {
  if (state.selected.size) return [...state.selected][0];
  if (state.extraPaths.length) return state.extraPaths[0];
  return "";
}

async function probe() {
  const t = q("inp-probe-t").value.trim();
  const vod = firstSelectedVod();
  if (!t || !vod) {
    q("probe-out").style.display = "block";
    q("probe-out").textContent = "Select (or add) a VOD above, and enter a timestamp.";
    return;
  }
  q("btn-probe").disabled = true;
  q("probe-out").style.display = "block";
  q("probe-out").textContent = `probing ${vod}… (extracts one frame, runs OCR)`;
  const { data } = await jpost("/api/probe", { vod, t, ocr: q("sel-ocr").value });
  q("probe-out").textContent = data.output || data.error || "no output";
  const img = q("probe-img");
  if (data.image) { img.src = data.image + "?t=" + Date.now(); img.style.display = "block"; }
  else img.style.display = "none";
  q("btn-probe").disabled = false;
}

// --- wire up -----------------------------------------------------------------------
q("btn-run").addEventListener("click", startRun);
q("btn-stop").addEventListener("click", stopRun);
q("btn-refresh").addEventListener("click", loadResults);
q("btn-refresh-vods").addEventListener("click", loadVods);
q("vod-select-all").addEventListener("change", (e) => toggleAllVods(e.target.checked));
q("btn-add-path").addEventListener("click", addPath);
q("inp-path").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); addPath(); } });
q("sel-run").addEventListener("change", loadRunDetail);
q("btn-probe").addEventListener("click", probe);

loadVods();
loadResults();
poll();
setInterval(poll, 1500);
