"""Model configuration routes.

Extracted from dashboard/app.py as part of Phase C.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from .. import _state
from ..config_io import load_models_config, save_models_config
from ..pipeline_runner import query_lm_studio_models

bp = Blueprint("models_routes", __name__)


@bp.route("/api/models")
def api_models():
    """Return current model configuration with role metadata."""
    config = load_models_config()
    roles = {}
    for key, meta in _state.MODEL_ROLES.items():
        roles[key] = {
            **meta,
            "current": config.get(key, _state.DEFAULT_MODELS.get(key, "")),
            "default": _state.DEFAULT_MODELS.get(key, ""),
        }
    return jsonify({
        "config": config,
        "roles": roles,
        "suggested": _state.SUGGESTED_MODELS,
        "context_length_guide": _state.CONTEXT_LENGTH_GUIDE,
    })


@bp.route("/api/models/available")
def api_models_available():
    """Query LM Studio for loaded models plus Whisper options."""
    return jsonify({
        "lmstudio": query_lm_studio_models(),
        "whisper": _state.WHISPER_MODELS,
    })


@bp.route("/api/models", methods=["PUT"])
def api_models_update():
    """Update model configuration."""
    data = request.get_json(force=True)
    config = load_models_config()

    changed = []
    for key in ("text_model", "vision_model", "whisper_model"):
        if key in data and data[key] != config.get(key):
            old = config.get(key, "")
            config[key] = data[key]
            changed.append({"role": key, "old": old, "new": data[key]})
    if "context_length" in data:
        try:
            ctx = int(data["context_length"])
            if ctx != config.get("context_length"):
                config["context_length"] = ctx
                changed.append({"role": "context_length", "old": config.get("context_length", 8192), "new": ctx})
        except (ValueError, TypeError):
            pass

    save_models_config(config)
    return jsonify({"status": "saved", "config": config, "changed": changed})
