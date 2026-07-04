#!/usr/bin/env python3
"""Jump-cut compression + white-flash transitions for clips.

Two effects, both driven by the per-moment ``edit_plan`` (see edit_plan.py):

* **flashes** — brief white pops (``fade`` to white and back) layered on the
  framed video for engagement/pattern-interrupt. Pure overlay, no re-timing.
* **cuts** — DROP spans of dead air/rambling and concatenate the kept spans
  with ``xfade=fadewhite`` so the clip skips to the payoff. This re-times the
  clip, so the burned caption SRT is remapped to the compressed timeline.

Coordinate convention: ``cuts`` / ``flashes`` ``t`` are **absolute VOD
seconds** (matching the timestamped transcript the model is shown). The per-clip
SRT is **clip-relative** (0-based). ``remap_time`` bridges the two and is shared
by the SRT remap and the flash placement so they stay in lockstep.

Everything here is failure-soft: bad/empty input yields the no-op (a single
keep-span covering the whole window, an empty filter), so callers can apply it
unconditionally behind a flag.

Run ``python clip_cuts.py --selftest`` for the pure-function unit checks.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Callable

Span = tuple[float, float]

# Defaults (overridable per call / by the caller from env or per-category config)
MIN_KEEP = 1.5          # don't keep slivers shorter than this (s)
MAX_DROP_FRAC = 0.45    # never drop more than this fraction of the window
GUARANTEE_TAIL = 2.0    # never cut into the last N s (protect the payoff)
SNAP_WINDOW = 1.0       # snap a cut edge to a transcript boundary within ±N s
MIN_DROP = 0.6          # ignore drops shorter than this (not worth a cut)
FADE = 0.22             # white-fade duration at each join (s)


# ─────────────────────────────────────────────────────────────────────────────
# Boundaries
# ─────────────────────────────────────────────────────────────────────────────

def load_boundaries(transcript_path: str, clip_start: float, clip_end: float) -> list[float]:
    """Absolute segment-edge times within [clip_start, clip_end] from a Whisper
    transcript.json (segments have start/end). Cutting on these = cutting at
    natural pauses, never mid-word. Empty list on any failure."""
    out: list[float] = []
    try:
        with open(transcript_path, encoding="utf-8") as f:
            segs = json.load(f)
        for s in segs:
            for key in ("start", "end"):
                v = s.get(key)
                if v is None:
                    continue
                v = float(v)
                if clip_start - 0.5 <= v <= clip_end + 0.5:
                    out.append(round(v, 3))
    except Exception:
        return []
    return sorted(set(out))


def _snap(t: float, boundaries: list[float], window: float) -> float:
    """Snap t to the nearest boundary within ±window, else return t unchanged."""
    best, best_d = t, window
    for b in boundaries:
        d = abs(b - t)
        if d <= best_d:
            best, best_d = b, d
    return best


# ─────────────────────────────────────────────────────────────────────────────
# Keep-span computation
# ─────────────────────────────────────────────────────────────────────────────

def compute_keep_spans(cuts: list[dict], clip_start: float, clip_end: float,
                       boundaries: list[float] | None = None, *,
                       min_keep: float = MIN_KEEP, max_drop_frac: float = MAX_DROP_FRAC,
                       guarantee_tail: float = GUARANTEE_TAIL,
                       snap_window: float = SNAP_WINDOW, min_drop: float = MIN_DROP) -> list[Span]:
    """Turn DROP spans into ordered KEEP spans over [clip_start, clip_end].

    Snaps cut edges to natural pauses, protects the last ``guarantee_tail`` s
    (the payoff), caps total dropped time at ``max_drop_frac`` (dropping the
    LONGEST dead spans first), and skips keep-slivers under ``min_keep``.
    Returns ``[(clip_start, clip_end)]`` (a no-op) when nothing should be cut."""
    clip_start = float(clip_start)
    clip_end = float(clip_end)
    total = clip_end - clip_start
    noop = [(clip_start, clip_end)]
    if total <= 0 or not cuts:
        return noop
    boundaries = boundaries or []
    keep_end_floor = clip_end - max(0.0, guarantee_tail)

    drops: list[Span] = []
    for c in cuts:
        a = max(clip_start, float(c.get("drop_start", c.get("start", 0))))
        b = min(keep_end_floor, float(c.get("drop_end", c.get("end", 0))))
        if boundaries:
            a = _snap(a, boundaries, snap_window)
            b = _snap(b, boundaries, snap_window)
        a = max(clip_start, a)
        b = min(keep_end_floor, b)
        if b - a >= min_drop:
            drops.append((a, b))
    if not drops:
        return noop

    # merge overlaps
    drops.sort()
    merged: list[Span] = []
    for a, b in drops:
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))

    # cap total dropped time — prefer dropping the longest dead spans
    max_drop = total * max_drop_frac
    chosen: list[Span] = []
    dropped = 0.0
    for a, b in sorted(merged, key=lambda d: -(d[1] - d[0])):
        d = b - a
        if dropped + d > max_drop + 1e-6:
            continue
        chosen.append((a, b))
        dropped += d
    if not chosen:
        return noop
    chosen.sort()

    # window minus chosen drops, skipping sub-min keep slivers
    keeps: list[Span] = []
    cur = clip_start
    for a, b in chosen:
        if a - cur >= min_keep:
            keeps.append((round(cur, 3), round(a, 3)))
        cur = max(cur, b)
    if clip_end - cur >= min(min_keep, 0.8):
        keeps.append((round(cur, 3), round(clip_end, 3)))

    # need ≥2 spans (an actual cut) and meaningful compression, else no-op
    if len(keeps) < 2 or (total - sum(e - s for s, e in keeps)) < min_drop:
        return noop
    return keeps


# ─────────────────────────────────────────────────────────────────────────────
# Time remap (shared by SRT remap + flash placement)
# ─────────────────────────────────────────────────────────────────────────────

def remap_time(t_abs: float, keep_spans: list[Span], fade: float = 0.0) -> float | None:
    """Map an absolute VOD time onto the compressed (post-cut) clip timeline.
    Returns None if t falls inside a dropped gap. With one keep-span this is
    just ``t - span_start`` (the no-cut case)."""
    base = 0.0
    for i, (s, e) in enumerate(keep_spans):
        comp_start = base - i * fade  # each xfade join overlaps by `fade`
        if s - 1e-6 <= t_abs <= e + 1e-6:
            return max(0.0, comp_start + (t_abs - s))
        base += (e - s)
    return None


# ─────────────────────────────────────────────────────────────────────────────
# SRT remap
# ─────────────────────────────────────────────────────────────────────────────

def _parse_ts(s: str) -> float:
    s = s.strip().replace(",", ".")
    h, m, rest = s.split(":")
    return int(h) * 3600 + int(m) * 60 + float(rest)


def _fmt_ts(t: float) -> str:
    t = max(0.0, t)
    h = int(t // 3600); m = int((t % 3600) // 60); s = t % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")


def remap_srt(srt_text: str, keep_spans: list[Span], clip_start: float,
              fade: float = FADE) -> str:
    """Remap a clip-relative SRT onto the compressed timeline. Drops entries
    whose words fall entirely in dropped spans; shifts the rest earlier."""
    blocks = srt_text.replace("\r\n", "\n").strip().split("\n\n")
    out_blocks: list[str] = []
    idx = 1
    for blk in blocks:
        lines = blk.strip().splitlines()
        tl = next((l for l in lines if "-->" in l), None)
        if not tl:
            continue
        a_str, _, b_str = tl.partition("-->")
        try:
            r_a = _parse_ts(a_str); r_b = _parse_ts(b_str)
        except Exception:
            continue
        # clip-relative -> absolute -> compressed
        na = remap_time(clip_start + r_a, keep_spans, fade)
        nb = remap_time(clip_start + r_b, keep_spans, fade)
        if na is None and nb is None:
            continue  # word lives entirely in a dropped span
        if na is None:
            na = nb
        if nb is None or nb <= na:
            nb = na + 0.20
        text = "\n".join(lines[lines.index(tl) + 1:]).strip()
        if not text:
            continue
        out_blocks.append(f"{idx}\n{_fmt_ts(na)} --> {_fmt_ts(nb)}\n{text}")
        idx += 1
    return "\n\n".join(out_blocks) + ("\n" if out_blocks else "")


def remap_srt_file(srt_in: str, srt_out: str, keep_spans: list[Span],
                   clip_start: float, fade: float = FADE) -> bool:
    try:
        txt = Path(srt_in).read_text(encoding="utf-8", errors="replace")
        Path(srt_out).write_text(remap_srt(txt, keep_spans, clip_start, fade),
                                 encoding="utf-8")
        return True
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Flash filter
# ─────────────────────────────────────────────────────────────────────────────

def white_flash_boxes(t: float, style: str = "soft") -> list[str]:
    """A TRANSIENT full-frame white flash centered at `t`, built from
    `drawbox`+`enable` filters (rise → peak → fall within a ~0.16 s window).

    > [!warning] Do NOT use `fade=t=out/in:color=white` for this. `fade` HOLDS
    > the colour outside its ramp window (`out` stays white after; `in` shows
    > white before), so chaining them paints the WHOLE clip white — that was the
    > BUG 64 all-white regression. `drawbox` with `enable=between(t,a,b)` only
    > draws inside the window, so the flash is genuinely transient."""
    peak = 0.9 if style == "hard" else 0.78
    h = 0.05 if style == "hard" else 0.08           # half-width (s)
    s = max(0.0, t - h)
    box = ("drawbox=x=0:y=0:w=iw:h=ih:t=fill:color=white@{a:.2f}"
           ":enable='between(t,{a0:.3f},{a1:.3f})'")
    return [
        box.format(a=peak * 0.45, a0=s,             a1=t - h * 0.35),
        box.format(a=peak,        a0=t - h * 0.35,  a1=t + h * 0.35),
        box.format(a=peak * 0.45, a0=t + h * 0.35,  a1=t + h),
    ]


def white_flash_vf(flashes: list[dict], keep_spans: list[Span], clip_start: float,
                   fade: float = 0.0) -> str:
    """Comma-joined `drawbox` flash filters, one transient white pop per flash
    time. Flash `t` is absolute; remapped onto the (possibly compressed) clip
    timeline. Returns '' when there's nothing to draw."""
    parts: list[str] = []
    seen: list[float] = []
    for fl in flashes or []:
        t_abs = float(fl.get("t", -1))
        if t_abs < 0:
            continue
        rel = remap_time(t_abs, keep_spans, fade)
        if rel is None:
            continue  # flash landed in a dropped span
        if any(abs(rel - s) < 0.4 for s in seen):
            continue  # de-dupe near-coincident flashes
        seen.append(rel)
        parts.extend(white_flash_boxes(rel, str(fl.get("style", "soft"))))
    return ",".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# Precut render (extract kept spans, concat with white fade)
