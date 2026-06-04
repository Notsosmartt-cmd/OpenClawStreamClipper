#!/usr/bin/env python3
"""Validate speaker diarization end-to-end (Tier-2 M1).

Downloads the pyannote weights once, runs diarization on a short audio sample
the same way speech.py::_maybe_diarize does, and prints the detected speakers.
Needs HF_TOKEN in .env with access to pyannote/speaker-diarization-3.1.

    .venv\\Scripts\\python.exe scripts\\validate_diarization.py [audio.wav]
"""
from __future__ import annotations

import os
import sys
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "lib"))

import paths  # noqa: E402

paths.load_dotenv()
try:
    import cuda_bootstrap  # noqa: F401  (registers nvidia DLL dirs)
except Exception:
    pass


def main() -> int:
    tok = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    if not tok:
        print("[diar] HF_TOKEN not set in .env — cannot run")
        return 1

    import torch
    import whisperx

    device = "cuda" if torch.cuda.is_available() else "cpu"
    audio_path = sys.argv[1] if len(sys.argv) > 1 else "diar_sample.wav"
    if not Path(audio_path).exists():
        print(f"[diar] audio not found: {audio_path}")
        return 1
    print(f"[diar] device={device}  audio={audio_path}")

    audio = whisperx.load_audio(audio_path)
    print(f"[diar] loaded audio: {len(audio)/16000:.1f}s")

    DP = getattr(whisperx, "DiarizationPipeline", None)
    if DP is None:
        from whisperx.diarize import DiarizationPipeline as DP

    print("[diar] building pipeline (downloads pyannote weights on first run)...")
    t0 = time.time()
    try:
        model = DP(model_name="pyannote/speaker-diarization-3.1",
                   token=tok, device=device)
    except TypeError:
        model = DP(token=tok, device=device)
    print(f"[diar] pipeline ready in {time.time()-t0:.1f}s; running diarization...")

    t1 = time.time()
    diar = model(audio)
    dt = time.time() - t1
    print(f"[diar] diarization ran in {dt:.1f}s")

    # whisperx returns a DataFrame with columns: speaker, start, end
    try:
        speakers = sorted(map(str, diar["speaker"].unique().tolist()))
        print(f"[diar] PASS — speakers detected: {speakers}  ({len(diar)} turns)")
        return 0
    except Exception:
        print(f"[diar] ran but unexpected result type: {type(diar)} -> {str(diar)[:200]}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
