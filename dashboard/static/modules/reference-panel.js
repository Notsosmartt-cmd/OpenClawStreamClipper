// Reference Lab panel — Clipper-style UX (2026-07-12 simplification).
// Check reference clips in a table -> press ONE button:
//   "Analyze Selected (N)" / "Analyze New"  = decompose-if-needed + style card
//   "Compare -> Gap Report"                 = card our clips (missing only) + report
// Findings render with plain-language labels + approve/reject per row.
// See AIclippingPipelineVault/wiki/concepts/plan-reference-deconstruction-2026-07.

import { apiPost, humanBytes } from "./util.js";

function esc(s) {
    return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

const refState = {
    selected: [],          // stems of checked reference clips
    clips: [],             // last corpus payload
    jobRunning: false,
};
let jobPoll = null;
let curReportDate = "latest";

// ---- Reference Clips table (mirrors the VOD Library) -----------------------
export async function fetchReferenceCorpus() {
    const tbody = document.getElementById("ref-tbody");
    if (!tbody) return;
    try {
        const res = await fetch("/api/reference/corpus");
        const d = await res.json();
        refState.clips = d.clips || [];
        const c = d.counts || {};
        const cs = document.getElementById("ref-counts");
        if (cs) cs.textContent = `${c.carded}/${c.total} analyzed`;

        if (!refState.clips.length) {
            tbody.innerHTML = '<tr><td colspan="5" class="empty-state">No reference clips — drop competitor .mp4s into reference_clips/</td></tr>';
            refState.selected = [];
            syncRefChecks();
            updateRefControls();
            return;
        }
        const present = new Set(refState.clips.map(x => x.stem));
        refState.selected = refState.selected.filter(s => present.has(s));

        tbody.innerHTML = refState.clips.map(cl => {
            const checked = refState.selected.includes(cl.stem);
            const status = cl.carded
                ? `<span class="ref-ok">✓ analyzed</span>${cl.category ? ` <span class="fx-muted">· ${esc(cl.category)}</span>` : ""}`
                : (cl.decomposed
                    ? `<span class="fx-muted">partial — needs analyze</span>`
                    : `<span class="fx-muted">not analyzed</span>`);
            return `
            <tr data-ref="${esc(cl.stem)}" class="${checked ? "selected" : ""}"
                onclick="toggleRef('${esc(cl.stem)}')">
                <td style="text-align:center; width:36px;" onclick="event.stopPropagation()">
                    <input type="checkbox" class="ref-check" data-ref="${esc(cl.stem)}"
                           ${checked ? "checked" : ""} onchange="toggleRef('${esc(cl.stem)}')">
                </td>
                <td>${esc(cl.name)}</td>
                <td>${humanBytes(cl.size_bytes)}</td>
                <td>${status}</td>
                <td onclick="event.stopPropagation()">${cl.carded
                    ? `<button class="btn-ghost btn-sm" data-card="${esc(cl.stem)}">card</button>` : ""}</td>
            </tr>`;
        }).join("");

        tbody.querySelectorAll("[data-card]").forEach(b =>
            b.addEventListener("click", () => viewCard(b.dataset.card)));
        syncRefChecks();
        updateRefControls();

        // run picker (newest first; ✓ = that run's clips already carded)
        const sel = document.getElementById("ref-run");
        if (sel) {
            const cur = sel.value;
            sel.innerHTML = (d.runs || []).map(r =>
                `<option value="${esc(r.stamp)}">${esc(r.stamp)}${r.carded ? " ✓" : ""} — ${r.renders} clips</option>`
            ).join("") || `<option value="">no clip runs yet — clip a VOD first</option>`;
            if (cur && [...sel.options].some(o => o.value === cur)) sel.value = cur;
        }
        loadModelPicker(d.default_model || "");
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="5" class="fx-warn">Failed: ${esc(e.message)}</td></tr>`;
    }
}

// ---- analysis-model picker (same source as the Clipper's Models panel) ------
let modelsLoaded = false;
async function loadModelPicker(defaultModel) {
    const sel = document.getElementById("ref-model");
    if (!sel) return;
    const defLabel = defaultModel
        ? `pipeline default — ${defaultModel.split("/").pop()}` : "pipeline default";
    if (sel.options.length) sel.options[0].textContent = defLabel;
    if (modelsLoaded) return;
    try {
        const res = await fetch("/api/models/available");
        const d = await res.json();
        const names = (d.lmstudio || []).map(m => m.name).filter(Boolean)
            .filter(n => !n.startsWith("text-embedding"));
        if (!names.length) return;
        modelsLoaded = true;
        const cur = sel.value;
        sel.innerHTML = `<option value="">${esc(defLabel)}</option>` +
            names.map(n => `<option value="${esc(n)}">${esc(n)}</option>`).join("");
        if (cur) sel.value = cur;
    } catch (e) { /* LM Studio down — default stays */ }
}

function refModel() {
    return document.getElementById("ref-model")?.value || "";
}

export function toggleRef(stem) {
    const i = refState.selected.indexOf(stem);
    if (i >= 0) refState.selected.splice(i, 1);
    else refState.selected.push(stem);
    syncRefChecks();
    updateRefControls();
}

export function toggleAllRefs(checked) {
    refState.selected = checked ? refState.clips.map(c => c.stem) : [];
    syncRefChecks();
    updateRefControls();
}

function syncRefChecks() {
    document.querySelectorAll("#ref-tbody tr[data-ref]").forEach(tr => {
        const on = refState.selected.includes(tr.dataset.ref);
        tr.classList.toggle("selected", on);
        const cb = tr.querySelector(".ref-check");
        if (cb) cb.checked = on;
    });
    const all = document.getElementById("ref-select-all");
    if (all) {
        const n = refState.selected.length, t = refState.clips.length;
        all.checked = t > 0 && n === t;
        all.indeterminate = n > 0 && n < t;
    }
}

function updateRefControls() {
    const n = refState.selected.length;
    const analyze = document.getElementById("btn-ref-analyze");
    const analyzeNew = document.getElementById("btn-ref-analyze-new");
    const compare = document.getElementById("btn-ref-compare");
    const stop = document.getElementById("btn-ref-stop");
    if (analyze) {
        analyze.disabled = n === 0 || refState.jobRunning;
        analyze.textContent = n > 1 ? `Analyze Selected (${n})` : "Analyze Selected";
    }
    if (analyzeNew) analyzeNew.disabled = refState.jobRunning;
    if (compare) compare.disabled = refState.jobRunning;
    if (stop) stop.style.display = refState.jobRunning ? "inline-block" : "none";
}

// ---- the two actions -------------------------------------------------------
async function startJob(url, payload, label) {
    const st = document.getElementById("ref-job-status");
    const { ok, data } = await apiPost(url, payload || {});
    if (!ok) { if (st) st.textContent = "✗ " + (data.error || "failed to start"); return; }
    refState.jobRunning = true;
    updateRefControls();
    if (st) st.textContent = `▶ ${label}…`;
    if (jobPoll) clearInterval(jobPoll);
    jobPoll = setInterval(pollJob, 2500);
    pollJob();
}

export function analyzeSelected() {
    if (!refState.selected.length) return;
    startJob("/api/reference/analyze",
        { stems: refState.selected.slice(), model: refModel() },
        `analyzing ${refState.selected.length} clip(s)`);
}
export function analyzeNew() {
    startJob("/api/reference/analyze", { model: refModel() }, "analyzing new clips");
}
export function runCompare() {
    const sel = document.getElementById("ref-run");
    const runs = sel ? [...sel.selectedOptions].map(o => o.value).filter(Boolean) : [];
    if (!runs.length) return;
    const label = runs.length === 1 ? `run ${runs[0]}` : `${runs.length} runs`;
    // Send BOTH `runs` (new multi-run backend) and `run` (first pick — so an
    // older, not-yet-restarted dashboard backend still works). Defensive against
    // a mid-run browser refresh landing new frontend on old routes (BUG 70 class).
    startJob("/api/reference/compare",
             { runs, run: runs[0], model: refModel() }, `comparing vs ${label}`);
}
export async function stopReferenceJob() {
    await apiPost("/api/reference/stop", {});
}

// ---- judged-report export (copy to clipboard + saved beside the raw report) --
export async function copyJudgedReport() {
    const btn = document.getElementById("btn-ref-copy-judged");
    try {
        const res = await fetch(`/api/reference/approvals-export?date=${encodeURIComponent(curReportDate || "latest")}`);
        const d = await res.json();
        if (!res.ok || !d.ok) { if (btn) btn.textContent = "✗ " + (d.error || "no report"); return; }
        await navigator.clipboard.writeText(d.markdown);
        if (btn) {
            const c = d.counts || {};
            btn.textContent = `✓ copied (${c.approved ?? 0}✓ ${c.rejected ?? 0}✗ ${c.unjudged ?? 0}?)`;
            setTimeout(() => { btn.textContent = "Copy judged report"; }, 4000);
        }
    } catch (e) {
        if (btn) { btn.textContent = "✗ " + e.message; setTimeout(() => { btn.textContent = "Copy judged report"; }, 4000); }
    }
}

async function pollJob() {
    const st = document.getElementById("ref-job-status");
    const logEl = document.getElementById("ref-job-log");
    try {
        const res = await fetch("/api/reference/job");
        const d = await res.json();
        if (logEl) { logEl.textContent = d.log || ""; logEl.scrollTop = logEl.scrollHeight; }
        if (d.running) {
            refState.jobRunning = true;
            if (st) st.textContent = `▶ ${d.name}… ${d.elapsed}s`;
        } else {
            clearInterval(jobPoll); jobPoll = null;
            refState.jobRunning = false;
            if (st) st.textContent = d.name
                ? (d.returncode === 0 ? `✓ ${d.name} finished` : `✗ ${d.name} exited ${d.returncode}`)
                : "idle";
            updateRefControls();
            fetchReferenceCorpus();
            loadReport();
        }
    } catch (e) { /* transient */ }
    updateRefControls();
}

// ---- card viewer ------------------------------------------------------------
async function viewCard(stem) {
    const el = document.getElementById("ref-detail");
    if (!el) return;
    try {
        const res = await fetch(`/api/reference/card?stem=${encodeURIComponent(stem)}`);
        const d = await res.json();
        if (!res.ok || !d.ok) { el.innerHTML = `<div class="fx-warn">${esc(d.error || "no card")}</div>`; return; }
        const c = d.card, h = c.hook || {}, a = c.arc || {}, co = c.comedy || {},
            eg = c.edit_grammar || {}, sg = c.sfx_grammar || {}, cap = c.captions || {};
        el.innerHTML = `
          <div class="fx-head">${esc(c.clip)} <span class="fx-muted">${esc(c.category)} · confidence ${esc(c.confidence)}</span></div>
          <div class="fx-chips">
            <span class="fx-chip"><b>${esc(eg.cuts_per_30s)}</b> cuts/30s</span>
            <span class="fx-chip"><b>${esc(sg.count_per_30s)}</b> sfx/30s</span>
            <span class="fx-chip"><b>${esc(cap.density_wps)}</b> caption wps</span>
            <span class="fx-chip">${esc(a.shape)}</span>
            <span class="fx-chip">${esc(co.verbal_vs_visual)}</span>
          </div>
          <div class="fx-k">Hook</div><div>${esc(h.mechanic)} <span class="fx-muted">— ${esc(h.text_hook_style)}</span></div>
          <div class="fx-k">Comedy</div><div>${esc(co.device)}</div>
          <div class="fx-k">Captions</div><div>${esc(cap.casing)} · ${esc(cap.voice)}</div>
          <div class="fx-k">What to copy</div><div>${esc(c.essence_commentary)}</div>
          <details class="fx-raw"><summary>Raw card JSON</summary><pre>${esc(JSON.stringify(c, null, 2))}</pre></details>`;
        el.scrollIntoView({ behavior: "smooth", block: "nearest" });
    } catch (e) { el.innerHTML = `<div class="fx-warn">${esc(e.message)}</div>`; }
}

// ---- gap report (plain-language) + approve/reject ---------------------------
const METRIC_LABELS = {
    sfx_per_30s_med: "Sound effects per 30s",
    cuts_per_30s_med: "Cuts per 30s",
    caption_wps_med: "Caption words/sec",
    caption_casing_top: "Caption casing",
    chat_overlay_pct: "Chat overlay usage",
    zooms_med: "Zoom punches",
    sfx_offset_ms_med: "SFX timing offset (ms)",
    category_coverage: "Format we never produce",
};

function humanizeItem(it) {
    const [scope, metric] = String(it.id || "").split(":");
    const label = METRIC_LABELS[metric] || METRIC_LABELS[it.metric] || it.metric || it.id;
    const where = scope === "ALL" ? "all clips"
        : scope === "coverage" ? ""
        : scope ? scope.replace(/_/g, " ") : "";
    return where ? `${label} — ${where}` : label;
}

export async function loadReport(date) {
    const el = document.getElementById("ref-report");
    if (!el) return;
    curReportDate = date || curReportDate || "latest";
    try {
        const res = await fetch(`/api/reference/report?date=${encodeURIComponent(curReportDate)}`);
        const d = await res.json();
        if (!res.ok || !d.ok) {
            el.innerHTML = `<div class="empty-state">${esc(d.error || 'No report yet — check some clips, Analyze, then "Compare → Gap Report".')}</div>`;
            return;
        }
        curReportDate = d.date;
        const items = (d.items || []).map(it => {
            const v = it.verdict || "";
            const badge = v ? `<span class="fx-chip ref-verdict-${esc(v)}">${esc(v)}</span>` : "";
            const btn = (verd, txt, cls) =>
                `<button class="${cls} btn-sm" data-appr="${esc(it.id)}" data-verd="${verd}">${txt}</button>`;
            return `<div class="ref-item">
              <div>
                <b>${esc(humanizeItem(it))}</b> ${badge}<br>
                <span class="fx-muted">their clips: <code>${esc(it.reference)}</code> · ours: <code>${esc(it.ours)}</code></span><br>
                <span class="fx-muted">${esc(it.note)}</span>
              </div>
              <div class="ref-item-actions">
                ${btn("approved", "✓ Fix it", "btn-primary")}
                ${btn("rejected", "✗ Not a problem", "btn-secondary")}
              </div>
            </div>`;
        }).join("");
        el.innerHTML = `
          <div class="fx-head">Report ${esc(d.date)} <span class="fx-muted">vs run ${esc(d.run)} · ${(d.items || []).length} findings — "Fix it" queues the change for the agent</span></div>
          ${items || '<div class="empty-state">No differences above the threshold — our clips match the reference style.</div>'}
          <details class="fx-raw"><summary>Full report (raw)</summary><pre>${esc(d.markdown)}</pre></details>`;
        el.querySelectorAll("[data-appr]").forEach(b =>
            b.addEventListener("click", () => approve(b.dataset.appr, b.dataset.verd)));
    } catch (e) { el.innerHTML = `<div class="fx-warn">${esc(e.message)}</div>`; }
}

async function approve(item, verdict) {
    await apiPost("/api/reference/approve", { date: curReportDate, item, verdict });
    loadReport(curReportDate);
}

export function initReferenceTab() {
    fetchReferenceCorpus();
    loadReport("latest");
    pollJob();   // pick up a job already in flight (e.g. tab re-opened)
}
