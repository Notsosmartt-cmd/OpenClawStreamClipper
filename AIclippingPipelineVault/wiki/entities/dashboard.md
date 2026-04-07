---
title: "Web Dashboard"
type: entity
tags: [dashboard, flask, web, ui, sse, docker-exec]
sources: 2
updated: 2026-04-07
---

# Web Dashboard

A Flask-based single-page web app for controlling the clip pipeline without Discord. Port 5000. Dark-themed, purple accent (`#7c5cfc`).

Files: `dashboard/app.py` (~410 lines), `dashboard/templates/index.html`, `dashboard/static/{style.css, app.js}`.

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

---

## REST API

| Endpoint | Method | Description |
|---|---|---|
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

## Docker exec bridge

When running on Windows host (`INSIDE_DOCKER` env var not set), the dashboard detects it's outside the container and uses `docker exec` to run the pipeline:

```python
# Instead of:
subprocess.Popen(["bash", "clip-pipeline.sh", ...])

# Uses:
subprocess.Popen(["docker", "exec", "stream-clipper-gpu", "bash",
                  "/root/scripts/clip-pipeline.sh", ...])
```

Stage file polling also works via `docker exec`: a background thread runs `docker exec stream-clipper-gpu cat /tmp/clipper/pipeline_stage.txt` every 2 seconds to track pipeline progress.

---

## Pipeline log

The dashboard streams the pipeline log via SSE from `/api/log/stream`. The log is written to `/tmp/clipper/pipeline.log` inside the container (always, regardless of how the pipeline was started). The stage file `/tmp/clipper/pipeline_stage.txt` is polled for the progress dots.

---

## Open feature request

> [!todo] Model switcher UI
> User requested a dashboard section showing the AI models used in each pipeline stage, with the ability to swap them for different models without editing config files. This would allow trying larger models if hardware is upgraded. Not yet implemented.
> See [[concepts/open-questions]] for full context.

---

## Related
- [[entities/openclaw]] — the other interface (Discord bot)
- [[entities/discord-bot]] — primary interface for normal use
- [[concepts/clipping-pipeline]] — the 8 stages the dashboard monitors
- [[concepts/open-questions]] — model switcher feature request
