---
title: "VRAM Budget and Model Orchestration"
type: concept
tags: [vram, gpu, memory, performance, models, orchestration, infrastructure]
sources: 2
updated: 2026-06-04
---

# VRAM Budget and Model Orchestration

The stream clipper uses three AI models across different pipeline stages. Only **one model occupies VRAM at a time**. The pipeline manages this via `unload_model()` in `clip-pipeline.sh`, which calls `POST /api/v1/models/unload` on [[entities/lm-studio]] at stage transitions. Whisper VRAM is managed separately (runs inside Docker, not through LM Studio).

---

## Per-model VRAM

Models are configured in `config/models.json` — the specific model ID and its VRAM footprint depend on what is loaded in LM Studio. Reference figures for common choices:

| Model | VRAM | Context |
|---|---|---|
| `qwen/qwen3.5-9b` — **current `text_model`** (Stage 3 + Pass B/D), non-thinking | ~6.5 GB (Q4) | 32K tokens |
| `google/gemma-4-12b` — **current `vision_model`** (Stage 6 + Judge), multimodal | ~7.6 GB (Q4) | 32K tokens |
| `openai/gpt-oss-20b` — fits; reasoning, dialable effort | ~12.1 GB (MXFP4) | — |
| ⚠️ **exceed 16 GB → CPU spill (slow)**: `qwen3.6-35b-a3b` 22.1 · `gemma-4-31b` 19.9 · `gemma-4-26b-a4b` 18.0 · `qwen3.6-27b` 17.5 GB | >16 GB | — |
| [[entities/faster-whisper]] `large-v3-turbo` (default) / `large-v3` | ~3–4 GB / ~6–7 GB | N/A (audio model) |

Whisper never co-resides with the LLM (the pipeline unloads LM Studio before Stage 2), so turbo's smaller footprint doesn't add LLM headroom — but it loads faster and transcribes ~2.5x quicker. See [[entities/faster-whisper]].

Peak VRAM usage: determined by the configured LLM. On the **16 GB RTX 5060 Ti**, keep the LLM **under ~14 GB** so the KV cache + desktop fit; the installed 27B/26B/31B/35B (≥17.5 GB at Q4) offload layers to **CPU** and run *much* slower — this was ~half the 135-min slow run ([[concepts/model-split]] §Active config). The current `qwen3.5-9b` + `gemma-4-12b` split (6.5 + 7.6 GB) fits with room to co-reside.

---

## Stage-by-stage VRAM state

> [!note] Two separate VRAM pools
> LLM models run in **LM Studio on Windows** (native GPU VRAM).
> Whisper runs **inside the Docker container** (NVIDIA CUDA via Container Toolkit).
> The pipeline calls `POST /api/v1/models/unload` before loading Whisper to prevent both occupying VRAM simultaneously.

### Unified model (single multimodal — e.g. Gemma-4)

When `text_model_passb` and `vision_model_stage6` are both `null` in `config/models.json`, the same model is used for all LLM stages (Pass B, Stage 6, Pass B grounding). No model swap needed between Stages 3–6.

```
Start           : No LLM loaded in LM Studio (JIT load on first request)
                  ↓
Stage 2 prep    : unload_model() → POST /api/v1/models/unload (clear any stale model)
Stage 2         : Whisper (Docker) loads → ~6-7GB used → exits after transcription
                  ↓
Stage 3–4       : LLM loads via LM Studio → stays loaded (same model for text + vision)
Stage 5         : No model needed (FFmpeg only)
Stage 6         : Same LLM still loaded → vision enrichment runs without swap
                  ↓
Stage 7 prep    : unload_model() → POST /api/v1/models/unload
Stage 7         : Whisper (Docker) loads → ~6-7GB used → exits after captions
Stage 7 FFmpeg  : 0GB → render clips
                  ↓
Stage 8         : 0GB
```

### Split model (separate text + vision models)

When `text_model_passb` or `vision_model_stage6` is set to a different model ID in `config/models.json`, the pipeline unloads the text model before Stage 6 and loads the vision model.

```
Stage 3–4   : text model loaded
Stage 6 prep: unload text model → load vision model
Stage 6     : vision model used
Stage 7 prep: unload vision model
```

Discord agent loads its own model on demand outside the pipeline stages — no VRAM conflict during pipeline execution.

---

## Minimum GPU requirements

| Setup | Minimum VRAM | Notes |
|---|---|---|
| Full pipeline, CUDA (NVIDIA) | 12 GB | qwen3.5:9b needs ~11.2GB; 12GB is tight |
| Full pipeline, comfortable | 16 GB | ~5GB headroom for VRAM fluctuation |
| Full pipeline, Vulkan (AMD) | 12 GB | Same model sizes; Whisper runs on CPU |
| CPU-only | 16 GB RAM | All inference on system RAM |

The tested hardware is an RTX 5060 Ti (16GB). An RTX 3090 (24GB) or RTX 4090 would have significant headroom.

**8GB VRAM is borderline**: works with qwen2.5:7b and qwen3-vl but may OOM with qwen3.5:9b at 32K context.

**Vulkan note**: Whisper does not have a Vulkan/CTranslate2 backend. In Vulkan mode, Whisper always runs on CPU (int8). This is enforced by the `entrypoint.sh` hardware config reader — `CLIP_WHISPER_DEVICE` is forced to `cpu` when `gpu_backend` is `vulkan` or `cpu`.

---

## Whisper vs LM Studio memory separation

[[entities/faster-whisper]] runs via Python directly inside the Docker container (not through LM Studio). Its VRAM is allocated by NVIDIA Container Toolkit independently of LM Studio's model pool. The pipeline calls LM Studio's unload API before loading Whisper — otherwise both could be in VRAM simultaneously and OOM.

---

## Related
- [[entities/lm-studio]] — the LLM server; GPU assignment via its GUI; `unload` endpoint
- [[entities/faster-whisper]] — uses GPU VRAM separately from LM Studio (runs in Docker)
- [[entities/qwen35]] — highest VRAM consumer
- [[concepts/deployment]] — hardware requirements table
