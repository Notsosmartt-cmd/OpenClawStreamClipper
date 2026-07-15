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
import sys
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

# Pipeline script paths. Bare-metal native mode runs the Python orchestrator
# directly; DOCKER_PIPELINE_SCRIPT is kept only for the legacy docker-exec path.
PIPELINE_SCRIPT = str(PROJECT_DIR / "scripts" / "run_pipeline.py")
DOCKER_PIPELINE_SCRIPT = "/root/scripts/clip-pipeline.sh"


def repo_python() -> str:
    """Interpreter for spawned pipeline/research jobs — pinned to the repo venv.

    W0.1 (plan-speed-wave3): the dashboard itself may run under ANY interpreter
    (system python, a venvlauncher child, an agent's shell), and spawning
    children with ``sys.executable`` made the speech backend depend on how the
    dashboard happened to be started — whisperx/pyannote live only in the venv,
    so runs launched from a system-python dashboard silently fell back to
    faster-whisper, losing wav2vec2 word alignment AND speaker diarization.
    Resolve the repo venv explicitly; fall back to ``sys.executable`` so an
    install without a .venv keeps working. (Inline ``-c`` probes that don't
    import repo deps may still use sys.executable directly.)
    """
    if os.name == "nt":
        cand = PROJECT_DIR / ".venv" / "Scripts" / "python.exe"
    else:
        cand = PROJECT_DIR / ".venv" / "bin" / "python"
    return str(cand) if cand.exists() else sys.executable

# Temp/work dir for pipeline state files. Resolve from the orchestrator's
# single source of truth (scripts/lib/paths.py) so the dashboard reads the
# exact stage/log/marker files run_pipeline.py writes.
try:
    import sys as _sys
    _sys.path.insert(0, str(PROJECT_DIR / "scripts" / "lib"))
    import paths as _paths  # type: ignore
    TEMP_DIR = _paths.PATHS.work_dir
except Exception:
    if INSIDE_DOCKER:
        TEMP_DIR = Path("/tmp/clipper")
    elif os.name == "nt":
        TEMP_DIR = Path(os.environ.get("TEMP", "C:/Temp")) / "clipper"
    else:
        TEMP_DIR = Path("/tmp/clipper")

# Lifecycle marker paths — the local work dir on bare metal.
PIPELINE_PID_PATH = str(TEMP_DIR / "pipeline.pid")
PIPELINE_DONE_PATH = str(TEMP_DIR / "pipeline.done")

STAGE_FILE = TEMP_DIR / "pipeline_stage.txt"
VOD_FILE = TEMP_DIR / "pipeline_vod.txt"     # per-VOD batch progress ({name,index,total})
LOG_FILE = TEMP_DIR / "pipeline.log"
STAGES_LOG = TEMP_DIR / "pipeline_stages.log"

# Pipeline process state
pipeline_process = None
pipeline_lock = threading.Lock()
pipeline_vod_name = None

# Reference Lab (R6) background job handle: {"name", "proc", "started", "log", "_lf"}
# or None. One at a time, mutually exclusive with the clip pipeline (GPU/LM Studio
# contention). See dashboard/routes/reference_routes.py + pipeline_runner.is_reference_running.
reference_job = None

# --- Default configuration constants ---
DEFAULT_ORIGINALITY = {
    "framing": "blur_fill",
    "originality": True,
    "stitch": False,
    "arc_stitch": False,
    "jump_cuts": "off",
    "cut_style": "auto",
    "flash_cuts": False,
    "narrative": True,
    "camera_pan": False,
    "tts_vo": False,
    "music_bed": "",
    "music_tier_c": False,
    # 2026-05-02: AI editing profiles toggle (per-category zoom punches,
    # freeze frames, slow-mo, meme cutaways, B-roll inserts, SFX cues,
    # kinetic captions, and audio + container fingerprint perturbation).
    # ON by default since 2026-07-10 (owner promotion after the 9/9-GOOD A/B
    # run; the SFX + A/B-variant lanes require profile mode). When on, Stage 7
    # dispatches each clip through scripts/lib/profile_render.py.
    "style_profiles": True,
    # 2026-06-13: cold-open teaser (concepts/hook-engineering-2026-06). Prepends
    # a ~1-2s tease of the run-up to the payoff + whoosh/flash into the clip.
    # Off by default — a Stage 7 post-step, failure-soft (keeps the original
    # clip if the teaser can't be built).
    "cold_open": False,
}

DEFAULT_MODELS = {
    "text_model": "qwen/qwen3.5-9b",
    "vision_model": "qwen/qwen3.6-35b-a3b",
    "whisper_model": "large-v3-turbo",
    "llm_url": "http://host.docker.internal:1234",
    "context_length": 8192,
}

