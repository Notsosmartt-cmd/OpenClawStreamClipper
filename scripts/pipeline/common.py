#!/usr/bin/env python3
"""Pipeline common helpers — Python port of ``scripts/lib/pipeline_common.sh``.

Provides the orchestrator and stage modules with:
  * ``Logger``       — tee stdout/stderr to console + ephemeral + persistent log
  * ``set_stage``    — write the stage marker the dashboard polls
  * ``unload_model`` / ``load_model`` / ``verify_models`` — LM Studio REST
  * ``run_module``   — run a reused ``scripts/lib`` module as a subprocess,
                       streaming its output into the log
  * ``run_ffmpeg``   — bounded FFmpeg/ffprobe invocation
  * ``cleanup``      — diagnostics dump + work-dir clear + done marker

Behaviour mirrors the bash helpers 1:1 (timeouts, failure-soft model calls,
the BUG-31 liveness markers) so the dashboard's polling contract is intact.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

import paths as _paths_mod  # scripts/lib on sys.path (run_pipeline sets this up)

PATHS = _paths_mod.PATHS


class PipelineExit(Exception):
    """Raised by a stage to end the run early but still run cleanup.

    ``summary`` is the JSON status line the old bash stages echoed to stdout
    for the OpenClaw agent / dashboard to read.
    """

    def __init__(self, code: int, summary: str | None = None):
        super().__init__(summary or f"exit {code}")
        self.code = code
        self.summary = summary


# ---------------------------------------------------------------------------
# Logging — tee to console + ephemeral pipeline.log + persistent run log
# ---------------------------------------------------------------------------
class Logger:
    """Writes every pipeline line to stdout, the ephemeral log (dashboard SSE
    tails this), and the timestamped persistent log."""

    def __init__(self, ephemeral: Path, persistent: Path):
        ephemeral.parent.mkdir(parents=True, exist_ok=True)
        persistent.parent.mkdir(parents=True, exist_ok=True)
        # line-buffered append handles
        self._eph = open(ephemeral, "a", encoding="utf-8", buffering=1)
        self._per = open(persistent, "a", encoding="utf-8", buffering=1)
        self._lock = threading.Lock()

    def write(self, text: str) -> None:
        with self._lock:
            sys.stdout.write(text)
            sys.stdout.flush()
            self._eph.write(text)
            self._per.write(text)

    def line(self, text: str) -> None:
        self.write(text + "\n")

    def log(self, msg: str) -> None:
        self.line(f"[PIPELINE] {msg}")

    def warn(self, msg: str) -> None:
        self.line(f"[WARN] {msg}")

    def err(self, msg: str) -> None:
        self.line(f"[ERROR] {msg}")

    def info(self, msg: str) -> None:
        self.line(f"[INFO] {msg}")

    def close(self) -> None:
        for fh in (self._eph, self._per):
            try:
                fh.close()
            except Exception:
                pass


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def set_stage(log: Logger, stage_text: str) -> None:
    """Write the current stage to the dashboard-polled marker files."""
    try:
        PATHS.stage_file.write_text(stage_text + "\n", encoding="utf-8")
        with open(PATHS.stages_log, "a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now(timezone.utc).isoformat()} {stage_text}\n")
    except OSError as e:
        log.warn(f"could not write stage marker: {e}")
    log.log(f">>> {stage_text}")


# ---------------------------------------------------------------------------
# LM Studio REST (ported from unload_model / load_model / verify_models)
# ---------------------------------------------------------------------------
def _http_post(url: str, body: dict, timeout: float) -> int:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0


def _http_get_json(url: str, timeout: float):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


# --- LM Studio CLI (`lms`) — version-stable model load/unload ----------------
# LM Studio's REST unload path varies by version (0.4.14 returns 404 for
# /api/v1/models/unload), which strands models in VRAM. The bundled `lms` CLI is
# stable across versions, so we prefer it and fall back to REST when absent.
def _find_lms() -> str | None:
    p = shutil.which("lms")
    if p:
        return p
    home = Path(os.path.expanduser("~"))
    for c in (home / ".cache" / "lm-studio" / "bin" / "lms.exe",
              home / ".lmstudio" / "bin" / "lms.exe",
              home / ".cache" / "lm-studio" / "bin" / "lms",
              home / ".lmstudio" / "bin" / "lms"):
        if c.exists():
            return str(c)
    return None


_LMS_BIN = _find_lms()


def _lms_loaded_ids() -> list:
    """Identifiers of currently-loaded models per `lms ps` (empty on any error)."""
    if not _LMS_BIN:
        return []
    try:
        r = subprocess.run([_LMS_BIN, "ps"], capture_output=True, text=True, timeout=15)
    except Exception:
        return []
    ids = []
    for line in r.stdout.splitlines():
        s = line.strip()
        if not s or s.startswith("IDENTIFIER"):
            continue
        ids.append(s.split()[0])
    return ids


def _heartbeat_stage(stop: threading.Event) -> None:
    """Touch the stage marker every 10 s so the dashboard staleness gate can't
    trip while a blocking model load is in progress."""
    while not stop.wait(10):
        try:
            PATHS.stage_file.touch()
        except OSError:
            break


def unload_model(log: Logger, llm_url: str, model: str) -> None:
    """Best-effort VRAM unload. Prefers the `lms` CLI (version-stable); falls
    back to the LM Studio REST API. Never raises."""
    if _LMS_BIN:
        log.log(f"Requesting unload of '{model}' from VRAM (lms)...")
        try:
            r = subprocess.run([_LMS_BIN, "unload", model],
                               capture_output=True, text=True, timeout=30)
            if r.returncode == 0:
                log.log(f"  unloaded '{model}'")
            else:
                tail = (r.stderr or r.stdout or "").strip().replace("\n", " ")[-160:]
                log.log(f"  unload: '{model}' not loaded / lms non-zero ({tail}) — continuing")
        except subprocess.TimeoutExpired:
            log.log("  unload: lms timed out — continuing")
        except Exception as e:  # noqa: BLE001
            log.log(f"  unload: lms error ({e}) — continuing")
        time.sleep(1)
        return

    # REST fallback (no lms on PATH)
    log.log(f"Requesting unload of '{model}' from VRAM...")
    code = _http_post(
        f"{llm_url}/api/v1/models/unload", {"instance_id": model}, timeout=15
    )
    if 200 <= code < 300:
        pass
    elif code == 0:
        log.log("  unload: LM Studio unreachable/timeout — JIT will reclaim VRAM")
    elif code == 404:
        log.log("  unload: endpoint unsupported (HTTP 404) — relying on JIT")
    else:
        log.log(f"  unload: HTTP {code} — continuing anyway")
    time.sleep(1)


def load_model(log: Logger, llm_url: str, model: str, ctx: int) -> None:
    """Best-effort pre-load. Prefers the `lms` CLI; falls back to REST. Skips
    when the model is already loaded. Sets an idle TTL (env CLIP_MODEL_TTL,
    default 3600 s) so abandoned models auto-evict instead of stranding VRAM.
    Heartbeats the stage marker during the (blocking) load."""
    ttl = os.environ.get("CLIP_MODEL_TTL", "3600")

    if _LMS_BIN:
        if model in _lms_loaded_ids():
            log.log(f"Model '{model}' already loaded — skipping pre-load")
            return
        log.log(f"Pre-loading '{model}' via lms (context_length={ctx}, ttl={ttl}s)...")
        cmd = [_LMS_BIN, "load", model, "-c", str(ctx), "-y"]
        if ttl and ttl != "0":
            cmd += ["--ttl", str(ttl)]
        stop = threading.Event()
        hb = threading.Thread(target=_heartbeat_stage, args=(stop,), daemon=True)
        hb.start()
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
            if r.returncode == 0:
                log.log("  pre-load OK (lms)")
            else:
                tail = (r.stderr or r.stdout or "").strip().replace("\n", " ")[-200:]
                log.log(f"  pre-load: lms non-zero ({tail}) — JIT will load on demand")
        except subprocess.TimeoutExpired:
            log.log("  pre-load: lms timed out — JIT will load on first inference")
        except Exception as e:  # noqa: BLE001
            log.log(f"  pre-load: lms error ({e}) — JIT will load on demand")
        finally:
            stop.set()
        time.sleep(1)
        return

    # REST fallback (no lms on PATH)
    log.log(f"Pre-loading '{model}' (context_length={ctx}, timeout=120s)...")
    if _http_get_json(f"{llm_url}/v1/models", timeout=5) is None:
        log.log(f"  LM Studio probe failed — skipping pre-load, JIT will handle it")
        time.sleep(2)
        return

    stop = threading.Event()
    hb = threading.Thread(target=_heartbeat_stage, args=(stop,), daemon=True)
    hb.start()
    code = _http_post(
        f"{llm_url}/api/v1/models/load",
        {"model": model, "context_length": ctx},
        timeout=120,
    )
    stop.set()

    if 200 <= code < 300:
        log.log(f"  pre-load OK (HTTP {code})")
    elif code == 0:
        log.log("  pre-load: timeout/unreachable — JIT will load on first inference")
    elif code == 404:
        log.log("  pre-load: endpoint unsupported (HTTP 404) — JIT will load")
    elif code in (400, 409):
        log.log(f"  pre-load: HTTP {code} — model likely already loaded; continuing")
    else:
        log.log(f"  pre-load: HTTP {code} — continuing (JIT will load if needed)")
    time.sleep(2)


def verify_models(log: Logger, llm_url: str, models: Iterable[str]) -> None:
    """Fail-fast (exit 2) if a configured model isn't loaded in LM Studio.
    Warns and continues when LM Studio is unreachable (cached runs still work)."""
    log.log("Verifying configured models are loaded in LM Studio...")
    data = _http_get_json(f"{llm_url}/v1/models", timeout=5)
    if data is None:
        log.warn(f"  LM Studio unreachable at {llm_url}/v1/models — skipping verification.")
        return
    available = [m.get("id") for m in (data.get("data") or []) if m.get("id")]
    if not available:
        log.warn("  /v1/models returned no parseable list — skipping verification.")
        return
    wanted = [m for m in dict.fromkeys(models) if m]  # de-dup, keep order
    missing = [m for m in wanted if m not in available]
    if missing:
        log.err("Configured model(s) NOT loaded in LM Studio:")
        for m in missing:
            log.err(f"    - {m}")
        log.err("  Available right now:")
        for m in available:
            log.err(f"    - {m}")
        log.err("  Fix: download the model in LM Studio, or point config/models.json")
        log.err("       at one of the available IDs. Aborting before Stage 3.")
        sys.exit(2)
    log.log(f"  All {len(wanted)} configured model(s) present in LM Studio.")


# ---------------------------------------------------------------------------
# Subprocess helpers
# ---------------------------------------------------------------------------
def run_module(
    log: Logger,
    module_relpath: str,
    args: Sequence[str] = (),
    env: dict | None = None,
    check: bool = True,
    capture: bool = False,
) -> subprocess.CompletedProcess:
    """Run a reused ``scripts/lib`` module as a subprocess via this venv's
    interpreter (so ``python3`` vs ``python`` is moot). Streams the child's
    combined output into the pipeline log line by line.

    When ``capture`` is True the child's stdout is also collected and returned
    in ``.stdout`` (used where bash captured ``$(python3 ...)`` output).
    """
    module_path = PATHS.lib_dir / module_relpath
    cmd = [sys.executable, str(module_path), *map(str, args)]
    collected: list[str] = []
    proc = subprocess.Popen(
        cmd,
        cwd=str(PATHS.repo_root),
        env=env if env is not None else PATHS.child_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None
    for raw in proc.stdout:
        if capture:
            collected.append(raw)
        log.write(raw)
    rc = proc.wait()
    result = subprocess.CompletedProcess(cmd, rc, "".join(collected) if capture else "", "")
    if check and rc != 0:
        raise subprocess.CalledProcessError(rc, cmd)
    return result


def run_ffmpeg(args: Sequence[str], timeout: float | None = None) -> int:
    """Run ffmpeg/ffprobe quietly; return exit code (never raises on non-zero)."""
    try:
        r = subprocess.run(
            list(args),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
        )
        return r.returncode
    except (subprocess.TimeoutExpired, OSError):
        return 1


def ffprobe_duration(media: Path) -> int:
    """Integer-seconds duration via ffprobe, or 0 on failure."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(media)],
            capture_output=True, text=True, timeout=30,
        )
        return int(float(r.stdout.strip().split(".")[0] or 0))
    except (ValueError, subprocess.SubprocessError, OSError):
        return 0


