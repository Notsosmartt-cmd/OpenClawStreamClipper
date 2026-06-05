---
title: "OpenClaw Stream Clipper — Overview"
type: overview
tags: [overview, architecture, pipeline, originality, hub]
sources: 3
updated: 2026-06-04
---

# OpenClaw Stream Clipper

A self-contained, Docker-based system that automatically finds and extracts highlight moments from livestream VODs using locally-hosted AI models. Zero cloud API costs. Controlled through Discord or a web dashboard.

---

## What it does

1. User drops a `.mp4` or `.mkv` VOD into `vods/`
2. User sends a natural-language command to the Discord bot ("clip the lacy stream", "find the funny moments")
3. The system runs an 8-stage pipeline: transcribes audio, detects high-energy moments using keywords + LLM analysis, scores frames with a vision model, renders clips, and delivers them back to Discord

Output: 1080×1920 vertical MP4 clips (~45 seconds each) with burned-in subtitles and a blurred-fill background. Ready for TikTok, Reels, Shorts.

---

## Architecture: one container + native Windows LLM

> [!note] Bare-metal Windows is now the default (2026-06-04)
> The system was ported off Docker to run fully natively on Windows — the bash
> pipeline became a Python orchestrator (`scripts/run_pipeline.py`), the
> dashboard and OpenClaw/Discord run as native processes, and only LM Studio is
> unchanged. The Docker description below is retained as the legacy path (files
> live under `legacy/`). See [[concepts/bare-metal-windows]].

| Component | Where | Role |
|---|---|---|
| [[entities/lm-studio]] | Windows host (native) | LLM inference server — serves Qwen models over OpenAI-compatible HTTP on port 1234 |
| Stream Clipper | Docker container (`stream-clipper`) | OpenClaw agent, Discord gateway, FFmpeg, faster-whisper, web dashboard |

The pipeline calls LM Studio at `http://host.docker.internal:1234` — Docker's bridge hostname that routes from the container back to the Windows host. The user manages LM Studio (model loading, GPU assignment) through LM Studio's own GUI; the pipeline never touches it except to call `/v1/chat/completions` and `/api/v1/models/unload`.

**Why LM Studio instead of Ollama-in-Docker**: LM Studio runs natively on Windows and supports NVIDIA+AMD multi-GPU without WSL2 Vulkan driver hacks. No Vulkan ICD injection issues. GPU assignment is handled through LM Studio's GUI. See [[entities/lm-studio]].

---

## AI models

| Role (config key) | Current ID | Stages |
|---|---|---|
| `text_model` | `qwen/qwen3.5-9b` (non-thinking, fast) | Stage 3 + Pass B/D |
| `vision_model` | `google/gemma-4-12b` (multimodal) | Stage 6 + Vision Judge |
| [[entities/faster-whisper]] | `large-v3-turbo` (~2.5× faster) | Stages 2 + 7 |

Model IDs are set via `config/models.json` (dashboard Models panel). Per-stage overrides (`text_model_passb`, `vision_model_stage6`) inherit the above when null. The pipeline unloads/swaps models between stages.

> [!note] Choosing models (2026-06-04 — see [[concepts/model-split]])
> **Thinking off almost everywhere** — research + BUG 20/57 show reasoning gives no benefit (and can exhaust `max_tokens`) for the pipeline's extraction/classification/generation work; the only candidate is the Vision Judge. The 35B-A3B's mandatory thinking made one run **135 min (Stage 4 = 49%)**. **Two model tiers:** *speed* = small dense (above) on CUDA/NVIDIA-only; *quality* = a big **MoE with thinking off** (`gemma-4-26b-a4b` ~4B active / `qwen3.6-35b-a3b` ~3B active) — better clips at near-small-model compute, fits the **~28 GB** dual-GPU Vulkan pool (RTX 5060 Ti 16 GB + AMD RX 6700 XT 12 GB). A Qwen3-VL would win OCR but PaddleOCR softens that. See [[concepts/vram-budget]].

---

## The pipeline

```
Stage 1: Discovery            — find new/named VOD files
Stage 2: Transcription        — WhisperX (large-v3-turbo), cached
Stage 3: Segment Detection    — classify stream type, build profile
Stage 4: Moment Detection     — Pass A keywords + Pass B LLM + Pass C re-rank (selection axes A/B/C/E) + Pass D rubric
Stage 4.5 Moment Groups       — (optional) narrative arcs + stitch bundles
Stage 5: Frame Extraction     — 6 JPEGs per candidate moment
Stage 5.5 Vision Judge        — multimodal tournament re-rank: lets vision SELECT the winners ([[entities/vision-judge]])
Stage 6: Vision Enrichment    — titles, hook, originality hints
Stage 6.5 Camera Pan Prep     — (optional) OpenCV face tracking → crop path
Stage 7: Editing & Export     — framing + originality + stitch render, batch captions
Stage 8: Logging              — processed.log, diagnostics JSON, Discord report
```

Full detail: [[concepts/clipping-pipeline]]. For the originality-stack sub-stages and render additions, see [[concepts/originality-stack]].

