#!/usr/bin/env python3
"""logtool — inspect & triage OpenClaw Stream Clipper logs (bare-metal Windows).

A diagnostics CLI for sifting pipeline run logs and catching errors. Runs in the
project venv so the `doctor` dependency checks are meaningful.

    .venv\\Scripts\\python.exe scripts\\logtool.py <command>

Commands:
  doctor                 Environment preflight — venv deps (torch/CUDA,
                         faster-whisper/CTranslate2, whisperx, flask, …), ffmpeg,
                         cuDNN DLLs, LM Studio reachability + loaded/configured
                         models, resolved paths, disk. Catches the usual causes
                         of run failures before you start a clip.
  list [-n N] [--json]   Recent runs: time, VOD, stage reached, exit, #errors, #clips.
  errors [RUN] [-n N] [--all] [-C K] [--json]
                         Scan run log(s) for errors, classified + grouped, with
                         the stage each occurred in. RUN = index from `list`, a
                         filename substring, or a path. Default: the latest run.
  show RUN [--tail N]    Print a run's full log (or last N lines).
  tail [-n N] [--follow] Read the live work-dir pipeline.log (current run).
  axes [RUN] [--judge-limit N]
                         Selection-axis tuning view for a past run: per-axis
                         coverage + multiplier spread + dependency readiness,
                         the base->passC->vision rank churn, per-stage timing,
                         and the Vision-Judge pairwise bracket. RUN = index from
                         newest, name substring, or path. Default: latest.

Exit code is non-zero from `doctor` when any check fails, so it's CI-friendly.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "lib"))
sys.path.insert(0, str(HERE))

import paths  # noqa: E402
from pipeline import common  # noqa: E402

PATHS = paths.PATHS

# --- pretty -----------------------------------------------------------------
_USE_COLOR = os.environ.get("NO_COLOR") is None and sys.stdout.isatty()


def _c(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m" if _USE_COLOR else s


def ok(b: bool) -> str:
    return _c("32", "[ OK ]") if b else _c("31", "[FAIL]")


def banner(s: str) -> None:
    print(_c("36;1", f"\n=== {s} ==="))


# --- error classification ---------------------------------------------------
# Order matters: first match wins. (label, severity, compiled regex)
_RULES = [
    ("traceback", "CRIT", re.compile(r"Traceback \(most recent call last\)")),
    ("exception", "CRIT", re.compile(r"\b\w*(Error|Exception)\b:")),
    ("import", "CRIT", re.compile(r"\b(ModuleNotFoundError|ImportError|DLL load failed|could not (load|locate))\b", re.I)),
    ("transcription", "CRIT", re.compile(r"transcription_failed|speech\.py transcription failed", re.I)),
    ("render-dead", "CRIT", re.compile(r"Render completely failed", re.I)),
    ("pipeline-fail", "CRIT", re.compile(r"\[ERROR\] pipeline failed", re.I)),
    ("conn-refused", "ERR", re.compile(r"actively refused|connection refused|WinError 10061|Errno 111", re.I)),
    ("http-4xx5xx", "ERR", re.compile(r"HTTP Error (4\d\d|5\d\d)|Bad Request", re.I)),
    ("net", "ERR", re.compile(r"Network is unreachable|Name or service not known|Errno 101", re.I)),
    ("error-tag", "ERR", re.compile(r"\[ERROR\]")),
    ("ffmpeg", "ERR", re.compile(r"ffmpeg.*(error|failed)|Render failed", re.I)),
    ("file", "ERR", re.compile(r"No such file|Permission denied|FileNotFoundError", re.I)),
    # WARN — notable but the pipeline handles it
    ("llm-empty", "WARN", re.compile(r"LLM returned empty content", re.I)),
    ("llm-skip", "WARN", re.compile(r"LLM call (attempt \d+|failed)|Chunk \d+: LLM call failed", re.I)),
    ("warn-tag", "WARN", re.compile(r"\[WARN\]")),
    ("timeout", "WARN", re.compile(r"timed out|Read timed out", re.I)),
]

# Lines containing any of these are benign noise — never reported.
_BENIGN = (
    "endpoint unsupported (HTTP 404)", "relying on JIT", "JIT will",
    "SyntaxWarning", "invalid escape sequence", "huggingface_hub", "symlink",
    "thinking not fully disabled", "no chat data available",
    "no event data available", "no_audio_source", "skipped_reason",
    "[GROUND]", "auto_fetch-disabled", "model likely already loaded",
    # normal "skipping" lines — not problems:
    "Found cached transcription", "Skipping transcription",
    "skipping VRAM swap", "models are the same", "already loaded",
    "skipping pre-load",
)

_SEV_ORDER = {"CRIT": 0, "ERR": 1, "WARN": 2}
_SEV_COLOR = {"CRIT": "31;1", "ERR": "31", "WARN": "33"}


def classify(line: str):
    if any(b in line for b in _BENIGN):
        return None
    for label, sev, rx in _RULES:
        if rx.search(line):
            return label, sev
    return None


# --- run discovery / parsing ------------------------------------------------
def _run_logs() -> list:
    d = PATHS.persistent_log_dir
    if not d.exists():
        return []
    return sorted(d.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)


def _parse_run(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    lines = text.splitlines()
    # filename: <date>_<time>_<slug>.log
    parts = path.stem.split("_", 2)
    vod = parts[2] if len(parts) == 3 else path.stem
    stage = ""
    for ln in lines:
        m = re.search(r">>>\s*(Stage[^\n]*)", ln)
        if m:
            stage = m.group(1).strip()
    exit_code = None
    m = re.search(r"exit=(\-?\d+)", text)
    if m:
        exit_code = int(m.group(1))
    clips = None
    m = re.search(r"Pipeline complete!\s*(\d+)\s*clip", text)
    if m:
        clips = int(m.group(1))
    findings = []
    for i, ln in enumerate(lines, 1):
        c = classify(ln)
        if c:
            findings.append((i, c[1], c[0], ln.strip()))
    return {
        "path": path, "vod": vod, "when": path.stem[:15],
        "stage": stage, "exit": exit_code, "clips": clips,
        "lines": lines, "findings": findings,
        "crit": sum(1 for f in findings if f[1] == "CRIT"),
        "err": sum(1 for f in findings if f[1] == "ERR"),
        "warn": sum(1 for f in findings if f[1] == "WARN"),
    }


def _resolve_run(arg: str | None) -> Path | None:
    runs = _run_logs()
    if not runs:
        return None
    if not arg:
        return runs[0]
    if arg.isdigit():
        i = int(arg) - 1
        return runs[i] if 0 <= i < len(runs) else None
    p = Path(arg)
    if p.exists():
        return p
    for r in runs:
        if arg.lower() in r.name.lower():
            return r
    return None


# --- commands ---------------------------------------------------------------
def cmd_doctor(args) -> int:
    banner("DOCTOR — environment preflight")
    fails = 0

    print(f"python : {sys.executable}  ({sys.version.split()[0]})")

    # CUDA DLLs (must precede the ctranslate2 import for a true GPU check)
    try:
        import cuda_bootstrap  # noqa: F401
    except Exception:
        pass
    nv = paths.nvidia_bin_dirs()
    print(f"{ok(bool(nv))} nvidia CUDA DLL dirs — {len(nv)} found")
    fails += not nv

    def check(mod, fn=None):
        nonlocal fails
        try:
            m = __import__(mod)
            detail = fn(m) if fn else getattr(m, "__version__", "ok")
            print(f"{ok(True)} import {mod:<20} {detail}")
        except Exception as e:  # noqa: BLE001
            print(f"{ok(False)} import {mod:<20} {type(e).__name__}: {e}")
            fails += 1

    check("torch", lambda m: f"{m.__version__}  cuda={m.cuda.is_available()}"
          + (f" ({m.cuda.get_device_name(0)})" if m.cuda.is_available() else ""))
    check("ctranslate2", lambda m: f"{m.__version__}  cuda_devices={m.get_cuda_device_count()}")
    check("faster_whisper")
    check("whisperx")
    check("flask")
    check("sentence_transformers")
    check("faiss")
    check("librosa")
    check("cv2", lambda m: m.__version__)
    check("soundfile")

    for exe in ("ffmpeg", "ffprobe"):
        w = shutil.which(exe)
        print(f"{ok(bool(w))} {exe:<26} {w or 'NOT on PATH'}")
        fails += not w

    # LM Studio
    cfg = {}
    cf = PATHS.config("models.json")
    if cf.exists():
        try:
            cfg = json.loads(cf.read_text(encoding="utf-8"))
        except Exception:
            pass
    url = str(cfg.get("llm_url", "http://localhost:1234")).replace("host.docker.internal", "localhost").rstrip("/")
    data = common._http_get_json(f"{url}/v1/models", timeout=5)
    avail = [m.get("id") for m in (data or {}).get("data", []) if m.get("id")] if data else None
    print(f"{ok(avail is not None)} LM Studio @ {url} — "
          + (f"{len(avail)} models available" if avail else "UNREACHABLE"))
    fails += avail is None
    if avail:
        for role in ("text_model", "vision_model", "text_model_passb", "vision_model_stage6"):
            mid = cfg.get(role)
            if mid:
                here = mid in avail
                print(f"  {ok(here)} {role} = {mid} {'(downloaded)' if here else '(NOT downloaded!)'}")
                fails += not here
    loaded = common._lms_loaded_ids()
    print(f"  loaded now (lms ps): {loaded or '(none — JIT will load on demand)'}")

    # paths + disk
    for f in ("work_dir", "vods_dir", "clips_dir", "whisper_cache", "config_dir"):
        d = getattr(PATHS, f)
        print(f"{ok(d.exists())} path {f:<14} {d}")
    try:
        du = shutil.disk_usage(str(PATHS.clips_dir if PATHS.clips_dir.exists() else PATHS.repo_root))
        print(f"       disk free: {du.free // (1024**3)} GB")
    except OSError:
        pass

    runs = _run_logs()
    if runs:
        r = _parse_run(runs[0])
        print(f"\nlatest run: {runs[0].name}  stage='{r['stage']}'  exit={r['exit']}  "
              f"crit={r['crit']} err={r['err']} warn={r['warn']} clips={r['clips']}")

    print(_c("32;1", "\nDOCTOR: all checks passed") if not fails
          else _c("31;1", f"\nDOCTOR: {fails} check(s) FAILED"))
    return 1 if fails else 0


def cmd_list(args) -> int:
    runs = [_parse_run(p) for p in _run_logs()[: args.n]]
    if args.json:
        print(json.dumps([{k: (str(r[k]) if k == "path" else r[k])
                            for k in ("path", "vod", "when", "stage", "exit", "clips", "crit", "err", "warn")}
                          for r in runs], indent=2))
        return 0
    banner(f"RECENT RUNS (latest {len(runs)})")
    print(f"{'#':<3} {'when':<16} {'vod':<34} {'exit':<5} {'crit':<5} {'err':<5} {'warn':<5} {'clips':<5} stage")
    for i, r in enumerate(runs, 1):
        ex = "-" if r["exit"] is None else str(r["exit"])
        crit = _c("31;1", str(r["crit"])) if r["crit"] else "0"
        err = _c("31", str(r["err"])) if r["err"] else "0"
        print(f"{i:<3} {r['when']:<16} {r['vod'][:34]:<34} {ex:<5} {crit:<5} {err:<5} "
              f"{r['warn']:<5} {str(r['clips']):<5} {r['stage']}")
    if not runs:
        print("(no runs in " + str(PATHS.persistent_log_dir) + ")")
    return 0


def cmd_errors(args) -> int:
    if args.all_runs:
        targets = _run_logs()[: args.n]
    else:
        one = _resolve_run(args.run)
        targets = [one] if one else []
    if not targets:
        print("no matching run(s)")
        return 1

    show_sev = {"CRIT", "ERR"} | ({"WARN"} if args.all else set())
    out = []
    for path in targets:
        r = _parse_run(path)
        flagged = [f for f in r["findings"] if f[1] in show_sev]
        if not flagged and not args.json:
            continue
        if args.json:
            out.append({"run": path.name, "exit": r["exit"], "stage": r["stage"],
                        "findings": [{"line": ln, "sev": sev, "kind": kind, "text": txt}
                                     for (ln, sev, kind, txt) in flagged]})
            continue
        banner(f"{path.name}  (exit={r['exit']}, stage='{r['stage']}', "
               f"crit={r['crit']} err={r['err']} warn={r['warn']})")
        # attribute each finding to the nearest preceding stage marker
        for (ln, sev, kind, txt) in flagged:
            stage = ""
            for j in range(ln - 1, -1, -1):
                m = re.search(r">>>\s*(Stage[^\n]*)", r["lines"][j])
                if m:
                    stage = m.group(1).strip()
                    break
            tag = _c(_SEV_COLOR[sev], f"{sev:<4}")
            print(f"  L{ln:<6} {tag} [{kind}] {_c('2', stage[:24]):<24} {txt[:140]}")
            if args.context:
                lo = max(0, ln - 1 - args.context)
                hi = min(len(r["lines"]), ln + args.context)
                for k in range(lo, hi):
                    print(_c("2", f"        {k+1}: {r['lines'][k][:160]}"))
    if args.json:
        print(json.dumps(out, indent=2))
    return 0


def cmd_show(args) -> int:
    p = _resolve_run(args.run)
    if not p:
        print("no matching run")
        return 1
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    if args.tail:
        lines = lines[-args.tail:]
    print(f"# {p}\n" + "\n".join(lines))
    return 0


def cmd_tail(args) -> int:
    f = PATHS.pipeline_log
    if not f.exists():
        print(f"no live log at {f} (no run in progress?)")
        return 1
    if not args.follow:
        lines = f.read_text(encoding="utf-8", errors="replace").splitlines()[-args.n:]
        print("\n".join(lines))
        return 0
    print(f"# following {f} (Ctrl+C to stop)")
    pos = 0
    try:
        while True:
            sz = f.stat().st_size
            if sz > pos:
                with open(f, "r", encoding="utf-8", errors="replace") as fh:
                    fh.seek(pos)
                    sys.stdout.write(fh.read())
                    pos = fh.tell()
            elif sz < pos:
                pos = 0
            time.sleep(0.5)
    except KeyboardInterrupt:
        return 0


def _diag_runs() -> list:
    d = PATHS.diagnostics_dir
    if not d.exists():
        return []
    return sorted(d.glob("last_run_*.json"), key=lambda p: p.name, reverse=True)


def _resolve_diag(arg: str | None) -> Path | None:
    runs = _diag_runs()
    if not runs:
        return None
    if not arg:
        return runs[0]
    if arg.isdigit():
        i = int(arg) - 1
        return runs[i] if 0 <= i < len(runs) else None
    p = Path(arg)
    if p.exists():
        return p
    for r in runs:
        if arg.lower() in r.name.lower():
            return r
    return None


def _moments_list(data: dict, key: str) -> list:
    """Pull a moment list from a diagnostics snapshot (lists are stored as
    ``{"count": N, "data": [...]}``; dicts are stored directly)."""
    v = data.get(key)
    if isinstance(v, dict) and "data" in v:
        return v.get("data") or []
    if isinstance(v, list):
        return v
    return []


def cmd_axes(args) -> int:
    """Selection-axis tuning view: axis report + rank churn + stage timing +
    Vision-Judge bracket, read back from a past run's diagnostics snapshot."""
    run = _resolve_diag(args.run)
    if not run:
        print("(no diagnostics snapshots in " + str(PATHS.diagnostics_dir) + ")")
        return 1
    try:
        data = json.loads(run.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"could not read {run}: {e}")
        return 1

    print(_c("1", f"== Axis diagnostics: {run.name} =="))

    # 1) Axis tuning report (dependency readiness + per-axis coverage/spread)
    rep = data.get("axis_report")
    if isinstance(rep, dict):
        deps = rep.get("dependencies", {})
        dep_str = " ".join(f"{k}={'on' if v else 'OFF'}" for k, v in deps.items())
        print(_c("36", f"\n-- axis report ({rep.get('candidates','?')} candidates, style={rep.get('style','?')}) --"))
        print(f"  deps: {dep_str}")
        gc = rep.get("global_clamp", {})
        print(f"  global clamp [{gc.get('floor')},{gc.get('ceil')}] bound {gc.get('bound_count', 0)} moment(s)")
        print(f"  {'axis':10s} {'active':>11s}  {'mult min/med/max':>22s}  {'ceil':>4s}")
        for name, blk in (rep.get("axes") or {}).items():
            ms = blk.get("multiplier", {})
            rng = f"{ms.get('min','-')}/{ms.get('median','-')}/{ms.get('max','-')}" if ms.get("n") else "-"
            act = f"{blk.get('active', 0)} ({blk.get('pct_active', 0)}%)"
            print(f"  {name:10s} {act:>11s}  {rng:>22s}  {blk.get('at_ceil', 0):>4d}")
    else:
        print("  (no axis_report — this run pre-dates the observability update)")

    # 2) Rank churn (base -> pass_c -> vision) over the delivered set
    moments = _moments_list(data, "scored_moments") or _moments_list(data, "hype_moments")
    if moments:
        print(_c("36", "\n-- rank churn (delivered clips: base -> passC -> vision) --"))
        print(f"  {'T':>7s} {'cat':12s} {'base':>4s} {'passC':>5s} {'vis':>4s} {'axis':>5s} {'score':>5s}")
        for m in sorted(moments, key=lambda m: (m.get("vision_rank") or m.get("pass_c_rank") or 999,
                                                -(m.get("score") or 0))):
            print(f"  {str(m.get('timestamp')):>7s} {str(m.get('category', ''))[:12]:12s} "
                  f"{str(m.get('base_rank', '-')):>4s} {str(m.get('pass_c_rank', '-')):>5s} "
                  f"{str(m.get('vision_rank', '-')):>4s} {str(m.get('axis_multiplier', '-')):>5s} "
                  f"{str(m.get('score', '-')):>5s}")

    # 3) Per-stage timing
    st = data.get("stage_timings")
    if isinstance(st, dict) and st.get("stages"):
        print(_c("36", f"\n-- stage timing (total {st.get('total_seconds','?')}s) --"))
        for t in st["stages"]:
            print(f"  {t.get('seconds', 0):>7.1f}s  {t.get('stage', '')}")

    # 4) Vision-Judge tournament bracket
    jt = data.get("judge_tournament")
    if isinstance(jt, dict) and jt.get("comparisons"):
        comps = jt["comparisons"]
        print(_c("36", f"\n-- vision-judge bracket ({len(comps)} comparisons, status={jt.get('status')}) --"))
        for c in comps[: args.judge_limit]:
            win = c.get("winner")
            conf = f" ({c['confidence']})" if c.get("confidence") is not None else ""
            print(f"  T{c.get('a')} vs T{c.get('b')} -> {('T' + str(win)) if win else 'tie'}{conf}  {str(c.get('reason', ''))[:70]}")
        if len(comps) > args.judge_limit:
            print(f"  ... {len(comps) - args.judge_limit} more (raise --judge-limit)")
    return 0


