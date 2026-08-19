"""OCR + per-frame HUD classification for THE FINALS.

Two detectors:
  * Revive: a cheap bright-pixel gate on the center-lower band, then easyocr on
    the gated band. Lines are classified strong/weak/hold (see classify_revive_lines).
  * End screens: a red-dominance gate (the summary screens are a wall of red
    bokeh; the personal screen is dark red) plus a timed unconditional backstop,
    then top-strip OCR — and only when the top strip says "summary family" a
    second body-band OCR picks the subtype.

easyocr runs on the CUDA GPU when >=2 GB VRAM is free (LM Studio can squat the
5060 Ti), else CPU (~7x slower per call but only gated frames are OCR'd).
"""
from __future__ import annotations

import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import finals_common as fc  # noqa: E402

_reader = None
_reader_gpu = None

UPSCALE = 1.6  # OCR the crops slightly enlarged; small HUD text reads better


def pick_gpu(mode: str = "auto") -> bool:
    """mode: auto | gpu | cpu. Auto requires CUDA plus 2 GB free VRAM."""
    if mode == "cpu":
        return False
    try:
        import torch
        if not torch.cuda.is_available():
            return False
        if mode == "gpu":
            return True
        free, _total = torch.cuda.mem_get_info(0)
        return free >= 2 * 2**30
    except Exception:
        return False


def get_reader(mode: str = "auto"):
    global _reader, _reader_gpu
    want_gpu = pick_gpu(mode)
    if _reader is None or _reader_gpu != want_gpu:
        import easyocr
        _reader = easyocr.Reader(["en"], gpu=want_gpu, verbose=False)
        _reader_gpu = want_gpu
    return _reader


def reader_is_gpu() -> bool:
    return bool(_reader_gpu)


# --- crops & gates --------------------------------------------------------------

def crop(frame: np.ndarray, box: tuple[float, float, float, float]) -> np.ndarray:
    h, w = frame.shape[:2]
    x0, y0, x1, y1 = box
    return frame[int(y0 * h):int(y1 * h), int(x0 * w):int(x1 * w)]


def bright_gate(frame: np.ndarray) -> bool:
    """White HUD text present in the revive band? (the REVIVE word and the
    score popup are pure white; the cyan teammate name alone would not trip a
    gray threshold, but it never appears without the white REVIVE)."""
    band = crop(frame, fc.REVIVE_BAND)
    gray = cv2.cvtColor(band, cv2.COLOR_BGR2GRAY)
    frac = float(np.count_nonzero(gray >= fc.BRIGHT_GATE_THR)) / gray.size
    return frac >= fc.BRIGHT_GATE_FRAC


def red_gate(frame: np.ndarray) -> bool:
    """End-of-match screens are overwhelmingly red (bokeh / dark maroon)."""
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    red = ((h <= 12) | (h >= 168)) & (s >= 60) & (v >= 40)
    return float(np.count_nonzero(red)) / red.size >= fc.RED_GATE_FRAC


# --- OCR line assembly ------------------------------------------------------------

def ocr_lines(img: np.ndarray, mode: str = "auto") -> list[dict]:
    """OCR a crop and join boxes into visual lines (easyocr splits on color
    changes, e.g. white REVIVE + cyan name). Returns
    [{text, h_px, boxes: [(x0,y0,x1,y1), ...]}, ...] with h_px in *pre-upscale*
    pixels of the input crop."""
    if img.size == 0:
        return []
    up = cv2.resize(img, None, fx=UPSCALE, fy=UPSCALE, interpolation=cv2.INTER_CUBIC)
    results = get_reader(mode).readtext(up)
    items = []
    for box, text, _conf in results:
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        items.append({
            "text": text, "x": min(xs), "yc": (min(ys) + max(ys)) / 2,
            "h": (max(ys) - min(ys)) / UPSCALE,
            "bbox": (min(xs) / UPSCALE, min(ys) / UPSCALE,
                     max(xs) / UPSCALE, max(ys) / UPSCALE),
        })
    items.sort(key=lambda d: d["yc"])
    lines: list[list[dict]] = []
    for it in items:
        if lines and abs(it["yc"] - lines[-1][-1]["yc"]) <= 0.6 * max(it["h"], lines[-1][-1]["h"]) * UPSCALE:
            lines[-1].append(it)
        else:
            lines.append([it])
    out = []
    for ln in lines:
        ln.sort(key=lambda d: d["x"])
        out.append({
            "text": " ".join(d["text"] for d in ln),
            "h_px": max(d["h"] for d in ln),
            "boxes": [d["bbox"] for d in ln],
        })
    return out


