---
title: "Stage-Specific Model Split — Phase 5.1"
type: concept
tags: [models, vram, phase-5, configuration, stage-4, stage-6, infrastructure]
sources: 2
updated: 2026-04-24
---

# Stage-Specific Model Split (Phase 5.1)

Per `ClippingResearch.md` §8.1 and §8.9: running a single multimodal model (Gemma 4 26B, Qwen 3.5-35B) for both Pass B text classification AND Stage 6 vision enrichment is wasteful — Pass B runs ~30× more often than Stage 6, and vision models pay the image-encoder cost on every call even when no image is involved.

Phase 5.1 adds **optional** per-stage model overrides in `config/models.json`. Backwards-compatible: when the overrides are `null` (the default), every stage uses the unified `text_model` / `vision_model` exactly as before.

---

## Configuration

`config/models.json`:

```json
{
  "text_model":          "google/gemma-4-26b-a4b",   // used by Stage 3 segment classify (always)
  "vision_model":        "google/gemma-4-26b-a4b",   // fallback for Stage 6 when override is null
  "text_model_passb":    null,                        // overrides text_model for Pass B only
  "vision_model_stage6": null,                        // overrides vision_model for Stage 6 only
  "whisper_model":       "large-v3-turbo",
  "llm_url":             "http://host.docker.internal:1234",
  "context_length":      32768
}
```

**Stage → model mapping:**

| Stage | Model env var (pipeline) | Source |
|---|---|---|
| Stage 3 segment classify | `TEXT_MODEL` | always `text_model` |
| Stage 4 Pass B moment detect | `TEXT_MODEL_PASSB` | `text_model_passb` ?? `text_model` |
| Stage 6 vision enrichment | `VISION_MODEL_STAGE6` | `vision_model_stage6` ?? `vision_model` |
| Stage 2 / 7 Whisper | `WHISPER_MODEL` | always `whisper_model` |

**Dashboard** (`dashboard/app.py`) forwards the overrides as env vars when set, both for `docker exec` and direct-subprocess launches. No UI changes — edit `config/models.json` directly to opt in.

---

## Recommended split (48 GB VRAM rig)

Per the research doc's §8.9 cost analysis:

```json
{
  "text_model_passb":    "qwen/qwen3-32b",
  "vision_model_stage6": "qwen/qwen3-vl-8b"
}
```

- **Qwen3-32B text-only** for Pass B: ~28 GB BF16, non-thinking (explicitly off via `/no_think` sentinel from Phase 0.2), 128K YaRN context. ~3× cheaper than the unified multimodal model for Pass B's pure classification workload.
- **Qwen3-VL-8B FP8** for Stage 6: ~10 GB, Apache 2.0, strongest on Twitch UI OCR per OCRBench 896 (vs 885 for the unified Gemma), ScreenSpot-Pro 61.8 validates UI grounding.

The two can be **co-resident** on a 48 GB GPU (38 GB total) — no hot-swap needed between Stage 4 and Stage 6. On 24 GB rigs, stick with the unified config.

Optional: keep `text_model = "google/gemma-4-26b-a4b"` for Stage 3 segment classification, where Gemma's better cross-lingual robustness can help VODs with multilingual chat overlays.

---

## VRAM choreography

The pipeline's model-loading logic (lines 117 / 126) compares effective stage-specific names:

```
# Stage 2 prep: unload ALL four possible model slots before Whisper gets GPU.
unload_model "$TEXT_MODEL"
unload_model "$VISION_MODEL"
[ "$TEXT_MODEL_PASSB" != "$TEXT_MODEL" ]   && unload_model "$TEXT_MODEL_PASSB"
[ "$VISION_MODEL_STAGE6" != "$VISION_MODEL" ] && unload_model "$VISION_MODEL_STAGE6"

# Stage 3 prep: load TEXT_MODEL.
load_model "$TEXT_MODEL"

# Stage 3 → Stage 4 swap: if Pass B's model differs, swap now.
[ "$TEXT_MODEL_PASSB" != "$TEXT_MODEL" ] && {
    unload_model "$TEXT_MODEL"
    load_model "$TEXT_MODEL_PASSB"
}

# Stage 5 → Stage 6 swap: same check.
[ "$TEXT_MODEL_PASSB" != "$VISION_MODEL_STAGE6" ] && {
    unload_model "$TEXT_MODEL_PASSB"
    load_model "$VISION_MODEL_STAGE6"
}

# Stage 6 → Stage 7 prep: unload vision before Whisper caption transcription.
unload_model "$VISION_MODEL_STAGE6"
```

Unified config (all four names resolve to the same model) → every `!=` check is false, and every swap is a no-op. Zero behavior change.

---

## Not shipped in Phase 5.1

Per `IMPLEMENTATION_PLAN.md`:

- **Stage 6a text classifier** — a text-only pass BEFORE Stage 6 that produces `{what_happened, category, confidence}` from transcript + chat + OCR, then Stage 6 vision treats that as a hard constraint and generates style-consistent title/hook/description. Requires a real Stage 6 rewrite (currently Stage 6 is a single call). Deferred.
- **Top-5 % vision escalation** — swap to Qwen3-VL-32B AWQ INT4 for the highest-scoring 5 % of candidates. Needs orchestration for hot-swap; defer until there's a cost/quality eval harness (Phase 5.3's bootstrap dataset).

---

## Related

- [[entities/lm-studio]] — model loader
- [[concepts/vram-budget]] — per-model VRAM costs
- [[concepts/highlight-detection]] — Pass B consumer
- [[concepts/vision-enrichment]] — Stage 6 consumer
- `config/models.json` — runtime config
- `IMPLEMENTATION_PLAN.md` Phase 5.1 — definition + deferred Stage 6a