# ─────────────────────────────────────────────────────────────────────────────

def render_precut(src: str, keep_spans: list[Span], out: str, *,
                  fade: float = FADE, fps: int = 30,
                  log: Callable[[str], None] | None = None) -> bool:
    """Extract each keep-span from `src` and concat with `xfade=fadewhite`
    (video) + `acrossfade` (audio) into `out` at source resolution. Returns
    True on success. A single span just trims (no fade)."""
    spans = [(float(s), float(e)) for s, e in keep_spans if e - s > 0.05]
    if len(spans) < 2:
        return False  # nothing to cut — caller renders normally
    fc: list[str] = []
    for i, (s, e) in enumerate(spans):
        dur = e - s
        fc.append(f"[0:v]trim={s:.3f}:{e:.3f},setpts=PTS-STARTPTS,fps={fps}[v{i}]")
        fc.append(f"[0:a]atrim={s:.3f}:{e:.3f},asetpts=PTS-STARTPTS[a{i}]")
        spans[i] = (s, e, dur)  # type: ignore

    # chained xfade(fadewhite) for video; acrossfade for audio
    vcur, acur = "v0", "a0"
    offset = spans[0][2] - fade  # type: ignore
    for i in range(1, len(spans)):
        vnew, anew = f"vx{i}", f"ax{i}"
        fc.append(f"[{vcur}][v{i}]xfade=transition=fadewhite:duration={fade:.3f}"
                  f":offset={offset:.3f}[{vnew}]")
        fc.append(f"[{acur}][a{i}]acrossfade=d={fade:.3f}[{anew}]")
        vcur, acur = vnew, anew
        offset += spans[i][2] - fade  # type: ignore

    cmd = [
        "ffmpeg", "-nostdin", "-y", "-i", src,
        "-filter_complex", ";".join(fc),
        "-map", f"[{vcur}]", "-map", f"[{acur}]",
        # high-quality intermediate — the final render re-encodes once more
        "-c:v", "libx264", "-crf", "16", "-preset", "veryfast",
        "-pix_fmt", "yuv420p", "-r", str(fps),
        "-c:a", "aac", "-b:a", "192k",
        str(out),
    ]
    try:
        r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                           timeout=300)
        ok = r.returncode == 0 and Path(out).is_file() and Path(out).stat().st_size > 1000
        if not ok and log:
            tail = (r.stderr or b"").decode("utf-8", "replace").splitlines()[-4:]
            log(f"  [precut] failed: {' / '.join(tail)}")
        return ok
    except Exception as e:
        if log:
            log(f"  [precut] error: {e}")
        return False


