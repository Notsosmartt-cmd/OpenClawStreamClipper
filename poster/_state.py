"""Shared state for the poster package: paths, secrets, config, job globals.

Mirrors the dashboard's path resolution (env override -> config/paths.json ->
project default) so the poster always sees the same clips folder the dashboard
and pipeline write to, without importing the dashboard package.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent

PATHS_CONFIG = PROJECT_DIR / "config" / "paths.json"
# Poster settings (Cloudinary credentials + remembered UI defaults).
# Gitignored — holds secrets. Created on first save from the Setup panel.
POSTER_CONFIG = PROJECT_DIR / "config" / "buffer_poster.json"
# The Buffer API key, one line, dropped in the repo root by the owner.
# Gitignored. Read at startup and on demand — never logged, never echoed.
API_KEY_FILE = PROJECT_DIR / "BufferIOapiKey.txt"

CLIPS_DIR = Path(os.environ.get("CLIP_CLIPS_DIR", str(PROJECT_DIR / "clips")))


def _reload_paths() -> None:
    """Apply config/paths.json (same precedence as dashboard/_state.py)."""
    global CLIPS_DIR
    if PATHS_CONFIG.exists():
        try:
            cfg = json.loads(PATHS_CONFIG.read_text(encoding="utf-8"))
            if cfg.get("clips_dir"):
                CLIPS_DIR = Path(cfg["clips_dir"])
        except Exception:
            pass


_reload_paths()

VIDEO_EXTS = (".mp4", ".mkv", ".webm", ".mov")


def posted_log_path() -> Path:
    """Ledger of clips already published through this app (lives beside the
    clips so it travels with the folder; clips/* is gitignored)."""
    return CLIPS_DIR / ".posted.buffer.json"


def load_api_key() -> str | None:
    try:
        key = API_KEY_FILE.read_text(encoding="utf-8").strip()
        return key or None
    except OSError:
        return None


def load_config() -> dict:
    if POSTER_CONFIG.exists():
        try:
            return json.loads(POSTER_CONFIG.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_config(cfg: dict) -> None:
    POSTER_CONFIG.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def load_posted() -> dict:
    p = posted_log_path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def record_posted(name: str, entry: dict) -> None:
    posted = load_posted()
    prev = posted.get(name)
    entry["times"] = (prev.get("times", 1) + 1) if prev else 1
    posted[name] = entry
    posted_log_path().write_text(json.dumps(posted, indent=2), encoding="utf-8")


# --- Batch job state (one at a time) ---
job_lock = threading.Lock()
current_job: dict | None = None

# Cached Buffer account/channels (avoid burning the 100-req/15-min budget on
# UI polls; refreshed by /api/channels?refresh=1).
account_cache: dict | None = None
channels_cache: list | None = None
channels_cache_at: float = 0.0
