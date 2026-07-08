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

NOTHING here has a fixed/target length — both edges are DERIVED FROM THE CONTENT
(owner directive 2026-07-05: "I don't want a fixed length, content is highly
variable"). The only fixed numbers are BOUNDS (min/max lead, tail cap, min floor)
that keep the result sane, never a target.

Head: find the true payoff (reusing sfx_cues._refine_payoff — the rescue/snap that
fixes the Hot-Cheeto 'boom at 1 s, real beat at 18 s' mis-anchor), then snap clip_start
to the NATURAL BEGINNING of the utterance leading into it — the most recent silence gap
in the audio (the streamer's pause before starting the bit), refined to the nearest
transcript sentence boundary. A one-sentence setup yields a short head; a built-up bit
keeps more; a monologue is capped at head_max_lead_s. The cold-open teaser still
prepends the payoff. Tail: end at the last speech/reaction burst within payoff +
tail_max_s and drop the trailing low-RMS filler — also content-derived.

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
# Segment types whose flow matters (owner 2026-07-08: the Disney clip was rap-battle
# commentary mis-patterned as social_callout — when the SEGMENT classifier catches the
# rap/music context, exempt even if the per-moment pattern missed it).
_EXEMPT_SEGMENT_SUBSTR = ("rap", "freestyle", "music", "karaoke", "singing")

# NB: none of these is a TARGET length. head_min/max_lead + tail_max are BOUNDS
# (guardrails), the clip edges themselves are derived from the audio/transcript.
_DEFAULTS = {
    "enabled": False,
    # HEAD: start snaps to the natural beginning of the utterance leading to the
    # payoff (silence gap / sentence boundary). These only BOUND that search — a
    # short setup stays short, a long one is capped; the value is content-derived.
    "head_min_lead_s": 4.0,     # always keep at least this much run-up (owner 2026-07-08:
                                # 2.0 cut too close to the punchline on dialog clips)
    "head_max_lead_s": 12.0,    # never keep MORE setup than this (guardrail for monologues)
    "head_gap_min_s": 0.30,     # a silence >= this counts as an utterance boundary
    "head_silence_frac": 0.20,  # "silence" = RMS below this fraction of the window's speech level
    "head_min_sentences": 2,    # head must start >= this many transcript sentence-starts
                                # before the payoff (keeps the prior exchange line — the
                                # Coke-Machine escalation/dialog build-up guard)
    "tail_max_s": 8.0,          # look at most this far past the payoff for the last activity
    "tail_pad_s": 1.0,          # keep this much after the last burst
    "min_final_s": 6.0,         # safety floor — never emit a clip shorter than this
}


def _cfg() -> dict:
    c = dict(_DEFAULTS)
    c["enabled"] = os.environ.get("CLIP_TIGHT_PUNCHLINE", "0").strip().lower() in (
        "1", "true", "yes", "on")
    for k in ("head_min_lead_s", "head_max_lead_s", "head_gap_min_s", "head_silence_frac",
              "head_min_sentences", "tail_max_s", "tail_pad_s", "min_final_s"):
        v = os.environ.get("CLIP_TIGHT_" + k.upper())
        if v:
            try:
                c[k] = float(v)
            except ValueError:
                pass
    return c


def _segment_starts(temp_dir: str) -> list:
    """Transcript segment/word start times (sorted) for boundary snapping, or []."""
    import json
    try:
        segs = json.loads(Path(temp_dir, "transcript.json").read_text(encoding="utf-8"))
        return sorted(float(s["start"]) for s in segs if isinstance(s.get("start"), (int, float)))
    except Exception:
        return []


def _natural_head_start(payoff_abs: float, clip_start: float, temp_dir: str,
                        cfg: dict) -> float:
    """Content-adaptive clip start: the beginning of the utterance that leads into the
    payoff, NOT a fixed offset. Walk back from the payoff through the audio energy;
    the start is just after the most recent real SILENCE GAP (the natural pause before
    the streamer starts the bit). Snap that to the nearest transcript sentence
    boundary when a transcript is present. Bounded only by head_min_lead_s /
    head_max_lead_s (guardrails, not targets). Returns the absolute start; falls back
    to clip_start (no head trim) if nothing can be resolved."""
    min_lead = float(cfg["head_min_lead_s"])
    max_lead = float(cfg["head_max_lead_s"])
    latest = payoff_abs - min_lead          # a start must be at least this far before the payoff
    earliest = payoff_abs - max_lead        # ...and at most this far
    if latest <= clip_start:
        return clip_start                   # already tighter than min_lead — leave it

    natural = None
    # --- acoustic: last silence gap in [earliest-1, latest] -> utterance start ---
    env, hop = _rms_env(temp_dir, max(clip_start, earliest - 1.0), latest)
    if env is not None and len(env) > 3:
        import numpy as np
        base = max(earliest - 1.0, clip_start)
        # Speech reference from the PAYOFF (always speech) so the silence threshold
        # holds even when the head window is mostly silence — else p75 of a quiet
        # window collapses and real pauses go undetected.
        ref, _ = _rms_env(temp_dir, payoff_abs, payoff_abs + 1.5)
        speech = (float(np.percentile(ref, 75)) if ref is not None and len(ref) else
                  float(np.percentile(env, 90))) or 1e-6
        thr = float(cfg["head_silence_frac"]) * speech
        gap_frames = max(1, int(float(cfg["head_gap_min_s"]) / hop))
        run = 0
        # scan forward; the END of the LAST qualifying gap is the utterance start
        for i, v in enumerate(env):
            if float(v) < thr:
                run += 1
            else:
                if run >= gap_frames:
                    natural = base + i * hop        # first speech frame after the gap
                run = 0

    # --- transcript refinement: snap to the nearest sentence boundary in-window ---
    segs = [s for s in _segment_starts(temp_dir) if earliest <= s <= latest]
    if segs:
        anchor = natural if natural is not None else earliest
        natural = min(segs, key=lambda s: abs(s - anchor))

    # --- min-sentences guard (owner 2026-07-08): the head must start at least
    # head_min_sentences transcript sentence-starts before the payoff — i.e. keep the
    # payoff's own line PLUS the exchange line(s) leading into it. Fixes the
    # dialog/escalation over-cut (Coke-Machine: students' argument build-up trimmed;
    # Disney: punchline left without its lead-in). Content-derived: if the transcript
    # has no earlier sentence in the max_lead window, nothing is forced. ---
    if natural is not None:
        min_sent = int(cfg.get("head_min_sentences", 0) or 0)
        if min_sent > 0:
            all_starts = [s for s in _segment_starts(temp_dir) if earliest <= s <= payoff_abs]
            # count sentences the clip will CONTAIN up to the payoff (inclusive of the
            # one the head starts on): payoff's line + its lead-in line(s).
            n_have = sum(1 for s in all_starts if natural <= s <= payoff_abs)
            if n_have < min_sent:
                earlier = [s for s in all_starts if s < natural]
                need = min_sent - n_have
                if len(earlier) >= need:
                    natural = earlier[-need]

    if natural is None:
        # No boundary found. If the run-up exceeds the guardrail, cap it; otherwise
        # keep the original start (the setup is already a reasonable length).
        return earliest if (payoff_abs - clip_start) > max_lead else clip_start
    # clamp into [earliest, latest] and only ever move the start LATER (shrink)
    return max(clip_start, min(max(natural, earliest), latest))


def _category(moment: dict) -> str:
    return str(moment.get("category") or moment.get("primary_category") or "").strip().lower()


def _exempt(moment: dict) -> bool:
    cat = _category(moment)
    if cat in _EXEMPT_CATS or cat not in _TIGHTEN_CATS:
        return True
    pat = str(moment.get("primary_pattern") or "").lower()
    if any(s in pat for s in _EXEMPT_PATTERN_SUBSTR):
        return True
    seg = str(moment.get("segment_type") or "").lower()
    return any(s in seg for s in _EXEMPT_SEGMENT_SUBSTR)


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

        # --- HEAD: snap start to the natural beginning of the utterance leading to
        # the payoff (silence gap / sentence boundary) — content-derived, NOT a fixed
        # lead. Only shrinks; bounded by head_min/max_lead guardrails. ---
        new_start = _natural_head_start(payoff_abs, clip_start, temp_dir, cfg)

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
