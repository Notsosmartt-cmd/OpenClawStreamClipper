// VOD library + clips gallery + stage history.
// Extracted from app.js as part of Phase D.

import { state } from "./state.js";

export async function fetchVods() {
    try {
        const res = await fetch("/api/vods");
        const vods = await res.json();
        renderVods(vods);
    } catch (e) {
        console.error("Failed to fetch VODs:", e);
    }
}

// Estimated pipeline minutes -> "≈ 47 min" / "≈ 1h 23m" (0/undefined -> em-dash)
function fmtEst(min) {
    if (!min) return "—";
    if (min < 60) return `≈ ${min} min`;
    return `≈ ${Math.floor(min / 60)}h ${min % 60}m`;
}

function renderVods(vods) {
    const tbody = document.getElementById("vod-tbody");
    if (!vods.length) {
        tbody.innerHTML = '<tr><td colspan="7" class="empty-state">No VODs found — drop .mp4 files into the vods/ folder</td></tr>';
        state.selectedVods = [];
        syncVodChecks();
        updateControls();
        return;
    }
    // Drop any prior selections whose VOD is no longer on disk.
    const present = new Set(vods.map(v => v.stem));
    state.selectedVods = state.selectedVods.filter(s => present.has(s));
    tbody.innerHTML = vods.map(v => {
        const checked = state.selectedVods.includes(v.stem);
        return `
        <tr data-vod="${v.stem}" class="${checked ? 'selected' : ''}"
            onclick="toggleVod('${v.stem}')">
            <td style="text-align:center; width:36px;" onclick="event.stopPropagation()">
                <input type="checkbox" class="vod-check" data-vod="${v.stem}"
                       ${checked ? 'checked' : ''} onchange="toggleVod('${v.stem}')">
            </td>
            <td>${v.stem}</td>
            <td>${v.size_mb.toLocaleString()} MB</td>
            <td>${v.duration_min} min</td>
            <td title="estimated end-to-end pipeline time at the measured per-stage rates${v.transcription_cached ? ' (transcript cached)' : ' (fresh transcription included)'}">${fmtEst(v.est_minutes)}</td>
            <td>${v.processed
                ? '<span class="badge badge-green">Processed</span>'
                : '<span class="badge badge-gray">Pending</span>'}
            </td>
            <td>${v.transcription_cached
                ? '<span class="badge badge-green">Cached</span>'
                : '<span class="badge badge-yellow">No cache</span>'}
            </td>
        </tr>`;
    }).join("");
    // Library-total row (owner req 2026-07-16): sum of the per-VOD estimates.
    const totalMin = vods.reduce((s, v) => s + (v.est_minutes || 0), 0);
    const totalDur = vods.reduce((s, v) => s + (v.duration_min || 0), 0);
    tbody.innerHTML += `
        <tr class="vod-total-row" style="font-weight:600; opacity:.85;">
            <td></td>
            <td>Library total (${vods.length} VOD${vods.length > 1 ? 's' : ''})</td>
            <td></td>
            <td>${totalDur} min</td>
            <td title="sum of per-VOD estimates">${fmtEst(totalMin)}</td>
            <td colspan="2"></td>
        </tr>`;
    syncVodChecks();
    updateControls();
}

// Push state.selectedVods onto the DOM: row highlight, per-row checkbox, and
// the header select-all (checked when all are picked, indeterminate for some).
function syncVodChecks() {
    const sel = new Set(state.selectedVods);
    document.querySelectorAll("#vod-tbody tr").forEach(tr => {
        const stem = tr.dataset.vod;
        if (!stem) return;
        const on = sel.has(stem);
        tr.classList.toggle("selected", on);
        const cb = tr.querySelector(".vod-check");
        if (cb) cb.checked = on;
    });
    const all = document.getElementById("vod-select-all");
    if (all) {
        const total = document.querySelectorAll("#vod-tbody .vod-check").length;
        const n = state.selectedVods.length;
        all.checked = total > 0 && n === total;
        all.indeterminate = n > 0 && n < total;
    }
}

// Toggle one VOD's membership (authoritative on state, so it stays correct
// whether the click came from the row or the native checkbox).
export function toggleVod(stem) {
    const i = state.selectedVods.indexOf(stem);
    if (i >= 0) state.selectedVods.splice(i, 1);
    else state.selectedVods.push(stem);
    syncVodChecks();
    updateControls();
}

// Header "select all" checkbox — check → every VOD; uncheck → none.
export function toggleAllVods(checked) {
    const stems = Array.from(document.querySelectorAll("#vod-tbody .vod-check"))
        .map(cb => cb.dataset.vod);
    state.selectedVods = checked ? stems : [];
    syncVodChecks();
    updateControls();
}

export function updateControls() {
    const clipBtn = document.getElementById("btn-clip");
    const clipAllBtn = document.getElementById("btn-clip-all");
    const stopBtn = document.getElementById("btn-stop");
    const newsBtn = document.getElementById("btn-news-compile");
    const n = state.selectedVods.length;

    clipBtn.disabled = n === 0 || state.pipelineRunning;
    clipBtn.textContent = n > 1 ? `Clip Selected (${n})` : "Clip Selected";
    clipAllBtn.disabled = state.pipelineRunning;
    // News Compile: a separate, explicit action on the multi-select (owner
    // directive 2026-07-11 — never part of the standard clip flow).
    if (newsBtn) {
        newsBtn.disabled = n === 0 || state.pipelineRunning;
        newsBtn.textContent = n > 1 ? `News Compile (${n})` : "News Compile";
    }
    stopBtn.disabled = !state.pipelineRunning;
    stopBtn.style.display = state.pipelineRunning ? "inline-block" : "none";
}

export async function fetchClips() {
    try {
        const res = await fetch("/api/clips");
        const clips = await res.json();
        renderClips(clips);
    } catch (e) { console.error("Failed to fetch clips:", e); }
}

function renderClips(clips) {
    const grid = document.getElementById("clips-grid");
    if (!clips.length) {
        grid.innerHTML = '<div class="empty-state">No clips generated yet</div>';
        return;
    }
    grid.innerHTML = clips.map(c => `
        <div class="clip-card">
            <video preload="metadata" onclick="this.paused ? this.play() : this.pause()"
                   src="/api/clips/${encodeURIComponent(c.name)}#t=0.5"></video>
            <div class="clip-card-info">
                <div class="clip-card-title" title="${c.name}">${c.name.replace('.mp4', '').replace(/_/g, ' ')}</div>
                <div class="clip-card-meta">
                    <span>${c.size_mb} MB &middot; ${c.modified}</span>
                    <a href="/api/clips/${encodeURIComponent(c.name)}" download>Download</a>
                </div>
            </div>
        </div>
    `).join("");
}

export async function fetchStages() {
    try {
        const res = await fetch("/api/stages");
        const stages = await res.json();
        const el = document.getElementById("stage-history");
        if (!stages.length) { el.innerHTML = ""; return; }
        el.innerHTML = stages.map(s => {
            const t = s.time.split("T")[1] || s.time;
            return `<div class="stage-history-item"><span class="time">${t}</span><span>${s.stage}</span></div>`;
        }).join("");
    } catch (e) { /* ignore */ }
}
