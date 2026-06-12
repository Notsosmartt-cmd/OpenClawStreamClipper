---
title: "Open Questions and Feature Requests"
type: concept
tags: [open-questions, feature-requests, research, todo, hub]
sources: 2
updated: 2026-06-12
---

# Open Questions and Feature Requests

Unresolved questions, user requests, and design decisions. Good starting points for future development sessions.

> [!note] Status reviewed 2026-06-12
> Most of the April questions have since been answered by the bare-metal port, the unified-model swap, and Stage 4 reworks. Each Q below now carries a **Status** line. Q1, Q2, Q3 are **resolved**; Q4 and Q5 are **moot** under the bare-metal/LM-Studio architecture. None remain blocking.

---

## Q1 — Score normalization: should scores be 0–1 instead of 1–10? — RESOLVED

**Source**: `fix.txt` (user question, 2026-04-07)

> [!note] Status: RESOLVED — scores are normalized to 0.0–1.0
> `scripts/lib/stages/stage4_moments.py` now normalizes both passes to 0.0–1.0: Pass A maps threshold→0.0, 10+ signals→1.0; Pass B maps the LLM 1–10 to `(score-1)/9.0`. Merge/cross-validation works on `normalized_score`. The recommendation below was adopted. See [[concepts/highlight-detection]].

**The question**: Would normalizing scores from 0–1 give better range, depth, and accuracy compared to the current 1–10 integer scale?

**Current state**:
- Pass A keywords produce integer scores capped at 8
- Pass B LLM returns integers 1–10
- Cross-validation adds +1.5
- Segment score boosts add +1
- Vision enrichment adds +1 or +2
- Final score can theoretically reach ~13+ before capping at 10

**Arguments for normalization (0–1 floats)**:
- Allows more granular differentiation between close moments
- Enables principled weighting when blending Pass A + Pass B scores (currently additive integers)
- Cross-validated moments (+1.5) and boosted moments (+1) might compete more fairly
- Better statistical properties for the time-bucket selection algorithm

**Arguments against / complicating factors**:
- LLM outputs integers 1–10 naturally; converting to 0–1 requires a mapping convention
- Vision enrichment is currently "+1 or +2" — would need recalibration as percentage boosts
- Threshold filtering (e.g., "score ≥ 7") would become "score ≥ 0.7" — minor change but all thresholds need updating
- The current system works; normalization is a refinement, not a fix

**Recommendation**: Worth doing for Pass A (keyword scores have wide integer variance), less critical for Pass B (already 1–10). Could normalize at the Pass C merge step before selection.

---

## Q2 — Variable clip length: should clips be 15–60 seconds instead of fixed 45? — RESOLVED

**Source**: `fix.txt` (user question, 2026-04-07)

> [!note] Status: RESOLVED — clips carry per-moment `clip_start`/`clip_duration`
> Stage 4 now emits LLM-annotated boundaries (`clip_start`/`clip_end` → `clip_duration`) per moment, clamped to the chunk and a max duration, with a centered-window fallback. Stage 7 reads them from the manifest instead of a fixed 45 s window (approach 2 below — LLM-annotated boundaries — was chosen). See [[concepts/clip-rendering]] and `scripts/lib/stages/stage4_moments.py`.

**The question**: Should clip length be variable based on content type? A storytime segment might need 60 seconds to include the payoff. A quick reaction might only need 15 seconds. Minimum 15 seconds. How would the pipeline determine length?

**Historical state (April)**: Fixed 45-second window (`T - 22s` to `T + 23s`). No content awareness.

**How variable length could work**:

1. **Category-based defaults** (simple):
   - `hype`, `funny`, `reactive` → 30 seconds (punchy moments)
   - `emotional`, `hot_take` → 45 seconds (need buildup)
   - `storytime` → 60 seconds (need payoff)