def compressed_duration(keep_spans: list[Span], fade: float = FADE) -> float:
    """Wall-clock length of the concatenated precut (kept time minus fade overlaps)."""
    if not keep_spans:
        return 0.0
    return max(0.1, sum(e - s for s, e in keep_spans) - (len(keep_spans) - 1) * fade)


# ─────────────────────────────────────────────────────────────────────────────
# Rule-based generators (work without the LLM)
# ─────────────────────────────────────────────────────────────────────────────

# Per-category compression appetite (P2 tuning). Rambly categories compress
# more; punchy one-liners barely at all.
CATEGORY_MAX_DROP = {
    "storytime": 0.50, "informational": 0.50, "emotional": 0.40,
    "reactive": 0.35, "funny": 0.30, "hype": 0.25, "hot_take": 0.30,
}


def load_segments(transcript_path: str, clip_start: float, clip_end: float) -> list[Span]:
    """Absolute speech segments (start,end) overlapping the window, clamped."""
    out: list[Span] = []
    try:
        with open(transcript_path, encoding="utf-8") as f:
            segs = json.load(f)
        for s in segs:
            a, b = s.get("start"), s.get("end")
            if a is None or b is None:
                continue
            a, b = float(a), float(b)
            if b >= clip_start and a <= clip_end:
                out.append((max(a, clip_start), min(b, clip_end)))
    except Exception:
        return []
    return sorted(out)


