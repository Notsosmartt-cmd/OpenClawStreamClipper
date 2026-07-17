"""Per-VOD pipeline checkpoints — resume a crashed/stopped run mid-pipeline.

Owner req 2026-07-17: a batch that dies on VOD 4 mid-Stage-6 should not
restart VOD 4 from transcription — the detected moments (timestamps, judge
verdicts, enrichment) already existed and are worth real GPU-hours.

Design: after each expensive stage (3 segments, 4 moments, 5 judge+frames,
6 enrichment) the shared work dir's small artifacts (*.json / *.srt, plus the
frames/ dir from stage 5 on) are snapshotted to a per-VOD state dir:

    vods/.pipeline_state/<vod stem>/
        checkpoint.json     <- {stage, vod size, style, ctx fields, ...}
        work/…              <- the snapshot (restored INTO the work dir)

Resume contract (run_pipeline._execute_stages):
  * plain process  -> after Stage 1 (cheap discovery, always runs) a valid
    checkpoint restores the snapshot and stages <= its stage are SKIPPED.
  * --force        -> the checkpoint is deleted first; the VOD starts from 0.
    (--force already means "process even if in processed.log"; the owner's
    "force reprocess = start from 0" maps onto the same dashboard checkbox.)
  * clean VOD completion -> checkpoint cleared (no stale state).

Validation: the checkpoint is discarded (never trusted) when the VOD file
size, style, or type hint changed, when it's older than MAX_AGE_DAYS, or when
its stage is out of range. Everything here is failure-soft: a checkpoint
problem logs and falls back to a fresh run — never the other way around.

NOT snapshotted: audio.wav (only Stage 2 reads it — gigabytes for long VODs)
and rendered clips (they live in clips/ already; a resumed Stage 7 re-renders
its manifest, overwriting any partial file the crash left behind).
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

# Stages worth the snapshot cost (after: segments / moments / judge+frames /
# vision enrichment). Stage 7 re-runs wholesale on resume by design.
SAVE_AFTER = (3, 4, 5, 6)
MAX_AGE_DAYS = 14
# ctx fields later stages read that earlier stages populate — restored on
# resume so a skipped stage's side effects survive.
CTX_FIELDS = ("vod_duration", "chat_available", "chat_path")


def _state_root(ctx) -> Path:
    return ctx.paths.vods_dir / ".pipeline_state"


def _state_dir(ctx) -> Path:
    return _state_root(ctx) / Path(ctx.vod_basename or "unknown").stem


def _manifest(ctx) -> Path:
    return _state_dir(ctx) / "checkpoint.json"


def _vod_size(ctx) -> int:
    try:
        return ctx.vod_path.stat().st_size if ctx.vod_path else 0
    except OSError:
        return 0


def clear(ctx, log, reason: str = "") -> None:
    d = _state_dir(ctx)
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
        if reason:
            log.log(f"[checkpoint] cleared for {d.name} ({reason})")


def save(ctx, log, stage: int) -> None:
    """Snapshot the work dir after `stage` completed cleanly. Failure-soft."""
    try:
        d = _state_dir(ctx)
        work_snap = d / "work"
        tmp = d / "work.tmp"
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True, exist_ok=True)
        n = 0
        for f in ctx.paths.work_dir.iterdir():
            if f.is_file() and f.suffix in (".json", ".srt"):
                shutil.copy2(f, tmp / f.name)
                n += 1
        frames = ctx.paths.work_dir / "frames"
        if stage >= 5 and frames.is_dir():
            shutil.copytree(frames, tmp / "frames", dirs_exist_ok=True)
        # swap-in (rename is near-atomic; a crash mid-save leaves the OLD
        # snapshot intact or a .tmp that a later save/clear removes)
        shutil.rmtree(work_snap, ignore_errors=True)
        tmp.rename(work_snap)
        manifest = {
            "stage": stage,
            "vod": ctx.vod_basename,
            "vod_size": _vod_size(ctx),
            "style": ctx.style,
            "type_hint": ctx.type_hint,
            "run_stamp": getattr(ctx, "run_stamp", ""),
            "saved_at": time.time(),
            "ctx": {k: getattr(ctx, k, None) for k in CTX_FIELDS},
        }
        _manifest(ctx).write_text(json.dumps(manifest, indent=2),
                                  encoding="utf-8")
        log.log(f"[checkpoint] saved after Stage {stage} "
                f"({n} artifacts{' + frames' if stage >= 5 else ''})")
    except Exception as e:  # noqa: BLE001
        log.warn(f"[checkpoint] save after Stage {stage} failed ({e}) — "
                 "run continues, resume just won't include this stage")


def prepare(ctx, log) -> int:
    """Called right after Stage 1. Returns the stage to RESUME FROM (i.e.
    stages strictly below it are skipped): 2..7 on a valid checkpoint, else
    2's floor value 1 (meaning: run everything). Honors fresh/--force."""
    try:
        if getattr(ctx, "fresh", False):
            clear(ctx, log, "force reprocess — starting from 0")
            return 1
        mf = _manifest(ctx)
        if not mf.exists():
            return 1
        m = json.loads(mf.read_text(encoding="utf-8"))
        stage = int(m.get("stage") or 0)
        why = None
        if stage not in SAVE_AFTER:
            why = f"bad stage {stage}"
        elif m.get("vod_size") != _vod_size(ctx):
            why = "VOD file changed"
        elif (m.get("style") or "") != (ctx.style or ""):
            why = f"style changed ({m.get('style')} -> {ctx.style})"
        elif (m.get("type_hint") or "") != (ctx.type_hint or ""):
            why = "type hint changed"
        elif time.time() - float(m.get("saved_at") or 0) > MAX_AGE_DAYS * 86400:
            why = f"older than {MAX_AGE_DAYS} days"
        if why:
            log.warn(f"[checkpoint] discarding saved state ({why}) — fresh run")
            clear(ctx, log)
            return 1
        work_snap = _state_dir(ctx) / "work"
        if not work_snap.is_dir():
            log.warn("[checkpoint] manifest without snapshot — fresh run")
            clear(ctx, log)
            return 1
        n = 0
        for f in work_snap.iterdir():
            if f.is_file():
                shutil.copy2(f, ctx.paths.work_dir / f.name)
                n += 1
        snap_frames = work_snap / "frames"
        if snap_frames.is_dir():
            shutil.copytree(snap_frames, ctx.paths.work_dir / "frames",
                            dirs_exist_ok=True)
        for k, v in (m.get("ctx") or {}).items():
            if k in CTX_FIELDS and v is not None:
                setattr(ctx, k, v)
        # keep the clips grouped under the ORIGINAL session in effects_log /
        # the Reference Lab: a resumed VOD reuses its first run's stamp
        if m.get("run_stamp"):
            ctx.run_stamp = m["run_stamp"]
        age_min = int((time.time() - float(m.get("saved_at") or 0)) / 60)
        log.line(f"[checkpoint] RESUMING '{ctx.vod_basename}' after completed "
                 f"Stage {stage} (saved {age_min} min ago, {n} artifacts "
                 f"restored) — stages 2-{stage} skipped. Force reprocess "
                 f"starts from 0.")
        return stage + 1
    except Exception as e:  # noqa: BLE001
        log.warn(f"[checkpoint] resume check failed ({e}) — fresh run")
        return 1
