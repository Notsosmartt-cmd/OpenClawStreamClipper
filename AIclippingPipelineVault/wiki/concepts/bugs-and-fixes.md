---
title: "Bugs and Fixes"
type: concept
tags: [bugs, fixes, debugging, history]
sources: 2
updated: 2026-04-19
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

## BUG 11 — apt-get fails during Docker build on Windows/WSL2

**Symptom**: `docker compose build` fails mid-layer with `E: Failed to fetch http://archive.ubuntu.com/...` — connection refused or timeout.

**Cause**: Docker BuildKit's isolated network on Windows/WSL2 has intermittent connectivity to `archive.ubuntu.com`. The default apt configuration makes one attempt per package with no retry or timeout — any transient DNS hiccup or dropped connection fails the entire layer.

**Fix**: Prepend apt retry/timeout config before `apt-get update` in **both** `Dockerfile` and `Dockerfile.ollama`:
```dockerfile
RUN echo 'Acquire::Retries "5";' > /etc/apt/apt.conf.d/80-retries \
    && echo 'Acquire::http::Timeout "30";' >> /etc/apt/apt.conf.d/80-retries \
    && echo 'Acquire::https::Timeout "30";' >> /etc/apt/apt.conf.d/80-retries \
    && apt-get update && apt-get install -y --no-install-recommends ...
```
This retries each package fetch up to 5 times with a 30-second timeout, surviving transient BuildKit network drops. Also ensure `zstd` is in the apt package list — newer Ollama versions use zstd-compressed archives and the installer fails silently without it.

---

## BUG 12 — Mixed mode falls back to CPU (OLLAMA_VULKAN disabled by default)

**Symptom**: In `mixed` or `vulkan` backend mode, Ollama logs "experimental Vulkan support disabled" and runs inference on CPU despite `GGML_VK_VISIBLE_DEVICES` being set.

**Cause**: Ollama 0.21+ ships with Vulkan disabled by default. Setting `GGML_VK_VISIBLE_DEVICES` alone is not enough — Ollama ignores it unless Vulkan is explicitly enabled.

**Fix**: Set `export OLLAMA_VULKAN=1` in `scripts/entrypoint-ollama.sh` for the `mixed` and `vulkan` backend cases, before calling `exec ollama serve`. Disabling CUDA (`CUDA_VISIBLE_DEVICES=""`) forces the code path that uses Vulkan.

---

## BUG 13 — `vulkaninfo` not found in container

**Symptom**: `docker exec ollama vulkaninfo --summary` returns "command not found".

**Cause**: `vulkan-tools` package not installed in `Dockerfile.ollama`.

**Fix**: Added `vulkan-tools` to the apt-get install list in `Dockerfile.ollama`. Rebuild with `docker compose build --no-cache ollama`.

---

## BUG 14 — Vulkan/mixed mode silently falls back to CPU when ICD fails

**Symptom**: Stage 3 (and all LLM stages) show high CPU usage but zero GPU utilization. `docker logs ollama` reports `inference compute library=cpu`. `vulkaninfo --summary` inside the container shows only `llvmpipe (LLVM)` — no discrete GPU hardware.

**Cause**: When `mixed` or `vulkan` backend is configured, `CUDA_VISIBLE_DEVICES=""` disables the CUDA path. If no real Vulkan GPU hardware is accessible (ICD init fails, `/dev/dxg` not mounted, Windows AMD driver not installed, or NVIDIA Vulkan ICD not injected by Container Toolkit), Ollama finds zero GPU devices and silently runs all inference on CPU. The pipeline continues to produce output but at ~10× the speed.

**Diagnostic output observed**:
```
# vulkaninfo --summary (inside container)
GPU0: deviceType = PHYSICAL_DEVICE_TYPE_CPU  ← only llvmpipe, no real GPU

# docker logs ollama
inference compute id=cpu library=cpu  ← CPU, not GPU
```

**Fix**: Added `count_real_vulkan_gpus()` helper to `scripts/entrypoint-ollama.sh` that runs `vulkaninfo --summary` before committing to Vulkan mode. If zero real (non-CPU) Vulkan devices are found:
- `mixed` and `vulkan` modes now **fall back to CUDA** automatically with a clear warning banner
- Inference runs on NVIDIA GPU instead of CPU
- The warning banner shows exact debugging steps for fixing Vulkan

**To confirm GPU is being used after fix**:
```bash
docker logs ollama 2>&1 | grep "inference compute"
# Should show: library=cuda (NVIDIA) or library=vulkan (Vulkan GPU)
# NOT: library=cpu
```

**Root cause of Vulkan ICD failure (WSL2)**:
- NVIDIA Vulkan ICD: Container Toolkit must inject it; `CUDA_VISIBLE_DEVICES=""` may interfere on WSL2
- AMD Vulkan ICD (Mesa DZN): requires AMD Adrenalin WSL2 driver installed on the Windows host, plus `/dev/dxg` and `/usr/lib/wsl` properly mounted

