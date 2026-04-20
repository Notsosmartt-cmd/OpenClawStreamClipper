# Log

Append-only chronological record of wiki operations. Newest entries at top.

Format: `## [YYYY-MM-DD] operation | Title`
Grep recent: `grep "^## \[" wiki/log.md | head -10`

---

## [2026-04-20] update | Hook caption, title spaces, 2× speed, fonts

Multiple Stage 7 rendering additions:
- **Hook caption**: AI-generated punchy top-of-video title in the style/voice of the stream niche. New `hook` field added to Stage 6 LLM prompt and manifest. `CLIP_HOOK_CAPTION` env var (default `true`); dashboard "Hook caption" checkbox toggle. Rendered via FFmpeg `drawtext` filter (DejaVuSans-Bold, white box, black text, top-center, y=55). `fonts-dejavu-core` added to Dockerfile.
- **Title spaces**: Clip filenames now use spaces instead of underscores (e.g. `Epic Clutch Play.mp4`). Title sanitization updated in manifest generation Python block.
- **2× speed**: Added `2.0` option to the dashboard Speed dropdown.
- Hook text wraps at 22 chars/line (max 3 lines) via Python `textwrap`, written to per-clip `clip_{T}_hook.txt` temp file to avoid shell quoting issues.

Pages touched: [[concepts/clip-rendering]].

Files changed: `scripts/clip-pipeline.sh`, `dashboard/app.py`, `dashboard/templates/index.html`, `dashboard/static/app.js`, `Dockerfile`.

---

## [2026-04-19] update | Simplified pitch: proportional to speed, no separate control

Removed independent voice pitch control. Pitch now always equals speed (`rubberband=tempo=N:pitch=N`) so voice sounds like a natural fast-talker. Removed `CLIP_PITCH` env var, Voice pitch dropdown from dashboard, and all `pitch` parameters from `app.py` and `app.js`. Pages touched: [[concepts/clip-rendering]].

---

## [2026-04-19] update | Speed-up + pitch shift for clip rendering

Added video speed-up and voice pitch controls to Stage 7 rendering. `CLIP_SPEED` (1.0/1.1/1.25/1.5) prepends `setpts=PTS/N` to the blur-fill filter chain and drives `rubberband=tempo=N:pitch=P` on the audio stream. `CLIP_PITCH` (1.0/1.059/1.122/1.189) sets the voice pitch ratio independently of tempo (no chipmunk effect at default 1.0). SRT timestamps are rescaled by `1/speed` via `rescale_srt()` when speed ≠ 1.0. Dashboard gains Speed and Voice pitch dropdowns in Clip Controls. Pages touched: [[concepts/clip-rendering]].

Files changed: `scripts/clip-pipeline.sh`, `dashboard/app.py`, `dashboard/templates/index.html`, `dashboard/static/app.js`.

---

## [2026-04-19] update | Caption size reduced + dashboard caption toggle

Reduced subtitle font size from 16 → 11 in Stage 7 FFmpeg render. Added `CLIP_CAPTIONS` env var (default `true`) to pipeline — when `false`, renders without subtitle filter. Dashboard Clip Controls panel gains a **Captions** checkbox (checked by default) that controls the toggle via the `/api/clip` and `/api/clip-all` POST bodies. Pages touched: [[concepts/clip-rendering]].

Files changed: `scripts/clip-pipeline.sh`, `dashboard/app.py`, `dashboard/templates/index.html`, `dashboard/static/app.js`.

---

## [2026-04-19] update | README rewrite + wiki deployment update; classification system documented

Complete `README.md` rewrite to reflect current architecture (LM Studio, no Ollama). Major additions:
- **Setup Guide** (8 steps, Discord bot intentionally last): prerequisites → clone → configs → LM Studio setup → build container → configure dashboard models → test pipeline → Discord bot
- **Classification System** section: every file that participates in deciding what gets clipped, data flow diagram, keyword category tables, segment-type weight multipliers table
- Updated Models section: LM Studio model IDs, 35B vs 9B tradeoffs, thinking mode note
- Updated Troubleshooting: Stage 3/4/6 failures, LM Studio unreachable, token budget issues
- Updated Project Structure: reflects current single-container + LM Studio architecture

`AIclippingPipelineVault/wiki/concepts/deployment.md` fully rewritten: LM Studio-centric, step-by-step setup, volume mounts, config file reference, persistent log docs.

