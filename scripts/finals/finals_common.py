"""Shared helpers for the Finals clipper (game-specific HUD detector).

THE FINALS revives and end-of-match screens are *visual* HUD events — the main
pipeline's transcript-driven moment detection can't see them, which is why this
engine exists as a separate vertical (see wiki entities/finals-clipper).

Everything here is resolution-independent: geometry is expressed as fractions
of the scanned frame, so 1080p/1440p VODs behave identically.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

# --- Crop geometry (x0, y0, x1, y1 as fractions of the frame) -----------------
# Calibrated from the owner's 2026-08-19 screenshots:
#   - completion banner "REVIVE <NAME>" sits centered at y ~0.67-0.71, with the
#     "REVIVE 200 + 200" score popup just below (~0.73-0.77)
#   - "HOLD TO REVIVE" prompt at y ~0.60 (in-band; classified, never clipped)
#   - world-space "REVIVE" markers over downed teammates float ~0.25-0.50 and
#     are tiny — excluded by the band top edge AND the line-height gate
REVIVE_BAND = (0.20, 0.55, 0.80, 0.83)
# End screens: "SUMMARY"/"DEFEATED" header + tab row live in the top strip;
# subtype keywords (RANK SCORE UPDATE vs COMBAT/OBJECTIVE/SUPPORT SCORE) in the
# body band.
TOP_STRIP = (0.00, 0.00, 0.92, 0.14)
BODY_BAND = (0.05, 0.38, 0.95, 0.82)

# --- Line-height gates (fractions of the scanned frame height) ----------------
MIN_LINE_H = 0.016    # anything smaller is world-marker/kill-feed junk
BANNER_H = 0.024      # a bare "REVIVE" only counts big (the completion banner)

# --- Pixel gates ---------------------------------------------------------------
BRIGHT_GATE_THR = 210     # grayscale floor for "white HUD text" pixels
BRIGHT_GATE_FRAC = 0.003  # fraction of revive-band pixels that must pass
RED_GATE_FRAC = 0.10      # fraction of red pixels marking an end screen

# --- Grouping / clipping defaults ----------------------------------------------
REVIVE_GAP_S = 3.0        # OCR hits closer than this are one revive event
END_GAP_S = 3.5           # end-screen samples closer than this are one span
END_OCR_EVERY_S = 12.0    # unconditional top-strip OCR cadence (red-gate backstop)
DEFAULT_PRE_S = 10.0      # owner ask: ~10 s of lead-in before the revive
DEFAULT_POST_S = 5.0      # ~5 s after
DEFAULT_END_CAP_S = 6.0   # "a quick clip of each time it is shown"
END_MIN_S = 2.5

END_TYPES = ("end_tournament", "end_teamperf", "end_personal", "end_summary")

_NORM_RE = re.compile(r"[^A-Z0-9+_$ ]+")
_SCORE_TOKEN_RE = re.compile(r"^[+]?\d{2,4}$")


def norm_text(s: str) -> str:
    """Uppercase, strip OCR punctuation noise, collapse whitespace."""
    return re.sub(r"\s+", " ", _NORM_RE.sub(" ", s.upper())).strip()


def fuzzy(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def fuzzy_contains(text: str, phrase: str, thr: float = 0.84) -> bool:
    """True if `phrase` appears in `text` (both normalized), tolerating OCR
    slips — exact substring first, then a sliding token window."""
    text_n, phrase_n = norm_text(text), norm_text(phrase)
    if not text_n or not phrase_n:
        return False
    if phrase_n in text_n:
        return True
    toks, n = text_n.split(), len(phrase_n.split())
    for i in range(0, max(1, len(toks) - n + 1)):
        if fuzzy(" ".join(toks[i:i + n]), phrase_n) >= thr:
            return True
    return False


def is_reviveish(tok: str) -> bool:
    return fuzzy(tok, "REVIVE") >= 0.83


def is_score_token(tok: str) -> bool:
    """The '+ 200' popup digits. Deliberately strict (>=2 digits, optional +)
    so world-marker distances like '5M'/'12M' can never fake a completion."""
    return bool(_SCORE_TOKEN_RE.match(tok)) or fuzzy(tok, "200") >= 0.75


def fmt_ts(t: float) -> str:
    t = max(0, int(round(t)))
    return f"{t // 3600:02d}-{t % 3600 // 60:02d}-{t % 60:02d}"


def fmt_clock(t: float) -> str:
    t = max(0, int(round(t)))
    return f"{t // 3600:02d}:{t % 3600 // 60:02d}:{t % 60:02d}"


@dataclass
class ReviveHit:
    t: float
    kind: str          # "strong" | "weak" | "hold"
    name: str = ""
    text: str = ""


@dataclass
class EndHit:
    t: float
    type: str          # one of END_TYPES


@dataclass
class ReviveEvent:
    t: float           # anchor = first hit (clip lead-in counts back from here)
    t_last: float      # last hit (clip tail counts forward from here)
    names: list = field(default_factory=list)
    strong: int = 0
    weak: int = 0
    texts: list = field(default_factory=list)


@dataclass
class EndSpan:
    type: str
    t_start: float
    t_end: float
    samples: int = 0


def group_revive_hits(hits: list[ReviveHit], gap_s: float = REVIVE_GAP_S) -> list[ReviveEvent]:
    """Cluster raw per-frame hits into revive events.

    Emission rule: >=1 strong hit (banner-with-name or score popup) OR >=2 weak
    hits (bare big banner seen twice). 'hold' prompts never emit on their own —
    the pre-roll buffer covers the approach; they only corroborate.
    """
    events: list[ReviveEvent] = []
    cur: list[ReviveHit] = []

    def flush():
        if not cur:
            return
        strong = sum(1 for h in cur if h.kind == "strong")
        weak = sum(1 for h in cur if h.kind == "weak")
        if strong >= 1 or weak >= 2:
            names = sorted({h.name for h in cur if h.name})
            texts = sorted({h.text for h in cur if h.text})[:6]
            events.append(ReviveEvent(t=cur[0].t, t_last=cur[-1].t, names=names,
                                      strong=strong, weak=weak, texts=texts))

    for h in sorted(hits, key=lambda x: x.t):
        if cur and h.t - cur[-1].t > gap_s:
            flush()
            cur = []
        cur.append(h)
    flush()
    return events


def group_end_hits(hits: list[EndHit], gap_s: float = END_GAP_S) -> list[EndSpan]:
    """Cluster classified end-screen samples into contiguous per-type spans.
    A type change (e.g. tabbing Tournament Result -> Team Performance) always
    starts a new span, so each screen appearance clips separately."""
    spans: list[EndSpan] = []
    for h in sorted(hits, key=lambda x: x.t):
        last = spans[-1] if spans else None
        if last and last.type == h.type and h.t - last.t_end <= gap_s:
            last.t_end = h.t
            last.samples += 1
        else:
            spans.append(EndSpan(type=h.type, t_start=h.t, t_end=h.t, samples=1))
    return spans


def merge_windows(windows: list[tuple[float, float, list]]) -> list[tuple[float, float, list]]:
    """Merge overlapping (start, end, payload-list) clip windows so back-to-back
    revives become one clip instead of two duplicated ones."""
    out: list[tuple[float, float, list]] = []
    for s, e, refs in sorted(windows):
        if out and s <= out[-1][1]:
            ps, pe, prefs = out[-1]
            out[-1] = (ps, max(pe, e), prefs + refs)
        else:
            out.append((s, e, list(refs)))
    return out
