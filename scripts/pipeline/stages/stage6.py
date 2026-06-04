#!/usr/bin/env python3
"""Stage 6 — Vision Enrichment (non-gatekeeping) + 6.5 camera-pan prep.
Port of stage6_vision.sh."""
from __future__ import annotations

import json

from pipeline import common


def run(ctx) -> None:
    log = ctx.log
    p = ctx.paths
    env = ctx.child_env()

    # Bump the stage marker before the (possibly slow) VRAM swap.
    common.set_stage(log, "Stage 6/8 — Vision Enrichment (loading model)")

    # Phase 5.1: swap Pass-B text model -> Stage-6 vision model only if different.
    if ctx.text_model_passb != ctx.vision_model_stage6:
        common.unload_model(log, ctx.llm_url, ctx.text_model_passb)
        common.load_model(log, ctx.llm_url, ctx.vision_model_stage6, ctx.context_length)
    else:
        log.log(f"Pass B text and Stage 6 vision models are the same "
                f"('{ctx.text_model_passb}') — skipping VRAM swap")

    # Stage 5.5 — Vision Judge (Plan 1.a): tournament re-rank of the Pass C
    # shortlist using the multimodal model just loaded above. Failure-soft
    # (check=False): on outage / too-few comparisons it leaves hype_moments.json
    # in Pass C order and Stage 6 proceeds unchanged.
    common.set_stage(log, "Stage 5.5/8 — Vision Judge (tournament re-rank)")
    log.log("=== Stage 5.5/8 — Vision Judge ===")
    common.run_module(log, "stages/stage5_5_judge.py", [], env=env, check=False)

    common.set_stage(log, "Stage 6/8 — Vision Enrichment")
    log.log("=== Stage 6/8 — Vision Enrichment ===")
    common.run_module(log, "stages/stage6_vision.py", [], env=env, check=True)

    scored = json.loads(p.scored_moments.read_text(encoding="utf-8")) if p.scored_moments.exists() else []
    log.log(f"Moments to render: {len(scored)} (all detected moments proceed to rendering)")

    # Stage 6.5 — Camera Pan Prep (optional).
    if ctx.camera_pan and ctx.framing == "camera_pan":
        common.set_stage(log, "Stage 6.5/8 — Camera Pan Prep")
        log.log("=== Stage 6.5/8 — Camera Pan Prep (face tracking) ===")
        common.run_module(log, "stages/stage6_5_campan.py", [], env=env, check=False)

    if not scored:
        log.warn("No moments to render (detection found nothing).")
        common.append_processed(p.processed_log, ctx.vod_basename, "no_moments", ctx.style)
        raise common.PipelineExit(0, json.dumps({"status": "no_moments", "clips": 0, "style": ctx.style}))
