---
title: "Ollama (retired)"
type: entity
tags: [inference-server, llm, docker, gpu, retired]
sources: 2
updated: 2026-04-18
---

# Ollama *(retired — replaced by LM Studio)*

> [!warning] Retired as of 2026-04-18
> Ollama has been removed from this project. LLM inference now runs via **[[entities/lm-studio]]** (LM Studio), a native Windows application that serves an OpenAI-compatible API on port 1234. The `ollama` Docker container, `Dockerfile.ollama`, and `scripts/entrypoint-ollama.sh` are no longer part of the active stack.
>
> **Reason for removal**: Ollama-in-Docker required WSL2 Vulkan driver hacks to use AMD GPUs, and these frequently fell back silently to CPU inference. LM Studio runs natively on Windows and handles NVIDIA+AMD multi-GPU without special drivers.

The content below is preserved for historical reference.

---

## Former role in the system

- Received inference requests from [[entities/openclaw]] at `http://ollama:11434`
- Auto-detected available hardware (NVIDIA GPU or CPU)
- Managed model loading/unloading from VRAM
- All user interaction routed through the Discord bot — Ollama was never accessed directly

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

## GPU backend modes

The `ollama` service is a single unified container. GPU backend is controlled at runtime via `config/hardware.json` (managed by the dashboard Hardware panel). Changing backend requires a `docker compose restart`.

| Backend | Set in `gpu_backend` | Notes |
|---|---|---|
| `cuda` | `"cuda"` | NVIDIA CUDA via Container Toolkit. Default. |
| `mixed` | `"mixed"` | NVIDIA + AMD via Vulkan. `OLLAMA_VULKAN=1` required. |
| `vulkan` | `"vulkan"` | AMD/Intel via Vulkan. `OLLAMA_VULKAN=1` required. |
| `cpu` | `"cpu"` | No GPU. All inference on CPU. |

The entrypoint (`scripts/entrypoint-ollama.sh`) reads `config/hardware.json` and sets `CUDA_VISIBLE_DEVICES` / `GGML_VK_VISIBLE_DEVICES` / `OLLAMA_VULKAN` accordingly before calling `exec ollama serve`.

> [!note] OLLAMA_VULKAN requirement
> Ollama 0.21+ ships with Vulkan disabled by default. `OLLAMA_VULKAN=1` must be set explicitly for `mixed` and `vulkan` backends. Without it, Ollama ignores `GGML_VK_VISIBLE_DEVICES` and falls through to CPU.

> [!warning] Vulkan ICD failure → silent CPU fallback
> If Vulkan ICDs fail to initialize (NVIDIA ICD injection issues, AMD DZN driver missing), Ollama silently runs all inference on CPU — high CPU usage, zero GPU utilization.
> `entrypoint-ollama.sh` now runs `vulkaninfo --summary` before starting Ollama in mixed/vulkan modes and **falls back to CUDA automatically** if no real GPU Vulkan devices are found, printing a warning banner. Check with `docker logs ollama | grep "inference compute"`.

The named Docker volume `ollama_data` persists models across restarts and backend switches.

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

- Container name: `ollama`
- Network alias: `ollama` (used by the clipper container for hostname resolution)
- Started with: `docker compose up -d` (no profile flags)

---

## Key commands

```bash
# Pull models
docker exec ollama ollama pull qwen3.5:9b
docker exec ollama ollama pull qwen2.5:7b
docker exec ollama ollama pull qwen3-vl:8b

# List downloaded models
docker exec ollama ollama list

# Check currently loaded models (in VRAM)
docker exec ollama ollama ps

# Verify Vulkan device indices (mixed/vulkan backends)
docker exec ollama vulkaninfo --summary

# View logs
docker compose logs -f ollama
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