2. **LLM-annotated boundaries** (complex):
   - Stage 4 Pass B already returns `{time, score, category, why}`
   - Could add `{start_offset, end_offset}` to the JSON — LLM specifies how many seconds before/after the peak to capture
   - Prompts would need to be updated: "also specify how many seconds before and after the peak are needed for context"

3. **Transcript-boundary detection** (medium):
   - Scan transcript around the peak moment for natural sentence/paragraph boundaries
   - Expand clip to include the full setup if detected within ±30 seconds of the peak

**Over-clipping prevention**:
- Hard cap: 90 seconds maximum
- Hard floor: 15 seconds minimum
- Score penalty for very long clips (encourage tight editing)
- Require the LLM to justify lengths > 60 seconds

**Risk**: Variable lengths complicate the Stage 7 audio extraction (currently a single-pass batch). Each clip would need its own duration calculation before extraction.

**Recommendation**: Start with category-based defaults (approach 1) — no pipeline changes needed beyond a lookup table in Stage 7. LLM-annotated boundaries are more accurate but require Stage 4 prompt changes and more testing.

---

## Q3 — Model switcher UI in the dashboard — RESOLVED

**Source**: `fix.txt` (user question, 2026-04-07)

> [!note] Status: RESOLVED — the dashboard has a model panel
> The dashboard now exposes model selection (`dashboard/_state.py` + the models panel, `dashboard/static/modules/models-panel.js`): `config/models.json` holds `text_model` / `vision_model` / `whisper_model`, the UI offers per-role dropdowns with descriptions, and `/api/models/context-recommendation` computes a per-model context size (GGUF-exact KV-cache estimate against live VRAM). The pipeline reads the selection through `CLIP_*` env vars → `config/models.json` (see `scripts/run_pipeline.py`). See [[entities/dashboard]] and [[concepts/vram-budget]].

**The question**: Add a section to the web dashboard showing which AI model is used at each pipeline stage, with the ability to swap models without editing config files.

**Current model assignments (2026-06)**: text and vision both default to the unified `qwen/qwen3.6-35b-a3b` (`config/models.json`); optional `text_model_passb` / `vision_model_stage6` override Pass B / Stage 6 on bigger rigs; transcription is `whisper large-v3-turbo`; the Discord agent model is in `config/openclaw.json`. See [[concepts/context-management]].

---

## Q4 — qwen3.5:9b vision broken in Ollama — MOOT (architecture changed)

**Source**: Project summary (2026)

> [!note] Status: MOOT — Ollama replaced by LM Studio; unified multimodal model
> This question assumed Ollama as the server and a text/vision model split. Since the bare-metal port (2026-06-04) the pipeline uses native **LM Studio**, and text + vision are served by a single multimodal model (`qwen/qwen3.6-35b-a3b`), so the Ollama GGUF-projector bug no longer applies. A dedicated vision model (`qwen/qwen3-vl-8b`) remains available as an optional `vision_model_stage6` override. See [[concepts/bare-metal-windows]] and [[entities/qwen35]].

**Original question**: When will Ollama fix qwen3.5:9b GGUF vision inference? Until then, all vision tasks route to qwen3-vl:8b.

---

## Q5 — Container dashboard zombie process — MOOT (bare-metal)

**Source**: `DEVELOPMENT_SUMMARY.txt` (BUG 10)

> [!note] Status: MOOT — the dashboard runs natively, not in a container
> Since the bare-metal port the Flask dashboard runs as a native Windows process; the Docker-container zombie failure mode no longer occurs in the default architecture. Relevant only to legacy Docker deployments. See [[concepts/bugs-and-fixes]] BUG 10.

The dashboard Flask app inside the Docker container could become a zombie process if it crashed on startup.

---

## Related
- [[concepts/clip-rendering]] — fixed 45s clip length (Q2)
- [[concepts/highlight-detection]] — scoring system (Q1)
- [[entities/dashboard]] — model switcher UI location (Q3)
- [[entities/qwen35]] — vision broken note (Q4)
- [[entities/qwen3-vl]] — currently handles all vision tasks (Q4)