# --- revive classification ---------------------------------------------------------

def classify_revive_lines(lines: list[dict], frame_h: int) -> list[tuple[str, str, str]]:
    """Classify OCR'd revive-band lines -> [(kind, name, text)].

    strong: "REVIVE <NAME>" completion banner, or the "REVIVE 200 + 200" score
            popup — the revive definitely happened.
    weak:   a bare big "REVIVE" (banner partially read); two weak hits in one
            group still count (see group_revive_hits).
    hold:   "HOLD TO REVIVE" interaction prompt — recorded, never clipped alone.
    Tiny lines (< MIN_LINE_H of frame) are world-space downed-teammate markers
    and are dropped outright.
    """
    hits = []
    for ln in lines:
        h_frac = ln["h_px"] / frame_h
        if h_frac < fc.MIN_LINE_H:
            continue
        toks = fc.norm_text(ln["text"]).split()
        for i, tok in enumerate(toks):
            if not fc.is_reviveish(tok):
                continue
            prev = toks[i - 1] if i > 0 else ""
            nxt = toks[i + 1] if i + 1 < len(toks) else ""
            if prev and (fc.fuzzy(prev, "HOLD") >= 0.75 or prev == "TO"):
                hits.append(("hold", "", ln["text"]))
            elif nxt and fc.is_score_token(nxt):
                hits.append(("strong", "", ln["text"]))
            elif (nxt and len(nxt) >= 3 and any(c.isalpha() for c in nxt)
                  and h_frac >= fc.BANNER_H):
                hits.append(("strong", nxt, ln["text"]))
            elif h_frac >= fc.BANNER_H:
                hits.append(("weak", "", ln["text"]))
            break  # one classification per line
    return hits


# --- end-screen classification -------------------------------------------------------

_PERSONAL_TOP = ("PERSONAL PERFORMANCE",)
_PERSONAL_WORDS = ("DEFEATED", "VICTORY", "ELIMINATED")
_SUMMARY_TOP = ("SUMMARY", "TOURNAMENT RESULT", "TEAM PERFORMANCE",
                "STANDOUT CONTESTANTS")
_TOURNAMENT_BODY = ("RANK SCORE", "FINISHING POSITION", "PERFORMANCE BONUS",
                    "TEAMMATE ABANDONS")
_TEAMPERF_BODY = ("COMBAT SCORE", "OBJECTIVE SCORE", "SUPPORT SCORE")
_STANDOUT_BODY = ("STANDOUT",)


def classify_end_screen(top_text: str, body_text_fn) -> str | None:
    """Classify an end-of-match screen from its top strip (and lazily its body).

    Order matters: the personal screen's tab row contains "MATCH SUMMARY", so
    the personal check must run before the summary-family check.
    `body_text_fn` is only called for summary screens (second OCR is not free).
    """
    top = fc.norm_text(top_text)
    if not top:
        return None
    if any(fc.fuzzy_contains(top, p) for p in _PERSONAL_TOP) or \
       any(any(fc.fuzzy(t, w) >= 0.85 for t in top.split()) for w in _PERSONAL_WORDS):
        return "end_personal"
    if any(fc.fuzzy_contains(top, p) for p in _SUMMARY_TOP):
        body = fc.norm_text(body_text_fn() or "")
        if any(fc.fuzzy_contains(body, p) for p in _TEAMPERF_BODY):
            return "end_teamperf"
        if any(fc.fuzzy_contains(body, p) for p in _TOURNAMENT_BODY):
            return "end_tournament"
        if any(fc.fuzzy_contains(body, p) for p in _STANDOUT_BODY):
            return "end_summary"
        return "end_summary"
    return None


# --- one-frame entry points (scanner + probe use these) --------------------------------

def scan_frame_revive(frame: np.ndarray, mode: str = "auto") -> list[tuple[str, str, str]]:
    if not bright_gate(frame):
        return []
    lines = ocr_lines(crop(frame, fc.REVIVE_BAND), mode)
    return classify_revive_lines(lines, frame.shape[0])


def scan_frame_end(frame: np.ndarray, mode: str = "auto", force: bool = False) -> str | None:
    if not force and not red_gate(frame):
        return None
    top = " ".join(ln["text"] for ln in ocr_lines(crop(frame, fc.TOP_STRIP), mode))

    def body():
        return " ".join(ln["text"] for ln in ocr_lines(crop(frame, fc.BODY_BAND), mode))

    return classify_end_screen(top, body)
