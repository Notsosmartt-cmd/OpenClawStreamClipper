// Hardware panel — Whisper device + restart-services flow.
// Extracted from app.js as part of Phase D.

import { apiRequest } from "./util.js";
import { fetchModels } from "./models-panel.js";

let currentHardware = {};
let pendingHardware = {};

const WHISPER_DESCS = {
    cuda: "float16 — fast, requires NVIDIA GPU (~6–7 GB VRAM).",
    cpu:  "int8 — works everywhere, slower. Uses system RAM.",
};

export async function fetchHardware() {
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

let lastGpuProfile = null;   // cached /api/hardware gpu_profile block (re-render safe)

function gpuProfileSection(gp) {
    // speed-wave3 §2b: detected profile + per-feature plain-language status +
    // manual override. Defensive: absent block (old backend / probe error) → no section.
    if (!gp) return "";
    const override = pendingHardware.gpu_profile ?? gp.override ?? "auto";
    const profiles = [
        { value: "auto",        label: `Auto — detected: ${gp.detected}` },
        { value: "dual_vendor", label: "Dual-vendor (NVIDIA + AMD)" },
        { value: "nvidia_only", label: "NVIDIA only" },
        { value: "amd_only",    label: "AMD only" },
        { value: "cpu_only",    label: "CPU only" },
    ].map(o =>
        `<option value="${o.value}" ${override === o.value ? "selected" : ""}>${o.label}</option>`
    ).join("");
    const gpus = [
        ...(gp.nvidia || []).map(g => `${g.name} (${(g.vram_mb / 1024).toFixed(0)} GB)`),
        ...(gp.amd || []).map(g => g.name),
    ].join(" + ") || "none detected";
    const feats = (gp.features || []).map(f => {
        const icon = f.status === "active" ? "🟢" : (f.status === "fallback" ? "🟡" : "⚪");
        return `<div class="hw-hint" style="margin: 2px 0;">${icon} <strong>${f.label}</strong> — ${f.reason}</div>`;
    }).join("");
    return `
            <div class="hw-field" style="margin-top: 14px;">
                <label class="hw-label">GPU Profile <span class="hw-hint">(speed optimizations adapt to this)</span></label>
                <div class="hw-hint" style="margin-bottom: 6px;">Detected hardware: ${gpus}</div>
                <select class="model-select" id="sel-gpu_profile" onchange="onHardwareDropdown('gpu_profile', this.value)">
                    ${profiles}
                </select>
                <div style="margin-top: 8px;">${feats}</div>
            </div>`;
}

function renderHardware(data) {
    const grid = document.getElementById("hardware-grid");
    const config = data.config || {};
    if (data.gpu_profile !== undefined) lastGpuProfile = data.gpu_profile;
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
            ${gpuProfileSection(lastGpuProfile)}
            <div class="hw-hint hw-hint-note" style="margin-top: 10px;">
                💡 LLM GPU assignment is managed in <strong>LM Studio</strong> — use its model load dialog to choose which GPU(s) each model uses. No restart required for LLM GPU changes.
            </div>
        </div>`;

    updateHardwareSaveBar();
    document.getElementById("hardware-restart-notice").style.display = "none";
}

export function onHardwareDropdown(key, value) {
    pendingHardware[key] = value;
    renderHardware({ config: pendingHardware });
    updateHardwareSaveBar();
}

function updateHardwareSaveBar() {
    const bar = document.getElementById("hardware-save-bar");
    const summary = document.getElementById("hardware-change-summary");
    const changes = [];
    if (pendingHardware.whisper_device !== undefined &&
        pendingHardware.whisper_device !== currentHardware.whisper_device) {
        changes.push(`Whisper: ${currentHardware.whisper_device} → ${pendingHardware.whisper_device}`);
    }
    if (pendingHardware.gpu_profile !== undefined &&
        pendingHardware.gpu_profile !== (currentHardware.gpu_profile ?? "auto")) {
        changes.push(`GPU profile: ${currentHardware.gpu_profile ?? "auto"} → ${pendingHardware.gpu_profile}`);
    }
    if (changes.length) {
        summary.textContent = changes.join("  ·  ");
        bar.style.display = "flex";
    } else {
        bar.style.display = "none";
    }
}

export async function saveHardware() {
    const btn = document.getElementById("btn-save-hardware");
    btn.disabled = true;
    btn.textContent = "Saving...";

    try {
        const { ok, data } = await apiRequest("/api/hardware", "PUT", pendingHardware);
        if (ok) {
            currentHardware = { ...data.config };
            pendingHardware = { ...currentHardware };
            updateHardwareSaveBar();

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

export async function restartServices() {
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
