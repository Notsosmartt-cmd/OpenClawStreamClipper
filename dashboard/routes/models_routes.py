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


@bp.route("/api/models/context-recommendation")
def api_context_recommendation():
    """Recommend a context_length for the given text+vision model pair on
    the current GPU pool. Reads exact KV-cache hyperparameters from each
    model's GGUF header (deterministic) and the live VRAM pool size.

    Query params: ``text_model``, ``vision_model`` (optional). Falls back
    to the saved config when params are absent.

    Returns ``{recommended, fit_class, constrained_by, pool_total_mb,
    consolidated, text:{...}, vision:{...}}`` or ``{error}`` when the
    prediction tooling isn't importable on this host.
    """
    import sys
    from pathlib import Path
    # scripts/lib on path so we can import the prediction modules.
    lib = Path(__file__).resolve().parents[2] / "scripts" / "lib"
    sys.path.insert(0, str(lib))
    try:
        import model_registry as _reg
        import vram_log as _vram
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": f"prediction tooling unavailable: {e}"}), 200

    config = load_models_config()
    text_model = request.args.get("text_model") or config.get("text_model", "")
    vision_model = request.args.get("vision_model") or config.get("vision_model", "")

    # Live pool size — prefer total VRAM across all detected adapters.
    try:
        snap = _vram.snapshot()
        pool_total = snap.get("pool_total_mb") or 0
        # Also expose the single largest (CUDA card) for the single-card view.
        nvidia_mb = 0
        for a in snap.get("adapters") or []:
            if a.get("vendor") == "NVIDIA":
                nvidia_mb = a.get("total_mb") or 0
                break
    except Exception:  # noqa: BLE001
        pool_total, nvidia_mb = 0, 0

    if not pool_total:
        return jsonify({"error": "could not determine GPU pool size"}), 200
    if not text_model:
        return jsonify({"error": "no text_model specified or configured"}), 200

    try:
        # Pass the CUDA card size so the recommendation can tell whether the
        # workload-optimal context keeps the model on the fast single-card
        # path vs. needing the Vulkan pool.
        combo = _reg.recommend_context_combo(
            text_model, vision_model or None, pool_total, cuda_card_mb=nvidia_mb)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": f"recommendation failed: {e}"}), 200

    combo["pool_total_mb"] = pool_total
    combo["nvidia_only_mb"] = nvidia_mb
    return jsonify(combo)


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
