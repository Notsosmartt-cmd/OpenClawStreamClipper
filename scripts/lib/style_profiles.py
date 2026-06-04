#!/usr/bin/env python3
"""Per-category editing profile resolver.

Maps Stage 6's `category` field (hype/comedy/skill/reactive/controversy/
emotional/storytime/irl/dancing/hot_take) to a templated set of editing knobs:
visual layers (zoom punches, freeze frames, slow-mo, mirror), audio layers
(SFX cues, music category, sidechain duck), caption preset, and originality
bias (saturation/contrast boost, vignette/shake probabilities).

Profiles are TEMPLATES — every probabilistic / range field is resolved per
clip via a deterministic seed (the moment timestamp) so two same-category
clips never render identically.

Read by Stage 7's profile-mode renderer when CLIP_STYLE_PROFILES=true. Module
also exports `fingerprint_params()` for the always-on pixel/audio/container
perturbation layer.
"""
from __future__ import annotations

import hashlib
import random
from typing import Any


# ─────────────────────────────────────────────────────────────────────────────
# Profile templates
# ─────────────────────────────────────────────────────────────────────────────

# Each profile entry is a dict of effect knobs. Tuple values like (a, b) are
# resolved as a uniform random in [a, b] (int range if both ints, else float).
# `*_prob` floats are resolved as a coin flip per clip. Lists stay literal.

