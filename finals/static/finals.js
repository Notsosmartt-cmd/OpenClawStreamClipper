/* THE FINALS Clipper — single-module UI. Polls /api/state; run/stop/results/probe. */
"use strict";

const q = (id) => document.getElementById(id);
const fmtClock = (t) => {
  t = Math.max(0, Math.round(t));
  const h = String(Math.floor(t / 3600)).padStart(2, "0");
  const m = String(Math.floor((t % 3600) / 60)).padStart(2, "0");
  const s = String(t % 60).padStart(2, "0");
  return `${h}:${m}:${s}`;
};

let wasRunning = false;

async function jget(url) { const r = await fetch(url); return r.json(); }
async function jpost(url, body) {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  return { status: r.status, data: await r.json().catch(() => ({})) };
}

// --- VOD list -----------------------------------------------------------------
async function loadVods() {
  const items = await jget("/api/vods");
  const sel = q("sel-vod");
  sel.innerHTML = "";
  for (const v of items) {
    const o = document.createElement("option");
    o.value = v.name;
    o.textContent = `${v.name}  (${v.size_gb} GB)`;
    sel.appendChild(o);
  }
  if (!items.length) {
    const o = document.createElement("option");
    o.value = "";
    o.textContent = "— no videos in vods/ — use the path field —";
    sel.appendChild(o);
  }
}

function chosenVod() {
  const path = q("inp-path").value.trim();
  return path || q("sel-vod").value;
}

// --- run / stop ------------------------------------------------------------------
async function startRun() {
  const body = {
    vod: chosenVod(),
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
  if (!body.vod) { q("run-msg").textContent = "Pick a VOD or enter a path."; return; }
  if (!body.revives && !body.endscreens) { q("run-msg").textContent = "Enable at least one detector."; return; }
  q("run-msg").textContent = "starting…";
  const { status, data } = await jpost("/api/run", body);
  q("run-msg").textContent = status === 200 ? `scanning ${data.vod}` : (data.error || `error ${status}`);
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

// --- probe ---------------------------------------------------------------------
async function probe() {
  const t = q("inp-probe-t").value.trim();
  const vod = chosenVod();
  if (!t || !vod) { q("probe-out").style.display = "block"; q("probe-out").textContent = "Need a VOD (above) and a timestamp."; return; }
  q("btn-probe").disabled = true;
  q("probe-out").style.display = "block";
  q("probe-out").textContent = "probing… (extracts one frame, runs OCR)";
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
q("sel-run").addEventListener("change", loadRunDetail);
q("btn-probe").addEventListener("click", probe);

loadVods();
loadResults();
poll();
setInterval(poll, 1500);