def gaps_to_cuts(segments: list[Span], clip_start: float, clip_end: float,
                 min_gap: float = 1.2) -> list[dict]:
    """Drop-spans for SILENCES (gaps between speech). The safest cut — removing
    dead air never breaks the story; the tail guard protects the payoff."""
    cuts: list[dict] = []
    prev_end = clip_start
    for a, b in segments:
        if a - prev_end >= min_gap:
            cuts.append({"drop_start": round(prev_end + 0.15, 3), "drop_end": round(a - 0.10, 3)})
        prev_end = max(prev_end, b)
    if clip_end - prev_end >= min_gap:
        cuts.append({"drop_start": round(prev_end + 0.15, 3), "drop_end": round(clip_end, 3)})
    return cuts


def flash_cadence(clip_start: float, clip_end: float, seed: int, *,
                  every: float = 9.0, jitter: float = 3.0, dur: float = 0.12) -> list[dict]:
    """Deterministic engagement flashes at a seeded cadence (no LLM needed).
    Seeded by the moment timestamp so re-renders match + clips vary."""
    import random
    rng = random.Random((int(seed) * 2654435761) & 0xFFFFFFFF)
    out: list[dict] = []
    t = clip_start + every * (0.6 + 0.4 * rng.random())
    while t < clip_end - 1.5:
        out.append({"t": round(t, 3), "dur": dur, "style": "soft"})
        t += max(4.0, every + rng.uniform(-jitter, jitter))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Unified post-render pass (cuts + flashes on the FINISHED clip)
