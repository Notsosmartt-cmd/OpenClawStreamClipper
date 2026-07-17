"""Clip score index — powers the poster's "Top rated" filter.

The pipeline's per-clip scores live in a few places, none of them designed as
a clip-file index, so this module joins what exists (newest-first):

1. ``clips/.diagnostics/clip_scores.jsonl`` — the durable per-render index
   (rows: {"clip": <filename stem incl. variant>, "score": float,
   "judge": float|null, "category": str}). The pipeline gains this writer
   in a follow-up; the reader is here first so scores appear the moment it
   ships. Later lines win (append-only).
2. ``last_run_*.json`` traces — ``clips_made`` lines are pipe-delimited
   ``<stem incl. variant>|<score>|<category>|...`` (a DIRECT filename join),
   plus ``enriched_<t>``/``moment_<t>`` sidecars and ``hype_moments.data``
   joined via the stage7 title sanitize (alnum/space/hyphen, [:50]).

Traces are per-invocation snapshots: a batch that is STOPPED mid-queue never
writes one (which is why 2026-07-16's daytime batches are unscored). Clips
without a resolvable score stay visible — the UI filter hides them with an
explicit count, never silently.

``score`` is the pipeline's composite ranking currency (~0.3-2.0, position
and axis weighted); ``judge`` is the S4.5 text-judge 0-10 when present.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

_TRACE_LIMIT = 50          # newest traces parsed per rebuild
_cache: dict = {"fp": None, "index": {}}

_SANITIZE_RE = re.compile(r"[^0-9A-Za-z \-]+")


def _sanitize_title(title: str) -> str:
    """stage7.py:123 — filename = alnum/space/hyphen chars of title, [:50]."""
    return "".join(c for c in str(title) if c.isalnum() or c in " -")[:50].strip()


def _fingerprint(diag: Path) -> tuple:
    try:
        traces = sorted(diag.glob("last_run_*.json"))
        newest = max((p.stat().st_mtime for p in traces), default=0)
        cs = diag / "clip_scores.jsonl"
        return (len(traces), newest,
                cs.stat().st_mtime if cs.exists() else 0)
    except OSError:
        return (0, 0, 0)


def _put(index: dict, key: str, score, judge=None, category=None) -> None:
    key = key.casefold().strip()
    if not key:
        return
    entry = index.get(key)
    if entry is None:
        index[key] = {"score": score, "judge": judge, "category": category}
    else:  # newest-first iteration: only fill gaps, never overwrite
        if entry.get("judge") is None and judge is not None:
            entry["judge"] = judge
        if entry.get("category") is None and category:
            entry["category"] = category


def _ingest_clip_scores(diag: Path, index: dict) -> None:
    p = diag / "clip_scores.jsonl"
    if not p.exists():
        return
    rows = []
    for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            rows.append(json.loads(ln))
        except Exception:
            continue
    for row in reversed(rows):  # later lines win
        clip = row.get("clip")
        if clip:
            _put(index, str(clip), row.get("score"), row.get("judge"),
                 row.get("category"))


def _ingest_traces(diag: Path, index: dict) -> None:
    traces = sorted(diag.glob("last_run_*.json"), reverse=True)[:_TRACE_LIMIT]
    for p in traces:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        # (a) clips_made: "<stem incl variant>|<score>|<category>|..."
        for ln in data.get("clips_made") or []:
            if not isinstance(ln, str) or "|" not in ln:
                continue
            parts = ln.split("|")
            try:
                score = float(parts[1])
            except (IndexError, ValueError):
                continue
            cat = parts[2].strip() if len(parts) > 2 else None
            _put(index, parts[0], score, None, cat)
        # (b) per-moment sidecars + the hype list — title-joined
        candidates = []
        for k, v in data.items():
            if (k.startswith("enriched_") or k.startswith("moment_")) \
                    and isinstance(v, dict) and v.get("title"):
                candidates.append(v)
        hype = (data.get("hype_moments") or {}).get("data")
        if isinstance(hype, list):
            candidates.extend(m for m in hype
                              if isinstance(m, dict) and m.get("title"))
        for m in candidates:
            judge = (m.get("s45_judge") or {}).get("score")
            _put(index, _sanitize_title(m["title"]), m.get("score"),
                 judge, m.get("category"))


def get_index(diagnostics_dir: Path) -> dict:
    """{casefolded clip stem (or title base): {score, judge, category}} —
    cached until the diagnostics dir changes."""
    fp = _fingerprint(diagnostics_dir)
    if _cache["fp"] == fp:
        return _cache["index"]
    index: dict = {}
    try:
        _ingest_clip_scores(diagnostics_dir, index)
        _ingest_traces(diagnostics_dir, index)
    except Exception:
        pass  # failure-soft: worst case the filter shows everything unscored
    _cache["fp"] = fp
    _cache["index"] = index
    return index


_VARIANT_RE = re.compile(r"\s*\((?:B|Short)\)\s*$", re.IGNORECASE)


def lookup(index: dict, stem: str) -> dict | None:
    """Exact stem first (clips_made rows include the variant marker), then
    the variant-stripped base (title-derived entries never carry it)."""
    hit = index.get(stem.casefold().strip())
    if hit:
        return hit
    return index.get(_VARIANT_RE.sub("", stem).strip().casefold())
