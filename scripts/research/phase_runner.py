#!/usr/bin/env python3
"""phase_runner.py — autonomous real-VOD run harness (plan-pipeline-upgrade-2026-07 Phase 0.0).

OFFLINE / DEV LANE. Never imported by the live pipeline. Drives the gated
real-VOD sections of the upgrade plan so the executing agent can run them
unattended: launch a pipeline run DETACHED (survives the launcher / agent
sandbox), wait on marker files with a hard cap (silence is never success),
auto-evaluate the run machine-readably, and persist phase state so the loop
resumes across session limits.

Verbs:
    launch   --vod NAME [--force] [--style auto] [--profile validation]
             [--label L] [--env K=V ...]         -> spawn detached, record run
    wait     --run ID [--timeout 5400]           -> block on markers, print status
    status   [--run ID]                          -> non-blocking marker read
    evaluate --run ID --phase N [--baseline ID]  -> grade run -> run_eval_<id>.json
    state    [--advance PASS|FAIL] [--phase N]    -> read / advance phase_state.json

All paths come from scripts/lib/paths.py (the orchestrator's single source of
truth) so the harness reads the exact files run_pipeline.py writes.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LIB = REPO / "scripts" / "lib"
sys.path.insert(0, str(LIB))
import paths as _paths  # noqa: E402  (orchestrator path authority)

P = _paths.PATHS
RUN_PIPELINE = REPO / "scripts" / "run_pipeline.py"
STATE_FILE = P.work_dir / "phase_state.json"
FATAL_LOG_PATTERNS = ("[ERROR]", "Traceback (most recent call last)",
                      "CUDA error", "persistent LM Studio outage", "FATAL")

# Flags the Phase 0.1 validation run exercises (clears the in-progress states).
_VALIDATION_ENV = {
    "CLIP_STYLE_PROFILES": "true",     # AI editing profiles (SFX anchor lives here)
    "CLIP_SFX_ANCHOR": "1",            # punchline-anchored SFX (default on in profile mode)
    "CLIP_COLD_OPEN": "1",             # cold-open teaser
    "CLIP_ARC_STITCH": "true",         # setup->payoff arc stitching
    "CLIP_ARC_GUARANTEE_MIN_RATIO": "0.45",
    "CLIP_SEGMENT_VOTES": "3",
    "CLIP_FLASH_CUTS": "true",
}


def _log(msg: str) -> None:
    print(f"[phase_runner] {msg}", file=sys.stderr, flush=True)


# --------------------------------------------------------------------------- state
def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"phase": 0, "runs": {}, "verdicts": {}, "history": []}


def _save_state(st: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(st, indent=2), encoding="utf-8")


def _stamp() -> str:
    # Wall-clock id; unique enough for run tracking (agent launches are serial).
    return time.strftime("%Y%m%d_%H%M%S", time.localtime())


# --------------------------------------------------------------------------- launch
def _lm_studio_up() -> bool:
    """Pre-flight: owner commits to keeping LM Studio up; halt gracefully if not."""
    import urllib.request
    try:
        url = "http://localhost:1234"
        try:
            cfg = json.loads((REPO / "config" / "models.json").read_text(encoding="utf-8"))
            url = str(cfg.get("llm_url") or url).replace("host.docker.internal", "localhost").rstrip("/")
        except Exception:
            pass
        with urllib.request.urlopen(f"{url}/v1/models", timeout=5) as r:
            return r.status == 200
    except Exception:
        return False


def _detached_popen(cmd: list[str], env: dict):
    """Spawn a process that OUTLIVES this launcher (and the agent sandbox).

    Windows: DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB
    so the sandbox job object can't reap the pipeline when the launcher exits.
    POSIX: start_new_session. stdio to DEVNULL — the pipeline tees its own log."""
    kwargs = dict(cwd=str(REPO), env=env,
                  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                  stderr=subprocess.DEVNULL, close_fds=True)
    if os.name == "nt":
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        CREATE_BREAKAWAY_FROM_JOB = 0x01000000
        flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        try:
            proc = subprocess.Popen(cmd, creationflags=flags | CREATE_BREAKAWAY_FROM_JOB, **kwargs)
        except OSError:
            # Not in a job that allows breakaway — fall back without it.
            proc = subprocess.Popen(cmd, creationflags=flags, **kwargs)
    else:
        proc = subprocess.Popen(cmd, start_new_session=True, **kwargs)
    return proc


def _pipeline_python() -> str:
    """The pipeline runs in its OWN venv (torch cu128) — NOT the system Python
    the harness/forensics use (torch cu130). Always launch run_pipeline.py with
    the venv interpreter regardless of what runs the harness."""
    for cand in (REPO / ".venv" / "Scripts" / "python.exe",
                 REPO / ".venv" / "bin" / "python"):
        if cand.exists():
            return str(cand)
    _log("WARNING: .venv python not found — falling back to sys.executable")
    return sys.executable


def cmd_launch(a) -> int:
    if not RUN_PIPELINE.exists():
        _log(f"run_pipeline.py not found at {RUN_PIPELINE}")
        return 2
    if not _lm_studio_up():
        _log("LM Studio not reachable at /v1/models — halting (not hanging). Start it and retry.")
        return 3
    env = os.environ.copy()
    if a.profile == "validation":
        env.update(_VALIDATION_ENV)
    for kv in (a.env or []):
        if "=" in kv:
            k, v = kv.split("=", 1)
            env[k] = v
    cmd = [_pipeline_python(), str(RUN_PIPELINE), "--style", a.style]
    if a.vod:
        cmd += ["--vod", a.vod]
    if a.force:
        cmd += ["--force"]

    # Clear stale markers so wait() can't read a previous run's done file.
    for m in (P.done_file, P.pid_file):
        try:
            m.unlink(missing_ok=True)
        except Exception:
            pass

    run_id = a.label or f"run_{_stamp()}"
    if a.dry_run:
        _log(f"[dry-run] would launch: {' '.join(cmd)}")
        pid = -1
    else:
        proc = _detached_popen(cmd, env)
        pid = proc.pid
        _log(f"launched detached pid={pid}: {' '.join(cmd)}")

    st = _load_state()
    st["runs"][run_id] = {
        "vod": a.vod, "style": a.style, "profile": a.profile,
        "pid": pid, "cmd": cmd, "launched_at": time.time(),
        "env_overrides": {k: env[k] for k in (_VALIDATION_ENV if a.profile == "validation" else {})},
        "phase": a.phase, "status": "running",
    }
    _save_state(st)
    print(json.dumps({"run_id": run_id, "pid": pid, "dry_run": bool(a.dry_run)}))
    return 0


# --------------------------------------------------------------------------- wait / status
def _read_done() -> int | None:
    if not P.done_file.exists():
        return None
    try:
        for line in P.done_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("exit_code="):
                return int(line.split("=", 1)[1])
    except Exception:
        return None
    return None


def _pid_alive(pid: int) -> bool:
    if pid is None or pid <= 0:
        return False
    if os.name == "nt":
        r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"],
                           capture_output=True, text=True)
        return str(pid) in (r.stdout or "")
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _log_has_fatal() -> str | None:
    try:
        txt = P.pipeline_log.read_text(encoding="utf-8", errors="replace")[-8000:]
    except Exception:
        return None
    for pat in FATAL_LOG_PATTERNS:
        if pat in txt:
            return pat
    return None


def _current_stage() -> str:
    try:
        return P.stage_file.read_text(encoding="utf-8").strip()
    except Exception:
        return "?"


def cmd_status(a) -> int:
    done = _read_done()
    st = _load_state()
    run = st["runs"].get(a.run, {}) if a.run else {}
    out = {"run": a.run, "done_exit": done, "stage": _current_stage(),
           "pid_alive": _pid_alive(run.get("pid")) if run else None,
           "fatal_log": _log_has_fatal()}
    print(json.dumps(out))
    return 0


def _log_mtime() -> float:
    try:
        return P.pipeline_log.stat().st_mtime
    except Exception:
        return 0.0


def cmd_wait(a) -> int:
    """Block on markers with a hard cap. Exit reasons: done | fatal | dead |
    STALL | timeout.  STALL = the pipeline log stopped advancing for > a.stall
    seconds while the pid is still alive (the 2026-07-04 incident: the Stage-2
    parallel audio_events scan hung 58 min, alive but frozen — silence-is-not-
    success)."""
    deadline = time.time() + a.timeout
    st = _load_state()
    run = st["runs"].get(a.run, {})
    pid = run.get("pid")
    launched = run.get("launched_at", time.time())
    last_mtime = _log_mtime()
    last_change = time.time()
    result = {"run": a.run, "reason": "timeout", "exit": None, "stage": None}
    while time.time() < deadline:
        code = _read_done()
        if code is not None:
            result.update(reason="done", exit=code, stage=_current_stage())
            break
        fatal = _log_has_fatal()
        if fatal:
            result.update(reason="fatal", exit=1, stage=_current_stage(), detail=fatal)
            break
        # PID death without a done marker, past a grace window = crash.
        if pid and pid > 0 and (time.time() - launched) > 60 and not _pid_alive(pid):
            time.sleep(3)  # let cleanup write the marker if it's mid-flush
            code = _read_done()
            if code is not None:
                result.update(reason="done", exit=code, stage=_current_stage())
            else:
                result.update(reason="dead", exit=1, stage=_current_stage())
            break
        # STALL: log frozen while pid alive (hang detection).
        mt = _log_mtime()
        if mt != last_mtime:
            last_mtime, last_change = mt, time.time()
        elif a.stall > 0 and (time.time() - last_change) > a.stall:
            result.update(reason="stall", exit=1, stage=_current_stage(),
                          detail=f"log frozen {int(time.time()-last_change)}s")
            break
        time.sleep(a.poll)
    if a.run in st["runs"]:
        st["runs"][a.run]["status"] = result["reason"]
        st["runs"][a.run]["exit"] = result["exit"]
        _save_state(st)
    print(json.dumps(result))
    return 0 if result["reason"] == "done" and result["exit"] == 0 else 1


# --------------------------------------------------------------------------- evaluate
def _latest_diag(vod: str | None):
    # Stage 8 writes clips/.diagnostics/last_run_<stamp>.json (also older
    # <vod>_diagnostics.json). Take the most recent of either.
    d = P.diagnostics_dir
    if not d.is_dir():
        return None
    cands = sorted([p for p in d.glob("*.json")],
                   key=lambda p: p.stat().st_mtime, reverse=True)
    if vod:
        stem = Path(vod).stem
        for c in cands:
            if stem in c.name:
                return c
    return cands[0] if cands else None


def _loudness(clip: Path) -> dict | None:
    """Integrated loudness (LUFS) of a rendered clip — sanity for SFX-vs-speech."""
    try:
        r = subprocess.run(
            ["ffmpeg", "-nostdin", "-i", str(clip), "-af", "loudnorm=print_format=json",
             "-f", "null", "-"], capture_output=True, text=True, timeout=120)
        err = r.stderr or ""
        s = err.rfind("{")
        e = err.rfind("}")
        if 0 <= s < e:
            j = json.loads(err[s:e + 1])
            return {"input_i": j.get("input_i"), "input_tp": j.get("input_tp")}
    except Exception:
        pass
    return None


def _moment_set(hype_path: Path) -> set:
    """Deterministic selection fingerprint for baseline comparison (NOT rendered
    bytes — NVENC is non-deterministic)."""
    try:
        data = json.loads(hype_path.read_text(encoding="utf-8"))
    except Exception:
        return set()
    items = data if isinstance(data, list) else data.get("moments", [])
    out = set()
    for m in items:
        t = m.get("timestamp") or m.get("clip_start") or m.get("time")
        if t is not None:
            out.add(round(float(t), 1))
    return out


def cmd_evaluate(a) -> int:
    checks = []

    def add(name, ok, evidence=""):
        checks.append({"check": name, "pass": bool(ok), "evidence": str(evidence)[:300]})

    st = _load_state()
    run = st["runs"].get(a.run, {})
    vod = run.get("vod")

    # 1) run health
    exit_code = run.get("exit", _read_done())
    add("run_completed_exit_0", exit_code == 0, f"exit={exit_code}")
    add("no_fatal_in_log", _log_has_fatal() is None, _log_has_fatal() or "clean")

    # 2) produced clips + diagnostics
    diag_path = _latest_diag(vod)
    diag = {}
    if diag_path:
        try:
            diag = json.loads(diag_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    cm = diag.get("clips_made")
    if isinstance(cm, list):
        clips_made = len(cm)
    elif isinstance(cm, int):
        clips_made = cm
    else:
        clips_made = diag.get("clip_count") or len(diag.get("clips", []) or [])
    add("clips_produced", (clips_made or 0) > 0, f"clips_made={clips_made} ({diag_path.name if diag_path else 'no diag'})")

    # 3) axis coverage (selection intelligence ran) — axis_report is embedded in
    # the diagnostics JSON; fall back to a standalone work_dir file.
    axis = diag.get("axis_report") if isinstance(diag.get("axis_report"), dict) else {}
    if not axis:
        ax_path = P.work_dir / "axis_report.json"
        if ax_path.exists():
            try:
                axis = json.loads(ax_path.read_text(encoding="utf-8"))
            except Exception:
                pass
    add("axis_report_present", bool(axis), "embedded in diagnostics" if axis else "missing")

    # 4) baseline moment-set comparison (deterministic artifact)
    if a.baseline:
        cur = _moment_set(P.hype_moments)
        base_path = P.work_dir / f"hype_moments_{a.baseline}.json"
        base = _moment_set(base_path) if base_path.exists() else set()
        if base:
            same = cur == base
            add("baseline_identical" if a.phase and int(a.phase) >= 1 else "baseline_captured",
                same, f"cur={len(cur)} base={len(base)} jaccard={len(cur & base)}/{len(cur | base)}")
        else:
            add("baseline_available", False, f"no {base_path.name}")

    # 5) forensics + loudness on a sample output clip (dogfood the grader)
    if a.forensics:
        clips = sorted(P.clips_dir.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)[:1]
        if clips:
            ld = _loudness(clips[0])
            add("output_loudness_readable", ld is not None, ld or "ffmpeg loudnorm failed")
            fx = REPO / "scripts" / "research" / "clip_forensics.py"
            try:
                r = subprocess.run(
                    [sys.executable, str(fx), "--clip", str(clips[0]), "--no-llm",
                     "--out", str(P.work_dir / f"eval_forensics_{a.run}.json")],
                    capture_output=True, text=True, timeout=400,
                    env={**os.environ, "KMP_DUPLICATE_LIB_OK": "TRUE"})
                add("forensics_on_output_ran", r.returncode == 0,
                    (r.stderr or "").strip().splitlines()[-1] if r.stderr else "ok")
            except Exception as e:
                add("forensics_on_output_ran", False, str(e))
        else:
            add("output_clip_present", False, "no mp4 in clips/")

    verdict = "PASS" if all(c["pass"] for c in checks) else "FAIL"
    report = {"run": a.run, "phase": a.phase, "vod": vod, "verdict": verdict,
              "exit": exit_code, "checks": checks, "evaluated_at": time.time()}
    out = P.work_dir / f"run_eval_{a.run}.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    st.setdefault("verdicts", {})[a.run] = verdict
    _save_state(st)
    print(json.dumps({"verdict": verdict, "report": str(out),
                      "failed": [c["check"] for c in checks if not c["pass"]]}, indent=2))
    return 0 if verdict == "PASS" else 1


# --------------------------------------------------------------------------- state
def cmd_state(a) -> int:
    st = _load_state()
    if a.advance:
        ph = int(a.phase) if a.phase is not None else st.get("phase", 0)
        st["history"].append({"phase": ph, "result": a.advance, "at": time.time()})
        if a.advance == "PASS" and a.phase is not None:
            st["phase"] = max(st.get("phase", 0), int(a.phase))
        _save_state(st)
    print(json.dumps(st, indent=2))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Autonomous real-VOD run harness (Phase 0.0)")
    sub = ap.add_subparsers(dest="verb", required=True)

    pl = sub.add_parser("launch")
    pl.add_argument("--vod", default="")
    pl.add_argument("--style", default="auto")
    pl.add_argument("--force", action="store_true")
    pl.add_argument("--profile", choices=["none", "validation"], default="none")
    pl.add_argument("--label")
    pl.add_argument("--phase")
    pl.add_argument("--env", action="append", help="KEY=VALUE (repeatable)")
    pl.add_argument("--dry-run", action="store_true")
    pl.set_defaults(func=cmd_launch)

    pw = sub.add_parser("wait")
    pw.add_argument("--run", required=True)
    pw.add_argument("--timeout", type=float, default=5400)
    pw.add_argument("--poll", type=float, default=15)
    pw.add_argument("--stall", type=float, default=600,
                    help="declare STALL if the pipeline log freezes this many "
                         "seconds while the pid is alive (0 disables). Default 600.")
    pw.set_defaults(func=cmd_wait)

    ps = sub.add_parser("status")
    ps.add_argument("--run")
    ps.set_defaults(func=cmd_status)

    pe = sub.add_parser("evaluate")
    pe.add_argument("--run", required=True)
    pe.add_argument("--phase")
    pe.add_argument("--baseline")
    pe.add_argument("--forensics", action="store_true")
    pe.set_defaults(func=cmd_evaluate)

    pt = sub.add_parser("state")
    pt.add_argument("--advance", choices=["PASS", "FAIL"])
    pt.add_argument("--phase")
    pt.set_defaults(func=cmd_state)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
