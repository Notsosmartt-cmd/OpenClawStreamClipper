---
title: "Bare-Metal Windows (native, no Docker)"
type: concept
tags: [deployment, windows, bare-metal, native, python, orchestrator, venv, cuda, hub]
sources: 1
updated: 2026-06-04
---

# Bare-Metal Windows (native, no Docker)

As of 2026-06-04 the project can run **fully natively on Windows** with no Docker
container and no WSL. The bash pipeline was ported to a pure-Python orchestrator;
the Flask dashboard and the OpenClaw/Discord gateway run as native processes
talking to LM Studio on `localhost:1234`.

> [!note] Why
> Docker was the source of most of the codebase's environmental complexity — the
> `docker exec` bridge, `host.docker.internal` rewriting, dual-mode routes, and
> the WSL2 file-boundary tax. The user's separate `VideoToText-main` repo proved
> PyTorch + CUDA transcription works natively on the RTX 5060 Ti, so the Linux
> container was no longer load-bearing. See [[concepts/deployment]] for the
> (legacy) Docker path, retained under `legacy/`.

---

## Architecture

```
Windows host (bare metal)
├── LM Studio (native)                  → http://localhost:1234
├── .venv (Python 3.12)
│   ├── scripts/run_pipeline.py         ← orchestrator (replaces clip-pipeline.sh)
│   │   └── subprocess → scripts/lib/**.py (reused unchanged, via sys.executable)
│   ├── dashboard/app.py (Flask :5001)  ← native run-mode
│   └── faster-whisper/WhisperX + torch(CUDA) + ffmpeg(PATH)
└── Node + openclaw (native) → Discord gateway → exec → clip.cmd → run_pipeline.py
```

The heavy Python modules in `scripts/lib/` are **reused as-is**. The port only
rewrote the bash *glue* (control flow, ffmpeg calls, logging, LM Studio REST,
file caching) into Python and invokes the existing modules via
`subprocess.run([sys.executable, module, ...])` — which also makes the
`python3`-vs-`python` problem moot.

---

## New / changed files