> [!note] Selection-intelligence overhaul (2026-06-04)
> Selection is no longer transcript-only. Stage 4 Pass C now folds in **per-axis selection signals** — A arc-completeness, B reaction-worthy, C baseline-contrast, E engagement (D batch-diversity deferred) — each a bounded, failure-soft multiplier accumulated under one global clamp. The new **Stage 5.5 Vision Judge** then runs a pairwise tournament so the multimodal model finally *selects* what gets clipped (not just titles it). All of it is instrumented — see [[concepts/clipping-quality-overhaul]] (roadmap), [[entities/vision-judge]], and [[concepts/observability]] (`logtool axes`).

---

## Two interfaces

**Discord bot** (primary): Natural-language commands. Style and stream type inferred automatically. Results delivered as inline video attachments. See [[entities/discord-bot]].

**Web dashboard** (secondary): Flask app on port 5000. VOD library, clip controls, 8-stage progress monitor, live log streaming via SSE, clips gallery. Works on Windows host via `docker exec` bridge. See [[entities/dashboard]].

---

## Clip styles

| Command intent | Style | Prioritizes |
|---|---|---|
| Default / "clip my stream" | `auto` | Best moments, balanced variety |
| "funny", "comedy" | `funny` | Comedy, awkward moments, banter |
| "hype", "exciting" | `hype` | Clutch plays, high-energy |
| "emotional" | `emotional` | Heartfelt, vulnerable moments |
| "spicy takes", "hot take" | `hot_take` | Controversial opinions |
| "storytime", "story" | `storytime` | Narratives with payoff |
| "reactions", "rage" | `reactive` | Strong reactions, shock |
| "mix", "variety" | `variety` | One clip from each category |

---

## Dynamic clip count

3 clips per hour of stream, minimum 3, maximum 20:

| Stream length | Target clips |
|---|---|
| 1 hour | 3 |
| 2 hours | 6 |
| 4 hours | 12 |
| 7+ hours | 20 (capped) |

---

## Key design decisions

- **Local-only inference**: all models run on user hardware; no API keys, no per-token costs
- **LM Studio for LLM inference**: runs natively on Windows; supports NVIDIA+AMD multi-GPU without WSL2 driver hacks; OpenAI-compatible API
- **One model in VRAM at a time**: explicit unloading between stages prevents OOM; pipeline calls `POST /api/v1/models/unload` at stage transitions
- **Whisper runs on GPU when available**: runs inside Docker with NVIDIA CUDA; unloads LLM model first; CPU int8 fallback
- **Vision is non-gatekeeping**: vision enrichment can only boost scores, never eliminate clips; frame content is often visually boring even when audio is clip-worthy
- **Vision now *selects*, not just titles** (2026-06-04): the Stage 5.5 [[entities/vision-judge]] runs a pairwise tournament to re-rank what gets clipped; Pass C folds in bounded, failure-soft per-axis signals (arc / reaction / baseline / engagement) under one global multiplier clamp — never gating, only re-ranking. See [[concepts/clipping-quality-overhaul]]
- **Everything is measured** (2026-06-04): per-run `axis_report` + per-stage `stage_timings` + the judge bracket + per-line log timestamps, read back via `logtool axes` — the tune→run→diff loop. See [[concepts/observability]]
- **Time-bucket distribution**: prevents early-VOD bias by guaranteeing clip selection from each time bucket
- **Blur-fill rendering**: full 16:9 content visible on 9:16 canvas — no information loss from hard crop
- **Two text models**: `qwen3.5-9b-instruct` for pipeline (better moment detection), `qwen2.5-7b-instruct` for Discord (reliable tool calling with small models)

---

## Project files

| File | Purpose |
|---|---|
| `docker-compose.yml` | Single `stream-clipper` service; NVIDIA GPU for Whisper; `extra_hosts` for `host.docker.internal` |
| `Dockerfile` | CUDA 12.3 + Node.js 22 + Python + OpenClaw + Whisper large-v3 (stream-clipper image) |
| `scripts/clip-pipeline.sh` | Thin 147-line orchestrator. Sources `scripts/lib/pipeline_common.sh` and `scripts/stages/stage{1..8}.sh`. Embedded Python lives in `scripts/lib/stages/`. Modularized 2026-05-01 — see [[concepts/modularization-plan]]. |
| `scripts/entrypoint.sh` | Container startup: LM Studio wait, gateway + dashboard start |
| `config/openclaw.json` | Model providers, agent config, compaction settings, Discord channels |
| `config/exec-approvals.json` | Command execution allowlist for the agent |
| `workspace/AGENTS.md` | Agent behavior rules, style/type inference, exec rules |
| `workspace/skills/stream-clipper/SKILL.md` | Skill triggers and pipeline invocation guide |
| `dashboard/app.py` | Flask dashboard backend — REST API + SSE + docker exec bridge |
| `dashboard/templates/index.html` | Single-page dark-themed dashboard UI |
| `dashboard/static/app.js` | Vanilla JS client, SSE streaming, Docker status |
