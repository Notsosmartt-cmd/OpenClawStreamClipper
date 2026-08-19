"""Shared state for the Finals dashboard (sibling app pattern, like poster/)."""
from __future__ import annotations

import threading
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
VODS_DIR = PROJECT_DIR / "vods"
OUT_ROOT = PROJECT_DIR / "finals_clips"
RUN_DIR = OUT_ROOT / ".run"
LOG_FILE = RUN_DIR / "scan.log"
RUNNER = PROJECT_DIR / "scripts" / "finals" / "run_finals.py"
PYTHON = PROJECT_DIR / ".venv" / "Scripts" / "python.exe"

VIDEO_EXTS = (".mp4", ".mkv", ".webm", ".mov", ".m4v", ".ts", ".avi")

job_lock = threading.Lock()
job_process = None          # subprocess.Popen of the current run (this instance)
job_meta: dict = {}         # {vod, params, started}
last_exit: int | None = None
