#!/usr/bin/env python3
"""chat_mine.py — mine burned-in chat OVERLAYS from VOD video (plan Phase 2).

The owner's chat is composited into the stream video (an overlay), not exported
as data — so the existing structured-chat path (chat_fetch.py -> JSONL ->
chat_features.py) has nothing to read. This module recovers it with OCR and
emits the SAME JSONL shape chat_features consumes, so all the downstream burst /
phrase-hit / z-score machinery applies unchanged.

Design (concepts/reference-humor-2026-07 §A2 + concepts/master-research-2026-07 RQ3):
  * AUTO-DETECT the chat region (owner doesn't know where it is) — doubles as
    the "does this VOD even have chat?" test (no region -> emit nothing).
  * Two tiers: cheap ROI frame-diff VELOCITY over the whole VOD (burst detector),
    then burst/candidate-anchored OCR that keeps only NEW scrolled-in lines.
  * Viewer-reaction LAG seeded at 7 s forward (EMNLP-2017), auto-calibrated by
    cross-correlating a reaction (laughter) series with chat velocity.

cv2 + EasyOCR are lazy/failure-soft; the pure-logic helpers (velocity from
frames, new-line dedup, lag cross-correlation) are dependency-injected and
unit-test with no heavy deps.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any, Callable

DEFAULT_LAG_S = 7.0  # EMNLP-2017 optimal forward window; a seed, not a constant


# --- pure logic (unit-testable, no cv2/EasyOCR) -------------------------------
def frame_diff_velocity(frames: list, roi: tuple | None = None) -> list[float]:
    """Per-sample change rate = mean abs pixel diff between consecutive frames
    (optionally within an ROI bbox (x,y,w,h)). frames = list of 2D/3D ndarrays."""
    import numpy as np
    vel: list[float] = []
    prev = None
    for f in frames:
        g = f
        if roi is not None:
            x, y, w, h = roi
            g = f[y:y + h, x:x + w]
        g = np.asarray(g, dtype="float32")
        if g.ndim == 3:
            g = g.mean(axis=2)
        if prev is not None and prev.shape == g.shape:
            vel.append(float(np.mean(np.abs(g - prev))))
        else:
            vel.append(0.0)
        prev = g
    return vel


def dedup_new_lines(prev_lines: list[str], cur_lines: list[str],
                    sim: float = 0.8) -> list[str]:
    """Return lines in cur_lines NOT already seen in prev_lines (chat scrolls up;
    new messages enter at the bottom). Fuzzy so OCR jitter doesn't re-emit."""
    out: list[str] = []
    for c in cur_lines:
        cn = c.strip()
        if not cn:
            continue
        if any(SequenceMatcher(None, cn.lower(), p.strip().lower()).ratio() >= sim
               for p in prev_lines):
            continue
        out.append(cn)
    return out


def estimate_lag(reaction_times: list[float], velocity: list[tuple[float, float]],
                 *, max_lag_s: float = 12.0, seed_s: float = DEFAULT_LAG_S) -> float:
    """Per-channel viewer lag by cross-correlating a reaction series (e.g. CLAP
    laughter timestamps) with the chat-velocity series. Returns the forward shift
    (0..max_lag) that best aligns chat bursts AFTER reactions; falls back to the
    7 s seed when there's not enough signal."""
    if not reaction_times or len(velocity) < 4:
        return seed_s
    times = [t for t, _ in velocity]
    vals = [v for _, v in velocity]
    if not times:
        return seed_s
    step = (times[-1] - times[0]) / max(1, len(times) - 1) or 1.0
    best_lag, best_score = seed_s, -1.0
    lag = 0.0
    while lag <= max_lag_s:
        score = 0.0
        for rt in reaction_times:
            target = rt + lag
            idx = min(range(len(times)), key=lambda i: abs(times[i] - target))
            if abs(times[idx] - target) <= step:
                score += vals[idx]
        if score > best_score:
            best_score, best_lag = score, lag
        lag += step
    return round(best_lag, 2) if best_score > 0 else seed_s


# --- cv2 / EasyOCR path (failure-soft) ----------------------------------------
def _sample_frames(video: str, n: int):
    try:
        import cv2
    except Exception:
        return []
    cap = cv2.VideoCapture(video)
    if not cap.isOpened():
        return []
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    out = []
    try:
        for i in range(n):
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(total * (i + 0.5) / n) if total else 0)
            ok, fr = cap.read()
            if ok and fr is not None:
                out.append(fr)
    finally:
        cap.release()
    return out


def detect_chat_roi(video: str, *, n: int = 16, min_frac: float = 0.4):
    """Auto-detect the chat overlay region: OCR-detect text boxes across sampled
    frames, keep small boxes that recur in a STABLE region (chat = small, dense,
    fixed-position, always-changing) -> bbox (x,y,w,h). Returns None when no
    persistent small-text cluster exists (= this VOD has no burned-in chat)."""
    try:
        import easyocr  # noqa: F401
        import numpy as np
    except Exception:
        return None
    frames = _sample_frames(video, n)
    if len(frames) < 4:
        return None
    try:
        import easyocr
        reader = easyocr.Reader(["en"], gpu=False, verbose=False)
        H, W = frames[0].shape[:2]
        heat = np.zeros((H, W), dtype="float32")
        for fr in frames:
            for (bbox, txt, conf) in reader.readtext(fr, detail=1, paragraph=False):
                if conf < 0.3 or not str(txt).strip():
                    continue
                xs = [int(p[0]) for p in bbox]; ys = [int(p[1]) for p in bbox]
                x0, x1, y0, y1 = max(0, min(xs)), min(W, max(xs)), max(0, min(ys)), min(H, max(ys))
                bw, bh = x1 - x0, y1 - y0
                if bh <= 0 or bw <= 0:
                    continue
                if bh < H * 0.06 and bw < W * 0.5:   # small line = chat-like
                    heat[y0:y1, x0:x1] += 1.0
        if heat.max() < len(frames) * min_frac:
            return None  # no region persistently carries small text -> no chat
        ys, xs = np.where(heat >= heat.max() * 0.5)
        if len(xs) == 0:
            return None
        x, y, w, h = int(xs.min()), int(ys.min()), int(xs.max() - xs.min()), int(ys.max() - ys.min())
        return (x, y, max(1, w), max(1, h))
    except Exception:
        return None


if __name__ == "__main__":  # smoke of the pure logic
    v = [(0.0, 1.0), (1.0, 2.0), (2.0, 9.0), (3.0, 8.0), (4.0, 1.0)]
    print("lag:", estimate_lag([0.0], v, max_lag_s=4))
    print("new:", dedup_new_lines(["hello world", "george bush lol"], ["george bush lol", "aint no way"]))
