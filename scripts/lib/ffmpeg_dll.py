"""Put a complete FFmpeg shared-lib dir on the Windows DLL search path so
torchcodec can load in ANY stage subprocess.

Context: torchcodec (pyannote.audio 4.x's preferred decoder) is pip-installed,
but it needs FFmpeg *shared* libraries at runtime. The project's bundled
ffmpeg (C:\\ffmpeg\\bin) is a STATIC build with no av*.dll, so torchcodec can't
find its dependencies and `import`/use of it raises
"Could not load libtorchcodec ... (or one of its dependencies)". Several
installed apps ship a complete FFmpeg shared set (notably the AMD GPU driver's
CNext dir on this host). Adding one to the per-process DLL search path lets
torchcodec load.

Why a shared module (2026-06-06): the pipeline runs each stage as its own
subprocess, so the fix has to run in EVERY process that might touch torchcodec —
not just Stage 2's speech.py. Stage 4's M3 callback detection imports
torch-ecosystem libs that eagerly probe torchcodec; without this it hard-failed
("M3 callback detection failed (Could not load libtorchcodec ...)"), losing the
callback signal. Call enable_ffmpeg_dll_dir() near the top of any stage module
that loads the torch stack. Idempotent + best-effort (never raises).

Override the search with CLIP_FFMPEG_SHARED_DIR. No-op off Windows.
See concepts/clip-quality-remediation-2026-06.md Fix 4 and
concepts/pass-b-false-negatives.md.
"""
from __future__ import annotations

import glob
import os

_RESOLVED: list = []   # cache: [dir-or-None] once resolved
_HANDLES: list = []    # keep os.add_dll_directory handles alive (GC drops them)

_NEEDED = ("avcodec", "avformat", "avutil", "swresample")
_CANDIDATES = (
    os.environ.get("CLIP_FFMPEG_SHARED_DIR", ""),
    r"C:\Program Files\AMD\CNext\CNext",
    r"C:\Program Files\Blackmagic Design\DaVinci Resolve",
    r"C:\Program Files\Blender Foundation\Blender 4.2\blender.shared",
)


def enable_ffmpeg_dll_dir():
    """Add the first complete FFmpeg shared-lib dir to the DLL search path.
    Returns the dir used (or None). Idempotent; safe to call from anywhere."""
    if _RESOLVED:
        return _RESOLVED[0]
    if os.name != "nt":
        _RESOLVED.append(None)
        return None
    for d in _CANDIDATES:
        if not d or not os.path.isdir(d):
            continue
        try:
            names = " ".join(
                os.path.basename(p).lower()
                for p in glob.glob(os.path.join(d, "*.dll"))
            )
        except OSError:
            continue
        if all(stem in names for stem in _NEEDED):
            try:
                _HANDLES.append(os.add_dll_directory(d))
                _RESOLVED.append(d)
                return d
            except OSError:
                continue
    _RESOLVED.append(None)
    return None
