"""Pipeline lifecycle: spawn / poll / kill the clip-pipeline.

Owns the docker-exec-detached run mode (DetachedDockerPipeline) and the
inside-Docker direct-subprocess run mode. The key invariant is that the
in-container bash is decoupled from any host-side exec session — see the
detailed BUG 31 commentary in DetachedDockerPipeline.

Extracted from dashboard/app.py as part of Phase C. Shared mutable state
(pipeline_process, pipeline_lock, pipeline_vod_name) lives in dashboard._state.
"""
from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import threading
import time
import urllib.request

from . import _state
from .config_io import load_models_config, originality_to_env, load_originality_config


# --- Docker helpers -----------------------------------------------------------

def get_docker_container() -> str | None:
    """Find the running stream-clipper container name, or None."""
    try:
        result = subprocess.run(
            ["docker", "ps", "--filter", "name=stream-clipper", "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=5,
        )
        names = result.stdout.strip().splitlines()
        return names[0] if names else None
    except Exception:
        return None


def use_docker_exec() -> bool:
    """Whether the pipeline runs via docker exec. Bare-metal native is the
    default now; set CLIP_USE_DOCKER=1 to drive a container instead."""
    if os.environ.get("CLIP_USE_DOCKER", "").lower() in ("1", "true", "yes"):
        return not _state.INSIDE_DOCKER
    return False


# --- LM Studio reachability ---------------------------------------------------
#
# `host.docker.internal` is a Docker-only hostname — it resolves from inside a
# container to the host machine. From the Windows host itself it either doesn't
# resolve at all or resolves to an unreachable VM gateway. The pipeline always
# runs inside the container so its `LLM_URL=http://host.docker.internal:1234`
# default works there. The dashboard, however, can run in either context:
# inside the container (entrypoint.sh starts it) or directly on the Windows host
# (`python dashboard/app.py`). The host-side path needs the URL rewritten to
# something the host can actually reach — `localhost` covers it because LM
# Studio binds to 127.0.0.1 in addition to whatever LAN interface it advertises.

def _dashboard_llm_url() -> str:
    """Return the LM Studio URL the dashboard process should query.

    When the dashboard runs inside the container, returns the configured
    `llm_url` as-is (typically `http://host.docker.internal:1234`).
    When the dashboard runs on the Windows host, rewrites
    `host.docker.internal` → `localhost` so urllib can reach LM Studio.
    Custom URLs (e.g. a remote LAN box) pass through unchanged.
    """
    config = load_models_config()
    url = config.get("llm_url", "http://host.docker.internal:1234")
    if not _state.INSIDE_DOCKER:
        url = url.replace("host.docker.internal", "localhost")
    return url


_lm_studio_cache: dict = {"ok": False, "ts": 0.0}
_LM_STUDIO_CACHE_TTL = 30  # seconds


def check_lm_studio() -> bool:
    """Check if LM Studio server is reachable. Result cached for 30 s."""
    global _lm_studio_cache
    now = time.time()
    if now - _lm_studio_cache["ts"] < _LM_STUDIO_CACHE_TTL:
        return _lm_studio_cache["ok"]
    try:
        url = _dashboard_llm_url()
        req = urllib.request.Request(f"{url}/v1/models")
        with urllib.request.urlopen(req, timeout=5) as resp:
            ok = resp.status == 200
    except Exception:
        ok = False
    _lm_studio_cache = {"ok": ok, "ts": now}
    return ok


def query_lm_studio_models() -> list[dict]:
    """Query LM Studio for available models via OpenAI-compatible /v1/models."""
    url = _dashboard_llm_url()
    try:
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
        print(f"Failed to query LM Studio models at {url}: {e}")
        return []


# --- Pipeline-env helpers -----------------------------------------------------

def pipeline_env(captions: bool = True, speed: str = "1.0",
                 hook_caption: bool = True, originality: dict | None = None,
                 passb_dead_gate: str | None = None,
                 enable_thinking: bool = False,
                 companion_shorts: bool = False) -> dict:
    """Build environment dict for direct pipeline subprocess (inside Docker).

    ``passb_dead_gate`` (added 2026-06-04) controls the Pass B dead-chunk
    gate via ``CLIP_PASSB_DEAD_GATE``. One of ``off`` (default, selection-
    safe), ``multi`` (6-signal), ``sample`` (multi + 1-in-N pass-through),
    ``strict`` (legacy 2-signal). When None, the env var is left unset so
    `stage4_moments.py`'s own default kicks in (``off``).
    """
    env = os.environ.copy()
    env["CLIP_VODS_DIR"] = str(_state.VODS_DIR)
    env["CLIP_CLIPS_DIR"] = str(_state.CLIPS_DIR)
    env["CLIP_WORK_DIR"] = str(_state.TEMP_DIR)
    config = load_models_config()
    env["CLIP_TEXT_MODEL"] = config.get("text_model", _state.DEFAULT_MODELS["text_model"])
    env["CLIP_VISION_MODEL"] = config.get("vision_model", _state.DEFAULT_MODELS["vision_model"])
    env["CLIP_WHISPER_MODEL"] = config.get("whisper_model", _state.DEFAULT_MODELS["whisper_model"])
    if config.get("text_model_passb"):
        env["CLIP_TEXT_MODEL_PASSB"] = config["text_model_passb"]
    if config.get("vision_model_stage6"):
        env["CLIP_VISION_MODEL_STAGE6"] = config["vision_model_stage6"]
    env["CLIP_CONTEXT_LENGTH"] = str(config.get("context_length", _state.DEFAULT_MODELS["context_length"]))
    env["CLIP_CAPTIONS"] = "true" if captions else "false"
    env["CLIP_SPEED"] = str(speed)
    env["CLIP_HOOK_CAPTION"] = "true" if hook_caption else "false"
    if passb_dead_gate and passb_dead_gate in ("off", "multi", "sample", "strict"):
        env["CLIP_PASSB_DEAD_GATE"] = passb_dead_gate
    # Thinking toggle (default OFF = the pipeline's no-think default). Reliably controls the
    # request-level lever on compliant models (qwen); non-compliant models (gemma-4) are caught
    # by the Stage-4 fail-fast guard instead. See BUG 67.
    env["CLIP_ENABLE_THINKING"] = "1" if enable_thinking else "0"
    # Companion punchline-only shorts (default off): Stage 7 also emits a "<title> (Short).mp4"
    # for long clips with a late payoff. See concepts/clip-rendering.
    env["CLIP_COMPANION_SHORTS"] = "1" if companion_shorts else "0"
    for k, v in originality_to_env(originality or load_originality_config()).items():
        env[k] = v
    return env


# --- Detached pipeline wrapper (BUG 31) ---------------------------------------

class DetachedDockerPipeline:
    """Façade over a `docker exec -d` pipeline that mimics subprocess.Popen.

    The pipeline runs detached inside the container and writes lifecycle
    markers; this class polls them via short `docker exec cat` calls. If
    Docker Desktop is wedged we return None (still-running) rather than
    falsely reporting completion — the caller will retry on the next poll.
    """

    def __init__(self, container: str):
        self.container = container
        self.returncode = None
        # The real pid lives inside the container. We expose -1 to satisfy
        # callers that read `proc.pid` for display purposes.
        self.pid = -1
        self._start_time = time.time()
        self._grace_window_s = 30  # before declaring "PID file never appeared"

    def _docker_cat(self, path: str):
        try:
            r = subprocess.run(
                ["docker", "exec", self.container, "cat", path],
                capture_output=True, text=True, timeout=5,
            )
            return r.returncode, r.stdout
        except subprocess.TimeoutExpired:
            return None, ""
        except Exception:
            return None, ""

    def _container_pid_alive(self, pid: int) -> bool:
        try:
            r = subprocess.run(
                ["docker", "exec", self.container, "kill", "-0", str(pid)],
                capture_output=True, timeout=5,
            )
            return r.returncode == 0
        except Exception:
            return True  # daemon hiccup — be conservative

    def poll(self):
        """Return None while running, exit code when finished."""
        if self.returncode is not None:
            return self.returncode

        rc, out = self._docker_cat(_state.PIPELINE_DONE_PATH)
        if rc == 0 and out.strip():
            code = 0
            for line in out.splitlines():
                if line.startswith("exit_code="):
                    try:
                        code = int(line.split("=", 1)[1])
                    except ValueError:
                        code = 0
            self.returncode = code
            return code
        if rc is None:
            return None  # Docker daemon glitch — don't false-positive

        rc, out = self._docker_cat(_state.PIPELINE_PID_PATH)
        if rc is None:
            return None
        if rc != 0 or not out.strip():
            if (time.time() - self._start_time) < self._grace_window_s:
                return None
            self.returncode = 1
            return 1

        pid = None
        for line in out.splitlines():
            if line.startswith("pid="):
                try:
                    pid = int(line.split("=", 1)[1])
                except ValueError:
                    pid = None
                break
        if pid is None:
            return None
        if self._container_pid_alive(pid):
            return None

        # PID dead and no done marker — re-check once after a brief delay.
        time.sleep(2)
        rc2, out2 = self._docker_cat(_state.PIPELINE_DONE_PATH)
        if rc2 == 0 and out2.strip():
            code = 0
            for line in out2.splitlines():
                if line.startswith("exit_code="):
                    try:
                        code = int(line.split("=", 1)[1])
                    except ValueError:
                        code = 0
            self.returncode = code
            return code
        self.returncode = 1
        return 1

    def terminate(self) -> None:
        try:
            subprocess.run(
                ["docker", "exec", self.container, "pkill", "-f", "clip-pipeline"],
                capture_output=True, timeout=5,
            )
        except Exception:
            pass

    def kill(self) -> None:
        try:
            subprocess.run(
                ["docker", "exec", self.container, "pkill", "-9", "-f", "clip-pipeline"],
                capture_output=True, timeout=5,
            )
        except Exception:
            pass

    def wait(self, timeout: float | None = None):
        deadline = time.time() + (timeout if timeout is not None else 86400)
        while time.time() < deadline:
            r = self.poll()
            if r is not None:
                return r
            time.sleep(1)
        return None


# --- Spawn / poll / kill ------------------------------------------------------

POLL_INTERVAL_S = 5  # see BUG 31 commentary in original app.py


def _read_remote_files(container: str, file_pairs: list[tuple]) -> None:
    """Read files from Docker container and write locally."""
    for remote_path, local_path, mode in file_pairs:
        try:
            result = subprocess.run(
                ["docker", "exec", container, "cat", remote_path],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                continue
            if mode == "stage":
                content = result.stdout.strip()
                if content:
                    local_path.write_text(content, encoding="utf-8")
            else:
                local_path.write_text(result.stdout, encoding="utf-8")
        except Exception:
            pass


def _poll_container_stages(container: str, proc) -> None:
    """Mirror stage + log files from Docker container to local temp dir."""
    remote_files = [
        ("/tmp/clipper/pipeline_stage.txt", _state.STAGE_FILE, "stage"),
        ("/tmp/clipper/pipeline_stages.log", _state.STAGES_LOG, "stage"),
        ("/tmp/clipper/pipeline.log", _state.LOG_FILE, "log"),
    ]
    while proc.poll() is None:
        _read_remote_files(container, remote_files)
        time.sleep(POLL_INTERVAL_S)
    _read_remote_files(container, remote_files)
    if hasattr(proc, "_log_fh"):
        try:
            proc._log_fh.close()
        except Exception:
            pass


def spawn_pipeline(cmd: list[str], captions: bool = True, speed: str = "1.0",
                   hook_caption: bool = True, originality: dict | None = None,
                   passb_dead_gate: str | None = None, enable_thinking: bool = False,
                   companion_shorts: bool = False):
    """Launch pipeline subprocess.

    Outside Docker: runs detached via `docker exec -d` inside the container.
    Inside Docker:  runs bash directly.

    ``passb_dead_gate`` (2026-06-04) forwards the dashboard's Pass-B gate
    dropdown into the pipeline as ``CLIP_PASSB_DEAD_GATE``. One of
    ``off`` / ``multi`` / ``sample`` / ``strict``. See
    concepts/pipeline-optimizations-2026-06.md §4.
    """
    orig_env = originality_to_env(originality or load_originality_config())

    if use_docker_exec():
        container = get_docker_container()
        if not container:
            raise RuntimeError(
                "No stream-clipper Docker container is running. "
                "Start it with: docker compose up -d"
            )

        try:
            subprocess.run(
                ["docker", "exec", container, "rm", "-f",
                 _state.PIPELINE_PID_PATH, _state.PIPELINE_DONE_PATH],
                capture_output=True, timeout=5,
            )
        except Exception:
            pass

        config = load_models_config()
        env_flags = [
            "-e", f"CLIP_TEXT_MODEL={config.get('text_model', _state.DEFAULT_MODELS['text_model'])}",
            "-e", f"CLIP_VISION_MODEL={config.get('vision_model', _state.DEFAULT_MODELS['vision_model'])}",
            "-e", f"CLIP_WHISPER_MODEL={config.get('whisper_model', _state.DEFAULT_MODELS['whisper_model'])}",
            "-e", f"CLIP_CONTEXT_LENGTH={config.get('context_length', _state.DEFAULT_MODELS['context_length'])}",
            "-e", f"CLIP_CAPTIONS={'true' if captions else 'false'}",
            "-e", f"CLIP_SPEED={speed}",
            "-e", f"CLIP_HOOK_CAPTION={'true' if hook_caption else 'false'}",
        ]
        if config.get("text_model_passb"):
            env_flags += ["-e", f"CLIP_TEXT_MODEL_PASSB={config['text_model_passb']}"]
        if config.get("vision_model_stage6"):
            env_flags += ["-e", f"CLIP_VISION_MODEL_STAGE6={config['vision_model_stage6']}"]
        if passb_dead_gate and passb_dead_gate in ("off", "multi", "sample", "strict"):
            env_flags += ["-e", f"CLIP_PASSB_DEAD_GATE={passb_dead_gate}"]
        env_flags += ["-e", f"CLIP_ENABLE_THINKING={'1' if enable_thinking else '0'}"]
        env_flags += ["-e", f"CLIP_COMPANION_SHORTS={'1' if companion_shorts else '0'}"]
        for k, v in orig_env.items():
            env_flags += ["-e", f"{k}={v}"]

        if len(cmd) >= 2 and cmd[1] == "-c":
            inner = cmd[2]
        else:
            args = cmd[2:]
            inner = "bash " + _state.DOCKER_PIPELINE_SCRIPT
            if args:
                inner += " " + " ".join(shlex.quote(a) for a in args)
        wrapped = f"nohup {inner} </dev/null >/dev/null 2>&1 &"

        docker_cmd = ["docker", "exec", "-d"] + env_flags + [
            container, "bash", "-c", wrapped,
        ]

        try:
            _state.LOG_FILE.write_text("", encoding="utf-8")
        except Exception:
            pass

        try:
            r = subprocess.run(docker_cmd, capture_output=True, text=True, timeout=15)
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                "docker exec -d timed out — Docker Desktop may be wedged. "
                "Quit Docker Desktop, run `wsl --shutdown`, then relaunch."
            )
        if r.returncode != 0:
            raise RuntimeError(
                f"docker exec -d failed: {(r.stderr or r.stdout or '').strip()}"
            )

        proc = DetachedDockerPipeline(container)

        threading.Thread(
            target=_poll_container_stages, args=(container, proc), daemon=True,
        ).start()

        return proc

    # Inside Docker — run directly
    kwargs = dict(
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=pipeline_env(captions=captions, speed=speed,
                         hook_caption=hook_caption, originality=originality,
                         passb_dead_gate=passb_dead_gate,
                         enable_thinking=enable_thinking,
                         companion_shorts=companion_shorts),
    )
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["preexec_fn"] = os.setsid
    return subprocess.Popen(cmd, **kwargs)


def kill_pipeline(proc) -> None:
    """Kill pipeline process tree across platforms."""
    try:
        if os.name == "nt":
            proc.terminate()
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


def _pid_alive(pid: int) -> bool:
    """True if a process with ``pid`` is currently running (Windows + POSIX)."""
    if not pid or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        STILL_ACTIVE = 259
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        try:
            code = ctypes.c_ulong()
            if not k.GetExitCodeProcess(h, ctypes.byref(code)):
                return False
            return code.value == STILL_ACTIVE
        finally:
            k.CloseHandle(h)
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _marker_pid() -> int | None:
    """BUG 67: PID of a LIVE bare-metal pipeline from the on-disk `pipeline.pid`
    marker, else None. This is the CROSS-PROCESS signal that the in-memory
    ``_state.pipeline_process`` misses — it catches a pipeline that THIS dashboard
    instance didn't launch (a second dashboard, or one restarted to change a setting,
    whose in-memory handle is None). A `pipeline.done` marker at/after the pid marker
    means it already finished. Docker mode uses its own detached poll, not this."""
    if use_docker_exec():
        return None
    try:
        pidf = _state.PIPELINE_PID_PATH
        donef = _state.PIPELINE_DONE_PATH
        if not os.path.exists(pidf):
            return None
        if os.path.exists(donef) and os.path.getmtime(donef) >= os.path.getmtime(pidf):
            return None  # finished cleanly
        pid = None
        with open(pidf, encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("pid="):
                    pid = int(line.split("=", 1)[1].strip())
                    break
        if pid and _pid_alive(pid):
            return pid
    except Exception:
        pass
    return None


def is_pipeline_running() -> bool:
    """Running if THIS dashboard's handle is alive OR the on-disk pid marker points to a
    live pipeline. The marker check is what prevents the double-launch (BUG 67): start →
    stop → restart-dashboard → play would otherwise miss the still-running first process."""
    if _state.pipeline_process is not None:
        if _state.pipeline_process.poll() is None:
            return True
        _state.pipeline_process = None
    return _marker_pid() is not None


def stop_running_pipeline() -> bool:
    """Stop whichever pipeline is running — this dashboard's handle AND/OR the one named
    by the on-disk marker (cross-process, survives a dashboard restart). Returns True if
    anything was stopped."""
    stopped = False
    if _state.pipeline_process is not None:
        kill_pipeline(_state.pipeline_process)
        _state.pipeline_process = None
        stopped = True
    pid = _marker_pid()
    if pid:
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                               capture_output=True, timeout=15)
            else:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            stopped = True
        except Exception:
            pass
    return stopped


def read_persistent_log_path() -> str | None:
    """Return the persistent log path written by the pipeline at startup."""
    from pathlib import Path
    candidates = (_state.PIPELINE_DONE_PATH, _state.PIPELINE_PID_PATH)
    if not use_docker_exec():
        for p in candidates:
            try:
                if Path(p).exists():
                    for line in Path(p).read_text(encoding="utf-8").splitlines():
                        if line.startswith("persistent_log="):
                            return line.split("=", 1)[1].strip()
            except Exception:
                continue
        return None
    container = get_docker_container()
    if not container:
        return None
    for remote_path in candidates:
        try:
            r = subprocess.run(
                ["docker", "exec", container, "cat", remote_path],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode != 0 or not r.stdout.strip():
                continue
            for line in r.stdout.splitlines():
                if line.startswith("persistent_log="):
                    in_container = line.split("=", 1)[1].strip()
                    rel = in_container.split("/.pipeline_logs/", 1)
                    if len(rel) == 2:
                        host_path = _state.CLIPS_DIR / ".pipeline_logs" / rel[1]
                        if host_path.exists():
                            return str(host_path)
                    return in_container
        except Exception:
            continue
    return None
