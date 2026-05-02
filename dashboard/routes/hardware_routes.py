"""Hardware configuration + restart routes.

Extracted from dashboard/app.py as part of Phase C.
"""
from __future__ import annotations

import subprocess

from flask import Blueprint, jsonify, request

from .. import _state
from ..config_io import load_hardware_config, save_hardware_config
from ..pipeline_runner import is_pipeline_running

bp = Blueprint("hardware_routes", __name__)


def _detect_capabilities() -> dict:
    """Probe GPU capabilities available inside the container."""
    caps = {"cuda": False, "vulkan": False, "nvidia_smi": False, "vulkaninfo": False}
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                           capture_output=True, text=True, timeout=5)
        if r.returncode == 0 and r.stdout.strip():
            caps["cuda"] = True
            caps["nvidia_smi"] = True
            caps["nvidia_gpus"] = [g.strip() for g in r.stdout.strip().splitlines()]
    except Exception:
        pass
    try:
        r = subprocess.run(["vulkaninfo", "--summary"], capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            caps["vulkan"] = True
            caps["vulkaninfo"] = True
    except Exception:
        pass
    return caps


@bp.route("/api/hardware")
def api_hardware():
    config = load_hardware_config()
    caps = _detect_capabilities() if _state.INSIDE_DOCKER else {}
    return jsonify({
        "config": config,
        "defaults": _state.DEFAULT_HARDWARE,
        "capabilities": caps,
        "restart_required": False,
    })


@bp.route("/api/restart", methods=["POST"])
def api_restart():
    """Restart Docker services to apply hardware config changes."""
    if is_pipeline_running():
        return jsonify({"error": "Pipeline is running — stop it before restarting"}), 409

    if _state.INSIDE_DOCKER:
        return jsonify({
            "error": "Cannot restart from inside Docker. Run:  docker compose restart"
        }), 400

    try:
        result = subprocess.run(
            ["docker", "compose", "restart"],
            capture_output=True, text=True, timeout=120,
            cwd=str(_state.PROJECT_DIR),
        )
        if result.returncode == 0:
            return jsonify({"status": "restarting",
                            "message": "Services restarting — page will reload once done"})
        else:
            err = (result.stderr or result.stdout or "docker compose restart failed").strip()
            return jsonify({"error": err}), 500
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Restart timed out (120 s) — check Docker Desktop"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/api/hardware", methods=["PUT"])
def api_hardware_update():
    data = request.get_json(force=True)
    config = load_hardware_config()

    if "whisper_device" in data:
        config["whisper_device"] = data["whisper_device"]

    save_hardware_config(config)
    return jsonify({
        "status": "saved",
        "config": config,
        "restart_required": False,
    })
