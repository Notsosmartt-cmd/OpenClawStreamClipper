---
title: "Deployment"
type: concept
tags: [deployment, docker, setup, hardware, cuda, lm-studio, windows]
sources: 2
updated: 2026-04-19
---

# Deployment

How to set up, run, and maintain the OpenClaw Stream Clipper.

---

## Architecture

One Docker container + LM Studio on the Windows host:

| Component | Where | Role |
|---|---|---|
| `stream-clipper` | Docker container | Pipeline, OpenClaw gateway, Whisper, FFmpeg, Flask dashboard |
| [[entities/lm-studio]] | Windows host (native) | LLM inference — OpenAI-compatible API on port 1234 |

The container calls LM Studio at `http://host.docker.internal:1234` (set via `extra_hosts: host.docker.internal:host-gateway` in `docker-compose.yml`). There is **no Ollama container** — [[entities/ollama]] is retired.

---

## Hardware Requirements

| Component | Minimum | Recommended |
|---|---|---|
| GPU | NVIDIA 8GB VRAM | NVIDIA 16GB+ VRAM (RTX 3090, 4090, 5060 Ti) |
| RAM | 16 GB | 32 GB+ |
| CPU | 8 cores | 12+ cores |
| Storage | 50 GB | 200 GB+ (models ~25 GB + VODs) |

For the 35B model: 24GB+ VRAM. LM Studio supports splitting across two GPUs.

---

## Software Prerequisites

