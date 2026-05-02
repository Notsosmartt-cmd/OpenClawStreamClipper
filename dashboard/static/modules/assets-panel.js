// Asset cache panel — Whisper / Piper download status + fetch.
// Extracted from app.js as part of Phase D.

import { apiPost, humanBytes } from "./util.js";

export async function fetchAssets() {
    const panel = document.getElementById("assets-panel");
    try {
        const res = await fetch("/api/assets/status");
        if (!res.ok) {
            const { error } = await res.json().catch(() => ({}));
            panel.innerHTML = `<div class="empty-state">Asset status unavailable: ${error || res.status}</div>`;
            return;
        }
        const data = await res.json();
        const whisperRows = (data.whisper.models || []).map(m =>
            `<tr><td>${m.name}</td><td style="text-align:right;">${humanBytes(m.size_bytes)}</td></tr>`
        ).join("");
        const piperRows = (data.piper.voices || []).map(v =>
            `<tr><td>${v.name}${v.has_meta ? "" : " <span class=\"badge badge-yellow\" style=\"font-size:0.6rem;\">no meta</span>"}</td><td style="text-align:right;">${humanBytes(v.size_bytes)}</td></tr>`
        ).join("");
        panel.innerHTML = `
            <div class="hw-form">
                <div class="hw-field">
                    <label class="hw-label">Whisper cache <span style="color: var(--text-muted); font-weight: normal;">(${humanBytes(data.whisper.total_size_bytes)} — ${data.whisper.dir})</span></label>
                    ${whisperRows
                        ? `<table class="vod-table"><thead><tr><th>Model</th><th style="text-align:right;">Size</th></tr></thead><tbody>${whisperRows}</tbody></table>`
                        : '<div class="hw-hint">Empty — the next pipeline run will download the configured model into this folder.</div>'}
                </div>
                <div class="hw-field">
                    <label class="hw-label">Piper voices <span style="color: var(--text-muted); font-weight: normal;">(${humanBytes(data.piper.total_size_bytes)} — ${data.piper.dir})</span></label>
                    ${piperRows
                        ? `<table class="vod-table"><thead><tr><th>Voice</th><th style="text-align:right;">Size</th></tr></thead><tbody>${piperRows}</tbody></table>`
                        : '<div class="hw-hint">No voices cached. Voiceover renders will fail fast until one is fetched.</div>'}
                </div>
            </div>`;
    } catch (e) {
        panel.innerHTML = `<div class="empty-state">Failed to load asset cache: ${e.message}</div>`;
    }
}

export async function fetchAsset(kind) {
    const inpId = kind === "whisper" ? "inp-asset-whisper" : "inp-asset-piper";
    const name = (document.getElementById(inpId)?.value || "").trim();
    const status = document.getElementById("assets-fetch-status");
    if (!name) { status.textContent = "Specify a name first"; return; }
    status.textContent = `Fetching ${kind} '${name}' — this can take a minute or more…`;
    try {
        const { ok, data } = await apiPost("/api/assets/fetch", { kind, name });
        if (ok && data.ok) {
            status.textContent = `✓ Fetched ${name}${data.size_human ? ` (${data.size_human})` : ""}`;
            fetchAssets();
        } else {
            status.textContent = "✗ " + (data.error || "fetch failed");
        }
    } catch (e) {
        status.textContent = "✗ " + e.message;
    }
}
