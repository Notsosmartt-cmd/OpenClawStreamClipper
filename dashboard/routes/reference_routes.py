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
import re
import subprocess
import sys
import time
from pathlib import Path

from flask import Blueprint, jsonify, request

from .. import _state
from ..pipeline_runner import is_pipeline_running, is_reference_running, use_docker_exec

bp = Blueprint("reference_routes", __name__)

REPO = _state.PROJECT_DIR
REF_DIR = REPO / "reference_clips"
CACHE = REF_DIR / ".cache"
NOTES_DIR = REF_DIR / "notes"   # owner notes grouped here (2026-07-13 reorg); legacy sidecars still read
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
        notes = NOTES_DIR / f"{stem}.notes.json"   # canonical grouped location
        if not notes.exists():                      # fall back to a legacy top-level sidecar
            legacy = f.with_suffix(".notes.json")
            notes = legacy if legacy.exists() else (REF_DIR / f"{stem}.notes.json")
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
    key on — SESSION start time since 2026-07-15, so a multi-VOD batch is ONE run),
    newest first, with clip counts + the VOD names in the batch (owner req: label a
    30-clip batch entry with its member VODs instead of timestamp fragments)."""
    stamps: dict[str, dict] = {}
    if EFFECTS_LOG.exists():
        for line in EFFECTS_LOG.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                r = json.loads(line)
            except Exception:
                continue
            run = r.get("run")
            if run and r.get("type") == "render_plan":
                agg = stamps.setdefault(run, {"renders": 0, "vods": []})
                agg["renders"] += 1
                vod = Path(str(r.get("vod") or "")).stem
                if vod and vod not in agg["vods"]:
                    agg["vods"].append(vod)
    runs = []
    for stamp in sorted(stamps, reverse=True):
        carded = (DIAG / "cards" / stamp).is_dir()
        runs.append({"stamp": stamp, "renders": stamps[stamp]["renders"],
                     "vods": stamps[stamp]["vods"], "carded": carded})
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


def _start_job(name: str, script: str, args: list[str], env_extra: dict | None = None):
    ok, err = _guard()
    if not ok:
        return None, err
    JOB_LOG.parent.mkdir(parents=True, exist_ok=True)
    lf = open(JOB_LOG, "w", encoding="utf-8")
    env = {**os.environ, "KMP_DUPLICATE_LIB_OK": "TRUE",
           "HF_HUB_DISABLE_SYMLINKS_WARNING": "1", "PYTHONUNBUFFERED": "1",
           **(env_extra or {})}
    # BUG 71c: writable numba JIT cache (librosa-importing jobs spin at 1 core
    # for minutes without it when Python lives under C:\Program Files).
    try:
        import sys as _sys
        _sys.path.insert(0, str(REPO / "scripts" / "lib"))
        from paths import ensure_numba_cache_env as _ence
        _ence(env)
    except Exception:
        pass
    # W0.1: pin to the repo venv (whisperx/pyannote + Lab decompose deps live there),
    # never the dashboard's own interpreter chain.
    cmd = [_state.repo_python(), str(RESEARCH / script), *args]
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
def _default_model() -> str:
    try:
        cfg = json.loads((REPO / "config" / "models.json").read_text(encoding="utf-8"))
        return cfg.get("text_model") or ""
    except Exception:
        return ""


@bp.route("/api/reference/corpus")
def api_ref_corpus():
    clips = _corpus()
    return jsonify({
        "dir": str(REF_DIR), "clips": clips, "runs": _effects_runs(),
        "default_model": _default_model(),
        "counts": {"total": len(clips),
                   "decomposed": sum(1 for c in clips if c["decomposed"]),
                   "carded": sum(1 for c in clips if c["carded"])},
    })


# Two owner-facing actions (Clipper-style UX, 2026-07-12): "Analyze" chains
# decompose-if-missing + card; "Compare" chains card-our-clips (missing only) +
# gap report. The old 4-step endpoints (decompose/cards/our-cards/diff) were
# removed — the numbered workflow confused the owner.

def _model_env(data: dict) -> tuple[dict | None, str]:
    """Per-job LLM override (owner req 2026-07-13): the Lab's LLM calls (card
    pass + report narrative) resolve via clip_forensics._llm_config(), which
    reads CLIP_TEXT_MODEL first — so a job-scoped env override selects the
    model exactly like the Clipper's models.json does for the pipeline.
    Empty model = pipeline default (config/models.json text_model)."""
    model = (data.get("model") or "").strip()
    if not model:
        return None, ""
    return {"CLIP_TEXT_MODEL": model}, f" [{model.split('/')[-1]}]"


@bp.route("/api/reference/analyze", methods=["POST"])
def api_ref_analyze():
    data = request.get_json(force=True) or {}
    stems = [str(s).strip() for s in (data.get("stems") or []) if str(s).strip()]
    env_extra, mtag = _model_env(data)
    if stems:
        args = ["--clips", ",".join(stems)]
        label = f"analyze ({len(stems)} selected){mtag}"
    else:
        args = ["--all-new"]
        label = f"analyze (all new){mtag}"
    proc, err = _start_job(label, "reference_analyze.py", args, env_extra)
    if err:
        return jsonify({"error": err}), 409
    return jsonify({"status": "started", "job": label}), 202


@bp.route("/api/reference/compare", methods=["POST"])
def api_ref_compare():
    data = request.get_json(force=True) or {}
    # Accept one run (`run`) or many (`runs`: [...]) — multi-run aggregates our
    # clips across the selected runs into ONE comparison (steadier medians).
    runs = [str(r).strip() for r in (data.get("runs") or []) if str(r).strip()]
    if not runs and (data.get("run") or "").strip():
        runs = [(data.get("run") or "").strip()]
    if not runs:
        return jsonify({"error": "pick at least one clip run to compare against"}), 400
    env_extra, mtag = _model_env(data)
    label = runs[0] if len(runs) == 1 else f"{len(runs)} runs"
    proc, err = _start_job(f"compare vs {label}{mtag}", "reference_compare.py",
                           ["--runs", ",".join(runs)], env_extra)
    if err:
        return jsonify({"error": err}), 409
    return jsonify({"status": "started", "job": "compare", "runs": runs}), 202


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
    progress = None
    try:
        if JOB_LOG.exists():
            _lines = JOB_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
            tail = "\n".join(_lines[-60:])
            # Owner req 2026-07-15: surface WHICH item the job is on + counts.
            # Jobs print "[i/N] <clip-or-step name>" progress markers — parse the
            # newest one so the panel can show "Analyzing 17/86 — <name>".
            for _ln in reversed(_lines[-200:]):
                _m = re.search(r"\[(\d+)/(\d+)\]\s*(\S.*)?$", _ln.strip())
                if _m:
                    progress = {"index": int(_m.group(1)), "total": int(_m.group(2)),
                                "current": (_m.group(3) or "").strip()[:80]}
                    break
    except Exception:
        progress = None
    rc = None
    if j and j.get("proc") and not running:
        rc = j["proc"].poll()
    return jsonify({
        "running": running,
        "name": (j or {}).get("name"),
        "elapsed": int(time.time() - (j or {}).get("started", time.time())) if j else 0,
        "returncode": rc,
        "progress": progress,
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


# Plain-language labels for the export (kept in sync with reference-panel.js).
_METRIC_LABELS = {
    "sfx_per_30s_med": "Sound effects per 30s",
    "cuts_per_30s_med": "Cuts per 30s",
    "caption_wps_med": "Caption words/sec",
    "caption_casing_top": "Caption casing",
    "chat_overlay_pct": "Chat overlay usage",
    "zooms_med": "Zoom punches",
    "sfx_offset_ms_med": "SFX timing offset (ms)",
    "category_coverage": "Format we never produce",
}


def _humanize(item_id: str, metric: str) -> str:
    scope, _, m = (item_id or "").partition(":")
    label = _METRIC_LABELS.get(m) or _METRIC_LABELS.get(metric) or m or item_id
    where = ("all clips" if scope == "ALL"
             else "" if scope == "coverage" else scope.replace("_", " "))
    return f"{label} — {where}" if where else label


@bp.route("/api/reference/approvals-export")
def api_ref_approvals_export():
    """The JUDGED report (owner req 2026-07-13): one copy-ready markdown doc
    merging the gap report's explained diffs with the owner's approve/reject
    verdicts. Also written to clips/.diagnostics/corpus_diff_<date>_judged.md
    so it exists on disk beside the raw report."""
    which = (request.args.get("date") or "latest").strip()
    reports = sorted(DIAG.glob("corpus_diff_*.json"))
    reports = [r for r in reports if not r.stem.endswith("_judged")]
    if not reports:
        return jsonify({"error": "no gap report yet"}), 404
    jf = reports[-1] if which == "latest" else DIAG / f"corpus_diff_{which}.json"
    if not jf.exists():
        return jsonify({"error": "report not found"}), 404
    try:
        rep = json.loads(jf.read_text(encoding="utf-8"))
    except Exception as e:
        return jsonify({"error": f"unreadable report: {e}"}), 500
    date = rep.get("date") or jf.stem.replace("corpus_diff_", "")
    verdicts = _read_approvals().get("reports", {}).get(date, {})

    by = {"approved": [], "rejected": [], "no-action": [], "unjudged": []}
    for it in rep.get("items", []):
        v = verdicts.get(it.get("id")) or {}
        by.get(v.get("verdict") or "unjudged", by["unjudged"]).append((it, v))

    md = [f"# Judged gap report — {date} (vs clip run {rep.get('run')})", "",
          f"Reference corpus vs our clips; each finding carries the config lever it maps to. "
          f"Verdicts: {len(by['approved'])} approved · {len(by['rejected'])} rejected · "
          f"{len(by['no-action'])} no-action · {len(by['unjudged'])} unjudged.", ""]
    _sec = {"approved": "✅ APPROVED — apply these levers",
            "rejected": "❌ REJECTED — not problems",
            "no-action": "➖ NO ACTION",
            "unjudged": "❓ UNJUDGED"}
    for key, title in _sec.items():
        if not by[key]:
            continue
        md += [f"## {title}", ""]
        for it, v in by[key]:
            md += [f"### {_humanize(it.get('id'), it.get('metric'))}",
                   f"- reference: `{it.get('reference')}` · ours: `{it.get('ours')}` "
                   f"(gap {it.get('gap')})",
                   f"- lever: `{it.get('lever')}`",
                   f"- explanation: {it.get('note')}"]
            if v.get("reason"):
                md += [f"- verdict reason: {v['reason']}"]
            md += [""]
    text = "\n".join(md)
    out = DIAG / f"corpus_diff_{date}_judged.md"
    try:
        out.write_text(text, encoding="utf-8")
    except Exception:
        pass
    return jsonify({"ok": True, "date": date, "markdown": text, "path": str(out),
                    "counts": {k: len(v) for k, v in by.items()}})


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
