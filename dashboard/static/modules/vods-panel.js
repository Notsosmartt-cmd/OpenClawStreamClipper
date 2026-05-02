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

function renderVods(vods) {
    const tbody = document.getElementById("vod-tbody");
    if (!vods.length) {
        tbody.innerHTML = '<tr><td colspan="5" class="empty-state">No VODs found — drop .mp4 files into the vods/ folder</td></tr>';
        return;
    }
    tbody.innerHTML = vods.map(v => `
        <tr data-vod="${v.stem}" class="${state.selectedVod === v.stem ? 'selected' : ''}"
            onclick="selectVod('${v.stem}')">
            <td>${v.stem}</td>
            <td>${v.size_mb.toLocaleString()} MB</td>
            <td>${v.duration_min} min</td>
            <td>${v.processed
                ? '<span class="badge badge-green">Processed</span>'
                : '<span class="badge badge-gray">Pending</span>'}
            </td>
            <td>${v.transcription_cached
                ? '<span class="badge badge-green">Cached</span>'
                : '<span class="badge badge-yellow">No cache</span>'}
            </td>
        </tr>
    `).join("");
}

export function selectVod(stem) {
    state.selectedVod = (state.selectedVod === stem) ? null : stem;
    document.querySelectorAll("#vod-tbody tr").forEach(tr => {
        tr.classList.toggle("selected", tr.dataset.vod === state.selectedVod);
    });
    updateControls();
}

export function updateControls() {
    const clipBtn = document.getElementById("btn-clip");
    const clipAllBtn = document.getElementById("btn-clip-all");
    const stopBtn = document.getElementById("btn-stop");

    clipBtn.disabled = !state.selectedVod || state.pipelineRunning;
    clipAllBtn.disabled = state.pipelineRunning;
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
