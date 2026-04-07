---
title: "VRAM Budget and Model Orchestration"
type: concept
tags: [vram, gpu, memory, performance, models, orchestration]
sources: 2
updated: 2026-04-07
---

# VRAM Budget and Model Orchestration

The stream clipper uses four AI models across different pipeline stages. Only **one model occupies VRAM at a time**. The pipeline manages this explicitly — it doesn't rely on the 5-minute Ollama timeout. It calls the Ollama API with `keep_alive=0` to force-unload models at stage transitions.

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

```
Start           : No models loaded (cold start)
                  ↓
Stage 2 prep    : Unload ALL Ollama models (keep_alive=0)
Stage 2         : Whisper loads → ~6-7GB used → Whisper exits after transcription
                  ↓
Stage 3         : qwen3.5:9b loads → ~11.2GB used → stays loaded
Stage 4         : qwen3.5:9b still loaded → ~11.2GB used
                  ↓
Stage 5         : No model needed (FFmpeg only) → 0GB (model idles, Ollama keeps alive)
                  ↓
Stage 6 prep    : Unload qwen3.5:9b (keep_alive=0)
Stage 6         : qwen3-vl:8b loads → ~11.1GB used
                  ↓
Stage 7 prep    : Unload qwen3-vl:8b (keep_alive=0)
Stage 7         : Whisper loads → ~6-7GB used → Whisper exits after captions
Stage 7 FFmpeg  : 0GB (no model needed) → render clips
                  ↓
Stage 8         : 0GB (no model needed)
```

Discord agent (`qwen2.5:7b`) loads on demand when the user sends a message — outside the pipeline stages themselves. During pipeline execution, the bot posts status updates before and after, not during.

---

## Ollama safety settings

```
OLLAMA_MAX_LOADED_MODELS=1   # prevents multiple models in VRAM simultaneously
OLLAMA_KEEP_ALIVE=5m         # secondary safety: auto-unload after 5min idle
OLLAMA_CONTEXT_LENGTH=32768  # caps context to prevent VRAM blowout at large contexts
```

`OLLAMA_MAX_LOADED_MODELS=1` is the critical guard. Without it, Ollama might load a second model while the first is still resident.

---

## Minimum GPU requirements

| Setup | Minimum VRAM | Notes |
|---|---|---|
| Full pipeline, GPU mode | 12 GB | qwen3.5:9b needs ~11.2GB; 12GB is tight |
| Full pipeline, comfortable | 16 GB | ~5GB headroom for VRAM fluctuation |
| CPU-only | 16 GB RAM | All inference on system RAM |

The tested hardware is an RTX 5060 Ti (16GB). An RTX 3090 (24GB) or RTX 4090 would have significant headroom.

**8GB VRAM is borderline**: the minimum listed in the README is 8GB (works with qwen2.5:7b and qwen3-vl but may OOM with qwen3.5:9b at 32K context).

---

## Whisper vs Ollama memory separation

[[entities/faster-whisper]] runs via Python directly (not through Ollama). Its VRAM is managed separately from Ollama's model pool. The pipeline calls Ollama's unload API before loading Whisper — otherwise both could be in VRAM simultaneously and OOM.

---

## Related
- [[entities/ollama]] — the model server with `MAX_LOADED_MODELS=1`
- [[entities/faster-whisper]] — uses GPU VRAM separately from Ollama
- [[entities/qwen35]] — highest VRAM consumer
- [[concepts/deployment]] — hardware requirements table
