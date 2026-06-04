#!/usr/bin/env python3
"""Stage 3 — Segment Detection. Port of stage3_segments.sh."""
from __future__ import annotations

from pipeline import common


def run(ctx) -> None:
    log = ctx.log
    common.set_stage(log, "Stage 3/8 — Segment Detection")
    log.log("=== Stage 3/8 — Segment Detection ===")

    # After Stage 2 (Whisper) all models are unloaded; load the text model fresh.
    common.load_model(log, ctx.llm_url, ctx.text_model, ctx.context_length)
    common.run_module(log, "stages/stage3_segments.py", [], env=ctx.child_env(), check=True)

    log.log("Segment detection complete")
