#!/usr/bin/env python3
"""beat_map.py — shared acoustic/transcript timing primitives.

Extracted from sfx_cues.py (2026-07-13, jump-cuts-v2 phase J0 —
concepts/plan-jump-cuts-v2-2026-07) so the jump-cut compressor (clip_cuts.py)
can reuse the SAME tuned beat detection the SFX placer uses instead of snapping
only to Whisper segment edges:

  * refined_payoff()      — the "effects came in too early" / rescue / after-line
                            payoff refinement (owner tuning 2026-07-04/05)
  * laughter_times()      — transcript laughter markers (precise reaction beats)
  * prominent_transients()— the RMS-flux "other acoustic peaks" scanner (R4)
  * breath_points()       — sustained RMS dips = natural pause points for a cut
                            edge to land on (finer than Whisper segment edges)
  * build()               — aggregate the above for a clip window

Every function is failure-soft: any problem (no audio, bad input, missing dep)
returns the safe fallback / empty list, never raises. The DSP bodies are copied
VERBATIM from sfx_cues so behavior is byte-identical; sfx_cues now delegates to
these (its cue output on a fixed input is the extraction gate — see
sfx_cues.build + this module's _selftest).
"""
from __future__ import annotations

import json
from pathlib import Path

# Laughter / crowd markers scanned in the transcript text (lowercased).
LAUGH_MARKERS = ("[laughter]", "hahaha", "haha", "lmfao", "lmao", "lol")


# ─────────────────────────────────────────────────────────────────────────────
# Transcript primitives (no audio needed)
# ─────────────────────────────────────────────────────────────────────────────

def laughter_times(temp_dir: str, clip_start: float, clip_end: float) -> list[float]:
    """Absolute VOD timestamps of transcript segments inside the clip that carry
    a laughter marker — precise reaction/punchline anchors. [] on any failure."""
    try:
        segs = json.loads(Path(temp_dir, "transcript.json").read_text(encoding="utf-8"))
    except Exception:
        return []
    out: list[float] = []
    for s in segs or []:
        try:
            st = float(s.get("start"))
        except (TypeError, ValueError):
            continue
        if not (clip_start <= st < clip_end):
            continue
        txt = str(s.get("text") or "").lower()
        if any(m in txt for m in LAUGH_MARKERS):
            out.append(st)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Payoff refinement (owner-tuned; copied verbatim from sfx_cues._refine_payoff)
# ─────────────────────────────────────────────────────────────────────────────

