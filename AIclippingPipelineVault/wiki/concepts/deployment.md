---
title: "Deployment"
type: concept
tags: [deployment, docker, setup, hardware, cuda, portability]
sources: 2
updated: 2026-04-07
---

# Deployment

How to deploy, run, and maintain the OpenClaw Stream Clipper. Fully Docker-based.

---

## Hardware requirements

| Component | Minimum | Recommended |
|---|---|---|
| GPU | NVIDIA 8GB VRAM | NVIDIA 16GB VRAM (RTX 4060 Ti 16GB, RTX 3090, RTX 5060 Ti) |
| RAM | 16 GB | 32 GB+ |
| CPU | 8 cores | 12+ cores |
| Storage | 50 GB | 100 GB+ (VODs are large) |

16GB VRAM is the comfortable target — `qwen3.5:9b` at 32K context uses ~11.2GB, leaving ~5GB headroom. See [[concepts/vram-budget]].

A CPU-only profile is available but LLM inference and transcription will be significantly slower.

---

## Software prerequisites

- **Windows 10/11 or Linux** (tested on Windows 11 with WSL2)
- **Docker Desktop** with WSL2 backend (Windows) or Docker Engine (Linux)
- **NVIDIA Container Toolkit** for GPU access
  - Windows: included with Docker Desktop when using WSL2 + NVIDIA drivers
  - Linux: [install guide](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
- **NVIDIA GPU drivers** (version 535+ recommended)
- **A Discord Bot Token** (Discord Developer Portal)

---

## First-time setup

```bash
# 1. Clone the repo
git clone https://github.com/YOUR_USERNAME/OpenClawClipperDocker.git
cd OpenClawClipperDocker

# 2. Create .env file with Discord bot token
cp .env.example .env
# Edit .env: DISCORD_BOT_TOKEN=your-token-here

# 3. Set up OpenClaw config
cp config/openclaw.example.json config/openclaw.json
cp config/exec-approvals.example.json config/exec-approvals.json
# The __DISCORD_BOT_TOKEN__ placeholder is auto-replaced at container startup

# 4. Start the stack
docker compose --profile gpu up -d    # GPU mode
docker compose --profile cpu up -d    # CPU mode

# 5. Models are auto-pulled on first startup via entrypoint.sh
# First startup: 10-20 minutes while Ollama models download (~16GB)
# Whisper large-v3 is pre-baked in the Docker image — no separate pull
```

---

## Docker image details

Base image: `nvidia/cuda:12.3.2-cudnn9-runtime-ubuntu22.04`

Image layers added by `Dockerfile`:
- CUDA 12.3 + cuDNN 9 (GPU acceleration for Whisper)
- Node.js 22 LTS (required by OpenClaw)
- Python 3 + faster-whisper (with GPU support)
- FFmpeg
- OpenClaw (installed via npm)
- Whisper large-v3 model weights (~3GB, pre-downloaded at build time)
- Flask + dashboard
- Pipeline script + entrypoint

---

## Container names and network

| Container | Profile | Purpose |
|---|---|---|
| `ollama-gpu` | `gpu` | LLM inference |
| `ollama-cpu` | `cpu` | LLM inference |
| `stream-clipper-gpu` | `gpu` | Pipeline + agent + dashboard |
| `stream-clipper-cpu` | `cpu` | Pipeline + agent + dashboard |

Network: `clipper-net` (Docker bridge). Ollama accessible at hostname `ollama` on port 11434.

---

## Volume mounts

| Host path | Container path | Purpose |
|---|---|---|
| `./config` | `/root/.openclaw` | OpenClaw config, sessions, exec approvals |
| `./workspace` | `/root/.openclaw/workspace` | AGENTS.md, skills |
| `./vods` | `/root/VODs` | Input VOD files |
| `./clips` | `/root/VODs/Clips_Ready` | Output rendered clips |
| `./scripts` | `/root/scripts` | Pipeline script |
| `./dashboard` | `/root/dashboard` | Web dashboard |
| `ollama_data` (named) | `/root/.ollama` | Ollama model storage |

---

## Common operations

```bash
# Start / stop
docker compose --profile gpu up -d
docker compose --profile gpu down

# Restart after config changes
docker compose --profile gpu restart

# View logs
docker compose logs -f stream-clipper-gpu
docker compose logs -f ollama-gpu

# Update pipeline (preserves models)
docker compose build --no-cache stream-clipper-gpu
docker compose --profile gpu up -d

# Full reset — DELETES models from volume
docker compose --profile gpu down -v

# Manage Ollama models
docker exec ollama-gpu ollama list
docker exec ollama-gpu ollama ps
docker exec ollama-gpu ollama pull qwen3.5:9b

# Enter container shell
docker exec -it stream-clipper-gpu bash

# Monitor running pipeline
docker exec stream-clipper-gpu bash -c "cat /tmp/clipper/pipeline_stage.txt"
docker exec stream-clipper-gpu bash -c "tail -f /tmp/clipper/pipeline.log"
docker exec stream-clipper-gpu bash -c "cat /tmp/clipper/pipeline_stages.log"
```

---

## Exec approvals

`config/exec-approvals.json` controls which shell commands the agent can run. Without this file, the `exec` tool is not exposed to the model at all (bot will describe commands instead of running them).

Default (allow all):
```json
{"*": {"allowlist": [{"pattern": "*"}]}}
```

Restricted:
```json
{"*": {"allowlist": [{"pattern": "bash /root/scripts/clip-pipeline.sh*"}]}}
```

---

## Build context optimization

`.dockerignore` excludes: `vods/`, `clips/`, `.git`, `config/`, `workspace/`, docs, env files.

Build context: ~107KB (was 32GB before `.dockerignore` was added — BUG 4 in [[concepts/bugs-and-fixes]]).

---

## Portability

The system is fully self-contained in one project directory. To deploy on a new machine: copy the directory, install Docker, create `.env`, start the stack. Models download automatically via `entrypoint.sh`.

To migrate models without re-downloading: export the `ollama_data` Docker volume.

---

## Related
- [[concepts/vram-budget]] — hardware requirements derived from VRAM needs
- [[entities/ollama]] — Ollama environment variables
- [[entities/dashboard]] — web dashboard access
- [[concepts/bugs-and-fixes]] — especially BUG 4 (build context), BUG 5 (Windows cross-platform)