def main(argv) -> int:
    ap = argparse.ArgumentParser(prog="logtool", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("doctor")

    pl = sub.add_parser("list")
    pl.add_argument("-n", type=int, default=12)
    pl.add_argument("--json", action="store_true")

    pe = sub.add_parser("errors")
    pe.add_argument("run", nargs="?", default=None)
    pe.add_argument("-n", type=int, default=12)
    pe.add_argument("--all", action="store_true", help="include WARN-level findings")
    pe.add_argument("--all-runs", action="store_true", help="scan the last -n runs")
    pe.add_argument("-C", "--context", type=int, default=0, help="lines of context")
    pe.add_argument("--json", action="store_true")

    ps = sub.add_parser("show")
    ps.add_argument("run", nargs="?", default=None)
    ps.add_argument("--tail", type=int, default=0)

    pt = sub.add_parser("tail")
    pt.add_argument("-n", type=int, default=60)
    pt.add_argument("--follow", action="store_true")

    pax = sub.add_parser("axes")
    pax.add_argument("run", nargs="?", default=None,
                     help="diagnostics run: index from newest, name substring, or path (default: latest)")
    pax.add_argument("--judge-limit", type=int, default=20, help="max judge comparisons to print")

    args = ap.parse_args(argv)
    if args.cmd == "doctor":
        return cmd_doctor(args)
    if args.cmd == "list":
        return cmd_list(args)
    if args.cmd == "errors":
        return cmd_errors(args)
    if args.cmd == "show":
        return cmd_show(args)
    if args.cmd == "tail":
        return cmd_tail(args)
    if args.cmd == "axes":
        return cmd_axes(args)
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
