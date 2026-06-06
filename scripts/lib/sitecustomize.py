"""Auto-run at interpreter startup in EVERY pipeline stage subprocess.

`PATHS.child_env()` puts `scripts/lib` on `PYTHONPATH` so the reused stage
modules can import their siblings; Python's `site` machinery therefore imports
this `sitecustomize` automatically at startup, before any stage code runs.

We use that hook to put a complete FFmpeg shared-lib dir on the Windows DLL
search path so **torchcodec loads in every process** — pyannote (Stage 2), M3
callbacks + MMR diversity (Stage 4), Pass D, and any future torch-stack stage —
without each module needing its own bootstrap call.

Why this is necessary: once torchcodec is pip-installed (for pyannote),
`transformers` / `sentence-transformers` eagerly probe it on import. Without the
FFmpeg *shared* libs on the search path that probe HARD-FAILS and takes the
importing module down — first M3 (BUG 62), then MMR diversity. A per-module fix
is whack-a-mole because each stage is its own subprocess; this central hook
covers them all.

Bulletproof: wrapped so it can never break interpreter startup; `ffmpeg_dll`
is itself a no-op off Windows / when no shared FFmpeg set is found.
See scripts/lib/ffmpeg_dll.py and concepts/bugs-and-fixes.md BUG 62.
"""
try:
    import ffmpeg_dll
    ffmpeg_dll.enable_ffmpeg_dll_dir()
except Exception:
    pass
