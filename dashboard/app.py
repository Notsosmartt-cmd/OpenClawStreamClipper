#!/usr/bin/env python3
"""Stream Clipper Dashboard — Web UI for the clip pipeline.

Runs standalone on Windows/Linux. When running outside Docker (e.g., on
a Windows host), the pipeline is executed inside the running Docker
container via 'docker exec'.
"""

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_from_directory

app = Flask(__name__)

# --- Environment detection ---
INSIDE_DOCKER = os.path.exists("/.dockerenv") or "DOCKER" in os.environ

# Paths — use project-level vods/ and clips/ directories
BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent
VODS_DIR = Path(os.environ.get("CLIP_VODS_DIR", str(PROJECT_DIR / "vods")))
CLIPS_DIR = Path(os.environ.get("CLIP_CLIPS_DIR", str(PROJECT_DIR / "clips")))
DIAGNOSTICS_DIR = CLIPS_DIR / ".diagnostics"
TRANSCRIPTION_DIR = VODS_DIR / ".transcriptions"
PROCESSED_LOG = VODS_DIR / "processed.log"
MODELS_CONFIG = PROJECT_DIR / "config" / "models.json"
HARDWARE_CONFIG = PROJECT_DIR / "config" / "hardware.json"

# Pipeline script paths
PIPELINE_SCRIPT = str(PROJECT_DIR / "scripts" / "clip-pipeline.sh")
DOCKER_PIPELINE_SCRIPT = "/root/scripts/clip-pipeline.sh"

# Temp dir for pipeline state files
if INSIDE_DOCKER:
    TEMP_DIR = Path("/tmp/clipper")
elif os.name == "nt":
    TEMP_DIR = Path(os.environ.get("TEMP", "C:/Temp")) / "clipper"
else:
    TEMP_DIR = Path("/tmp/clipper")

STAGE_FILE = TEMP_DIR / "pipeline_stage.txt"
LOG_FILE = TEMP_DIR / "pipeline.log"
STAGES_LOG = TEMP_DIR / "pipeline_stages.log"

# Ensure dirs exist
TRANSCRIPTION_DIR.mkdir(parents=True, exist_ok=True)
TEMP_DIR.mkdir(parents=True, exist_ok=True)

# Pipeline state
pipeline_process = None
pipeline_lock = threading.Lock()
pipeline_vod_name = None


# --- Docker helpers ---

