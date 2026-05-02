// Running-pipeline UX: status badge, stage dots, SSE log stream, start/stop
// controls, originality form, music scan. Anything tied to "the pipeline is
// or could be running" lives here.
// Extracted from app.js as part of Phase D.

import { stripAnsi, classifyLogLine, parseStageNumber, apiRequest, apiPost } from "./util.js";
import { state } from "./state.js";
import { fetchVods, updateControls } from "./vods-panel.js";
import { fetchClips, fetchStages } from "./vods-panel.js";

// --- Originality form ---

export function collectOriginality() {
    const q = (id) => document.getElementById(id);
    return {
        framing:       q("sel-framing")?.value || "smart_crop",
        originality:   !!q("chk-originality")?.checked,
        stitch:        !!q("chk-stitch")?.checked,
        narrative:     !!q("chk-narrative")?.checked,
        camera_pan:    !!q("chk-camera-pan")?.checked,
        tts_vo:        !!q("chk-tts-vo")?.checked,
        music_bed:     (q("inp-music-bed")?.value || "").trim(),
        music_tier_c:  !!q("chk-music-tier-c")?.checked,
    };
}

export async function fetchOriginality() {
    try {
        const res = await fetch("/api/originality");
        if (!res.ok) return;
        const cfg = await res.json();
        const q = (id) => document.getElementById(id);
        if (q("sel-framing") && cfg.framing) q("sel-framing").value = cfg.framing;
        if (q("chk-originality")) q("chk-originality").checked = cfg.originality !== false;
        if (q("chk-narrative")) q("chk-narrative").checked = cfg.narrative !== false;
        if (q("chk-stitch")) q("chk-stitch").checked = !!cfg.stitch;
        if (q("chk-camera-pan")) q("chk-camera-pan").checked = !!cfg.camera_pan;
        if (q("chk-tts-vo")) q("chk-tts-vo").checked = !!cfg.tts_vo;
        if (q("inp-music-bed")) q("inp-music-bed").value = cfg.music_bed || "";
        if (q("chk-music-tier-c")) q("chk-music-tier-c").checked = !!cfg.music_tier_c;
    } catch (e) { /* ignore */ }
}

export async function onOriginalityChange() {
    const cfg = collectOriginality();
    try { await apiRequest("/api/originality", "PUT", cfg); } catch (e) { /* ignore */ }
}

export async function browseMusicFolder() {
    const input = document.getElementById("inp-music-bed");
    if (!input) return;
    try {
        const { ok, data } = await apiPost("/api/browse-folder",
            { initial_dir: input.value.trim() });
        if (ok && data.path) {
            input.value = data.path;
            onOriginalityChange();
        }
    } catch (e) { /* ignore */ }
}

export async function scanMusicLibrary() {
    const input = document.getElementById("inp-music-bed");
    const status = document.getElementById("music-scan-status");
    const btn = document.getElementById("btn-scan-music");
    const library = (input?.value || "").trim();
    if (!library) {
        status.textContent = "Pick a music folder first";
        return;
    }
    btn.disabled = true;
    btn.textContent = "Scanning…";
    status.textContent = "";
    try {
        const { ok, data } = await apiPost("/api/music/scan", { library });
        if (ok) {
            status.textContent = `Scanned ${data.count} track(s) → ${data.sidecar}`;
            const chk = document.getElementById("chk-music-tier-c");
            if (chk && data.count > 0) { chk.checked = true; onOriginalityChange(); }
        } else {
            status.textContent = "✗ " + (data.error || "Scan failed");
        }
    } catch (e) {
        status.textContent = "✗ " + e.message;
    } finally {
        btn.disabled = false;
        btn.textContent = "Scan Music";
    }
}

// --- Status / stage / log ---

export function updateStatusBadge(running, stageText) {
    const badge = document.getElementById("status-badge");
    const label = document.getElementById("status-label");
    if (running) {
        badge.classList.add("running");
        label.textContent = stageText || "Running...";
    } else {
        badge.classList.remove("running");
        label.textContent = "Idle";
    }
}

export function updateStageDots(stageNum) {
    for (let i = 1; i <= 8; i++) {
        const dot = document.getElementById(`stage-${i}`);
        if (!dot) continue;
        dot.className = "stage-dot";
        if (i < stageNum) dot.classList.add("done");
        else if (i === stageNum) dot.classList.add("active");
    }
}

