---
title: "VRAM Budget and Model Orchestration"
type: concept
tags: [vram, gpu, memory, performance, models, orchestration]
sources: 2
updated: 2026-04-18
---

# VRAM Budget and Model Orchestration

The stream clipper uses four AI models across different pipeline stages. Only **one model occupies VRAM at a time**. The pipeline manages this explicitly via `unload_model()` in `clip-pipeline.sh`, which calls `POST /api/v1/models/unload` on [[entities/lm-studio]] at stage transitions. Whisper VRAM is managed separately (runs inside Docker, not through LM Studio).

---

## Per-model VRAM

| Model | VRAM | Context |
|---|---|---|
| [[entities/qwen35]] `qwen3.5:9b` | ~11.2 GB | 32K tokens (capped) |
| [[entities/qwen3-vl]] `qwen3-vl:8b` | ~11.1 GB | 8K tokens (capped) |
| [[entities/qwen25]] `qwen2.5:7b` | ~8.8 GB | 32K tokens |
| [[entities/faster-whisper]] `large-v3` | ~6–7 GB | N/A (audio model) |

Peak VRAM usage at any point: **~11.2 GB** (during Stages 3–4 with qwen3.5:9b). A 16GB GPU handles this comfortably with ~5GB headroom.

---

## Stage-by-stage VRAM state

> [!note] Two separate VRAM pools
> LLM models (qwen3.5, qwen3-vl, qwen2.5) run in **LM Studio on Windows** (native VRAM).
> Whisper runs **inside the Docker container** (NVIDIA CUDA VRAM via Container Toolkit).
> The pipeline unloads LM Studio models with `POST /api/v1/models/unload` before loading Whisper to avoid OOM on shared NVIDIA GPU.

```
Start           : No LLM models loaded in LM Studio (JIT load on first request)
                  ↓
Stage 2 prep    : unload_model() → request LM Studio unload any loaded model
Stage 2         : Whisper (Docker) loads → ~6-7GB used → Whisper exits after transcription
                  ↓
Stage 3         : qwen3.5-9b-instruct loads via LM Studio → ~11.2GB used → stays loaded
Stage 4         : qwen3.5-9b-instruct still loaded → ~11.2GB used
                  ↓
Stage 5         : No model needed (FFmpeg only) — LM Studio keeps model resident per TTL
                  ↓
Stage 6 prep    : unload_model(qwen3.5-9b-instruct) → POST /api/v1/models/unload
Stage 6         : qwen2.5-vl-7b-instruct loads via LM Studio → ~8-9GB used
                  ↓
Stage 7 prep    : unload_model(qwen2.5-vl-7b-instruct) → POST /api/v1/models/unload
Stage 7         : Whisper (Docker) loads → ~6-7GB used → Whisper exits after captions
Stage 7 FFmpeg  : 0GB (no model needed) → render clips
                  ↓
Stage 8         : 0GB (no model needed)
```

Discord agent (`qwen2.5-7b-instruct`) loads on demand when the user sends a message — outside the pipeline stages themselves. During pipeline execution, the bot posts status updates before and after, not during.

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

## Whisper vs Ollama memory separation

[[entities/faster-whisper]] runs via Python directly (not through Ollama). Its VRAM is managed separately from Ollama's model pool. The pipeline calls Ollama's unload API before loading Whisper — otherwise both could be in VRAM simultaneously and OOM.

---

## Related
- [[entities/lm-studio]] — the LLM server; GPU assignment via its GUI; `unload` endpoint
- [[entities/faster-whisper]] — uses GPU VRAM separately from LM Studio (runs in Docker)
- [[entities/qwen35]] — highest VRAM consumer
- [[concepts/deployment]] — hardware requirements table
