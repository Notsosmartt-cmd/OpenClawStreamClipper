# OpenClaw Stream Clipper

A fully self-hosted, AI-powered livestream highlight clipper that runs locally via Docker. Drop a VOD into a folder, tell your Discord bot to clip it, and get back vertical 9:16 highlight clips with burned-in captions — no cloud APIs, no subscriptions, everything runs on your hardware.

Built on [OpenClaw](https://openclaw.ai) (autonomous AI agent framework), [Ollama](https://ollama.ai) (local LLM inference), and [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (GPU-accelerated speech-to-text).

---

## Table of Contents

- [How It Works](#how-it-works)
- [The 8-Stage Pipeline (Detailed)](#the-8-stage-pipeline-detailed)
- [Models Used](#models-used)
- [Token and Context Management](#token-and-context-management)
- [Requirements](#requirements)
- [Quick Start](#quick-start)
- [Project Structure](#project-structure)
- [Architecture](#architecture)
- [Configuration](#configuration)
- [Usage](#usage)
- [Known Issues and Fixes](#known-issues-and-fixes)
- [Troubleshooting](#troubleshooting)

---

## How It Works

```
Discord message ("clip the irl lacy stream")
        |
  OpenClaw Agent (qwen2.5:7b — infers style, stream type, VOD name, calls exec)
        |
  exec tool runs: bash /root/scripts/clip-pipeline.sh --style funny --vod lacy --type irl
        |
  8-Stage Clip Pipeline
  |-- Stage 1: Discovery — find VOD by name (--vod), support re-processing
  |-- Stage 2: Transcription — chunked audio → text (faster-whisper large-v3, GPU, cached)
  |-- Stage 3: Segment detection + stream profiling (qwen3.5:9b)
  |-- Stage 4: Three-pass hybrid moment detection
  |     |-- Pass A: Keyword scanning with segment-aware weights (instant, no LLM)
  |     |-- Pass B: Context-aware LLM analysis — setup/payoff, irony, social dynamics (qwen3.5:9b)
  |     +-- Pass C: Cross-validate, merge, deduplicate, diversify, select
  |-- Stage 5: Frame extraction around each detected moment
  |-- Stage 6: Vision enrichment (qwen3-vl:8b) — titles, descriptions, score boosts
  |-- Stage 7: Render vertical 9:16 clips with burned-in captions (FFmpeg)
  +-- Stage 8: Log results, save diagnostics, report to Discord
        |
  Clips appear in ./clips/ with descriptive filenames
  Bot reports back: clip count, titles, categories, scores
```

### Clip Styles

The bot silently infers the clip style from your message — it never asks you to choose:

| You say | Style used | What it prioritizes |
|---------|-----------|---------------------|
| "clip my stream" | `auto` | Best moments of any type, balanced variety |
| "find the funny moments" | `funny` | Comedy, awkward moments, witty lines, banter |
| "get the emotional parts" | `emotional` | Heartfelt, vulnerable, wholesome moments |
| "find the spicy takes" | `hot_take` | Hot takes, bold claims, unpopular opinions |
| "get the hype clips" | `hype` | Clutch plays, epic wins, high-energy reactions |
| "tell me a story" | `storytime` | Narrative moments, anecdotes, stories with payoff |
| "get the reactions" | `reactive` | Strong reactions, rage, shock, disbelief |
| "find a mix of everything" | `variety` | One clip from each category, maximum diversity |

### Stream Type Hints

Users can optionally specify the stream type to improve detection accuracy. Streams are highly variable — a single VOD might contain IRL walking, desktop chatting, gaming, and reaction segments. The pipeline auto-detects these, but a hint biases the classification:

| You say | Type used | Effect |
|---------|----------|--------|
| "clip the irl stream" | `irl` | Biases toward IRL segment detection |
| "process the gaming vod" | `gaming` | Biases toward gaming segment detection |
| "clip the react stream" | `reaction` | Biases toward reaction content |
| "clip my stream" | auto | Pipeline infers types from transcript |

### Segment-Aware Detection

Long streams shift between gaming, IRL, desktop reaction, and just-chatting segments. The pipeline classifies the stream into segments, generates a **stream profile** (dominant type, variety detection), and tailors detection per segment type. A "funny moment" threshold is lower during an IRL segment than a gaming segment, because IRL comedy is subtler. This prevents gaming hype from drowning out quieter but clip-worthy conversational moments.

### Dynamic Clip Scaling

The number of clips scales with stream length (3 clips per hour, minimum 3, maximum 20):

| Stream Length | Target Clips |
|--------------|-------------|
| 1 hour | 3 clips |
| 2 hours | 6 clips |
| 4 hours | 12 clips |
| 7+ hours | 20 clips (capped) |

---

## The 8-Stage Pipeline (Detailed)

The pipeline (`scripts/clip-pipeline.sh`, ~1,550 lines) processes a single VOD end-to-end. It manages VRAM carefully — unloading Ollama models before Whisper needs the GPU, and vice versa.

### Stage 1 — Discovery

- Scans `/root/VODs` (mapped to `./vods/` on host) for `.mp4` and `.mkv` files
- Filters against `processed.log` to skip already-clipped VODs
- Verifies the file is complete (size check with delay)
- Gets VOD duration via `ffprobe`
- Supports `--vod <keyword>` to target a specific file by name match (bypasses processed.log, enables re-processing)
- Supports `--force` to re-process the latest VOD without naming it
- Supports `--list` mode to return JSON inventory of all VODs with size, duration, processed status, and cached transcription info
- When a VOD isn't found, error responses include the full list of available VOD filenames to help the user

### Stage 2 — Chunked Audio Transcription

- **Unloads all Ollama models** from VRAM first (`keep_alive=0`) so Whisper gets full GPU access
- Extracts audio to 16kHz mono WAV via FFmpeg
- **Splits the audio into 20-minute chunks** to prevent faster-whisper's degenerate loop on long files (see [Known Issues](#whisper-produces-all-dots-or-you-on-long-audio))
- Transcribes each chunk with `faster-whisper` using the `large-v3` model
  - Tries GPU (float16) first, falls back to CPU (int8) automatically
  - Beam search size: 5, word-level timestamps enabled
- Merges all chunk results with offset-corrected timestamps
- Filters out degenerate segments (dots, empty text) during merge
- **Caches transcriptions** to `vods/.transcriptions/` so re-clips skip this stage entirely
- Outputs: `transcript.json` (timestamped segments), `transcript.srt` (subtitle file)

**Typical performance**: ~3.5 hours of audio transcribes in ~40-60 minutes on an RTX 5060 Ti with large-v3.

### Stage 3 — Segment Detection and Stream Profiling

- Chunks the transcript into 10-minute windows
- For each chunk, sends the first ~600 words to the text model with a cheap classification prompt (`num_predict=10`)
- Classifies into exactly one type: `gaming`, `irl`, `just_chatting`, `reaction`, or `debate`
- Accepts optional `--type` hint from user (e.g., `--type irl`) to bias classification for known stream types
- Merges adjacent same-type segments into contiguous blocks
- **Generates a stream profile** (`stream_profile.json`): dominant type, percentage breakdown, variety detection
- Outputs: `segments.json` (segment boundaries/types), `stream_profile.json` (overall stream classification)

This is fast (~1 second per chunk) because the model only outputs a single word. The stream profile helps downstream stages (vision enrichment) make better contextual decisions.

### Stage 4 — Three-Pass Hybrid Moment Detection

The core detection engine. Three independent passes, then merge and select.

**Pass A — Keyword Scanning (instant, no LLM)**:
- Slides a 30-second window across the transcript (10-second step)
- Matches against six keyword categories:
  - **Hype**: "oh my god", "no way", "clip that", "let's go", "holy shit", "clutch", "poggers"...
  - **Funny**: "i'm dead", "bruh", "that's so bad", "you're trolling", "i'm crying"...
  - **Emotional**: "i love you", "thank you so much", "from the bottom of my heart", "mental health"...
  - **Hot Take**: "hot take", "unpopular opinion", "fight me", "hear me out", "controversial"...
  - **Storytime**: "so basically", "let me tell you", "you won't believe", "long story short", "true story"...
  - **Reactive**: "what is wrong with", "are you kidding", "i'm so done", "rage", "tilted", "look at this"...
- Applies **segment-specific weight multipliers** (e.g., "funny" keywords get 1.4x weight during IRL segments, "controversial" gets 1.5x during reaction segments)
- Detects **universal signals**: exclamation clusters (2+), ALL CAPS streaks (3+ words), rapid-fire short sentences (4+), laughter markers, question clusters (3+), long pauses followed by speech bursts
- Multi-category hits get a bonus point
- Dynamic threshold per segment type (gaming: 3, IRL: 2, just_chatting: 2, reaction: 3, debate: 2)
- Deduplicates within 20 seconds

**Pass B — LLM Chunk Analysis (qwen3.5:9b via Ollama)**:
- Splits the transcript into 5-minute chunks with 30-second overlap
- Sends each chunk to the text model with **segment-specific prompts**:
  - Gaming: "Find clutch plays, epic wins/losses, rage quits, skill moments"
  - IRL: "Find funny stories, emotional moments, surprising encounters"
  - Just_chatting: "Find hot takes, funny stories, emotional vulnerability, audience interaction"
  - Reaction: "Find strong reactions, controversial takes, emotional responses"
  - Debate: "Find persuasive arguments, heated exchanges, mic-drop moments"
- Adds **style-aware hints** based on the `--style` flag
- **Context-aware detection**: Looks for setup+payoff, storytelling, situational irony, social dynamics, and quotable moments rather than just keyword exclamations
- Encourages inclusion with lower scores (3-5) when in doubt, letting the scoring system make the final call
- Model responds with JSON: `[{time: "MM:SS", score: 1-10, category, why}]`
- Applies segment score boosts (quieter segments like IRL/just_chatting get +1 so they compete fairly with gaming hype)
- **Thinking model support**: `call_ollama()` detects when a thinking model exhausts `num_predict` on internal reasoning and automatically retries with a larger token budget

**Pass C — Merge, Deduplicate, Time-Bucket Distribute, Select**:
- Normalizes keyword scores (capped at 8 to prevent keyword-only moments from dominating)
- Cross-validates: moments detected by **both** passes get a +1.5 score boost and a `cross_validated` flag
- Applies style weighting (e.g., `--style funny` gives funny moments a 1.4x multiplier)
- Category cap: no single category exceeds 60% of final candidates (for `auto` style)
- **Time-bucket distribution**: divides the VOD into equal time buckets (2 per hour, 3-10 range) and guarantees clip selection from each bucket — prevents early-VOD bias where the LLM focuses on the beginning
  - Phase 1: guaranteed picks from each bucket (ensures time spread)
  - Phase 2: fill overflow slots with best remaining moments
  - Phase 3: style-aware re-ranking (variety = round-robin by category; specific style = re-sort by weighted score)
- Temporal spread: enforces minimum 45 seconds between final selected clips
- Selects up to `MAX_CANDIDATES` (2x the target clip count)

### Stage 5 — Frame Extraction

- For each selected moment, extracts 6 JPEG frames from a 30-second window centered on the peak
- Resolution: 960x540 (half-res for speed)
- Quality: FFmpeg `q:v 2` (high quality)
- All FFmpeg calls use `-nostdin` to prevent stdin conflicts in bash loops

### Stage 6 — Vision Enrichment (Non-Gatekeeping)

- Loads stream profile from Stage 3 for contextual vision prompts
- Sends the middle 2 frames (of 6) to `qwen3-vl:8b` via Ollama's vision API
- **Thinking model handling**: Uses `think: true` with `num_predict: 800` to accommodate qwen3-vl's internal reasoning tokens (~300-500 thinking tokens + ~200 content tokens)
- Vision prompt includes stream context (dominant type, current segment, detection reason)
- Asks for: `{score: 1-10, category, title: "viral clip title", description: "one sentence"}`
- **Score blending** (vision is a bonus, never a penalty):
  - Vision score >= 7: transcript score + 2 (capped at 10)
  - Vision score >= 5: transcript score + 1
  - Vision score < 5: keep transcript score unchanged
- If vision fails (bad JSON, timeout, model error): uses transcript data as-is
- **Timeout protection**: 20-minute total stage timeout + 90-second per-moment timeout prevents hangs
- **Every moment that survived detection WILL be rendered** — vision can only help, never eliminate

This design was deliberate. Livestream frames are often visually boring (desk, face, chat overlay) even when the *audio* content is clip-worthy. Making vision a gatekeeper killed 90%+ of valid moments.

### Stage 7 — Editing and Export

This stage manages VRAM carefully — it unloads the vision model, then uses Whisper for captions, then renders with FFmpeg.

1. **Generate clip manifest**: uses vision-generated titles as filenames (e.g., `IRL_Fat_Sack_Checkout_Fiasco.mp4`), sanitizes for filesystem safety, creates `clip_manifest.txt`
2. **Extract clip audio** (FFmpeg, all clips in one pass): pulls 45-second audio segments for each moment
3. **Batch caption transcription** (single Whisper model load): transcribes all clip audio segments with word-level timestamps, outputs individual SRT files
4. **Render all clips** (FFmpeg, blur-fill 9:16): for each moment:
   - Source window: `T - 22s` to `T + 23s` (45 seconds total)
   - **Blur-fill technique**: full 16:9 frame centered on 9:16 canvas with blurred+zoomed background (no content cropped out)
   - Video filter chain: `split[bg][fg] -> [bg]scale+crop+boxblur(25:5) -> [fg]scale(fit) -> overlay(centered)` + `subtitles(burned-in)`
   - Codec: H.264 (libx264), CRF 23, preset medium
   - Audio: AAC 128kbps
   - Subtitle style: white text, black outline (2px), bold, font size 16, bottom-aligned with 40px margin (`Alignment=2,MarginV=40`)
5. **Output**: `.mp4` files saved to `./clips/` on host

### Stage 8 — Summary and Logging

- Appends VOD name + timestamp + clip count + style to `processed.log`
- Saves full diagnostic JSON to `clips/.diagnostics/` (keyword_moments, llm_moments, hype_moments, scored_moments, segments, transcript sample, clips_made)
- Prints JSON summary to stdout (which OpenClaw agent relays to Discord)
- Cleans up temp files

---

## Models Used

The system uses three AI models, each with a specific role. Only one model occupies VRAM at a time — the pipeline actively unloads models between stages.

### Ollama Models (downloaded on first startup)

| Model | Size | VRAM | Context Window | Role | Used In |
|-------|------|------|---------------|------|---------|
| **qwen3.5:9b** | ~6GB | ~11.2GB | 262,144 tokens (capped to 32K) | Pipeline text model | Stage 3 (segment classification), Stage 4 Pass B (LLM moment analysis) |
| **qwen2.5:7b** | ~4.7GB | ~8.8GB | 32,768 tokens | Discord bot agent | OpenClaw gateway agent (tool calling, user interaction) |
| **qwen3-vl:8b** | ~5.5GB | ~11.1GB | 262,144 tokens (capped to 8K) | Vision model (thinking) | Stage 6 (frame analysis, clip title/description generation) |

**Why two text models (qwen3.5:9b + qwen2.5:7b)**:
- **qwen3.5:9b** produces dramatically better moment detection — in benchmarks on real stream transcripts, it found 3 contextual moments where qwen2.5:7b found 0. It understands setup+payoff, situational irony, and social dynamics. Used with `think=false` (thinking mode consumes all tokens and never produces output on this model). ~2x slower but well within acceptable pipeline processing times.
- **qwen2.5:7b** remains the Discord agent model because it has reliable tool calling behavior. Small models (7B) with minimal system prompts produce more consistent structured outputs (JSON tool calls) than larger thinking models in the OpenClaw agent context.

**Why qwen3-vl:8b for vision**: It's the smallest Qwen vision model that produces usable frame analysis. It's a thinking model — `think: true` is required with `num_predict >= 600` to allow enough tokens for both internal reasoning (~300-500 tokens) and actual content output (~100-200 tokens). The pipeline caps its context to 8K since vision prompts are short.

**VRAM note**: qwen3.5:9b uses ~11.2GB VRAM at 32K context. This fits on a 16GB GPU since only one model loads at a time. The pipeline unloads models between stages.

**Thinking model compatibility**: The pipeline's `call_ollama()` function handles thinking models automatically. If a model exhausts its `num_predict` budget on thinking tokens (returning empty content), the function detects this and retries with a larger budget.

### faster-whisper Model (baked into Docker image)

| Model | Size | VRAM | Role |
|-------|------|------|------|
| **large-v3** | ~3GB | ~6-7GB (float16) | Speech-to-text transcription (Stages 2 and 7) |

The Whisper model is pre-downloaded during `docker build` so first startup doesn't need an internet connection for it. It runs on GPU (float16) when VRAM is available, automatically falling back to CPU (int8) if not.

### VRAM Orchestration

With a 16GB GPU, only one large model fits in VRAM at a time. The pipeline manages this:

```
Stage 2: Unload ALL Ollama models -> Load Whisper (GPU, ~6-7GB) -> Transcribe -> Whisper exits
Stage 3: Load qwen3.5:9b (via Ollama, ~11.2GB) -> Classify segments -> Keep loaded
Stage 4: qwen3.5:9b still loaded -> LLM analysis -> Keep loaded
Stage 5: No model needed (FFmpeg only)
Stage 6: Unload qwen3.5:9b -> Load qwen3-vl:8b (via Ollama, ~11.1GB) -> Vision enrichment
Stage 7: Unload qwen3-vl:8b -> Load Whisper (GPU, ~6-7GB) -> Batch caption -> Whisper exits -> FFmpeg render
```

Peak VRAM usage is ~11.2GB during Stages 3-4 (qwen3.5:9b). A 16GB GPU handles this comfortably with ~5GB headroom.

The `OLLAMA_KEEP_ALIVE=5m` setting in docker-compose tells Ollama to release VRAM after 5 minutes of inactivity. The pipeline also explicitly unloads models with `keep_alive=0` API calls when switching between stages.

`OLLAMA_MAX_LOADED_MODELS=1` ensures only one Ollama model is in VRAM at a time.

---

## Token and Context Management

### The Problem

The Discord bot (OpenClaw agent) accumulates conversation history over time. With only a 32K context window on local models, old messages can bloat the context, leaving insufficient room for system prompts and tool descriptions. When the context overflows, the model produces degraded output — it may describe what it wants to do instead of calling the `exec` tool.

### The Solution

The `openclaw.json` config manages context at multiple levels:

**Compaction** (`agents.defaults.compaction`):
- `reserveTokens: 8192` — keeps 8K tokens free for system prompt + next model output
- `keepRecentTokens: 6000` — when compaction triggers, preserves the most recent ~6K tokens of conversation and summarizes everything older
- Compaction fires automatically when context exceeds ~24K tokens (32K window - 8K reserve)

**Session Reset** (`session.reset`):
- `idleMinutes: 60` — after 60 minutes of no Discord messages, a fresh session starts with clean context
- Prevents stale conversations from carrying over between clipping sessions

**Session Maintenance** (`session.maintenance`):
- `mode: "enforce"` — actively prunes old session data (not just warnings)
- `pruneAfter: "7d"` — deletes session files older than 7 days
- `maxEntries: 200` — caps stored session entries at 200

**Discord History Limit** (`channels.discord`):
- `historyLimit: 10` — only loads the last 10 Discord messages into context when processing a new message
- Prevents channel history from consuming the entire context window

**Heartbeat Disabled** (`agents.defaults.heartbeat`):
- `every: "0m"` — disables the periodic "Read HEARTBEAT.md" messages that would otherwise add noise to the context every 30 minutes

### Token Budget Breakdown

For a typical clip request with a 32K context window:

| Component | Tokens | Notes |
|-----------|--------|-------|
| System prompt + AGENTS.md + SKILL.md | ~3,000 | Agent identity, behavior rules, skill definitions |
| Tool definitions (exec, read, write, etc.) | ~2,000 | OpenClaw's available tool schemas |
| Discord history (last 10 messages) | ~1,000-3,000 | Varies by conversation length |
| Reserved for output | ~8,192 | `reserveTokens` setting |
| **Available for conversation** | **~18,000-20,000** | Plenty for typical bot interactions |

The pipeline script itself runs as a subprocess (via `exec` tool) — its output doesn't consume the agent's context unless the bot reads it back. The `exec` tool returns a truncated tail of the process output.

### Model Compatibility

All Ollama models are registered with `compat` flags in `openclaw.json`:

```json
"compat": {
  "supportsDeveloperRole": false,
  "supportsReasoningEffort": false
}
```

These are required for local models that don't support OpenAI-style developer messages or reasoning effort parameters. Without these flags, OpenClaw may send unsupported API parameters that cause silent failures.

---

## Requirements

### Hardware

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| **GPU** | NVIDIA GPU with 8GB VRAM | NVIDIA GPU with 12GB+ VRAM |
| **RAM** | 16GB | 32GB+ |
| **CPU** | 8 cores | 12+ cores |
| **Storage** | 50GB free (models + Docker image) | 100GB+ (VODs are large) |

A CPU-only profile is available but LLM inference and transcription will be significantly slower.

### Software

- **Windows 10/11** or **Linux** (tested on Windows 11 with WSL2)
- **Docker Desktop** with WSL2 backend (Windows) or Docker Engine (Linux)
- **NVIDIA Container Toolkit** (for GPU acceleration)
  - Windows: Included with Docker Desktop when using WSL2 + NVIDIA drivers
  - Linux: [Install guide](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
- **NVIDIA GPU drivers** (version 535+ recommended)
- **A Discord Bot Token** ([Discord Developer Portal](https://discord.com/developers/applications))

### Models (auto-downloaded on first run)

| Model | Download Size | Purpose |
|-------|------|---------|
| `qwen3.5:9b` | ~6GB | Pipeline text analysis — segment classification, moment detection (with `think=false`) |
| `qwen2.5:7b` | ~4.7GB | Discord bot agent — tool calling, user interaction |
| `qwen3-vl:8b` | ~5.5GB | Vision — frame analysis, clip title/description generation (with `think=true`) |
| `large-v3` (Whisper) | ~3GB | Speech-to-text (baked into Docker image at build time) |

Total first-run download: ~16GB for Ollama models + ~3GB for Whisper (in Docker image).

---

## Quick Start

### 1. Clone the repo

```bash
git clone https://github.com/YOUR_USERNAME/OpenClawClipperDocker.git
cd OpenClawClipperDocker
```

### 2. Create your `.env` file

```bash
cp .env.example .env
```

Edit `.env` and paste your Discord bot token:

```
DISCORD_BOT_TOKEN=your-discord-bot-token-here
```

### 3. Set up the OpenClaw config

```bash
cp config/openclaw.example.json config/openclaw.json
cp config/exec-approvals.example.json config/exec-approvals.json
```

The `__DISCORD_BOT_TOKEN__` placeholder in `openclaw.json` is automatically replaced at container startup from your `.env` file. You should change the `gateway.auth.token` in `openclaw.json` to a random string for security.

### 4. Start with GPU acceleration

```bash
docker compose --profile gpu up -d
```

Or for CPU-only (slower):

```bash
docker compose --profile cpu up -d
```

First startup takes 10-20 minutes while Ollama models download.

### 5. Invite the bot to your Discord server

In the [Discord Developer Portal](https://discord.com/developers/applications):
1. Select your application
2. Go to **OAuth2 > URL Generator**
3. Select scopes: `bot`
4. Select permissions: `Send Messages`, `Read Message History`, `Add Reactions`
5. Copy the generated URL and open it to invite the bot

**Important**: Enable **Message Content Intent** under Bot settings (required for reading messages).

### 6. Drop a VOD and clip it

1. Copy a stream VOD file (`.mp4` or `.mkv`) into the `vods/` folder
2. In Discord, message the bot: **"clip my stream"**
3. Wait for the pipeline to finish (20-60 minutes depending on VOD length and hardware)
4. Clips appear in the `clips/` folder

---

## Project Structure

```
OpenClawClipperDocker/
├── docker-compose.yml              # Container orchestration (GPU/CPU profiles)
├── Dockerfile                      # CUDA 12.3 + Node.js 22 + Python + OpenClaw + Whisper
├── .env.example                    # Template for secrets (Discord token)
├── scripts/
│   ├── entrypoint.sh               # Container startup: Ollama wait, model pull, gateway start
│   └── clip-pipeline.sh            # 8-stage AI clipping pipeline (~1,700 lines)
├── dashboard/                      # Web admin dashboard (Flask)
│   ├── app.py                      # REST API + SSE log streaming + docker exec bridge
│   ├── requirements.txt            # flask>=3.0
│   ├── templates/
│   │   └── index.html              # Single-page dark-themed UI
│   └── static/
│       ├── style.css               # Dark theme, purple accent
│       └── app.js                  # Vanilla JS client, SSE streaming
├── config/
│   ├── openclaw.example.json       # Template: models, agent config, compaction, Discord
│   └── exec-approvals.example.json # Template: command execution allowlist
├── workspace/
│   ├── AGENTS.md                   # Agent behavior: style/type inference, exec rules
│   ├── HEARTBEAT.md                # Periodic task template (disabled)
│   └── skills/
│       └── stream-clipper/
│           └── SKILL.md            # Skill triggers, pipeline docs, execution guide
├── vods/                           # Drop VOD files here (gitignored)
│   ├── .transcriptions/            # Cached Whisper transcriptions (auto-created)
│   └── .gitkeep
└── clips/                          # Rendered clips appear here (gitignored)
    ├── .diagnostics/               # Pipeline diagnostic JSONs (auto-created)
    └── .gitkeep
```

---

## Architecture

### Docker Containers

| Container | Image | Purpose |
|-----------|-------|---------|
| `ollama-gpu` / `ollama-cpu` | `ollama/ollama:latest` | Local LLM inference server (hosts Qwen models) |
| `stream-clipper-gpu` / `stream-clipper-cpu` | Custom (Dockerfile) | OpenClaw agent + faster-whisper + FFmpeg pipeline |

Both containers share a Docker bridge network (`clipper-net`). The clipper container connects to Ollama via the `ollama` network alias on port `11434`.

### Docker Image Stack

The clipper container is built from `nvidia/cuda:12.3.2-cudnn9-runtime-ubuntu22.04`:
- **CUDA 12.3 + cuDNN 9**: GPU acceleration for Whisper
- **Node.js 22 LTS**: Required by OpenClaw
- **Python 3 + faster-whisper**: Speech-to-text with GPU support
- **FFmpeg**: Audio extraction, video rendering, subtitle burning
- **OpenClaw**: AI agent framework (installed via npm)
- **Whisper large-v3**: Pre-downloaded during build (~3GB baked into image layer)

### Ollama Configuration

Key environment variables in `docker-compose.yml`:

| Variable | Value | Purpose |
|----------|-------|---------|
| `OLLAMA_KEEP_ALIVE` | `5m` | Release VRAM after 5 minutes of model inactivity |
| `OLLAMA_MAX_LOADED_MODELS` | `1` | Only one model in VRAM at a time (prevents OOM) |
| `OLLAMA_FLASH_ATTENTION` | `1` | Enable flash attention for faster inference |
| `OLLAMA_CONTEXT_LENGTH` | `32768` | 32K token context window |

### Volume Mounts

| Host Path | Container Path | Purpose |
|-----------|---------------|---------|
| `./config` | `/root/.openclaw` | OpenClaw config, sessions, exec approvals |
| `./workspace` | `/root/.openclaw/workspace` | Agent behavior (AGENTS.md, skills) |
| `./vods` | `/root/VODs` | Input VOD files |
| `./clips` | `/root/VODs/Clips_Ready` | Output rendered clips |
| `ollama_data` (named volume) | `/root/.ollama` | Persistent Ollama model storage |

---

## Configuration

### Exec Approvals

The `exec-approvals.json` file controls which shell commands the AI agent can execute. Without this file (or with an empty allowlist), the `exec` tool is **not exposed to the model at all**, and the bot will describe commands instead of running them.

The default template allows all commands:

```json
{
  "*": {
    "allowlist": [
      { "pattern": "*" }
    ]
  }
}
```

For tighter security, restrict to the pipeline script only:

```json
{
  "*": {
    "allowlist": [
      { "pattern": "bash /root/scripts/clip-pipeline.sh*" }
    ]
  }
}
```

---

## Web Dashboard

A standalone web UI for triggering the clip pipeline without Discord or OpenClaw.

### Running the Dashboard

**On Windows (recommended for development):**
```bash
pip install flask
python dashboard/app.py
# Open http://localhost:5000
```

The Windows dashboard automatically detects the running Docker container and executes the pipeline inside it via `docker exec`. The Docker containers must be running (`docker compose --profile gpu up -d`).

**Inside Docker (automatic):**
The dashboard starts automatically inside the container on port 5000 (exposed to host). Access it at `http://localhost:5000` after starting the containers.

### Dashboard Features

- **VOD Library**: browse all VODs with size, duration, processing status, and transcription cache info
- **Clip Controls**: select style (auto, funny, hype, emotional, hot_take, storytime, reactive, variety), stream type hint, force reprocess
- **Pipeline Monitor**: 8-stage progress dots, real-time log streaming via SSE, stage history with timestamps
- **Clips Gallery**: preview and download rendered clips directly in-browser
- **Docker Status**: green/red indicator shows whether the Docker container is connected

### Dashboard API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/vods` | GET | List all VODs with metadata |
| `/api/status` | GET | Pipeline running/idle + Docker connectivity |
| `/api/clip` | POST | Start clipping a specific VOD |
| `/api/clip-all` | POST | Clip all VODs sequentially |
| `/api/stop` | POST | Stop the running pipeline |
| `/api/clips` | GET | List generated clips |
| `/api/clips/<file>` | GET | Serve a clip for preview/download |
| `/api/diagnostics` | GET | Most recent diagnostic JSON |
| `/api/stages` | GET | Stage history with timestamps |
| `/api/log/stream` | GET | SSE endpoint for live pipeline log |

---

## Usage

### Basic Commands (via Discord)

```
clip my stream              -> Auto-detect best moments
find the funny parts        -> Prioritize comedy
get the emotional moments   -> Prioritize heartfelt/vulnerable moments
clip the hype               -> Prioritize high-energy/gaming moments
find the spicy takes        -> Prioritize hot takes, bold claims
tell me a story             -> Prioritize narrative/storytime moments
get the reactions           -> Prioritize strong reactions, rage, shock
get a mix of everything     -> One clip from each category
```

### Stream Type Hints (via Discord)

You can optionally hint the stream type to improve segment detection:

```
clip the irl lacy stream    -> Uses --type irl, biases classification toward IRL content
process the gaming vod      -> Uses --type gaming
clip the react stream       -> Uses --type reaction
```

If no type is specified, the pipeline auto-detects segment types from the transcript. The hint is a soft bias — individual segments can still be classified differently if the content clearly doesn't match.

### Re-processing a VOD

Name a specific VOD to re-process it even if it's been clipped before:

```
clip the lacy stream        -> Re-processes even if already in processed.log
```

The `--vod` flag bypasses the processed log check. You can also manually clear the log:

```bash
# Clear the log to re-process all VODs
echo "" > vods/processed.log

# Delete the cached transcript to force re-transcription
rm -f vods/.transcriptions/*.json vods/.transcriptions/*.srt

# Then ask the bot to clip again in Discord
```

### Viewing Diagnostics

After each run, diagnostic data is saved to `clips/.diagnostics/`:

```bash
ls clips/.diagnostics/
# last_run_20260328_211632.json
```

The diagnostic JSON contains the full pipeline state: transcript sample, detected segments, stream profile, keyword moments, LLM moments, final scored moments, and rendered clips.

### Pipeline CLI Flags

The pipeline script accepts these flags (normally passed by the bot, but usable directly):

| Flag | Example | Description |
|------|---------|-------------|
| `--style <type>` | `--style funny` | Clip style: `auto`, `funny`, `hype`, `emotional`, `hot_take`, `storytime`, `reactive`, `variety` |
| `--vod <keyword>` | `--vod lacy` | Target a specific VOD by name match (bypasses processed.log) |
| `--type <type>` | `--type irl` | Stream type hint: `gaming`, `irl`, `just_chatting`, `reaction`, `debate` |
| `--list` | `--list` | List all VODs with status (returns JSON, no processing) |
| `--force` | `--force` | Re-process the latest VOD without naming it |

Direct execution example:
```bash
docker exec stream-clipper-gpu bash -c 'bash /root/scripts/clip-pipeline.sh --style auto --vod lacy --type irl 2>&1'
```

### Container Management

```bash
# View logs
docker logs -f stream-clipper-gpu

# Restart after config changes
docker restart stream-clipper-gpu

# Stop everything
docker compose --profile gpu down

# Rebuild after script changes
docker compose --profile gpu up -d --build

# Check Ollama models
docker exec ollama-gpu ollama list

# Clear stale sessions (fixes context bloat)
docker exec stream-clipper-gpu sh -c 'rm -f /root/.openclaw/agents/main/sessions/*.jsonl'
docker restart stream-clipper-gpu
```

---

## Known Issues and Fixes

### Whisper produces all-dots or "you" on long audio

**Problem**: faster-whisper enters a degenerate repetition loop when fed very long audio files (2+ hours). Instead of real speech, it outputs thousands of segments containing only `"."` or `"you"`.

**Root cause**: This is a known behavior in CTranslate2-based Whisper implementations. The attention mechanism loses coherence on extremely long sequences.

**Fix (implemented)**: The pipeline splits audio into 20-minute chunks before transcription. Each chunk is transcribed independently, then results are merged with offset-corrected timestamps. Degenerate segments (dots, empty text) are filtered during merge. A 3.5-hour stream produces ~3,500 real speech segments (~20,000 words) with chunked processing.

### Thinking models return empty responses

**Problem**: Thinking models have an internal "thinking mode" where the model spends output tokens on reasoning before generating content. Behavior varies by model:

| Model | `think: false` | `think: true` | Best approach |
|-------|---------------|---------------|---------------|
| **qwen3.5:9b** | Works — thinking disabled, reliable output | Broken — ALL tokens go to thinking (even 6000), content always empty | Use `think: false` |
| **qwen3-vl:8b** | Ignored — still thinks, tokens exhausted | Works with `num_predict >= 600` | Use `think: true` with generous budget |
| **qwen2.5:7b** | Works (no thinking capability) | N/A | Use `think: false` |

**Root cause**: Each model handles the `think` parameter differently. qwen3.5:9b properly respects `think: false`, but qwen3-vl:8b ignores it. When thinking is active, thinking tokens count toward `num_predict`. qwen3.5's thinking mode appears to have no natural stopping point — it keeps reasoning until the token limit.

**Fix (implemented)**:
1. **Pipeline text model (qwen3.5:9b)**: Uses `think: false`. Produces better contextual analysis than qwen2.5:7b — found 153 moments vs ~20 on the same stream in benchmarks.
2. **Pipeline vision model (qwen3-vl:8b)**: Uses `think: true` with `num_predict: 800`. Thinking uses ~300-500 tokens, leaving ~300-500 for content.
3. **Pipeline `call_ollama()` function**: Detects empty content with non-empty thinking (the "token exhaustion" pattern) and automatically retries with a larger `num_predict` budget.
4. **OpenClaw agent**: Uses `qwen2.5:7b` (non-thinking) for reliable tool calling.

### FFmpeg consumes stdin in bash loops — only 1 clip renders

**Problem**: When the pipeline detected 11 moments but only rendered 1 clip. The rendering loop (`while IFS='|' read`) processes the first FFmpeg call correctly, then all remaining manifest lines vanish.

**Root cause**: `ffmpeg` reads from stdin by default. Inside a `while read < file` loop, FFmpeg's stdin read drains the file descriptor that feeds the loop. After the first iteration, there's nothing left to read.

**Fix (implemented)**: Added `-nostdin` flag to all FFmpeg calls inside `while read` loops (4 locations: frame extraction, audio extraction, main render, and re-encode render). This is a common bash pitfall with any program that reads stdin (ffmpeg, ssh, mplayer, etc.).

### Bot describes commands instead of executing them

**Problem**: The bot responds with markdown code blocks showing the command it wants to run, but never actually calls the `exec` tool.

**Root cause (multiple)**:
1. **Empty exec approvals**: Without any allowlist entries in `exec-approvals.json`, the `exec` tool is not exposed to the model at all. The model literally can't call it.
2. **Stale session**: If a session was created before exec approvals were configured, the tool list in the session context doesn't include `exec`. New sessions pick up the current tool configuration.
3. **Context bloat**: When the conversation accumulates too many messages, the model's output quality degrades. It may output text about what it wants to do rather than structured tool calls.

**Fix**:
1. Ensure `exec-approvals.json` has a wildcard pattern (`"*"`)
2. Clear stale sessions: `rm -f config/agents/main/sessions/*.jsonl`
3. Configure compaction (see [Token and Context Management](#token-and-context-management))
4. Restart the container

### Vision scoring blocks valid clips

**Problem**: The vision model (qwen3-vl:8b) scores most livestream frames 3-4 out of 10 because they're visually static (face, desk, chat overlay). When vision score was used as a gatekeeper (requiring score >= 5 to proceed), 90%+ of transcript-detected moments were silently eliminated — even when the audio content was clearly clip-worthy.

**Fix (implemented)**: Vision is now enrichment-only. Every moment that survives transcript detection (Stages 3-4) proceeds to rendering regardless of vision score. Vision can boost scores (+1 to +2 for high visual interest) and provide better titles/descriptions, but it never eliminates moments.

### Vision model requires minimum image dimensions

**Problem**: qwen3-vl:8b crashes with a Go panic (`height:N or width:N must be larger than factor:32`) if any image dimension is smaller than 32 pixels.

**Root cause**: The Qwen3-VL image processor has a `SmartResize` function that divides image dimensions by a factor of 32. Images smaller than 32px in either dimension trigger a division-by-zero panic in Ollama's Go runtime.

**Fix**: The pipeline extracts frames at 960x540 resolution, well above the minimum. This is only an issue if you modify the frame extraction resolution or feed thumbnails directly.

### Health monitor restart loops

**Problem**: Container logs show repeated `[health-monitor] restarting (reason: stale-socket)` messages every ~60-70 minutes.

**Fix**: This is normal Discord WebSocket behavior — connections go stale during idle periods. The config now uses `channelStaleEventThresholdMinutes: 180` and `channelMaxRestartsPerHour: 5` to reduce unnecessary restarts. The Discord health monitor is also disabled (`healthMonitor.enabled: false`) since the gateway handles reconnection natively.

---

## Troubleshooting

### Pipeline says "All VODs already processed"

The VOD filename is in `vods/processed.log` from a previous run. Clear it:

```bash
echo "" > vods/processed.log
```

### No clips in the output folder

Check the diagnostic JSON in `clips/.diagnostics/` for the most recent run. Key things to look for:
- **Transcript quality**: Are segments real words or dots/empty?
- **Keyword moments count**: Should be in the hundreds for a multi-hour stream
- **LLM moments count**: Should be 5-30+ (if zero, the LLM prompt may be too strict or the model is exhausting tokens on thinking — see [Thinking models](#thinking-models-return-empty-responses))
- **Hype moments count**: Final selected moments (should match target clips)
- **Scored moments**: Should have `vision_ok: true` on most entries — if all are `false`, check vision model connectivity
- **Clips made**: Actual rendered clips

### CUDA out of memory

The pipeline uses one model at a time, but if Ollama doesn't release VRAM fast enough:

```bash
# Force unload all Ollama models
docker exec ollama-gpu sh -c 'curl -sf http://localhost:11434/api/generate -d "{\"model\": \"qwen2.5:7b\", \"keep_alive\": 0}"'
docker exec ollama-gpu sh -c 'curl -sf http://localhost:11434/api/generate -d "{\"model\": \"qwen3-vl:8b\", \"keep_alive\": 0}"'
```

### Bot not responding at all

1. Check the bot is online: `docker logs --tail 20 stream-clipper-gpu`
2. Look for `[discord] logged in to discord as ...` in logs
3. If you see `Config invalid`, check `openclaw.json` syntax
4. If the bot reacts (checkmark) but doesn't respond, check exec approvals and sessions (see [Known Issues](#bot-describes-commands-instead-of-executing-them))

### Bot says "already running" but GPU is idle / nothing is processing

This happens when the container is restarted or rebuilt while the bot had an active pipeline session. The old session history still references a dead process, so the bot thinks it's still running.

**Step 1 — Confirm nothing is actually running:**

```bash
# Check for running pipeline processes
docker exec stream-clipper-gpu ps aux | findstr "python bash clip"

# Check GPU usage
docker exec ollama-gpu nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv,noheader
```

If empty process list and low GPU → the pipeline died. Proceed to Step 2.

**Step 2 — Clear stale sessions and restart:**

```bash
docker exec stream-clipper-gpu bash -c "rm -f /root/.openclaw/agents/main/sessions/*.jsonl"
docker restart stream-clipper-gpu
```

Wait ~15 seconds for the bot to reconnect to Discord, then send your clip request again.

### Bot tried a weird command / hallucinated a pipeline invocation

Small models (7B) occasionally hallucinate creative but broken commands (e.g., piping `--list` output into xargs). If the bot ran something wrong, run the pipeline manually.

> **Note:** The pipeline now always writes to `/tmp/clipper/pipeline.log` automatically. The redirect in these commands is optional but keeps a separate manual log if you want it.

**Clip a specific VOD:**
```bash
docker exec -d stream-clipper-gpu bash -c "bash /root/scripts/clip-pipeline.sh --style auto --vod jason"
```

**Clip next unprocessed VOD:**
```bash
docker exec -d stream-clipper-gpu bash -c "bash /root/scripts/clip-pipeline.sh --style auto"
```

**Clip ALL VODs (one after another):**
```bash
docker exec -d stream-clipper-gpu bash -c 'for vod in /root/VODs/*.mp4; do name=$(basename "$vod" .mp4); echo "=== Clipping $name ==="; bash /root/scripts/clip-pipeline.sh --style auto --vod "$name"; done'
```

**List available VODs:**
```bash
docker exec stream-clipper-gpu bash -c "bash /root/scripts/clip-pipeline.sh --list 2>&1"
```

### Monitoring a running pipeline

The pipeline **always** writes to `/tmp/clipper/pipeline.log` regardless of whether it was started by the bot or manually. It also writes the current stage to `/tmp/clipper/pipeline_stage.txt` for instant status checks.

**Check current stage** (instant — works for both bot-started and manual pipelines):
```bash
docker exec stream-clipper-gpu bash -c "cat /tmp/clipper/pipeline_stage.txt 2>/dev/null"

```

**Tail the log in real time** (Ctrl+C to stop watching — the pipeline keeps running):
```bash
docker exec stream-clipper-gpu bash -c "tail -f /tmp/clipper/pipeline.log"
```

**Quick status check** (last 5 log lines + running processes):
```bash
docker exec stream-clipper-gpu bash -c "echo 'Stage:'; cat /tmp/clipper/pipeline_stage.txt 2>/dev/null; echo '---'; tail -5 /tmp/clipper/pipeline.log 2>/dev/null; echo '---'; ps aux | grep clip-pipeline | grep -v grep"
```

**View stage history** (see how long each stage took):
```bash
docker exec stream-clipper-gpu cat /tmp/clipper/pipeline_stages.log 2>/dev/null
```

### Pipeline is hung (30+ minutes with no progress)

Check what it's stuck on:
```bash
docker exec stream-clipper-gpu bash -c "cat /tmp/clipper/pipeline_stage.txt 2>/dev/null; echo '---'; tail -20 /tmp/clipper/pipeline.log 2>/dev/null"
```

Kill and re-run:
```bash
docker exec stream-clipper-gpu bash -c "pkill -f clip-pipeline"
# Then start again using one of the manual commands above
```

### Full nuclear reset

If everything is broken and you want a clean slate:

```bash
docker compose --profile gpu down
docker compose --profile gpu up -d
# Wait ~30 seconds for Ollama health check and Discord reconnection
docker exec stream-clipper-gpu bash -c "rm -f /root/.openclaw/agents/main/sessions/*.jsonl"
```

Then send your message in Discord or run manually.

### Quick reference

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| Bot says "already running" but GPU idle | Stale session from container restart | Clear sessions, restart |
| Bot responds with text but no exec call | Context bloat or stale session | Clear sessions, restart |
| Bot tries weird piped commands | 7B model hallucinated a command | Run pipeline manually |
| Pipeline started but no progress for 30+ min | Vision model or Ollama timeout | Kill pipeline, re-run |
| No pipeline log file exists | Pipeline never started or crashed on launch | Check `docker logs stream-clipper-gpu`, run `--list` to check for errors |
| Troubleshooting commands return empty | Pipeline was started by bot (pre-v3.2.1) | Rebuild container; new version always writes `/tmp/clipper/pipeline.log` |
| Container keeps restarting | Bad config or crash loop | Check logs: `docker compose --profile gpu logs --tail=50 stream-clipper-gpu` |

---

## License

MIT