> [!warning] Mixed NVIDIA+AMD not yet confirmed working
> Even with the fallback fix, true mixed NVIDIA+AMD Vulkan inference has not been verified.
> The entrypoint will fall back to CUDA (NVIDIA-only) until both Vulkan ICDs initialize correctly.
> See [[concepts/deployment]] for setup requirements.

---

## BUG 15 — Qwen3.5 reasoning model returns empty content (token exhaustion)

**Symptom**: All Stage 4 chunks log "LLM call failed, skipping". Stage 6 shows "no JSON in response". LM Studio logs show `reasoning_tokens: 799, content: ""` with `finish_reason: "length"`.

**Cause**: `qwen/qwen3.5-9b` is a reasoning model. On the OpenAI-compatible endpoint in LM Studio, it spends its entire `max_tokens` budget on internal thinking (`reasoning_content`) and emits `content: ""`. The pipeline checks `if response:` — empty string is falsy, so all chunks fail.

**Important**: Stage 6 JSON truncation is a secondary symptom — even when some content IS generated, it gets cut off before the closing `}` because thinking already consumed most of the token budget. The JSON parse then fails because `rfind("}") == -1`.

**Diagnostic evidence from LM Studio logs**:
```json
"content": "",
"reasoning_content": "The user wants me to analyze...",  // 799 tokens
"finish_reason": "length"
```

**What does NOT work**: `/no_think` as a user-message prefix. This is a **Qwen3** feature that **Qwen3.5 dropped**. Despite appearing in Qwen3 documentation, it has no effect in Qwen3.5 models.

**Fix**: Use `chat_template_kwargs: {"enable_thinking": false}` in the LM Studio API request body — this is the correct LM Studio extension parameter. Applied at all three pipeline LLM call sites:
- Stage 3 payload: removed `/no_think` prefix, added `"chat_template_kwargs": {"enable_thinking": False}`, `max_tokens` raised 20 → 50
- Stage 4 `call_llm()`: same, default `max_tokens` 800 → 1500
- Stage 6 vision payload: same, `max_tokens` 800 → 1500

**LM Studio UI note**: The "When applicable, separate reasoning_content and content in API responses" toggle controls presentation only — it does NOT stop the model from thinking. Even with it enabled, `chat_template_kwargs: {"enable_thinking": false}` should suppress thinking. The token budget increase (max_tokens raised at all three sites) is the safety net: if thinking is not fully suppressed, the model now has room to finish reasoning AND produce content.

**max_tokens values after fix**:
- Stage 3: 50 → 1024
- Stage 4 `call_llm()`: 1500 → 3000 default
- Stage 6 vision: 1500 → 2000

**Diagnostics**: When `content` is empty, the pipeline now logs `finish_reason`, `reasoning_tokens`, and a preview of `reasoning_content`. This makes it possible to distinguish "model hit limit mid-thinking" from actual API errors.

---

## BUG 16 — LM Studio `/v1/models` flooded by dashboard status polls

**Symptom**: LM Studio logs show a constant stream of `GET /v1/models` requests, one every 3 seconds.

**Cause**: `check_lm_studio()` in `dashboard/app.py` calls `GET /v1/models` on every invocation, and `api_status()` is polled by the frontend every 3 seconds.

**Fix**: Added a 30-second time-based cache to `check_lm_studio()`. The result is cached in `_lm_studio_cache` and only re-fetched when the TTL expires. Reduces polling from 20× per minute to ≤2× per minute.

---

## BUG 17 — 35B+ models: `chat_template_kwargs` ignored, answer in `reasoning_content`

**Symptom**: With `qwen/qwen3.5-35b-a3b` (and potentially other large models), Stage 3 times out, Stage 4 logs all-chunk failures with `total_tokens=800`, Stage 6 shows all vision as failed. LM Studio logs show `finish_reason: stop` but `content: ""` and the full answer in `reasoning_content`.

**Cause**: Two compounding issues:
1. **`chat_template_kwargs: {"enable_thinking": false}` has no effect on the 35B MoE model** — it always routes its answer through `reasoning_content` and emits empty `content`, even when it finishes naturally (`finish_reason=stop`). This is model-specific: the 9B model respects this parameter, the 35B does not.
2. **Stage 4 Pass B had an explicit `max_tokens=800` override** at the call site (`call_llm(prompt, max_tokens=800)`) which overrode the function default of 3000 — this was the root cause of Stage 4 failures even before the reasoning_content issue.
3. **Stage 3 had `timeout=30`** — the 35B model needs 60–180 seconds for a single classification call (5–10s prompt processing + ~50s generation at ~15 tok/s).

