#!/usr/bin/env python3
"""Register the venv's pip-installed CUDA DLLs on the Windows search path.

CTranslate2 (faster-whisper / whisperx) loads cuDNN/cuBLAS at runtime; on
Windows those DLLs live in ``site-packages/nvidia/*/bin`` and must be added
via ``os.add_dll_directory`` before the first model load (PATH alone is no
longer searched for extension-module dependencies on Python 3.8+).

Import this module at the top of any module that loads faster-whisper or
whisperx — importing it runs :func:`ensure` once.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_done = False


def ensure() -> int:
    """Add nvidia/*/bin dirs to the DLL search path. Returns how many added."""
    global _done
    if _done or not hasattr(os, "add_dll_directory"):
        _done = True
        return 0
    n = 0
    site = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
    if site.is_dir():
        for sub in site.iterdir():
            binp = sub / "bin"
            if binp.is_dir():
                try:
                    os.add_dll_directory(str(binp))
                    n += 1
                except OSError:
                    pass
    _done = True
    return n


ensure()
