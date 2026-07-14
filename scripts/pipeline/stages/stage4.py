#!/usr/bin/env python3
"""Stage 4 — Moment Detection (Pass A/B/C + Pass D + 4.5 groups).
Port of stage4_moments.sh."""
from __future__ import annotations

import json
import os

from pipeline import common


def run(ctx) -> None:
    log = ctx.log
    p = ctx.paths
    env = ctx.child_env()
    common.set_stage(log, "Stage 4/8 — Moment Detection")
    log.log(f"=== Stage 4/8 — Moment Detection (style: {ctx.style}) ===")

    # Phase 5.1: swap to the Pass-B text model when it differs from Stage 3's.
    # B2 (plan-speed-wave3): on a dual-vendor rig the Pass-B model loads on the
    # NVIDIA-only CUDA lane (measured 1.8× per call over the same model on the
    # Vulkan split; 6.4× over the unified 35B). hw_profile keeps this INERT on
    # cpu-only / nvidia-only / amd-only installs; CLIP_PASSB_RUNTIME=off|cuda
    # overrides. The lane caps context at CLIP_PASSB_CUDA_CTX (default 16384 —
    # the documented Pass-B safe floor) so the KV cache fits the 16 GB card.
    lane = common.passb_lane(ctx)
    _runtime = "cuda" if lane.get("active") else None
    if _runtime:
        log.log(f"[B2] Pass-B CUDA lane ACTIVE — {lane.get('reason')}")
    elif lane.get("reason"):
        log.log(f"[B2] Pass-B CUDA lane inactive — {lane.get('reason')}")
    if ctx.text_model_passb != ctx.text_model or _runtime:
        log.log(f"Phase 5.1: swapping text model {ctx.text_model} -> {ctx.text_model_passb}"
                + (" (CUDA lane)" if _runtime else ""))
        common.unload_model(log, ctx.llm_url, ctx.text_model)
        _ctx_len = ctx.context_length
        if _runtime:
            # 32768 (not 16384) since the Raud finding: the ctx is a shared pool
            # across concurrent slots — 2 workers × (~10k prompt + ~5k gen) needs
            # ~30k. KV for the 9B at 32k is tiny (lms estimate: 6.1 GiB total).
            try:
                _ctx_len = int(os.environ.get("CLIP_PASSB_CUDA_CTX", "32768") or 32768)
            except ValueError:
                _ctx_len = 32768
        common.load_model(log, ctx.llm_url, ctx.text_model_passb, _ctx_len, runtime=_runtime)

    # BUG 67 fail-fast guard: one tiny probe of the (now-loaded) Pass-B model. Aborts in
    # ~1 s if the model ignores no-think (permanent reasoning -> would fail every chunk).
    common.preflight_thinking(log, ctx.llm_url, ctx.text_model_passb)

    # D1 REVERTED to 2 workers (2026-07-14 Raud run + concurrency bench): the
    # loaded context is a POOL shared by all in-flight requests — at 4 workers,
    # 4 × (~6-10k prompt + generation) overflowed the 16384 pool and ALL 28
    # chunks failed ("Context size has been exceeded"), leaving the serial
    # end-of-pass re-queue to do the real work (62-min Stage 4 + the recovered
    # moments only get light grounding). Pool sizing rule:
    #   workers ≤ loaded_ctx / (max prompt + worst-case generation) ≈ 32768/15k → 2.
    # More concurrency later requires shrinking prompts/outputs, not more slots.
    # CLIP_PASSB_MOMENT_WORKERS still overrides for experiments.

    common.run_module(log, "stages/stage4_moments.py", [], env=env, check=True)

    moments = json.loads(p.hype_moments.read_text(encoding="utf-8")) if p.hype_moments.exists() else []
    log.log(f"Found {len(moments)} clip-worthy moments")
    if not moments:
        log.warn("No clip-worthy moments detected. No clips to make.")
        common.append_processed(p.processed_log, ctx.vod_basename, "no_moments", ctx.style)
        raise common.PipelineExit(0, json.dumps({"status": "no_moments", "clips": 0, "style": ctx.style}))

    # Pass D rubric judge (Tier-4) — failure-soft.
    log.log("Applying Tier-4 Pass D rubric judge...")
    common.run_module(log, "stages/stage4_rubric.py", [str(p.hype_moments)], env=env, check=False)

    # Tier-4 Phase 4.6 MMR diversity rank — failure-soft.
    log.log("Applying Tier-4 Phase 4.6 MMR diversity rank...")
    common.run_module(log, "stages/stage4_diversity.py", [str(p.hype_moments)], env=env, check=False)

    # Phase 4.2 boundary snap — failure-soft.
    log.log("Applying Phase 4.2 boundary snap...")
    common.run_module(log, "stages/stage4_5_snap.py",
                      [str(p.transcript_json), str(p.hype_moments)], env=env, check=False)

    # Stage 4.5 — Moment Groups (only when stitching/narrative/arc-stitch enabled).
    if ctx.stitch or ctx.narrative or ctx.arc_stitch:
        common.set_stage(log, "Stage 4.5/8 — Moment Groups")
        log.log(f"=== Stage 4.5/8 — Moment Groups (stitch={ctx.stitch} narrative={ctx.narrative} arc_stitch={ctx.arc_stitch}) ===")
        common.run_module(log, "moment_groups.py", [
            "--stitch", "true" if ctx.stitch else "false",
            "--narrative", "true" if ctx.narrative else "false",
            "--arc-stitch", "true" if ctx.arc_stitch else "false",
            "--moments", str(p.hype_moments),
            "--out", str(p.work("moment_groups.json")),
        ], env=env, check=False)
