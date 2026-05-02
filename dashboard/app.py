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
        return send_from_directory(
            os.path.join(app.root_path, "templates"), "index.html",
        )

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


if __name__ == "__main__":
    print(f"Dashboard mode: {'Docker (local)' if _state.INSIDE_DOCKER else 'Windows host → docker exec'}")
    print(f"VODs dir: {_state.VODS_DIR}")
    print(f"Clips dir: {_state.CLIPS_DIR}")
    if use_docker_exec():
        c = get_docker_container()
        print(f"Docker container: {c or 'NOT FOUND — start Docker first!'}")
    app.run(host="0.0.0.0", port=5001, threaded=True)
