"""Reference Lab routes — the full reverse-engineering loop from the UI (R6).

Drives the reference-deconstruction pipeline
(concepts/plan-reference-deconstruction-2026-07) end-to-end from the dashboard,
replacing the old single-clip Forensics tab:

    decompose (R0) -> attribute cards (R1) -> card our clips (R2)
        -> gap report (R3) -> approve / reject each gap item (R4 queue)

Each heavy step runs as ONE background subprocess (streamed to a log the UI
polls). Mutual exclusion: refuses to start while the clip pipeline runs (and the
clip routes refuse while a reference job runs) — the two share the GPU + LM
Studio. Bare-metal only. Approvals are written to the R4 queue
(clips/.diagnostics/diff_approvals.json); nothing auto-applies — an agent works
the queue into config/prompt commits.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

from flask import Blueprint, jsonify, request

from .. import _state
from ..pipeline_runner import is_pipeline_running, is_reference_running, use_docker_exec

bp = Blueprint("reference_routes", __name__)

REPO = _state.PROJECT_DIR
REF_DIR = REPO / "reference_clips"
CACHE = REF_DIR / ".cache"
DIAG = _state.CLIPS_DIR / ".diagnostics"
RESEARCH = REPO / "scripts" / "research"
EFFECTS_LOG = DIAG / "effects_log.jsonl"
APPROVALS = DIAG / "diff_approvals.json"
JOB_LOG = _state.TEMP_DIR / "reference_job.log"
_VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}


# ---------------------------------------------------------------------------
# Corpus + runs inventory
# ---------------------------------------------------------------------------
def _corpus() -> list[dict]:
    out: list[dict] = []
    if not REF_DIR.is_dir():
        return out
    for f in sorted(REF_DIR.iterdir()):
        if not f.is_file() or f.suffix.lower() not in _VIDEO_EXT:
            continue
        stem = f.stem
        tl = CACHE / f"{stem}.timeline.json"
        card = CACHE / f"{stem}.card.json"
        notes = f.with_suffix(".notes.json")
        if not notes.exists():
            notes = REF_DIR / f"{stem}.notes.json"
        notes_state = "none"
        if notes.exists():
            try:
                notes_state = "draft" if json.loads(
                    notes.read_text(encoding="utf-8")).get("_draft") else "corrected"
            except Exception:
                notes_state = "unreadable"
        category = None
        if card.exists():
            try:
                category = json.loads(card.read_text(encoding="utf-8")).get("category")
            except Exception:
                pass
        out.append({"name": f.name, "stem": stem, "size_bytes": f.stat().st_size,
                    "decomposed": tl.exists(), "carded": card.exists(),
                    "category": category, "notes": notes_state})
    return out


def _effects_runs() -> list[dict]:
    """Distinct clip-run stamps from effects_log (the stamp our_clip_cards/corpus_diff
    key on — pipeline START time), newest first, marked if our-cards already exist."""
    stamps: dict[str, int] = {}
    if EFFECTS_LOG.exists():
        for line in EFFECTS_LOG.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                r = json.loads(line)
            except Exception:
                continue
            run = r.get("run")
            if run and r.get("type") == "render_plan":
                stamps[run] = stamps.get(run, 0) + 1
    runs = []
    for stamp in sorted(stamps, reverse=True):
        carded = (DIAG / "cards" / stamp).is_dir()
        runs.append({"stamp": stamp, "renders": stamps[stamp], "carded": carded})
    return runs


# ---------------------------------------------------------------------------
# Background job runner (one at a time, mutually exclusive with the pipeline)
# ---------------------------------------------------------------------------
def _job_running() -> bool:
    return is_reference_running()


def _guard() -> tuple[bool, str]:
    if use_docker_exec():
        return False, "Reference Lab runs bare-metal only."
    if is_pipeline_running():
        return False, "The clip pipeline is running — wait for it to finish."
    if _job_running():
        return False, f"A reference job ({(_state.reference_job or {}).get('name')}) is already running."
    return True, ""


def _start_job(name: str, script: str, args: list[str]):
    ok, err = _guard()
    if not ok:
        return None, err
    JOB_LOG.parent.mkdir(parents=True, exist_ok=True)
    lf = open(JOB_LOG, "w", encoding="utf-8")
    env = {**os.environ, "KMP_DUPLICATE_LIB_OK": "TRUE",
           "HF_HUB_DISABLE_SYMLINKS_WARNING": "1", "PYTHONUNBUFFERED": "1"}
    cmd = [sys.executable, str(RESEARCH / script), *args]
    kwargs = {"stdout": lf, "stderr": subprocess.STDOUT, "cwd": str(REPO), "env": env}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    proc = subprocess.Popen(cmd, **kwargs)
    _state.reference_job = {"name": name, "proc": proc, "started": time.time(),
                            "log": str(JOB_LOG), "_lf": lf}
    return proc, None


def _stop_job() -> bool:
    j = _state.reference_job
    if not (j and j.get("proc")):
        return False
    proc = j["proc"]
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, timeout=15)
        else:
            proc.terminate()
    except Exception:
        pass
    return True


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@bp.route("/api/reference/corpus")
def api_ref_corpus():
    clips = _corpus()
    return jsonify({
        "dir": str(REF_DIR), "clips": clips, "runs": _effects_runs(),
        "counts": {"total": len(clips),
                   "decomposed": sum(1 for c in clips if c["decomposed"]),
                   "carded": sum(1 for c in clips if c["carded"])},
    })


# Two owner-facing actions (Clipper-style UX, 2026-07-12): "Analyze" chains
# decompose-if-missing + card; "Compare" chains card-our-clips (missing only) +
# gap report. The old 4-step endpoints (decompose/cards/our-cards/diff) were
# removed — the numbered workflow confused the owner.

@bp.route("/api/reference/analyze", methods=["POST"])
def api_ref_analyze():
    data = request.get_json(force=True) or {}
    stems = [str(s).strip() for s in (data.get("stems") or []) if str(s).strip()]
    if stems:
        args = ["--clips", ",".join(stems)]
        label = f"analyze ({len(stems)} selected)"
    else:
        args = ["--all-new"]
        label = "analyze (all new)"
    proc, err = _start_job(label, "reference_analyze.py", args)
    if err:
        return jsonify({"error": err}), 409
    return jsonify({"status": "started", "job": label}), 202


@bp.route("/api/reference/compare", methods=["POST"])
def api_ref_compare():
    data = request.get_json(force=True) or {}
    run = (data.get("run") or "").strip()
    if not run:
        return jsonify({"error": "pick a clip run to compare against"}), 400
    proc, err = _start_job(f"compare vs {run}", "reference_compare.py", ["--run", run])
    if err:
        return jsonify({"error": err}), 409
    return jsonify({"status": "started", "job": "compare", "run": run}), 202


@bp.route("/api/reference/stop", methods=["POST"])
def api_ref_stop():
    if not _job_running():
        return jsonify({"error": "no reference job running"}), 404
    _stop_job()
    return jsonify({"status": "stopped"})


@bp.route("/api/reference/job")
def api_ref_job():
    j = _state.reference_job
    running = _job_running()
    tail = ""
    try:
        if JOB_LOG.exists():
            tail = "\n".join(
                JOB_LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-60:])
    except Exception:
        pass
    rc = None
    if j and j.get("proc") and not running:
        rc = j["proc"].poll()
    return jsonify({
        "running": running,
        "name": (j or {}).get("name"),
        "elapsed": int(time.time() - (j or {}).get("started", time.time())) if j else 0,
        "returncode": rc,
        "log": tail,
    })


@bp.route("/api/reference/card")
def api_ref_card():
    stem = (request.args.get("stem") or "").strip()
    card = CACHE / f"{stem}.card.json"
    if not stem or not card.exists():
        return jsonify({"error": "no card for that clip"}), 404
    try:
        return jsonify({"ok": True, "card": json.loads(card.read_text(encoding="utf-8"))})
    except Exception as e:
        return jsonify({"error": f"unreadable card: {e}"}), 500


def _read_approvals() -> dict:
    if APPROVALS.exists():
        try:
            return json.loads(APPROVALS.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"reports": {}}


@bp.route("/api/reference/report")
def api_ref_report():
    """The newest (or ?date=) gap report + each item's current approval verdict."""
    which = (request.args.get("date") or "latest").strip()
    reports = sorted(DIAG.glob("corpus_diff_*.json"))
    if not reports:
        return jsonify({"error": "no gap report yet — run one"}), 404
    if which == "latest":
        jf = reports[-1]
    else:
        jf = DIAG / f"corpus_diff_{which}.json"
        if not jf.exists():
            return jsonify({"error": "report not found"}), 404
    try:
        rep = json.loads(jf.read_text(encoding="utf-8"))
    except Exception as e:
        return jsonify({"error": f"unreadable report: {e}"}), 500
    date = rep.get("date") or jf.stem.replace("corpus_diff_", "")
    md = jf.with_suffix(".md")
    verdicts = _read_approvals().get("reports", {}).get(date, {})
    for it in rep.get("items", []):
        v = verdicts.get(it.get("id"))
        it["verdict"] = (v or {}).get("verdict")
        it["reason"] = (v or {}).get("reason")
    return jsonify({
        "ok": True, "date": date, "run": rep.get("run"),
        "items": rep.get("items", []),
        "markdown": md.read_text(encoding="utf-8") if md.exists() else "",
        "available": [p.stem.replace("corpus_diff_", "") for p in reversed(reports)],
    })


@bp.route("/api/reference/approve", methods=["POST"])
def api_ref_approve():
    data = request.get_json(force=True) or {}
    date = (data.get("date") or "").strip()
    item = (data.get("item") or "").strip()
    verdict = (data.get("verdict") or "").strip()
    if not (date and item and verdict in ("approved", "rejected", "no-action")):
        return jsonify({"error": "date, item, and verdict (approved|rejected|no-action) required"}), 400
    appr = _read_approvals()
    appr.setdefault("reports", {}).setdefault(date, {})[item] = {
        "verdict": verdict,
        "reason": (data.get("reason") or f"owner via dashboard {time.strftime('%Y-%m-%d')}").strip(),
    }
    APPROVALS.parent.mkdir(parents=True, exist_ok=True)
    APPROVALS.write_text(json.dumps(appr, indent=2), encoding="utf-8")
    return jsonify({"ok": True, "item": item, "verdict": verdict})