**Diagnostic evidence**:
```json
"content": "",
"reasoning_content": "The user wants me to classify...\n\njust_chatting",
"finish_reason": "stop"   ← model FINISHED normally, answer is in reasoning_content
```

**Fix** (applied to `scripts/clip-pipeline.sh`):
1. **`reasoning_content` fallback**: When `content` is empty and `finish_reason == "stop"` and `reasoning_content` is non-empty, the pipeline now uses `reasoning_content` as the answer. Applied at all three LLM call sites:
   - Stage 3: scans `reasoning_content` for the segment type keyword
   - Stage 4 `call_llm()`: returns `reasoning_content` as the LLM response (JSON is parsed from it)
   - Stage 6 vision: parses JSON from `reasoning_content`
   - Token-limit case (`finish_reason="length"`) still falls through to retry as before
2. **Stage 4 call site fix**: `call_llm(prompt, max_tokens=800)` → `call_llm(prompt)` (uses 3000 default)
3. **Stage 3 timeout**: `timeout=30` → `timeout=180`

**Key distinction**: `finish_reason=stop` means the model finished naturally — its answer is in `reasoning_content`. `finish_reason=length` means it was cut off mid-thinking — retrying with more tokens is the right response.

---

## BUG 18 — Pipeline logs not persisted (lost after EXIT cleanup)

**Symptom**: Pipeline log at `/tmp/clipper/pipeline.log` is deleted when the cleanup trap runs on EXIT. No record of the run is available after the pipeline finishes.

**Cause**: The EXIT trap calls `rm -rf /tmp/clipper/*`, deleting the log file. Logs were only available during the run via SSE streaming from the dashboard.

**Fix**: Added a persistent timestamped log in `scripts/clip-pipeline.sh`. Every run now writes to both:
- `/tmp/clipper/pipeline.log` — ephemeral, for SSE streaming (still cleaned up on EXIT)
- `$CLIPS_DIR/.pipeline_logs/YYYYMMDD_HHMMSS_VODSLUG.log` — persistent, survives cleanup

The filename includes UTC timestamp and a sanitized VOD name slug (first 40 chars, alphanumeric/underscore/hyphen only). The log path is printed at pipeline startup: `=== Persistent log: ... ===`.

The `tee` command writes to both files simultaneously: `exec > >(tee -a "$PIPELINE_LOG" "$PERSISTENT_LOG") 2>&1`.

---

## BUG 21 — Stage 3 `max_tokens=1024` causes silent misclassification of all segments

**Symptom**: Stage 3 logs `Segment classify: empty content (finish=length, reasoning_tokens=1023)` for most chunks. All affected segments silently default to `just_chatting`. The segment map looks plausible but is largely incorrect — segments that should be `gaming`, `irl`, etc. are all classified as `just_chatting` by the fallback default.

**Why it's not obvious**: Most long VODs are predominantly `just_chatting` (~90%+), so the wrong default matches the correct answer most of the time. The pipeline continues and produces clips, making the misclassification invisible unless the monitor output is inspected.

**Cause**: Stage 3 `max_tokens=1024` — the 35B model uses all 1023/1024 tokens for reasoning (`finish=length`), leaving zero for the 1-word answer. The `reasoning_content` fallback (BUG 17 fix) only fires on `finish=stop` (natural termination), not `finish=length`. Chunks where the model finished naturally in under 1024 tokens (typically for highly distinctive transcripts like clear gaming or IRL content) produced correct classifications without warnings.

**Fix** (applied to `scripts/clip-pipeline.sh`):
1. `max_tokens` raised `1024` → `3000`: the 35B model needs ~1500–2500 reasoning tokens for classification; 3000 gives it room to finish naturally (`finish=stop`) and write the 1-word answer
2. Added `finish=length` tail-scan fallback: when still cut off, the last 600 characters of `reasoning_content` are scanned for the classification keyword. Models frequently write their tentative conclusion near the end of reasoning before being truncated (e.g., "...so this is just_chatting content" appears in the reasoning tail even when cut off)

---

## BUG 20 — 35B-A3B token exhaustion: thinking consumes all max_tokens, no content produced

**Symptom**: All Stage 4 chunks fail with `finish=length, reasoning_tokens=2999, total_tokens=3000, content=""`. Stage 6 fails on more demanding frames with `reasoning_tokens=1999, total_tokens=2000`. The `reasoning_content` fallback (BUG 17 fix) does NOT help because it only fires on `finish_reason=stop` (natural termination), not `finish_reason=length` (cut off mid-think).

**Root cause (confirmed from Qwen documentation and LM Studio bug tracker)**:

