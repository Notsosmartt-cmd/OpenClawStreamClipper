// Folders panel — VOD source / clips output paths + native folder picker.
// Extracted from app.js as part of Phase D.

import { apiRequest, apiPost, escAttr } from "./util.js";
import { fetchVods, fetchClips } from "./vods-panel.js";

let currentFolders = {};

export async function fetchFolders() {
    try {
        const res = await fetch("/api/paths");
        const data = await res.json();
        currentFolders = data;
        renderFolders(data);
    } catch (e) {
        console.error("Failed to fetch folder config:", e);
        document.getElementById("folders-panel").innerHTML =
            '<div class="empty-state">Failed to load folder settings</div>';
    }
}

function renderFolders(data) {
    const panel = document.getElementById("folders-panel");
    panel.innerHTML = `
        <div class="hw-form">
            <div class="hw-field">
                <label class="hw-label">VOD Source Folder</label>
                <div class="folder-row">
                    <input type="text" id="inp-vods-dir" class="folder-input"
                           value="${escAttr(data.vods_dir || '')}"
                           oninput="onFoldersChange()" />
                    <button class="btn-secondary folder-browse-btn"
                            onclick="browseFolderFor('inp-vods-dir')">Browse…</button>
                </div>
                <div class="hw-hint">Dashboard scans this folder for .mp4 / .mkv files to clip.</div>
            </div>
            <div class="hw-field">
                <label class="hw-label">Clips Output Folder</label>
                <div class="folder-row">
                    <input type="text" id="inp-clips-dir" class="folder-input"
                           value="${escAttr(data.clips_dir || '')}"
                           oninput="onFoldersChange()" />
                    <button class="btn-secondary folder-browse-btn"
                            onclick="browseFolderFor('inp-clips-dir')">Browse…</button>
                </div>
                <div class="hw-hint">Finished clips are saved here. The gallery shows this folder.</div>
            </div>
        </div>
        <div id="folders-save-bar" class="models-save-bar" style="display: none;">
            <span id="folders-change-summary" class="models-change-text"></span>
            <button id="btn-save-folders" class="btn-primary"
                    style="padding: 6px 16px; font-size: 0.8rem;"
                    onclick="saveFolders()">Save</button>
        </div>
        <div id="folders-restart-notice" class="hardware-restart-notice" style="display: none; margin-top: 12px;">
            <div class="hardware-restart-title">&#x26A0; docker-compose.yml updated — restart required</div>
            <div style="font-size: 0.72rem; color: var(--text-muted); margin-top: 4px; line-height: 1.5;">
                Volume mounts changed. Restart the container so the pipeline reads VODs from and writes clips to the new folders.
            </div>
            <div style="display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-top: 8px;">
                <button id="btn-restart-for-folders" class="btn-primary" style="padding: 6px 16px; font-size: 0.8rem;">Restart Container</button>
                <span id="folders-restart-status" style="font-size: 0.72rem; color: var(--text-muted);"></span>
            </div>
            <div class="hardware-restart-cmd" style="margin-top: 6px;">docker compose restart</div>
        </div>
    `;

    document.getElementById("btn-restart-for-folders")?.addEventListener("click", async () => {
        const btn = document.getElementById("btn-restart-for-folders");
        const status = document.getElementById("folders-restart-status");
        btn.disabled = true;
        btn.textContent = "Restarting…";
        status.textContent = "";
        try {
            const { ok, data } = await apiRequest("/api/restart", "POST", {});
            if (ok) {
                status.textContent = "✓ Restarting…";
            } else {
                status.textContent = "✗ " + (data.error || "Failed — run: docker compose restart");
                btn.disabled = false;
                btn.textContent = "Restart Container";
            }
        } catch (e) {
            status.textContent = "✗ " + e.message;
            btn.disabled = false;
            btn.textContent = "Restart Container";
        }
    });
}

export async function browseFolderFor(inputId) {
    const input = document.getElementById(inputId);
    if (!input) return;
    const btn = input.closest(".folder-row")?.querySelector(".folder-browse-btn");
    if (btn) { btn.disabled = true; btn.textContent = "Opening…"; }

    try {
        const { ok, data } = await apiPost("/api/browse-folder", { initial_dir: input.value.trim() });
        if (ok && data.path) {
            input.value = data.path;
            onFoldersChange();
        } else if (!ok && data.error) {
            console.warn("Folder browse unavailable:", data.error);
        }
    } catch (e) {
        console.error("Browse folder failed:", e);
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = "Browse…"; }
    }
}

export function onFoldersChange() {
    const bar = document.getElementById("folders-save-bar");
    const summary = document.getElementById("folders-change-summary");
    if (!bar) return;

    const vodsVal = (document.getElementById("inp-vods-dir")?.value || "").trim();
    const clipsVal = (document.getElementById("inp-clips-dir")?.value || "").trim();

    const changes = [];
    if (vodsVal !== (currentFolders.vods_dir || "")) changes.push("VOD folder");
    if (clipsVal !== (currentFolders.clips_dir || "")) changes.push("Clips folder");

    if (changes.length) {
        summary.textContent = changes.join(" + ") + " changed — unsaved";
        bar.style.display = "flex";
    } else {
        bar.style.display = "none";
    }
}

export async function saveFolders() {
    const btn = document.getElementById("btn-save-folders");
    if (btn) { btn.disabled = true; btn.textContent = "Saving..."; }

    const vods_dir = (document.getElementById("inp-vods-dir")?.value || "").trim();
    const clips_dir = (document.getElementById("inp-clips-dir")?.value || "").trim();

    if (!vods_dir || !clips_dir) {
        alert("Both folder paths are required.");
        if (btn) { btn.disabled = false; btn.textContent = "Save"; }
        return;
    }

    try {
        const { ok, data } = await apiRequest("/api/paths", "PUT", { vods_dir, clips_dir });
        if (ok) {
            currentFolders = data.config || { vods_dir, clips_dir };
            const bar = document.getElementById("folders-save-bar");
            if (bar) bar.style.display = "none";
            fetchVods();
            fetchClips();
            if (data.restart_required) {
                const notice = document.getElementById("folders-restart-notice");
                if (notice) notice.style.display = "block";
            }
        } else {
            alert(data.error || "Failed to save folder settings");
        }
    } catch (e) {
        alert("Failed to save: " + e.message);
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = "Save"; }
    }
}