`AIclippingPipelineVault/wiki/overview.md`: Updated model table to current LM Studio IDs; added 35B vs 9B note.

`AIclippingPipelineVault/wiki/index.md`: Fixed stale model descriptions; updated bugs-and-fixes count (21); updated deployment description.

Pages touched: [[overview]], [[concepts/deployment]], [[index]].

## [2026-04-19] update | Fix Stage 3 silent misclassification: max_tokens 1024→3000, add tail-scan fallback

Stage 3 `max_tokens=1024` caused the 35B model to use 1023/1024 tokens on reasoning (finish=length) for almost every chunk, silently defaulting all segments to `just_chatting`. Fix (BUG 21): `max_tokens` raised to 3000 so the model has room to finish thinking naturally; added `finish=length` tail-scan fallback (last 600 chars of `reasoning_content`) as a safety net if still cut off.

Pages touched: [[concepts/bugs-and-fixes]] (BUG 21).

## [2026-04-19] update | Fix 35B token exhaustion: raise max_tokens; research 9B vs 35B thinking behavior

Root cause of all remaining Stage 4/6 failures with `qwen/qwen3.5-35b-a3b` confirmed (BUG 20): LM Studio's endpoint does not forward `chat_template_kwargs` to the 35B model's chat template. Thinking cannot be disabled. The 35B has thinking ENABLED by default (~8192 token budget); the 9B has it DISABLED by default. At `max_tokens=3000`, the model used 2999 reasoning tokens and hit the limit before writing any content.

Changes to `scripts/clip-pipeline.sh`:
- `call_llm()` `max_tokens` `3000` → `8000`
- `call_llm()` `timeout` `300` → `600` s (8000 tokens at ~30 tok/s = ~267 s generation)
- Stage 6 `max_tokens` `4000` → `6000`
- `VISION_STAGE_TIMEOUT` `1200` → `3600` s (11 moments × ~220 s/moment with 35B)

Pages touched: [[concepts/bugs-and-fixes]] (BUG 20), [[entities/lm-studio]] (9B vs 35B thinking table, confirmed failure modes).

## [2026-04-19] update | Fix LM Studio queue backup; increase Stage 4/6 timeouts and Stage 6 max_tokens

Three fixes to `scripts/clip-pipeline.sh` for large (35B MoE) model support (BUG 19):

1. **`call_llm()` timeout** `120` → `300` s: The 35B model takes 150–250 s per Stage 4 chunk. At 120 s, every attempt timed out and submitted another request to LM Studio while it was still processing. Queue depth grew by 3 per chunk, causing all subsequent chunks to time out — except one that fluked through while LM Studio was between requests.

2. **Stage 6 `VISION_PER_MOMENT_TIMEOUT`** `90` → `300` s: Same logic — 35B vision calls need ~150–200 s. 90 s caused per-frame timeouts which fed the LM Studio queue.

3. **Stage 6 `max_tokens`** `2000` → `4000`: The 35B model uses 1148–1999 reasoning tokens before writing the JSON answer (~100 tokens). Calls that hit 1999/2000 tokens got `finish_reason=length` and empty content. 4000 tokens gives room to finish.

Pages touched: [[concepts/bugs-and-fixes]] (BUG 19).

## [2026-04-19] update | reasoning_content fallback for 35B models; persistent timestamped logs; Stage 3/4 fixes

Four fixes applied to `scripts/clip-pipeline.sh` to handle large models (35B MoE) and improve observability:

1. **`reasoning_content` fallback** (BUG 17): When `content` is empty and `finish_reason == "stop"`, all three LLM call sites (Stages 3, 4, 6) now extract the answer from `reasoning_content`. Models like `qwen/qwen3.5-35b-a3b` ignore `chat_template_kwargs` and always put their answer there. `finish_reason=length` (mid-think cutoff) still retries as before. Applied in the Stage 3 inline block, `call_llm()`, and the Stage 6 vision loop.

2. **Stage 3 timeout** (BUG 17): `timeout=30` → `timeout=180`. The 35B model needs 60–180 s per classification call; 30 s caused every chunk to time out.

3. **Stage 4 call site fix** (BUG 17): `call_llm(prompt, max_tokens=800)` → `call_llm(prompt)`. This explicit override was causing `total_tokens=800` in all Stage 4 diagnostics despite the function default being 3000.