# ─────────────────────────────────────────────────────────────────────────────

def _build_filter(keep_rel: list[Span], flash_rel: list[float], fade: float, fps: int) -> str:
    """filter_complex → [vout][aout] from input 0 (a finished clip). Cuts via
    trim+xfade(fadewhite); flashes via fade-to-white. Works on the rendered clip
    so burned captions/effects stay in sync (they're pixels by now)."""
    fc: list[str] = []
    if len(keep_rel) >= 2:
        for i, (s, e) in enumerate(keep_rel):
            fc.append(f"[0:v]trim={s:.3f}:{e:.3f},setpts=PTS-STARTPTS,fps={fps}[v{i}]")
            fc.append(f"[0:a]atrim={s:.3f}:{e:.3f},asetpts=PTS-STARTPTS[a{i}]")
        vcur, acur = "v0", "a0"
        off = (keep_rel[0][1] - keep_rel[0][0]) - fade
        for i in range(1, len(keep_rel)):
            fc.append(f"[{vcur}][v{i}]xfade=transition=fadewhite:duration={fade:.3f}"
                      f":offset={off:.3f}[vx{i}]")
            fc.append(f"[{acur}][a{i}]acrossfade=d={fade:.3f}[ax{i}]")
            vcur, acur = f"vx{i}", f"ax{i}"
            off += (keep_rel[i][1] - keep_rel[i][0]) - fade
    else:
        fc.append("[0:v]null[v0]"); fc.append("[0:a]anull[a0]")
        vcur, acur = "v0", "a0"
    flparts: list[str] = []
    for ft in flash_rel:
        # TRANSIENT drawbox flash — NOT fade (fade holds white outside its window
        # and paints the whole clip white; that was BUG 64).
        flparts.extend(white_flash_boxes(ft, "soft"))
    fc.append(f"[{vcur}]" + (",".join(flparts) if flparts else "null") + "[vout]")
    fc.append(f"[{acur}]anull[aout]")
    return ";".join(fc)


def apply_transitions(clip_in: str, clip_out: str, keep_rel: list[Span],
                      flash_rel: list[float], *, fade: float = FADE, fps: int = 30,
                      log: Callable[[str], None] | None = None) -> bool:
    """Render clip_in → clip_out applying cuts (keep_rel ≥2 spans) and/or flashes.
    Uses the shared NVENC/libx264 selection. No-op (returns False) if nothing to do."""
    if len(keep_rel) < 2 and not flash_rel:
        return False
    fc = _build_filter(keep_rel, flash_rel, fade, fps)
    try:
        import venc  # shared encoder selection
        vargs = venc.video_args(crf=20, preset_libx264="veryfast")
    except Exception:
        vargs = ["-c:v", "libx264", "-crf", "20", "-preset", "veryfast"]
    cmd = ["ffmpeg", "-nostdin", "-y", "-i", clip_in, "-filter_complex", fc,
           "-map", "[vout]", "-map", "[aout]", *vargs,
           "-profile:v", "high", "-level", "4.2", "-pix_fmt", "yuv420p", "-r", str(fps),
           "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", clip_out]
    try:
        r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=300)
        ok = r.returncode == 0 and Path(clip_out).is_file() and Path(clip_out).stat().st_size > 1000
        if not ok and log:
            tail = (r.stderr or b"").decode("utf-8", "replace").splitlines()[-4:]
            log(f"  [transitions] ffmpeg failed: {' / '.join(tail)}")
        return ok
    except Exception as e:
        if log:
            log(f"  [transitions] error: {e}")
        return False