# ---------------------------------------------------------------------------
# Lifecycle markers + cleanup (BUG-31 contract)
# ---------------------------------------------------------------------------
def write_pid_marker(persistent_log: Path) -> None:
    PATHS.done_file.unlink(missing_ok=True)
    PATHS.pid_file.write_text(
        f"pid={os.getpid()}\n"
        f"started={datetime.now(timezone.utc).isoformat()}\n"
        f"persistent_log={persistent_log}\n",
        encoding="utf-8",
    )


def cleanup(log: Logger, persistent_log: Path, exit_code: int, start_epoch: float) -> None:
    """EXIT-trap equivalent: elapsed report, diagnostics dump, work-dir clear,
    done marker (so the dashboard learns the final exit code)."""
    elapsed = int(time.time() - start_epoch)
    h, m, s = elapsed // 3600, (elapsed % 3600) // 60, elapsed % 60
    if h:
        log.log(f"Pipeline elapsed: {h}h {m}m {s}s ({elapsed}s, exit={exit_code})")
    else:
        log.log(f"Pipeline elapsed: {m}m {s}s ({elapsed}s, exit={exit_code})")

    # Diagnostics: snapshot every work-dir JSON (ported from pipeline_common.sh)
    try:
        PATHS.diagnostics_dir.mkdir(parents=True, exist_ok=True)
        diag_file = PATHS.diagnostics_dir / f"last_run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
        diag: dict = {}
        for f in PATHS.work_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                name = f.stem
                if isinstance(data, list):
                    diag[name] = {"count": len(data), "data": data[:30]}
                else:
                    diag[name] = data
            except Exception:
                pass
        if PATHS.clips_made.exists():
            diag["clips_made"] = PATHS.clips_made.read_text(encoding="utf-8").strip().splitlines()
        diag_file.write_text(json.dumps(diag, indent=2), encoding="utf-8")
        log.log(f"Diagnostics saved to {diag_file}")
    except Exception as e:
        log.warn(f"diagnostics dump failed: {e}")

    log.log("Cleaning up temp files...")
    # Clear work-dir contents but keep the dir.
    for child in PATHS.work_dir.glob("*"):
        try:
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
        except Exception:
            pass

    try:
        PATHS.done_file.write_text(
            f"exit_code={exit_code}\n"
            f"finished={datetime.now(timezone.utc).isoformat()}\n"
            f"persistent_log={persistent_log}\n",
            encoding="utf-8",
        )
    except OSError:
        pass


def append_processed(processed_log: Path, basename: str, tag: str, style: str) -> None:
    """Append a tab-separated record to processed.log (basename, UTC, tag, style)."""
    try:
        with open(processed_log, "a", encoding="utf-8") as f:
            f.write(f"{basename}\t{_utc_stamp()}\t{tag}\t{style}\n")
    except OSError:
        pass


__all__ = [
    "PATHS", "PipelineExit", "Logger", "set_stage", "unload_model", "load_model",
    "verify_models", "run_module", "run_ffmpeg", "ffprobe_duration",
    "write_pid_marker", "cleanup", "append_processed", "_utc_stamp",
]