The `qwen3.5-35b-a3b` and `qwen3.5-9b` have **opposite defaults**:
- **9B**: thinking **disabled by default**. `chat_template_kwargs: {"enable_thinking": false}` is redundant (no-op) but harmless. Model answers directly with ~100–200 tokens.
- **35B-A3B**: thinking **enabled by default** AND LM Studio's OpenAI-compatible `/v1/chat/completions` endpoint does NOT forward `chat_template_kwargs` to the model's chat template for this model. Thinking cannot be disabled. Every call begins with `"Thinking Process:\n\n1. Analyze the Request:..."` and uses its full thinking budget before producing content.

The 35B-A3B model's default thinking budget is ~8,192 tokens. At `max_tokens=3000`, it consumes 2,999 tokens on reasoning, hits the limit, and emits `content=""`. The JSON answer is never written.

**Architecture note**: `35b-a3b` = 35 billion total parameters, ~3 billion activated per token (sparse MoE with 8 routed + 1 shared experts, 8.6% activation rate). The MoE routing and thinking mode are tightly coupled in the 35B variant in ways that differ from the 9B dense model.

**Fix** (applied to `scripts/clip-pipeline.sh`):
- `call_llm()` `max_tokens`: `3000` → `8000` — gives the 35B model room to finish its natural reasoning phase (~3000–6000 tokens) and still have budget for the JSON answer
- `call_llm()` `timeout`: `300` → `600` s — at ~30 tok/s, 8000 tokens takes ~267 s of generation + prefill; 600 s gives a 2× safety margin
- Stage 6 `max_tokens`: `4000` → `6000` — vision prompts are simpler but still need ~2000–4000 reasoning tokens on the 35B model
- `VISION_STAGE_TIMEOUT`: `1200` → `3600` s — 11 moments × ~220 s each exceeds the previous 20-minute limit

**Expected behavior after fix**: Model uses ~3000–6000 thinking tokens, then produces the JSON answer. `reasoning_content` fallback catches any `finish_reason=stop` edge cases. If the model still exhausts budget, increase `max_tokens` further (theoretical maximum before content is produced is ~8192 tokens of reasoning).

---

## BUG 19 — LM Studio queue backup: short timeouts cause cascading failures across all chunks

**Symptom**: With a 35B model, only 1 out of 44 Stage 4 chunks succeeds. Diagnostic: one chunk succeeds on attempt 2 immediately after attempt 1 times out, while all surrounding chunks fail all 3 attempts. Stage 6 vision shows "timed out" on the first frame of several moments.

**Cause**: LM Studio processes requests sequentially. When `call_llm()` had `timeout=120` and the 35B model needs 150–250 s per chunk:
1. Attempt 1 times out after 120 s, but LM Studio is still processing
2. Attempt 2 is immediately submitted — now TWO requests are queued in LM Studio
3. Attempt 3 is submitted — THREE requests queued
4. All 3 attempts fail and the chunk is skipped, but LM Studio's queue now has 3 abandoned requests to work through
5. The next chunk's attempt 1 arrives while LM Studio is still draining the previous chunk's queue → it also times out
6. This cascades: every chunk adds 3 more requests to LM Studio's backlog, eventually making all subsequent chunks impossible to process

The one chunk that succeeded (Chunk 2, attempt 2) did so because LM Studio happened to finish Chunk 1's request at that exact moment and processed Chunk 2 before the backlog grew further.

The same mechanism affects Stage 6: `VISION_PER_MOMENT_TIMEOUT=90` was too short for 35B vision calls (~150-200 s), causing the same abandonment pattern.

**Additional Stage 6 issue**: `max_tokens=2000` is too tight — the 35B model uses 1100–1999 reasoning tokens before writing the JSON answer (~100 tokens). When reasoning hits 1999/2000 tokens, `finish_reason=length` fires and content is empty. Successful calls used 1148–1690 reasoning tokens, so increasing to 4000 gives the model room to finish.

**Fix**:
- `call_llm()` default timeout: `120` → `300` s (35B calls typically complete in 150–250 s)
- Stage 6 `VISION_PER_MOMENT_TIMEOUT`: `90` → `300` s
- Stage 6 `max_tokens`: `2000` → `4000` (extra headroom for reasoning-heavy calls)

**Key principle**: The timeout must be set ABOVE the model's actual latency. A timeout below actual latency causes more requests to be submitted per chunk than LM Studio can drain between chunks, creating exponentially growing queue depth.

---

## Related
- [[entities/dashboard]] — BUGs 3, 5, 6, 7, 8, 10 are dashboard-specific
- [[entities/lm-studio]] — BUGs 15, 16, 17, 19 are LM Studio / pipeline integration bugs
- [[entities/faster-whisper]] — Whisper degenerate loop
- [[concepts/highlight-detection]] — BUG 9 (early-VOD bias fix)
- [[concepts/deployment]] — BUG 4 (build context), BUG 2 (Windows paths), BUG 11 (apt build), BUG 12–14 (Vulkan/GPU)
