"""VOD listing and clip serving routes.

Extracted from dashboard/app.py as part of Phase C.
"""
from __future__ import annotations

import json
import subprocess
import time

from flask import Blueprint, jsonify, send_from_directory

from .. import _state

bp = Blueprint("vods_routes", __name__)


def _get_vod_duration(filepath) -> int:
    """Get video duration in minutes via ffprobe."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", str(filepath)],
            capture_output=True, text=True, timeout=15,
        )
        data = json.loads(result.stdout)
        seconds = float(data.get("format", {}).get("duration", 0))
        return round(seconds / 60)
    except Exception:
        return 0


def _get_processed_entries() -> dict:
    """Parse processed.log and return dict of processed VOD basenames."""
    entries = {}
    if _state.PROCESSED_LOG.exists():
        for line in _state.PROCESSED_LOG.read_text(encoding="utf-8", errors="replace").strip().splitlines():
            parts = line.split("\t")
            if parts:
                name = parts[0].strip()
                if name:
                    entries[name] = {
                        "date": parts[1] if len(parts) > 1 else "",
                        "clips": parts[2] if len(parts) > 2 else "",
                        "style": parts[3] if len(parts) > 3 else "",
                    }
    return entries


@bp.route("/api/vods")
def api_vods():
    """List all VODs with metadata."""
    vods = []
    processed = _get_processed_entries()

    for f in sorted(_state.VODS_DIR.iterdir()):
        if f.suffix.lower() not in (".mp4", ".mkv", ".avi", ".mov", ".webm"):
            continue
        if not f.is_file():
            continue

        stem = f.stem
        size_mb = round(f.stat().st_size / (1024 * 1024))
        duration_min = _get_vod_duration(f)

        cached_json = _state.TRANSCRIPTION_DIR / f"{stem}.transcript.json"
        cached_srt = _state.TRANSCRIPTION_DIR / f"{stem}.transcript.srt"
        has_cache = cached_json.exists() and cached_srt.exists()

        proc_info = processed.get(f.name)

        vods.append({
            "name": f.name,
            "stem": stem,
            "size_mb": size_mb,
            "duration_min": duration_min,
            "processed": proc_info is not None,
            "processed_info": proc_info,
            "transcription_cached": has_cache,
        })
    return jsonify(vods)


@bp.route("/api/clips")
def api_clips():
    """List generated clips."""
    clips = []
    if not _state.CLIPS_DIR.exists():
        return jsonify(clips)

    for f in sorted(_state.CLIPS_DIR.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if f.suffix.lower() not in (".mp4", ".mkv", ".webm"):
            continue
        if not f.is_file():
            continue

        stat = f.stat()
        clips.append({
            "name": f.name,
            "size_mb": round(stat.st_size / (1024 * 1024), 1),
            "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.st_mtime)),
        })
    return jsonify(clips)


@bp.route("/api/clips/<path:filename>")
def serve_clip(filename):
    """Serve a clip file for preview/download."""
    return send_from_directory(str(_state.CLIPS_DIR), filename)
