#!/usr/bin/env python3
"""Stage 3 — Segment Detection. Port of stage3_segments.sh."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

from pipeline import common

# C4 (Speed Wave 2, plan-serving-stack-2026-07): bump when stage3_segments.py logic
# changes in a way that alters segments for the same transcript+config (forces a miss).
_SEGCACHE_VERSION = 1


def _truthy(v: str) -> bool:
    return str(v).strip().lower() not in ("0", "false", "no", "off", "")


def _seg_config_key(ctx) -> str:
    """sha1 over the transcript bytes + EVERY input stage3_segments.py consumes.
    Any input not in this key = a silent staleness bug, so this mirrors the child's
    env reads (with the child's own defaults) exactly — see stage3_segments.py."""
    p = ctx.paths
    try:
        transcript_bytes = p.transcript_json.read_bytes()
    except OSError:
        return ""  # no transcript -> caller must not cache
    cfg = {
        "v": _SEGCACHE_VERSION,
        "text_model": ctx.text_model,
        "context_length": ctx.context_length,
        "stream_type_hint": ctx.type_hint or "",
        # env inputs read by stage3_segments.py, with its exact defaults:
        "chunk": os.environ.get("CLIP_SEGMENT_CHUNK", "600") or "600",
        "overlap": os.environ.get("CLIP_SEGMENT_OVERLAP", "0") or "0",
        "votes": os.environ.get("CLIP_SEGMENT_VOTES", "1") or "1",
        "smooth": _truthy(os.environ.get("CLIP_SEGMENT_SMOOTH", "1")),
        "smooth_below": os.environ.get("CLIP_SEGMENT_SMOOTH_BELOW", "0.67") or "0.67",
    }
    h = hashlib.sha1()
    h.update(transcript_bytes)
    h.update(json.dumps(cfg, sort_keys=True).encode("utf-8"))
    return h.hexdigest()[:12]


def _segcache_path(ctx, key: str) -> Path:
    stem = Path(ctx.vod_basename).stem
    return ctx.paths.transcriptions_dir / f"{stem}.segcache.{key}.json"


def run(ctx) -> None:
    log = ctx.log
    p = ctx.paths
    common.set_stage(log, "Stage 3/8 — Segment Detection")
    log.log("=== Stage 3/8 — Segment Detection ===")

    segments_out = p.work("segments.json")
    profile_out = p.work("stream_profile.json")

    # C4: segment cache — DEFAULT ON (promoted 2026-07-09 after live validation: real
    # Stage-3 miss 100 s -> hit 3.7 s, byte-identical restore, model reload skipped,
    # --force bypasses). Stage 3 is LLM-VOTED, so a fresh run re-rolls segments while a
    # cache hit REPLAYS the prior draw — same semantics as the transcript cache, and
    # --force (every --all batch) always re-rolls. Kill switch: CLIP_SEGMENT_CACHE=0.
    _cache_on = _truthy(os.environ.get("CLIP_SEGMENT_CACHE", "1"))
    key = _seg_config_key(ctx) if _cache_on else ""
    cache_file = _segcache_path(ctx, key) if key else None

    # CLIP_REUSE_SEGMENTS (2026-07-18): dev/harness flag — reuse cached segments
    # EVEN under --force, mirroring CLIP_REUSE_TRANSCRIPT in stage 2. Stage 3 is
    # LLM-VOTED, so a forced re-roll yields a DIFFERENT segment set each run; a
    # finder A/B (two models, same VOD) is only valid if both arms see the
    # IDENTICAL segments. Default off = strict force-re-roll, unchanged.
    _reuse_segments = _truthy(os.environ.get("CLIP_REUSE_SEGMENTS", "0"))

    if cache_file and cache_file.exists() and (not ctx.force or _reuse_segments):
        try:
            payload = json.loads(cache_file.read_text(encoding="utf-8"))
            segs, prof = payload["segments"], payload["stream_profile"]
            segments_out.write_text(json.dumps(segs, indent=2), encoding="utf-8")
            profile_out.write_text(json.dumps(prof, indent=2), encoding="utf-8")
            log.log(f"C4: reusing cached segments ({cache_file.name}, {len(segs)} segs) "
                    "— skipping LLM segment detection + model reload.")
            log.log("Segment detection complete (cached)")
            return
        except Exception as e:  # noqa: BLE001 - corrupt/partial cache -> regenerate
            log.warn(f"C4: segment cache unreadable ({e}) — regenerating.")

    # Miss (or cache off / --force): load the text model fresh and detect.
    # After Stage 2 (Whisper) all models are unloaded UNLESS the C3 cached-transcript
    # path kept the 35B resident — in which case load_model() is a no-op.
    common.load_model(log, ctx.llm_url, ctx.text_model, ctx.context_length)
    common.run_module(log, "stages/stage3_segments.py", [], env=ctx.child_env(), check=True)

    if cache_file:
        try:
            payload = {
                "segments": json.loads(segments_out.read_text(encoding="utf-8")),
                "stream_profile": json.loads(profile_out.read_text(encoding="utf-8")),
            }
            cache_file.write_text(json.dumps(payload), encoding="utf-8")
            log.log(f"C4: cached segments to {cache_file.name}")
        except Exception as e:  # noqa: BLE001 - never fail the stage over a cache write
            log.warn(f"C4: segment cache write failed ({e}) — continuing.")

    log.log("Segment detection complete")
