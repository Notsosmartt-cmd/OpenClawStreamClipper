#!/usr/bin/env python3
"""Stage 5 — Frame Extraction (6 payoff frames + A2 setup frames).

Port of stage5_frames.sh, with a 2026-06-04 parallel-dispatch optimization.

The original implementation invoked ffmpeg once per (moment, frame_offset)
pair = ~6 frames per moment × 80-120 moments per VOD ≈ **480-720 ffmpeg
invocations**, each with ~200 ms startup + seek + JPEG-encode cost. That's
~2-3 minutes of pure subprocess overhead on a long run.

We dispatch the same per-frame calls via a ``ThreadPoolExecutor`` so
multiple ffmpeg processes run concurrently. Each invocation is short
(~150-300 ms total) and subprocess work releases the GIL while ffmpeg
runs, so threads are the right tool here (ProcessPool would also work but
has higher per-task overhead).

Speedup on the i9-13900K: 4-8× wall-clock for the extract phase. Output
files and their contents are identical to the serial path — same ffmpeg
command, same frame offsets, same scale/quality.
"""
from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Tuple

from pipeline import common

# label:offset around the moment peak T (payoff window per ClippingResearch).
FRAME_OFFSETS = [("tminus2", -2), ("t0", 0), ("tplus1", 1),
                 ("tplus2", 2), ("tplus3", 3), ("tplus5", 5)]

# Tier-3 A2 setup-frame offsets, applied around ``setup_time`` (NOT around T).
A2_FRAME_OFFSETS = [("setupminus1", -1), ("setupplus1", 1)]

# Default worker count for the extract pool. Each ffmpeg invocation uses
# only a thread or two internally (single-frame decode + JPEG encode is
# fast), so we can run ~8 concurrently on a 24-core i9-13900K without
# starving the rest of the host. Tunable via ``STAGE5_WORKERS`` env var.
_DEFAULT_WORKERS = 8


# C1 vision image diet (plan-speed-wave3, 2026-07-14): these frames exist ONLY
# for the VLM (Stage 5.5 judge + Stage 6 enrichment) — renders cut from the VOD
# directly — so their resolution is pure image-token budget. 960:540 ≈ ~650
# image tokens/frame; the 640:360 default ≈ ~290 (≈2.3× less prefill per vision
# call) while staying readable for on-screen-text reads (the R0 lesson: the VLM
# does real OCR off these). CLIP_FRAME_SCALE=960:540 restores the old size.
_FRAME_SCALE = os.environ.get("CLIP_FRAME_SCALE", "640:360").strip() or "640:360"


def _extract(ctx, frame_t: int, out_name: str) -> None:
    common.run_ffmpeg([
        "ffmpeg", "-nostdin", "-y", "-ss", str(max(0, frame_t)),
        "-i", str(ctx.vod_path), "-frames:v", "1",
        "-vf", f"scale={_FRAME_SCALE}", "-q:v", "2",
        str(ctx.paths.work(out_name)),
    ])


def _resolve_workers() -> int:
    """``STAGE5_WORKERS`` env override → default. Set to 1 to force serial."""
    env = os.environ.get("STAGE5_WORKERS", "").strip()
    if env:
        try:
            v = int(env)
            if v > 0:
                return v
        except ValueError:
            pass
    return _DEFAULT_WORKERS


def _collect_payoff_tasks(moments) -> List[Tuple[int, int, str]]:
    """Build the full (peak_T, frame_t, out_name) list for the payoff window
    across all moments. Pre-computed so we can dispatch them as a flat
    parallel batch instead of nested per-moment loops."""
    tasks: List[Tuple[int, int, str]] = []
    for m in moments:
        t = int(m["timestamp"])
        for label, off in FRAME_OFFSETS:
            tasks.append((t, t + off, f"frames_{t}_{label}.jpg"))
    return tasks


def _collect_setup_tasks(moments) -> List[Tuple[int, int, str]]:
    """Build the (peak_T, frame_t, out_name) list for Tier-3 A2 setup frames.
    Only moments with a ``setup_time`` get setup frames; others are skipped
    silently (same as the serial version)."""
    tasks: List[Tuple[int, int, str]] = []
    for m in moments:
        setup = m.get("setup_time")
        if setup is None:
            continue
        t = int(m["timestamp"])
        for label, off in A2_FRAME_OFFSETS:
            tasks.append((t, int(setup) + off, f"frames_{t}_{label}.jpg"))
    return tasks


def _dispatch(ctx, tasks: List[Tuple[int, int, str]], n_workers: int, kind: str) -> int:
    """Run ``tasks`` through ``n_workers`` ffmpeg invocations concurrently.

    Returns the count of *distinct moments* whose frames were extracted —
    useful for the log line that summarises the stage. Per-task failures
    propagate as exceptions from ``common.run_ffmpeg`` and bubble up to
    the caller (same behavior as the serial path)."""
    if not tasks:
        return 0
    log = ctx.log
    if n_workers <= 1 or len(tasks) <= 2:
        for _, frame_t, out_name in tasks:
            _extract(ctx, frame_t, out_name)
    else:
        log.log(f"  [stage5] {kind}: dispatching {len(tasks)} ffmpeg calls "
                f"across {n_workers} workers...")
        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            futs = {pool.submit(_extract, ctx, frame_t, out_name): (frame_t, out_name)
                    for _, frame_t, out_name in tasks}
            for fut in as_completed(futs):
                # Surface ffmpeg failures (same as serial loop's run_ffmpeg).
                fut.result()
    # Count distinct peak_Ts so the caller can log "extracted frames for N moments".
    return len({t for t, _, _ in tasks})


