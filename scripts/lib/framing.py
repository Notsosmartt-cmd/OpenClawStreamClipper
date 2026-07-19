#!/usr/bin/env python3
"""framing.py — W1 of concepts/plan-edit-quality-2026-07: full-bleed framing.

The legacy look scales a 16:9 source to 1080 wide and letterboxes it over a
blurred copy of itself, so real content occupies only ~35% of frame height and
the stream's own chat/overlay chrome rides along inside the frame. Every clip in
the reference corpus is instead FULL-BLEED native vertical with the subject at
80-100% of frame height (direct frame audit, 2026-07-18).

`fill` mode scales the source to FILL 1080x1920 and crops the sides. Which
horizontal window to keep is decided deterministically by
`visual_sense.action_center_x()` (column centroid of the frame-diff) — no VLM,
no new model. Cropping the sides also removes edge-mounted chat panels for free
(measured: chat visible in 83% of our clips vs 30% of the reference corpus).

Default is `blur` — byte-identical to today. `CLIP_FRAME_MODE=fill` opts in.

Guardrails (v1 is a STATIC crop by design):
  - unstable action (high centroid spread) -> center crop, never a moving one
  - cv2/probe failure                      -> center crop
  - a source already >= 9:16 (already vertical) -> unchanged, no crop
"""
from __future__ import annotations

import os
import subprocess

OUT_W, OUT_H = 1080, 1920

# Above this median-absolute-deviation of per-sample centroids the action is not
# in one place (subject crossing frame / multi-speaker) -> center crop instead.
SPREAD_MAX = 0.18
# Never push the crop window past this fraction of its legal travel, so a noisy
# centroid cannot slam the window to a hard edge.
MAX_OFFSET_FRAC = 0.85


def mode() -> str:
    """'fill' | 'blur' | 'auto'. `CLIP_FRAME_MODE` selects; default 'blur'
    (module-level legacy safety — the DASHBOARD defaults to 'auto', the
    owner-approved behavior after the 2026-07-19 fill-run eyeball)."""
    m = os.environ.get("CLIP_FRAME_MODE", "blur").strip().lower()
    if m in ("fill", "full", "fullbleed", "full_bleed"):
        return "fill"
    if m == "auto":
        return "auto"
    return "blur"


# Owner verdict (2026-07-19, after eyeballing the fill run): full-bleed FILL is
# best for IRL-style content, the blur-fill letterbox is best for gaming /
# desktop-screen content. Segment type is the reliable signal (it labels the
# STREAM content at that timestamp); category is a weak fallback (a gaming clip
# is often categorized 'hype'). `reaction` counts as desktop — the screen being
# watched IS the content, and cropping it loses the thing being reacted to.
_BLUR_SEGMENTS = {"gaming", "reaction"}
_BLUR_CATEGORIES = {"gaming", "skill", "skill_play", "gameplay", "clutch"}


def effective_mode(segment_type: str | None = None,
                   category: str | None = None) -> str:
    """Resolve the per-clip mode: 'fill' or 'blur'. In auto, gaming/desktop
    content keeps the letterbox; IRL/talky content goes full-bleed. Unknown
    signals fall back to 'blur' (the owner's baseline look)."""
    m = mode()
    if m != "auto":
        return m
    # Segment type is authoritative in BOTH directions (it labels the actual
    # stream content); category is only a fallback when the segment is unknown.
    seg = str(segment_type or "").strip().lower()
    if seg in _BLUR_SEGMENTS:
        return "blur"
    if seg in ("irl", "just_chatting"):
        return "fill"
    cat = str(category or "").strip().lower()
    if cat in _BLUR_CATEGORIES:
        return "blur"
    return "blur"


