"""Pipeline lifecycle + status + log-stream + diagnostics routes.

URL prefix: none (routes registered at root /api/* paths).

Extracted from dashboard/app.py as part of Phase C.
"""
from __future__ import annotations

import json
import sys
import time

from flask import Blueprint, Response, jsonify, request

from .. import _state
from ..config_io import extract_originality_fields
from ..pipeline_runner import (
    check_lm_studio,
    is_pipeline_running,
    is_reference_running,
    kill_pipeline,
    read_persistent_log_path,
    spawn_pipeline,
    stop_running_pipeline,
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
    enable_thinking = bool(data.get("enable_thinking", False))
    companion_shorts = bool(data.get("companion_shorts", False))
    # A/B caption variants: checkbox → classic A/B (2) or off (0). Post kit =
    # per-platform clips/post_kits/"<title>.post.json". BOTH DEFAULT ON since
    # 2026-07-10 (owner promotion) — an absent field means on; the checkbox
    # sends an explicit false to disable. See plan-captions-and-ab-variants-2026-07.
    ab_variants = 2 if data.get("ab_variants", True) else 0
    post_kit = bool(data.get("post_kit", True))
    news_after = bool(data.get("news_after", False))   # end the run with a news compile
    # Pass B dead-chunk gate mode — UI dropdown sends one of
    # {off, multi, sample, strict}. Default "off" preserves selection
    # fidelity (no LLM calls skipped). See pipeline_runner.spawn_pipeline.
    passb_dead_gate = (data.get("passb_dead_gate") or "off").strip().lower()
    if passb_dead_gate not in ("off", "multi", "sample", "strict"):
        passb_dead_gate = "off"
    orig_override = extract_originality_fields(data)

    if not vod:
        return jsonify({"error": "No VOD specified"}), 400

    with _state.pipeline_lock:
        if is_pipeline_running():
            return jsonify({"error": "Pipeline already running"}), 409
        if is_reference_running():
            return jsonify({"error": "A Reference Lab job is running — wait for it to finish"}), 409

        for f in [_state.LOG_FILE, _state.STAGE_FILE, _state.STAGES_LOG]:
            if f.exists():
                f.unlink()

        if use_docker_exec():
            cmd = ["bash", _state.DOCKER_PIPELINE_SCRIPT, "--style", style, "--vod", vod]
        else:
            cmd = [sys.executable, _state.PIPELINE_SCRIPT, "--style", style, "--vod", vod]
        if force:
            cmd.append("--force")
        if type_hint:
            cmd.extend(["--type", type_hint])

        try:
            _state.pipeline_process = spawn_pipeline(
                cmd, captions=captions, speed=speed,
                hook_caption=hook_caption, originality=orig_override,
                passb_dead_gate=passb_dead_gate,
                enable_thinking=enable_thinking,
                companion_shorts=companion_shorts,
                ab_variants=ab_variants,
                post_kit=post_kit,
                news_after=news_after,
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
    enable_thinking = bool(data.get("enable_thinking", False))
    companion_shorts = bool(data.get("companion_shorts", False))
    # A/B caption variants: checkbox → classic A/B (2) or off (0). Post kit =
    # per-platform clips/post_kits/"<title>.post.json". BOTH DEFAULT ON since
    # 2026-07-10 (owner promotion) — an absent field means on; the checkbox
    # sends an explicit false to disable. See plan-captions-and-ab-variants-2026-07.
    ab_variants = 2 if data.get("ab_variants", True) else 0
    post_kit = bool(data.get("post_kit", True))
    news_after = bool(data.get("news_after", False))   # end the run with a news compile
    passb_dead_gate = (data.get("passb_dead_gate") or "off").strip().lower()
    if passb_dead_gate not in ("off", "multi", "sample", "strict"):
        passb_dead_gate = "off"
    orig_override = extract_originality_fields(data)

    with _state.pipeline_lock:
        if is_pipeline_running():
            return jsonify({"error": "Pipeline already running"}), 409
        if is_reference_running():
            return jsonify({"error": "A Reference Lab job is running — wait for it to finish"}), 409

        for f in [_state.LOG_FILE, _state.STAGE_FILE, _state.STAGES_LOG]:
            if f.exists():
                f.unlink()

        if use_docker_exec():
            force_flag = " --force" if force else ""
            cmd_str = (
                f'for vod in "/root/VODs"/*.mp4 "/root/VODs"/*.mkv; do '
                f'[ -f "$vod" ] || continue; '
                f'name=$(basename "$vod" | sed "s/\\.[^.]*$//"); '
                f'echo "=== Clipping $name ==="; '
                f'bash {_state.DOCKER_PIPELINE_SCRIPT} --style {style}{force_flag} --vod "$name"; '
                f'done'
            )
            cmd = ["bash", "-c", cmd_str]
        else:
            cmd = [sys.executable, _state.PIPELINE_SCRIPT, "--all", "--style", style]
            if force:
                cmd.append("--force")

        try:
            _state.pipeline_process = spawn_pipeline(
                cmd, captions=captions,
                speed=speed, hook_caption=hook_caption,
                originality=orig_override,
                passb_dead_gate=passb_dead_gate,
                enable_thinking=enable_thinking,
                companion_shorts=companion_shorts,
                ab_variants=ab_variants,
                post_kit=post_kit,
                news_after=news_after,
            )
        except RuntimeError as e:
            return jsonify({"error": str(e)}), 503

        _state.pipeline_vod_name = "all VODs"

    return jsonify({"status": "started", "mode": "all"}), 202


@bp.route("/api/clip-batch", methods=["POST"])
def api_clip_batch():
    """Clip a specific subset of VODs sequentially (dashboard multi-select).

    Accepts `vods`: a list of VOD stems. Validated against the VODs actually on
    disk (drops typos AND guards the docker shell loop against injection),
    preserving the caller's selection order. A 1-element list is fine — it just
    runs that one VOD through the same sequential loop."""
    data = request.get_json(force=True)
    requested = data.get("vods") or []
    if isinstance(requested, str):
        requested = [requested]
    requested = [str(v).strip() for v in requested if str(v).strip()]
    style = data.get("style", "auto").strip() or "auto"
    type_hint = data.get("type", "").strip()
    force = data.get("force", False)
    captions = data.get("captions", True)
    speed = str(data.get("speed", "1.0"))
    hook_caption = data.get("hook_caption", True)
    enable_thinking = bool(data.get("enable_thinking", False))
    companion_shorts = bool(data.get("companion_shorts", False))
    # A/B caption variants: checkbox → classic A/B (2) or off (0). Post kit =
    # per-platform clips/post_kits/"<title>.post.json". BOTH DEFAULT ON since
    # 2026-07-10 (owner promotion) — an absent field means on; the checkbox
    # sends an explicit false to disable. See plan-captions-and-ab-variants-2026-07.
    ab_variants = 2 if data.get("ab_variants", True) else 0
    post_kit = bool(data.get("post_kit", True))
    news_after = bool(data.get("news_after", False))   # end the run with a news compile
    passb_dead_gate = (data.get("passb_dead_gate") or "off").strip().lower()
    if passb_dead_gate not in ("off", "multi", "sample", "strict"):
        passb_dead_gate = "off"
    orig_override = extract_originality_fields(data)

    if not requested:
        return jsonify({"error": "No VODs selected"}), 400

    # Keep only selections that exist on disk, in the user's chosen order.
    on_disk = {
        f.stem for f in sorted(_state.VODS_DIR.iterdir())
        if f.is_file() and f.suffix.lower() in (".mp4", ".mkv", ".avi", ".mov", ".webm")
    }
    vods = [v for v in requested if v in on_disk]
    if not vods:
        return jsonify({"error": "None of the selected VODs were found on disk"}), 400

    with _state.pipeline_lock:
        if is_pipeline_running():
            return jsonify({"error": "Pipeline already running"}), 409
        if is_reference_running():
            return jsonify({"error": "A Reference Lab job is running — wait for it to finish"}), 409

        for f in [_state.LOG_FILE, _state.STAGE_FILE, _state.STAGES_LOG]:
            if f.exists():
                f.unlink()

        if use_docker_exec():
            force_flag = " --force" if force else ""
            type_flag = f" --type {type_hint}" if type_hint else ""
            steps = []
            for v in vods:
                safe = v.replace("'", "'\\''")  # validated stem; single-quote anyway
                steps.append(
                    f'echo "=== Clipping {safe} ==="; '
                    f'bash {_state.DOCKER_PIPELINE_SCRIPT} --style {style}{force_flag}{type_flag} --vod \'{safe}\''
                )
            cmd = ["bash", "-c", "; ".join(steps)]
        else:
            cmd = [sys.executable, _state.PIPELINE_SCRIPT,
                   "--style", style, "--vods", ",".join(vods)]
            if force:
                cmd.append("--force")
            if type_hint:
                cmd.extend(["--type", type_hint])

        try:
            _state.pipeline_process = spawn_pipeline(
                cmd, captions=captions, speed=speed,
                hook_caption=hook_caption, originality=orig_override,
                passb_dead_gate=passb_dead_gate,
                enable_thinking=enable_thinking,
                companion_shorts=companion_shorts,
                ab_variants=ab_variants,
                post_kit=post_kit,
                news_after=news_after,
            )
        except RuntimeError as e:
            return jsonify({"error": str(e)}), 503

        _state.pipeline_vod_name = (vods[0] if len(vods) == 1
                                    else f"{len(vods)} VODs")

    return jsonify({"status": "started", "count": len(vods), "vods": vods}), 202


@bp.route("/api/news-compile", methods=["POST"])
def api_news_compile():
    """'Streamers Update' news compilation (plan-news-compilation-2026-07 v1).

    A SEPARATE explicit action on the multi-select (owner directive: never part
    of the standard clip flow). Compiles from FINISHED clips + diagnostics of
    already-clipped VODs — fast, no re-detection. Bare-metal only."""
    data = request.get_json(force=True)
    requested = data.get("vods") or []
    if isinstance(requested, str):
        requested = [requested]
    requested = [str(v).strip() for v in requested if str(v).strip()]
    if not requested:
        return jsonify({"error": "No VODs selected"}), 400
    if use_docker_exec():
        return jsonify({"error": "News compile runs bare-metal only"}), 501
    on_disk = {
        f.stem for f in sorted(_state.VODS_DIR.iterdir())
        if f.is_file() and f.suffix.lower() in (".mp4", ".mkv", ".avi", ".mov", ".webm")
    }
    vods = [v for v in requested if v in on_disk]
    if not vods:
        return jsonify({"error": "None of the selected VODs were found on disk"}), 400

    with _state.pipeline_lock:
        if is_pipeline_running():
            return jsonify({"error": "Pipeline already running"}), 409
        if is_reference_running():
            return jsonify({"error": "A Reference Lab job is running — wait for it to finish"}), 409
        script = str(_state.PROJECT_DIR / "scripts" / "news_compile.py")
        cmd = [sys.executable, script, "--vods", ",".join(vods)]
        try:
            _state.pipeline_process = spawn_pipeline(cmd)
        except RuntimeError as e:
            return jsonify({"error": str(e)}), 503
        _state.pipeline_vod_name = f"news compile ({len(vods)} VOD{'s' if len(vods) > 1 else ''})"
    return jsonify({"status": "started", "mode": "news", "vods": vods}), 202


@bp.route("/api/stop", methods=["POST"])
def api_stop():
    """Stop the running pipeline."""
    if not is_pipeline_running():
        return jsonify({"error": "No pipeline running"}), 404

    # Cross-process stop: kills our own handle AND/OR the pid-marker process (so a
    # dashboard restarted to change a setting can still stop the run it didn't launch).
    stop_running_pipeline()
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
