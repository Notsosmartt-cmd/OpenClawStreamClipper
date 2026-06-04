#!/usr/bin/env python3
"""Stage 8 — Logging & Summary. Port of stage8_logging.sh."""
from __future__ import annotations

from pipeline import common


def run(ctx) -> None:
    log = ctx.log
    p = ctx.paths
    common.set_stage(log, "Stage 8/8 — Summary")
    log.log("=== Stage 8/8 — Summary ===")

    total = 0
    if p.clips_made.exists():
        total = sum(1 for _ in p.clips_made.open(encoding="utf-8"))

    common.append_processed(p.processed_log, ctx.vod_basename, f"{total}_clips", ctx.style)

    # Stage 8 summary JSON (relayed to Discord by OpenClaw) — failure-soft.
    common.run_module(log, "stages/stage8_summary.py", [], env=ctx.child_env(), check=False)

    log.log(f"Pipeline complete! {total} clip(s) saved to {p.clips_dir} (style: {ctx.style})")