4. **Persistent timestamped log** (BUG 18): Every pipeline run now writes to both the ephemeral `/tmp/clipper/pipeline.log` (for SSE streaming, cleaned up on EXIT) and a new `$CLIPS_DIR/.pipeline_logs/YYYYMMDD_HHMMSS_VODSLUG.log` that survives the cleanup trap. Path printed at startup.

Pages touched: [[concepts/bugs-and-fixes]] (BUG 17, BUG 18), [[entities/lm-studio]] (request format, reasoning_content fallback note).

## [2026-04-19] update | Increase LLM token budgets; rich diagnostics for empty-content case

Follow-up to the Qwen3.5 thinking fix. Clarified from LM Studio docs: the "separate reasoning_content and content" toggle controls **presentation** only, not whether the model thinks. `chat_template_kwargs: {"enable_thinking": false}` may suppress thinking, but the generous token budget is the safety net if it doesn't — the model can finish reasoning AND still produce content before hitting the limit.

Changes to `scripts/clip-pipeline.sh`:
- Stage 3 `max_tokens` 50 → 1024 (50 was never sufficient even without thinking)
- Stage 4 `call_llm()` default `max_tokens` 1500 → 3000; completely rewritten response handling: detects empty content, logs `finish_reason` + `reasoning_tokens` + `reasoning_content` preview, separates "still thinking" from actual errors, only counts as failure if content is empty after all retries
- Stage 6 vision `max_tokens` 1500 → 2000; same diagnostic pattern applied; JSON parse errors now caught separately from empty-content cases

Pages touched: [[concepts/bugs-and-fixes]], [[entities/lm-studio]].

## [2026-04-18] update | Fix Qwen3.5 thinking via chat_template_kwargs; unified model; context length API

Root cause of all Stage 4/6 LLM failures confirmed: `/no_think` user-message prefix has no effect on Qwen3.5 (it was removed from Qwen3 → Qwen3.5). Correct LM Studio parameter is `chat_template_kwargs: {"enable_thinking": false}` in the request body.

Changes:
- `scripts/clip-pipeline.sh`: Replaced `/no_think` prefix with `chat_template_kwargs` at Stages 3, 4, 6. Added `load_model()` bash function that calls `/api/v1/models/load` with `context_length` before Stage 3 (and conditionally before Stage 6). Stage 5→6 model swap now skipped when `TEXT_MODEL == VISION_MODEL`. Stage 6 `max_tokens` raised 800 → 1500. Default `TEXT_MODEL` and `VISION_MODEL` both set to `qwen/qwen3.5-9b`. Added `CONTEXT_LENGTH` env var (default 8192).
- `config/models.json`: `vision_model` → `qwen/qwen3.5-9b`, added `context_length: 8192`.
- `dashboard/app.py`: `DEFAULT_MODELS` and `SUGGESTED_MODELS` updated for unified model; added `CONTEXT_LENGTH_GUIDE`; `/api/models` now returns `context_length_guide`; PUT handler accepts `context_length`; both `pipeline_env()` and `spawn_pipeline()` inject `CLIP_CONTEXT_LENGTH`.
- `dashboard/static/app.js`: Added context window picker card in Models panel with VRAM guidance; `updateSaveBar()` tracks context changes.

Pages touched: [[concepts/bugs-and-fixes]] (BUG 15 revised), [[entities/lm-studio]].

## [2026-04-18] update | Fix Qwen3 reasoning token exhaustion; correct model IDs; cache LM Studio poll

Fixed three root causes of pipeline LLM call failures:

1. **`/no_think` prefix on all pipeline LLM calls** (`scripts/clip-pipeline.sh`): `qwen/qwen3.5-9b` is a reasoning model — without this switch it spends all `max_tokens` on thinking (`reasoning_content`) and returns `content: ""`, silently failing every call. Added `/no_think\n\n` prefix to Stage 3 payload, Stage 4 `call_llm()`, and Stage 6 vision text part. Also raised `max_tokens`: Stage 3: 20→50, Stage 4 default: 800→1500.

2. **Correct model IDs** (`config/models.json` already correct; `dashboard/app.py` `DEFAULT_MODELS` and `SUGGESTED_MODELS` updated; `config/openclaw.json` model entries updated): LM Studio uses `org/model` format — `qwen/qwen3.5-9b`, `qwen/qwen3-vl-8b`, `qwen/qwen2.5-vl-7b`. Old stale IDs (`qwen3.5-9b-instruct`, `qwen2.5-vl-7b-instruct`) replaced throughout dashboard code and OpenClaw config.

