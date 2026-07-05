#!/usr/bin/env python3
"""P-TIGHT — payoff-anchored clip boundary tightening (owner 2026-07-05).

Owner review of the L0 batch: "the clipping is grabbing a little too much — for the
shorter punchline jokes I want short clips, but I still want the pipeline's ability to
pick up on long talking segments." Concrete: the Shower-Bluff clip wasted 19.5 s of
setup before the punchline (+2 s tail); the Mental-Breakdown clip had 5 s of filler
tail. The rap-battle clips were a good length (don't touch).

So this trims HEAD and TAIL around the acoustic payoff — but ONLY for punchline-type
categories, and never for the long-form lanes:

  * Tighten set: funny / reactive / hot_take / controversial / comedy / social_callout.
  * EXEMPT (returned untouched): storytime, emotional, and anything whose primary
    pattern is a rap / freestyle / storytelling / interview / informational arc — the
    "keep long talking segments" guarantee, structural not hoped-for.

Head: find the true payoff (reusing sfx_cues._refine_payoff — the rescue/snap that
fixes the Hot-Cheeto 'boom at 1 s, real beat at 18 s' mis-anchor), and if the setup
before it exceeds head_max_s, pull clip_start up to payoff − head_lead_s (keeps a
breath of context, not a dead run-up). The cold-open teaser still prepends the payoff.
Tail: end at the last speech/reaction burst within payoff + tail_max_s and drop the
trailing low-RMS filler.

Flag `CLIP_TIGHT_PUNCHLINE` (default OFF) + failure-soft: no flag, exempt category,
missing audio, or any error -> the ORIGINAL (clip_start, clip_duration), byte-identical
to today. A min_final_s floor prevents over-cutting."""
from __future__ import annotations

import os
from pathlib import Path

# Categories whose clips are punchline-shaped -> tighten. Everything else is left alone.
_TIGHTEN_CATS = {"funny", "reactive", "hot_take", "controversial", "comedy",
                 "social_callout"}
# Patterns that are long-form by nature -> never tighten, even if the category matches.
_EXEMPT_PATTERN_SUBSTR = ("rap", "freestyle", "storytell", "interview", "informational",
                          "ramble")
_EXEMPT_CATS = {"storytime", "emotional"}

_DEFAULTS = {
    "enabled": False,
    "head_max_s": 10.0,     # setup longer than this before the payoff -> trim the head
    "head_lead_s": 6.0,     # keep this much run-up before the payoff after trimming
    "tail_max_s": 8.0,      # look this far past the payoff for the last real activity
    "tail_pad_s": 1.0,      # keep this much after the last burst
    "min_final_s": 6.0,     # never cut a clip below this
}


def _cfg() -> dict:
    c = dict(_DEFAULTS)
    c["enabled"] = os.environ.get("CLIP_TIGHT_PUNCHLINE", "0").strip().lower() in (
        "1", "true", "yes", "on")
    for k in ("head_max_s", "head_lead_s", "tail_max_s", "tail_pad_s", "min_final_s"):
        v = os.environ.get("CLIP_TIGHT_" + k.upper())
        if v:
            try:
                c[k] = float(v)
            except ValueError:
                pass
    return c


def _category(moment: dict) -> str:
    return str(moment.get("category") or moment.get("primary_category") or "").strip().lower()


def _exempt(moment: dict) -> bool:
    cat = _category(moment)
    if cat in _EXEMPT_CATS or cat not in _TIGHTEN_CATS:
        return True
    pat = str(moment.get("primary_pattern") or "").lower()
    return any(s in pat for s in _EXEMPT_PATTERN_SUBSTR)


