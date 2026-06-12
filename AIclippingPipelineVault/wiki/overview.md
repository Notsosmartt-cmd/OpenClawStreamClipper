---
title: "OpenClaw Stream Clipper — Overview"
type: overview
tags: [overview, architecture, pipeline, originality, hub]
sources: 3
updated: 2026-06-12
---

# OpenClaw Stream Clipper

A self-contained system that automatically finds and extracts highlight moments from livestream VODs using locally-hosted AI models. Zero cloud API costs. Controlled through Discord or a web dashboard.

---

## What it does

1. User drops a `.mp4` or `.mkv` VOD into `vods/`
2. User sends a natural-language command to the Discord bot ("clip the lacy stream", "find the funny moments")
3. The system runs an 8-stage pipeline: transcribes audio, detects high-energy moments using keywords + LLM analysis, scores frames with a vision model, renders clips, and delivers them back to Discord

Output: 1080×1920 vertical MP4 clips (~45 seconds each) with burned-in subtitles and a blurred-fill background. Ready for TikTok, Reels, Shorts.

---

## Architecture: bare-metal Windows + native LM Studio

> [!note] Bare-metal Windows is the default (since 2026-06-04)
> The system runs **fully natively on Windows** — no Docker container, no WSL.
> The bash pipeline became a pure-Python orchestrator (`scripts/run_pipeline.py`),
> the Flask dashboard and the OpenClaw/Discord gateway run as native processes,
> and only LM Studio is unchanged. The retired Docker path lives under `legacy/`
> (see the Legacy section below). Full detail: [[concepts/bare-metal-windows]].

| Component | Where | Role |
|---|---|---|
| [[entities/lm-studio]] | Windows host (native) | LLM inference server — serves Qwen models over OpenAI-compatible HTTP on port 1234 |
| Python orchestrator | Windows host (native, `.venv`) | `scripts/run_pipeline.py` + `scripts/pipeline/stages/stage{1..8}.py`; FFmpeg + faster-whisper via PATH/venv |
| Dashboard | Windows host (native, Flask :5001) | VOD library, clip controls, 8-stage monitor, SSE log stream |
| OpenClaw / Discord | Windows host (native, Node 22+) | Discord gateway → exec → `clip.cmd` → `run_pipeline.py` |

The pipeline calls LM Studio at `http://localhost:1234`. The user manages LM Studio (model loading, GPU assignment) through LM Studio's own GUI; the pipeline only calls `/v1/chat/completions` and manages VRAM via the bundled `lms` CLI (load/unload between stages). Heavy Python modules in `scripts/lib/` are reused unchanged — the port rewrote only the bash glue into Python.

**Why LM Studio**: it runs natively on Windows and supports NVIDIA+AMD multi-GPU without WSL2 Vulkan driver hacks. No Vulkan ICD injection issues. GPU assignment is handled through LM Studio's GUI. See [[entities/lm-studio]].

### Legacy: Docker "one container" (pre-2026-06-04, files under `legacy/`)

Before the bare-metal port the system shipped as **one Docker container + native Windows LM Studio**:

| Component | Where | Role |
|---|---|---|
| [[entities/lm-studio]] | Windows host (native) | LLM inference server on port 1234 |
| Stream Clipper | Docker container (`stream-clipper`) | OpenClaw agent, Discord gateway, FFmpeg, faster-whisper, web dashboard |

In that path the pipeline called LM Studio at `http://host.docker.internal:1234` — Docker's bridge hostname routing from the container back to the Windows host — and the dashboard reached the pipeline through a `docker exec` bridge. The container ran the bash pipeline (`legacy/clip-pipeline.sh`). The Docker files (`legacy/Dockerfile`, `legacy/docker-compose.yml`, `legacy/entrypoint*.sh`, `legacy/clip-pipeline.sh` and stage scripts) are retained for reference / rollback. The opt-in `CLIP_USE_DOCKER=1` flag still drives the container path. See [[concepts/deployment]].