| File | Role |
|---|---|
| `scripts/run_pipeline.py` | Orchestrator. Arg parse (`--style/--vod/--type/--list/--force/--all`), config (env → `models.json` → defaults), logging tee, pid/done markers, signal handling, 8-stage dispatch, cleanup. Replaces `clip-pipeline.sh`. |
| `scripts/pipeline/common.py` | Helpers: `Logger` (tee console + ephemeral + persistent log), `set_stage`, `unload_model`/`load_model`/`verify_models` (LM Studio REST via urllib), `run_module` (subprocess a lib module, tee output), `cleanup`, `PipelineExit`. Replaces `pipeline_common.sh`. |
| `scripts/pipeline/stages/stage{1..8}.py` | One module per stage, ported 1:1 from `scripts/stages/stage*.sh`. Each exposes `run(ctx)`. |
| `scripts/lib/paths.py` | **Single source of truth** for all paths. Maps `/tmp/clipper`→`%LOCALAPPDATA%\OpenClawClipper\work`, `/root/VODs`→`<repo>\vods`, `/root/.openclaw`→`<repo>\config`, etc. `child_env()` builds the env (incl. nvidia DLL dirs on PATH, PYTHONPATH, per-feature config vars) for subprocessed lib modules. |
| `scripts/lib/cuda_bootstrap.py` | `os.add_dll_directory()` for the venv's `nvidia/*/bin` so CTranslate2 finds cuDNN/cuBLAS on Windows. |
| `scripts/validate_gpu.py` | Phase-0 GPU gate: confirms faster-whisper loads + runs inference on the GPU. |
| `clip.cmd` | Native launcher wrapper — runs the venv python on `run_pipeline.py`. Used by the OpenClaw skill's `exec`. |
| `start.ps1` | Startup script (replaces `entrypoint.sh`): links `~/.openclaw`→`config\`, optional `.env` token inject, waits for LM Studio, starts dashboard + openclaw gateway. |
| `requirements-windows.txt` | Consolidated native deps. |
| `legacy/` | Retired `Dockerfile`, `docker-compose.yml`, `entrypoint*.sh`, `clip-pipeline.sh`, `pipeline_common.sh`, `stages/*.sh` (kept for reference / rollback). |

---

## Path parameterization (Phase 1)

The reused `scripts/lib/**.py` modules hardcoded `/tmp/clipper/...` and
`/root/.openclaw/X.json`. They now read env vars (set by the orchestrator's
`child_env()`), each with the old Linux path as fallback:

- `TEMP_DIR = os.environ.get("CLIP_WORK_DIR", "/tmp/clipper")` in ~9 stage modules
- config loaders honor `CLIP_RUBRIC_CONFIG` / `CLIP_PATTERNS_CONFIG` /
  `CLIP_DENYLIST_PATH` / `CLIP_GROUNDING_CONFIG` / … pointed at `<repo>\config`
- `WHISPER_MODEL_DIR`, `PIPER_VOICE_DIR`, `CALLBACKS_CACHE_DIR`, `LIB_DIR` set too

No edits to the modules' *logic* — only where they resolve paths/config.

---

## The venv (Phase 0)

```
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install torch==2.8.0+cu128 torchaudio==2.8.0+cu128 ^
    torchvision==0.23.0+cu128 --index-url https://download.pytorch.org/whl/cu128
.venv\Scripts\python.exe -m pip install -r requirements-windows.txt
```

> [!warning] Install torch AFTER whisperx, or pin cu128
> `whisperx`/`pyannote` depend on `torch==2.8.0` and pip will pull the **CPU**
> build from PyPI, clobbering CUDA. Fix: (re)install `torch==2.8.0+cu128` from
> the cu128 index. Verify with `python -c "import torch; print(torch.cuda.is_available())"` → `True`.

> [!note] faster-whisper GPU on Blackwell (RTX 50-series)
> CTranslate2 4.7.2 + `nvidia-cudnn-cu12` 9.23 + `nvidia-cublas-cu12` drives the
> RTX 5060 Ti (sm_120) fine — validated by `scripts/validate_gpu.py`. CT2 is
> independent of torch (its CUDA path worked before torch was even installed).
> On Windows the nvidia `*/bin` dirs must be on the DLL search path — handled by
> `cuda_bootstrap.py` (add_dll_directory) and `paths.child_env()` (PATH).

---

## Dashboard native mode (Phase 3)

`dashboard/pipeline_runner.py::use_docker_exec()` now defaults to **False**
(native); set `CLIP_USE_DOCKER=1` to drive a container again. In native mode:

- `spawn_pipeline` runs `[sys.executable, run_pipeline.py, ...]` directly (the
  pre-existing non-docker `Popen` branch, which already handled `os.name=="nt"`).
- `dashboard/_state.py` imports `paths.py` so `TEMP_DIR` == the orchestrator's
  work dir — the dashboard reads the exact stage/log/marker files the pipeline
  writes (live SSE + 8-stage monitor work unchanged).
- `pipeline_routes.py` builds `clip.cmd`-equivalent python commands; `--all`
  maps to `run_pipeline.py --all`.
- The whole `docker exec` / `DetachedDockerPipeline` machinery is dead in native
  mode (kept for the opt-in `CLIP_USE_DOCKER` path).

Dashboard port is **5001** (`app.py`).

---

## OpenClaw / Discord native (Phase 4)

- `npm install -g openclaw@latest` (Node 22+).
- `config/openclaw.json`: `baseUrl` → `http://localhost:1234/v1`; `workspace` →
  `G:/OpenClawStreamClipper/workspace`.
- `workspace/AGENTS.md` + `workspace/skills/stream-clipper/SKILL.md`: exec
  commands now call `clip.cmd ...` (was `bash /root/scripts/clip-pipeline.sh`);
  `requires.bins` `python3`→`python`.
- `start.ps1` junctions `~/.openclaw` → `config\` so openclaw discovers its
  config (mirrors the Docker `./config:/root/.openclaw` mount), then launches
  the gateway from the repo root so `clip.cmd` resolves.

---

## Running it

```
powershell -ExecutionPolicy Bypass -File start.ps1
```
Or manually: `clip.cmd --vod <name> --style auto` (CLI), or drive from the
dashboard at `http://localhost:5001`.

