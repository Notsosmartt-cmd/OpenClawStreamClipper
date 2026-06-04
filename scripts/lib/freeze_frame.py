#!/usr/bin/env python3
"""Emit an FFmpeg fragment that freezes a single frame at time `t` for
`duration` seconds, then resumes.

Implementation note: FFmpeg's `tpad=stop_mode=clone` only pads the END of
a stream. Mid-clip freezes need a more involved trick using `select` and
`fps` plus `setpts`. We use the standard pattern:

    [v]
        split=2[fa][fb]
        ; [fa]trim=0:T,setpts=PTS-STARTPTS[pre]
        ; [fb]trim=T:T+0.04,setpts=PTS-STARTPTS,
              loop=loop=N:size=1:start=0,setpts=N/FRAME_RATE/TB[hold]
        ; [pre][hold]concat=n=2:v=1:a=0[out]

This produces a clean still-frame hold of `duration` at time `t`.

If the source has audio, the caller is responsible for the audio side;
this helper only handles video. The renderer should pad the audio using
silence for the same `duration` and concat to keep A/V in sync.

Tradeoff: filter graph gets noticeably more complex; we keep this off by
default and only enable when the comedy/skill profile picks it up.

Usage:
    fragment, out_label = build_freeze_fragment(
        in_label="base", out_label="frozen", t=4.2, duration=0.5, fps=30,
    )
"""
from __future__ import annotations


def build_freeze_fragment(in_label: str, out_label: str,
                          t: float, duration: float = 0.5,
                          fps: int = 30) -> tuple[str, str]:
    if duration <= 0 or t < 0:
        return "", in_label

    # Number of cloned frames to fill `duration` at `fps`.
    n_frames = max(1, int(round(duration * fps)))

    # Use `select` + `loop` to freeze exactly ONE frame at time t for n_frames.
    # The `setpts=N/FRAME_RATE/TB` resets the PTS so concat doesn't barf.
    fragment = (
        f"[{in_label}]split=2[ff_a][ff_b];"
        f"[ff_a]trim=0:{t:.3f},setpts=PTS-STARTPTS[ff_pre];"
        f"[ff_b]trim={t:.3f}:{t + 1.0/max(fps,1):.3f},"
        f"setpts=PTS-STARTPTS,loop=loop={n_frames}:size=1:start=0,"
        f"setpts=N/{fps}/TB[ff_hold];"
        f"[{in_label}]trim=start={t + 1.0/max(fps,1):.3f},"
        f"setpts=PTS-STARTPTS[ff_post];"
        f"[ff_pre][ff_hold][ff_post]concat=n=3:v=1:a=0[{out_label}]"
    )
    return fragment, out_label


def build_audio_pad(t: float, duration: float, in_label: str = "0:a",
                    out_label: str = "fz_audio") -> str:
    """Companion: pad audio with silence at `t` for `duration` to keep A/V
    in sync with the video freeze."""
    if duration <= 0:
        return ""
    return (
        f"[{in_label}]asplit=2[fa_a][fa_b];"
        f"[fa_a]atrim=0:{t:.3f},asetpts=PTS-STARTPTS[fa_pre];"
        f"anullsrc=channel_layout=stereo:sample_rate=44100[fa_silsrc];"
        f"[fa_silsrc]atrim=0:{duration:.3f}[fa_sil];"
        f"[fa_b]atrim=start={t:.3f},asetpts=PTS-STARTPTS[fa_post];"
        f"[fa_pre][fa_sil][fa_post]concat=n=3:v=0:a=1[{out_label}]"
    )


def _cli() -> int:
    import argparse, json, sys
    ap = argparse.ArgumentParser()
    ap.add_argument("--t", type=float, required=True)
    ap.add_argument("--duration", type=float, default=0.5)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--in-label", default="base")
    ap.add_argument("--out-label", default="frozen")
    args = ap.parse_args()
    frag, lab = build_freeze_fragment(args.in_label, args.out_label,
                                      args.t, args.duration, args.fps)
    print(json.dumps({"fragment": frag, "out_label": lab}))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_cli())