---

## AI models

| Role (config key) | Current ID | Stages |
|---|---|---|
| `text_model` | `qwen/qwen3.6-35b-a3b` (unified MoE, ~3B active, thinking off) | Stage 3 + Pass B/D |
| `vision_model` | `qwen/qwen3.6-35b-a3b` (same unified MoE, multimodal) | Stage 6 + Vision Judge |
| [[entities/faster-whisper]] | `large-v3-turbo` (~2.5× faster) | Stages 2 + 7 |

Model IDs are set via `config/models.json` (dashboard Models panel). As of 2026-06-12 `text_model` **and** `vision_model` are both `qwen/qwen3.6-35b-a3b` — a single unified MoE serves both text detection and vision enrichment (the older qwen3.5-9b text / gemma-4-12b vision split is retired). Per-stage overrides (`text_model_passb`, `vision_model_stage6`) default to `null` and fall back to the unified model; they only diverge if a larger rig sets them explicitly. The pipeline unloads/swaps models between stages via the `lms` CLI.

> [!note] Choosing models (see [[concepts/model-split]])
> **Thinking off almost everywhere** — research + BUG 20/57 show reasoning gives no benefit (and can exhaust `max_tokens`) for the pipeline's extraction/classification/generation work; the only candidate is the Vision Judge. The 35B-A3B's mandatory thinking made one run **135 min (Stage 4 = 49%)**. **Two model tiers:** *speed* = small dense on CUDA/NVIDIA-only; *quality* = a big **MoE with thinking off** (`qwen3.6-35b-a3b` ~3B active / `gemma-4-26b-a4b` ~4B active) — better clips at near-small-model compute, fits the **~28 GB** dual-GPU Vulkan pool (RTX 5060 Ti 16 GB + AMD RX 6700 XT 12 GB). The unified `qwen3.6-35b-a3b` is the current default for both roles. See [[concepts/vram-budget]].

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

**Web dashboard** (secondary): Flask app on port 5001. VOD library, clip controls, 8-stage progress monitor, live log streaming via SSE, clips gallery. Runs natively on the Windows host (the `docker exec` bridge is dead in native mode, kept only for the opt-in `CLIP_USE_DOCKER` path). See [[entities/dashboard]].

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
- **Bare-metal over Docker** (2026-06-04): Docker was the source of most environmental complexity (the `docker exec` bridge, `host.docker.internal` rewriting, WSL2 file-boundary tax). The pipeline is now a native Python orchestrator; only LM Studio is unchanged. See [[concepts/bare-metal-windows]]
- **One model in VRAM at a time**: explicit unloading between stages prevents OOM; the pipeline uses the `lms` CLI (`lms load`/`lms unload`) at stage transitions, falling back to LM Studio REST when `lms` isn't found
- **Whisper runs on GPU when available**: faster-whisper / CTranslate2 on NVIDIA CUDA (RTX 5060 Ti, sm_120); unloads LLM model first; CPU int8 fallback
- **Vision is non-gatekeeping**: vision enrichment can only boost scores, never eliminate clips; frame content is often visually boring even when audio is clip-worthy
- **Vision now *selects*, not just titles** (2026-06-04): the Stage 5.5 [[entities/vision-judge]] runs a pairwise tournament to re-rank what gets clipped; Pass C folds in bounded, failure-soft per-axis signals (arc / reaction / baseline / engagement) under one global multiplier clamp — never gating, only re-ranking. See [[concepts/clipping-quality-overhaul]]
- **Everything is measured** (2026-06-04): per-run `axis_report` + per-stage `stage_timings` + the judge bracket + per-line log timestamps, read back via `logtool axes` — the tune→run→diff loop. See [[concepts/observability]]
- **Time-bucket distribution**: prevents early-VOD bias by guaranteeing clip selection from each time bucket
- **Blur-fill rendering**: full 16:9 content visible on 9:16 canvas — no information loss from hard crop
- **Unified model inside the pipeline; a separate small model for the Discord agent**: `config/models.json` sets both `text_model` and `vision_model` to the one unified MoE `qwen/qwen3.6-35b-a3b` (the older qwen3.5-9b-text / gemma-4-12b-vision split is retired). The **OpenClaw Discord agent is a *different*, smaller model** — `config/openclaw.json` sets `agents.defaults.model.primary` to `qwen/qwen3.5-9b` (fallback `qwen/qwen3-vl-8b`) for reliable tool-calling, superseding the original [[entities/qwen25]] (`qwen2.5:7b`). So "agent model" (qwen3.5-9b) and "pipeline model" (qwen3.6-35b-a3b) are not the same — see [[concepts/model-split]].

