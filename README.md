# OpenClaw Stream Clipper

A self-hosted, AI-powered livestream highlight clipper. Drop a VOD into a folder, tell your Discord bot (or a web dashboard) to clip it, and get back vertical 9:16 highlight clips with burned-in captions, hooks, and optional editing effects — no cloud APIs, no subscriptions, everything runs on your own hardware.

**Stack**: [LM Studio](https://lmstudio.ai) (local LLM inference, native Windows) · [OpenClaw](https://openclaw.ai) (Discord agent framework) · Python 3.11 (bare-metal orchestrator) · [faster-whisper](https://github.com/SYSTRAN/faster-whisper) / [WhisperX](https://github.com/m-bain/whisperX) (GPU speech-to-text) · FFmpeg

> **This README is a snapshot.** The authoritative, continuously-updated knowledge base for this project is the Obsidian wiki at [`AIclippingPipelineVault/wiki/`](AIclippingPipelineVault/wiki/index.md) — start at [`wiki/hot.md`](AIclippingPipelineVault/wiki/hot.md) for current state or [`wiki/overview.md`](AIclippingPipelineVault/wiki/overview.md) for architecture. It has far more depth than this file on every feature below.

---

## Table of Contents

- [How It Works](#how-it-works)
- [Two Interfaces (+ a Third App)](#two-interfaces--a-third-app)
- [The Pipeline](#the-pipeline)
- [Feature Highlights](#feature-highlights)
- [Models](#models)
- [Requirements](#requirements)
- [Setup Guide](#setup-guide)
- [Dashboard](#dashboard)
- [Buffer Clip Poster](#buffer-clip-poster)
- [Usage](#usage)
- [Configuration Files](#configuration-files)
- [Troubleshooting](#troubleshooting)
- [Project Structure](#project-structure)
- [Legacy Docker Path](#legacy-docker-path)

---

## How It Works

```
Discord: "clip the funny moments from the lacy stream"
         │
   OpenClaw Agent (LM Studio, qwen3.5-9b)
   → infers: --style funny --vod lacy
   → calls exec: clip.cmd --style funny --vod lacy
         │
   scripts/run_pipeline.py  (native Python orchestrator, runs on Windows directly)
         │
   ┌───────────────────────────────────────────────────────────┐
   │  Stage 1    Discovery          find VOD by name            │
   │  Stage 2    Transcription      WhisperX / faster-whisper   │
   │  Stage 3    Segment Detect     classify stream sections    │
   │  Stage 4    Moment Detect      keyword + LLM + quality judge│
   │  Stage 5    Frame Extract      JPEG frames per candidate   │
   │  Stage 5.5  Vision Judge       pairwise tournament re-rank │
   │  Stage 6    Vision Enrich      titles, hooks, categories   │
   │  Stage 7    Editing & Export   captions, effects, render   │
   │  Stage 8    Log & Report       processed.log, Discord msg  │
   └───────────────────────────────────────────────────────────┘
         │
   clips/ ← vertical MP4s, captions burned in, ready to post
   Discord ← "Made 8 clips: Accidental PC Unplug Chaos, ..."
```

Everything runs natively on the Windows host — **no Docker, no WSL2** (that path still exists but is legacy; see [Legacy Docker Path](#legacy-docker-path)). LM Studio serves the LLM at `http://localhost:1234`; the orchestrator, dashboard, and Discord gateway are plain Windows processes.

### Clip Styles

The bot infers style from your message — you rarely need to specify anything:

| You say | Style | Prioritizes |
|---|---|---|
| "clip my stream" | `auto` | Best moments across all types |
| "find the funny parts" | `funny` | Comedy, awkward moments, banter |
| "get the hype clips" | `hype` | Clutch plays, wins, high energy |
| "find the emotional moments" | `emotional` | Heartfelt, vulnerable, real talk |
| "clip the hot takes" | `hot_take` | Bold claims, unpopular opinions |
| "get the stories" | `storytime` | Narrative moments with setup+payoff |
| "find the reactions" | `reactive` | Shock, rage, disbelief |
| "get the dancing clips" | `dancing` | Physical performance, dance moves |
| "get a mix of everything" | `variety` | One clip per category |

### Dynamic Clip Count

Scales with VOD length — 3 clips/hour, minimum 3, capped at 20:

| Stream Length | Target Clips |
|---|---|
| 1 hour | 3 |
| 2 hours | 6 |
| 4 hours | 12 |
| 7+ hours | 20 (capped) |

---

## Two Interfaces (+ a Third App)

**Discord bot** (primary) — natural-language commands via the OpenClaw agent; results delivered as attachments. Driven by [`workspace/AGENTS.md`](workspace/AGENTS.md) (bot identity/rules) and [`workspace/skills/stream-clipper/SKILL.md`](workspace/skills/stream-clipper/SKILL.md) (trigger words → `clip.cmd` invocation).

**Web dashboard** (secondary) — Flask app, default port **5001** (rolls forward to the next free port if squatted — check the terminal output for the actual port). VOD library, clip controls (style, quality gate, frame-fit mode, and more), live 8-stage progress monitor with SSE log streaming, clips gallery, and a **Reference Lab** tab for comparing your output against a corpus of reference clips. Start with:
```powershell
python dashboard\app.py
```

**Buffer Clip Poster** (optional, separate app) — a sibling Flask app on port **5100** for batch-posting finished clips straight to TikTok + Instagram Reels via the Buffer API. Entirely independent of the main dashboard. See [Buffer Clip Poster](#buffer-clip-poster) below. Start with:
```powershell
start-poster.cmd
```

---

## The Pipeline

`scripts/run_pipeline.py` is a thin Python orchestrator; each stage is its own module under `scripts/pipeline/stages/stage{1..8}.py`, calling into the heavier logic in `scripts/lib/` (64 modules). All stages log to a live file plus a persistent timestamped copy at `clips/.pipeline_logs/`.

| Stage | What happens |
|---|---|
| **1 — Discovery** | Scans `vods/` for `.mp4`/`.mkv`, checks `vods/processed.log` to skip already-clipped VODs |
| **2 — Transcription** | WhisperX (word-level timestamps + speaker diarization) by default, falls back to faster-whisper; cached per-VOD so re-clips skip this entirely |
| **3 — Segment Detection** | Classifies the stream into `gaming` / `irl` / `just_chatting` / `reaction` / `debate` windows; builds a stream profile used to weight later stages |
| **4 — Moment Detection** | Three-pass hybrid: keyword scan (instant) + LLM chunk analysis + merge/re-rank/time-bucket selection, followed by an **S4.5 text-quality judge** that reviews every candidate against its verbatim transcript evidence before any frames are extracted (kills weak candidates early — faster *and* higher quality) |
| **5 — Frame Extraction** | Pulls JPEG frames per surviving candidate for vision analysis |
| **5.5 — Vision Judge** | A pairwise tournament where the vision model actually *selects* which moments win, not just titles them |
| **6 — Vision Enrichment** | Generates titles, hook lines, categories, and originality hints from frames + context |
| **7 — Editing & Export** | Renders each clip: framing, captions, optional style-profile effects (zoom punches, SFX, kinetic captions), A/B variants, post-kit generation |
| **8 — Log & Report** | Appends to `processed.log`, writes diagnostics to `clips/.diagnostics/`, reports back to Discord |

Full per-stage detail, every environment flag, and the history behind each design decision live in the wiki — start at [`wiki/concepts/clipping-pipeline`](AIclippingPipelineVault/wiki/concepts/clipping-pipeline.md).

---

## Feature Highlights

The pipeline has grown well past "transcribe, detect, render" — a non-exhaustive tour of what's actually in there today, all wiki-documented in depth:

- **Quality judge** — every moment candidate gets reviewed against its verbatim transcript evidence by the vision-tier model before frames are ever extracted; an optional dashboard "Quality gate" can require a minimum judge score to render at all.
- **Style profiles & A/B variants** — per-category editing templates (zoom punches, freeze frames, SFX cues, kinetic captions, fingerprint perturbation) plus optional automatic A/B render variants per clip.
- **Frame fit modes** — one dashboard dropdown controls how a 16:9 source fits the 9:16 frame: classic blur-fill letterbox, full-bleed crop-to-action, an **auto** mode that picks per clip by content type (IRL → full-bleed, gaming/reaction → letterbox), or face-tracking camera pan.
- **Kinetic captions** — CapCut-style word-box captions with sentence-aware grouping, configurable casing, and a bundled font — no system font dependencies.
- **SFX + jump cuts** — a stocked sound-effect taxonomy keyed to transcript beats (payoff, build-up, laughter), and an optional silence-gap jump-cut compressor.
- **News compilation mode** — a separate output mode that stitches finished clips from multiple VODs into one narrated news-style compilation.
- **Per-VOD checkpoints** — a crashed or stopped run resumes from the last completed stage per VOD instead of restarting from scratch.
- **Reference Lab** — a dashboard tab that decomposes a corpus of reference (competitor) clips into structured attribute cards, cards your own output the same way, and generates a gap report with concrete config levers.
- **Buffer Clip Poster** — a separate app for batch-publishing finished clips to TikTok + Instagram Reels.

See [`wiki/index.md`](AIclippingPipelineVault/wiki/index.md) for the full list of concept pages — captions, style profiles, SFX taxonomy, jump cuts, originality/fingerprinting, the reference-comparison loop, and more each have their own page.

---

## Models

LLM inference runs in **LM Studio** on your Windows host, reachable at `http://localhost:1234`. The orchestrator loads/unloads models between stages via the bundled `lms` CLI to keep VRAM usage bounded.

| Role (config key) | Typical choice | Stages |
|---|---|---|
| `text_model` | a smaller/faster model (e.g. `qwen/qwen3.5-9b`) | Stage 3, Stage 4 detection |
| `vision_model` | a larger multimodal model (e.g. `qwen/qwen3.6-35b-a3b`) | Stage 4.5 judge, Stage 5.5, Stage 6 |
| Discord agent model | a small tool-calling model (e.g. `qwen/qwen3.5-9b`) | OpenClaw agent only — **not** the same config as the pipeline models |
| Whisper | `large-v3-turbo` | Stage 2 (and Stage 7 for per-clip subtitle re-alignment) |

Set text/vision models and context length in **Dashboard → Models**, which writes to `config/models.json`. The Discord agent's model is configured separately in `config/openclaw.json`. Per-stage overrides exist (`text_model_passb`, `vision_model_stage6`) for splitting extraction from judgment across two different models — see [`wiki/concepts/model-split`](AIclippingPipelineVault/wiki/concepts/model-split.md).

There's no fixed "the" model — this project has run everything from a single 9B up through 35B-class MoE models across two GPUs. Pick what fits your VRAM; the pipeline adapts.

---

## Requirements

### Hardware

| Component | Minimum | Recommended |
|---|---|---|
| **GPU** | NVIDIA 8GB VRAM | NVIDIA 16GB+ VRAM (more headroom = bigger LLM) |
| **RAM** | 16GB | 32GB+ |
| **CPU** | 8 cores | 12+ cores |
| **Storage** | 50GB free | 200GB+ (models + VODs add up fast) |

An AMD GPU can be pooled alongside an NVIDIA one for LLM inference through LM Studio's Vulkan backend, letting a larger model fit than either card would hold alone.

### Software

- **Windows 10/11** (this is the primary, tested target — the whole point of the bare-metal port was avoiding WSL2)
- **Python 3.11+** with a venv (`requirements-windows.txt` has the consolidated dependency list for native operation)
- **[LM Studio](https://lmstudio.ai)** — free desktop app, must be running with its local server enabled
- **FFmpeg** and **ffprobe** on `PATH`
- **Node.js 22+** (only if you want the Discord bot / OpenClaw gateway)

---

## Setup Guide

### Step 1 — Install Prerequisites

1. Install **[LM Studio](https://lmstudio.ai)**, open it, confirm it runs.
2. Install **Python 3.11+** and **FFmpeg** (make sure both are on `PATH`).
3. If you want the Discord bot: install **Node.js 22+**.

### Step 2 — Clone and Create a Virtual Environment

```powershell
git clone <this-repo-url>
cd OpenClawStreamClipper
python -m venv .venv
.venv\Scripts\pip install -r requirements-windows.txt
```

### Step 3 — Create Config Files

```powershell
copy config\openclaw.example.json config\openclaw.json
copy config\exec-approvals.example.json config\exec-approvals.json
copy config\originality.example.json config\originality.json
copy .env.example .env
```
Leave `DISCORD_BOT_TOKEN` in `.env` blank for now — you can add it after confirming the pipeline works.

### Step 4 — Set Up LM Studio

1. Open LM Studio's **Models** tab and download a text-capable model and a vision-capable model (they can be the same model if it's multimodal).
2. Go to the **Developer** tab, load a model, and **Start Server**. Confirm it's serving on `http://localhost:1234`.

> Keep LM Studio running whenever you use the pipeline — it loads/unloads models between stages automatically via the `lms` CLI.

### Step 5 — Configure Models in the Dashboard

```powershell
python dashboard\app.py
```
Open the URL it prints (default `http://localhost:5001`, rolls forward if that port is taken). Go to **Models**, set your text and vision model IDs to match what's loaded in LM Studio exactly, set a context length appropriate to your VRAM, and save.

### Step 6 — Test the Pipeline

1. Drop a `.mp4` or `.mkv` VOD into `vods/`.
2. In the dashboard, select it, leave style as `auto`, and click **Clip Selected**.
3. Watch the stage progress monitor. First-time transcription is the slowest step; everything after is cached per-VOD.
4. Finished clips land in `clips/` and show up in the dashboard's Clips gallery.

Or from the command line:
```powershell
clip.cmd --style auto --vod <name-fragment>
```

### Step 7 — Set Up Discord Bot (Optional)

1. Create an application + bot at the [Discord Developer Portal](https://discord.com/developers/applications), enable **Message Content Intent**, copy the bot token.
2. Put the token in `.env` as `DISCORD_BOT_TOKEN=...`.
3. Invite the bot to your server (OAuth2 → URL Generator → scope `bot` → permissions: Send Messages, Read Message History, Attach Files).
4. Start the gateway via `start.ps1`, then message the bot: `clip my stream`.

The agent's behavior is governed by `workspace/AGENTS.md` and `workspace/skills/stream-clipper/SKILL.md` — it always calls the `exec` tool to run `clip.cmd`, never just replies with text.

---

## Dashboard

Runs natively on Windows (`python dashboard/app.py`), default port **5001**. Key panels:

| Panel | Function |
|---|---|
| **VOD Library** | Browse VODs, processing status, transcription cache state |
| **Clip Controls** | Style, stream-type hint, force reprocess, quality gate, frame-fit mode |
| **Originality & Render** | Framing mode, style profiles toggle, jump cuts, captions, A/B variants, music bed |
| **Pipeline Monitor** | 8-stage progress, live SSE log stream |
| **Clips Gallery** | Preview and download rendered clips |
| **Models** | Text/vision model selection, context length |
| **Hardware** | Whisper compute device (CUDA/CPU) |
| **Reference Lab** | Decompose reference clips + your own output into attribute cards, generate a gap report |

The dashboard is a Flask app split into blueprints under `dashboard/routes/`; the frontend is vanilla JS modules under `dashboard/static/modules/`.

---

## Buffer Clip Poster

A separate app (`poster/`, port **5100**) for batch-publishing finished clips to TikTok + Instagram Reels through the [Buffer](https://buffer.com) API. Select clips from your `clips/` folder, apply hashtags, and post — each clip becomes its own post on each platform. Requires a Buffer API key and (since Buffer has no direct upload endpoint) a media-hosting leg — see [`wiki/entities/buffer-poster`](AIclippingPipelineVault/wiki/entities/buffer-poster.md) for the full setup, rate-limit behavior, and the top-rated clip filter.

Start it with `start-poster.cmd` — it's fully independent of the main dashboard and safe to run alongside it.

---

## Usage

### Discord Commands

```
clip my stream                  → auto style, next unprocessed VOD
find the funny parts            → funny style
clip the hype moments           → hype style
get the emotional clips         → emotional style
find the hot takes              → hot_take style
clip the lacy stream            → target VOD named "lacy"
clip the funny irl lacy stream  → funny style, irl type hint, VOD "lacy"
list my vods / what streams     → list available VODs
```

### CLI

```powershell
# Clip a specific VOD
clip.cmd --style auto --vod lacy

# Clip the next unprocessed VOD
clip.cmd --style auto

# Process every unprocessed VOD in one batch
clip.cmd --all

# List available VODs (JSON, no processing)
clip.cmd --list

# Force re-process (ignores processed.log; per-VOD checkpoints still apply
# unless you also clear vods/.pipeline_state/<vod>)
clip.cmd --vod lacy --force
```

### Pipeline Flags

| Flag | Values | Description |
|---|---|---|
| `--style` | `auto`, `funny`, `hype`, `emotional`, `hot_take`, `storytime`, `reactive`, `dancing`, `variety` | Clip category weighting |
| `--vod` | any keyword | Target VOD by filename match; bypasses `processed.log` |
| `--vods` | comma-separated keywords | Target multiple specific VODs in one batch |
| `--type` | `gaming`, `irl`, `just_chatting`, `reaction`, `debate` | Stream type hint for segment classification |
| `--all` | — | Process every unprocessed VOD |
| `--force` | — | Ignore `processed.log` and re-run from scratch |
| `--list` | — | Return JSON inventory of all VODs, no processing |

Many more behaviors are controlled via environment variables (`CLIP_*`) rather than CLI flags — the dashboard sets these for you; see the wiki for the full list per feature.

---

## Configuration Files

| File | What it controls |
|---|---|
| `config/models.json` | `text_model`, `vision_model`, `whisper_model`, context length, per-stage model overrides |
| `config/hardware.json` | Whisper compute device (CUDA/CPU) |
| `config/openclaw.json` | Discord agent config: model, Discord token wiring, compaction settings |
| `config/exec-approvals.json` | Command-execution allowlist for the Discord agent (must permit `clip.cmd`) |
| `config/originality.json` | Dashboard-managed render defaults (framing mode, style profiles, captions, jump cuts, etc.) — regenerate from `config/originality.example.json` if deleted |
| `config/shape_priors.json` | Per-content-species duration/pacing hints derived from reference-clip analysis |
| `.env` | `DISCORD_BOT_TOKEN` |

`config/` holds ~30 JSON files in total covering SFX cues, hook templates, caption voice, chat keywords, selection weighting, and more — each is documented on its relevant wiki concept page rather than exhaustively here. Secrets (`BufferIOapiKey.txt`, `CloudinaryAPI.txt`, `config/buffer_poster.json`) are gitignored and never committed.

---

## Troubleshooting

### "All VODs already processed"

Clear `vods/processed.log`, or use `--vod <name>` to target a specific VOD (always bypasses the log).

### No clips produced

Check `clips/.diagnostics/last_run_*.json` and the pipeline log in `clips/.pipeline_logs/`. Look for: whether the transcript has real words (not empty/garbled), whether Stage 4 found any candidates, and whether a quality gate is set too high for a weak VOD (unjudged/low-scoring VODs can legitimately produce zero clips at a strict gate — lower it or set it to "All clips").

### LM Studio "not reachable"

- Confirm LM Studio is running and its local server is started (Developer tab).
- Confirm it's serving on port 1234 (default) — nothing else should be bound there.
- The orchestrator always resolves to `http://localhost:1234` regardless of what's in `config/models.json`, so a stale docker-era URL in that file is harmless.

### Dashboard shows stale UI / missing features after an update

**Restart it.** Flask reads templates/static per-request but only registers routes at process start, so an old `dashboard/app.py` process will serve new frontend files against old (missing) backend routes. Kill every running `app.py` process and start one fresh. Identify dashboards by **listening port**, not process count — Windows' venv launcher spawns a base-interpreter child process, so one dashboard can show as two processes in Task Manager.

### Port already in use

The dashboard (default 5001) and poster app (default 5100) both roll forward to the next free port automatically if squatted — check the terminal output for the actual bound port, or pin one explicitly with `DASHBOARD_PORT` / `POSTER_PORT`.

### Stage 4 (or any LLM stage) failing on every chunk

Usually a token-budget issue with reasoning-capable models: check the log for `finish=length, reasoning_tokens=XXXX`. If reasoning tokens are consuming the whole budget, either raise the context length in **Dashboard → Models**, use a model with thinking disabled, or switch to a smaller/faster model.

### Bot responds with text but doesn't run the pipeline

1. Confirm `config/exec-approvals.json` still has its wildcard allowlist pattern (`{"pattern": "*"}`) — this ships as the default in `exec-approvals.example.json`, but if you've since locked it down, `clip.cmd` needs to be explicitly allowed.
2. Confirm **Message Content Intent** is enabled in the Discord Developer Portal.
3. Clear stale OpenClaw sessions if the agent seems stuck in an old context.

### CUDA / GPU not available for Whisper

Check `config/hardware.json` — set via **Dashboard → Hardware**, or confirm NVIDIA drivers are installed and `nvidia-smi` runs cleanly from a terminal.

### Pipeline seems hung

Check the log's last-modified time before assuming a hang — some stages (the vision judge, in particular) log in large silent batches and can legitimately run 10+ minutes with no new log lines. If it's genuinely stuck, stop it from the dashboard (which uses a PID marker to kill the correct process tree) rather than force-killing terminals blindly.

---

## Project Structure

```
OpenClawStreamClipper/
├── clip.cmd                        # Native launcher — forwards args to run_pipeline.py
├── start.ps1                       # Starts the dashboard + OpenClaw Discord gateway
├── start-poster.cmd                # Starts the Buffer Clip Poster app (:5100)
├── requirements-windows.txt        # Consolidated native (bare-metal) dependencies
├── scripts/
│   ├── run_pipeline.py             # Orchestrator: arg parsing, config resolution, 8-stage dispatch
│   ├── pipeline/
│   │   ├── common.py               # Logger, model load/unload via `lms` CLI, subprocess helpers
│   │   └── stages/stage{1..8}.py   # One module per pipeline stage
│   ├── lib/                        # ~64 modules: moment detection, vision, rendering, captions,
│   │                                #   SFX, framing, style profiles, checkpoints, etc.
│   └── research/                   # Reference Lab tooling, benchmarking, corpus comparison
├── dashboard/
│   ├── app.py                      # Flask entrypoint (native run, default :5001)
│   ├── routes/                     # Blueprint per feature area
│   ├── static/modules/             # Vanilla JS, one module per panel
│   └── templates/index.html        # Single-page UI
├── poster/                         # Buffer Clip Poster — separate sibling app (:5100)
├── config/                         # ~30 JSON config files (models, originality, SFX, captions, ...)
├── workspace/
│   ├── AGENTS.md                   # Discord agent identity + exec rules
│   └── skills/stream-clipper/
│       └── SKILL.md                # Trigger words, style/type inference, exec commands
├── vods/                           # Drop VOD files here (gitignored)
│   ├── .transcriptions/            # Cached transcripts (auto-created)
│   └── .pipeline_state/            # Per-VOD checkpoint snapshots (auto-created)
├── clips/                          # Rendered clips (gitignored)
│   ├── .pipeline_logs/             # Persistent per-run logs
│   ├── .diagnostics/               # Per-run diagnostic JSON
│   └── post_kits/                  # Per-platform caption/hashtag kits
├── reference_clips/                # Optional reference-clip corpus for the Reference Lab
├── AIclippingPipelineVault/        # The living wiki — authoritative project knowledge base
│   └── wiki/index.md               # Start here for anything not covered in this README
└── legacy/                         # Retired Docker deployment (Dockerfile, docker-compose.yml,
                                     #   the original bash pipeline) — kept for reference/rollback
```

---

## Legacy Docker Path

Before the bare-metal port, this ran as one Docker container (bash pipeline) talking to native Windows LM Studio over `host.docker.internal`. That path is retired but not deleted — the files live under `legacy/`, and setting `CLIP_USE_DOCKER=1` still routes the dashboard through a `docker exec` bridge into that container if you need it. It does **not** receive any of the features or fixes documented above; bare-metal is the only actively developed path. See [`wiki/concepts/bare-metal-windows`](AIclippingPipelineVault/wiki/concepts/bare-metal-windows.md) for the full migration history.

---

## License

MIT
