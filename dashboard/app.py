#!/usr/bin/env python3
"""Stream Clipper Dashboard — Web UI for the clip pipeline.

Entry point. Boots Flask, registers blueprints, exposes /, /static/<file>
and global error handlers. The actual logic lives in:

    dashboard/_state.py          — shared mutable state
    dashboard/config_io.py       — config/*.json load/save helpers
    dashboard/pipeline_runner.py — pipeline lifecycle + Docker bridge
    dashboard/routes/*.py        — one blueprint per URL domain

Modularized in Phase C of the modularization plan. Behavior is unchanged
from the pre-split monolith — every route URL, status code, and JSON
response is byte-identical.
"""
from __future__ import annotations

import os
import socket
import sys

# Allow running as either `python3 dashboard/app.py` (script) or
# `python3 -m dashboard.app` (module). When run as a script the parent
# dir isn't on sys.path, so the `dashboard` package can't be imported.
if __package__ in (None, ""):
    _here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, os.path.dirname(_here))

from flask import Flask, jsonify, send_from_directory  # noqa: E402

from dashboard import _state  # noqa: E402
from dashboard.pipeline_runner import get_docker_container, use_docker_exec  # noqa: E402
from dashboard.routes import ALL_BLUEPRINTS  # noqa: E402


def create_app() -> Flask:
    app = Flask(__name__)

    @app.route("/")
    def index():
        # No-cache on the HTML shell — Flask's default send_from_directory
        # ships a 12-hour Cache-Control which made the dashboard ship stale
        # markup after every UI change (users had to hard-refresh to see new
        # controls). Static JS/CSS still cache normally.
        resp = send_from_directory(
            os.path.join(app.root_path, "templates"), "index.html",
        )
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
        return resp

    @app.route("/static/<path:filename>")
    def static_files(filename):
        return send_from_directory(
            os.path.join(app.root_path, "static"), filename,
        )

    for bp in ALL_BLUEPRINTS:
        app.register_blueprint(bp)

    @app.errorhandler(404)
    def _not_found(e):
        return jsonify({"error": "Not found"}), 404

    @app.errorhandler(405)
    def _method_not_allowed(e):
        return jsonify({"error": "Method not allowed"}), 405

    @app.errorhandler(500)
    def _internal_error(e):
        return jsonify({"error": str(e)}), 500

    return app


app = create_app()


def _resolve_port(preferred: int, tries: int = 12) -> int:
    """Return `preferred` if it can be bound, else the next free port (scanning
    up to `tries`). Tests the bind exactly as Flask will (IPv4 INADDR_ANY) so a
    port held by another app — e.g. a stale dashboard, or a background service
    squatting on 5001 — makes us roll forward instead of crashing with the
    Windows 'access to a socket … forbidden' (WSAEACCES) / address-in-use error.
    Pin a specific port with DASHBOARD_PORT (or PORT)."""
    for p in range(preferred, preferred + tries):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("", p))
            return p
        except OSError:
            continue
        finally:
            s.close()
    return preferred  # nothing free in range — let Flask surface the real error


if __name__ == "__main__":
    mode = ("Docker (inside container)" if _state.INSIDE_DOCKER
            else "Windows host → docker exec" if use_docker_exec()
            else "Windows host → native (bare-metal)")
    print(f"Dashboard mode: {mode}")
    print(f"VODs dir: {_state.VODS_DIR}")
    print(f"Clips dir: {_state.CLIPS_DIR}")
    if use_docker_exec():
        c = get_docker_container()
        print(f"Docker container: {c or 'NOT FOUND — start Docker first!'}")

    preferred = int(os.environ.get("DASHBOARD_PORT") or os.environ.get("PORT") or 5001)
    port = _resolve_port(preferred)
    if port != preferred:
        print(f"  [port] {preferred} is in use (another app is on it) — "
              f"using {port} instead. Set DASHBOARD_PORT to pin a port.")
    print(f"  Dashboard ready at http://127.0.0.1:{port}")
    app.run(host="0.0.0.0", port=port, threaded=True)
