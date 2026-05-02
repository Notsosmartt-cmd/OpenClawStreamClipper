"""Originality-config routes.

Extracted from dashboard/app.py as part of Phase C.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from .. import _state
from ..config_io import load_originality_config, save_originality_config

bp = Blueprint("originality_routes", __name__)


@bp.route("/api/originality")
def api_originality():
    """Return the persisted originality config."""
    return jsonify(load_originality_config())


@bp.route("/api/originality", methods=["PUT"])
def api_originality_update():
    """Persist originality toggles."""
    data = request.get_json(force=True) or {}
    cfg = load_originality_config()
    for k in _state.DEFAULT_ORIGINALITY:
        if k in data:
            cfg[k] = data[k]
    save_originality_config(cfg)
    return jsonify({"status": "saved", "config": cfg})
