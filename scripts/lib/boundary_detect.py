#!/usr/bin/env python3
"""Clip boundary snap — Phase 4.2 of the 2026 upgrade.

Pragmatic variable-length windows without CG-DETR. Given a candidate
moment at timestamp ``T`` with tentative boundaries ``(clip_start,
clip_end)``, snap each boundary to the nearest "good" cut point:

- **Sentence boundary** (primary): word-level timestamps from the
  transcript.json that Phase 3 WhisperX produces. Snap start to the
  nearest word-start within ``max_start_drift_sec`` backward; snap end
  to the nearest word-end within ``max_end_drift_sec`` FORWARD (we
  prefer including extra payoff to chopping it).

- **Silence gap** (secondary): after sentence-snap, further nudge to a
  silence gap (a gap between consecutive words > ``silence_threshold_ms``)
  within a small extra drift budget. This produces cleaner audio cut
  points for Stage 7 render.

- **Shot cut** (optional, off by default): TransNet V2 shot-boundary
  detection. Disabled in ``config/boundaries.json`` unless the user
  explicitly wants the extra model dep.

All snaps are bounded by a duration_bounds safety clamp — a snap that
would produce a clip shorter than ``min_sec`` or longer than ``max_sec``
is rejected and the original tentative boundaries are kept.

CG-DETR / SG-DETR / Lighthouse are NOT implemented here — those need a
proper QVHighlights-trained eval harness which is Phase 5 scope.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

DEFAULT_BOUNDARIES_CONFIG = Path(
    os.environ.get("CLIP_BOUNDARIES_CONFIG", "/root/.openclaw/boundaries.json")
)


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def load_boundaries_config(path: Optional[str] = None) -> dict:
    cfg = _read_json(Path(path) if path else DEFAULT_BOUNDARIES_CONFIG)
    cfg.setdefault("enabled", True)
    cfg.setdefault(
        "sentence_snap",
        {"enabled": True, "max_start_drift_sec": 3.0, "max_end_drift_sec": 8.0},
    )
    cfg.setdefault(
        "silence_snap",
        {"enabled": True, "silence_threshold_ms": 250, "max_extra_drift_sec": 1.5},
    )
    cfg.setdefault("shot_cut_snap", {"enabled": False})
    cfg.setdefault("duration_bounds", {"min_sec": 15, "max_sec": 90})
    return cfg


# ---------------------------------------------------------------------------
# Transcript timeline — extract word-level (start, end) pairs
# ---------------------------------------------------------------------------


def load_word_timeline(transcript_path: str) -> List[Tuple[float, float]]:
    """Return a time-ordered list of (word_start, word_end) pairs.

    Uses word-level timestamps when present (Phase 3 WhisperX output);
    falls back to segment-level (start, end) when segments lack a
    ``words`` array. Empty list when the transcript is missing or empty.
    """
    p = Path(transcript_path)
    if not p.exists():
        return []
    try:
        segments = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(segments, list):
        return []

    out: List[Tuple[float, float]] = []
    for seg in segments:
        words = seg.get("words") if isinstance(seg, dict) else None
        if words:
            for w in words:
                try:
                    s = float(w.get("start"))
                    e = float(w.get("end"))
                    if e >= s:
                        out.append((s, e))
                except (TypeError, ValueError):
                    continue
        else:
            try:
                s = float(seg["start"])
                e = float(seg["end"])
                if e >= s:
                    out.append((s, e))
            except (KeyError, TypeError, ValueError):
                continue
    out.sort(key=lambda x: x[0])
    return out


# ---------------------------------------------------------------------------
# Sentence boundary snap
# ---------------------------------------------------------------------------


def snap_to_word_boundary(
    tentative_t: float,
    timeline: List[Tuple[float, float]],
    direction: str,
    max_drift_sec: float,
) -> float:
    """Snap ``tentative_t`` to the nearest word-boundary in the requested
    direction. ``direction`` is ``"start"`` (snap to a word-start,
    searching backward then forward) or ``"end"`` (snap to a word-end,
    searching forward preferred then backward).

    Returns the original ``tentative_t`` when no suitable boundary
    exists within ``max_drift_sec``.
    """
    if not timeline:
        return tentative_t

    best = None
    best_dist = max_drift_sec + 1.0  # larger than any allowed drift

    if direction == "start":
        # Prefer a word-START at or before the tentative time; if none in
        # range, take the nearest word-START after it.
        for ws, _we in timeline:
            d = tentative_t - ws  # positive when ws is earlier than tentative
            if 0 <= d <= max_drift_sec:
                if d < best_dist:
                    best_dist = d
                    best = ws
        if best is None:
            # Fall through: look forward up to max_drift_sec
            for ws, _we in timeline:
                d = ws - tentative_t
                if 0 <= d <= max_drift_sec and d < best_dist:
                    best_dist = d
                    best = ws
    else:  # "end"
        # Prefer a word-END at or after the tentative time; if none in
        # range, take the nearest word-END before it.
        for _ws, we in timeline:
            d = we - tentative_t
            if 0 <= d <= max_drift_sec:
                if d < best_dist:
                    best_dist = d
                    best = we
        if best is None:
            for _ws, we in timeline:
                d = tentative_t - we
                if 0 <= d <= max_drift_sec and d < best_dist:
                    best_dist = d
                    best = we

    return float(best) if best is not None else tentative_t


# ---------------------------------------------------------------------------
# Silence gap snap
# ---------------------------------------------------------------------------


def detect_silence_gaps(
    timeline: List[Tuple[float, float]], threshold_sec: float = 0.25
) -> List[Tuple[float, float]]:
    """Return (gap_start, gap_end) for every inter-word gap >= threshold."""
    if len(timeline) < 2:
        return []
    gaps: List[Tuple[float, float]] = []
    for i in range(1, len(timeline)):
        prev_end = timeline[i - 1][1]
        curr_start = timeline[i][0]
        if curr_start - prev_end >= threshold_sec:
            gaps.append((prev_end, curr_start))
    return gaps


def nudge_to_silence(
    t: float,
    gaps: List[Tuple[float, float]],
    max_extra_drift_sec: float,
) -> float:
    """Nudge ``t`` into the nearest silence gap within ``max_extra_drift_sec``.

    If a gap's endpoint is within drift, snap to that endpoint. Returns
    ``t`` unchanged when no nearby gap exists.
    """
    if not gaps:
        return t
    best = None
    best_dist = max_extra_drift_sec + 1.0
    for gs, ge in gaps:
        # Prefer aligning to the START of a gap on an end-snap context
        # or the END of a gap on a start-snap context. But we don't know
        # the context here — pick the nearer endpoint.
        for endpoint in (gs, ge):
            d = abs(endpoint - t)
            if d <= max_extra_drift_sec and d < best_dist:
                best_dist = d
                best = endpoint
    return float(best) if best is not None else t


# ---------------------------------------------------------------------------
# Top-level snap
# ---------------------------------------------------------------------------


def snap_boundaries(
    tentative_start: float,
    tentative_end: float,
    timeline: Optional[List[Tuple[float, float]]] = None,
    config: Optional[dict] = None,
    transcript_path: Optional[str] = None,
) -> Dict:
    """Snap (tentative_start, tentative_end) to nearby sentence + silence
    boundaries. Either ``timeline`` or ``transcript_path`` must be provided.

    Returns:

        {
          "clip_start": float,
          "clip_end": float,
          "clip_duration": float,
          "snapped": bool,           # True if either boundary moved
          "source": "sentence+silence" | "sentence" | "none",
          "drift_start_sec": float,
          "drift_end_sec": float,
        }
    """
    cfg = config if config is not None else load_boundaries_config()
    result = {
        "clip_start": float(tentative_start),
        "clip_end": float(tentative_end),
        "clip_duration": float(tentative_end - tentative_start),
        "snapped": False,
        "source": "none",
        "drift_start_sec": 0.0,
        "drift_end_sec": 0.0,
    }

    if not cfg.get("enabled", True):
        return result

    if timeline is None:
        if not transcript_path:
            return result
        timeline = load_word_timeline(transcript_path)
    if not timeline:
        return result

    new_start = float(tentative_start)
    new_end = float(tentative_end)
    source_parts: List[str] = []

    sent_cfg = cfg.get("sentence_snap") or {}
    if sent_cfg.get("enabled", True):
        new_start = snap_to_word_boundary(
            new_start,
            timeline,
            direction="start",
            max_drift_sec=float(sent_cfg.get("max_start_drift_sec", 3.0)),
        )
        new_end = snap_to_word_boundary(
            new_end,
            timeline,
            direction="end",
            max_drift_sec=float(sent_cfg.get("max_end_drift_sec", 8.0)),
        )
        if new_start != tentative_start or new_end != tentative_end:
            source_parts.append("sentence")

    sil_cfg = cfg.get("silence_snap") or {}
    if sil_cfg.get("enabled", True):
        threshold = float(sil_cfg.get("silence_threshold_ms", 250)) / 1000.0
        extra = float(sil_cfg.get("max_extra_drift_sec", 1.5))
        gaps = detect_silence_gaps(timeline, threshold)
        if gaps:
            nudged_start = nudge_to_silence(new_start, gaps, extra)
            nudged_end = nudge_to_silence(new_end, gaps, extra)
            if nudged_start != new_start or nudged_end != new_end:
                source_parts.append("silence")
            new_start = nudged_start
            new_end = nudged_end

    # Safety: enforce duration bounds. When the snapped result violates them,
    # fall back to the tentative values.
    bounds = cfg.get("duration_bounds") or {}
    min_sec = float(bounds.get("min_sec", 15))
    max_sec = float(bounds.get("max_sec", 90))
    dur = new_end - new_start
    if dur < min_sec or dur > max_sec:
        print(
            f"[BOUNDARY] snapped duration {dur:.2f}s out of [{min_sec},{max_sec}]; "
            f"reverting to tentative ({tentative_end - tentative_start:.2f}s)",
            file=sys.stderr,
        )
        return result

    # Safety: preserve ordering.
    if new_end <= new_start:
        return result

    result["clip_start"] = round(new_start, 2)
    result["clip_end"] = round(new_end, 2)
    result["clip_duration"] = round(new_end - new_start, 2)
    result["drift_start_sec"] = round(new_start - tentative_start, 2)
    result["drift_end_sec"] = round(new_end - tentative_end, 2)
    result["snapped"] = result["drift_start_sec"] != 0.0 or result["drift_end_sec"] != 0.0
    result["source"] = "+".join(source_parts) if source_parts else "none"
    return result


# ---------------------------------------------------------------------------
# Batch helper for Pass C integration
# ---------------------------------------------------------------------------


def snap_moments_in_place(
    moments: List[dict], transcript_path: str, config: Optional[dict] = None
) -> int:
    """Apply boundary snap to every moment's (clip_start, clip_end) in
    place. Returns the count of moments whose boundaries actually moved.

    Decorates each snapped moment with:

        m["clip_start"], m["clip_end"], m["clip_duration"]  (overwritten)
        m["boundary_snapped"]  = True/False
        m["boundary_source"]   = "sentence+silence" | "sentence" | "none"
        m["boundary_drift_s"]  = (drift_start, drift_end)
    """
    cfg = config if config is not None else load_boundaries_config()
    if not cfg.get("enabled", True):
        for m in moments:
            m["boundary_snapped"] = False
            m["boundary_source"] = "disabled"
        return 0

    timeline = load_word_timeline(transcript_path)
    if not timeline:
        print(
            "[BOUNDARY] no word timeline available; snap is a no-op",
            file=sys.stderr,
        )
        for m in moments:
            m["boundary_snapped"] = False
            m["boundary_source"] = "no_timeline"
        return 0

    moved = 0
    for m in moments:
        if "clip_start" not in m or "clip_end" not in m:
            continue
        snapped = snap_boundaries(
            float(m["clip_start"]), float(m["clip_end"]),
            timeline=timeline, config=cfg,
        )
        m["clip_start"] = snapped["clip_start"]
        m["clip_end"] = snapped["clip_end"]
        m["clip_duration"] = snapped["clip_duration"]
        m["boundary_snapped"] = snapped["snapped"]
        m["boundary_source"] = snapped["source"]
        m["boundary_drift_s"] = (snapped["drift_start_sec"], snapped["drift_end_sec"])
        if snapped["snapped"]:
            moved += 1
    return moved


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="Clip boundary snap (Phase 4.2)")
    ap.add_argument("--transcript", required=True, help="path to transcript.json")
    ap.add_argument("--start", type=float, required=True, help="tentative clip start (s)")
    ap.add_argument("--end", type=float, required=True, help="tentative clip end (s)")
    ap.add_argument("--config", default=None)
    args = ap.parse_args()

    cfg = load_boundaries_config(args.config)
    timeline = load_word_timeline(args.transcript)
    if not timeline:
        print(f"(no usable transcript at {args.transcript})", file=sys.stderr)
        sys.exit(2)
    result = snap_boundaries(args.start, args.end, timeline=timeline, config=cfg)
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    _cli()