- **Windows 10/11** (tested on Windows 11; Linux also works)
- **[Docker Desktop](https://www.docker.com/products/docker-desktop/)** with WSL2 backend
- **NVIDIA GPU drivers** (535+ recommended)
- **NVIDIA Container Toolkit** — included with Docker Desktop on Windows when NVIDIA drivers are installed
- **[LM Studio](https://lmstudio.ai)** 0.3.x or later

---

## Setup (Step-by-Step)

### Step 1 — Install Prerequisites

1. Install Docker Desktop. Enable WSL2 integration.
2. Install LM Studio.
3. Verify Docker GPU access: `docker run --rm --gpus all nvidia/cuda:12.3.2-base-ubuntu22.04 nvidia-smi`

### Step 2 — Clone

```bash
git clone https://github.com/YOUR_USERNAME/OpenClawStreamClipper.git
cd OpenClawStreamClipper
```

### Step 3 — Create Config Files

```bash
cp config/openclaw.example.json config/openclaw.json
cp config/exec-approvals.example.json config/exec-approvals.json
cp .env.example .env
# Leave DISCORD_BOT_TOKEN blank for now
```

### Step 4 — Set Up LM Studio

1. Open LM Studio → Models tab → Download:
   - `qwen3.5-9b` Q4_K_M (~6 GB) — fast, works well
   - `qwen3.5-35b-a3b` Q4_K_M (~20 GB) — best quality, slower
   - `qwen3-vl-8b` Q4_K_M (~5 GB) — vision model
2. Go to Developer tab → Load the text model → Enable **"Serve on Local Network"** → **Start Server**
3. Confirm: `Server running at http://0.0.0.0:1234`

### Step 5 — Build and Start Container

```bash
docker compose up -d --build
```

First build: 5–15 minutes (downloads CUDA base image, pre-bakes Whisper large-v3).

Watch startup:
```bash
docker compose logs -f stream-clipper
```

Expected output:
```
=== OpenClaw Stream Clipper ===
Hardware: whisper=cuda (float16)
LM Studio server is reachable.
Starting web dashboard on port 5000...
Starting OpenClaw gateway...
```

### Step 6 — Configure Models in Dashboard

Open **http://localhost:5000** → Models panel:
- Set **Text Model** to match your LM Studio model ID (e.g., `qwen/qwen3.5-35b-a3b`)
- Set **Vision Model** (e.g., `qwen/qwen3-vl-8b`)
- Set **Context Length** based on VRAM (8192 default; 32768 if 24GB+ VRAM)
- Click **Save**

### Step 7 — Test Pipeline

1. Drop a `.mp4` or `.mkv` into `vods/`
2. Dashboard → select VOD → Clip Selected → watch Pipeline Monitor
3. Clips appear in `clips/` when done

### Step 8 — Discord Bot (Optional, Do Last)

1. [Discord Developer Portal](https://discord.com/developers/applications) → New Application → Bot → Add Bot
2. Enable **Message Content Intent** under Privileged Gateway Intents
3. Copy the bot token
4. Edit `.env`: `DISCORD_BOT_TOKEN=your-token-here`
5. Restart: `docker compose down && docker compose up -d`
6. OAuth2 → URL Generator → scopes: `bot` → permissions: Send Messages, Read Message History, Attach Files → invite URL → invite to server
7. Test: message the bot `clip my stream`

---

## Volume Mounts

| Host path | Container path | Purpose |
|---|---|---|
| `./config` | `/root/.openclaw` | OpenClaw config, exec-approvals, models.json, hardware.json |
| `./workspace` | `/root/.openclaw/workspace` | AGENTS.md, SKILL.md |
| `./vods` | `/root/VODs` | Input VOD files |
| `./clips` | `/root/VODs/Clips_Ready` | Output clips, pipeline logs, diagnostics |
| `./scripts` | `/root/scripts` | Pipeline script (live-mounted — no rebuild needed for script changes) |
| `./dashboard` | `/root/dashboard` | Dashboard files (live-mounted) |

> [!note] Live script editing
> `./scripts` is mounted directly into the container. Changes to `clip-pipeline.sh` take effect immediately on the next pipeline run — no container rebuild needed.

---

## Common Operations

```bash
# Start / stop
docker compose up -d
docker compose down

# Rebuild after Dockerfile changes (preserves config and clips)
docker compose up -d --build

# Restart to pick up config changes (models.json, hardware.json)
docker compose restart

# View container logs
docker compose logs -f stream-clipper

# Enter container shell
docker exec -it stream-clipper bash

# Run pipeline manually
docker exec stream-clipper bash -c "bash /root/scripts/clip-pipeline.sh --style auto --vod VODNAME"

# Check current pipeline stage
docker exec stream-clipper bash -c "cat /tmp/clipper/pipeline_stage.txt"

# Tail live pipeline log
docker exec stream-clipper bash -c "tail -f /tmp/clipper/pipeline.log"

# List VODs
docker exec stream-clipper bash -c "bash /root/scripts/clip-pipeline.sh --list"

# Clear processed log (force re-process all VODs)
echo "" > vods/processed.log

# Clear stale bot sessions (fixes "bot describes but doesn't run")
docker exec stream-clipper bash -c "rm -f /root/.openclaw/agents/main/sessions/*.jsonl"
docker compose restart
```

---

## Persistent Pipeline Logs

Every pipeline run writes to two log locations:
- `/tmp/clipper/pipeline.log` — ephemeral, used for live SSE streaming in dashboard; deleted by EXIT cleanup trap
- `clips/.pipeline_logs/YYYYMMDD_HHMMSS_VODNAME.log` — **persistent**, survives cleanup, always available for post-run review

The persistent log path is printed at pipeline startup: `=== Persistent log: /root/VODs/Clips_Ready/.pipeline_logs/... ===`

---

## Config Files Reference

| File | Purpose |
|---|---|
| `config/models.json` | `text_model`, `vision_model`, `whisper_model`, `llm_url`, `context_length` |
| `config/hardware.json` | `whisper_device`: `"cuda"` or `"cpu"` |
| `config/openclaw.json` | OpenClaw agent: LM Studio provider, Discord token, compaction, exec config |
| `config/exec-approvals.json` | Command allowlist — must have `{"pattern": "*"}` for pipeline to run |
| `.env` | `DISCORD_BOT_TOKEN` — injected into openclaw.json at startup |

---

## Verifying GPU / LM Studio

```bash
# Check NVIDIA CUDA is available in container (for Whisper)
docker exec stream-clipper nvidia-smi

# Check LM Studio is reachable from container
docker exec stream-clipper curl -s http://host.docker.internal:1234/v1/models | python3 -m json.tool

# Check which models are loaded in LM Studio
curl http://localhost:1234/v1/models
```

---

## Related

- [[entities/lm-studio]] — LM Studio API, model IDs, 9B vs 35B behavior differences
- [[entities/dashboard]] — Web dashboard access and features
- [[concepts/vram-budget]] — VRAM requirements per model and stage
- [[concepts/bugs-and-fixes]] — Common errors and their fixes