def process_clip_transitions(clip_path: str, *, cuts: list[dict], flashes: list[dict],
                             clip_start: float, duration: float, temp_dir: str,
                             jump_mode: str, flash_mode: str, seed: int,
                             category: str = "", fps: int = 30,
                             log: Callable[[str], None] | None = None) -> bool:
    """Orchestrator entry point. Combines rule-based + LLM cuts/flashes, applies
    them to a FINISHED clip IN PLACE (via temp), and returns True if it modified
    the clip. Fully failure-soft: on any problem the original clip is untouched.

    jump_mode:  off | gaps (silence only) | llm (model cuts only) | on (both)
    flash_mode: off | on (seeded cadence + any model flashes)"""
    jump_mode = (jump_mode or "off").lower()
    flash_mode = (flash_mode or "off").lower()
    if jump_mode == "off" and flash_mode == "off":
        return False
    clip_end = clip_start + duration
    seg = load_segments(f"{temp_dir}/transcript.json", clip_start, clip_end)
    boundaries = sorted({b for s in seg for b in s})

    all_cuts: list[dict] = []
    if jump_mode in ("gaps", "on"):
        all_cuts += gaps_to_cuts(seg, clip_start, clip_end)
    if jump_mode in ("llm", "on"):
        all_cuts += (cuts or [])
    max_drop = CATEGORY_MAX_DROP.get((category or "").lower(), MAX_DROP_FRAC)
    keep_abs = (compute_keep_spans(all_cuts, clip_start, clip_end, boundaries, max_drop_frac=max_drop)
                if (jump_mode != "off" and all_cuts) else [(clip_start, clip_end)])
    keep_rel = [(round(s - clip_start, 3), round(e - clip_start, 3)) for s, e in keep_abs]

    all_flashes: list[dict] = []
    if flash_mode == "on":
        all_flashes += (flashes or [])
        all_flashes += flash_cadence(clip_start, clip_end, seed)
    flash_rel: list[float] = []
    for fl in all_flashes:
        r = remap_time(float(fl.get("t", -1)), keep_abs, fade=FADE)
        if r is not None and 0.4 < r < (compressed_duration(keep_abs) - 0.4):
            flash_rel.append(round(r, 2))
    flash_rel = sorted(set(flash_rel))[:6]

    if len(keep_rel) < 2 and not flash_rel:
        return False
    if log:
        kept = compressed_duration(keep_abs)
        log(f"  [transitions] cuts={len(keep_rel) - 1 if len(keep_rel) > 1 else 0} "
            f"({duration:.1f}s→{kept:.1f}s) flashes={len(flash_rel)} cat={category}")
    out_tmp = str(Path(clip_path).with_suffix(".trans.mp4"))
    if apply_transitions(clip_path, out_tmp, keep_rel, flash_rel, fps=fps, log=log):
        try:
            os.replace(out_tmp, clip_path)
            try:  # effects manifest (logging only, never affects the render)
                import effects_log as _efl
                _efl.log_effect(Path(clip_path).stem, "transitions",
                                {"flashes": [{"t": t} for t in flash_rel],
                                 "jump_cuts": max(0, len(keep_rel) - 1),
                                 "category": category})
            except Exception:
                pass
            return True
        except Exception:
            return False
    try:
        if os.path.exists(out_tmp):
            os.remove(out_tmp)
    except Exception:
        pass
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Self-test
# ─────────────────────────────────────────────────────────────────────────────