def get_docker_container():
    """Find running stream-clipper container name."""
    try:
        result = subprocess.run(
            ["docker", "ps", "--filter", "name=stream-clipper", "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=5,
        )
        names = result.stdout.strip().splitlines()
        return names[0] if names else None
    except Exception:
        return None



def use_docker_exec():
    """Whether pipeline should run via docker exec (Windows host mode)."""
    return not INSIDE_DOCKER


# --- Pipeline management ---

def pipeline_env():
    """Build environment dict for direct pipeline subprocess (inside Docker)."""
    env = os.environ.copy()
    env["CLIP_VODS_DIR"] = str(VODS_DIR)
    env["CLIP_CLIPS_DIR"] = str(CLIPS_DIR)
    # Inject model selections from config
    config = load_models_config()
    env["CLIP_TEXT_MODEL"] = config.get("text_model", DEFAULT_MODELS["text_model"])
    env["CLIP_VISION_MODEL"] = config.get("vision_model", DEFAULT_MODELS["vision_model"])
    env["CLIP_WHISPER_MODEL"] = config.get("whisper_model", DEFAULT_MODELS["whisper_model"])
    env["CLIP_CONTEXT_LENGTH"] = str(config.get("context_length", DEFAULT_MODELS["context_length"]))
    return env


def spawn_pipeline(cmd):
    """Launch pipeline subprocess.

    Outside Docker: runs via 'docker exec' inside the container.
    Inside Docker:  runs bash directly.
    """
    if use_docker_exec():
        container = get_docker_container()
        if not container:
            raise RuntimeError(
                "No stream-clipper Docker container is running. "
                "Start it with: docker compose up -d"
            )

        # Inject model env vars into docker exec
        config = load_models_config()
        env_flags = [
            "-e", f"CLIP_TEXT_MODEL={config.get('text_model', DEFAULT_MODELS['text_model'])}",
            "-e", f"CLIP_VISION_MODEL={config.get('vision_model', DEFAULT_MODELS['vision_model'])}",
            "-e", f"CLIP_WHISPER_MODEL={config.get('whisper_model', DEFAULT_MODELS['whisper_model'])}",
            "-e", f"CLIP_CONTEXT_LENGTH={config.get('context_length', DEFAULT_MODELS['context_length'])}",
        ]

        # Build docker exec command
        if len(cmd) >= 2 and cmd[1] == "-c":
            # Shell command string (clip-all mode)
            docker_cmd = ["docker", "exec"] + env_flags + [container, "bash", "-c", cmd[2]]
        else:
            # Direct script: ["bash", LOCAL_SCRIPT, args...]
            args = cmd[2:]  # skip "bash" and local script path
            docker_cmd = [
                "docker", "exec",
            ] + env_flags + [
                container,
                "bash", DOCKER_PIPELINE_SCRIPT,
            ] + args

        # Pipe output to local log file for SSE streaming
        log_fh = open(LOG_FILE, "w", encoding="utf-8")

        kwargs = dict(
            stdout=log_fh,
            stderr=subprocess.STDOUT,
        )
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["preexec_fn"] = os.setsid

        proc = subprocess.Popen(docker_cmd, **kwargs)
        proc._log_fh = log_fh

        # Poll stage files from container in background
        threading.Thread(
            target=_poll_container_stages, args=(container, proc), daemon=True,
        ).start()

        return proc

    # Inside Docker — run directly
    kwargs = dict(
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=pipeline_env(),
    )
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["preexec_fn"] = os.setsid
    return subprocess.Popen(cmd, **kwargs)


def _poll_container_stages(container, proc):
    """Mirror stage files from Docker container to local temp dir."""
    remote_files = [
        ("/tmp/clipper/pipeline_stage.txt", STAGE_FILE),
        ("/tmp/clipper/pipeline_stages.log", STAGES_LOG),
    ]

    while proc.poll() is None:
        _read_remote_files(container, remote_files)
        time.sleep(2)

    # Final read after process ends
    _read_remote_files(container, remote_files)

    # Close log file handle
    if hasattr(proc, "_log_fh"):
        try:
            proc._log_fh.close()
        except Exception:
            pass


def _read_remote_files(container, file_pairs):
    """Read files from Docker container and write locally."""
    for remote_path, local_path in file_pairs:
        try:
            result = subprocess.run(
                ["docker", "exec", container, "cat", remote_path],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                local_path.write_text(result.stdout.strip(), encoding="utf-8")
        except Exception:
            pass


def kill_pipeline(proc):
    """Kill pipeline process tree across platforms."""
    try:
        if os.name == "nt":
            proc.terminate()
            # Also kill pipeline inside container
            if use_docker_exec():
                container = get_docker_container()
                if container:
                    subprocess.run(
                        ["docker", "exec", container, "pkill", "-f", "clip-pipeline"],
                        capture_output=True, timeout=5,
                    )
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        pass

    # Close log file handle
    if hasattr(proc, "_log_fh"):
        try:
            proc._log_fh.close()
        except Exception:
            pass

    for _ in range(10):
        if proc.poll() is not None:
            return
        time.sleep(0.5)

    try:
        proc.kill()
    except (ProcessLookupError, PermissionError, OSError):
        pass


def get_vod_duration(filepath):
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


def get_processed_entries():
    """Parse processed.log and return dict of processed VOD basenames."""
    entries = {}
    if PROCESSED_LOG.exists():
        for line in PROCESSED_LOG.read_text(encoding="utf-8", errors="replace").strip().splitlines():
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


def is_pipeline_running():
    """Check if a pipeline process is currently running."""
    global pipeline_process
    if pipeline_process is not None:
        if pipeline_process.poll() is None:
            return True
        pipeline_process = None
    return False


# --- Routes ---

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


@app.route("/api/vods")
def api_vods():
    """List all VODs with metadata."""
    vods = []
    processed = get_processed_entries()

    for f in sorted(VODS_DIR.iterdir()):
        if f.suffix.lower() not in (".mp4", ".mkv", ".avi", ".mov", ".webm"):
            continue
        if not f.is_file():
            continue

        stem = f.stem
        size_mb = round(f.stat().st_size / (1024 * 1024))
        duration_min = get_vod_duration(f)

        cached_json = TRANSCRIPTION_DIR / f"{stem}.transcript.json"
        cached_srt = TRANSCRIPTION_DIR / f"{stem}.transcript.srt"
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


@app.route("/api/status")
def api_status():
    """Pipeline running/idle status with Docker connectivity."""
    running = is_pipeline_running()
    stage = ""
    if STAGE_FILE.exists():
        try:
            stage = STAGE_FILE.read_text(encoding="utf-8").strip()
        except Exception:
            pass

    # Docker status (only relevant outside Docker)
    docker_ok = True
    if use_docker_exec():
        docker_ok = get_docker_container() is not None

    lm_studio_ok = check_lm_studio()

    return jsonify({
        "running": running,
        "stage": stage,
        "vod": pipeline_vod_name if running else None,
        "pid": pipeline_process.pid if pipeline_process and running else None,
        "mode": "docker" if use_docker_exec() else "local",
        "docker": docker_ok,
        "lm_studio": lm_studio_ok,
    })


@app.route("/api/clip", methods=["POST"])
def api_clip():
    """Start clipping a specific VOD."""
    global pipeline_process, pipeline_vod_name

    data = request.get_json(force=True)
    vod = data.get("vod", "").strip()
    style = data.get("style", "auto").strip() or "auto"
    type_hint = data.get("type", "").strip()
    force = data.get("force", False)

    if not vod:
        return jsonify({"error": "No VOD specified"}), 400

    with pipeline_lock:
        if is_pipeline_running():
            return jsonify({"error": "Pipeline already running"}), 409

        # Clear old state files
        for f in [LOG_FILE, STAGE_FILE, STAGES_LOG]:
            if f.exists():
                f.unlink()

        cmd = ["bash", PIPELINE_SCRIPT, "--style", style, "--vod", vod]
        if force:
            cmd.append("--force")
        if type_hint:
            cmd.extend(["--type", type_hint])

        try:
            pipeline_process = spawn_pipeline(cmd)
        except RuntimeError as e:
            return jsonify({"error": str(e)}), 503

        pipeline_vod_name = vod

    return jsonify({"status": "started", "vod": vod, "pid": pipeline_process.pid}), 202


@app.route("/api/clip-all", methods=["POST"])
def api_clip_all():
    """Clip all VODs sequentially."""
    global pipeline_process, pipeline_vod_name

    data = request.get_json(force=True) if request.data else {}
    style = data.get("style", "auto").strip() or "auto"
    force = data.get("force", False)

    with pipeline_lock:
        if is_pipeline_running():
            return jsonify({"error": "Pipeline already running"}), 409

        for f in [LOG_FILE, STAGE_FILE, STAGES_LOG]:
            if f.exists():
                f.unlink()

        force_flag = " --force" if force else ""

        # Use Docker paths when executing via docker exec
        if use_docker_exec():
            vods_path = "/root/VODs"
            script_path = DOCKER_PIPELINE_SCRIPT
        else:
            vods_path = str(VODS_DIR)
            script_path = PIPELINE_SCRIPT

        cmd_str = (
            f'for vod in "{vods_path}"/*.mp4 "{vods_path}"/*.mkv; do '
            f'[ -f "$vod" ] || continue; '
            f'name=$(basename "$vod" | sed "s/\\.[^.]*$//"); '
            f'echo "=== Clipping $name ==="; '
            f'bash {script_path} --style {style}{force_flag} --vod "$name"; '
            f'done'
        )

        try:
            pipeline_process = spawn_pipeline(["bash", "-c", cmd_str])
        except RuntimeError as e:
            return jsonify({"error": str(e)}), 503

        pipeline_vod_name = "all VODs"

    return jsonify({"status": "started", "mode": "all"}), 202


@app.route("/api/stop", methods=["POST"])
def api_stop():
    """Stop the running pipeline."""
    global pipeline_process, pipeline_vod_name

    if not is_pipeline_running():
        return jsonify({"error": "No pipeline running"}), 404

    kill_pipeline(pipeline_process)
    pipeline_process = None
    pipeline_vod_name = None
    return jsonify({"status": "stopped"})


@app.route("/api/clips")
def api_clips():
    """List generated clips."""
    clips = []
    if not CLIPS_DIR.exists():
        return jsonify(clips)

    for f in sorted(CLIPS_DIR.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
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


@app.route("/api/clips/<path:filename>")
def serve_clip(filename):
    """Serve a clip file for preview/download."""
    return send_from_directory(str(CLIPS_DIR), filename)


@app.route("/api/diagnostics")
def api_diagnostics():
    """Return the most recent diagnostics JSON."""
    if not DIAGNOSTICS_DIR.exists():
        return jsonify(None)

    files = sorted(
        DIAGNOSTICS_DIR.glob("*.json"),
        key=lambda f: f.stat().st_mtime, reverse=True,
    )
    if not files:
        return jsonify(None)

    try:
        return jsonify(json.loads(files[0].read_text()))
    except Exception:
        return jsonify(None)


@app.route("/api/stages")
def api_stages():
    """Return stage history with timestamps."""
    stages = []
    if STAGES_LOG.exists():
        try:
            for line in STAGES_LOG.read_text(encoding="utf-8", errors="replace").strip().splitlines():
                parts = line.split(" ", 1)
                if len(parts) == 2:
                    stages.append({"time": parts[0], "stage": parts[1]})
        except Exception:
            pass
    return jsonify(stages)


@app.route("/api/log/stream")
def log_stream():
    """SSE endpoint for live pipeline log."""
    def generate():
        last_stage = ""
        last_pos = 0

        if LOG_FILE.exists():
            last_pos = LOG_FILE.stat().st_size

        while True:
            running = is_pipeline_running()

            if STAGE_FILE.exists():
                try:
                    stage = STAGE_FILE.read_text(encoding="utf-8").strip()
                    if stage != last_stage:
                        last_stage = stage
                        yield f"event: stage\ndata: {stage}\n\n"
                except Exception:
                    pass

            if LOG_FILE.exists():
                try:
                    size = LOG_FILE.stat().st_size
                    if size > last_pos:
                        with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
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


# --- Model configuration ---

DEFAULT_MODELS = {
    "text_model": "qwen/qwen3.5-9b",
    "vision_model": "qwen/qwen3.5-9b",
    "whisper_model": "large-v3",
    "llm_url": "http://host.docker.internal:1234",
    "context_length": 8192,
}

# Pipeline role descriptions for the UI
MODEL_ROLES = {
    "text_model": {
        "label": "Text Model",
        "description": "Segment classification (Stage 3) and moment detection (Stage 4). Needs strong reasoning and JSON output.",
        "provider": "lmstudio",
    },
    "vision_model": {
        "label": "Vision Model",
        "description": "Frame analysis and clip title generation (Stage 6). Must support image input.",
        "provider": "lmstudio",
    },
    "whisper_model": {
        "label": "Whisper Model",
        "description": "Audio transcription (Stage 2) and clip captions (Stage 7). Runs via faster-whisper.",
        "provider": "whisper",
    },
}

# Recommended model IDs for each pipeline role.
# These are used to guide the user in the dashboard — shown with a ⭐ in the picker.
SUGGESTED_MODELS = {
    "text_model": {
        "id": "qwen/qwen3.5-9b",
        "reason": "Best reasoning + JSON output for moment detection. Also handles vision "
                  "(Stage 6) — use the same model for both roles to avoid VRAM swap. ~11 GB VRAM.",
    },
    "vision_model": {
        "id": "qwen/qwen3.5-9b",
        "reason": "qwen3.5-9b supports both text and vision — setting the same model for "
                  "both roles skips the Stage 5 unload/reload and saves ~2 min per run. "
                  "Use qwen/qwen3-vl-8b or qwen/qwen2.5-vl-7b if you prefer a dedicated vision model.",
        "alternatives": ["qwen/qwen3-vl-8b", "qwen/qwen2.5-vl-7b"],
    },
    "whisper_model": {
        "id": "large-v3",
        "reason": "Best transcription accuracy. Recommended for GPU. ~3 GB VRAM.",
    },
}

# Context length guidance per VRAM tier (informational only — actual value in models.json)
CONTEXT_LENGTH_GUIDE = [
    {"value": 4096,  "label": "4096 — ~2 GB KV cache  (8 GB VRAM total)"},
    {"value": 8192,  "label": "8192 — ~4 GB KV cache  (12 GB VRAM total) ⭐ recommended"},
    {"value": 16384, "label": "16384 — ~8 GB KV cache (20 GB VRAM total)"},
    {"value": 32768, "label": "32768 — ~16 GB KV cache (28 GB VRAM total)"},
]

WHISPER_MODELS = [
    {"name": "large-v3", "size": "~3 GB", "description": "Best accuracy, recommended for GPU"},
    {"name": "large-v2", "size": "~3 GB", "description": "Previous best, very accurate"},
    {"name": "medium", "size": "~1.5 GB", "description": "Good balance of speed and accuracy"},
    {"name": "small", "size": "~500 MB", "description": "Fast, decent accuracy"},
    {"name": "base", "size": "~150 MB", "description": "Very fast, lower accuracy"},
    {"name": "tiny", "size": "~75 MB", "description": "Fastest, lowest accuracy"},
]


def load_models_config():
    """Load model configuration from config/models.json."""
    if MODELS_CONFIG.exists():
        try:
            with open(MODELS_CONFIG, "r") as f:
                config = json.load(f)
            # Merge with defaults for any missing keys
            for k, v in DEFAULT_MODELS.items():
                config.setdefault(k, v)
            return config
        except Exception:
            pass
    return dict(DEFAULT_MODELS)


def save_models_config(config):
    """Save model configuration to config/models.json."""
    MODELS_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    with open(MODELS_CONFIG, "w") as f:
        json.dump(config, f, indent=2)


# LM Studio reachability cache — avoid hammering /v1/models on every 3-second status poll.
_lm_studio_cache: dict = {"ok": False, "ts": 0.0}
_LM_STUDIO_CACHE_TTL = 30  # seconds


def check_lm_studio():
    """Check if LM Studio server is reachable. Result cached for 30 s."""
    global _lm_studio_cache
    now = time.time()
    if now - _lm_studio_cache["ts"] < _LM_STUDIO_CACHE_TTL:
        return _lm_studio_cache["ok"]
    try:
        import urllib.request
        config = load_models_config()
        url = config.get("llm_url", "http://host.docker.internal:1234")
        req = urllib.request.Request(f"{url}/v1/models")
        with urllib.request.urlopen(req, timeout=5) as resp:
            ok = resp.status == 200
    except Exception:
        ok = False
    _lm_studio_cache = {"ok": ok, "ts": now}
    return ok


def query_lm_studio_models():
    """Query LM Studio for available models via OpenAI-compatible /v1/models."""
    try:
        import urllib.request
        config = load_models_config()
        url = config.get("llm_url", "http://host.docker.internal:1234")
        req = urllib.request.Request(f"{url}/v1/models")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        models = []
        for m in data.get("data", []):
            models.append({
                "name": m.get("id", ""),
                "size_gb": 0,
                "family": "",
                "parameter_size": "",
                "quantization": "",
            })
        return models
    except Exception as e:
        print(f"Failed to query LM Studio models: {e}")
        return []


@app.route("/api/models")
def api_models():
    """Return current model configuration with role metadata."""
    config = load_models_config()
    roles = {}
    for key, meta in MODEL_ROLES.items():
        roles[key] = {
            **meta,
            "current": config.get(key, DEFAULT_MODELS.get(key, "")),
            "default": DEFAULT_MODELS.get(key, ""),
        }
    return jsonify({
        "config": config,
        "roles": roles,
        "suggested": SUGGESTED_MODELS,
        "context_length_guide": CONTEXT_LENGTH_GUIDE,
    })


@app.route("/api/models/available")
def api_models_available():
    """Query LM Studio for loaded models plus Whisper options."""
    lm_studio_models = query_lm_studio_models()
    return jsonify({
        "lmstudio": lm_studio_models,
        "whisper": WHISPER_MODELS,
    })


@app.route("/api/models", methods=["PUT"])
def api_models_update():
    """Update model configuration."""
    data = request.get_json(force=True)
    config = load_models_config()

    changed = []
    for key in ("text_model", "vision_model", "whisper_model"):
        if key in data and data[key] != config.get(key):
            old = config.get(key, "")
            config[key] = data[key]
            changed.append({"role": key, "old": old, "new": data[key]})
    if "context_length" in data:
        try:
            ctx = int(data["context_length"])
            if ctx != config.get("context_length"):
                config["context_length"] = ctx
                changed.append({"role": "context_length", "old": config.get("context_length", 8192), "new": ctx})
        except (ValueError, TypeError):
            pass

    save_models_config(config)
    return jsonify({"status": "saved", "config": config, "changed": changed})


# --- Hardware configuration ---

DEFAULT_HARDWARE = {
    "whisper_device": "cuda",
}


def load_hardware_config():
    if HARDWARE_CONFIG.exists():
        try:
            with open(HARDWARE_CONFIG, "r") as f:
                config = json.load(f)
            for k, v in DEFAULT_HARDWARE.items():
                config.setdefault(k, v)
            return config
        except Exception:
            pass
    return dict(DEFAULT_HARDWARE)


def save_hardware_config(config):
    HARDWARE_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    with open(HARDWARE_CONFIG, "w") as f:
        json.dump(config, f, indent=2)


def detect_capabilities():
    """Probe GPU capabilities available inside the container."""
    caps = {"cuda": False, "vulkan": False, "nvidia_smi": False, "vulkaninfo": False}
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                           capture_output=True, text=True, timeout=5)
        if r.returncode == 0 and r.stdout.strip():
            caps["cuda"] = True
            caps["nvidia_smi"] = True
            caps["nvidia_gpus"] = [g.strip() for g in r.stdout.strip().splitlines()]
    except Exception:
        pass
    try:
        r = subprocess.run(["vulkaninfo", "--summary"], capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            caps["vulkan"] = True
            caps["vulkaninfo"] = True
    except Exception:
        pass
    return caps


@app.route("/api/hardware")
def api_hardware():
    config = load_hardware_config()
    caps = detect_capabilities() if INSIDE_DOCKER else {}
    return jsonify({
        "config": config,
        "defaults": DEFAULT_HARDWARE,
        "capabilities": caps,
        "restart_required": False,
    })


@app.route("/api/restart", methods=["POST"])
def api_restart():
    """Restart Docker services to apply hardware config changes.

    Only works when dashboard is running outside Docker (Windows host mode).
    Inside Docker, instruct the user to run 'docker compose restart' manually.
    """
    if is_pipeline_running():
        return jsonify({"error": "Pipeline is running — stop it before restarting"}), 409

    if INSIDE_DOCKER:
        return jsonify({
            "error": "Cannot restart from inside Docker. Run:  docker compose restart"
        }), 400

    try:
        # Run docker compose restart from the project directory so it finds
        # docker-compose.yml without needing --file flags.
        result = subprocess.run(
            ["docker", "compose", "restart"],
            capture_output=True, text=True, timeout=120,
            cwd=str(PROJECT_DIR),
        )
        if result.returncode == 0:
            return jsonify({"status": "restarting",
                            "message": "Services restarting — page will reload once done"})
        else:
            err = (result.stderr or result.stdout or "docker compose restart failed").strip()
            return jsonify({"error": err}), 500
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Restart timed out (120 s) — check Docker Desktop"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/hardware", methods=["PUT"])
def api_hardware_update():
    data = request.get_json(force=True)
    config = load_hardware_config()

    if "whisper_device" in data:
        config["whisper_device"] = data["whisper_device"]

    save_hardware_config(config)
    return jsonify({
        "status": "saved",
        "config": config,
        "restart_required": False,
    })


@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Not found"}), 404


@app.errorhandler(405)
def method_not_allowed(e):
    return jsonify({"error": "Method not allowed"}), 405


@app.errorhandler(500)
def internal_error(e):
    return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    print(f"Dashboard mode: {'Docker (local)' if INSIDE_DOCKER else 'Windows host → docker exec'}")
    print(f"VODs dir: {VODS_DIR}")
    print(f"Clips dir: {CLIPS_DIR}")
    if use_docker_exec():
        c = get_docker_container()
        print(f"Docker container: {c or 'NOT FOUND — start Docker first!'}")
    app.run(host="0.0.0.0", port=5001, threaded=True)
