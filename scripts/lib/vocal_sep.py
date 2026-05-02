#!/usr/bin/env python3
"""Vocal-stem separation — Phase 3.3 of the 2026 upgrade.

Thin wrapper around Demucs v4 (``htdemucs_ft``) that extracts the vocals
stem from an audio file BEFORE transcription. Opt-in only: gated behind
``config/speech.json::vocal_separation.enabled``.

Why it matters: for streams with heavy background music or game audio
(DJ sets, music-game content, IRL driving with music in the car), Whisper
can burn attention on non-speech audio and either miss speech entirely
or hallucinate lyrics as dialogue. Running Demucs first gives Whisper a
clean vocals-only stem to work with — at a cost of ~60-120 s per hour of
audio on a 4090, much longer on CPU.

Graceful degradation: if ``demucs`` isn't installed (SPEECH_STACK=slim or
install failure), ``is_available()`` returns False and ``separate()``
returns None so callers can fall back to the original audio.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional


def is_available() -> bool:
    """True iff the demucs package is importable."""
    try:
        import demucs  # type: ignore  # noqa: F401
        return True
    except ImportError:
        return False


def separate(
    audio_path: str,
    out_path: str,
    backend: str = "demucs",
    model: str = "htdemucs_ft",
    device: Optional[str] = None,
) -> Optional[str]:
    """Run vocal-stem separation on ``audio_path``, write the vocals stem
    to ``out_path``. Returns the output path on success, None on failure.

    Supported backends:
    - ``demucs``: runs ``python3 -m demucs --two-stems=vocals -n {model}``
      via subprocess; the only backend implemented in Phase 3.3. Demucs
      writes to ``{out_dir}/{model}/{stem}/vocals.wav`` internally, which
      we then move to the caller's ``out_path``.
    """
    if backend != "demucs":
        print(
            f"[VOCAL] unsupported backend '{backend}'; only 'demucs' ships in Phase 3.3",
            file=sys.stderr,
        )
        return None

    if not is_available():
        print("[VOCAL] demucs not installed; skipping vocal separation", file=sys.stderr)
        return None

    audio = Path(audio_path)
    if not audio.exists():
        print(f"[VOCAL] input not found: {audio}", file=sys.stderr)
        return None

    out = Path(out_path)
    work_dir = out.parent / f"_demucs_{audio.stem}"
    work_dir.mkdir(parents=True, exist_ok=True)

    # Device detection — demucs respects CUDA_VISIBLE_DEVICES. Explicitly
    # pass `-d cuda` when available so it doesn't default to CPU on shared
    # rigs.
    demucs_device = device
    if demucs_device is None:
        try:
            import torch  # type: ignore
            demucs_device = "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:
            demucs_device = "cpu"

    cmd = [
        sys.executable, "-m", "demucs",
        "--two-stems=vocals",
        "-n", model,
        "-d", demucs_device,
        "-o", str(work_dir),
        str(audio),
    ]
    print(f"[VOCAL] running: {' '.join(cmd)}", file=sys.stderr)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        print("[VOCAL] demucs module-entry not found", file=sys.stderr)
        return None

    if proc.returncode != 0:
        print(
            f"[VOCAL] demucs failed (rc={proc.returncode}): "
            f"{proc.stderr.strip()[-500:]}",
            file=sys.stderr,
        )
        return None

    # Demucs v4 writes to {work_dir}/{model}/{stem}/vocals.wav
    expected = work_dir / model / audio.stem / "vocals.wav"
    if not expected.exists():
        # Older layouts or different model naming — fall back to a recursive search.
        matches = list(work_dir.glob("**/vocals.wav"))
        if not matches:
            print(
                f"[VOCAL] demucs produced no vocals.wav under {work_dir}",
                file=sys.stderr,
            )
            return None
        expected = matches[0]

    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        expected.replace(out)
    except OSError as e:
        print(f"[VOCAL] move vocals.wav → {out} failed: {e}", file=sys.stderr)
        return None

    # Clean up demucs working dir.
    try:
        for p in sorted(work_dir.rglob("*"), reverse=True):
            if p.is_file():
                p.unlink()
            else:
                p.rmdir()
        work_dir.rmdir()
    except OSError:
        pass

    return str(out)


def _cli() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="Demucs vocal-stem separation")
    ap.add_argument("--audio", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default="htdemucs_ft")
    ap.add_argument("--device", default=None, help="cuda | cpu | auto (default)")
    args = ap.parse_args()

    r = separate(args.audio, args.out, model=args.model, device=args.device)
    sys.exit(0 if r else 1)


if __name__ == "__main__":
    _cli()
