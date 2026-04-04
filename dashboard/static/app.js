// Stream Clipper Dashboard — Client-side logic

let selectedVod = null;
let pipelineRunning = false;
let evtSource = null;

function stripAnsi(str) {
    return str.replace(/\x1b\[[0-9;]*m/g, "");
}

function classifyLogLine(text) {
    if (text.includes("[PIPELINE]")) return "log-line-pipeline";
    if (text.includes("[WARN]")) return "log-line-warn";
    if (text.includes("[ERROR]")) return "log-line-error";
    if (text.includes("[INFO]")) return "log-line-info";
    return "";
}

function parseStageNumber(stageText) {
    const match = stageText.match(/Stage (\d+)\/8/);
    return match ? parseInt(match[1]) : 0;
}

// --- VOD Browser ---
async function fetchVods() {
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
        <tr data-vod="${v.stem}" class="${selectedVod === v.stem ? 'selected' : ''}"
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

function selectVod(stem) {
    selectedVod = (selectedVod === stem) ? null : stem;
    document.querySelectorAll("#vod-tbody tr").forEach(tr => {
        tr.classList.toggle("selected", tr.dataset.vod === selectedVod);
    });
    updateControls();
}

// --- Controls ---
function updateControls() {
    const clipBtn = document.getElementById("btn-clip");
    const clipAllBtn = document.getElementById("btn-clip-all");
    const stopBtn = document.getElementById("btn-stop");

    clipBtn.disabled = !selectedVod || pipelineRunning;
    clipAllBtn.disabled = pipelineRunning;
    stopBtn.disabled = !pipelineRunning;
    stopBtn.style.display = pipelineRunning ? "inline-block" : "none";
}

async function apiPost(url, body) {
    const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
    });
    const text = await res.text();
    let data;
    try { data = JSON.parse(text); } catch { data = { error: text.substring(0, 200) }; }
    return { ok: res.ok, data };
}

async function startClip() {
    if (!selectedVod || pipelineRunning) return;
    const style = document.getElementById("sel-style").value;
    const type = document.getElementById("inp-type").value.trim();
    const force = document.getElementById("chk-force").checked;

    const { ok, data } = await apiPost("/api/clip", { vod: selectedVod, style, type, force });
    if (ok) {
        pipelineRunning = true;
        updateControls();
        updateStatusBadge(true, "Starting...");
        clearLog();
        startLogStream();
    } else {
        alert(data.error || "Failed to start pipeline");
    }
}

async function startClipAll() {
    if (pipelineRunning) return;
    const style = document.getElementById("sel-style").value;
    const force = document.getElementById("chk-force").checked;

    const { ok, data } = await apiPost("/api/clip-all", { style, force });
    if (ok) {
        pipelineRunning = true;
        updateControls();
        updateStatusBadge(true, "Starting all VODs...");
        clearLog();
        startLogStream();
    } else {
        alert(data.error || "Failed to start pipeline");
    }
}

async function stopPipeline() {
    if (!pipelineRunning) return;
    if (!confirm("Stop the running pipeline?")) return;
    await fetch("/api/stop", { method: "POST" });
    pipelineRunning = false;
    updateControls();
    updateStatusBadge(false);
    if (evtSource) { evtSource.close(); evtSource = null; }
}

// --- Status ---
function updateStatusBadge(running, stageText) {
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

function updateStageDots(stageNum) {
    for (let i = 1; i <= 8; i++) {
        const dot = document.getElementById(`stage-${i}`);
        if (!dot) continue;
        dot.className = "stage-dot";
        if (i < stageNum) dot.classList.add("done");
        else if (i === stageNum) dot.classList.add("active");
    }
}

async function pollStatus() {
    try {
        const res = await fetch("/api/status");
        const data = await res.json();
        const wasRunning = pipelineRunning;
        pipelineRunning = data.running;

        updateStatusBadge(data.running, data.stage || (data.running ? "Running..." : ""));
        updateStageDots(parseStageNumber(data.stage || ""));
        updateControls();

        // Docker status indicator (only shown when running outside Docker)
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

        if (data.running && !wasRunning && !evtSource) startLogStream();
        if (wasRunning && !data.running) { fetchClips(); fetchVods(); }
    } catch (e) { /* ignore */ }
}

// --- Log Streaming ---
function clearLog() {
    document.getElementById("log-viewer").innerHTML = "";
    document.getElementById("stage-label").textContent = "Waiting for pipeline...";
    updateStageDots(0);
}

function appendLog(line) {
    const log = document.getElementById("log-viewer");
    const clean = stripAnsi(line);
    const el = document.createElement("div");
    el.className = classifyLogLine(clean);
    el.textContent = clean;
    log.appendChild(el);
    log.scrollTop = log.scrollHeight;
}

function startLogStream() {
    if (evtSource) evtSource.close();
    evtSource = new EventSource("/api/log/stream");

    evtSource.onmessage = (e) => appendLog(e.data);

    evtSource.addEventListener("stage", (e) => {
        document.getElementById("stage-label").textContent = e.data;
        updateStageDots(parseStageNumber(e.data));
        updateStatusBadge(true, e.data);
    });

    evtSource.addEventListener("done", () => {
        appendLog("--- Pipeline finished ---");
        pipelineRunning = false;
        updateControls();
        updateStatusBadge(false);
        evtSource.close();
        evtSource = null;
        fetchClips();
        fetchVods();
        fetchStages();
    });

    evtSource.onerror = () => {
        if (!pipelineRunning) { evtSource.close(); evtSource = null; }
    };
}

// --- Clips Gallery ---
async function fetchClips() {
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

// --- Stage History ---
async function fetchStages() {
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

// --- Init ---
document.addEventListener("DOMContentLoaded", () => {
    fetchVods();
    fetchClips();
    fetchStages();
    pollStatus();
    setInterval(pollStatus, 3000);

    document.getElementById("btn-clip").addEventListener("click", startClip);
    document.getElementById("btn-clip-all").addEventListener("click", startClipAll);
    document.getElementById("btn-stop").addEventListener("click", stopPipeline);
    document.getElementById("btn-refresh-clips").addEventListener("click", fetchClips);
    document.getElementById("btn-refresh-vods").addEventListener("click", fetchVods);
});
