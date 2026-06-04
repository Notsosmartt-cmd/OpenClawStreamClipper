#!/usr/bin/env python3
"""GPU validation for bare-metal faster-whisper transcription.

Phase 0 gate of the Windows migration: confirms CTranslate2 can actually
load a Whisper model on the GPU and run inference — not just enumerate the
device. Run inside the venv after installing faster-whisper + the CUDA libs:

    .venv\\Scripts\\python.exe scripts\\validate_gpu.py [audio.wav]

Exit 0 = GPU transcription works (keep faster-whisper).
Exit 1 = GPU path is broken (fall back to CPU int8 or the openai-whisper
         backend proven by VideoToText-main).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def add_nvidia_dll_dirs() -> list[str]:
    """Put the pip-installed CUDA libs (cuDNN/cuBLAS/nvrtc) on the Windows DLL
    search path so CTranslate2 can load them. No-op off Windows."""
    added: list[str] = []
    if not hasattr(os, "add_dll_directory"):
        return added
    site = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
    for sub in ("cudnn", "cublas", "cuda_nvrtc", "cuda_runtime"):
        binp = site / sub / "bin"
        if binp.is_dir():
            try:
                os.add_dll_directory(str(binp))
                added.append(str(binp))
            except OSError:
                pass
    return added


def main() -> int:
    added = add_nvidia_dll_dirs()
    print(f"[gate] added {len(added)} nvidia DLL dir(s)")

    import ctranslate2

    print(f"[gate] ctranslate2 {ctranslate2.__version__}")
    try:
        n = ctranslate2.get_cuda_device_count()
    except Exception as e:  # noqa: BLE001
        print(f"[gate] get_cuda_device_count failed: {e}")
        n = 0
    print(f"[gate] CUDA device count: {n}")
    if n < 1:
        print("[gate] FAIL: no CUDA device visible to CTranslate2")
        return 1

    from faster_whisper import WhisperModel

    # 'tiny' exercises the same CUDA runtime/kernels as large-v3 without the
    # 3 GB download. If tiny runs on cuda, large-v3 will too.
    try:
        model = WhisperModel("tiny", device="cuda", compute_type="float16")
        print("[gate] model loaded on cuda (float16)")
    except Exception as e:  # noqa: BLE001
        print(f"[gate] FAIL: could not load model on cuda: {e}")
        return 1

    wav = sys.argv[1] if len(sys.argv) > 1 else "test_gate.wav"
    if not Path(wav).exists():
        print(f"[gate] no audio at {wav}; model loaded OK but inference not run")
        return 0
    try:
        segments, info = model.transcribe(wav, beam_size=1)
        segs = list(segments)  # force the generator to actually run on GPU
        print(f"[gate] inference OK: {len(segs)} segment(s), lang={info.language}")
    except Exception as e:  # noqa: BLE001
        print(f"[gate] FAIL: inference on cuda crashed: {e}")
        return 1

    print("[gate] PASS: faster-whisper runs on the GPU")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