3. **LM Studio poll cache** (`dashboard/app.py`): `check_lm_studio()` was calling `GET /v1/models` every 3 seconds (every status poll). Added 30-second TTL cache via `_lm_studio_cache` module global — reduces poll rate from 20×/min to ≤2×/min.

Pages touched: [[concepts/bugs-and-fixes]] (BUG 15, BUG 16), [[entities/lm-studio]].

## [2026-04-18] update | Fix openclaw.json api field; add LM Studio model picker with recommendations

Fixed `config/openclaw.json` and `config/openclaw.example.json`: `api: "openai"` → `api: "openai-completions"` (OpenClaw only accepts specific API type strings — this was causing the container restart loop). Added `SUGGESTED_MODELS` dict to `dashboard/app.py` returned via `/api/models`; updated `dashboard/static/app.js` to fix stale `availableOllama` → `availableLmStudio`, use `suggested` data to show ⭐ markers and tip/warning messages in model dropdowns, `resetModel()` now switches to the suggested model ID (with alert if not loaded in LM Studio), simplified Hardware panel to only show `whisper_device` (GPU backend now managed in LM Studio), fixed `restartServices()` to check `lm_studio` not `ollama` field. Added `.model-status-warn` / `.model-status-tip` CSS. Pages touched: [[entities/lm-studio]].

## [2026-04-18] update | Migrate LLM backend from Ollama-in-Docker to LM Studio (native Windows)

Replaced the `ollama` Docker container with LM Studio running natively on Windows. LM Studio serves an OpenAI-compatible API on port 1234, accessible from the container via `http://host.docker.internal:1234`. Motivation: native NVIDIA+AMD multi-GPU support without WSL2 Vulkan driver hacks (which caused silent CPU fallback — see BUG 14 in [[concepts/bugs-and-fixes]]).

Code changes:
- `docker-compose.yml`: removed `ollama` service and `ollama_data` volume; added `extra_hosts: ["host.docker.internal:host-gateway"]` to `stream-clipper`
- `scripts/clip-pipeline.sh`: `OLLAMA_URL` → `LLM_URL`; `unload_ollama()` → `unload_model()` (uses `/api/v1/models/unload`); `call_ollama()` → `call_llm()` (OpenAI API format); all three Python heredocs (Stages 3, 4, 6) updated to `/v1/chat/completions`, OpenAI payload structure, response via `choices[0].message.content`, vision via `image_url` content part; think-tag stripping added
- `scripts/entrypoint.sh`: removed Ollama wait + model pull; added LM Studio readiness poll (`GET /v1/models`)
- `config/hardware.json`: removed `gpu_backend`/`gpu_count`/`gpu_pair`; only `whisper_device` remains
- `config/models.json`: `ollama_url` → `llm_url`; model IDs updated to LM Studio format (`qwen3.5-9b-instruct`, `qwen2.5-vl-7b-instruct`)
- `config/openclaw.json` + `openclaw.example.json`: provider changed from `ollama` to `lmstudio` with `baseUrl` pointing to port 1234
- `dashboard/app.py`: removed `get_ollama_container()`; replaced `query_ollama_models()` with `query_lm_studio_models()` (calls `/v1/models`); added `check_lm_studio()`; `api_status()` now reports `lm_studio` not `ollama`; simplified `DEFAULT_HARDWARE` and `api_hardware_update()`

Pages touched: [[overview]], [[entities/lm-studio]] (created), [[entities/ollama]] (marked retired), [[concepts/vram-budget]], [[index]].

## [2026-04-17] update | Fix Vulkan CPU fallback; add GPU detection to entrypoint; strengthen CLAUDE.md

Diagnosed Stage 3+ high CPU usage: Vulkan ICDs not initializing inside container, Ollama silently using CPU (confirmed via `inference compute library=cpu` in docker logs and `vulkaninfo` showing only llvmpipe). Fixed `scripts/entrypoint-ollama.sh`: added `count_real_vulkan_gpus()` helper that runs `vulkaninfo --summary` before committing to Vulkan mode; if no real GPU hardware found, auto-falls back to CUDA with a warning banner instead of silently using CPU. Added prompt injection banner to `CLAUDE.md`. Fixed stale container names in `CLAUDE.md` (`ollama-gpu` → `ollama`, `stream-clipper-gpu` → `stream-clipper`). Pages touched: [[entities/ollama]], [[concepts/bugs-and-fixes]] (BUG 14), [[concepts/deployment]].

