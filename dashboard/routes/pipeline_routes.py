"""Pipeline lifecycle + status + log-stream + diagnostics routes.

URL prefix: none (routes registered at root /api/* paths).

Extracted from dashboard/app.py as part of Phase C.
"""
from __future__ import annotations

import json
import time

from flask import Blueprint, Response, jsonify, request

from .. import _state
from ..config_io import extract_originality_fields
from ..pipeline_runner import (
    check_lm_studio,
    is_pipeline_running,
    kill_pipeline,
    read_persistent_log_path,
    spawn_pipeline,
    use_docker_exec,
)

bp = Blueprint("pipeline_routes", __name__)


@bp.route("/api/status")
def api_status():
    """Pipeline running/idle status with Docker connectivity."""
    from ..pipeline_runner import get_docker_container
    running = is_pipeline_running()
    stage = ""
    if _state.STAGE_FILE.exists():
        try:
            stage = _state.STAGE_FILE.read_text(encoding="utf-8").strip()
        except Exception:
            pass

    docker_ok = True
    if use_docker_exec():
        docker_ok = get_docker_container() is not None

    lm_studio_ok = check_lm_studio()

    return jsonify({
        "running": running,
        "stage": stage,
        "vod": _state.pipeline_vod_name if running else None,
        "pid": _state.pipeline_process.pid if _state.pipeline_process and running else None,
        "mode": "docker" if use_docker_exec() else "local",
        "docker": docker_ok,
        "lm_studio": lm_studio_ok,
        "persistent_log": read_persistent_log_path(),
    })


@bp.route("/api/clip", methods=["POST"])
def api_clip():
    """Start clipping a specific VOD."""
    data = request.get_json(force=True)
    vod = data.get("vod", "").strip()
    style = data.get("style", "auto").strip() or "auto"
    type_hint = data.get("type", "").strip()
    force = data.get("force", False)
    captions = data.get("captions", True)
    speed = str(data.get("speed", "1.0"))
    hook_caption = data.get("hook_caption", True)
    orig_override = extract_originality_fields(data)

    if not vod:
        return jsonify({"error": "No VOD specified"}), 400

    with _state.pipeline_lock:
        if is_pipeline_running():
            return jsonify({"error": "Pipeline already running"}), 409

        for f in [_state.LOG_FILE, _state.STAGE_FILE, _state.STAGES_LOG]:
            if f.exists():
                f.unlink()

        cmd = ["bash", _state.PIPELINE_SCRIPT, "--style", style, "--vod", vod]
        if force:
            cmd.append("--force")
        if type_hint:
            cmd.extend(["--type", type_hint])

        try:
            _state.pipeline_process = spawn_pipeline(
                cmd, captions=captions, speed=speed,
                hook_caption=hook_caption, originality=orig_override,
            )
        except RuntimeError as e:
            return jsonify({"error": str(e)}), 503

        _state.pipeline_vod_name = vod

    return jsonify({"status": "started", "vod": vod, "pid": _state.pipeline_process.pid}), 202