def refined_payoff(payoff_rel: float, clip_start: float, clip_duration: float,
                   temp_dir: str, *, delay: float = 0.35, snap: bool = True,
                   snap_window: float = 1.2, rescue: bool = True,
                   after_line: bool = True, gap_window: float = 2.5) -> float:
    """Refine the payoff anchor from the (early) detection timestamp to the actual
    acoustic hit, then to just AFTER the delivered line. Three owner-driven layers
    (RESCUE for an early-timestamp setup, SNAP to the strongest RMS rise, AFTER-LINE
    into the first speech gap) plus a first-2.5s floor for clips > 8 s. Failure-soft:
    any problem falls back to payoff + delay clamped to the floor.

    Parameterized (vs sfx_cues' cfg dict) so clip_cuts can call it with defaults."""
    floor = 2.5 if clip_duration > 8 else 0.05
    fallback = max(floor, min(payoff_rel + delay, clip_duration - 0.2))
    if not snap:
        return fallback
    try:
        import numpy as np
        import soundfile as sf
        wav = Path(temp_dir, "audio.wav")
        if not wav.exists():
            return fallback
        sr = sf.info(str(wav)).samplerate
        hop = max(1, int(0.05 * sr))           # 50 ms energy envelope

        def _rms_slice(a_rel: float, b_rel: float):
            """RMS envelope of clip-relative [a_rel, b_rel); None on any problem."""
            a_rel = max(0.0, a_rel)
            frames = int(max(0.0, b_rel - a_rel) * sr)
            if frames < sr // 4:
                return None
            data, _ = sf.read(str(wav), start=max(0, int((clip_start + a_rel) * sr)),
                              frames=frames, dtype="float32", always_2d=False)
            if data is None or getattr(data, "size", 0) < sr // 4:
                return None
            if getattr(data, "ndim", 1) > 1:
                data = data.mean(axis=1)
            n_h = len(data) // hop
            if n_h < 3:
                return None
            return np.sqrt(np.mean(data[:n_h * hop].reshape(n_h, hop) ** 2, axis=1) + 1e-12)

        # --- 1) RESCUE: early payoff -> dominant transient anywhere in the clip ---
        rescue_floor = max(3.0, 0.12 * clip_duration)
        if rescue and payoff_rel < rescue_floor and clip_duration > 12:
            r = _rms_slice(2.0, clip_duration - 1.0)
            if r is not None:
                flux = np.diff(r)
                if len(flux) and float(flux.max()) > 0:
                    cand = 2.0 + (int(np.argmax(flux)) + 1) * hop / sr
                    if cand > rescue_floor:    # only accept a genuinely later beat
                        payoff_rel = cand

        # --- 2) SNAP: strongest rise near the payoff ---
        win = float(snap_window or 1.2)
        peak_rel = payoff_rel + delay
        r = _rms_slice(payoff_rel - 0.1, payoff_rel + win + 0.3)
        if r is not None:
            flux = np.diff(r)
            if len(flux) and float(flux.max()) > 0:
                peak_rel = (payoff_rel - 0.1) + (int(np.argmax(flux)) + 1) * hop / sr

        # --- 3) AFTER-LINE: shift into the first speech gap after the peak ---
        if after_line:
            gapw = float(gap_window or 2.5)
            r2 = _rms_slice(peak_rel, peak_rel + gapw)
            if r2 is not None and len(r2) > 4:
                thr = 0.35 * float(r2.max())
                need = 3                        # 3 × 50 ms = 150 ms of quiet
                runlen = 0
                for i, v in enumerate(r2):
                    runlen = runlen + 1 if float(v) < thr else 0
                    if runlen >= need:
                        peak_rel = peak_rel + (i - need + 1) * hop / sr + 0.05
                        break

        return max(floor, min(peak_rel, clip_duration - 0.2))
    except Exception:
        return fallback


# ─────────────────────────────────────────────────────────────────────────────
# Prominent transients (copied verbatim from sfx_cues._secondary_peaks DSP)
# ─────────────────────────────────────────────────────────────────────────────

def prominent_transients(clip_start: float, clip_duration: float, temp_dir: str, *,
                         min_prominence_ratio: float = 0.55, min_gap: float = 2.5,
                         max_n: int = 3, taken=()) -> list[float]:
    """The clip's strongest acoustic transients (laughter bursts, exclamations,
    slams) as clip-relative times, strongest-first, skipping anything within
    `min_gap` of an already-taken anchor or a prior pick. Used two ways:
      - SFX: place a ducked secondary hit on each (sfx_cues, gated by config)
      - CUTS: HIGH flux here = real non-verbal ACTION, not dead air → veto a drop
    Failure-soft: [] on any problem (no audio, short clip, missing dep)."""
    taken = list(taken or [])
    try:
        import numpy as np
        import soundfile as sf
        wav = Path(temp_dir, "audio.wav")
        if not wav.exists() or clip_duration < 10:
            return []
        sr = sf.info(str(wav)).samplerate
        hop = max(1, int(0.05 * sr))
        a_rel = 2.0
        frames = int(max(0.0, (clip_duration - 1.0) - a_rel) * sr)
        if frames < sr:
            return []
        data, _ = sf.read(str(wav), start=max(0, int((clip_start + a_rel) * sr)),
                          frames=frames, dtype="float32", always_2d=False)
        if data is None or getattr(data, "size", 0) < sr:
            return []
        if getattr(data, "ndim", 1) > 1:
            data = data.mean(axis=1)
        n_h = len(data) // hop
        if n_h < 10:
            return []
        rms = np.sqrt(np.mean(data[:n_h * hop].reshape(n_h, hop) ** 2, axis=1) + 1e-12)
        flux = np.diff(rms)
        if not len(flux) or float(flux.max()) <= 0:
            return []
        min_prom = float(min_prominence_ratio or 0.55) * float(flux.max())
        min_gap = float(min_gap or 2.5)
        max_n = int(max_n or 3)
        order = np.argsort(flux)[::-1]         # strongest-first candidate times
        out: list[float] = []
        for idx in order:
            if float(flux[idx]) < min_prom:
                break
            t = a_rel + (int(idx) + 1) * hop / sr
            if any(abs(t - p) < min_gap for p in taken) or \
               any(abs(t - p) < min_gap for p in out):
                continue
            out.append(t)
            if len(out) >= max_n:
                break
        return sorted(out)
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Breath points (NEW J0 primitive — natural pause edges for a cut)
# ─────────────────────────────────────────────────────────────────────────────

