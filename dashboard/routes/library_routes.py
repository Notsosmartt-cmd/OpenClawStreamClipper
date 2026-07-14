"""Asset-library scan route — rebuilds library.json manifests under
``assets/`` from on-disk contents. Triggered by the dashboard's
"Scan Memes" button (and any future "Scan Libraries" UI action).

Wraps ``scripts/seed_libraries.py --scan``. Distinct from
``assets_routes.py`` which manages the Whisper / Piper model cache via
``scripts/lib/fetch_assets.py``.

Added 2026-05-02 as part of the AI editing-profiles wiring.
"""
from __future__ import annotations

import subprocess
import sys

from flask import Blueprint, jsonify

from .. import _state
from ..pipeline_runner import get_docker_container, use_docker_exec

bp = Blueprint("library_routes", __name__)


def _seed_script_path_local() -> str:
    return str(_state.PROJECT_DIR / "scripts" / "seed_libraries.py")


def _seed_script_path_container() -> str:
    return "/root/scripts/seed_libraries.py"


@bp.route("/api/libraries/scan", methods=["POST"])
def api_libraries_scan():
    """Run `seed_libraries.py --scan` to rebuild every library.json from disk."""
    if use_docker_exec():
        container = get_docker_container()
        if not container:
            return jsonify({"error": "Docker container not running"}), 503
        cmd = ["docker", "exec", container,
               "python3", _seed_script_path_container(), "--scan"]
    else:
        cmd = [_state.repo_python(), _seed_script_path_local(), "--scan"]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return jsonify({"error": "library scan timed out (120s)"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "scan failed").strip()[-400:]
        return jsonify({"error": err}), 500

    out = proc.stdout.strip().splitlines()
    rebuilt = [l.strip() for l in out if l.strip().startswith("ok    rebuilt")]
    summary = next((l.strip() for l in reversed(out)
                    if l.strip().startswith("scan complete")), "scan complete")
    return jsonify({
        "status": "scanned",
        "summary": summary,
        "rebuilt": rebuilt,
    })
