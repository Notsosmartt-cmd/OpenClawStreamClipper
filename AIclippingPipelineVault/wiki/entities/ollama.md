---
title: "Ollama"
type: entity
tags: [inference-server, llm, docker, gpu]
sources: 2
updated: 2026-04-07
---

# Ollama

Local LLM inference server. Hosts and serves [[entities/qwen25]], [[entities/qwen35]], and [[entities/qwen3-vl]] over HTTP on port 11434. Runs as its own Docker container.

---

## Role in the system

- Receives inference requests from [[entities/openclaw]] at `http://ollama:11434`
- Auto-detects available hardware (NVIDIA GPU or CPU)
- Manages model loading/unloading from VRAM
- All user interaction routes through the Discord bot — Ollama is never accessed directly

---

## Environment variables

| Variable | Value | Purpose |
|---|---|---|
| `OLLAMA_KEEP_ALIVE` | `5m` | Unload model from VRAM after 5 minutes of inactivity |
| `OLLAMA_MAX_LOADED_MODELS` | `1` | Only one model in VRAM at a time — prevents OOM |
| `OLLAMA_FLASH_ATTENTION` | `1` | Enable flash attention for faster inference |
| `OLLAMA_CONTEXT_LENGTH` | `32768` | 32K token context window |
| `OLLAMA_HOST` | `0.0.0.0` | Bind to all interfaces (accessible over Docker network) |

`OLLAMA_MAX_LOADED_MODELS=1` is critical — without it, Ollama might try to keep multiple models resident and OOM on a 16GB card.

---

## GPU / CPU profiles

Docker Compose profiles:
- `--profile gpu` — passes `--gpus=all`; requires `nvidia-container-toolkit` on host
- `--profile cpu` — identical image without GPU flags

Both profiles share the same named Docker volume `ollama_data`. Models persist across profile switches and container restarts.

---

## Explicit model unloading

The pipeline doesn't rely on `OLLAMA_KEEP_ALIVE` alone. It explicitly unloads models between stages by calling the Ollama API with `keep_alive=0`:

```
Stage 2 prep: POST /api/generate {keep_alive: 0} → unload all models → load Whisper
Stage 6 prep: POST /api/generate {model: qwen3.5:9b, keep_alive: 0} → unload → load qwen3-vl:8b
Stage 7 prep: POST /api/generate {model: qwen3-vl:8b, keep_alive: 0} → unload → load Whisper
```

This ensures predictable VRAM state at each stage transition.

---

## Container name

- GPU mode: `ollama-gpu`
- CPU mode: `ollama-cpu`
- Network alias: `ollama` (used by the clipper container for hostname resolution)

---

## Key commands

```bash
# Pull models
docker exec ollama-gpu ollama pull qwen3.5:9b
docker exec ollama-gpu ollama pull qwen2.5:7b
docker exec ollama-gpu ollama pull qwen3-vl:8b

# List downloaded models
docker exec ollama-gpu ollama list

# Check currently loaded models (in VRAM)
docker exec ollama-gpu ollama ps

# View logs
docker compose logs -f ollama-gpu
```

---

## Model storage

Named Docker volume `ollama_data` mounted at `/root/.ollama` inside the container. Survives container restarts and image updates. Total size: ~16GB for all three models.

To migrate to a new machine: export/import the `ollama_data` Docker volume using standard Docker volume backup (`docker run --rm -v ollama_data:/data ...`).

---

## Healthcheck

`docker-compose.yml` includes a healthcheck (`ollama list`, every 60s). The clipper container waits for this healthcheck to pass before starting the Discord gateway. Prevents the agent from starting while models are still loading.

---

## Related
- [[entities/qwen25]] — Discord agent model
- [[entities/qwen35]] — pipeline text model
- [[entities/qwen3-vl]] — vision model
- [[concepts/vram-budget]] — full memory accounting
- [[concepts/deployment]] — setup, profiles, first-run
