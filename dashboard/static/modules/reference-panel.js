// Reference Lab panel (R6) — the reverse-engineering loop from the UI.
// decompose -> attribute cards -> card our clips -> gap report -> approve/reject.
// See AIclippingPipelineVault/wiki/concepts/plan-reference-deconstruction-2026-07.

import { apiPost, humanBytes } from "./util.js";

function esc(s) {
    return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

let jobPoll = null;
let curReportDate = "latest";

// ---- corpus table + run picker -------------------------------------------
export async function fetchReferenceCorpus() {
    const body = document.getElementById("ref-corpus");
    if (!body) return;
    try {
        const res = await fetch("/api/reference/corpus");
        const d = await res.json();
        const c = d.counts || {};
        const cs = document.getElementById("ref-counts");
        if (cs) cs.textContent = `${c.total} clips · ${c.decomposed} decomposed · ${c.carded} carded`;

        body.innerHTML = (d.clips || []).map(cl => {
            const chip = (ok, txt) => `<span class="fx-chip" style="background:${ok ? "var(--accent-dim,#1c3)" : "#333"};opacity:${ok ? 1 : .5}">${esc(txt)}</span>`;
            const notesChip = cl.notes === "corrected" ? chip(true, "notes✓")
                : cl.notes === "draft" ? `<span class="fx-chip" style="opacity:.6">notes-draft</span>` : "";
            const catChip = cl.category ? `<span class="fx-chip">${esc(cl.category)}</span>` : "";
            return `<tr>
              <td>${esc(cl.name)} <span class="fx-muted">${humanBytes(cl.size_bytes)}</span></td>
              <td>${chip(cl.decomposed, "decomp")} ${chip(cl.carded, "card")} ${catChip} ${notesChip}</td>
              <td>${cl.carded ? `<button class="btn-ghost btn-sm" data-card="${esc(cl.stem)}">view card</button>` : ""}</td>
            </tr>`;
        }).join("") || `<tr><td colspan="3" class="fx-muted">No clips in reference_clips/</td></tr>`;

        body.querySelectorAll("[data-card]").forEach(b =>
            b.addEventListener("click", () => viewCard(b.dataset.card)));

        // run picker for R2/R3
        const sel = document.getElementById("ref-run");
        if (sel) {
            const cur = sel.value;
            sel.innerHTML = (d.runs || []).map(r =>
                `<option value="${esc(r.stamp)}">${esc(r.stamp)}${r.carded ? "  ✓cards" : ""} — ${r.renders} renders</option>`
            ).join("") || `<option value="">no clip runs found — clip a VOD first</option>`;
            if (cur) sel.value = cur;
        }
    } catch (e) {
        body.innerHTML = `<tr><td colspan="3" class="fx-warn">Failed: ${esc(e.message)}</td></tr>`;
    }
}

// ---- job launch + poll ----------------------------------------------------
async function startJob(url, payload, label) {
    const st = document.getElementById("ref-job-status");
    const { ok, data } = await apiPost(url, payload || {});
    if (!ok) { if (st) st.textContent = "✗ " + (data.error || "failed to start"); return; }
    if (st) st.textContent = `▶ ${label} started…`;
    setJobButtons(true);
    if (jobPoll) clearInterval(jobPoll);
    jobPoll = setInterval(pollJob, 2500);
    pollJob();
}

async function pollJob() {
    const st = document.getElementById("ref-job-status");
    const logEl = document.getElementById("ref-job-log");
    try {
        const res = await fetch("/api/reference/job");
        const d = await res.json();
        if (logEl) { logEl.textContent = d.log || ""; logEl.scrollTop = logEl.scrollHeight; }
        if (d.running) {
            if (st) st.textContent = `▶ ${d.name}… ${d.elapsed}s`;
        } else {
            clearInterval(jobPoll); jobPoll = null;
            setJobButtons(false);
            if (st) st.textContent = d.name
                ? `✓ ${d.name} finished (exit ${d.returncode ?? 0})` : "idle";
            fetchReferenceCorpus();
            loadReport();  // a diff run may have produced a fresh report
        }
    } catch (e) { /* transient */ }
}

function setJobButtons(disabled) {
    ["btn-ref-decompose", "btn-ref-cards", "btn-ref-our-cards", "btn-ref-diff"]
        .forEach(id => { const b = document.getElementById(id); if (b) b.disabled = disabled; });
    const stop = document.getElementById("btn-ref-stop");
    if (stop) stop.style.display = disabled ? "inline-block" : "none";
}

export function runDecompose() {
    const scope = document.getElementById("ref-scope")?.value || "missing";
    startJob("/api/reference/decompose", { scope }, `decompose (${scope})`);
}
export function runCards() {
    const scope = document.getElementById("ref-scope")?.value || "missing";
    startJob("/api/reference/cards", { scope }, `build cards (${scope})`);
}
export function runOurCards() {
    const run = document.getElementById("ref-run")?.value;
    if (!run) return;
    startJob("/api/reference/our-cards", { run }, `card our clips (${run})`);
}
export function runDiff() {
    const run = document.getElementById("ref-run")?.value;
    if (!run) return;
    startJob("/api/reference/diff", { run }, `gap report (${run})`);
}
export async function stopReferenceJob() {
    await apiPost("/api/reference/stop", {});
}

// ---- card viewer ----------------------------------------------------------
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
          <div class="fx-head">${esc(c.clip)} <span class="fx-muted">${esc(c.category)} · conf ${esc(c.confidence)}</span></div>
          <div class="fx-chips">
            <span class="fx-chip"><b>${esc(eg.cuts_per_30s)}</b> cuts/30s</span>
            <span class="fx-chip"><b>${esc(sg.count_per_30s)}</b> sfx/30s</span>
            <span class="fx-chip"><b>${esc(cap.density_wps)}</b> cap wps</span>
            <span class="fx-chip">${esc(a.shape)}</span>
            <span class="fx-chip">${esc(co.verbal_vs_visual)}</span>
          </div>
          <div class="fx-k">Hook</div><div>${esc(h.mechanic)} <span class="fx-muted">— ${esc(h.text_hook_style)}</span></div>
          <div class="fx-k">Comedy</div><div>${esc(co.device)}</div>
          <div class="fx-k">Captions</div><div>${esc(cap.casing)} · ${esc(cap.voice)}</div>
          <div class="fx-k">Essence</div><div>${esc(c.essence_commentary)}</div>
          <details class="fx-raw"><summary>Raw card JSON</summary><pre>${esc(JSON.stringify(c, null, 2))}</pre></details>`;
    } catch (e) { el.innerHTML = `<div class="fx-warn">${esc(e.message)}</div>`; }
}

// ---- gap report + approve/reject ------------------------------------------
export async function loadReport(date) {
    const el = document.getElementById("ref-report");
    if (!el) return;
    curReportDate = date || curReportDate || "latest";
    try {
        const res = await fetch(`/api/reference/report?date=${encodeURIComponent(curReportDate)}`);
        const d = await res.json();
        if (!res.ok || !d.ok) { el.innerHTML = `<div class="fx-muted">${esc(d.error || "no report yet — run a gap report")}</div>`; return; }
        curReportDate = d.date;
        const items = (d.items || []).map(it => {
            const v = it.verdict || "";
            const badge = v ? `<span class="fx-chip" style="background:${v === "approved" ? "#1a4" : v === "rejected" ? "#a33" : "#555"}">${esc(v)}</span>` : "";
            const btn = (verd, txt) => `<button class="btn-ghost btn-sm" data-appr="${esc(it.id)}" data-verd="${verd}">${txt}</button>`;
            return `<div class="ref-item">
              <div><b>${esc(it.id)}</b> ${badge}<br>
                <span class="fx-muted">ref <code>${esc(it.reference)}</code> vs ours <code>${esc(it.ours)}</code> · lever <code>${esc(it.lever)}</code></span><br>
                <span class="fx-muted">${esc(it.note)}</span></div>
              <div class="ref-item-actions">${btn("approved", "✓ approve")} ${btn("rejected", "✗ reject")} ${btn("no-action", "– skip")}</div>
            </div>`;
        }).join("");
        el.innerHTML = `<div class="fx-head">Gap report ${esc(d.date)} <span class="fx-muted">run ${esc(d.run)} · ${(d.items || []).length} items</span></div>
          ${items || '<div class="fx-muted">No gaps above threshold.</div>'}
          <details class="fx-raw"><summary>Full report markdown</summary><pre>${esc(d.markdown)}</pre></details>`;
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
}
