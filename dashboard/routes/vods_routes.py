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


_DURATION_CACHE: dict = {}  # (name, size_bytes) -> minutes; ffprobe once per file identity


def _get_vod_duration(filepath) -> int:
    """Get video duration in minutes via ffprobe (cached per (name, size))."""
    try:
        key = (filepath.name, filepath.stat().st_size)
    except OSError:
        key = None
    if key in _DURATION_CACHE:
        return _DURATION_CACHE[key]
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", str(filepath)],
            capture_output=True, text=True, timeout=15,
        )
        data = json.loads(result.stdout)
        seconds = float(data.get("format", {}).get("duration", 0))
        minutes = round(seconds / 60)
    except Exception:
        minutes = 0
    if key is not None and minutes:
        _DURATION_CACHE[key] = minutes
    return minutes


def _estimate_processing_minutes(duration_min: int, transcription_cached: bool) -> int:
    """Estimated end-to-end pipeline time for one VOD, from the 2026-07-16
    measured rates (wiki: plan-s45-text-judge / speed findings):
      per VOD-hour: S2 fresh 1.24 min (0 when the transcript is cached)
                    + S3 0.40 + S4 2.9  -> 4.54 fresh / 3.30 cached
      flats:        ~5.5 min (S1 + S4.5 judge + frames + seeded S5.5 tournament)
      S6+renders:   ~1.15 min per shipped clip x ~3.2 clips/VOD-hour
    Estimates, not promises — clip density is the biggest real-world swing.
    """
    if not duration_min:
        return 0
    h = duration_min / 60.0
    per_hour = 3.30 + (0.0 if transcription_cached else 1.24)
    return round(h * per_hour + 5.5 + h * 3.2 * 1.15)


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
            # estimated end-to-end pipeline minutes (owner req 2026-07-16);
            # the panel sums these for the library total
            "est_minutes": _estimate_processing_minutes(duration_min, has_cache),
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
