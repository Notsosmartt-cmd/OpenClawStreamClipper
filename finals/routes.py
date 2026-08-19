"""API routes for the Finals dashboard."""
from __future__ import annotations

import json
import os
import re
import subprocess

from flask import Blueprint, jsonify, request, send_from_directory

from . import _state, runner

bp = Blueprint("finals_api", __name__)


def _resolve_vod(raw: str) -> str | None:
    """Accept a bare filename from vods/ or any absolute/relative path."""
    raw = (raw or "").strip().strip('"')
    if not raw:
        return None
    cand = _state.VODS_DIR / raw
    if cand.exists() and cand.is_file():
        return str(cand)
    if os.path.isfile(raw):
        return os.path.abspath(raw)
    return None


@bp.route("/api/state")
def api_state():
    running = runner.is_running()
    vod = _state.job_meta.get("vod") or runner.marker_vod()
    return jsonify({
        "running": running,
        "vod": os.path.basename(vod) if (running and vod) else "",
        "params": _state.job_meta.get("params", {}),
        "progress": runner.progress() if (running or _state.LOG_FILE.exists()) else {},
        "last_exit": _state.last_exit,
    })


@bp.route("/api/vods")
def api_vods():
    items = []
    try:
        for p in sorted(_state.VODS_DIR.iterdir()):
            if p.suffix.lower() in _state.VIDEO_EXTS and p.is_file():
                items.append({"name": p.name, "size_gb": round(p.stat().st_size / 2**30, 2),
                              "mtime": int(p.stat().st_mtime)})
    except FileNotFoundError:
        pass
    items.sort(key=lambda d: -d["mtime"])
    return jsonify(items)


@bp.route("/api/run", methods=["POST"])
def api_run():
    data = request.get_json(silent=True) or {}
    with _state.job_lock:
        if runner.is_running():
            return jsonify({"error": "A Finals scan is already running"}), 409
        vod = _resolve_vod(data.get("vod", ""))
        if not vod:
            return jsonify({"error": f"VOD not found: {data.get('vod', '')!r}"}), 400
        params = {
            "pre": max(0.0, float(data.get("pre", 10))),
            "post": max(0.0, float(data.get("post", 5))),
            "end_cap": max(1.0, float(data.get("end_cap", 6))),
            "fps": min(6.0, max(0.5, float(data.get("fps", 2)))),
            "ocr": data.get("ocr") if data.get("ocr") in ("auto", "gpu", "cpu") else "auto",
            "revives": bool(data.get("revives", True)),
            "endscreens": bool(data.get("endscreens", True)),
            "start": str(data.get("start") or "").strip(),
            "end": str(data.get("end") or "").strip(),
            "max_minutes": min(600.0, max(5.0, float(data.get("max_minutes", 240)))),
        }
        for key in ("start", "end"):
            if params[key] and not re.fullmatch(r"[\d:.]+", params[key]):
                return jsonify({"error": f"Bad {key} time: {params[key]!r}"}), 400
        runner.spawn(vod, params)
    return jsonify({"ok": True, "vod": os.path.basename(vod)})


@bp.route("/api/stop", methods=["POST"])
def api_stop():
    return jsonify({"stopped": runner.stop()})


@bp.route("/api/log")
def api_log():
    n = min(2000, max(10, int(request.args.get("tail", 300))))
    return runner.tail_log(n), 200, {"Content-Type": "text/plain; charset=utf-8"}


@bp.route("/api/results")
def api_results():
    """All run dirs under finals_clips/ with clip counts (newest first)."""
    runs = []
    try:
        for d in _state.OUT_ROOT.iterdir():
            if not d.is_dir() or d.name.startswith((".", "_")):
                continue
            ev = d / "events.json"
            entry = {"stem": d.name, "mtime": int(d.stat().st_mtime),
                     "clips": sum(1 for f in d.iterdir() if f.suffix == ".mp4"),
                     "has_events": ev.exists()}
            runs.append(entry)
    except FileNotFoundError:
        pass
    runs.sort(key=lambda r: -r["mtime"])
    return jsonify(runs)


@bp.route("/api/results/<stem>")
def api_result_detail(stem: str):
    d = _state.OUT_ROOT / os.path.basename(stem)
    if not d.is_dir():
        return jsonify({"error": "unknown run"}), 404
    payload = {}
    ev = d / "events.json"
    if ev.exists():
        try:
            payload = json.loads(ev.read_text(encoding="utf-8"))
        except Exception as e:
            payload = {"error": f"events.json unreadable: {e}"}
    files = []
    for f in sorted(d.iterdir()):
        if f.suffix == ".mp4":
            files.append({"file": f.name, "size_mb": round(f.stat().st_size / 2**20, 1)})
    payload["files"] = files
    payload["stem"] = d.name
    return jsonify(payload)


@bp.route("/clips/<path:subpath>")
def serve_clip(subpath: str):
    return send_from_directory(str(_state.OUT_ROOT), subpath, conditional=True)


@bp.route("/api/probe", methods=["POST"])
def api_probe():
    """Synchronous one-frame classification — the tuning loop for a new VOD:
    point it at a known revive timestamp and eyeball the annotated frame."""
    data = request.get_json(silent=True) or {}
    vod = _resolve_vod(data.get("vod", ""))
    if not vod:
        return jsonify({"error": f"VOD not found: {data.get('vod', '')!r}"}), 400
    t = str(data.get("t") or "").strip()
    if not re.fullmatch(r"[\d:.]+", t):
        return jsonify({"error": f"Bad time: {t!r}"}), 400
    try:
        r = subprocess.run(
            [str(_state.PYTHON), str(_state.RUNNER), "--vod", vod, "--probe", t,
             "--ocr", data.get("ocr") if data.get("ocr") in ("auto", "gpu", "cpu") else "auto"],
            capture_output=True, text=True, timeout=180, cwd=str(_state.PROJECT_DIR))
    except subprocess.TimeoutExpired:
        return jsonify({"error": "probe timed out"}), 500
    out = (r.stdout or "") + ("\n" + r.stderr if r.returncode != 0 and r.stderr else "")
    image = ""
    m = re.search(r"annotated frame -> (.+\.jpg)", out)
    if m:
        rel = os.path.relpath(m.group(1).strip(), str(_state.OUT_ROOT))
        image = "/clips/" + rel.replace("\\", "/")
    return jsonify({"ok": r.returncode == 0, "output": out.strip(), "image": image})
