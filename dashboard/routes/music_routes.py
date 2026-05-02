"""Music-library scan route.

Extracted from dashboard/app.py as part of Phase C.
"""
from __future__ import annotations

import json
import subprocess
import sys

from flask import Blueprint, jsonify, request

from .. import _state
from ..pipeline_runner import get_docker_container, use_docker_exec

bp = Blueprint("music_routes", __name__)


@bp.route("/api/music/scan", methods=["POST"])
def api_music_scan():
    """Run librosa-based music analysis on the target folder."""
    data = request.get_json(force=True) or {}
    library = (data.get("library") or "").strip()
    if not library:
        return jsonify({"error": "library path is required"}), 400

    if use_docker_exec():
        container = get_docker_container()
        if not container:
            return jsonify({"error": "Docker container not running"}), 503
        cmd = [
            "docker", "exec", container,
            "python3", "/root/scripts/lib/scan_music.py", "--library", library,
        ]
    else:
        cmd = [
            sys.executable,
            str(_state.PROJECT_DIR / "scripts" / "lib" / "scan_music.py"),
            "--library", library,
        ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Music scan timed out (10 min)"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "scan failed").strip()[-400:]
        return jsonify({"error": err}), 500

    summary = {}
    for line in reversed(proc.stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                summary = json.loads(line)
                break
            except Exception:
                continue
    return jsonify({
        "status": "scanned",
        "count": summary.get("count", 0),
        "sidecar": summary.get("sidecar", ""),
    })