def probe_dims(path: str) -> tuple[int, int] | None:
    """(width, height) of the first video stream, or None."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(path)],
            capture_output=True, text=True, timeout=30)
        w, h = (r.stdout or "").strip().split("\n")[0].split("x")[:2]
        return int(w), int(h)
    except Exception:
        return None


def resolve_center_x(src: str, *, start_s: float = 0.0,
                     duration_s: float | None = None, log=None) -> tuple[float, str]:
    """(center_x in 0..1, why). Falls back to 0.5 (center) on any doubt."""
    def _say(msg: str) -> None:
        if log:
            try:
                log(msg)
            except Exception:
                pass
    try:
        import visual_sense
    except Exception as e:  # noqa: BLE001
        _say(f"  [framing] visual_sense unavailable ({type(e).__name__}) — center crop")
        return 0.5, "no-visual_sense"
    info = visual_sense.action_center_x(src, start_s=start_s, duration_s=duration_s)
    if not info:
        _say("  [framing] no usable motion samples — center crop")
        return 0.5, "no-samples"
    if float(info.get("spread", 1.0)) > SPREAD_MAX:
        _say(f"  [framing] action spread {info['spread']:.2f} > {SPREAD_MAX} "
             f"(moving/multi-subject) — center crop")
        return 0.5, f"unstable(spread={info['spread']})"
    cx = float(info.get("center_x", 0.5))
    # Clamp toward center so a noisy centroid can't hug an edge.
    cx = 0.5 + (cx - 0.5) * MAX_OFFSET_FRAC
    _say(f"  [framing] action center x={cx:.3f} "
         f"(spread {info['spread']:.3f}, {info['samples']} samples)")
    return max(0.0, min(1.0, cx)), f"centroid(n={info['samples']})"


def fill_filter(center_x: float = 0.5) -> str:
    """FFmpeg fragment: scale to FILL 1080x1920, crop the sides around center_x.

    Height fills exactly; the surviving horizontal window is centered on
    `center_x` and clamped inside the frame by the `min/max` expression, so no
    black edges are possible regardless of the value passed in.
    """
    cx = max(0.0, min(1.0, float(center_x)))
    return (f"scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,"
            f"crop={OUT_W}:{OUT_H}:"
            f"x='max(0\\,min(iw-{OUT_W}\\,(iw-{OUT_W})*{cx:.4f}))':y=0")


def should_fill(src: str, *, segment_type: str | None = None,
                category: str | None = None) -> tuple[bool, str]:
    """Fill mode only helps a source WIDER than 9:16; anything already vertical
    is passed through untouched. Resolves 'auto' per clip via effective_mode."""
    eff = effective_mode(segment_type, category)
    if eff != "fill":
        return False, (f"auto->blur(seg={segment_type},cat={category})"
                       if mode() == "auto" else "mode=blur")
    dims = probe_dims(src)
    if not dims:
        return False, "probe-failed"
    w, h = dims
    if h <= 0:
        return False, "bad-dims"
    if (w / h) <= (OUT_W / OUT_H) + 1e-6:
        return False, f"already-vertical({w}x{h})"
    return True, f"{w}x{h}"


def stack_filter(top_h_frac: float = 0.38) -> str:
    """W7 primitive — a 9:16 SPLIT-SCREEN stack from two inputs [0:v] (top) and
    [1:v] (bottom), the shape 12/115 reference clips use (calm/chaos before-after
    for storytime, a grid for news).

    Shipped as a tested PRIMITIVE only. The composition itself is deferred: every
    reference split-screen carries a SECOND SOURCE (the clip being reacted to),
    which a single-VOD render does not have. The existing news-compilation mode is
    the one place a second source already exists — that is where this plugs in
    first. Emitting it here keeps the geometry in one tested place.
    """
    f = max(0.15, min(0.85, float(top_h_frac)))
    th = int(OUT_H * f) // 2 * 2          # even heights (yuv420p requirement)
    bh = OUT_H - th
    return (f"[0:v]scale={OUT_W}:{th}:force_original_aspect_ratio=increase,"
            f"crop={OUT_W}:{th}[stop];"
            f"[1:v]scale={OUT_W}:{bh}:force_original_aspect_ratio=increase,"
            f"crop={OUT_W}:{bh}[sbot];"
            f"[stop][sbot]vstack=inputs=2")


if __name__ == "__main__":  # selftest
    f = fill_filter(0.5)
    assert "scale=1080:1920:force_original_aspect_ratio=increase" in f
    assert "crop=1080:1920" in f and "0.5000" in f
    assert "0.0000" in fill_filter(-3.0) and "1.0000" in fill_filter(9.0)  # clamped
    assert mode() in ("fill", "blur", "auto")
    os.environ["CLIP_FRAME_MODE"] = "fill"
    assert mode() == "fill" and effective_mode("gaming") == "fill"  # explicit wins
    os.environ["CLIP_FRAME_MODE"] = "auto"
    assert effective_mode("irl") == "fill"
    assert effective_mode("just_chatting") == "fill"
    assert effective_mode("gaming") == "blur"
    assert effective_mode("reaction") == "blur"            # desktop content
    assert effective_mode("irl", "gaming") == "fill"       # segment outranks category
    assert effective_mode(None, "skill") == "blur"
    assert effective_mode(None, None) == "blur"            # unknown -> owner baseline
    assert effective_mode("unknown") == "blur"
    os.environ["CLIP_FRAME_MODE"] = "blur"
    assert mode() == "blur" and effective_mode("irl") == "blur"
    print("framing selftest: ALL PASS")
