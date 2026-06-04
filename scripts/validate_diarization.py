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


def _full_path_test(audio_path: str) -> int:
    """Run the production speech.transcribe path and confirm speaker labels land
    in the transcript JSON (transcribe -> align -> diarize -> assign_word_speakers)."""
    import json
    os.environ.setdefault("CLIP_WHISPER_MODEL", "large-v3")
    for k, v in paths.PATHS.child_env().items():
        os.environ.setdefault(k, v)
    import speech
    print("[full] running speech.transcribe (transcribe+align+diarize+assign)...")
    summary = speech.transcribe(audio_path=audio_path, out_json="diar_full_out.json",
                                out_srt="diar_full_out.srt", vod_basename="diar_test")
    print("[full] summary:", summary)
    segs = json.load(open("diar_full_out.json", encoding="utf-8"))
    spk = [s.get("speaker") for s in segs if s.get("speaker")]
    print(f"[full] segments with speaker: {len(spk)}/{len(segs)} | speakers: {sorted(set(spk))}")
    print("[full] PASS — speaker labels present" if spk else "[full] FAIL — no speaker labels")
    return 0 if spk else 1


def main() -> int:
    tok = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    if not tok:
        print("[diar] HF_TOKEN not set in .env — cannot run")
        return 1

    import torch
    import whisperx

    device = "cuda" if torch.cuda.is_available() else "cpu"
    cli = [a for a in sys.argv[1:] if not a.startswith("-")]
    audio_path = cli[0] if cli else "diar_sample.wav"
    full = "--full" in sys.argv  # also run the production speech.transcribe path
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
        print(f"[diar] PASS (model) — speakers detected: {speakers}  ({len(diar)} turns)")
    except Exception:
        print(f"[diar] model ran; unexpected result type: {type(diar)}")

    if full:
        return _full_path_test(audio_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