@bp.route("/api/clip-all", methods=["POST"])
def api_clip_all():
    """Clip all VODs sequentially."""
    data = request.get_json(force=True) if request.data else {}
    style = data.get("style", "auto").strip() or "auto"
    force = data.get("force", False)
    captions = data.get("captions", True)
    speed = str(data.get("speed", "1.0"))
    hook_caption = data.get("hook_caption", True)
    orig_override = extract_originality_fields(data)

    with _state.pipeline_lock:
        if is_pipeline_running():
            return jsonify({"error": "Pipeline already running"}), 409

        for f in [_state.LOG_FILE, _state.STAGE_FILE, _state.STAGES_LOG]:
            if f.exists():
                f.unlink()

        force_flag = " --force" if force else ""

        if use_docker_exec():
            vods_path = "/root/VODs"
            script_path = _state.DOCKER_PIPELINE_SCRIPT
        else:
            vods_path = str(_state.VODS_DIR)
            script_path = _state.PIPELINE_SCRIPT

        cmd_str = (
            f'for vod in "{vods_path}"/*.mp4 "{vods_path}"/*.mkv; do '
            f'[ -f "$vod" ] || continue; '
            f'name=$(basename "$vod" | sed "s/\\.[^.]*$//"); '
            f'echo "=== Clipping $name ==="; '
            f'bash {script_path} --style {style}{force_flag} --vod "$name"; '
            f'done'
        )

        try:
            _state.pipeline_process = spawn_pipeline(
                ["bash", "-c", cmd_str], captions=captions,
                speed=speed, hook_caption=hook_caption,
                originality=orig_override,
            )
        except RuntimeError as e:
            return jsonify({"error": str(e)}), 503

        _state.pipeline_vod_name = "all VODs"

    return jsonify({"status": "started", "mode": "all"}), 202


@bp.route("/api/stop", methods=["POST"])
def api_stop():
    """Stop the running pipeline."""
    if not is_pipeline_running():
        return jsonify({"error": "No pipeline running"}), 404

    kill_pipeline(_state.pipeline_process)
    _state.pipeline_process = None
    _state.pipeline_vod_name = None
    return jsonify({"status": "stopped"})


@bp.route("/api/diagnostics")
def api_diagnostics():
    """Return the most recent diagnostics JSON."""
    if not _state.DIAGNOSTICS_DIR.exists():
        return jsonify(None)

    files = sorted(
        _state.DIAGNOSTICS_DIR.glob("*.json"),
        key=lambda f: f.stat().st_mtime, reverse=True,
    )
    if not files:
        return jsonify(None)

    try:
        return jsonify(json.loads(files[0].read_text()))
    except Exception:
        return jsonify(None)


@bp.route("/api/stages")
def api_stages():
    """Return stage history with timestamps."""
    stages = []
    if _state.STAGES_LOG.exists():
        try:
            for line in _state.STAGES_LOG.read_text(encoding="utf-8", errors="replace").strip().splitlines():
                parts = line.split(" ", 1)
                if len(parts) == 2:
                    stages.append({"time": parts[0], "stage": parts[1]})
        except Exception:
            pass
    return jsonify(stages)


@bp.route("/api/log/stream")
def log_stream():
    """SSE endpoint for live pipeline log."""
    STAGE_STALENESS_BEFORE_DONE_S = 30  # see BUG 31 commentary

    def generate():
        last_stage = ""
        last_pos = 0

        if _state.LOG_FILE.exists():
            last_pos = _state.LOG_FILE.stat().st_size

        while True:
            running = is_pipeline_running()

            if _state.STAGE_FILE.exists():
                try:
                    stage = _state.STAGE_FILE.read_text(encoding="utf-8").strip()
                    if stage != last_stage:
                        last_stage = stage
                        yield f"event: stage\ndata: {stage}\n\n"
                except Exception:
                    pass

            if _state.LOG_FILE.exists():
                try:
                    size = _state.LOG_FILE.stat().st_size
                    if size > last_pos:
                        with open(_state.LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
                            f.seek(last_pos)
                            new_data = f.read()
                            last_pos = f.tell()
                            for line in new_data.splitlines():
                                if line.strip():
                                    yield f"data: {line}\n\n"
                    elif size < last_pos:
                        last_pos = 0
                except Exception:
                    pass

            if not running and last_stage:
                stage_quiet = True
                try:
                    if _state.STAGE_FILE.exists():
                        age = time.time() - _state.STAGE_FILE.stat().st_mtime
                        stage_quiet = age >= STAGE_STALENESS_BEFORE_DONE_S
                except Exception:
                    pass
                if stage_quiet:
                    yield "event: done\ndata: Pipeline finished\n\n"
                    break

            time.sleep(0.5)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
