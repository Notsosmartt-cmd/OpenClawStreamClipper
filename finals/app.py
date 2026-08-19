#!/usr/bin/env python3
"""Finals Clipper dashboard — entrypoint.

A separate sibling app to the main dashboard and the Buffer poster (owner ask
2026-08-19: "served on a different port so I don't get them confused"):
started the same way (`.venv\\Scripts\\python.exe finals\\app.py` or
start-finals.cmd) but on its own port — default 5200, pin with FINALS_PORT.
5200 sits outside the dashboard's 5001..5013 roll-forward range and away from
the poster's 5100.
"""
from __future__ import annotations

import os
import socket
import sys

if __package__ in (None, ""):
    _here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, os.path.dirname(_here))

from flask import Flask, jsonify, send_from_directory  # noqa: E402

from finals import _state  # noqa: E402
from finals.routes import bp  # noqa: E402


def create_app() -> Flask:
    app = Flask(__name__)

    @app.route("/")
    def index():
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

    app.register_blueprint(bp)

    @app.errorhandler(404)
    def _not_found(e):
        return jsonify({"error": "Not found"}), 404

    @app.errorhandler(500)
    def _internal_error(e):
        return jsonify({"error": str(e)}), 500

    return app


app = create_app()


def _resolve_port(preferred: int, tries: int = 12) -> int:
    """Same bind-test roll-forward as dashboard/poster."""
    for p in range(preferred, preferred + tries):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("", p))
            return p
        except OSError:
            continue
        finally:
            s.close()
    return preferred


if __name__ == "__main__":
    print("THE FINALS Clipper")
    print(f"  VODs dir:   {_state.VODS_DIR}")
    print(f"  Output dir: {_state.OUT_ROOT}")
    _state.OUT_ROOT.mkdir(exist_ok=True)
    preferred = int(os.environ.get("FINALS_PORT") or 5200)
    port = _resolve_port(preferred)
    if port != preferred:
        print(f"  [port] {preferred} is in use — using {port}. "
              f"Set FINALS_PORT to pin a port.")
    print(f"  Finals Clipper ready at http://127.0.0.1:{port}")
    app.run(host="0.0.0.0", port=port, threaded=True)
