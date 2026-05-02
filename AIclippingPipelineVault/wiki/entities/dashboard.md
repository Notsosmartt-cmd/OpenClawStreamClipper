---
title: "Web Dashboard"
type: entity
tags: [dashboard, flask, web, ui, sse, docker-exec, originality, detached-exec, interface, hub]
sources: 3
updated: 2026-05-01
---

# Web Dashboard

A Flask-based single-page web app for controlling the clip pipeline without Discord. Port 5001 (entrypoint banner says 5000, code uses 5001 — long-standing mismatch unrelated to modularization). Dark-themed, purple accent (`#7c5cfc`).

Layout (modularized 2026-05-01 — see [[concepts/modularization-plan]]):
- `dashboard/app.py` — 78-line Flask bootstrap + blueprint registration
- `dashboard/_state.py` — shared mutable state (paths, defaults, pipeline_process)
- `dashboard/config_io.py` — load/save helpers for config/{models,hardware,paths,originality}.json
- `dashboard/pipeline_runner.py` — DetachedDockerPipeline class, spawn/kill/poll, LM Studio reachability
- `dashboard/routes/{pipeline,vods,models,hardware,paths,originality,music,assets}_routes.py` — one Flask blueprint per URL domain
- `dashboard/templates/index.html` — single-page UI; uses `<script type="module">`
- `dashboard/static/app.js` — 67-line entry module wiring window.* handlers + DOMContentLoaded
- `dashboard/static/modules/*.js` — 8 ES modules (util, state, pipeline-ui, vods-panel, models-panel, hardware-panel, folders-panel, assets-panel)
- `dashboard/static/style.css` — dark theme styles

### Originality & Render panel (added April 2026)

New panel driving the [[concepts/originality-stack]]. Every control posts to `PUT /api/originality` on change (persisted to `config/originality.json`) and is forwarded as `CLIP_*` env vars when the pipeline spawns.

| Control | `CLIP_*` env | Effect |
|---|---|---|
| Framing mode | `CLIP_FRAMING` | `blur_fill` / `smart_crop` / `centered_square` / `camera_pan` |
| Per-clip randomization | `CLIP_ORIGINALITY` | Wave A blur/eq/mirror/hook/subtitle variance |
| Narrative merge | `CLIP_NARRATIVE` | Wave C long-form storytime arcs |
| Stitch short moments | `CLIP_STITCH` | Wave C multi-segment posts |
| Face-tracked camera pan | `CLIP_CAMERA_PAN` | Wave E (requires framing=camera_pan) |
| Voiceover layer | `CLIP_TTS_VO` | Wave D Piper TTS mix |
| Music bed folder | `CLIP_MUSIC_BED` | Wave D music path |
| Tier C music matching | `CLIP_MUSIC_TIER_C` | Wave D librosa scoring |
| **Scan Music** button | — | Runs `scripts/lib/scan_music.py` via `POST /api/music/scan` |

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
| **Folder Settings** | Configure VOD source folder and clips output folder; persisted to `config/paths.json` |

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
| `/api/paths` | GET | Current VOD source and clips output folder paths |
| `/api/paths` | PUT | Update folder paths; reloads path globals immediately |
| `/api/browse-folder` | POST | Open native OS folder-picker dialog; returns selected path (host mode only) |

---

## Docker exec bridge

When running on Windows host (`INSIDE_DOCKER` env var not set), the dashboard detects it's outside the container and uses `docker exec` to run the pipeline. As of 2026-04-27 the pipeline runs **detached** (`docker exec -d`) so it survives Docker Desktop named-pipe failures — see [[concepts/bugs-and-fixes#BUG 31]].

```python
# spawn_pipeline() launches the pipeline detached:
docker exec -d -e CLIP_TEXT_MODEL=... stream-clipper \
    bash -c 'nohup bash /root/scripts/clip-pipeline.sh ... </dev/null >/dev/null 2>&1 &'
```

The host-side `subprocess.Popen` is replaced by a `DetachedDockerPipeline` wrapper that mimics the Popen interface (`poll`, `terminate`, `kill`, `pid`, `wait`). Its `poll()` reads two lifecycle markers the pipeline writes inside the container:

- `/tmp/clipper/pipeline.pid` — written at startup; contains `pid=`, `started=`, `persistent_log=`
- `/tmp/clipper/pipeline.done` — written by the EXIT trap; contains `exit_code=`, `finished=`, `persistent_log=`

Liveness check: probe the in-container PID with `docker exec stream-clipper kill -0 <pid>`. On Docker daemon timeout, return `None` (still-running) so a transient pipe failure doesn't false-positive completion.

Three files are mirrored from container to host every 5 s by a background polling thread:
- `/tmp/clipper/pipeline_stage.txt` → host `STAGE_FILE` (current stage label)
- `/tmp/clipper/pipeline_stages.log` → host `STAGES_LOG` (timestamped stage history)
- `/tmp/clipper/pipeline.log` → host `LOG_FILE` (full stdout for SSE)

Polling cadence was bumped from 2 s → 5 s when log mirroring was added; keeps host load roughly flat and takes pressure off the daemon when it's degraded.

The SSE generator at `/api/log/stream` requires the stage file mtime to be ≥ 30 s old before emitting the `done` event, even after `is_pipeline_running()` returns False — belt-and-suspenders against a transient false-positive `poll()`.

`/api/status` includes a `persistent_log` field with the host-visible path of the on-disk log under `clips/.pipeline_logs/`, translated from the in-container path stored in the lifecycle markers. Operators have a one-click post-mortem path even when Docker Desktop is wedged.

Ollama model queries bypass the stream-clipper container and go directly to the `ollama` container via `docker exec ollama curl -sf http://localhost:11434/api/tags` — this avoids any network-connectivity issues with the stream-clipper container.

---

## Pipeline log

The dashboard streams the pipeline log via SSE from `/api/log/stream`. The pipeline always writes to `/tmp/clipper/pipeline.log` inside the container (the script `tee`s its stdout there). In Windows-host mode, the polling thread mirrors the in-container log into the host `LOG_FILE` every 5 s; SSE reads from the host file. The stage file `/tmp/clipper/pipeline_stage.txt` is polled for the progress dots.

The pipeline also writes a persistent timestamped log under `<CLIPS_DIR>/.pipeline_logs/<UTC>_<vod-slug>.log` that survives the EXIT cleanup trap and is surfaced via `/api/status::persistent_log` for post-mortem use.

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