---

## Project files

### Current (bare-metal, native Windows)

| File | Purpose |
|---|---|
| `scripts/run_pipeline.py` | Python orchestrator (replaces `clip-pipeline.sh`). Arg parse (`--style/--vod/--type/--list/--force/--all`), config resolution (env → `models.json` → defaults), logging tee, pid/done markers, signal handling, 8-stage dispatch, cleanup. See [[concepts/bare-metal-windows]] |
| `scripts/pipeline/common.py` | Orchestrator helpers: `Logger` tee, `set_stage`, model load/unload/verify (via `lms` CLI / LM Studio REST), `run_module`, cleanup. Replaces `pipeline_common.sh` |
| `scripts/pipeline/stages/stage{1..8}.py` | One module per pipeline stage, each exposing `run(ctx)`; ported 1:1 from the old bash stages |
| `scripts/lib/**.py` | Heavy Python (moment detection, vision, rendering, etc.) reused unchanged; invoked via `subprocess.run([sys.executable, ...])` |
| `scripts/lib/paths.py` | Single source of truth for paths; `child_env()` builds the subprocess env (CUDA DLL dirs, PYTHONPATH, per-feature config vars) |
| `clip.cmd` / `start.ps1` | Native launcher + startup script (replaces `entrypoint.sh`): junctions `~/.openclaw`→`config\`, waits for LM Studio, starts dashboard + OpenClaw gateway |
| `config/models.json` | `text_model` / `vision_model` / `whisper_model`, context length, per-stage overrides |
| `config/openclaw.json` | Model providers, agent config, compaction settings, Discord channels |
| `config/exec-approvals.json` | Command execution allowlist for the agent |
| `workspace/AGENTS.md` | Agent behavior rules, style/type inference, exec rules (exec now calls `clip.cmd …`) |
| `workspace/skills/stream-clipper/SKILL.md` | Skill triggers and pipeline invocation guide |
| `dashboard/app.py` | Flask dashboard backend (native run-mode, port 5001) — REST API + SSE |
| `dashboard/templates/index.html` | Single-page dark-themed dashboard UI |
| `dashboard/static/app.js` | Vanilla JS client, SSE streaming, status |

### Legacy (pre-2026-06-04, files under `legacy/`)

| File | Purpose |
|---|---|
| `legacy/docker-compose.yml` | Single `stream-clipper` service; NVIDIA GPU for Whisper; `extra_hosts` for `host.docker.internal` |
| `legacy/Dockerfile` | CUDA 12.3 + Node.js 22 + Python + OpenClaw + Whisper large-v3 (stream-clipper image) |
| `legacy/clip-pipeline.sh` | Former bash pipeline orchestrator (147-line thin version after the 2026-05-01 modularization; sourced `pipeline_common.sh` + `stages/stage{1..8}.sh`). Superseded by `scripts/run_pipeline.py` |
| `legacy/entrypoint.sh` | Container startup: LM Studio wait, gateway + dashboard start |
| `legacy/pipeline_common.sh`, `legacy/stages/*.sh` | Former bash helpers + stage bodies |