## [2026-04-17] lint | Post-refactor wiki audit and fixes

Audited all wiki pages against actual codebase after profile-collapse refactor. Fixed stale container names (`ollama-gpu` → `ollama`, `stream-clipper-gpu` → `stream-clipper`), removed old profile commands, documented `OLLAMA_VULKAN=1` requirement (BUG 12), documented `vulkan-tools` fix (BUG 13), updated dashboard feature list (model switcher + hardware panel now implemented), expanded REST API table with 6 new endpoints, added hardware.json schema table to dashboard page, added deprecated-files notice to deployment page, fixed `spawn_pipeline()` error message in `dashboard/app.py` (still referenced old `--profile` flags). Pages touched: [[overview]], [[entities/ollama]], [[entities/dashboard]], [[concepts/deployment]], [[concepts/bugs-and-fixes]].

## [2026-04-17] update | Collapse multi-profile architecture to single service pair

Removed all Docker Compose profiles (cuda/vulkan/mixed/cpu). Single `ollama` + `stream-clipper` service pair. New `Dockerfile.ollama` (unified CUDA+Vulkan image) and `scripts/entrypoint-ollama.sh` (reads hardware.json, sets CUDA_VISIBLE_DEVICES / GGML_VK_VISIBLE_DEVICES). WSL2 AMD Vulkan enabled via /dev/dxg + /usr/lib/wsl mounts in compose. Dashboard Hardware panel gains "Restart Services" button (calls new /api/restart endpoint). Pages touched: [[concepts/deployment]].

## [2026-04-17] update | Fix apt-get network failures in Dockerfile.ollama-vulkan

Added apt retry and timeout configuration to `Dockerfile.ollama-vulkan` to handle intermittent Docker BuildKit network issues on Windows/WSL2. The `apt-get` layer now retries each package fetch up to 5 times with a 30-second timeout before failing. Pages touched: [[concepts/bugs-and-fixes]].

## [2026-04-16] update | Multi-backend GPU support — CUDA, Vulkan (AMD), CPU profiles

Added Vulkan (AMD/Intel) and explicit CUDA/CPU backend selection. New docker-compose profiles: `cuda` (NVIDIA, also aliased `gpu`), `vulkan` (AMD/Intel), `cpu`. New files: `Dockerfile.ollama-vulkan`, `scripts/entrypoint-ollama-vulkan.sh`, `config/hardware.json`. Whisper device (`cuda`/`cpu`) now controlled via `CLIP_WHISPER_DEVICE` env var read from hardware config; Vulkan and CPU modes force Whisper to CPU. Dashboard Hardware panel added for backend, GPU count, and Whisper device selection. Pages touched: [[concepts/deployment]], [[concepts/vram-budget]].

## [2026-04-07] update | Full wiki rebuild — external summaries integrated and removed

Ingested `DEVELOPMENT_SUMMARY.txt` and `fix.txt`. Corrected all inaccuracies from initial bootstrap (7→8 stages, missing models, wrong rendering technique, wrong Whisper hardware). External summary files deleted.

Pages rewritten: [[overview]], [[entities/faster-whisper]], [[entities/qwen3-vl]], [[entities/qwen35]], [[entities/ollama]], [[entities/openclaw]], [[entities/ffmpeg]], [[entities/discord-bot]], [[concepts/clipping-pipeline]], [[concepts/highlight-detection]], [[concepts/vram-budget]], [[concepts/deployment]].

Pages created: [[entities/qwen25]], [[entities/dashboard]], [[concepts/segment-detection]], [[concepts/vision-enrichment]], [[concepts/clip-rendering]], [[concepts/context-management]], [[concepts/bugs-and-fixes]], [[concepts/open-questions]], [[sources/development-summary]], [[sources/fix-txt]].

Root `CLAUDE.md` created with vault-update prompt injection for agents working on the project.

## [2026-04-07] ingest | OpenClaw Stream Clipper — Detailed System Summary

Processed `OpenClaw_Stream_Clipper_Summary.md` (project root). Initial wiki bootstrap.

Pages created: [[overview]], [[sources/openclaw-stream-clipper-summary]], [[entities/openclaw]], [[entities/ollama]], [[entities/qwen3-vl]], [[entities/qwen35]], [[entities/faster-whisper]], [[entities/ffmpeg]], [[entities/discord-bot]], [[concepts/clipping-pipeline]], [[concepts/highlight-detection]], [[concepts/vram-budget]], [[concepts/deployment]].
