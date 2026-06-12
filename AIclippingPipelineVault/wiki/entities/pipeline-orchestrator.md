---
title: "Pipeline Orchestrator (scripts/run_pipeline.py)"
type: entity
tags: [orchestrator, pipeline, bare-metal, python, entrypoint, cli, infrastructure]
sources: 0
updated: 2026-06-12
---

# `scripts/run_pipeline.py`

The native **pipeline orchestrator** — the post-[[concepts/bare-metal-windows]]-port entry point that drives the 8 stages on Windows with no Docker and no bash. It is a Python port of the legacy `scripts/clip-pipeline.sh`, keeping the same CLI surface so the [[entities/dashboard]] and the Discord/OpenClaw skill call it the same way the shell script was called.

It is a **thin driver**: it builds run configuration, wires logging, and then iterates the 8 stage modules (`scripts/pipeline/stages/stage{1..8}.py`), each of which shells out to the heavy `scripts/lib/stages/*.py` workers. See [[concepts/clipping-pipeline]] for the stage-by-stage detail.

---

## Role

- **Config assembly.** Each setting resolves by precedence `CLIP_* env var > config/models.json > built-in default` (the `Ctx.pick()` helper). The dashboard sets the env vars; a bare CLI run falls back to `models.json` so it still works standalone. This is where `text_model` / `vision_model` / `text_model_passb` / `vision_model_stage6`, `context_length`, the originality toggles (`CLIP_ORIGINALITY`, `CLIP_FRAMING`, `CLIP_STITCH`, `CLIP_NARRATIVE`, `CLIP_ARC_STITCH`, `CLIP_TTS_VO`, `CLIP_MUSIC_BED`, `CLIP_CAMERA_PAN`, `CLIP_STYLE_PROFILES`, …) get read into the `Ctx` run object.
- **Model verification.** Before any non-`--list` run it calls `common.verify_models()`, which hits LM Studio's `GET /v1/models` and **aborts (exit 2)** if any configured model ID is missing — see [[entities/lm-studio]].
- **Stage execution.** `_execute_stages()` runs model verification then imports and runs `pipeline.stages.stage1 … stage8` in order for one VOD, catching `PipelineExit` (an intentional early stop carrying its own exit code + JSON summary) and timing the VOD session.
- **Logging lifecycle.** Attaches a `common.Logger` writing to both the ephemeral SSE log and a persistent timestamped log, writes a PID marker, and runs `common.cleanup()` in a `finally`. See [[concepts/observability]].
- **Signal handling.** Installs `SIGINT`/`SIGTERM` handlers that raise `PipelineExit(130, '{"status":"aborted","clips":0}')` so an aborted run still emits a clean status line.

The `Ctx.child_env()` method exports the resolved config back out as `CLIP_*` (and bare `TEXT_MODEL` / `VISION_MODEL` / `LLM_URL`) env vars for the subprocessed lib modules, since each stage worker is its own process.

---

## CLI flags & semantics

| Flag | Meaning |
|---|---|
| `--style <name>` | Style/preset hint (default `auto`). Forwarded as `CLIP_STYLE`. |
| `--vod <name>` | Single target VOD name/stem. |
| `--vods a,b,c` | **Comma-separated multi-select** (dashboard). Processes exactly these, sequentially, **respecting the caller's `--force`**. |
| `--all` | Process **every unprocessed** VOD sequentially. Discovery via `stage1._find_vods()` minus anything in the processed log (unless `--force`). Each VOD in an `--all` batch is reprocessed fresh (`batch_force=True`). |
| `--type <hint>` | Stream-type hint, forwarded as `STREAM_TYPE_HINT`. |
| `--force` | Re-transcribe / reprocess even if already processed. For `--vods` it is honored per the caller's choice; for `--all` it controls whether already-processed VODs are skipped during discovery. |
| `--list` | List mode — skips model verification, the VOD session timer, and cleanup-heavy paths. |

Unknown args are tolerated (`parse_known_args`), mirroring the old bash `*) shift ;;` behavior.

### Batch (`--all` / `--vods`) semantics

When `--all` or `--vods` is set (and not `--list`), `main()` builds a `targets` list, then for each target constructs a **fresh `Ctx`** (so per-VOD runtime state doesn't leak) and runs `_execute_stages()`. Between iterations (`i > 0`) it calls `_reset_work_artifacts()`, which clears per-VOD work files from the work dir while **keeping** the streamed log, stage marker, stages log, PID file, and done file intact. The batch exit code is the last non-zero stage exit code seen.

---

## Persistent-log slug naming

Each run writes a persistent, timestamped log alongside the ephemeral SSE log so it survives work-dir cleanup. The filename is `<stamp>_<slug>.log` under `paths.persistent_log_dir`, where:

- `stamp` = UTC `%Y%m%d_%H%M%S`.
- `slug` is derived from the first available of: `--vod` target, the **first** entry of `--vods`, the literal `"all"` (when `--all`), else `"unknown"`. It is taken as the `Path(...).stem`, filtered to `[A-Za-z0-9_-]`, and truncated to 40 chars (falling back to `"unknown"` if empty).

Example: a run of `--vod 20260424_2xRaKai` yields a log like `20260606_071210_20260424_2xRaKai.log` — the same naming the validation-run names in [[concepts/clip-quality-remediation-2026-06]] follow.

---

## Related

- [[concepts/clipping-pipeline]] — the 8 stages this orchestrator drives
- [[concepts/bare-metal-windows]] — the migration that made this the live entrypoint (Docker/`clip-pipeline.sh` now legacy)
- [[entities/dashboard]] — sets the `CLIP_*` env vars and invokes this script
- [[concepts/observability]] — the persistent/ephemeral logs, PID marker, and stage markers this orchestrator manages
- [[entities/lm-studio]] — model verification + inference backend
