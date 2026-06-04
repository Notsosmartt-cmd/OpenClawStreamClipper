#!/usr/bin/env python3
"""Stage 5 — Frame Extraction (6 payoff frames + A2 setup frames).
Port of stage5_frames.sh."""
from __future__ import annotations

import json

from pipeline import common

# label:offset around the moment peak T (payoff window per ClippingResearch).
FRAME_OFFSETS = [("tminus2", -2), ("t0", 0), ("tplus1", 1),
                 ("tplus2", 2), ("tplus3", 3), ("tplus5", 5)]


def _extract(ctx, frame_t: int, out_name: str) -> None:
    common.run_ffmpeg([
        "ffmpeg", "-nostdin", "-y", "-ss", str(max(0, frame_t)),
        "-i", str(ctx.vod_path), "-frames:v", "1",
        "-vf", "scale=960:540", "-q:v", "2",
        str(ctx.paths.work(out_name)),
    ])


def run(ctx) -> None:
    log = ctx.log
    p = ctx.paths
    common.set_stage(log, "Stage 5/8 — Frame Extraction")
    log.log("=== Stage 5/8 — Frame Extraction ===")

    moments = json.loads(p.hype_moments.read_text(encoding="utf-8"))

    n = 0
    for m in moments:
        t = m["timestamp"]
        log.log(f"Extracting payoff-window frames for moment at T={t}s (T-2..T+5)...")
        for label, off in FRAME_OFFSETS:
            _extract(ctx, int(t) + off, f"frames_{t}_{label}.jpg")
        n += 1
    log.log(f"Extracted payoff-window frames for {n} moments")

    # Tier-3 A2 — setup frames for callback / arc moments.
    log.log("Tier-3 A2: extracting setup frames for callback/arc moments...")
    sc = 0
    for m in moments:
        setup = m.get("setup_time")
        if setup is None:
            continue
        t = m["timestamp"]
        for label, off in [("setupminus1", -1), ("setupplus1", 1)]:
            _extract(ctx, int(setup) + off, f"frames_{t}_{label}.jpg")
        sc += 1
    log.log(f"A2 extracted setup frames for {sc} callback/arc moments")
