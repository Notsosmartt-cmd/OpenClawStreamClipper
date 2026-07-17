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

POSTED_SUBDIR = "posted_clips"


def posted_log_path() -> Path:
    """Ledger of clips already published through this app (lives beside the
    clips so it travels with the folder; clips/* is gitignored)."""
    return CLIPS_DIR / ".posted.buffer.json"


def posted_clips_dir() -> Path:
    """Fully-posted clips are moved here (owner req 2026-07-16): a clip whose
    every post verified 'sent' leaves the working folder so what remains in
    clips/ is the unposted backlog. The poster still lists/serves both."""
    return CLIPS_DIR / POSTED_SUBDIR


def resolve_clip_path(name: str) -> Path | None:
    """Find a clip by bare filename in the working folder, then posted_clips."""
    for d in (CLIPS_DIR, posted_clips_dir()):
        p = d / name
        if p.is_file():
            return p
    return None


def sweep_posted_clips() -> list[str]:
    """Move every ledger clip whose posts ALL verified 'sent' (and at least
    one exists) from clips/ into posted_clips/. Strict by design: any error,
    cap-skip, scheduled or still-publishing post keeps the file in place.
    Returns the names moved."""
    moved: list[str] = []
    try:
        posted = load_posted()
    except Exception:
        return moved
    dest_dir = posted_clips_dir()
    for name, entry in posted.items():
        posts = entry.get("posts") or []
        if not posts or any(p.get("status") != "sent" for p in posts):
            continue
        src = CLIPS_DIR / name
        if not src.is_file():
            continue  # already moved, renamed, or deleted
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / name
            if dest.exists():
                continue  # same name already archived — leave both untouched
            src.rename(dest)
            moved.append(name)
        except OSError:
            continue  # locked/in-use etc. — try again on the next sweep
    return moved


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


def update_posted_posts(name: str, posts: list) -> None:
    """Refresh an entry's posts array in place (verification results) —
    no times bump, nothing else touched."""
    posted = load_posted()
    if name in posted:
        posted[name]["posts"] = posts
        posted_log_path().write_text(json.dumps(posted, indent=2),
                                     encoding="utf-8")


def merge_posted_posts(name: str, new_posts: list) -> None:
    """Replace an entry's per-service posts with retried ones (a retry of
    the failed TikTok post must not clobber the Instagram record)."""
    posted = load_posted()
    entry = posted.get(name)
    if not entry:
        return
    replaced = {p.get("service") for p in new_posts}
    entry["posts"] = [p for p in entry.get("posts", [])
                      if p.get("service") not in replaced] + new_posts
    posted_log_path().write_text(json.dumps(posted, indent=2),
                                 encoding="utf-8")


# --- Batch job state (one at a time) ---
job_lock = threading.Lock()
current_job: dict | None = None

# Cached Buffer account/channels (avoid burning the 100-req/15-min budget on
# UI polls; refreshed by /api/channels?refresh=1).
account_cache: dict | None = None
channels_cache: list | None = None
channels_cache_at: float = 0.0
