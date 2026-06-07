"""Shared video-encoder selection for the Stage 7 render paths (stitch, profile,
and any future path), mirroring the solo-render NVENC switch in
`scripts/pipeline/stages/stage7.py`.

Picks **h264_nvenc** when it actually works on this machine (the model is
unloaded before Stage 7, so the GPU is free), else falls back to **libx264**.
`STAGE7_ENCODER=auto|nvenc|libx264` overrides (default `auto` = probe). The probe
and choice are cached per process, so it's safe to call `video_args()` at every
encode site. NVENC accelerates the *encode* only; the per-clip filtering stays on
CPU. See concepts/clip-rendering.md.
"""
from __future__ import annotations

import os
import subprocess
import sys

_RESOLVED: list = []


def nvenc_works() -> bool:
    """True iff h264_nvenc can actually encode here (build has it AND the
    driver/GPU accept a session). One-shot 0.1 s null-muxed probe."""
    try:
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
             "-i", "color=c=black:s=256x256:r=30:d=0.1", "-c:v", "h264_nvenc",
             "-f", "null", "-"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30,
        )
        return r.returncode == 0
    except Exception:
        return False


def encoder() -> str:
    """Return 'nvenc' or 'libx264' (cached per process). `STAGE7_ENCODER`
    (auto|nvenc|libx264, default auto) overrides; auto probes NVENC."""
    if _RESOLVED:
        return _RESOLVED[0]
    choice = os.environ.get("STAGE7_ENCODER", "auto").strip().lower()
    if choice == "libx264":
        enc = "libx264"
    elif choice == "nvenc":
        enc = "nvenc"
    else:
        enc = "nvenc" if nvenc_works() else "libx264"
    _RESOLVED.append(enc)
    print(f"[VENC] video encoder: {enc} (STAGE7_ENCODER to override)", file=sys.stderr)
    return enc


def video_args(crf: int = 20, preset_libx264: str = "fast") -> list:
    """The `-c:v ...` encoder flags for the resolved encoder. NVENC maps crf->cq
    in VBR mode (~the libx264 crf quality target). Callers keep their own
    -profile/-level/-pix_fmt/-r/-g/-b:v + audio flags (all valid for both)."""
    if encoder() == "nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", str(crf)]
    return ["-c:v", "libx264", "-crf", str(crf), "-preset", preset_libx264]