DEFAULT_HARDWARE = {
    "whisper_device": "cuda",
}

# Two-phase model architecture (speed-wave3, 2026-07-15): the pipeline runs a
# SMALL fast model for the text phase and a BIG quality model for the vision
# phase, swapping at the stage boundary (~25 s). On dual-vendor GPU rigs the
# text model additionally runs on the NVIDIA-only CUDA lane (see hw_profile /
# the Hardware panel). One dropdown per PHASE: text_model governs Stages 3-4
# (text_model_passb stays null and inherits it), vision_model governs 5.5-6.
MODEL_ROLES = {
    "text_model": {
        "label": "Text-phase model (fast — Stages 3–4)",
        "description": "The SMALL fast model: segment votes, chunk cards, moment detection, "
                       "grounding judges. Speed matters most here (~100+ calls per VOD). "
                       "On a dual-GPU rig it runs on the NVIDIA-only CUDA lane. Needs solid "
                       "JSON output; a thinking-locked model is rejected at run start (BUG 67 guard).",
        "provider": "lmstudio",
    },
    "vision_model": {
        "label": "Vision-phase model (quality — Stages 5.5–6)",
        "description": "The BIG quality model: the Vision Judge ranking clips from frames, and "
                       "enrichment writing titles/hooks/descriptions plus the caption gates. "
                       "Parameter count earns its cost here. Runs on the pooled GPUs (dual-GPU "
                       "Vulkan on this rig — the only way a 22 GB model fits). Must support image input.",
        "provider": "lmstudio",
    },
    "whisper_model": {
        "label": "Whisper Model",
        "description": "Audio transcription (Stage 2, via WhisperX with alignment + speaker "
                       "diarization) and the master caption timing. Runs on CUDA when available.",
        "provider": "whisper",
    },
}

SUGGESTED_MODELS = {
    "text_model": {
        "id": "qwen/qwen3.5-9b",
        "reason": "The tested default for the fast text phase — measured 6.4× per Pass-B call "
                  "vs the unified 35B (3.6× model size + 1.8× CUDA lane). Fits the 16 GB NVIDIA "
                  "card with headroom. Swap for another SMALL model (e.g. a Gemma-4 e-class) if "
                  "you want to compare finders — the run-start no-think probe rejects "
                  "incompatible thinking models fast instead of wedging Stage 4.",
        "alternatives": ["google/gemma-4-e4b", "qwen/qwen3-8b"],
    },
    "vision_model": {
        "id": "qwen/qwen3.6-35b-a3b",
        "reason": "The unified multimodal 35B (MoE, ~3B active) — the quality ceiling for judge "
                  "ranking and title/hook voice, pooled across both GPUs. Picking the SAME model "
                  "as the text phase skips the phase swap but gives up the fast text lane.",
        "alternatives": ["qwen/qwen3-vl-8b"],
    },
    "whisper_model": {
        "id": "large-v3-turbo",
        "reason": "Distilled large-v3: ~2.5x faster transcription (the slowest non-LLM stage) for <1% WER loss, and ~half the VRAM (~1.6 GB). Switch to large-v3 for noisy / accented / overlapping-speech VODs where the accuracy ceiling matters.",
        "alternatives": ["large-v3"],
    },
}

# Context-length dropdown tiers. The per-model "recommended" value is computed
# dynamically by /api/models/context-recommendation (GGUF-exact KV cache + live
# VRAM) and shown as a separate line under the dropdown — these labels are just
# the selectable tiers and are intentionally model-AGNOSTIC (KV cache size
# varies ~10x by architecture, so a fixed "~N GB" claim was misleading).
#
# 2026-06-06: removed the hardcoded "8192 ⭐ recommended" star — it was wrong.
# 8192 is too small for Pass B (its prompt ~5k tokens + generation can exceed
# 8192 on long chunks → silent truncation). 16384 is the practical floor.
CONTEXT_LENGTH_GUIDE = [
    {"value": 8192,   "label": "8192 — tight (⚠ risks Pass B truncation)"},
    {"value": 16384,  "label": "16384 — Pass B safe floor"},
    {"value": 32768,  "label": "32768 — comfortable (pipeline default)"},
    {"value": 65536,  "label": "65536 — large (needs headroom)"},
    {"value": 131072, "label": "131072 — very large (verify it fits)"},
]

WHISPER_MODELS = [
    {"name": "large-v3-turbo", "size": "~1.6 GB", "description": "Distilled large-v3 — ~2.5x faster, <1% WER loss. Default/recommended"},
    {"name": "large-v3", "size": "~3 GB", "description": "Quality ceiling — safest on noisy / accented / overlapping-speech audio"},
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
