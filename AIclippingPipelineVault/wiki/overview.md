---
title: "OpenClaw Stream Clipper — Overview"
type: overview
tags: [overview, architecture, pipeline]
sources: 2
updated: 2026-04-07
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

## Two-container architecture

| Container | Name | Role |
|---|---|---|
| Ollama | `ollama-gpu` / `ollama-cpu` | LLM inference server — hosts Qwen models over HTTP on port 11434 |
| Stream Clipper | `stream-clipper-gpu` / `stream-clipper-cpu` | OpenClaw agent, Discord gateway, FFmpeg, faster-whisper, web dashboard |

The two communicate over a Docker bridge network (`clipper-net`). [[entities/openclaw]] calls [[entities/ollama]] at `http://ollama:11434`. The user never touches Ollama directly.

---

## Four AI models

| Model | Role | Hardware |
|---|---|---|
| [[entities/qwen25]] `qwen2.5:7b` | Discord bot agent — tool calling, user interaction | GPU preferred |
| [[entities/qwen35]] `qwen3.5:9b` | Pipeline text — segment classification, moment analysis | GPU preferred |
| [[entities/qwen3-vl]] `qwen3-vl:8b` | Vision enrichment — titles, descriptions, score boosts | GPU preferred |
| [[entities/faster-whisper]] `large-v3` | Speech-to-text transcription | GPU (float16) → CPU (int8) fallback |

Only **one model occupies VRAM at a time**. The pipeline explicitly unloads models between stages. See [[concepts/vram-budget]].

---

## The 8-stage pipeline

```
Stage 1: Discovery         — find new/named VOD files
Stage 2: Transcription     — chunked GPU Whisper, cached
Stage 3: Segment Detection — classify stream type, build profile
Stage 4: Moment Detection  — Pass A keywords + Pass B LLM + Pass C merge/select
Stage 5: Frame Extraction  — 6 JPEGs per candidate moment
Stage 6: Vision Enrichment — score boosts, titles, descriptions (non-gatekeeping)
Stage 7: Editing & Export  — blur-fill 9:16, batch captions, FFmpeg render
Stage 8: Logging           — processed.log, diagnostics JSON, Discord report
```

Full detail: [[concepts/clipping-pipeline]]

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
- **One model in VRAM at a time**: explicit unloading between stages prevents OOM; `OLLAMA_MAX_LOADED_MODELS=1`
- **Whisper runs on GPU when available**: unloads Ollama first, loads Whisper GPU, then reverses; CPU int8 fallback
- **Vision is non-gatekeeping**: vision enrichment can only boost scores, never eliminate clips; frame content is often visually boring even when audio is clip-worthy
- **Time-bucket distribution**: prevents early-VOD bias by guaranteeing clip selection from each time bucket
- **Blur-fill rendering**: full 16:9 content visible on 9:16 canvas — no information loss from hard crop
- **Two text models**: `qwen3.5:9b` for pipeline (better moment detection), `qwen2.5:7b` for Discord (reliable tool calling with small models)

---

## Project files

| File | Purpose |
|---|---|
| `docker-compose.yml` | Containers, volumes, GPU/CPU profiles, Ollama env vars |
| `Dockerfile` | CUDA 12.3 + Node.js 22 + Python + OpenClaw + Whisper large-v3 |
| `scripts/clip-pipeline.sh` | The full 8-stage pipeline (~1,700 lines) |
| `scripts/entrypoint.sh` | Container startup: Ollama wait, model pull, gateway + dashboard start |
| `config/openclaw.json` | Model providers, agent config, compaction settings, Discord channels |
| `config/exec-approvals.json` | Command execution allowlist for the agent |
| `workspace/AGENTS.md` | Agent behavior rules, style/type inference, exec rules |
| `workspace/skills/stream-clipper/SKILL.md` | Skill triggers and pipeline invocation guide |
| `dashboard/app.py` | Flask dashboard backend — REST API + SSE + docker exec bridge |
| `dashboard/templates/index.html` | Single-page dark-themed dashboard UI |
| `dashboard/static/app.js` | Vanilla JS client, SSE streaming, Docker status |
