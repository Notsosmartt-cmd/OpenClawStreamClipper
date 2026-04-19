---
title: "Web Dashboard"
type: entity
tags: [dashboard, flask, web, ui, sse, docker-exec]
sources: 2
updated: 2026-04-17
---

# Web Dashboard

A Flask-based single-page web app for controlling the clip pipeline without Discord. Port 5000. Dark-themed, purple accent (`#7c5cfc`).

Files: `dashboard/app.py` (~920 lines), `dashboard/templates/index.html`, `dashboard/static/{style.css, app.js}`.

---

## Running the dashboard

**On Windows host (recommended for development):**
```bash
pip install flask
python dashboard/app.py
# Open http://localhost:5000
```
The Windows dashboard detects the running Docker container and executes the pipeline inside it via `docker exec`. Docker containers must be running.

**Inside Docker (automatic):**
Dashboard starts automatically inside the container on port 5000 (via `entrypoint.sh`). Access at `http://localhost:5000` after `docker compose up`.

> [!warning] Container dashboard known issue (zombie process)
> The dashboard Flask app inside Docker can become a zombie process (`<defunct>`) if it crashes on startup (e.g., missing dependency). Since `entrypoint.sh` launches it with `&`, Docker still forwards port 5000 but nothing is listening. **Workaround**: run the dashboard locally on Windows host instead. The local Windows dashboard is the primary interface.

---

## Features

| Feature | Description |
|---|---|
| **VOD Library** | All VODs with size, duration, processed status, transcription cache indicator |
| **Clip Controls** | Style dropdown (8 styles), stream type hint, force reprocess checkbox |
| **Pipeline Monitor** | 8-stage progress dots, real-time log streaming via SSE, stage history with timestamps |
| **Clips Gallery** | In-browser video preview, download links |
| **Docker Status** | Green/red badge showing Docker container connectivity |
| **Model Switcher** | Select active text/vision/Whisper models per pipeline role; shows downloaded Ollama models |
| **Hardware Panel** | Select GPU backend (CUDA/mixed/Vulkan/CPU), GPU count, gpu_pair; Save + Restart Services button |

---

## REST API

| Endpoint | Method | Description |
|---|---|---|
| `/api/vods` | GET | List all VODs with metadata |
| `/api/status` | GET | Pipeline running/idle + Docker connectivity + `ollama_ok` flag |
| `/api/clip` | POST | Start clipping a specific VOD |
| `/api/clip-all` | POST | Clip all VODs sequentially |
| `/api/stop` | POST | Stop the running pipeline |
| `/api/clips` | GET | List generated clips |
| `/api/clips/<file>` | GET | Serve a clip for preview/download |
| `/api/diagnostics` | GET | Most recent diagnostic JSON |
| `/api/stages` | GET | Stage history with timestamps |
| `/api/log/stream` | GET | SSE endpoint for live pipeline log |
| `/api/models` | GET | Current model config with role metadata |
| `/api/models/available` | GET | Downloaded Ollama models + Whisper model list |
| `/api/models` | PUT | Update text/vision/whisper model selection |
| `/api/hardware` | GET | Hardware config, backend options, GPU capabilities |
| `/api/hardware` | PUT | Update gpu_backend, gpu_count, gpu_pair, whisper_device |
| `/api/restart` | POST | Run `docker compose restart` (Windows host mode only) |

---

## Docker exec bridge

When running on Windows host (`INSIDE_DOCKER` env var not set), the dashboard detects it's outside the container and uses `docker exec` to run the pipeline:

```python
# Instead of:
subprocess.Popen(["bash", "clip-pipeline.sh", ...])

# Uses:
subprocess.Popen(["docker", "exec", "stream-clipper", "bash",
                  "/root/scripts/clip-pipeline.sh", ...])
```

Stage file polling also works via `docker exec`: a background thread runs `docker exec stream-clipper cat /tmp/clipper/pipeline_stage.txt` every 2 seconds to track pipeline progress.

Ollama model queries bypass the stream-clipper container and go directly to the `ollama` container via `docker exec ollama curl -sf http://localhost:11434/api/tags` — this avoids any network-connectivity issues with the stream-clipper container.

---

## Pipeline log

The dashboard streams the pipeline log via SSE from `/api/log/stream`. The log is written to `/tmp/clipper/pipeline.log` inside the container (always, regardless of how the pipeline was started). The stage file `/tmp/clipper/pipeline_stage.txt` is polled for the progress dots.

---

## Hardware config managed by dashboard

The Hardware panel reads/writes `config/hardware.json` with fields:

| Field | Values | Purpose |
|---|---|---|
| `gpu_backend` | `cuda`, `mixed`, `vulkan`, `cpu` | Which GPU backend Ollama uses |
| `gpu_count` | `"1"`, `"2"`, `"all"` | Number of Vulkan GPUs (vulkan/mixed only) |
| `gpu_pair` | `"nvidia_primary"`, `"amd_primary"` | Device order for mixed mode (maps to `GGML_VK_VISIBLE_DEVICES`) |
| `whisper_device` | `cuda`, `cpu` | Auto-constrained: mixed → always cuda; vulkan/cpu → always cpu |

Saving hardware config requires a container restart to take effect. The **Restart Services** button calls `/api/restart` which runs `docker compose restart` from the project directory (Windows host mode only; inside Docker it returns an error with the manual command).

---

## Related
- [[entities/openclaw]] — the other interface (Discord bot)
- [[entities/discord-bot]] — primary interface for normal use
- [[concepts/clipping-pipeline]] — the 8 stages the dashboard monitors
- [[concepts/deployment]] — GPU backend setup and hardware config schema