def _s45_text_judge(ctx, log, moments):
    """S4.5 batched text judge (plan-s45-text-judge-2026-07, CLIP_S45_JUDGE=1).

    Runs at the START of the vision phase: does the 9B→35B swap that stage 6
    would do anyway (the boundary already exists — judgment rides it free),
    judges every Pass-B candidate comparatively from evidence packets, and
    culls BEFORE frame extraction so S5/S5.5 only pay for survivors.
    Failure-soft: any error returns the input moments unchanged.
    BUG-74 law: the judge model is passed EXPLICITLY (ctx.vision_model_stage6),
    never resolved from env fallbacks."""
    import sys
    import time
    from pathlib import Path
    lib = Path(__file__).resolve().parents[2] / "lib"
    if str(lib) not in sys.path:
        sys.path.insert(0, str(lib))
    try:
        import evidence_packets
        import s45_text_judge
    except Exception as e:  # noqa: BLE001
        log.warn(f"[s45] modules unavailable ({type(e).__name__}: {e}) — judge skipped")
        return moments

    common.set_stage(log, "Stage 4.5/8 — Text Judge (loading vision model)")
    log.log(f"=== Stage 4.5/8 — Text Judge ({len(moments)} candidates) ===")
    try:
        if ctx.text_model_passb != ctx.vision_model_stage6:
            common.unload_model(log, ctx.llm_url, ctx.text_model_passb)
            common.load_model(log, ctx.llm_url, ctx.vision_model_stage6, ctx.context_length)
        ctx.s45_swapped = True  # stage 6 skips its own swap

        t0 = time.time()
        events_path = ctx.paths.work("audio_events.json")
        packets = evidence_packets.build_packets(
            moments, ctx.paths.transcript_json,
            audio_events_path=events_path if events_path.exists() else None)
        t_packets = time.time() - t0
        result = s45_text_judge.judge_moments(
            moments, packets, model=ctx.vision_model_stage6, url=ctx.llm_url,
            log=lambda m: log.log(str(m)))
        survivors = result["survivors"]
        t_judge = time.time() - t0 - t_packets
        elapsed = time.time() - t0
        # Section-timing metric (owner 2026-07-15: measure sections, never
        # full runs) — packets vs judge split, also persisted in the report.
        log.log(f"[s45] judged {len(moments)} candidates in {elapsed:.0f}s "
                f"(packets {t_packets:.1f}s, judge {t_judge:.0f}s): "
                f"{len(survivors)} kept, {len(result['killed'])} culled, "
                f"{result['groups_failed']} group(s) unjudged")
        result["timing"] = {"packets_s": round(t_packets, 1),
                            "judge_s": round(t_judge, 1),
                            "total_s": round(elapsed, 1)}

        # Persist the decision report (survives temp cleanup — J6's diff input).
        try:
            diag = Path(__file__).resolve().parents[3] / "clips" / ".diagnostics"
            diag.mkdir(parents=True, exist_ok=True)
            stamp = os.environ.get("CLIP_RUN_STAMP", time.strftime("%Y%m%d_%H%M%S"))
            rpt = diag / f"s45_judge_{stamp}_{ctx.vod_basename}.json"
            rpt.write_text(json.dumps({
                "vod": ctx.vod_basename, "model": ctx.vision_model_stage6,
                "candidates": len(moments), "kept": len(survivors),
                "timing": result.get("timing"),
                "killed": result["killed"], "decisions": result["decisions"],
            }, indent=2), encoding="utf-8")
            log.log(f"[s45] report: {rpt.name}")
        except Exception as e:  # noqa: BLE001
            log.warn(f"[s45] report write failed ({type(e).__name__}) — continuing")

        ctx.paths.hype_moments.write_text(json.dumps(survivors, indent=2),
                                          encoding="utf-8")
        return survivors
    except Exception as e:  # noqa: BLE001
        log.warn(f"[s45] judge pass failed ({type(e).__name__}: {e}) — "
                 f"continuing with all {len(moments)} candidates")
        return moments


def run(ctx) -> None:
    log = ctx.log
    p = ctx.paths

    moments = json.loads(p.hype_moments.read_text(encoding="utf-8"))

    # S4.5 batched text judge (default OFF; CLIP_S45_JUDGE=1 enables) — must
    # run BEFORE frame extraction so frames are only paid for survivors.
    if os.environ.get("CLIP_S45_JUDGE", "0").strip() == "1" and moments:
        moments = _s45_text_judge(ctx, log, moments)

    common.set_stage(log, "Stage 5/8 — Frame Extraction")
    log.log("=== Stage 5/8 — Frame Extraction ===")

    n_workers = _resolve_workers()

    # Payoff window: T-2 .. T+5 frames for every moment.
    payoff_tasks = _collect_payoff_tasks(moments)
    log.log(f"Extracting payoff-window frames for {len(moments)} moments "
            f"({len(payoff_tasks)} frames, {n_workers} workers, T-2..T+5)...")
    n_payoff = _dispatch(ctx, payoff_tasks, n_workers, "payoff")
    log.log(f"Extracted payoff-window frames for {n_payoff} moments")

    # Tier-3 A2: setup frames for callback / arc moments only.
    setup_tasks = _collect_setup_tasks(moments)
    log.log(f"Tier-3 A2: extracting setup frames "
            f"({len(setup_tasks)} frames across {len({t for t,_,_ in setup_tasks})} "
            f"callback/arc moments)...")
    n_setup = _dispatch(ctx, setup_tasks, n_workers, "setup")
    log.log(f"A2 extracted setup frames for {n_setup} callback/arc moments")