def _selftest() -> int:
    fails = 0

    def check(name, cond):
        nonlocal fails
        print(f"  {'OK ' if cond else 'FAIL'} {name}")
        if not cond:
            fails += 1

    # no cuts -> no-op
    check("empty cuts -> whole window",
          compute_keep_spans([], 100.0, 130.0) == [(100.0, 130.0)])

    # one middle drop -> two keeps
    ks = compute_keep_spans([{"drop_start": 110, "drop_end": 118}], 100.0, 130.0)
    check("middle drop -> 2 spans", len(ks) == 2 and ks[0] == (100.0, 110.0) and ks[1] == (118.0, 130.0))

    # tail protected — a drop fully inside the last 3 s is rejected
    ks2 = compute_keep_spans([{"drop_start": 128, "drop_end": 129.5}], 100.0, 130.0, guarantee_tail=3.0)
    check("tail protected -> no-op", ks2 == [(100.0, 130.0)])

    # drop-fraction cap (try to drop 25 of 30 = 83% > 45%)
    ks3 = compute_keep_spans([{"drop_start": 101, "drop_end": 126}], 100.0, 130.0)
    check("over-cap drop rejected", ks3 == [(100.0, 130.0)])

    # remap_time: no-cut == relative
    check("remap no-cut", abs((remap_time(112.0, [(100.0, 130.0)], 0.0) or -1) - 12.0) < 1e-6)

    # remap_time across a cut (drop 110-118, fade 0): t=120 -> 10 (kept 100-110)+(120-118)=12
    spans = [(100.0, 110.0), (118.0, 130.0)]
    check("remap after cut", abs((remap_time(120.0, spans, 0.0) or -1) - 12.0) < 1e-6)
    check("remap inside drop -> None", remap_time(114.0, spans, 0.0) is None)

    # SRT remap: a word at rel 20s (abs 120) -> compressed 12s; a word at rel 12 (abs 112, dropped) gone
    srt = ("1\n00:00:20,000 --> 00:00:21,000\nkept\n\n"
           "2\n00:00:12,000 --> 00:00:13,000\ndropped\n")
    out = remap_srt(srt, spans, 100.0, fade=0.0)
    check("srt keeps mapped word", "kept" in out and "00:00:12,000" in out)
    check("srt drops in-gap word", "dropped" not in out)

    # flash remap + filter
    vf = white_flash_vf([{"t": 120.0, "dur": 0.12, "style": "soft"}], spans, 100.0, fade=0.0)
    check("flash builds TRANSIENT drawbox (not fade)",
          "drawbox=" in vf and "white@" in vf and "enable='between(t," in vf
          and "fade=" not in vf)
    vf2 = white_flash_vf([{"t": 114.0}], spans, 100.0, fade=0.0)
    check("flash in dropped gap skipped", vf2 == "")

    # gaps_to_cuts: 5s silence (105-110) between speech -> one drop
    gc = gaps_to_cuts([(100, 105), (110, 130)], 100.0, 130.0, min_gap=1.2)
    check("gap -> drop", len(gc) == 1 and 104.9 < gc[0]["drop_start"] < 105.4
          and 109.7 < gc[0]["drop_end"] < 110.2)

    # flash_cadence deterministic + in-window
    fa = flash_cadence(100.0, 140.0, 1234)
    fb = flash_cadence(100.0, 140.0, 1234)
    check("cadence deterministic + in-window",
          [f["t"] for f in fa] == [f["t"] for f in fb]
          and all(100.0 < f["t"] < 140.0 for f in fa) and len(fa) >= 2)

    print("SELFTEST", "PASS" if fails == 0 else f"FAIL ({fails})")
    return 1 if fails else 0


def _cli() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()
    print("nothing to do (use --selftest)")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_cli())
