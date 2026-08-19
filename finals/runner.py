"""Spawn / poll / stop the Finals scan subprocess.

Same lifecycle discipline as the main pipeline (BUG 67 / BUG 72 lessons):
cross-process pid/done markers so a run this dashboard didn't launch is still
detected, and taskkill /T so a Stop takes the whole tree (ffmpeg children).
Every run is bounded by the engine's --max-minutes watchdog.
"""
from __future__ import annotations

import os
import subprocess
import time

from . import _state


def _pid_alive(pid: int) -> bool:
    if not pid or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        STILL_ACTIVE = 259
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, int(pid))
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


def marker_pid() -> int | None:
    """PID of a live run from the on-disk marker, else None (done marker at or
    after the pid marker means it finished)."""
    pidf = _state.RUN_DIR / "pid"
    donef = _state.RUN_DIR / "done"
    try:
        if not pidf.exists():
            return None
        if donef.exists() and donef.stat().st_mtime >= pidf.stat().st_mtime:
            return None
        pid = None
        for line in pidf.read_text(encoding="utf-8").splitlines():
            if line.startswith("pid="):
                pid = int(line.split("=", 1)[1].strip())
                break
        if pid and _pid_alive(pid):
            return pid
    except Exception:
        pass
    return None


def marker_info() -> dict:
    """Parse the pid marker's queue fields: current vod, 0-based index, total,
    and the full queue (pipe-joined vod= is the current item; queue= is all)."""
    out = {"vod": "", "index": 0, "total": 0, "queue": []}
    try:
        for line in (_state.RUN_DIR / "pid").read_text(encoding="utf-8").splitlines():
            if line.startswith("vod="):
                out["vod"] = line.split("=", 1)[1].strip()
            elif line.startswith("index="):
                out["index"] = int(line.split("=", 1)[1].strip())
            elif line.startswith("total="):
                out["total"] = int(line.split("=", 1)[1].strip())
            elif line.startswith("queue="):
                out["queue"] = [q for q in line.split("=", 1)[1].strip().split("|") if q]
    except Exception:
        pass
    return out


def marker_vod() -> str:
    return marker_info()["vod"]


def is_running() -> bool:
    global_proc = _state.job_process
    if global_proc is not None:
        if global_proc.poll() is None:
            return True
        _state.last_exit = global_proc.returncode
        _state.job_process = None
    return marker_pid() is not None


def spawn(vods: list[str], params: dict) -> None:
    _state.RUN_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [str(_state.PYTHON), str(_state.RUNNER)]
    for v in vods:
        cmd += ["--vod", v]
    cmd += ["--pre", str(params.get("pre", 10)),
           "--post", str(params.get("post", 5)),
           "--end-cap", str(params.get("end_cap", 6)),
           "--fps", str(params.get("fps", 2)),
           "--ocr", str(params.get("ocr", "auto")),
           "--max-minutes", str(params.get("max_minutes", 240))]
    if not params.get("revives", True):
        cmd.append("--no-revives")
    if not params.get("endscreens", True):
        cmd.append("--no-endscreens")
    if params.get("start"):
        cmd += ["--start", str(params["start"])]
    if params.get("end"):
        cmd += ["--end", str(params["end"])]

    log_fh = open(_state.LOG_FILE, "w", encoding="utf-8", buffering=1)
    kwargs = dict(stdout=log_fh, stderr=subprocess.STDOUT, cwd=str(_state.PROJECT_DIR))
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    proc = subprocess.Popen(cmd, **kwargs)
    proc._log_fh = log_fh  # closed on stop; GC'd otherwise
    _state.job_process = proc
    _state.job_meta = {"vods": vods, "params": params, "started": time.time()}
    _state.last_exit = None


def stop() -> bool:
    stopped = False
    pids = set()
    if _state.job_process is not None and _state.job_process.poll() is None:
        pids.add(_state.job_process.pid)
    mp = marker_pid()
    if mp:
        pids.add(mp)
    for pid in pids:
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                               capture_output=True, timeout=15)
            else:
                os.kill(pid, 15)
            stopped = True
        except Exception:
            pass
    if _state.job_process is not None:
        fh = getattr(_state.job_process, "_log_fh", None)
        if fh:
            try:
                fh.close()
            except Exception:
                pass
        _state.job_process = None
    # A killed run never writes its own done marker — write one so marker_pid()
    # goes quiet immediately.
    try:
        (_state.RUN_DIR / "done").write_text("exit_code=130\nstopped=1\n", encoding="utf-8")
    except Exception:
        pass
    return stopped


def tail_log(n: int = 300) -> str:
    try:
        lines = _state.LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-n:])
    except Exception:
        return ""


def progress() -> dict:
    """Parse the newest '[scan] xx.x%' line (per-VOD bar) plus the queue
    position from the pid marker (overall 'VOD 2/4' bar)."""
    out = {"pct": None, "line": "", "queue_index": 0, "queue_total": 0, "queue": []}
    try:
        for line in reversed(_state.LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()):
            if line.startswith(("[scan]", "[cut]", "[finals]", "[queue]")):
                out["line"] = line.strip()
                break
        for line in reversed(_state.LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()):
            if line.startswith("[scan]") and "%" in line:
                out["pct"] = float(line.split("%", 1)[0].split()[-1])
                break
            if line.startswith(("[cut]", "[finals]")):
                out["pct"] = 100.0
                break
            if line.startswith("[queue]"):
                out["pct"] = 0.0
                break
    except Exception:
        pass
    info = marker_info()
    out["queue_index"] = info["index"]
    out["queue_total"] = info["total"]
    out["queue"] = [os.path.basename(v) for v in info["queue"]]
    return out
