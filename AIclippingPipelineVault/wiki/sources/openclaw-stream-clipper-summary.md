---
title: "OpenClaw Stream Clipper — Detailed System Summary"
type: source
tags: [source, architecture, reference]
file: "OpenClaw_Stream_Clipper_Summary.md"
location: project root
ingested: 2026-04-07
---

# Source: OpenClaw Stream Clipper — Detailed System Summary

**File**: `OpenClaw_Stream_Clipper_Summary.md` (project root)
**Type**: Comprehensive system documentation written by the project author
**Scope**: Full architecture, pipeline stages, AI models, hardware requirements, configuration reference, operational commands, technology stack

---

## What this document covers

The primary reference document for the OpenClaw Stream Clipper project. Covers:

- Two-container Docker architecture (Ollama + stream-clipper)
- All three AI models: Whisper large-v3-turbo, Qwen3-VL 8B, Qwen 3.5 9B
- The complete 7-stage clipping pipeline
- Discord bot integration
- Hardware requirements and performance benchmarks (GPU and CPU modes)
- Docker Compose portability and deployment steps
- Full file structure
- Configuration reference for `openclaw.json`, `SKILL.md`, `AGENTS.md`
- Operational commands for managing the stack

---

## Key facts extracted

- Models run **sequentially** — intentional design to avoid VRAM contention
- Whisper runs on **CPU always** — keeps GPU free for vision model
- Qwen 3.5 GGUF **vision inference is broken in Ollama** as of March 2026 (multimodal projector issue)
- Clip threshold: **≥7/10** virality score required to produce a clip
- Output format: **1080×1920 H.264 CRF 23** with burned-in subtitles
- Whisper model weights are **baked into the Docker image** (no first-run download)
- Ollama models stored in **named Docker volume** (survives container restarts)
- `OLLAMA_KEEP_ALIVE=5m` — model unloads after 5 minutes idle

---

## Pages this source informed

- [[overview]]
- [[entities/openclaw]]
- [[entities/ollama]]
- [[entities/qwen3-vl]]
- [[entities/qwen35]]
- [[entities/faster-whisper]]
- [[entities/ffmpeg]]
- [[entities/discord-bot]]
- [[concepts/clipping-pipeline]]
- [[concepts/highlight-detection]]
- [[concepts/vram-budget]]
- [[concepts/deployment]]
