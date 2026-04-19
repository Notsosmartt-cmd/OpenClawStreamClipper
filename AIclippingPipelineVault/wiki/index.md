# Wiki Index

Content catalog. Updated on every ingest. Read this first when answering queries — find relevant pages here, then drill in.

---

## Overview
- [[overview]] — Full synthesis: architecture, pipeline, models, interfaces, design decisions

## Entities

### Models
- [[entities/faster-whisper]] — Speech-to-text; large-v3 on GPU (float16) or CPU (int8); Stages 2 and 7
- [[entities/qwen35]] — Pipeline text model (`qwen/qwen3.5-9b` or `qwen/qwen3.5-35b-a3b`); segment classification + moment analysis; 35B has permanent thinking mode
- [[entities/qwen3-vl]] — Vision model (`qwen/qwen3-vl-8b`); non-gatekeeping enrichment; Stage 6
- [[entities/qwen25]] — Discord agent model (older reference; current setup uses same LM Studio model for agent and pipeline)

### Infrastructure
- [[entities/openclaw]] — Agent framework (Node.js); Discord gateway; runs exec tool to invoke pipeline
- [[entities/lm-studio]] — LLM inference server (native Windows); OpenAI-compatible API on port 1234; 9B vs 35B behavior; reasoning_content fallback
- [[entities/ollama]] — *Retired* — former LLM inference container; replaced by LM Studio as of 2026-04-18
- [[entities/ffmpeg]] — Video/audio processing; blur-fill 9:16 rendering; subtitle burn-in
- [[entities/discord-bot]] — Primary user interface; natural-language commands; delivers clip attachments
- [[entities/dashboard]] — Web UI (Flask, port 5000); 8-stage monitor; SSE streaming; docker exec bridge; Models + Hardware panels

## Concepts

### Pipeline
- [[concepts/clipping-pipeline]] — All 8 stages with detail, performance table, temp files
- [[concepts/segment-detection]] — Stage 3: 5-type classification, stream profile, segment-aware weighting
- [[concepts/highlight-detection]] — Stage 4: three-pass (keywords + LLM + merge/time-bucket/select)
- [[concepts/vision-enrichment]] — Stage 6: non-gatekeeping design, score blending, thinking model handling
- [[concepts/clip-rendering]] — Stage 7: blur-fill 9:16, batch captions, FFmpeg filter chain, subtitle style

### System
- [[concepts/vram-budget]] — Per-model VRAM, stage-by-stage orchestration, explicit unloading sequence
- [[concepts/context-management]] — Token compaction, session reset, history limit, compat flags
- [[concepts/deployment]] — Hardware requirements, LM Studio setup, Docker setup, step-by-step guide

### Reference
- [[concepts/bugs-and-fixes]] — 21 bugs documented; symptoms, root causes, solutions (includes 35B thinking mode bugs)
- [[concepts/open-questions]] — Score normalization, variable clip length, model switcher UI, known gaps

## Sources
- [[sources/openclaw-stream-clipper-summary]] — Full system architecture doc (project summary)
- [[sources/development-summary]] — Feature list, 10 bugs, file inventory, current state (2026-04-04)
- [[sources/fix-txt]] — User questions: score normalization, variable clip length, model switcher
