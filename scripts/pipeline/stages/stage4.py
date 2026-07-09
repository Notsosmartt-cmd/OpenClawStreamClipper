#!/usr/bin/env python3
"""Stage 4 — Moment Detection (Pass A/B/C + Pass D + 4.5 groups).
Port of stage4_moments.sh."""
from __future__ import annotations

import json

from pipeline import common


def run(ctx) -> None:
    log = ctx.log
    p = ctx.paths
    env = ctx.child_env()
    common.set_stage(log, "Stage 4/8 — Moment Detection")
    log.log(f"=== Stage 4/8 — Moment Detection (style: {ctx.style}) ===")

    # Phase 5.1: swap to the Pass-B text model when it differs from Stage 3's.
    if ctx.text_model_passb != ctx.text_model:
        log.log(f"Phase 5.1: swapping text model {ctx.text_model} -> {ctx.text_model_passb}")
        common.unload_model(log, ctx.llm_url, ctx.text_model)
        common.load_model(log, ctx.llm_url, ctx.text_model_passb, ctx.context_length)

    # BUG 67 fail-fast guard: one tiny probe of the (now-loaded) Pass-B model. Aborts in
    # ~1 s if the model ignores no-think (permanent reasoning -> would fail every chunk).
    common.preflight_thinking(log, ctx.llm_url, ctx.text_model_passb)

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
