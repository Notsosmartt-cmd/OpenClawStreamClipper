# OpenClaw Stream Clipper

A fully self-hosted, AI-powered livestream highlight clipper. Drop a VOD into a folder, tell your Discord bot to clip it, and receive vertical 9:16 highlight clips with burned-in captions — no cloud APIs, no subscriptions, everything runs on your own hardware.

**Stack**: [OpenClaw](https://openclaw.ai) (AI agent framework) · [LM Studio](https://lmstudio.ai) (local LLM inference) · [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (GPU speech-to-text) · Docker · FFmpeg

---

## Table of Contents

- [How It Works](#how-it-works)
- [The 8-Stage Pipeline](#the-8-stage-pipeline)
- [Classification System](#classification-system)
- [Models](#models)
- [Requirements](#requirements)
- [Setup Guide](#setup-guide)
- [Dashboard](#dashboard)
- [Usage](#usage)
- [Troubleshooting](#troubleshooting)

---

## How It Works

```
Discord: "clip the funny moments from the lacy stream"
         │
   OpenClaw Agent (LM Studio)
   → infers: --style funny --vod lacy
   → calls exec tool
         │
   bash /root/scripts/clip-pipeline.sh --style funny --vod lacy
         │
   ┌─────────────────────────────────────────────────────┐
   │  Stage 1  Discovery        find VOD by name         │
   │  Stage 2  Transcription    Whisper large-v3 (CUDA)  │
   │  Stage 3  Segment Detect   classify stream sections │
   │  Stage 4  Moment Detect    keyword + LLM analysis   │
   │  Stage 5  Frame Extract    JPEG frames per moment   │
   │  Stage 6  Vision Enrich    titles + score boost     │
   │  Stage 7  Render           FFmpeg 9:16 blur-fill    │
   │  Stage 8  Log & Report     Discord delivery         │
   └─────────────────────────────────────────────────────┘
         │
   clips/ ← vertical MP4s with burned-in captions
   Discord ← "Made 8 clips: Accidental PC Unplug Chaos, ..."
```

### Clip Styles

The bot infers the clip style silently from your message — you never need to specify a flag:

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

Scales automatically with VOD length (3 clips/hour, capped at 20):

| Stream Length | Target Clips |
|---|---|
| 1 hour | 3 clips |
| 2 hours | 6 clips |
| 4 hours | 12 clips |
| 7+ hours | 20 clips |

---

## The 8-Stage Pipeline

The pipeline script (`scripts/clip-pipeline.sh`) processes a single VOD end-to-end. All stages are logged to `/tmp/clipper/pipeline.log` (live) and to a persistent timestamped file at `clips/.pipeline_logs/YYYYMMDD_HHMMSS_VOD.log`.

### Stage 1 — Discovery

Scans `vods/` for `.mp4` and `.mkv` files. Checks `vods/processed.log` to skip already-clipped VODs (bypassed when `--vod` is specified). Gets duration via `ffprobe`.

**Flags**: `--vod <keyword>` (target by name), `--force` (re-process latest), `--list` (inventory JSON, no processing)

### Stage 2 — Chunked Audio Transcription

1. Extracts audio to 16kHz mono WAV via FFmpeg
2. Splits into 20-minute chunks (prevents faster-whisper's degenerate repetition loop on long files)
3. Transcribes each chunk with `faster-whisper large-v3` — GPU (float16) first, CPU (int8) fallback
4. Merges chunks with offset-corrected timestamps; filters degenerate segments (dots, empty text)
5. **Caches** to `vods/.transcriptions/` — re-clips of the same VOD skip transcription entirely

**Outputs**: `transcript.json` (timestamped segments), `transcript.srt`

**Typical speed**: ~3.5 hours of audio → ~50 minutes on an RTX 5060 Ti with large-v3

### Stage 3 — Segment Detection and Stream Profiling

Chunks the transcript into 10-minute windows and classifies each with the LLM:

| Type | What it means |
|---|---|
| `gaming` | Gameplay talk, strategy, callouts, wins/losses |
| `irl` | Real life, walking around, eating, traveling |
| `just_chatting` | Casual Q&A, stories, chat interaction |
| `reaction` | Watching/reacting to videos or content |
| `debate` | Arguments, heated discussion, controversy |

Merges adjacent same-type blocks into contiguous segments. Outputs a **stream profile**: dominant type, percentage breakdown, variety detection flag. This profile is used by Stage 4 (score weighting) and Stage 6 (vision context hints).

**Outputs**: `segments.json`, `stream_profile.json`

### Stage 4 — Three-Pass Hybrid Moment Detection

The core detection engine. Three independent passes, then a merge/select phase.

**Pass A — Keyword Scanning** (instant, no LLM):

Slides a 30-second window across the full transcript. Scores moments by keyword density across six categories. Applies segment-type weight multipliers (e.g., `funny` keywords score 1.4× during IRL segments). Detects universal signals: exclamation clusters, ALL-CAPS streaks, rapid short sentences, laughter markers. Deduplicates within 20 seconds.

**Pass B — LLM Chunk Analysis** (LM Studio text model):

Splits the transcript into 5-minute chunks (30-second overlap). Sends each chunk to the LLM with a segment-specific prompt: gaming prompts look for clutch plays and rage quits; just_chatting prompts look for hot takes, storytelling, social dynamics; IRL prompts look for funny encounters and emotional moments. The LLM returns a JSON array of moments with timestamps, scores, categories, and one-sentence explanations.

**Pass C — Merge, Deduplicate, Time-Bucket Select**:

- Cross-validates moments found by both Pass A and B (+1.5 score boost, `[CROSS-VALIDATED]` flag)
- Applies style weighting (e.g., `--style funny` gives funny moments a 1.4× multiplier)
- **Time-bucket distribution**: divides the VOD into equal time buckets and guarantees at least one clip per bucket before filling overflow slots — prevents early-VOD bias where the LLM focuses on the first hour
- Enforces 45-second minimum spacing between final clips

**Outputs**: `keyword_moments.json`, `llm_moments.json`, `hype_moments.json`

### Stage 5 — Frame Extraction

Extracts 6 JPEG frames per detected moment (30-second window, 960×540 resolution). Uses FFmpeg with `-nostdin` to prevent stdin consumption in bash loops.

### Stage 6 — Vision Enrichment (Non-Gatekeeping)

For each moment, sends 2 frames to the LM Studio vision model along with stream context (type, segment, transcript reason). The model returns a JSON with score (1–10), category, viral title, and one-sentence description.

**Score blending** (vision is always a bonus, never a penalty):
- Vision ≥ 7/10 → transcript score × 1.15
- Vision ≥ 5/10 → transcript score × 1.08
- Vision < 5/10 → transcript score unchanged

If vision fails (timeout, bad JSON, model error), the moment proceeds to rendering with its transcript score as-is. **Every moment that survived Stage 4 is rendered regardless of vision.**

**Outputs**: `scored_moments.json`

### Stage 7 — Editing and Export

1. Generates clip manifest with vision-generated titles as filenames
2. Extracts clip audio (all clips in one FFmpeg pass)
3. Transcribes clip audio with Whisper for per-clip SRT subtitles
4. Renders each clip with FFmpeg:
   - **Blur-fill 9:16**: full 16:9 frame on a 9:16 canvas — top/bottom filled with a blurred+zoomed version of the same frame (no content cropped)
   - H.264 CRF 23, AAC 128kbps
   - Subtitles burned in — white text, black outline, bottom-aligned

**Outputs**: `clips/*.mp4`

### Stage 8 — Log and Report

Appends VOD entry to `vods/processed.log`. Saves full diagnostic JSON to `clips/.diagnostics/`. Prints JSON summary to stdout (relayed to Discord by OpenClaw). Cleans up `/tmp/clipper/`.

---

## Classification System

This section describes every file that participates in deciding what gets clipped.

### Agent-Level Classification (Discord → pipeline flags)

These files teach the Discord bot how to translate natural language into pipeline flags:

| File | Purpose |
|---|---|
| `workspace/AGENTS.md` | Bot identity + mandatory rules: always call `exec`, never just reply with text, keep messages short |
| `workspace/skills/stream-clipper/SKILL.md` | Skill trigger words, exact exec commands to run, `--style` and `--type` flag inference table |

When you say "clip the funny irl lacy stream", the agent (running on the LM Studio model configured in `config/openclaw.json`) reads these files and infers:
- `--style funny` (from "funny")
- `--type irl` (from "irl")
- `--vod lacy` (from "lacy")

The `--style` and `--type` values are passed as environment hints into the pipeline.

### Config Files

| File | What it controls |
|---|---|
| `config/models.json` | `text_model`, `vision_model`, `whisper_model`, `llm_url`, `context_length` — read by `scripts/clip-pipeline.sh` at startup |
| `config/hardware.json` | `whisper_device: "cuda"` or `"cpu"` — sets Whisper's compute device and precision |
| `config/openclaw.json` | OpenClaw agent config: LM Studio provider, model IDs for Discord bot, compaction settings, Discord token, exec tool config |
| `config/exec-approvals.json` | Allowlist of shell commands the agent is permitted to run — must contain `{"pattern": "*"}` for the pipeline to be executable |
| `.env` | `DISCORD_BOT_TOKEN` — injected into `openclaw.json` at container startup |

### Pipeline Classification Data Flow

```
vods/YOUR_VOD.mp4
    │
    ▼ Stage 2
vods/.transcriptions/YOUR_VOD.json   ← cached transcript (reused on re-clip)
    │
    ▼ Stage 3 (LLM: config/models.json → text_model)
/tmp/clipper/segments.json           ← [{start, end, type: "just_chatting"|"gaming"|...}]
/tmp/clipper/stream_profile.json     ← {dominant_type, type_breakdown, is_variety}
    │
    ▼ Stage 4 Pass A (keyword lists in scripts/clip-pipeline.sh)
/tmp/clipper/keyword_moments.json    ← [{timestamp, score, category, why, segment_type}]
    │
    ▼ Stage 4 Pass B (LLM: config/models.json → text_model)
/tmp/clipper/llm_moments.json        ← [{timestamp, score, category, why, segment_type}]
    │
    ▼ Stage 4 Pass C (merge + time-bucket select)
/tmp/clipper/hype_moments.json       ← final selected moments with clip boundaries
    │
    ▼ Stage 5 (FFmpeg frame extraction — 6 payoff-window frames per moment)
/tmp/clipper/frames_T{N}_tminus2.jpg ← T-2s (pre-peak setup)
/tmp/clipper/frames_T{N}_t0.jpg      ← T+0s (peak)
/tmp/clipper/frames_T{N}_tplus1.jpg  ← T+1s
/tmp/clipper/frames_T{N}_tplus2.jpg  ← T+2s
/tmp/clipper/frames_T{N}_tplus3.jpg  ← T+3s (typical payoff)
/tmp/clipper/frames_T{N}_tplus5.jpg  ← T+5s (aftermath)
    │
    ▼ Stage 6 (LLM: config/models.json → vision_model; all 6 frames per moment in one call)
/tmp/clipper/scored_moments.json     ← moments enriched with vision score + title + description
    │
    ▼ Stage 7 (FFmpeg render)
clips/YOUR_CLIP_TITLE.mp4
clips/.pipeline_logs/TIMESTAMP_VOD.log  ← persistent full log for this run
clips/.diagnostics/last_run_*.json      ← full pipeline state snapshot
```

### Keyword Categories (Stage 4 Pass A)

The keyword lists are defined directly in `scripts/clip-pipeline.sh` (around the Pass A section):

| Category | Example keywords |
|---|---|
| `hype` | "oh my god", "no way", "clip that", "let's go", "clutch", "holy shit" |
| `funny` | "i'm dead", "bruh", "that's so bad", "you're trolling", "i'm crying" |
| `emotional` | "i love you", "thank you so much", "mental health", "from the bottom of my heart" |
| `hot_take` | "hot take", "unpopular opinion", "fight me", "hear me out", "controversial" |
| `storytime` | "so basically", "let me tell you", "you won't believe", "long story short" |
| `reactive` | "what is wrong with", "are you kidding", "i'm so done", "tilted", "look at this" |
| `dancing` | "go off", "slay", "moves", dance-related exclamations |
| `controversial` | call-outs, drama, beef-related keywords |

### Segment-Type Score Weights (Stage 4 Pass A)

Each keyword category gets a multiplier based on what segment type the moment falls in:

| Segment | Funny × | Hype × | Emotional × | Hot Take × | Controversial × |
|---|---|---|---|---|---|
| `gaming` | 1.0 | 1.5 | 1.0 | 1.0 | 1.0 |
| `irl` | 1.4 | 1.0 | 1.3 | 1.0 | 1.2 |
| `just_chatting` | 1.2 | 1.0 | 1.2 | 1.3 | 1.3 |
| `reaction` | 1.2 | 1.1 | 1.0 | 1.2 | 1.5 |
| `debate` | 1.0 | 1.0 | 1.2 | 1.5 | 1.5 |

---

## Models

LLM inference runs in **LM Studio** on your Windows host. The Docker container communicates with it at `http://host.docker.internal:1234`. Whisper runs inside the container via CUDA.

### LM Studio Models (you download and manage these in LM Studio)

| Model | Size | Role | Used in |
|---|---|---|---|
| `qwen/qwen3.5-35b-a3b` *(recommended)* | ~20GB (Q4) | Text analysis — segment classification, moment detection, Discord agent | Stages 3, 4; OpenClaw agent |
| `qwen/qwen3.5-9b` *(lighter alternative)* | ~6GB (Q4) | Same role, faster but less accurate | Stages 3, 4; OpenClaw agent |
| `qwen/qwen3-vl-8b` | ~5GB | Vision enrichment — frame analysis, clip titles | Stage 6 |
| `qwen/qwen2.5-vl-7b` | ~5GB | Alternative vision model (lighter) | Stage 6 |

Set which model to use for text and vision in the **Dashboard → Models** panel (writes to `config/models.json`).

> **35B vs 9B tradeoffs**: The 35B model produces significantly better moment detection and more accurate classifications. It's slower (each Stage 4 chunk takes 3–8 minutes vs ~30 seconds for 9B) and requires 20+ GB of VRAM for the model alone. If you have less than 24GB VRAM, use the 9B model. Both work correctly with the pipeline — the 35B just finds more nuanced moments.

> **35B thinking behavior**: `qwen3.5-35b-a3b` has reasoning mode permanently enabled in LM Studio — it cannot be disabled. The pipeline is designed for this: `max_tokens` is set high enough for the model to finish reasoning (~3000–6000 tokens) AND write its answer. This is why the pipeline runs slowly with 35B but correctly.

### Whisper Model (baked into Docker image)

| Model | Size | Role |
|---|---|---|
| `large-v3` | ~3GB | Speech-to-text — Stages 2 (transcription) and 7 (caption subtitles) |

Pre-downloaded during `docker build`. Runs on CUDA by default; falls back to CPU if unavailable. Set in `config/hardware.json` or the Dashboard → Hardware panel.

---

## Requirements

### Hardware

| Component | Minimum | Recommended |
|---|---|---|
| **GPU** | NVIDIA 8GB VRAM | NVIDIA 16GB+ VRAM (RTX 3090, 4090, 5060 Ti, etc.) |
| **RAM** | 16GB | 32GB+ |
| **CPU** | 8 cores | 12+ cores |
| **Storage** | 50GB free | 200GB+ (models ~25GB + VODs can be 10–50GB each) |

For the 35B model: at least 24GB VRAM (can split across two GPUs in LM Studio).

### Software

- **Windows 10/11** (tested on Windows 11; Linux also works with minor adjustments)
- **[Docker Desktop](https://www.docker.com/products/docker-desktop/)** with WSL2 backend
- **NVIDIA GPU drivers** (version 535+ recommended)
- **NVIDIA Container Toolkit** — included with Docker Desktop on Windows when NVIDIA drivers are installed
- **[LM Studio](https://lmstudio.ai)** (0.3.x or later) — free desktop app

---

## Setup Guide

Follow these steps in order. Discord bot setup is **last** because the bot is optional and you should verify the pipeline works without it first.

### Step 1 — Install Prerequisites

1. Install **[Docker Desktop](https://www.docker.com/products/docker-desktop/)** and start it. Ensure WSL2 integration is enabled (Settings → Resources → WSL Integration).

2. Install **[LM Studio](https://lmstudio.ai)**. Run it and confirm it opens correctly.

3. Verify Docker can see your GPU:
   ```powershell
   docker run --rm --gpus all nvidia/cuda:12.3.2-base-ubuntu22.04 nvidia-smi
   ```
   You should see your GPU listed. If not, check that NVIDIA drivers are installed and Docker Desktop has GPU support enabled.

### Step 2 — Clone the Repository

```powershell
git clone https://github.com/YOUR_USERNAME/OpenClawStreamClipper.git
cd OpenClawStreamClipper
```

### Step 3 — Create Config Files

```powershell
# Copy the example configs (do not edit yet — you'll configure via dashboard)
copy config\openclaw.example.json config\openclaw.json
copy config\exec-approvals.example.json config\exec-approvals.json
```

Create the `.env` file (required by Docker Compose even if you set the token later):
```powershell
copy .env.example .env
```
Leave `DISCORD_BOT_TOKEN` blank for now — you'll fill it in after testing the pipeline.

### Step 4 — Set Up LM Studio

1. Open LM Studio.
2. Go to the **Models** tab (puzzle piece icon) and download:
   - Search `qwen3.5-9b` → download the **Q4_K_M** variant (~6GB) for a lighter setup, **or**
   - Search `qwen3.5-35b-a3b` → download the **Q4_K_M** variant (~20GB) for best quality
   - Search `qwen3-vl-8b` → download the **Q4_K_M** variant (~5GB) for vision
3. Go to the **Developer** tab (the `</>` icon or "Local Server" section).
4. Load your text model.
5. Enable **"Serve on Local Network"** — this is what makes it reachable from Docker at `host.docker.internal:1234`.
6. Click **Start Server**. You should see `Server running at http://0.0.0.0:1234`.

> **Keep LM Studio running** whenever you use the pipeline. The container will warn (non-fatal) if LM Studio isn't reachable at startup.

### Step 5 — Build and Start the Container

```powershell
docker compose up -d --build
```

The first build takes 5–15 minutes (downloads CUDA base image, installs packages, pre-bakes Whisper large-v3 into the image layer). Subsequent builds are fast.

Watch the startup logs:
```powershell
docker compose logs -f stream-clipper
```

You should see:
```
=== OpenClaw Stream Clipper ===
Hardware: whisper=cuda (float16)
Waiting for LM Studio server at http://host.docker.internal:1234...
LM Studio server is reachable.
Starting web dashboard on port 5000...
Starting OpenClaw gateway...
```

If you see `WARNING: LM Studio not reachable`, make sure LM Studio's server is running with "Serve on Local Network" enabled.

### Step 6 — Open the Dashboard and Configure Models

Open **http://localhost:5000** in your browser.

1. Go to the **Models** panel.
2. Set **Text Model** to the exact model ID from LM Studio (e.g., `qwen/qwen3.5-35b-a3b`). This must match LM Studio's ID exactly — click the dropdown to see loaded models.
3. Set **Vision Model** (e.g., `qwen/qwen3-vl-8b`). You can use the same model as text if it supports vision.
4. Set **Context Length** based on your VRAM (8192 is a safe default; 32768 if you have 24GB+ VRAM).
5. Click **Save**.

The dashboard status bar shows **LM Studio** (green = reachable, red = not reachable).

### Step 7 — Test the Pipeline

1. Copy a `.mp4` or `.mkv` stream recording into the `vods/` folder.

2. In the Dashboard, click the VOD you added, set style to **auto**, and click **Clip Selected**.

3. Watch the 8-stage progress in the Pipeline Monitor panel. A 3-hour VOD typically takes:
   - Transcription: 40–60 minutes (first time; cached after)
   - Segment detection: 5–20 minutes (depends on model)
   - Moment detection: 30–180 minutes (depends on model and VOD length)
   - Rendering: 5–10 minutes

4. When complete, clips appear in the **Clips** panel and in the `clips/` folder.

If anything fails, check the pipeline log: **Dashboard → Pipeline Monitor → View Log**, or:
```powershell
docker exec stream-clipper bash -c "tail -50 /tmp/clipper/pipeline.log"
```

Persistent logs are in `clips/.pipeline_logs/` and survive after the pipeline finishes.

### Step 8 — Set Up Discord Bot (Optional)

Once the pipeline works from the dashboard, you can add Discord integration.

**Create the Discord application:**

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications) and click **New Application**.
2. Give it a name (e.g., "Stream Clipper").
3. Go to **Bot** → click **Add Bot**.
4. Under **Privileged Gateway Intents**, enable **Message Content Intent** (required to read messages).
5. Copy the **Token** (click Reset Token if needed). **Keep this secret.**

**Configure the bot token:**

Open `.env` and add your token:
```
DISCORD_BOT_TOKEN=your-bot-token-here
```

Restart the container to inject the token:
```powershell
docker compose down
docker compose up -d
```

**Invite the bot to your server:**

1. In the Developer Portal, go to **OAuth2 → URL Generator**.
2. Under Scopes, check `bot`.
3. Under Bot Permissions, check: **Send Messages**, **Read Message History**, **Add Reactions**, **Attach Files**.
4. Copy the generated URL and open it in your browser to invite the bot to your server.

**Test it in Discord:**

Send the bot a message: `clip my stream`

It will respond and start the pipeline. You can also be specific:
```
clip the funny parts from the lacy stream
clip the irl jason stream
find the hot takes
```

> **Note:** The bot uses the model configured in `config/openclaw.json → agents.defaults.model`. By default this is the same LM Studio model you configured in Step 6. A lighter model (9B) is recommended for the agent role since Discord commands are simple.

---

## Dashboard

Access at **http://localhost:5000** while the container is running.

### Running on Windows (Recommended for Development)

The dashboard can also run directly on Windows without being inside the container:
```powershell
pip install flask
python dashboard/app.py
```

When running on Windows, the dashboard auto-detects this and routes all pipeline executions through `docker exec` into the running container.

### Features

| Panel | Function |
|---|---|
| **VOD Library** | Browse VODs with size, duration, processing status, transcription cache |
| **Clip Controls** | Select style, stream type hint, force reprocess |
| **Pipeline Monitor** | 8-stage progress dots, real-time log streaming (SSE), stage timestamps |
| **Clips Gallery** | Preview and download rendered clips |
| **Models** | Text model, vision model, context length — saves to `config/models.json` |
| **Hardware** | Whisper device (CUDA/CPU) — saves to `config/hardware.json` |
| **Status Bar** | LM Studio connectivity, pipeline running/idle |

### Dashboard API

| Endpoint | Method | Description |
|---|---|---|
| `GET /api/vods` | GET | List all VODs with metadata |
| `GET /api/status` | GET | Pipeline state + LM Studio connectivity |
| `POST /api/clip` | POST | Start clipping a VOD |
| `POST /api/stop` | POST | Stop the running pipeline |
| `GET /api/clips` | GET | List generated clips |
| `GET /api/models` | GET | Current model config + context guide |
| `PUT /api/models` | PUT | Update model config |
| `GET /api/log/stream` | GET | SSE live pipeline log |

---

## Usage

### Discord Commands

The bot infers everything from natural language — no special syntax required:

```
clip my stream                → auto style, next unprocessed VOD
find the funny parts          → funny style
clip the hype moments         → hype style
get the emotional clips       → emotional style
find the hot takes            → hot_take style
get the dancing moments       → dancing style
clip the lacy stream          → target VOD named "lacy"
clip the funny irl lacy stream → funny style, irl type hint, VOD "lacy"
list my vods / what streams   → list available VODs
```

### CLI (Run Pipeline Directly)

```powershell
# Clip a specific VOD
docker exec stream-clipper bash -c "bash /root/scripts/clip-pipeline.sh --style auto --vod lacy"

# Clip the next unprocessed VOD
docker exec stream-clipper bash -c "bash /root/scripts/clip-pipeline.sh --style auto"

# List available VODs
docker exec stream-clipper bash -c "bash /root/scripts/clip-pipeline.sh --list"

# Force re-process a VOD (ignore processed.log)
docker exec stream-clipper bash -c "bash /root/scripts/clip-pipeline.sh --vod lacy --style funny"
```

### Pipeline Flags

| Flag | Values | Description |
|---|---|---|
| `--style` | `auto`, `funny`, `hype`, `emotional`, `hot_take`, `storytime`, `reactive`, `dancing`, `variety` | Clip category weighting |
| `--vod` | any keyword | Target VOD by filename match; bypasses processed.log |
| `--type` | `gaming`, `irl`, `just_chatting`, `reaction`, `debate` | Stream type hint for segment classification |
| `--force` | — | Re-process the most recently added VOD |
| `--list` | — | Return JSON inventory of all VODs, no processing |

### Monitoring a Running Pipeline

```powershell
# Current stage (instant)
docker exec stream-clipper bash -c "cat /tmp/clipper/pipeline_stage.txt"

# Live log (Ctrl+C to stop watching; pipeline keeps running)
docker exec stream-clipper bash -c "tail -f /tmp/clipper/pipeline.log"

# Persistent log (survives pipeline completion)
# On Windows host: clips\.pipeline_logs\YYYYMMDD_HHMMSS_VODNAME.log
```

### Re-Processing and Cache Management

```powershell
# Force re-process all VODs (clear the processed log)
echo. > vods\processed.log

# Force re-transcription (delete cached transcript)
del vods\.transcriptions\VODNAME.json

# Force re-process without touching the log (use --vod flag)
docker exec stream-clipper bash -c "bash /root/scripts/clip-pipeline.sh --vod lacy"
```

---

## Troubleshooting

### "All VODs already processed"

Clear `vods/processed.log`:
```powershell
echo. > vods\processed.log
```
Or use `--vod <name>` to target a specific VOD by name (always bypasses the log).

### No clips in the output folder

Check `clips/.diagnostics/last_run_*.json` or the pipeline log. Look for:
- **Transcript quality**: Real words or dots/empty? → transcription failed
- **keyword_moments count**: Should be hundreds for a long stream
- **llm_moments count**: Zero → LM Studio not reachable or model token budget issue
- **scored_moments count**: Zero → Stage 6 failed entirely
- **clips_made**: Should match target clip count

### LM Studio "not reachable"

- Confirm LM Studio is running
- Confirm **"Serve on Local Network"** is enabled in LM Studio's Developer/Server panel
- Default port is 1234 — confirm nothing else is using it
- Restart the container after enabling the setting: `docker compose restart`

### Stage 3/4/6 all chunks failing

The most common cause is token budget — the 35B model needs large `max_tokens` values to finish reasoning before producing output. Check the pipeline log for `finish=length, reasoning_tokens=XXXX, total_tokens=YYYY`. If `reasoning_tokens ≈ total_tokens`, the model was cut off mid-think. Use the dashboard Models panel to set the context length, or use a smaller model (9B).

### Bot responds with text but doesn't run the pipeline

1. Check `config/exec-approvals.json` contains a wildcard pattern: `{"*": {"allowlist": [{"pattern": "*"}]}}`
2. Clear stale sessions:
   ```powershell
   docker exec stream-clipper bash -c "rm -f /root/.openclaw/agents/main/sessions/*.jsonl"
   docker compose restart
   ```
3. Make sure **Message Content Intent** is enabled in Discord Developer Portal → Bot settings

### CUDA / GPU not available for Whisper

```powershell
# Check GPU is visible in container
docker exec stream-clipper nvidia-smi

# Check Whisper device config
type config\hardware.json
# Should show: {"whisper_device": "cuda"}

# If hardware.json missing, dashboard will default to CUDA
# Set via Dashboard → Hardware panel
```

### Pipeline hung (30+ minutes, no stage progress)

```powershell
# Check what stage it's stuck on
docker exec stream-clipper bash -c "cat /tmp/clipper/pipeline_stage.txt"

# Kill and restart
docker exec stream-clipper bash -c "pkill -f clip-pipeline"
docker exec stream-clipper bash -c "bash /root/scripts/clip-pipeline.sh --vod YOURVODNAME"
```

### Full Reset

```powershell
docker compose down
docker compose up -d --build
```

Then clear stale sessions if the bot was connected:
```powershell
docker exec stream-clipper bash -c "rm -f /root/.openclaw/agents/main/sessions/*.jsonl"
```

### Quick Reference

| Symptom | Cause | Fix |
|---|---|---|
| "All VODs already processed" | VOD in processed.log | Clear log or use `--vod` |
| No clips, llm_moments=0 | LM Studio not reachable or token limit | Check LM Studio server, check model config |
| Stage 3/4 all "empty content" | Token budget too low for model | Increase max_tokens or use smaller model |
| Bot replies with text, no exec | Missing exec-approvals or stale session | Fix exec-approvals, clear sessions |
| "LM Studio not reachable" | Server off or LAN serving disabled | Start LM Studio + enable "Serve on Local Network" |
| No CUDA for Whisper | NVIDIA toolkit not working | Check `docker exec stream-clipper nvidia-smi` |
| Pipeline hung in Stage 4 | 35B model slow (normal) | Wait, or switch to 9B for faster runs |

---

## Project Structure

```
OpenClawStreamClipper/
├── docker-compose.yml              # Single service: stream-clipper
├── Dockerfile                      # CUDA 12.3 + Python + Node.js + Whisper + FFmpeg + OpenClaw
├── .env.example                    # Discord token template
├── scripts/
│   ├── entrypoint.sh               # Container startup: inject token, detect hardware, start gateway
│   └── clip-pipeline.sh            # 8-stage AI clipping pipeline (~1,800 lines)
├── dashboard/
│   ├── app.py                      # Flask API + SSE streaming + docker exec bridge (Windows)
│   ├── templates/index.html        # Single-page dark UI
│   └── static/                     # CSS + vanilla JS
├── config/
│   ├── models.json                 # text_model, vision_model, whisper_model, llm_url, context_length
│   ├── hardware.json               # whisper_device: "cuda" or "cpu"
│   ├── openclaw.json               # OpenClaw agent config (LM Studio, Discord token, compaction)
│   ├── exec-approvals.json         # Command execution allowlist for the agent
│   ├── openclaw.example.json       # Template for openclaw.json
│   └── exec-approvals.example.json # Template for exec-approvals.json
├── workspace/
│   ├── AGENTS.md                   # Bot identity + exec rules
│   └── skills/stream-clipper/
│       └── SKILL.md                # Skill triggers, commands, style/type inference
├── vods/                           # Drop VOD files here (gitignored)
│   ├── .transcriptions/            # Cached Whisper transcriptions (auto-created)
│   └── processed.log               # Log of already-clipped VODs (auto-created)
└── clips/                          # Rendered clips appear here (gitignored)
    ├── .pipeline_logs/             # Persistent timestamped logs per run (auto-created)
    └── .diagnostics/               # Pipeline diagnostic JSONs per run (auto-created)
```

---

## License

MIT
