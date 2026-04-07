---
title: "Open Questions and Feature Requests"
type: concept
tags: [open-questions, feature-requests, research, todo]
sources: 2
updated: 2026-04-07
---

# Open Questions and Feature Requests

Unresolved questions, user requests, and design decisions that haven't been implemented yet. Good starting points for future development sessions.

---

## Q1 — Score normalization: should scores be 0–1 instead of 1–10?

**Source**: `fix.txt` (user question, 2026-04-07)

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

## Q2 — Variable clip length: should clips be 15–60 seconds instead of fixed 45?

**Source**: `fix.txt` (user question, 2026-04-07)

**The question**: Should clip length be variable based on content type? A storytime segment might need 60 seconds to include the payoff. A quick reaction might only need 15 seconds. Minimum 15 seconds. How would the pipeline determine length?

**Current state**: Fixed 45-second window (`T - 22s` to `T + 23s`). No content awareness.

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

## Q3 — Model switcher UI in the dashboard

**Source**: `fix.txt` (user question, 2026-04-07)

**The question**: Add a section to the web dashboard showing which AI model is used at each pipeline stage, with the ability to swap models without editing config files. Useful if hardware is upgraded (bigger model) or if wanting to experiment.

**Current model assignments**:

| Stage | Model | Config location |
|---|---|---|
| Discord agent | `qwen2.5:7b` | `config/openclaw.json` |
| Stage 3 (segment classification) | `qwen3.5:9b` | `scripts/clip-pipeline.sh` |
| Stage 4 Pass B (LLM analysis) | `qwen3.5:9b` | `scripts/clip-pipeline.sh` |
| Stage 6 (vision enrichment) | `qwen3-vl:8b` | `scripts/clip-pipeline.sh` |
| Stages 2 & 7 (transcription) | `whisper large-v3` | `Dockerfile` (baked in) |

**What would need to change for a UI switcher**:

1. **Dashboard UI**: dropdown per stage showing available Ollama models (fetched from `ollama list` API)
2. **Config storage**: save model selections to a JSON file (e.g., `config/model-overrides.json`)
3. **Pipeline reads overrides**: `clip-pipeline.sh` checks for `config/model-overrides.json` and substitutes model names
4. **OpenClaw config**: Discord agent model updated in `openclaw.json` (already a config file, just needs a write endpoint)
5. **Whisper**: cannot be swapped via UI (baked into Docker image at build time) — could display as read-only

**Risk**: Swapping models without understanding VRAM implications could cause OOM errors. The UI should display model VRAM costs and warn if the selected combination exceeds available VRAM.

**Not yet implemented.** See [[entities/dashboard]].

---

## Q4 — qwen3.5:9b vision broken in Ollama

**Source**: Project summary (2026)

**The question**: When will Ollama fix qwen3.5:9b GGUF vision inference? Until then, all vision tasks route to qwen3-vl:8b.

**Current state**: The GGUF multimodal projector for qwen3.5:9b isn't handled correctly by Ollama as of early 2026. Vision calls silently fail or produce garbage.

**Implication**: Stage 6 cannot use qwen3.5:9b even though its architecture supports vision. The model routing is `qwen3.5:9b → text tasks only`, `qwen3-vl:8b → all vision tasks`.

**Watch for**: Ollama releases and qwen3.5 GGUF updates. Once fixed, consolidating to one model for both text and vision could reduce VRAM swapping.

---

## Q5 — Container dashboard zombie process

**Source**: `DEVELOPMENT_SUMMARY.txt` (BUG 10)

**Status**: Open, low priority.

The dashboard Flask app inside the Docker container can become a zombie process if it crashes on startup. The local Windows dashboard works fine and is the primary interface, so this is a convenience issue only.

**Needs investigation**: What import or startup error causes the crash inside the container? Likely a missing dependency or path issue not present on the Windows host.

See [[concepts/bugs-and-fixes]] BUG 10.

---

## Related
- [[concepts/clip-rendering]] — fixed 45s clip length (Q2)
- [[concepts/highlight-detection]] — scoring system (Q1)
- [[entities/dashboard]] — model switcher UI location (Q3)
- [[entities/qwen35]] — vision broken note (Q4)
- [[entities/qwen3-vl]] — currently handles all vision tasks (Q4)
