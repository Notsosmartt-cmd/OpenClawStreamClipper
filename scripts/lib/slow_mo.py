#!/usr/bin/env python3
"""Emit slow-mo fragments with an FPS guard.

Per the editing-profile plan: slow-mo only looks good on ≥50 fps source.
Below that threshold we ROUTE TO ZOOM PUNCH instead. The dispatcher
returns one of two outcomes:

    {"mode": "slow_mo", "fragment": "<filter>", "out_label": "<label>"}
    {"mode": "downgrade", "zoom_punch": {"t": <t>, "scale": 1.15, "hold": 0.40}}

Caller checks `mode` and either splices the filter or appends the zoom
punch to the existing list.

Source FPS is probed by `ffprobe` once per render and passed in.
"""
from __future__ import annotations


SLOW_MO_FPS_FLOOR = 50


def probe_source_fps(src: str) -> float:
    """Return source FPS via ffprobe. 0.0 on failure."""
    import subprocess
    try:
        out = subprocess.check_output([
            "ffprobe", "-v", "quiet", "-select_streams", "v:0",
            "-show_entries", "stream=avg_frame_rate", "-of", "csv=p=0", src,
        ], timeout=15).decode("utf-8").strip()
        if "/" in out:
            num, den = out.split("/", 1)
            n = float(num); d = float(den) or 1.0
            return n / d
        return float(out or "0")
    except Exception:
        return 0.0


def plan_slow_mo(in_label: str, out_label: str, start: float, end: float,
                 rate: float, source_fps: float) -> dict:
    """Decide whether to apply slow-mo or downgrade to a zoom punch."""
    if end <= start or rate <= 0 or rate >= 1.0:
        return {"mode": "noop"}

    if source_fps < SLOW_MO_FPS_FLOOR:
        # Downgrade: emit a zoom punch centered on the slow-mo midpoint.
        return {
            "mode": "downgrade",
            "reason": f"source {source_fps:.1f} fps below slow-mo floor {SLOW_MO_FPS_FLOOR}",
            "zoom_punch": {
                "t":     round((start + end) / 2.0, 3),
                "scale": 1.18,
                "hold":  round(min(end - start, 0.6), 3),
            },
        }

    fragment = (
        f"[{in_label}]split=2[sm_a][sm_b];"
        f"[sm_a]trim=0:{start:.3f},setpts=PTS-STARTPTS[sm_pre];"
        f"[sm_b]trim={start:.3f}:{end:.3f},"
        f"setpts=(PTS-STARTPTS)/{rate:.3f}[sm_slow];"
        f"[{in_label}]trim=start={end:.3f},setpts=PTS-STARTPTS[sm_post];"
        f"[sm_pre][sm_slow][sm_post]concat=n=3:v=1:a=0[{out_label}]"
    )
    return {"mode": "slow_mo", "fragment": fragment, "out_label": out_label}


def _cli() -> int:
    import argparse, json, sys
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_probe = sub.add_parser("probe")
    p_probe.add_argument("src")
    p_plan = sub.add_parser("plan")
    p_plan.add_argument("--start", type=float, required=True)
    p_plan.add_argument("--end", type=float, required=True)
    p_plan.add_argument("--rate", type=float, default=0.5)
    p_plan.add_argument("--fps", type=float, required=True)
    p_plan.add_argument("--in-label", default="base")
    p_plan.add_argument("--out-label", default="slow")
    args = ap.parse_args()
    if args.cmd == "probe":
        print(probe_source_fps(args.src))
    else:
        print(json.dumps(plan_slow_mo(
            args.in_label, args.out_label, args.start, args.end,
            args.rate, args.fps), indent=2))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_cli())
