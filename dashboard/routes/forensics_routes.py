"""Clip-forensics routes — drive scripts/research/clip_forensics.py from the UI.

Lets the owner pick a curated reference clip, run the offline decomposer
(audio_sense + visual_sense + censor/music + LLM style profile), and read the
timeline/style-profile back — so the forensics tool is iterable from the
dashboard's Forensics tab instead of the CLI. Mirrors the assets_routes pattern
(native subprocess on bare metal, docker-exec inside the container).

The decomposer is failure-soft and watchdog-bounded internally; this layer just
adds a generous outer timeout and parses the JSON it writes.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

from flask import Blueprint, jsonify, request

from .. import _state
from ..pipeline_runner import get_docker_container, use_docker_exec

bp = Blueprint("forensics_routes", __name__)

REF_DIR = _state.PROJECT_DIR / "reference_clips"
CACHE_DIR = REF_DIR / ".cache"
_VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}


def _timeline_path(stem: str):
    return CACHE_DIR / f"{stem}.timeline.json"


def _list_clips() -> list[dict]:
    """Reference clips on disk + whether each has a cached timeline."""
    out: list[dict] = []
    if not REF_DIR.is_dir():
        return out
    for f in sorted(REF_DIR.iterdir()):
        if not f.is_file() or f.suffix.lower() not in _VIDEO_EXT:
            continue
        tl = _timeline_path(f.stem)
        out.append({
            "name": f.name,
            "size_bytes": f.stat().st_size,
            "analyzed": tl.exists(),
            "analyzed_at": int(tl.stat().st_mtime) if tl.exists() else None,
        })
    return out


@bp.route("/api/forensics/clips")
def api_forensics_clips():
    """List curated reference clips + analysis status."""
    return jsonify({"dir": str(REF_DIR), "clips": _list_clips()})


@bp.route("/api/forensics/result")
def api_forensics_result():
    """Return a previously-cached timeline so the user can re-read without re-running."""
    clip = (request.args.get("clip") or "").strip()
    if not clip:
        return jsonify({"error": "clip is required"}), 400
    stem = os.path.splitext(os.path.basename(clip))[0]
    tl = _timeline_path(stem)
    if not tl.exists():
        return jsonify({"error": "not analyzed yet"}), 404
    try:
        return jsonify({"ok": True, "timeline": json.loads(tl.read_text(encoding="utf-8"))})
    except Exception as e:
        return jsonify({"error": f"unreadable cache: {e}"}), 500


@bp.route("/api/forensics/run", methods=["POST"])
def api_forensics_run():
    """Run clip_forensics.py on one reference clip and return its timeline JSON."""
    data = request.get_json(force=True) or {}
    clip = (data.get("clip") or "").strip()
    if not clip:
        return jsonify({"error": "clip is required"}), 400
    stem = os.path.splitext(os.path.basename(clip))[0]
    out_rel = f"reference_clips/.cache/{stem}.timeline.json"

    args = ["--clip", clip, "--out", out_rel,
            "--trim-end", str(float(data.get("trim_end") or 0)),
            "--trim-start", str(float(data.get("trim_start") or 0))]
    if data.get("ocr"):
        args.append("--ocr")
    if not data.get("llm", True):
        args.append("--no-llm")
    if data.get("cuda"):
        args.append("--cuda")

    # OpenMP guard (see entities/audio-sense-module) + quiet HF symlink warning.
    env = {**os.environ, "KMP_DUPLICATE_LIB_OK": "TRUE",
           "HF_HUB_DISABLE_SYMLINKS_WARNING": "1"}
    if use_docker_exec():
        container = get_docker_container()
        if not container:
            return jsonify({"ok": False, "error": "Docker container not running"}), 500
        cmd = ["docker", "exec", "-e", "KMP_DUPLICATE_LIB_OK=TRUE", container,
               "python3", "/root/scripts/research/clip_forensics.py"] + args
    else:
        cmd = [sys.executable,
               str(_state.PROJECT_DIR / "scripts" / "research" / "clip_forensics.py")] + args

    # Generous outer cap — the tool's own per-stage watchdog bounds each stage, so
    # this only catches a wholesale hang. LLM synthesis (first call loads the 35B)
    # is the slow part.
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1500, env=env)
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": "clip_forensics timed out (1500s)"}), 500
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    tl = _timeline_path(stem)
    if not tl.exists():
        tail = (proc.stderr or proc.stdout or "no output").strip()[-600:]
        return jsonify({"ok": False, "error": f"no timeline written.\n{tail}"}), 500
    try:
        timeline = json.loads(tl.read_text(encoding="utf-8"))
    except Exception as e:
        return jsonify({"ok": False, "error": f"unreadable timeline: {e}"}), 500
    # The trailing forensics summary line is handy context for the UI.
    summary = ""
    for line in reversed((proc.stderr or "").splitlines()):
        if "events=" in line:
            summary = line.strip()
            break
    return jsonify({"ok": True, "timeline": timeline, "summary": summary})