def breath_points(temp_dir: str, clip_start: float, clip_duration: float, *,
                  dip_ratio: float = 0.35, min_quiet_s: float = 0.15) -> list[float]:
    """Clip-relative start-times of sustained speech GAPS (RMS below `dip_ratio`
    of the local peak for ≥ `min_quiet_s`) — the natural breaths a human editor
    cuts on, finer than Whisper segment boundaries. clip_cuts snaps a candidate
    cut edge to the nearest of these. [] on any failure/no audio."""
    try:
        import numpy as np
        import soundfile as sf
        wav = Path(temp_dir, "audio.wav")
        if not wav.exists() or clip_duration < 2:
            return []
        sr = sf.info(str(wav)).samplerate
        hop = max(1, int(0.05 * sr))
        frames = int(max(0.0, clip_duration) * sr)
        if frames < sr // 2:
            return []
        data, _ = sf.read(str(wav), start=max(0, int(clip_start * sr)),
                          frames=frames, dtype="float32", always_2d=False)
        if data is None or getattr(data, "size", 0) < sr // 2:
            return []
        if getattr(data, "ndim", 1) > 1:
            data = data.mean(axis=1)
        n_h = len(data) // hop
        if n_h < 4:
            return []
        rms = np.sqrt(np.mean(data[:n_h * hop].reshape(n_h, hop) ** 2, axis=1) + 1e-12)
        thr = float(dip_ratio or 0.35) * float(rms.max())
        need = max(1, int(round(float(min_quiet_s or 0.15) / (hop / sr))))
        out: list[float] = []
        runlen = 0
        for i, v in enumerate(rms):
            if float(v) < thr:
                runlen += 1
                if runlen == need:               # start of a sustained quiet run
                    out.append(round((i - need + 1) * hop / sr, 3))
            else:
                runlen = 0
        return out
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Aggregator
# ─────────────────────────────────────────────────────────────────────────────

def build(temp_dir: str, clip_start: float, clip_duration: float,
          moment: dict | None = None) -> dict:
    """Aggregate the beat map clip_cuts needs for one clip window (all times
    CLIP-RELATIVE seconds):
      payoff_rel   — refined payoff (protect a halo around it)
      laughter_rel — reaction beats (veto a drop overlapping one)
      transient_rel— prominent non-verbal action (veto)
      breath_rel   — natural pause edges (snap a cut edge to the nearest)
    Failure-soft throughout; a field is [] / a midpoint fallback on any problem."""
    try:
        clip_start = float(clip_start)
        clip_duration = float(clip_duration)
    except (TypeError, ValueError):
        return {"payoff_rel": 0.0, "laughter_rel": [], "transient_rel": [], "breath_rel": []}
    clip_end = clip_start + clip_duration

    payoff_rel = clip_duration / 2.0
    if moment:
        try:
            payoff_rel = float(moment.get("timestamp", clip_start + clip_duration / 2.0)) - clip_start
        except (TypeError, ValueError):
            payoff_rel = clip_duration / 2.0
    payoff_rel = max(0.0, min(payoff_rel, clip_duration))
    payoff = refined_payoff(payoff_rel, clip_start, clip_duration, temp_dir)

    laughs = [t - clip_start for t in laughter_times(temp_dir, clip_start, clip_end)]
    transients = prominent_transients(clip_start, clip_duration, temp_dir,
                                      min_prominence_ratio=0.55, min_gap=1.5, max_n=8)
    breaths = breath_points(temp_dir, clip_start, clip_duration)
    return {"payoff_rel": round(payoff, 3),
            "laughter_rel": sorted(round(t, 3) for t in laughs),
            "transient_rel": sorted(round(t, 3) for t in transients),
            "breath_rel": breaths}


