#!/usr/bin/env python3
"""Buffer Clip Poster — entrypoint.

A separate sibling app to the dashboard (owner directive 2026-07-16): started
the same way (`.venv\\Scripts\\python.exe poster\\app.py` or start-poster.cmd)
but on its own port — default 5100, pin with POSTER_PORT. 5100 sits outside
the dashboard's 5001..5013 roll-forward range, so the two never collide.
"""
from __future__ import annotations

import os
import socket
import sys

# Runnable as `python poster/app.py` or `python -m poster.app` (same shim as
# dashboard/app.py — script mode doesn't have the parent dir on sys.path).
if __package__ in (None, ""):
    _here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, os.path.dirname(_here))

from flask import Flask, jsonify, send_from_directory  # noqa: E402

from poster import _state  # noqa: E402
from poster.routes import bp  # noqa: E402


def create_app() -> Flask:
    app = Flask(__name__)

    @app.route("/")
    def index():
        # no-cache on the HTML shell (same rationale as the dashboard: stale
        # markup after UI changes). Static JS/CSS cache normally.
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
    """Same roll-forward as dashboard/app.py: bind-test each port so a
    squatter makes us move instead of crashing with WSAEACCES."""
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
    print("Buffer Clip Poster")
    print(f"  Clips dir: {_state.CLIPS_DIR}")
    print(f"  Buffer key: {'found' if _state.load_api_key() else 'MISSING — drop it in BufferIOapiKey.txt'}")
    from poster import media_host
    print(f"  Media hosting: {'configured' if media_host.configured() else 'not configured (Setup panel)'}")
    preferred = int(os.environ.get("POSTER_PORT") or os.environ.get("PORT") or 5100)
    port = _resolve_port(preferred)
    if port != preferred:
        print(f"  [port] {preferred} is in use — using {port}. "
              f"Set POSTER_PORT to pin a port.")
    print(f"  Poster ready at http://127.0.0.1:{port}")
    app.run(host="0.0.0.0", port=port, threaded=True)
