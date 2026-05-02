"""Asset cache routes (Whisper + Piper download into host-mounted cache).

Extracted from dashboard/app.py as part of Phase C.
"""
from __future__ import annotations

import json
import subprocess
import sys

from flask import Blueprint, jsonify, request

from .. import _state
from ..pipeline_runner import get_docker_container, use_docker_exec

bp = Blueprint("assets_routes", __name__)


def _run_fetch_assets(args: list[str], timeout: int = 300) -> dict:
    """Invoke scripts/lib/fetch_assets.py and decode the JSON result."""
    if use_docker_exec():
        container = get_docker_container()
        if not container:
            return {"ok": False, "error": "Docker container not running"}
        cmd = ["docker", "exec", container,
               "python3", "/root/scripts/lib/fetch_assets.py"] + args
    else:
        cmd = [sys.executable,
               str(_state.PROJECT_DIR / "scripts" / "lib" / "fetch_assets.py")] + args

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"fetch_assets timed out after {timeout}s"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

    out = (proc.stdout or "").strip()
    if not out and proc.returncode != 0:
        return {"ok": False,
                "error": (proc.stderr or "fetch_assets returned no output").strip()[-400:]}
    try:
        parsed = json.loads(out)
        return parsed if isinstance(parsed, dict) else {"ok": False, "data": parsed}
    except json.JSONDecodeError:
        for line in reversed(out.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue
    return {"ok": False, "error": f"unparseable fetch_assets output: {out[-200:]}"}


@bp.route("/api/assets/status")
def api_assets_status():
    """Report what's cached on disk under models/{whisper,piper}."""
    result = _run_fetch_assets(["status"], timeout=30)
    if not isinstance(result, dict) or "whisper" not in result:
        return jsonify({"error": result.get("error", "status unavailable")}), 500
    return jsonify(result)


@bp.route("/api/assets/fetch", methods=["POST"])
def api_assets_fetch():
    """Download a Whisper model or Piper voice into the host-mounted cache."""
    data = request.get_json(force=True) or {}
    kind = (data.get("kind") or "").strip().lower()
    name = (data.get("name") or "").strip()
    if kind not in ("whisper", "piper"):
        return jsonify({"error": "kind must be 'whisper' or 'piper'"}), 400
    if not name:
        return jsonify({"error": "name is required"}), 400

    timeout = 1800 if kind == "whisper" else 300
    result = _run_fetch_assets([kind, name], timeout=timeout)
    status = 200 if result.get("ok") else 500
    return jsonify(result), status