export function clearLog() {
    document.getElementById("log-viewer").innerHTML = "";
    document.getElementById("stage-label").textContent = "Waiting for pipeline...";
    updateStageDots(0);
}

export function appendLog(line) {
    const log = document.getElementById("log-viewer");
    const clean = stripAnsi(line);
    const el = document.createElement("div");
    el.className = classifyLogLine(clean);
    el.textContent = clean;
    log.appendChild(el);
    log.scrollTop = log.scrollHeight;
}

export function startLogStream() {
    if (state.evtSource) state.evtSource.close();
    state.evtSource = new EventSource("/api/log/stream");

    state.evtSource.onmessage = (e) => appendLog(e.data);

    state.evtSource.addEventListener("stage", (e) => {
        document.getElementById("stage-label").textContent = e.data;
        updateStageDots(parseStageNumber(e.data));
        updateStatusBadge(true, e.data);
    });

    state.evtSource.addEventListener("done", () => {
        appendLog("--- Pipeline finished ---");
        state.pipelineRunning = false;
        updateControls();
        updateStatusBadge(false);
        state.evtSource.close();
        state.evtSource = null;
        fetchClips();
        fetchVods();
        fetchStages();
    });

    state.evtSource.onerror = () => {
        if (!state.pipelineRunning) {
            state.evtSource.close();
            state.evtSource = null;
        }
    };
}

// --- Pipeline lifecycle (start / stop / clip-all) ---

export async function startClip() {
    if (!state.selectedVod || state.pipelineRunning) return;
    const style = document.getElementById("sel-style").value;
    const type = document.getElementById("inp-type").value.trim();
    const force = document.getElementById("chk-force").checked;
    const captions = document.getElementById("chk-captions").checked;
    const hook_caption = document.getElementById("chk-hook-caption").checked;
    const speed = document.getElementById("sel-speed").value;
    const originality = collectOriginality();

    const { ok, data } = await apiPost("/api/clip", {
        vod: state.selectedVod, style, type, force, captions, hook_caption, speed,
        ...originality,
    });
    if (ok) {
        state.pipelineRunning = true;
        updateControls();
        updateStatusBadge(true, "Starting...");
        clearLog();
        startLogStream();
    } else {
        alert(data.error || "Failed to start pipeline");
    }
}

export async function startClipAll() {
    if (state.pipelineRunning) return;
    const style = document.getElementById("sel-style").value;
    const force = document.getElementById("chk-force").checked;
    const captions = document.getElementById("chk-captions").checked;
    const hook_caption = document.getElementById("chk-hook-caption").checked;
    const speed = document.getElementById("sel-speed").value;
    const originality = collectOriginality();

    const { ok, data } = await apiPost("/api/clip-all", {
        style, force, captions, hook_caption, speed, ...originality,
    });
    if (ok) {
        state.pipelineRunning = true;
        updateControls();
        updateStatusBadge(true, "Starting all VODs...");
        clearLog();
        startLogStream();
    } else {
        alert(data.error || "Failed to start pipeline");
    }
}

export async function stopPipeline() {
    if (!state.pipelineRunning) return;
    if (!confirm("Stop the running pipeline?")) return;
    await fetch("/api/stop", { method: "POST" });
    state.pipelineRunning = false;
    updateControls();
    updateStatusBadge(false);
    if (state.evtSource) { state.evtSource.close(); state.evtSource = null; }
}

// --- Status polling (3 s tick) ---

export async function pollStatus() {
    try {
        const res = await fetch("/api/status");
        const data = await res.json();
        const wasRunning = state.pipelineRunning;
        state.pipelineRunning = data.running;

        updateStatusBadge(data.running, data.stage || (data.running ? "Running..." : ""));
        updateStageDots(parseStageNumber(data.stage || ""));
        updateControls();

        const dockerBadge = document.getElementById("docker-badge");
        if (dockerBadge && data.mode === "docker") {
            dockerBadge.style.display = "inline-block";
            if (data.docker) {
                dockerBadge.className = "badge badge-green";
                dockerBadge.textContent = "Docker Connected";
            } else {
                dockerBadge.className = "badge badge-red";
                dockerBadge.textContent = "Docker Not Found";
            }
        }

        if (data.running && !wasRunning && !state.evtSource) startLogStream();
        if (wasRunning && !data.running) { fetchClips(); fetchVods(); }
    } catch (e) { /* ignore */ }
}
