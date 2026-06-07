---
title: "Web Dashboard"
type: entity
tags: [dashboard, flask, web, ui, sse, docker-exec, originality, detached-exec, interface, hub]
sources: 3
updated: 2026-06-06
---

# Web Dashboard

A Flask-based single-page web app for controlling the clip pipeline without Discord. Port **5001 by default**, overridable via `DASHBOARD_PORT` (or `PORT`). If the preferred port is already taken — e.g. a background service squatting on it — `app.py` now **auto-rolls to the next free port** (scans 5001→5012) and prints `Dashboard ready at http://127.0.0.1:<port>`, so a port collision no longer crashes startup with the Windows `WSAEACCES` "An attempt was made to access a socket … forbidden" error. (Docker/`entrypoint.sh` still uses 5000.) The startup banner also reports the real run mode now — `native (bare-metal)` vs `docker exec` vs `inside container` — instead of always saying "docker exec". **Studio theme** (2026-06-06): zinc-dark surfaces + teal accent (`#14b8a6`/`#2dd4bf`), Plus Jakarta Sans UI font + JetBrains Mono for code/logs, rounded panels, a thin teal top accent line, and pill-style status/docker badges. (Was dark + purple `#7c5cfc` before.)

Layout (modularized 2026-05-01 — see [[concepts/modularization-plan]]):
- `dashboard/app.py` — 78-line Flask bootstrap + blueprint registration
- `dashboard/_state.py` — shared mutable state (paths, defaults, pipeline_process)
- `dashboard/config_io.py` — load/save helpers for config/{models,hardware,paths,originality}.json
- `dashboard/pipeline_runner.py` — DetachedDockerPipeline class, spawn/kill/poll, LM Studio reachability
- `dashboard/routes/{pipeline,vods,models,hardware,paths,originality,music,assets}_routes.py` — one Flask blueprint per URL domain
- `dashboard/templates/index.html` — single-page UI; uses `<script type="module">`
- `dashboard/static/app.js` — 67-line entry module wiring window.* handlers + DOMContentLoaded
- `dashboard/static/modules/*.js` — 8 ES modules (util, state, pipeline-ui, vods-panel, models-panel, hardware-panel, folders-panel, assets-panel)
- `dashboard/static/style.css` — Studio theme (~420 lines), teal/zinc, written to match the existing JS class names

> [!note] Studio theme redesign (2026-06-06)
> `templates/index.html` + `static/style.css` were replaced from an imported design package (Anthropic design artifact → `_design_handoff/`, git-ignored). The HTML was **merged, not overwritten** — every functional `id`/handler was preserved (all `btn-*` `addEventListener` targets, `vod-select-all` + the multi-VOD checkbox column, `chk-arc-stitch`, the originality controls, models/hardware/folders/assets panels, stage dots, SSE log). Verified all 11 `app.js` wired IDs exist and no statically-referenced JS id is missing. Retained on top of the design: the detailed **Pass B gate** option labels + tooltip, and hover **tooltips** on the originality controls. Emoji `iconMap` in `models-panel.js` blanked (the theme hides `.model-card-icon`). Fonts load via Google `@import` (graceful fallback to `system-ui`/monospace offline). A green-terminal alternative theme also shipped in the package but the Studio (teal) variant is the one implemented.

### Originality & Render panel (added April 2026)

New panel driving the [[concepts/originality-stack]]. Every control posts to `PUT /api/originality` on change (persisted to `config/originality.json`) and is forwarded as `CLIP_*` env vars when the pipeline spawns.

| Control | `CLIP_*` env | Effect |
|---|---|---|
| Framing mode | `CLIP_FRAMING` + `CLIP_CAMERA_PAN` | `blur_fill` (legacy) or `camera_pan` (face track). Picking `camera_pan` sets *both* env vars — consolidated 2026-05-02; was previously a dropdown + separate "Face-tracked camera pan" checkbox where the two had to agree manually. |
| Per-clip randomization | `CLIP_ORIGINALITY` | Wave A blur/eq/mirror/hook/subtitle variance |
| Narrative merge | `CLIP_NARRATIVE` | Wave C long-form storytime arcs |
| Stitch short moments | `CLIP_STITCH` | Wave C multi-segment posts |
| **Stitch setup→payoff arcs** | `CLIP_ARC_STITCH` **+** `CLIP_ARC_GUARANTEE_MIN_RATIO` | Fix 3 — renders A1/M3 cross-chunk arcs as a 2-part "Earlier: … → payoff" jump-cut. Enabling the checkbox **also loosens the arc-guarantee floor 0.6→0.45** (`config_io.py`) so the top arc actually reaches the final selection — otherwise arc-stitch has no arc to act on (on rich VODs strong Pass B moments out-score the dedicated arcs at 0.6). Off by default. See [[concepts/arc-aware-extraction]]. |
| **White-flash transitions** | `CLIP_FLASH_CUTS` | Brief white pops for engagement (seeded cadence + any model-picked beats). Off by default. See [[concepts/transition-animations]]. |
| **Jump-cut compression** | `CLIP_JUMP_CUTS` (off/gaps/on) | Drop dead air / rambling and jump-cut to the payoff with a white fade. Silence-only (safe) or Smart+silence (adds LLM-inferred cuts). Applied to the finished clip so captions stay in sync. Off by default. See [[concepts/transition-animations]]. |
| Voiceover layer | `CLIP_TTS_VO` | Wave D Piper TTS mix |
| Music bed folder | `CLIP_MUSIC_BED` | Wave D music path |
| Tier C music matching | `CLIP_MUSIC_TIER_C` | Wave D librosa scoring |
| AI editing profiles | `CLIP_STYLE_PROFILES` | Per-category editing layer — see [[concepts/style-profiles]]. Off by default. |
| **Scan Music** button | — | Runs `scripts/lib/scan_music.py` via `POST /api/music/scan` |
| **Scan Libraries** button | — | Runs `scripts/seed_libraries.py --scan` via `POST /api/libraries/scan` |

> [!note] HTML cache headers
> The `/` route in `dashboard/app.py` returns `Cache-Control: no-cache, no-store, must-revalidate` so future UI changes appear on a normal refresh. Static JS/CSS still cache normally. Before 2026-05-02 the HTML inherited Flask's default 12-hour `send_from_directory` cache, which made dashboard updates appear "missing" until the user hard-refreshed.

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
| **VOD Library** | All VODs with size, duration, processed status, transcription cache indicator. **Multi-select (2026-06-06):** a checkbox per row + a header **select-all** checkbox (indeterminate when a subset is picked). Clicking anywhere on a row also toggles it; selection state lives in `state.selectedVods` (array of stems). |
| **Clip Controls** | Style dropdown (8 styles), stream type hint, force reprocess checkbox (re-runs from selection AND re-transcribes, replacing the cached transcript — see [[concepts/bugs-and-fixes]] BUG 58). **"Clip Selected (N)"** runs the checked VODs sequentially via `/api/clip-batch` (label shows the count; disabled when none checked). **"Clip All"** still clips every VOD via `/api/clip-all` regardless of checkboxes. |
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
| `/api/clip` | POST | Start clipping a specific VOD (single `vod` stem) |
| `/api/clip-batch` | POST | Clip a chosen subset sequentially — `vods: [stem, …]`, validated against on-disk files, preserves selection order. Maps to `run_pipeline.py --vods a,b,c` (respects the Force checkbox; `--all` always forces). |
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