def _rms_env(temp_dir: str, a_abs: float, b_abs: float):
    """(rms envelope array, hop_s) for absolute [a_abs, b_abs); (None, None) on any
    problem. 50 ms hop."""
    try:
        import numpy as np
        import soundfile as sf
        wav = Path(temp_dir, "audio.wav")
        if not wav.exists():
            return None, None
        sr = sf.info(str(wav)).samplerate
        frames = int(max(0.0, b_abs - a_abs) * sr)
        if frames < sr // 4:
            return None, None
        data, _ = sf.read(str(wav), start=max(0, int(a_abs * sr)), frames=frames,
                          dtype="float32", always_2d=False)
        if data is None or getattr(data, "size", 0) < sr // 4:
            return None, None
        if getattr(data, "ndim", 1) > 1:
            data = data.mean(axis=1)
        hop = max(1, int(0.05 * sr))
        n_h = len(data) // hop
        if n_h < 3:
            return None, None
        return np.sqrt(np.mean(data[:n_h * hop].reshape(n_h, hop) ** 2, axis=1) + 1e-12), hop / sr
    except Exception:
        return None, None


def tighten(moment: dict, clip_start: float, clip_duration: float, *,
            temp_dir: str) -> tuple[float, float]:
    """Return a (possibly) tightened (clip_start, clip_duration). Failure-soft: any
    reason to not tighten returns the inputs unchanged."""
    cfg = _cfg()
    orig = (float(clip_start), float(clip_duration))
    if not cfg["enabled"] or _exempt(moment):
        return orig
    try:
        clip_start = float(clip_start)
        clip_duration = float(clip_duration)
        if clip_duration <= cfg["min_final_s"]:
            return orig
        clip_end = clip_start + clip_duration

        # True payoff (rel to clip_start): reuse the SFX rescue/snap so head-trim aims
        # at the real beat, not the (possibly early) detection timestamp.
        try:
            import sfx_cues
            payoff_rel = float(moment.get("timestamp", clip_start + clip_duration / 2) or 0) - clip_start
            payoff_rel = max(0.0, min(payoff_rel, clip_duration))
            payoff_rel = sfx_cues._refine_payoff(payoff_rel, clip_start, clip_duration,
                                                 temp_dir, sfx_cues.load_config())
        except Exception:
            payoff_rel = float(moment.get("timestamp", clip_start + clip_duration / 2) or 0) - clip_start
        payoff_abs = clip_start + payoff_rel

        new_start, new_end = clip_start, clip_end

        # --- HEAD: too much setup before the payoff -> pull start up ---
        if payoff_rel > cfg["head_max_s"]:
            new_start = max(clip_start, payoff_abs - cfg["head_lead_s"])

        # --- TAIL: end at the last real activity within payoff + tail_max_s ---
        env, hop_s = _rms_env(temp_dir, payoff_abs, min(clip_end, payoff_abs + cfg["tail_max_s"]))
        if env is not None:
            import numpy as np
            thr = 0.30 * float(env.max())
            active = np.where(env >= thr)[0]
            if len(active):
                last_active_abs = payoff_abs + (int(active[-1]) + 1) * hop_s
                cand_end = min(clip_end, last_active_abs + cfg["tail_pad_s"])
                if cand_end > new_start + cfg["min_final_s"]:
                    new_end = cand_end

        # Enforce the floor + only ever SHRINK (never extend).
        new_start = max(clip_start, min(new_start, clip_end - cfg["min_final_s"]))
        new_end = min(clip_end, max(new_end, new_start + cfg["min_final_s"]))
        new_dur = new_end - new_start
        if new_dur < cfg["min_final_s"] or new_dur >= clip_duration - 0.4:
            return orig  # nothing worth trimming (or would under-cut)
        return round(new_start, 2), round(new_dur, 2)
    except Exception:
        return orig


if __name__ == "__main__":
    import json
    import sys
    m = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {"timestamp": 20, "category": "funny"}
    print(tighten(m, float(sys.argv[2]) if len(sys.argv) > 2 else 0.0,
                  float(sys.argv[3]) if len(sys.argv) > 3 else 30.0,
                  temp_dir=os.environ.get("CLIP_WORK_DIR", "/tmp/clipper")))