---

## Model load/unload (lms CLI)

`scripts/pipeline/common.py` manages VRAM by unloading/loading models between
stages. LM Studio's REST unload path is version-dependent — **0.4.14 returns 404
for `/api/v1/models/unload`**, which strands models in VRAM and lets two pile up
on the 16 GB GPU (spilling to system RAM → slow). The pipeline therefore prefers
the bundled **`lms` CLI** (`lms ps` / `lms load -c <ctx> -y --ttl <s>` /
`lms unload <id>`) and falls back to REST only when `lms` isn't found. `lms` is
located via PATH or `~/.cache/lm-studio/bin/lms.exe`. A default idle TTL
(`CLIP_MODEL_TTL`, 3600 s) auto-evicts abandoned models; `load_model` also skips
pre-load when `lms ps` shows the model already resident. Belt-and-suspenders:
enable LM Studio's own "Idle TTL and Auto-Evict" (or JIT max-loaded-models = 1).

## Downloading models (Whisper / Piper)

Whisper weights live in `models\whisper\` (gitignored; HuggingFace layout) and
auto-download on first transcription. To pre-fetch, use the `get-models.cmd`
wrapper around `scripts/lib/fetch_assets.py`:

- `get-models.cmd available` — list downloadable Whisper models + sizes
- `get-models.cmd whisper large-v3` — download the default
- `get-models.cmd status` — show what's cached
- `get-models.cmd piper en_US-amy-low` — a Piper TTS voice (Wave-D voiceover)

`fetch_assets.py` calls `faster_whisper.WhisperModel(..., download_root=models\whisper)`,
so the same CTranslate2 cache serves both Stage 2 and Stage 7. The project default
is **large-v3** (`config/models.json::whisper_model`); see [[entities/faster-whisper]]
for the full model table and speed/accuracy/VRAM tradeoffs.

## Logging & diagnostics (`scripts/logtool.py`)

Every run writes a persistent log to `clips/.pipeline_logs/<timestamp>_<vod>.log`
(plus the ephemeral work-dir `pipeline.log` the dashboard streams via SSE).
`logtool` triages them — run via the venv:
`.venv\Scripts\python.exe scripts\logtool.py <cmd>`.

| Command | Purpose |
|---|---|
| `doctor` | Environment preflight — venv deps (torch/CUDA, CTranslate2 cuda devices, faster-whisper, whisperx, sentence-transformers, flask, opencv, librosa…), ffmpeg/ffprobe, cuDNN DLL dirs, LM Studio reachability + `lms ps` loaded + configured-model availability, resolved paths, disk. Exit non-zero on any failure. |
| `list [-n N]` | Recent runs: time, VOD, stage reached, exit code, crit/err/warn counts, clips. |
| `errors [RUN] [--all] [-C K]` | Scan a run's log for errors, **classified** (CRIT/ERR/WARN) and **attributed to the stage** they occurred in; benign noise (the 404 unload, JIT, grounding nulls, syntax warnings) is filtered. RUN = `list` index, filename substring, or path. `--all-runs` scans the last N. |
| `show RUN [--tail N]` | Print a run's full log (or last N lines). |
| `tail [-n N] [--follow]` | Read / follow the live current-run log. |

> [!warning] torchcodec breaks sentence-transformers on torch 2.8
> `torchcodec` 0.7+ requires torch 2.9; its DLLs fail to load against the pinned
> torch 2.8.0+cu128 and break `import sentence_transformers` (→ Pass B+ callbacks
> + Tier-4 MMR diversity silently disabled). **`logtool doctor` catches this.**
> Fix: `pip uninstall -y torchcodec` (the pipeline uses ffmpeg directly, not
> torchcodec). Noted in `requirements-windows.txt`.

## Related
- [[concepts/deployment]] — legacy Docker setup (retained under `legacy/`)
- [[concepts/clipping-pipeline]] — the 8 stages (now Python modules)
- [[entities/faster-whisper]] — GPU transcription backend
- [[overview]] — top-level architecture