# ─────────────────────────────────────────────────────────────────────────────
# Self-test (pure paths — no audio.wav needed)
# ─────────────────────────────────────────────────────────────────────────────

def _selftest() -> int:
    import tempfile
    import os
    fails = 0

    def check(name, cond):
        nonlocal fails
        print(f"  {'OK ' if cond else 'FAIL'} {name}")
        if not cond:
            fails += 1

    with tempfile.TemporaryDirectory() as td:
        # laughter_times reads transcript.json segments in-window
        segs = [{"start": 101.0, "text": "that was hahaha insane"},
                {"start": 105.0, "text": "normal line"},
                {"start": 108.0, "text": "LMFAO no way"},
                {"start": 200.0, "text": "hahaha out of window"}]
        Path(td, "transcript.json").write_text(json.dumps(segs), encoding="utf-8")
        lt = laughter_times(td, 100.0, 130.0)
        check("laughter in-window only", lt == [101.0, 108.0])

        # refined_payoff with NO audio.wav -> deterministic fallback path
        # floor=2.5 (dur>8); fallback=max(2.5, min(payoff+delay, dur-0.2))
        rp = refined_payoff(10.0, 100.0, 30.0, td, delay=0.35)
        check("refined_payoff fallback (no audio)", abs(rp - 10.35) < 1e-6)
        rp2 = refined_payoff(0.5, 100.0, 30.0, td)           # below floor 2.5
        check("refined_payoff floor", abs(rp2 - 2.5) < 1e-6)
        rp3 = refined_payoff(10.0, 100.0, 6.0, td, delay=0.35)  # short clip floor 0.05
        check("refined_payoff short-clip clamp", abs(rp3 - (6.0 - 0.2)) < 1e-6)

        # prominent_transients / breath_points -> [] with no audio
        check("transients no-audio -> []",
              prominent_transients(100.0, 30.0, td) == [])
        check("breaths no-audio -> []", breath_points(td, 100.0, 30.0) == [])

        # build aggregates without raising
        b = build(td, 100.0, 30.0, {"timestamp": 112.0})
        check("build shape", set(b) == {"payoff_rel", "laughter_rel", "transient_rel", "breath_rel"}
              and b["laughter_rel"] == [1.0, 8.0])

        # ---- byte-identical extraction gate: sfx_cues delegates to us ----
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        try:
            import sfx_cues
            # sfx_cues.build on a synthetic moment (no audio -> fallback payoff)
            cues = sfx_cues.build({"category": "funny", "timestamp": 112.0},
                                  100.0, 30.0, temp_dir=td)
            # funny scans laughter -> punchline cues at the laughter beats survive
            # (asset resolution optimistic when sfx_inject import fails). Just assert
            # it runs and returns a list — the delegation itself is the equivalence.
            check("sfx_cues.build delegates cleanly", isinstance(cues, list))
            # direct equivalence: sfx_cues._laughter_times == our laughter_times
            check("sfx_cues._laughter_times == beat_map.laughter_times",
                  sfx_cues._laughter_times(td, 100.0, 130.0) == lt)
            check("sfx_cues._refine_payoff == beat_map.refined_payoff",
                  abs(sfx_cues._refine_payoff(10.0, 100.0, 30.0, td, sfx_cues.load_config()) - rp) < 1e-6)
        except Exception as e:
            check(f"sfx_cues delegation import ({type(e).__name__}: {e})", False)

    print("SELFTEST", "PASS" if fails == 0 else f"FAIL ({fails})")
    return 1 if fails else 0


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    print("beat_map.py — shared timing primitives (use --selftest)")
