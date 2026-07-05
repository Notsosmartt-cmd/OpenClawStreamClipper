#!/usr/bin/env python3
"""Deterministic acoustic-anchor SFX cue builder.

Implements wiki concepts/sfx-cue-taxonomy-2026-06: instead of placing sound
effects on the vision model's guess / zoom-punch timing, anchor them on the
moment's actual beats — the payoff (the moment timestamp is payoff-centered),
a build-up riser before it, and transcript laughter markers inside the clip.

Maps the moment's `category` -> beat-types (config/sfx_cues.json `category_beats`)
-> an ordered sound-option list (`beat_defaults`). For each beat it emits the
FIRST option whose `assets/sfx/<kind>/` folder has audio (so it works today with
the seeded whoosh/impact/ding/riser/boom libraries and upgrades to the ideal
kind once scratch/sad_trombone/applause/etc. are seeded).

Every cue carries `gain_db` (relative to source audio) which sfx_inject converts
to a per-cue volume — so the Vine boom rides hot on a punchline while most SFX
duck under speech (the research's per-kind mix policy).

Failure-soft by construction: a missing config, transcript, or asset just yields
fewer (or zero) cues; callers keep their existing behavior. Gated upstream by
CLIP_SFX_ANCHOR (profile_render).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# Laughter / crowd markers scanned in the transcript text (lowercased).
_LAUGH_MARKERS = ("[laughter]", "hahaha", "haha", "lmfao", "lmao", "lol")

# In-code fallback used when config/sfx_cues.json is missing/unreadable. Mirrors
# the shipped config so the feature degrades to sane defaults, never to a crash.
_DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": True,
    "max_cues": 4,
    # Below the build-up lead (1.0s) so a riser + its payoff hit coexist as a
    # one-two punch; large enough to merge coincident cues.
    "dedup_window_s": 0.6,
    "buildup_lead_s": 1.0,
    # Owner feedback 2026-07-04 ("effects came in a little too early"): the moment
    # timestamp is the DETECTION time, which typically lands a beat before the
    # punchline is actually delivered. payoff_delay_s shifts the payoff anchor
    # later; snap_to_onset then refines it to the strongest acoustic transient in
    # [payoff, payoff + onset_snap_window_s] (the real hit), falling back to the
    # fixed delay when audio.wav is unavailable. The riser keeps its 1.0s lead
    # relative to the REFINED payoff, so the whole one-two punch shifts together.
    "payoff_delay_s": 0.35,
    "snap_to_onset": True,
    "onset_snap_window_s": 1.2,
    "beat_defaults": {
        "punchline": [{"kind": "boom", "offset_s": 0.10, "gain_db": 0.0},
                      {"kind": "impact", "offset_s": 0.08, "gain_db": -3.0}],
        "punchline_light": [{"kind": "pop", "offset_s": 0.08, "gain_db": -8.0},
                            {"kind": "impact", "offset_s": 0.08, "gain_db": -6.0},
                            {"kind": "ding", "offset_s": 0.08, "gain_db": -8.0}],
        "fail": [{"kind": "scratch", "offset_s": 0.05, "gain_db": -6.0},
                 {"kind": "sad_trombone", "offset_s": 0.80, "gain_db": -8.0},
                 {"kind": "sad_violin", "offset_s": 0.80, "gain_db": -8.0},
                 {"kind": "whoosh", "offset_s": 0.05, "gain_db": -7.0}],
        "reveal": [{"kind": "applause", "offset_s": 0.15, "gain_db": -12.0},
                   {"kind": "ding", "offset_s": 0.10, "gain_db": -8.0}],
        "transition": [{"kind": "whoosh", "offset_s": 0.05, "gain_db": -6.0}],
        # offset_s 0.0: the build-up anchor is already placed buildup_lead_s
        # before the payoff in build(); a non-zero offset here would double the
        # lead and put the riser ~2s early (outside the 0.4-1.25s research window).
        "buildup": [{"kind": "riser", "offset_s": 0.0, "gain_db": -10.0}],
        "awkward_silence": [{"kind": "crickets", "offset_s": 2.50, "gain_db": -12.0}],
        "disbelief": [{"kind": "bruh", "offset_s": 0.10, "gain_db": -6.0},
                      {"kind": "scratch", "offset_s": 0.10, "gain_db": -6.0}],
    },
    "category_beats": {
        "funny": {"payoff": "punchline", "scan_laughter": True, "buildup": False},
        "reactive": {"payoff": "punchline", "scan_laughter": True, "buildup": False},
        "hype": {"payoff": "punchline", "scan_laughter": False, "buildup": True},
        "dancing": {"payoff": "punchline", "scan_laughter": False, "buildup": True},
        "controversial": {"payoff": "punchline", "scan_laughter": False, "buildup": False},
        "hot_take": {"payoff": "punchline", "scan_laughter": False, "buildup": True},
        "storytime": {"payoff": "reveal", "scan_laughter": False, "buildup": True},
        "emotional": {"payoff": None, "scan_laughter": False, "buildup": False},
    },
}

_CONFIG_CACHE: dict | None = None


def load_config(path: str | None = None) -> dict:
    """Load config/sfx_cues.json with the three-tier path fallback used across
    the pipeline (env var -> Linux default -> repo config), then back-fill any
    missing top-level keys from the in-code defaults. Cached when path is None."""
    global _CONFIG_CACHE
    if path is None and _CONFIG_CACHE is not None:
        return _CONFIG_CACHE
    candidates = [
        path,
        os.environ.get("CLIP_SFX_CUES_CONFIG"),
        "/root/.openclaw/sfx_cues.json",
        str(Path(__file__).resolve().parents[2] / "config" / "sfx_cues.json"),
    ]
    cfg: dict = {}
    for c in candidates:
        if c and os.path.exists(c):
            try:
                cfg = json.loads(Path(c).read_text(encoding="utf-8")) or {}
                break
            except (OSError, json.JSONDecodeError):
                cfg = {}
    merged = dict(_DEFAULT_CONFIG)
    for k, v in (cfg or {}).items():
        merged[k] = v
    if path is None:
        _CONFIG_CACHE = merged
    return merged


def _has_assets(kind: str) -> bool:
    """True when assets/sfx/<kind>/ has at least one usable audio file. Uses
    sfx_inject when importable (shares its alias/manifest resolution); otherwise
    optimistically returns True so cues still emit (sfx_inject drops empties)."""
    try:
        import sfx_inject as _sx  # type: ignore
        return _sx.has_assets(kind)
    except Exception:
        return True


def _pick_kind(beat_type: str, cfg: dict) -> dict | None:
    """First sound option for this beat whose kind has assets. Returns the
    matched option dict ({kind, offset_s, gain_db}) or None."""
    for opt in cfg.get("beat_defaults", {}).get(beat_type, []) or []:
        kind = str(opt.get("kind") or "").strip().lower()
        if kind and _has_assets(kind):
            return opt
    return None


def _laughter_times(temp_dir: str, clip_start: float, clip_end: float) -> list[float]:
    """Absolute VOD timestamps of transcript segments inside the clip that carry
    a laughter marker — precise punchline anchors. [] on any failure."""
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
        if any(m in txt for m in _LAUGH_MARKERS):
            out.append(st)
    return out


def _refine_payoff(payoff_rel: float, clip_start: float, clip_duration: float,
                   temp_dir: str, cfg: dict) -> float:
    """Refine the payoff anchor from the (early) detection timestamp to the actual
    acoustic hit, then to just AFTER the delivered line. Three owner-driven layers:

    1. RESCUE (owner 2026-07-05, 'Hot Cheeto'): when the detected payoff sits at the
       very START of the clip, the timestamp is the setup, not the payoff — the real
       beat lands much later (camera pan / reaction at ~18s while the boom fired at
       ~1s). If payoff_rel < max(3s, 12% of duration), search the WHOLE clip for the
       dominant RMS transient instead of trusting the timestamp.
    2. SNAP (owner 2026-07-04): strongest RMS rise within onset_snap_window_s of the
       (possibly rescued) payoff — the hit itself.
    3. AFTER-LINE (owner 2026-07-05, 'Shower Bluff'): a 0 dB boom ON the punchline
       masks the words. Shift the cue into the first speech GAP after the peak
       (first sustained RMS dip < 35% of peak within boom_gap_window_s) — the meme
       edit convention: line → breath → boom.

    Plus a floor: never place the payoff cue in the first 2.5 s of a clip > 8 s
    (nothing to punctuate yet; the cold-open teaser owns that space).
    Failure-soft at every layer: any problem falls back to the previous layer's
    value, ultimately payoff + payoff_delay_s clamped to the floor."""
    delay = float(cfg.get("payoff_delay_s", 0.35) or 0.0)
    floor = 2.5 if clip_duration > 8 else 0.05
    fallback = max(floor, min(payoff_rel + delay, clip_duration - 0.2))
    if not cfg.get("snap_to_onset", True):
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
        if (cfg.get("payoff_rescue", True) and payoff_rel < rescue_floor
                and clip_duration > 12):
            r = _rms_slice(2.0, clip_duration - 1.0)
            if r is not None:
                flux = np.diff(r)
                if len(flux) and float(flux.max()) > 0:
                    cand = 2.0 + (int(np.argmax(flux)) + 1) * hop / sr
                    if cand > rescue_floor:    # only accept a genuinely later beat
                        payoff_rel = cand

        # --- 2) SNAP: strongest rise near the payoff ---
        win = float(cfg.get("onset_snap_window_s", 1.2) or 1.2)
        peak_rel = payoff_rel + delay
        r = _rms_slice(payoff_rel - 0.1, payoff_rel + win + 0.3)
        if r is not None:
            flux = np.diff(r)
            if len(flux) and float(flux.max()) > 0:
                peak_rel = (payoff_rel - 0.1) + (int(np.argmax(flux)) + 1) * hop / sr

        # --- 3) AFTER-LINE: shift into the first speech gap after the peak ---
        if cfg.get("boom_after_line", True):
            gapw = float(cfg.get("boom_gap_window_s", 2.5) or 2.5)
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


def build(moment: dict, clip_start: float, clip_duration: float, *,
          temp_dir: str, seed: object = 0, config: dict | None = None) -> list[dict]:
    """Return a list of {t, kind, gain_db} cues (t = clip-relative seconds).

    Empty list when the category opts out (emotional), the config is disabled,
    or nothing anchors — callers then keep their prior cues.
    """
    cfg = config if config is not None else load_config()
    if not cfg.get("enabled", True):
        return []

    try:
        clip_start = float(clip_start)
        clip_duration = float(clip_duration)
    except (TypeError, ValueError):
        return []
    if clip_duration <= 1.0:
        return []
    clip_end = clip_start + clip_duration

    category = str(moment.get("category") or moment.get("primary_category") or "").strip().lower()
    cat_cfg = (cfg.get("category_beats", {}) or {}).get(category)
    if cat_cfg is None:
        # Unknown category -> treat the payoff as a generic punchline.
        cat_cfg = {"payoff": "punchline", "scan_laughter": False, "buildup": False}

    try:
        payoff_abs = float(moment.get("timestamp", clip_start + clip_duration / 2.0))
    except (TypeError, ValueError):
        payoff_abs = clip_start + clip_duration / 2.0
    payoff_rel = payoff_abs - clip_start
    payoff_rel = max(0.0, min(payoff_rel, clip_duration))
    # Owner feedback 2026-07-04: detection time lands early → shift to the real hit.
    payoff_rel = _refine_payoff(payoff_rel, clip_start, clip_duration, temp_dir, cfg)

    # (beat_rel, beat_type) anchors before kind/offset resolution.
    anchors: list[tuple[float, str]] = []

    payoff_beat = cat_cfg.get("payoff")
    if payoff_beat:
        anchors.append((payoff_rel, str(payoff_beat)))
        if cat_cfg.get("buildup"):
            lead = float(cfg.get("buildup_lead_s", 1.0) or 1.0)
            b_rel = payoff_rel - lead
            if b_rel > 0.3:
                anchors.append((b_rel, "buildup"))

    if cat_cfg.get("scan_laughter"):
        for t_abs in _laughter_times(temp_dir, clip_start, clip_end):
            rel = t_abs - clip_start
            # Skip a laughter marker that coincides with the payoff beat.
            if abs(rel - payoff_rel) > 1.0:
                anchors.append((rel, "punchline"))

    if not anchors:
        return []

    # Resolve each anchor to a concrete cue, applying the beat's offset + the
    # first available kind. Sort by time so dedup keeps the earliest.
    dedup = float(cfg.get("dedup_window_s", 1.2) or 1.2)
    max_cues = int(cfg.get("max_cues", 4) or 4)
    anchors.sort(key=lambda a: a[0])

    cues: list[dict] = []
    placed: list[float] = []
    for beat_rel, beat_type in anchors:
        opt = _pick_kind(beat_type, cfg)
        if opt is None:
            continue
        t = beat_rel + float(opt.get("offset_s", 0.0) or 0.0)
        t = max(0.05, min(t, clip_duration - 0.10))
        if any(abs(t - p) < dedup for p in placed):
            continue
        placed.append(t)
        cue = {"t": round(t, 3), "kind": str(opt["kind"]).strip().lower()}
        if opt.get("gain_db") is not None:
            cue["gain_db"] = float(opt["gain_db"])
        cues.append(cue)
        if len(cues) >= max_cues:
            break
    return cues


def _cli() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Build acoustic-anchor SFX cues for a moment")
    ap.add_argument("--moment-json", required=True)
    ap.add_argument("--clip-start", type=float, required=True)
    ap.add_argument("--clip-duration", type=float, required=True)
    ap.add_argument("--temp-dir", default=os.environ.get("CLIP_WORK_DIR", "/tmp/clipper"))
    args = ap.parse_args()
    moment = json.loads(Path(args.moment_json).read_text(encoding="utf-8"))
    cues = build(moment, args.clip_start, args.clip_duration,
                 temp_dir=args.temp_dir, seed=moment.get("timestamp", 0))
    print(json.dumps(cues, indent=2))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_cli())
