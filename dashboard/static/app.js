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

async function apiRequest(url, method, body) {
    const res = await fetch(url, {
        method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
    });
    const text = await res.text();
    let data;
    try { data = JSON.parse(text); } catch { data = { error: text.substring(0, 200) }; }
    return { ok: res.ok, data };
}

async function apiPost(url, body) {
    return apiRequest(url, "POST", body);
}

async function startClip() {
    if (!selectedVod || pipelineRunning) return;
    const style = document.getElementById("sel-style").value;
    const type = document.getElementById("inp-type").value.trim();
    const force = document.getElementById("chk-force").checked;
    const captions = document.getElementById("chk-captions").checked;
    const hook_caption = document.getElementById("chk-hook-caption").checked;
    const speed = document.getElementById("sel-speed").value;

    const { ok, data } = await apiPost("/api/clip", { vod: selectedVod, style, type, force, captions, hook_caption, speed });
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
    const captions = document.getElementById("chk-captions").checked;
    const hook_caption = document.getElementById("chk-hook-caption").checked;
    const speed = document.getElementById("sel-speed").value;

    const { ok, data } = await apiPost("/api/clip-all", { style, force, captions, hook_caption, speed });
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

// --- AI Models ---
let currentModels = {};
let pendingModels = {};
let availableLmStudio = [];
let availableWhisper = [];
let suggestedModels = {}; // keyed by role: { id, reason, alternatives? }
let contextLengthGuide = []; // [{value, label}]

async function fetchModels() {
    try {
        const [configRes, availRes] = await Promise.all([
            fetch("/api/models"),
            fetch("/api/models/available"),
        ]);
        const configData = await configRes.json();
        const availData = await availRes.json();

        currentModels = configData.config || {};
        pendingModels = { ...currentModels };
        availableLmStudio = availData.lmstudio || [];
        availableWhisper = availData.whisper || [];
        suggestedModels = configData.suggested || {};
        contextLengthGuide = configData.context_length_guide || [];

        renderModels(configData.roles || {});
    } catch (e) {
        console.error("Failed to fetch models:", e);
        document.getElementById("models-grid").innerHTML =
            '<div class="empty-state">Failed to load models — is the backend running?</div>';
    }
}

function renderModels(roles) {
    const grid = document.getElementById("models-grid");

    const roleOrder = ["text_model", "vision_model", "whisper_model"];
    const stageMap = {
        text_model: ["Stage 3 — Segments", "Stage 4 — Moments"],
        vision_model: ["Stage 6 — Vision"],
        whisper_model: ["Stage 2 — Transcription", "Stage 7 — Captions"],
    };
    const iconMap = {
        text_model: "\u{1F4DD}",
        vision_model: "\u{1F441}",
        whisper_model: "\u{1F3A4}",
    };

    const modelCards = roleOrder.map(key => {
        const role = roles[key];
        if (!role) return "";

        const current = role.current || "";
        const isWhisper = role.provider === "whisper";
        const icon = iconMap[key] || "";
        const suggestion = suggestedModels[key] || {};
        const suggestedId = suggestion.id || "";
        const suggestedReason = suggestion.reason || "";
        const suggestedAlts = suggestion.alternatives || [];

        const stages = (stageMap[key] || []).map(s =>
            `<span class="model-stage-tag">${s}</span>`
        ).join("");

        // Helper: is a model ID a match for the suggestion (exact or alternative)
        const isSuggestedMatch = (id) =>
            id === suggestedId || suggestedAlts.some(a => id.toLowerCase().includes(a.toLowerCase()));

        let options = "";
        let statusHtml = "";

        if (isWhisper) {
            options = availableWhisper.map(m => {
                const sel = m.name === current ? "selected" : "";
                const star = m.name === suggestedId ? "⭐ " : "";
                return `<option value="${m.name}" ${sel}>${star}${m.name} (${m.size}) — ${m.description}</option>`;
            }).join("");
            if (!availableWhisper.some(m => m.name === current)) {
                options = `<option value="${current}" selected>${current}</option>` + options;
            }
        } else {
            // LM Studio models
            if (availableLmStudio.length === 0) {
                // LM Studio unreachable or no models loaded
                options = `<option value="${current}" selected>${current}</option>`;
                statusHtml = `
                    <div class="model-status model-status-warn">
                        ⚠ LM Studio returned no models — make sure the server is running and at least one model is loaded.
                    </div>`;
            } else {
                options = availableLmStudio.map(m => {
                    const sel = m.name === current ? "selected" : "";
                    const star = isSuggestedMatch(m.name) ? "⭐ " : "";
                    return `<option value="${m.name}" ${sel}>${star}${m.name}</option>`;
                }).join("");

                const currentInList = availableLmStudio.some(m => m.name === current);
                if (!currentInList) {
                    // Current model isn't loaded in LM Studio
                    options = `<option value="${current}" selected>⚠ ${current} — not loaded in LM Studio</option>` + options;
                    statusHtml = `
                        <div class="model-status model-status-warn">
                            ⚠ <strong>${current}</strong> isn't currently loaded in LM Studio.
                            Open LM Studio, load this model, then refresh — or pick a loaded model below.
                        </div>`;
                } else if (suggestedId && current !== suggestedId && !isSuggestedMatch(current)) {
                    // Model is loaded but isn't the suggested one — soft guidance
                    const suggestedLoaded = availableLmStudio.some(m => isSuggestedMatch(m.name));
                    if (suggestedLoaded) {
                        statusHtml = `
                            <div class="model-status model-status-tip">
                                💡 <strong>${suggestedId}</strong> is recommended for this role — it's loaded and available above.
                                <span class="model-status-reason">${suggestedReason}</span>
                            </div>`;
                    }
                }
            }
        }

        // Badge: green if using suggested model, yellow if custom
        const usingRecommended = isWhisper
            ? current === suggestedId
            : isSuggestedMatch(current);
        const badge = usingRecommended
            ? '<span class="badge badge-green" style="font-size: 0.65rem;">recommended</span>'
            : `<span class="badge badge-yellow" style="font-size: 0.65rem; cursor: pointer;" onclick="resetModel('${key}')" title="Click to switch to recommended model">custom</span>`;

        return `
            <div class="model-card" id="model-card-${key}">
                <div class="model-card-header">
                    <span class="model-card-icon">${icon}</span>
                    <span class="model-card-label">${role.label}</span>
                    ${badge}
                </div>
                <div class="model-card-desc">${role.description}</div>
                <div class="model-card-stages">${stages}</div>
                <select class="model-select" id="sel-${key}" onchange="onModelChange('${key}', this.value)">
                    ${options}
                </select>
                ${statusHtml}
            </div>
        `;
    }).join("");

    // Context length card
    const currentCtx = pendingModels.context_length || currentModels.context_length || 8192;
    const ctxOptions = contextLengthGuide.length
        ? contextLengthGuide.map(g =>
            `<option value="${g.value}" ${g.value === currentCtx ? "selected" : ""}>${g.label}</option>`
          ).join("")
        : `<option value="${currentCtx}" selected>${currentCtx}</option>`;

    const ctxChanged = pendingModels.context_length !== undefined &&
                       pendingModels.context_length !== (currentModels.context_length || 8192);
    const ctxBadge = ctxChanged
        ? '<span class="badge badge-yellow" style="font-size:0.65rem;">unsaved</span>'
        : '<span class="badge badge-green" style="font-size:0.65rem;">active</span>';

    const ctxCard = `
        <div class="model-card ${ctxChanged ? "model-card-changed" : ""}" id="model-card-context_length">
            <div class="model-card-header">
                <span class="model-card-icon">📐</span>
                <span class="model-card-label">Context Window</span>
                ${ctxBadge}
            </div>
            <div class="model-card-desc">
                Token budget for LLM prompt + response. Set at model load time via LM Studio API.
                Larger context = more VRAM for the KV cache. Tune to your GPU.
            </div>
            <select class="model-select" id="sel-context_length"
                    onchange="onModelChange('context_length', parseInt(this.value))">
                ${ctxOptions}
            </select>
            <div class="model-status model-status-tip" style="margin-top:8px;">
                ⚠ Takes effect when the model is loaded fresh. If already loaded in LM Studio with
                a different context, unload it first — the pipeline will reload it automatically.
            </div>
        </div>`;

    grid.innerHTML = modelCards + ctxCard;
    updateSaveBar();
}

function onModelChange(key, value) {
    pendingModels[key] = value;
    updateSaveBar();
}

function resetModel(key) {
    const suggestion = suggestedModels[key];
    if (!suggestion) return;
    const suggestedId = suggestion.id;
    const sel = document.getElementById(`sel-${key}`);
    if (!sel || !suggestedId) return;
    // Only switch if the suggested model is actually in the list
    const optionExists = Array.from(sel.options).some(o => o.value === suggestedId);
    if (optionExists) {
        sel.value = suggestedId;
        pendingModels[key] = suggestedId;
        updateSaveBar();
    } else {
        alert(`${suggestedId} isn't loaded in LM Studio yet.\nOpen LM Studio → load that model → then refresh.`);
    }
}

function updateSaveBar() {
    const bar = document.getElementById("models-save-bar");
    const summary = document.getElementById("models-change-summary");

    const changes = [];
    for (const key of ["text_model", "vision_model", "whisper_model"]) {
        if (pendingModels[key] && pendingModels[key] !== currentModels[key]) {
            const label = { text_model: "Text", vision_model: "Vision", whisper_model: "Whisper" }[key];
            changes.push(`${label}: ${currentModels[key]} \u2192 ${pendingModels[key]}`);
        }
    }
    if (pendingModels.context_length !== undefined &&
        pendingModels.context_length !== (currentModels.context_length || 8192)) {
        changes.push(`Context: ${currentModels.context_length || 8192} \u2192 ${pendingModels.context_length}`);
    }

    if (changes.length > 0) {
        summary.textContent = changes.join(" | ");
        bar.style.display = "flex";
    } else {
        bar.style.display = "none";
    }

    // Highlight changed cards
    for (const key of ["text_model", "vision_model", "whisper_model", "context_length"]) {
        const card = document.getElementById(`model-card-${key}`);
        if (card) {
            const isChanged = key === "context_length"
                ? (pendingModels.context_length !== undefined &&
                   pendingModels.context_length !== (currentModels.context_length || 8192))
                : pendingModels[key] !== currentModels[key];
            card.classList.toggle("model-card-changed", isChanged);
        }
    }
}

async function saveModels() {
    const btn = document.getElementById("btn-save-models");
    btn.disabled = true;
    btn.textContent = "Saving...";

    try {
        const { ok, data } = await apiRequest("/api/models", "PUT", pendingModels);
        if (ok) {
            currentModels = { ...pendingModels };
            updateSaveBar();
            fetchModels();
        } else {
            alert(data.error || "Failed to save model configuration");
        }
    } catch (e) {
        alert("Failed to save: " + e.message);
    } finally {
        btn.disabled = false;
        btn.textContent = "Save Changes";
    }
}

// --- Hardware Configuration ---
let currentHardware = {};
let pendingHardware = {};

const WHISPER_DESCS = {
    cuda: "float16 — fast, requires NVIDIA GPU (~6–7 GB VRAM).",
    cpu:  "int8 — works everywhere, slower. Uses system RAM.",
};

async function fetchHardware() {
    try {
        const res = await fetch("/api/hardware");
        const data = await res.json();
        currentHardware = data.config || {};
        pendingHardware = { ...currentHardware };
        renderHardware(data);
    } catch (e) {
        console.error("Failed to fetch hardware config:", e);
        document.getElementById("hardware-grid").innerHTML =
            '<div class="empty-state">Failed to load hardware config</div>';
    }
}

function renderHardware(data) {
    const grid = document.getElementById("hardware-grid");
    const config = data.config || {};
    const whisperDevice = pendingHardware.whisper_device ?? config.whisper_device ?? "cuda";

    const whisperOptions = [
        { value: "cuda", label: "GPU — NVIDIA CUDA (float16)" },
        { value: "cpu",  label: "CPU (int8)" },
    ].map(o =>
        `<option value="${o.value}" ${whisperDevice === o.value ? "selected" : ""}>${o.label}</option>`
    ).join("");

    grid.innerHTML = `
        <div class="hw-form">
            <div class="hw-field">
                <label class="hw-label">Whisper Transcription Device</label>
                <select class="model-select" id="sel-whisper_device" onchange="onHardwareDropdown('whisper_device', this.value)">
                    ${whisperOptions}
                </select>
                <div class="hw-hint">${WHISPER_DESCS[whisperDevice] || ""}</div>
            </div>
            <div class="hw-hint hw-hint-note" style="margin-top: 10px;">
                💡 LLM GPU assignment is managed in <strong>LM Studio</strong> — use its model load dialog to choose which GPU(s) each model uses. No restart required for LLM GPU changes.
            </div>
        </div>`;

    updateHardwareSaveBar();
    document.getElementById("hardware-restart-notice").style.display = "none";
}

function onHardwareDropdown(key, value) {
    pendingHardware[key] = value;
    renderHardware({ config: pendingHardware });
    updateHardwareSaveBar();
}

function updateHardwareSaveBar() {
    const bar = document.getElementById("hardware-save-bar");
    const summary = document.getElementById("hardware-change-summary");
    const changed = pendingHardware.whisper_device !== undefined &&
                    pendingHardware.whisper_device !== currentHardware.whisper_device;
    if (changed) {
        summary.textContent = `Whisper: ${currentHardware.whisper_device} \u2192 ${pendingHardware.whisper_device}`;
        bar.style.display = "flex";
    } else {
        bar.style.display = "none";
    }
}

async function saveHardware() {
    const btn = document.getElementById("btn-save-hardware");
    btn.disabled = true;
    btn.textContent = "Saving...";

    try {
        const { ok, data } = await apiRequest("/api/hardware", "PUT", pendingHardware);
        if (ok) {
            currentHardware = { ...data.config };
            pendingHardware = { ...currentHardware };
            updateHardwareSaveBar();

            // Show restart notice with button + fallback command
            document.getElementById("hardware-restart-cmd").textContent =
                "Or run manually:  docker compose restart";
            document.getElementById("hardware-restart-status").textContent = "";
            document.getElementById("hardware-restart-notice").style.display = "block";
        } else {
            alert(data.error || "Failed to save hardware configuration");
        }
    } catch (e) {
        alert("Failed to save: " + e.message);
    } finally {
        btn.disabled = false;
        btn.textContent = "Save";
    }
}

async function restartServices() {
    const btn = document.getElementById("btn-restart-services");
    const status = document.getElementById("hardware-restart-status");
    btn.disabled = true;
    btn.textContent = "Restarting...";
    status.textContent = "";

    try {
        const { ok, data } = await apiRequest("/api/restart", "POST", {});
        if (ok) {
            status.textContent = "✓ Restarting…";
            btn.textContent = "Restarting…";
            setTimeout(async () => {
                status.textContent = "Waiting for container…";
                let attempts = 0;
                const poll = setInterval(async () => {
                    attempts++;
                    try {
                        const res = await fetch("/api/status");
                        const s = await res.json();
                        if (s.docker && s.lm_studio) {
                            clearInterval(poll);
                            status.textContent = "✓ Services back online";
                            btn.textContent = "Restart Services";
                            btn.disabled = false;
                            fetchModels();
                        }
                    } catch (_) {}
                    if (attempts > 30) {
                        clearInterval(poll);
                        status.textContent = "Timeout — check Docker Desktop";
                        btn.textContent = "Restart Services";
                        btn.disabled = false;
                    }
                }, 5000);
            }, 5000);
        } else {
            status.textContent = "✗ " + (data.error || "Restart failed");
            btn.textContent = "Restart Services";
            btn.disabled = false;
        }
    } catch (e) {
        status.textContent = "✗ " + e.message;
        btn.textContent = "Restart Services";
        btn.disabled = false;
    }
}

// --- Init ---
document.addEventListener("DOMContentLoaded", () => {
    fetchVods();
    fetchClips();
    fetchStages();
    fetchModels();
    fetchHardware();
    pollStatus();
    setInterval(pollStatus, 3000);

    document.getElementById("btn-clip").addEventListener("click", startClip);
    document.getElementById("btn-clip-all").addEventListener("click", startClipAll);
    document.getElementById("btn-stop").addEventListener("click", stopPipeline);
    document.getElementById("btn-refresh-clips").addEventListener("click", fetchClips);
    document.getElementById("btn-refresh-vods").addEventListener("click", fetchVods);
    document.getElementById("btn-refresh-models").addEventListener("click", fetchModels);
    document.getElementById("btn-save-models").addEventListener("click", saveModels);
    document.getElementById("btn-refresh-hardware").addEventListener("click", fetchHardware);
    document.getElementById("btn-save-hardware").addEventListener("click", saveHardware);
    document.getElementById("btn-restart-services").addEventListener("click", restartServices);
});
