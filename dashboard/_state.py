"""Shared state for the dashboard package.

All cross-module globals live here as module-level attributes. Routes and
helpers access them via attribute lookup (``from dashboard import _state``
then ``_state.VODS_DIR``) — NOT ``from dashboard._state import VODS_DIR``,
because ``_reload_path_globals()`` rebinds these values when the user
saves a new VODS/CLIPS folder, and a captured binding would go stale.

Extracted from dashboard/app.py as part of the modularization plan
(Phase C). Behavior is unchanged — only the home of the state moved.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

# --- Environment detection ---
INSIDE_DOCKER = os.path.exists("/.dockerenv") or "DOCKER" in os.environ

# Paths — use project-level vods/ and clips/ directories
BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent
VODS_DIR = Path(os.environ.get("CLIP_VODS_DIR", str(PROJECT_DIR / "vods")))
CLIPS_DIR = Path(os.environ.get("CLIP_CLIPS_DIR", str(PROJECT_DIR / "clips")))
DIAGNOSTICS_DIR = CLIPS_DIR / ".diagnostics"
TRANSCRIPTION_DIR = VODS_DIR / ".transcriptions"
PROCESSED_LOG = VODS_DIR / "processed.log"
MODELS_CONFIG = PROJECT_DIR / "config" / "models.json"
HARDWARE_CONFIG = PROJECT_DIR / "config" / "hardware.json"
PATHS_CONFIG = PROJECT_DIR / "config" / "paths.json"
ORIGINALITY_CONFIG = PROJECT_DIR / "config" / "originality.json"
DOCKER_COMPOSE_FILE = PROJECT_DIR / "docker-compose.yml"

# Pipeline script paths
PIPELINE_SCRIPT = str(PROJECT_DIR / "scripts" / "clip-pipeline.sh")
DOCKER_PIPELINE_SCRIPT = "/root/scripts/clip-pipeline.sh"

# Lifecycle marker paths (always inside the container)
PIPELINE_PID_PATH = "/tmp/clipper/pipeline.pid"
PIPELINE_DONE_PATH = "/tmp/clipper/pipeline.done"

# Temp dir for pipeline state files (resolved at module import)
if INSIDE_DOCKER:
    TEMP_DIR = Path("/tmp/clipper")
elif os.name == "nt":
    TEMP_DIR = Path(os.environ.get("TEMP", "C:/Temp")) / "clipper"
else:
    TEMP_DIR = Path("/tmp/clipper")

STAGE_FILE = TEMP_DIR / "pipeline_stage.txt"
LOG_FILE = TEMP_DIR / "pipeline.log"
STAGES_LOG = TEMP_DIR / "pipeline_stages.log"

# Pipeline process state
pipeline_process = None
pipeline_lock = threading.Lock()
pipeline_vod_name = None

# --- Default configuration constants ---
DEFAULT_ORIGINALITY = {
    "framing": "blur_fill",
    "originality": True,
    "stitch": False,
    "narrative": True,
    "camera_pan": False,
    "tts_vo": False,
    "music_bed": "",
    "music_tier_c": False,
}

DEFAULT_MODELS = {
    "text_model": "qwen/qwen3.5-9b",
    "vision_model": "qwen/qwen3.5-9b",
    "whisper_model": "large-v3",
    "llm_url": "http://host.docker.internal:1234",
    "context_length": 8192,
}

DEFAULT_HARDWARE = {
    "whisper_device": "cuda",
}

MODEL_ROLES = {
    "text_model": {
        "label": "Text Model",
        "description": "Segment classification (Stage 3) and moment detection (Stage 4). Needs strong reasoning and JSON output.",
        "provider": "lmstudio",
    },
    "vision_model": {
        "label": "Vision Model",
        "description": "Frame analysis and clip title generation (Stage 6). Must support image input.",
        "provider": "lmstudio",
    },
    "whisper_model": {
        "label": "Whisper Model",
        "description": "Audio transcription (Stage 2) and clip captions (Stage 7). Runs via faster-whisper.",
        "provider": "whisper",
    },
}

SUGGESTED_MODELS = {
    "text_model": {
        "id": "qwen/qwen3.5-9b",
        "reason": "Best reasoning + JSON output for moment detection. Also handles vision "
                  "(Stage 6) — use the same model for both roles to avoid VRAM swap. ~11 GB VRAM.",
    },
    "vision_model": {
        "id": "qwen/qwen3.5-9b",
        "reason": "qwen3.5-9b supports both text and vision — setting the same model for "
                  "both roles skips the Stage 5 unload/reload and saves ~2 min per run. "
                  "Use qwen/qwen3-vl-8b or qwen/qwen2.5-vl-7b if you prefer a dedicated vision model.",
        "alternatives": ["qwen/qwen3-vl-8b", "qwen/qwen2.5-vl-7b"],
    },
    "whisper_model": {
        "id": "large-v3",
        "reason": "Best transcription accuracy. Recommended for GPU. ~3 GB VRAM.",
    },
}

CONTEXT_LENGTH_GUIDE = [
    {"value": 4096,  "label": "4096 — ~2 GB KV cache  (8 GB VRAM total)"},
    {"value": 8192,  "label": "8192 — ~4 GB KV cache  (12 GB VRAM total) ⭐ recommended"},
    {"value": 16384, "label": "16384 — ~8 GB KV cache (20 GB VRAM total)"},
    {"value": 32768, "label": "32768 — ~16 GB KV cache (28 GB VRAM total)"},
]

WHISPER_MODELS = [
    {"name": "large-v3", "size": "~3 GB", "description": "Best accuracy, recommended for GPU"},
    {"name": "large-v2", "size": "~3 GB", "description": "Previous best, very accurate"},
    {"name": "medium", "size": "~1.5 GB", "description": "Good balance of speed and accuracy"},
    {"name": "small", "size": "~500 MB", "description": "Fast, decent accuracy"},
    {"name": "base", "size": "~150 MB", "description": "Very fast, lower accuracy"},
    {"name": "tiny", "size": "~75 MB", "description": "Fastest, lowest accuracy"},
]


def _reload_path_globals() -> None:
    """Apply folder paths from config/paths.json, overriding env-based defaults."""
    global VODS_DIR, CLIPS_DIR, DIAGNOSTICS_DIR, TRANSCRIPTION_DIR, PROCESSED_LOG
    if PATHS_CONFIG.exists():
        try:
            with open(PATHS_CONFIG) as f:
                cfg = json.load(f)
            if cfg.get("vods_dir"):
                VODS_DIR = Path(cfg["vods_dir"])
            if cfg.get("clips_dir"):
                CLIPS_DIR = Path(cfg["clips_dir"])
        except Exception:
            pass
    DIAGNOSTICS_DIR = CLIPS_DIR / ".diagnostics"
    TRANSCRIPTION_DIR = VODS_DIR / ".transcriptions"
    PROCESSED_LOG = VODS_DIR / "processed.log"


_reload_path_globals()

# Ensure dirs exist (post-reload so they're created in the right place)
TRANSCRIPTION_DIR.mkdir(parents=True, exist_ok=True)
TEMP_DIR.mkdir(parents=True, exist_ok=True)