PROFILES: dict[str, dict[str, Any]] = {
    "hype": {
        "zoom_punch_count":       (2, 3),
        "freeze_frame_prob":      0.0,
        "slow_mo_prob":           0.0,
        "meme_cutaway_prob":      0.0,
        "broll_insert_prob":      0.0,
        "use_glitch_transition":  True,
        "saturation_boost":       0.18,
        "contrast_boost":         0.12,
        "mirror_prob":            0.50,
        "vignette_prob":          0.10,
        "shake_prob":             0.40,
        "caption_preset":         "neon",
        "music_category":         "hype",
        "sfx_on_cuts":            ["whoosh", "impact"],
        "sfx_on_peak":            ["impact", "riser"],
        "punchline_pitch_cents":  0,
        "punchline_echo":         False,
        "chat_overlay":           False,
    },
    "comedy": {
        "zoom_punch_count":       (1, 2),
        "freeze_frame_prob":      0.85,
        "slow_mo_prob":           0.0,
        "meme_cutaway_prob":      0.75,
        "broll_insert_prob":      0.10,
        "use_glitch_transition":  False,
        "saturation_boost":       0.10,
        "contrast_boost":         0.05,
        "mirror_prob":            0.20,
        "vignette_prob":          0.20,
        "shake_prob":             0.20,
        "caption_preset":         "bouncy",
        "music_category":         "funny",
        "sfx_on_cuts":            ["scratch", "ding"],
        "sfx_on_peak":            ["scratch"],
        "punchline_pitch_cents":  200,
        "punchline_echo":         True,
        "chat_overlay":           False,
    },
    "skill": {
        "zoom_punch_count":       (1, 2),
        "freeze_frame_prob":      0.10,
        "slow_mo_prob":           0.85,
        "meme_cutaway_prob":      0.0,
        "broll_insert_prob":      0.0,
        "use_glitch_transition":  False,
        "saturation_boost":       0.12,
        "contrast_boost":         0.08,
        "mirror_prob":            0.10,
        "vignette_prob":          0.10,
        "shake_prob":             0.20,
        "caption_preset":         "clean",
        "music_category":         "hype",
        "sfx_on_cuts":            ["impact", "riser"],
        "sfx_on_peak":            ["impact"],
        "punchline_pitch_cents":  0,
        "punchline_echo":         False,
        "chat_overlay":           False,
    },
    "reactive": {
        "zoom_punch_count":       (1, 2),
        "freeze_frame_prob":      0.30,
        "slow_mo_prob":           0.0,
        "meme_cutaway_prob":      0.20,
        "broll_insert_prob":      0.0,
        "use_glitch_transition":  False,
        "saturation_boost":       0.10,
        "contrast_boost":         0.05,
        "mirror_prob":            0.50,
        "vignette_prob":          0.15,
        "shake_prob":             0.25,
        "caption_preset":         "bouncy",
        "music_category":         "reactive",
        "sfx_on_cuts":            ["whoosh", "ding"],
        "sfx_on_peak":            ["impact"],
        "punchline_pitch_cents":  0,
        "punchline_echo":         False,
        "chat_overlay":           False,
    },
    "controversy": {
        "zoom_punch_count":       (1, 2),
        "freeze_frame_prob":      0.50,
        "slow_mo_prob":           0.0,
        "meme_cutaway_prob":      0.0,
        "broll_insert_prob":      0.10,
        "use_glitch_transition":  False,
        "saturation_boost":       0.05,
        "contrast_boost":         0.10,
        "mirror_prob":            0.0,
        "vignette_prob":          0.30,
        "shake_prob":             0.10,
        "caption_preset":         "news",
        "music_category":         "tension",
        "sfx_on_cuts":            ["impact"],
        "sfx_on_peak":            ["riser"],
        "punchline_pitch_cents":  0,
        "punchline_echo":         False,
        "chat_overlay":           True,
    },
    "hot_take": {
        "zoom_punch_count":       (1, 2),
        "freeze_frame_prob":      0.50,
        "slow_mo_prob":           0.0,
        "meme_cutaway_prob":      0.0,
        "broll_insert_prob":      0.0,
        "use_glitch_transition":  False,
        "saturation_boost":       0.05,
        "contrast_boost":         0.10,
        "mirror_prob":            0.0,
        "vignette_prob":          0.30,
        "shake_prob":             0.10,
        "caption_preset":         "news",
        "music_category":         "tension",
        "sfx_on_cuts":            ["impact"],
        "sfx_on_peak":            ["riser"],
        "punchline_pitch_cents":  0,
        "punchline_echo":         False,
        "chat_overlay":           True,
    },
    "emotional": {
        "zoom_punch_count":       (0, 1),
        "freeze_frame_prob":      0.0,
        "slow_mo_prob":           0.0,
        "meme_cutaway_prob":      0.0,
        "broll_insert_prob":      0.0,
        "use_glitch_transition":  False,
        "saturation_boost":       -0.05,
        "contrast_boost":         -0.02,
        "mirror_prob":            0.0,
        "vignette_prob":          0.60,
        "shake_prob":             0.0,
        "caption_preset":         "soft",
        "music_category":         "emotional",
        "sfx_on_cuts":            [],
        "sfx_on_peak":            [],
        "punchline_pitch_cents":  0,
        "punchline_echo":         False,
        "chat_overlay":           False,
    },
    "storytime": {
        "zoom_punch_count":       (0, 1),
        "freeze_frame_prob":      0.10,
        "slow_mo_prob":           0.0,
        "meme_cutaway_prob":      0.0,
        "broll_insert_prob":      0.85,
        "use_glitch_transition":  False,
        "saturation_boost":       0.05,
        "contrast_boost":         0.03,
        "mirror_prob":            0.0,
        "vignette_prob":          0.20,
        "shake_prob":             0.0,
        "caption_preset":         "clean",
        "music_category":         "storytime",
        "sfx_on_cuts":            ["whoosh"],
        "sfx_on_peak":            [],
        "punchline_pitch_cents":  0,
        "punchline_echo":         False,
        "chat_overlay":           False,
    },
    "irl": {
        "zoom_punch_count":       (1, 2),
        "freeze_frame_prob":      0.10,
        "slow_mo_prob":           0.0,
        "meme_cutaway_prob":      0.10,
        "broll_insert_prob":      0.50,
        "use_glitch_transition":  False,
        "saturation_boost":       0.08,
        "contrast_boost":         0.05,
        "mirror_prob":            0.20,
        "vignette_prob":          0.15,
        "shake_prob":             0.15,
        "caption_preset":         "clean",
        "music_category":         "reactive",
        "sfx_on_cuts":            ["whoosh", "ding"],
        "sfx_on_peak":            [],
        "punchline_pitch_cents":  0,
        "punchline_echo":         False,
        "chat_overlay":           False,
    },
    "dancing": {
        "zoom_punch_count":       (3, 5),
        "freeze_frame_prob":      0.0,
        "slow_mo_prob":           0.0,
        "meme_cutaway_prob":      0.0,
        "broll_insert_prob":      0.0,
        "use_glitch_transition":  True,
        "saturation_boost":       0.20,
        "contrast_boost":         0.10,
        "mirror_prob":            0.50,
        "vignette_prob":          0.10,
        "shake_prob":             0.30,
        "caption_preset":         "neon",
        "music_category":         "hype",
        "sfx_on_cuts":            [],
        "sfx_on_peak":            [],
        "punchline_pitch_cents":  0,
        "punchline_echo":         False,
        "chat_overlay":           False,
    },
}

