---
title: "Bugs and Fixes"
type: concept
tags: [bugs, fixes, debugging, history]
sources: 2
updated: 2026-04-07
---

# Bugs and Fixes

Known bugs encountered during development and how they were resolved. Useful for debugging similar symptoms.

---

## BUG 1 — Pipeline not reclipping after rebuild

**Symptom**: Bot says "All VODs already processed" after container rebuild.

**Cause**: All VODs listed in `processed.log` from previous runs; bot didn't use `--force` flag.

**Fix**: Clear `processed.log` or use `--force` flag. Dashboard has a "Force reprocess" checkbox.

---

## BUG 2 — PowerShell breaks `2>/dev/null` redirects

**Symptom**: Commands with `2>/dev/null` create files named `null` on Windows.

**Cause**: PowerShell interprets `2>` as a Windows redirect to `G:\dev\null`.

**Fix**: Wrap commands in `bash -c "..."` so bash handles redirects correctly. All pipeline invocations should go through bash, not PowerShell directly.

---

## BUG 3 — Dashboard JSON parsing error ("Unexpected token '<'")

**Symptom**: Frontend shows `"Unexpected token '<', '<!doctype'..."` when fetching clip data.

**Cause**: Flask returning HTML error pages (404/405/500) for API routes; JavaScript trying to parse HTML as JSON.

**Fix**: Added JSON error handlers for 404, 405, 500 in Flask. Hardened JS fetch: parse as text first, then `JSON.parse` with try/catch.

---

## BUG 4 — Docker build uploading 32GB on every build

**Symptom**: `docker compose build` takes forever, transfers ~32GB.

**Cause**: No `.dockerignore` file — all 48GB of VODs sent as build context.

**Fix**: Created `.dockerignore` excluding `vods/`, `clips/`, `config/`, `workspace/`, `.git`, docs, env files. Build context now ~107KB.

---

## BUG 5 — `os.setsid` AttributeError on Windows

**Symptom**: 500 error when clicking "Clip Selected" on Windows dashboard.

**Cause**: `os.setsid` is Linux-only; dashboard runs on Windows with Python 3.12.

**Fix**: Platform check: `os.setsid` on Linux, `CREATE_NEW_PROCESS_GROUP` on Windows. Also fixed `kill_pipeline` for cross-platform compatibility.

---

## BUG 6 — Dashboard can't see VODs ("No VODs found")

**Symptom**: Dashboard shows "No VODs found" despite 48GB of videos in `vods/`.

**Cause**: `app.py` used `BASE_DIR / "vods"` (= `dashboard/vods/`, an empty directory) instead of `PROJECT_DIR / "vods"` (= project root `vods/`).

**Fix**: Changed path resolution: `PROJECT_DIR = BASE_DIR.parent`, then `VODS_DIR = PROJECT_DIR / "vods"`. Removed empty `dashboard/vods/` and `dashboard/clips/` directories and their docker-compose mounts.

---

## BUG 7 — `processed.log` UnicodeDecodeError

**Symptom**: 500 error on `/api/vods` — `"utf-8 codec can't decode byte 0xff"`.

**Cause**: `processed.log` had UTF-16 LE BOM (FF FE) — likely written by a Windows tool. Python's default `read_text()` assumes UTF-8.

**Fix**: Reset file to empty UTF-8. Hardened reader with `encoding="utf-8", errors="replace"`.

---

## BUG 8 — Pipeline doesn't start from dashboard ("Waiting for pipeline")

**Symptom**: Clicking "Clip Selected" returns success but pipeline never starts; log viewer shows "Waiting for pipeline..." indefinitely.

**Cause**: Dashboard runs locally on Windows but spawns `bash clip-pipeline.sh` as a local process. The script needs:
- Ollama at `http://ollama:11434` (Docker internal network only)
- `faster-whisper`, `python3`, CUDA (only in container)
- VODs at `/root/VODs` (Docker mount)

Local bash process crashes immediately; stdout/stderr go to `DEVNULL` so no error is visible.

**Fix**: Dashboard now detects it's running outside Docker (`INSIDE_DOCKER` check). When on Windows host, uses `docker exec <container> bash /root/scripts/clip-pipeline.sh ...` to execute pipeline inside the running container. Pipeline stdout is piped to a local log file for SSE streaming. Stage files are polled via background thread running `docker exec cat /tmp/clipper/pipeline_stage.txt` every 2 seconds.

---

## BUG 9 — Early-VOD clip bias

**Symptom**: Most clips come from the first 30–60 minutes of multi-hour VODs.

**Cause**: LLM analyzes transcript chunks sequentially. Combined with top-N selection by score, early chunks fill the quota before later chunks are even considered. Also, keyword density tends to be higher early when the streamer is fresh.

**Fix**: Time-bucket distribution (Stage 4 Pass C). VOD divided into equal buckets, guaranteed picks from each before overflow fills remaining slots. Clips now spread across the entire timeline. See [[concepts/highlight-detection]].

---

## BUG 10 — Docker container dashboard crashes (zombie process)

**Symptom**: Dashboard inside Docker shows as zombie process (`<defunct>`).

**Cause**: Flask app inside Docker crashes on startup (e.g., missing dependency or import error). Since `entrypoint.sh` launched it with `&` (background), Docker still forwards port 5000 but nothing is listening.

**Status**: Not fully fixed. The local Windows dashboard is the primary interface. Container dashboard may need debugging separately.

**Workaround**: Run dashboard locally on Windows host (`python dashboard/app.py`) — it connects to the running container via `docker exec`. See [[entities/dashboard]].

---

## Whisper degenerate loop (known issue, not a bug per se)

**Symptom**: Whisper transcribes long audio and outputs only dots ("... ... ...") or repetitive "you you you".

**Cause**: Known upstream issue in faster-whisper with long audio files.

**Fix**: Stage 2 splits audio into 20-minute chunks before transcription. See [[entities/faster-whisper]].

---

## Related
- [[entities/dashboard]] — BUGs 3, 5, 6, 7, 8, 10 are dashboard-specific
- [[entities/faster-whisper]] — Whisper degenerate loop
- [[concepts/highlight-detection]] — BUG 9 (early-VOD bias fix)
- [[concepts/deployment]] — BUG 4 (build context), BUG 2 (Windows paths)
