"""Folder-path configuration routes (paths + browse-folder).

Extracted from dashboard/app.py as part of Phase C.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from flask import Blueprint, jsonify, request

from .. import _state
from ..config_io import load_paths_config, save_paths_config

bp = Blueprint("paths_routes", __name__)


def _update_docker_compose_mounts(vods_host: str, clips_host: str) -> bool:
    """Rewrite the volume-mount host paths in docker-compose.yml.

    Returns True if the file was modified (container restart required).
    """
    if not _state.DOCKER_COMPOSE_FILE.exists():
        return False

    try:
        content = _state.DOCKER_COMPOSE_FILE.read_text(encoding="utf-8")
        original = content

        vods_path = str(vods_host).replace("\\", "/")
        clips_path = str(clips_host).replace("\\", "/")

        try:
            if Path(vods_host).resolve() == (_state.PROJECT_DIR / "vods").resolve():
                vods_path = "./vods"
        except Exception:
            pass
        try:
            if Path(clips_host).resolve() == (_state.PROJECT_DIR / "clips").resolve():
                clips_path = "./clips"
        except Exception:
            pass

        content = re.sub(
            r"([ \t]*-[ \t]*)(\S+)(:/root/VODs/Clips_Ready)",
            lambda m: m.group(1) + clips_path + m.group(3),
            content,
        )
        content = re.sub(
            r"([ \t]*-[ \t]*)(\S+)(:/root/VODs)(?!/)",
            lambda m: m.group(1) + vods_path + m.group(3),
            content,
        )

        if content != original:
            _state.DOCKER_COMPOSE_FILE.write_text(content, encoding="utf-8")
            return True
    except Exception:
        pass

    return False


@bp.route("/api/paths")
def api_paths():
    """Return current folder configuration."""
    return jsonify(load_paths_config())


@bp.route("/api/paths", methods=["PUT"])
def api_paths_update():
    """Update folder configuration, reload path globals, and update docker-compose mounts."""
    data = request.get_json(force=True)
    config = load_paths_config()

    changed = False
    for key in ("vods_dir", "clips_dir"):
        val = (data.get(key) or "").strip()
        if val and val != config.get(key):
            config[key] = val
            changed = True

    restart_required = False
    if changed:
        save_paths_config(config)
        _state._reload_path_globals()
        try:
            _state.TRANSCRIPTION_DIR.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        if not _state.INSIDE_DOCKER:
            restart_required = _update_docker_compose_mounts(
                config["vods_dir"], config["clips_dir"]
            )

    return jsonify({
        "status": "saved" if changed else "unchanged",
        "config": load_paths_config(),
        "restart_required": restart_required,
    })


@bp.route("/api/browse-folder", methods=["POST"])
def api_browse_folder():
    """Open a native OS folder-picker dialog and return the selected path."""
    if _state.INSIDE_DOCKER:
        return jsonify({"error": "Folder browser not available inside Docker — type the path manually"}), 400

    data = request.get_json(force=True) or {}
    initial_dir = (data.get("initial_dir") or "").strip()
    if not initial_dir or not os.path.isdir(initial_dir):
        initial_dir = str(Path.home())

    script = (
        "import tkinter as tk; from tkinter import filedialog; "
        "root = tk.Tk(); root.withdraw(); root.wm_attributes('-topmost', True); "
        f"result = filedialog.askdirectory(initialdir={repr(initial_dir)}, title='Select Folder'); "
        "print(result or '', end='')"
    )

    try:
        proc = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, timeout=120,
        )
        return jsonify({"path": proc.stdout.strip()})
    except subprocess.TimeoutExpired:
        return jsonify({"path": ""})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
