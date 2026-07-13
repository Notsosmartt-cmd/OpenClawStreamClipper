"""Config-IO helpers for the dashboard.

Pure I/O wrappers around config/{models,hardware,paths,originality}.json.
No Flask imports — safe to call from any module. Defaults live in
dashboard/_state.py.

Extracted from dashboard/app.py as part of Phase C.
"""
from __future__ import annotations

import json
from typing import Any

from . import _state


def load_originality_config() -> dict:
    cfg = dict(_state.DEFAULT_ORIGINALITY)
    if _state.ORIGINALITY_CONFIG.exists():
        try:
            with open(_state.ORIGINALITY_CONFIG, "r") as f:
                disk = json.load(f)
            if isinstance(disk, dict):
                for k, v in disk.items():
                    if k in cfg:
                        cfg[k] = v
        except Exception:
            pass
    return cfg


def save_originality_config(cfg: dict) -> None:
    _state.ORIGINALITY_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    with open(_state.ORIGINALITY_CONFIG, "w") as f:
        json.dump(cfg, f, indent=2)


def originality_to_env(orig: dict) -> dict:
    """Turn an originality-config dict into CLIP_* env vars the pipeline reads."""
    return {
        "CLIP_ORIGINALITY": "true" if orig.get("originality", True) else "false",
        "CLIP_FRAMING": str(orig.get("framing", "smart_crop")),
        "CLIP_STITCH": "true" if orig.get("stitch") else "false",
        "CLIP_NARRATIVE": "true" if orig.get("narrative", True) else "false",
        "CLIP_CAMERA_PAN": "true" if orig.get("camera_pan") else "false",
        "CLIP_TTS_VO": "true" if orig.get("tts_vo") else "false",
        "CLIP_MUSIC_BED": str(orig.get("music_bed", "") or ""),
        "CLIP_MUSIC_TIER_C": "true" if orig.get("music_tier_c") else "false",
        "CLIP_STYLE_PROFILES": "true" if orig.get("style_profiles") else "false",
        # Fix 3 arc setup->payoff stitch. Enabling it also LOOSENS the arc
        # guarantee floor (0.6 -> 0.45) so the top A1/M3 arc actually reaches the
        # final selection — otherwise arc-stitch has no arc to act on (on rich
        # VODs strong Pass B moments out-score the dedicated arcs at 0.6).
        "CLIP_ARC_STITCH": "true" if orig.get("arc_stitch") else "false",
        "CLIP_ARC_GUARANTEE_MIN_RATIO": "0.45" if orig.get("arc_stitch") else "0.6",
        # Transition animations (Stage 7d.5). jump_cuts: off|gaps|llm|on
        # (gaps = drop silence only, safe; on = silence + text-anchored cuts).
        # cut_style: auto|hard|fadewhite join look (v2 J4). flash_cuts: white-flash
        # engagement beats (seeded cadence + LLM picks).
        "CLIP_JUMP_CUTS": str(orig.get("jump_cuts", "off") or "off"),
        "CLIP_CUT_STYLE": str(orig.get("cut_style", "auto") or "auto"),
        "CLIP_FLASH_CUTS": "on" if orig.get("flash_cuts") else "off",
        # Cold-open teaser (concepts/hook-engineering-2026-06) — Stage 7 post-step.
        "CLIP_COLD_OPEN": "true" if orig.get("cold_open") else "false",
    }


def load_models_config() -> dict:
    if _state.MODELS_CONFIG.exists():
        try:
            with open(_state.MODELS_CONFIG, "r") as f:
                config = json.load(f)
            for k, v in _state.DEFAULT_MODELS.items():
                config.setdefault(k, v)
            return config
        except Exception:
            pass
    return dict(_state.DEFAULT_MODELS)


def save_models_config(config: dict) -> None:
    _state.MODELS_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    with open(_state.MODELS_CONFIG, "w") as f:
        json.dump(config, f, indent=2)


def load_hardware_config() -> dict:
    if _state.HARDWARE_CONFIG.exists():
        try:
            with open(_state.HARDWARE_CONFIG, "r") as f:
                config = json.load(f)
            for k, v in _state.DEFAULT_HARDWARE.items():
                config.setdefault(k, v)
            return config
        except Exception:
            pass
    return dict(_state.DEFAULT_HARDWARE)


def save_hardware_config(config: dict) -> None:
    _state.HARDWARE_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    with open(_state.HARDWARE_CONFIG, "w") as f:
        json.dump(config, f, indent=2)


def load_paths_config() -> dict:
    """Load folder configuration, returning current paths as defaults."""
    defaults = {"vods_dir": str(_state.VODS_DIR), "clips_dir": str(_state.CLIPS_DIR)}
    if _state.PATHS_CONFIG.exists():
        try:
            with open(_state.PATHS_CONFIG) as f:
                cfg = json.load(f)
            for k in defaults:
                cfg.setdefault(k, defaults[k])
            return cfg
        except Exception:
            pass
    return defaults


def save_paths_config(config: dict) -> None:
    _state.PATHS_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    with open(_state.PATHS_CONFIG, "w") as f:
        json.dump(config, f, indent=2)


def extract_originality_fields(data: dict) -> dict | None:
    """Pull a partial originality override from a POSTed payload.

    Returns a merged dict (disk defaults + POSTed overrides) only for the
    fields that were actually present — so a clip request that doesn't
    mention originality still uses the persisted config.
    """
    disk = load_originality_config()
    keys = ("framing", "originality", "stitch", "arc_stitch", "narrative",
            "camera_pan", "tts_vo", "music_bed", "music_tier_c",
            "style_profiles", "jump_cuts", "cut_style", "flash_cuts", "cold_open")
    touched = False
    merged = dict(disk)
    for k in keys:
        if k in data:
            merged[k] = data[k]
            touched = True
    return merged if touched else None
