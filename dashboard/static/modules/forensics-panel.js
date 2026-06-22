// Clip Forensics panel — drive scripts/research/clip_forensics.py from the UI.
// Pick a reference clip, run the decomposer, read back the timeline + the
// LLM-synthesized style profile (the "replicable essence"). New in the Forensics
// tab; see AIclippingPipelineVault/wiki/concepts/plan-clip-forensics.md.

import { apiPost, humanBytes } from "./util.js";

function esc(s) {
    return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

export async function fetchForensicsClips() {
    const sel = document.getElementById("fx-clip");
    if (!sel) return;
    try {
        const res = await fetch("/api/forensics/clips");
        const data = await res.json();
        const clips = data.clips || [];
        if (!clips.length) {
            sel.innerHTML = `<option value="">No clips in reference_clips/</option>`;
            return;
        }
        const cur = sel.value;
        sel.innerHTML = clips.map(c =>
            `<option value="${esc(c.name)}">${esc(c.name)}${c.analyzed ? "  ✓" : ""} — ${humanBytes(c.size_bytes)}</option>`
        ).join("");
        if (cur) sel.value = cur;
    } catch (e) {
        sel.innerHTML = `<option value="">Failed to load: ${esc(e.message)}</option>`;
    }
}

function statChip(label, value) {
    return `<span class="fx-chip"><b>${esc(value)}</b> ${esc(label)}</span>`;
}

function renderStyleProfile(sp) {
    if (!sp || typeof sp !== "object") {
        return `<div class="hw-hint">No style profile (LM Studio was off, or --no-llm). Turn on "LLM style profile" and ensure LM Studio is running.</div>`;
    }
    const cues = (sp.sfx_cues || []).map(c =>
        `<li><b>${esc(c.beat)}</b> → <code>${esc(c.sound)}</code>${c.note ? ` — ${esc(c.note)}` : ""}</li>`).join("");
    const notes = (sp.replication_notes || []).map(n => `<li>${esc(n)}</li>`).join("");
    const music = sp.music || {};
    const pacing = sp.pacing || {};
    return `
      <div class="fx-profile">
        <div class="fx-prof-summary">${esc(sp.summary || "")}</div>
        <div class="fx-prof-grid">
          <div><span class="fx-k">Pacing</span> ${esc(pacing.cuts_per_min ?? "?")} cuts/min · ${esc(pacing.feel || "?")}</div>
          <div><span class="fx-k">Music</span> ${music.used ? "yes" : "none"}${music.added_by_editor ? " · editor-added" : ""}${music.mood ? ` · ${esc(music.mood)}` : ""}</div>
          <div><span class="fx-k">Censor</span> ${esc(sp.censor_style || "none")}</div>
          <div><span class="fx-k">Hook</span> ${esc(sp.hook || "")}</div>
        </div>
        ${cues ? `<div class="fx-k">SFX cues</div><ul class="fx-list">${cues}</ul>` : ""}
        ${notes ? `<div class="fx-k">Replication notes</div><ul class="fx-list">${notes}</ul>` : ""}
      </div>`;
}

function renderForensics(t, summary) {
    const el = document.getElementById("fx-result");
    if (!el) return;
    const labels = {};
    (t.audio_events || []).forEach(e => { labels[e.label] = (labels[e.label] || 0) + 1; });
    const labelStr = Object.entries(labels).sort((a, b) => b[1] - a[1])
        .map(([k, v]) => `${esc(k)}×${v}`).join(", ") || "none";
    const music = (t.music || []).map(m =>
        `<li>${m.start}–${m.end}s · ${m.added ? "<b>editor-added</b>" : "ambient"}${m.mood ? ` · ${esc(m.mood)}` : ""}${m.under_speech ? " · under speech" : ""}</li>`).join("");
    const censor = (t.censor || []).map(c =>
        `<li>${c.t}s · ${esc(c.via)} (${esc(c.confidence)})${c.sfx ? ` · ${esc(c.sfx)}` : ""}</li>`).join("");
    const caps = (t.captions && t.captions.available)
        ? `${t.captions.words_per_s} wps (${t.captions.n_text_frames} frames)` : "off";
    const stages = t._stages || {};
    const badStages = Object.entries(stages).filter(([, v]) => v === "timeout" || v === "error");
    const trimNote = (t.analysis_window)
        ? ` <span class="fx-muted">(analyzed ${t.duration_s}s of ${t.source_duration_s}s — trimmed)</span>` : "";

    el.innerHTML = `
      <div class="fx-head">${esc(t.clip)} <span class="fx-muted">${t.duration_s}s · ${t.fps}fps${trimNote.replace(/^ /, " ")}</span></div>
      <div class="fx-chips">
        ${statChip("audio events", (t.audio_events || []).length)}
        ${statChip("cuts", (t.cuts || []).length)}
        ${statChip("music", (t.music || []).length)}
        ${statChip("censor", (t.censor || []).length)}
        ${statChip("motion", (t.motion || []).length)}
        ${statChip("words", t.n_words ?? 0)}
        ${statChip("captions", caps)}
      </div>
      ${badStages.length ? `<div class="fx-warn">⚠ stage(s) not ok: ${badStages.map(([k, v]) => `${esc(k)}=${esc(v)}`).join(", ")}</div>` : ""}
      <div class="fx-k">Audio events</div><div class="fx-mono">${labelStr}</div>
      ${music ? `<div class="fx-k">Music beds</div><ul class="fx-list">${music}</ul>` : ""}
      ${censor ? `<div class="fx-k">Censor</div><ul class="fx-list">${censor}</ul>` : ""}
      <div class="fx-k" style="margin-top:12px;">Style profile (replicable essence)</div>
      ${renderStyleProfile(t.style_profile)}
      <details class="fx-raw"><summary>Raw timeline JSON</summary><pre>${esc(JSON.stringify(t, null, 2))}</pre></details>
      ${summary ? `<div class="fx-muted" style="margin-top:8px;">${esc(summary)}</div>` : ""}`;
}

export async function runForensics() {
    const clip = document.getElementById("fx-clip")?.value;
    const status = document.getElementById("fx-status");
    if (!clip) { if (status) status.textContent = "Pick a clip first"; return; }
    const body = {
        clip,
        trim_end: parseFloat(document.getElementById("fx-trim-end")?.value || "0") || 0,
        ocr: !!document.getElementById("fx-ocr")?.checked,
        llm: !!document.getElementById("fx-llm")?.checked,
        cuda: !!document.getElementById("fx-cuda")?.checked,
    };
    const btn = document.getElementById("btn-fx-run");
    if (btn) btn.disabled = true;
    if (status) status.textContent = `Analyzing ${clip}… (CLAP + Whisper${body.ocr ? " + OCR" : ""}${body.llm ? " + LLM" : ""}; can take 1–2 min on first run)`;
    try {
        const { ok, data } = await apiPost("/api/forensics/run", body);
        if (ok && data.ok) {
            renderForensics(data.timeline, data.summary);
            if (status) status.textContent = `✓ Analyzed ${clip}`;
            fetchForensicsClips();
        } else {
            if (status) status.textContent = "✗ " + (data.error || "analysis failed");
        }
    } catch (e) {
        if (status) status.textContent = "✗ " + e.message;
    } finally {
        if (btn) btn.disabled = false;
    }
}

export async function loadForensicsResult() {
    const clip = document.getElementById("fx-clip")?.value;
    const status = document.getElementById("fx-status");
    if (!clip) { if (status) status.textContent = "Pick a clip first"; return; }
    try {
        const res = await fetch(`/api/forensics/result?clip=${encodeURIComponent(clip)}`);
        const data = await res.json();
        if (res.ok && data.ok) {
            renderForensics(data.timeline, "");
            if (status) status.textContent = `Loaded cached result for ${clip}`;
        } else {
            if (status) status.textContent = data.error === "not analyzed yet"
                ? "Not analyzed yet — click Analyze" : ("✗ " + (data.error || "load failed"));
        }
    } catch (e) {
        if (status) status.textContent = "✗ " + e.message;
    }
}