# Aliases — map LLM-emitted category synonyms to canonical profile keys.
CATEGORY_ALIASES: dict[str, str] = {
    "funny":        "comedy",
    "humor":        "comedy",
    "comedic":      "comedy",
    "joke":         "comedy",
    "reaction":     "reactive",
    "reactive":     "reactive",
    "controversial": "controversy",
    "skill_play":   "skill",
    "gameplay":     "skill",
    "clutch":       "skill",
    "hype":         "hype",
    "emotional":    "emotional",
    "sad":          "emotional",
    "story":        "storytime",
    "storytime":    "storytime",
    "anecdote":     "storytime",
    "in_real_life": "irl",
    "irl":          "irl",
    "dance":        "dancing",
    "dancing":      "dancing",
    "hot_take":     "hot_take",
    "opinion":      "hot_take",
}

DEFAULT_CATEGORY = "reactive"


# ─────────────────────────────────────────────────────────────────────────────
# Resolution
# ─────────────────────────────────────────────────────────────────────────────

def _seeded_rng(seed: Any) -> random.Random:
    s = int(hashlib.md5(str(seed).encode()).hexdigest()[:8], 16)
    return random.Random(s)


def canonical_category(category: str | None) -> str:
    if not category:
        return DEFAULT_CATEGORY
    cat = str(category).strip().lower()
    if cat in PROFILES:
        return cat
    return CATEGORY_ALIASES.get(cat, DEFAULT_CATEGORY)


def get_profile(category: str | None, seed: Any = None) -> dict[str, Any]:
    """Return a resolved profile dict for the given category.

    With `seed`, every range/probability field collapses to a concrete value
    (range → int/float in [a,b], probability → bool from coin flip). Same seed
    + same category always produces the same resolution.
    """
    cat = canonical_category(category)
    base = PROFILES.get(cat, PROFILES[DEFAULT_CATEGORY])

    if seed is None:
        return dict(base)

    rng = _seeded_rng(("profile", seed, cat))
    out: dict[str, Any] = {"_category": cat}
    for k, v in base.items():
        if isinstance(v, tuple) and len(v) == 2 and all(isinstance(x, (int, float)) for x in v):
            lo, hi = v
            if isinstance(lo, int) and isinstance(hi, int):
                out[k] = rng.randint(lo, hi)
            else:
                out[k] = round(rng.uniform(lo, hi), 3)
        elif k.endswith("_prob") and isinstance(v, (int, float)):
            out[k] = (rng.random() < v)
            out[k + "_value"] = float(v)  # preserve original probability
        else:
            out[k] = v
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Audio + container fingerprint perturbation (always-on when profile mode is on)
# ─────────────────────────────────────────────────────────────────────────────

def fingerprint_params(seed: Any) -> dict[str, Any]:
    """Per-clip perturbation knobs. Designed so each clip's bitstream and
    audio fingerprint differ slightly while staying perceptually identical:

    - pitch_cents:   ±2..5¢ pitch shift via rubberband (sub-perceptual)
    - eq_tilt_db:    ±0.4 dB tilt at ~3 kHz via firequalizer
    - gop:           240..360 — randomize keyframe interval
    - crf_jitter:    -1..1 — small variance around the base CRF
    - metadata_strip: True (always) — `-map_metadata -1 -fflags +bitexact`
    - encoder_string: random short token written into `comment` metadata
    """
    rng = _seeded_rng(("fingerprint", seed))
    sign = 1 if rng.random() < 0.5 else -1
    pitch_cents = round(sign * rng.uniform(2.0, 5.0), 2)
    eq_tilt_db = round((1 if rng.random() < 0.5 else -1) * rng.uniform(0.2, 0.6), 2)
    gop = rng.randint(240, 360)
    crf_jitter = rng.randint(-1, 1)
    enc_token = "oc" + "".join(rng.choice("abcdef0123456789") for _ in range(8))
    return {
        "pitch_cents":   pitch_cents,
        "eq_tilt_db":    eq_tilt_db,
        "gop":           gop,
        "crf_jitter":    crf_jitter,
        "encoder_token": enc_token,
    }


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def _cli() -> int:
    import argparse, json, sys
    ap = argparse.ArgumentParser()
    ap.add_argument("category")
    ap.add_argument("--seed", default="0")
    ap.add_argument("--fingerprint", action="store_true",
                    help="emit fingerprint params instead of profile")
    args = ap.parse_args()
    if args.fingerprint:
        print(json.dumps(fingerprint_params(args.seed), indent=2))
    else:
        print(json.dumps(get_profile(args.category, seed=args.seed), indent=2))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_cli())
