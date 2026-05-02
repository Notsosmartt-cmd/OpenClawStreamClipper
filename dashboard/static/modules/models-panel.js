// Models panel — fetch, render, save model configuration.
// Extracted from app.js as part of Phase D.

import { apiRequest } from "./util.js";

let currentModels = {};
let pendingModels = {};
let availableLmStudio = [];
let availableWhisper = [];
let suggestedModels = {};
let contextLengthGuide = [];

export async function fetchModels() {
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
            if (availableLmStudio.length === 0) {
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
                    options = `<option value="${current}" selected>⚠ ${current} — not loaded in LM Studio</option>` + options;
                    statusHtml = `
                        <div class="model-status model-status-warn">
                            ⚠ <strong>${current}</strong> isn't currently loaded in LM Studio.
                            Open LM Studio, load this model, then refresh — or pick a loaded model below.
                        </div>`;
                } else if (suggestedId && current !== suggestedId && !isSuggestedMatch(current)) {
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

export function onModelChange(key, value) {
    pendingModels[key] = value;
    updateSaveBar();
}

export function resetModel(key) {
    const suggestion = suggestedModels[key];
    if (!suggestion) return;
    const suggestedId = suggestion.id;
    const sel = document.getElementById(`sel-${key}`);
    if (!sel || !suggestedId) return;
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
            changes.push(`${label}: ${currentModels[key]} → ${pendingModels[key]}`);
        }
    }
    if (pendingModels.context_length !== undefined &&
        pendingModels.context_length !== (currentModels.context_length || 8192)) {
        changes.push(`Context: ${currentModels.context_length || 8192} → ${pendingModels.context_length}`);
    }

    if (changes.length > 0) {
        summary.textContent = changes.join(" | ");
        bar.style.display = "flex";
    } else {
        bar.style.display = "none";
    }

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

export async function saveModels() {
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
