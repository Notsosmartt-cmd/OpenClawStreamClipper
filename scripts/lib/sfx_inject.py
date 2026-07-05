#!/usr/bin/env python3
"""Pick SFX files from `assets/sfx/<kind>/` and emit FFmpeg `amix` inputs
for placing them at specific timestamps.

Reads `library.json` in each `assets/sfx/<kind>/` folder; falls back to
listing every audio file in the folder when no manifest exists. Returns:

    {
      "inputs": ["path1.mp3", "path2.mp3", ...],   # for `-i` flags
      "filter_defs": "[1:a]adelay=...|...,volume=0.7[sfx0];...",
      "mix_inputs": "[sfx0][sfx1]"                  # to feed into amix
    }

Caller stitches these into the existing audio mix (alongside source audio
+ optional VO + optional music bed). Sidechain ducking is a separate
filter applied AFTER amix — see `audio_chain.py` (or inline in stage 7).
"""
from __future__ import annotations

import json
import os
import random
from pathlib import Path

ASSETS_ROOT = Path(__file__).resolve().parent.parent.parent / "assets"
SFX_ROOT = ASSETS_ROOT / "sfx"
AUDIO_EXTS = {".wav", ".mp3", ".ogg", ".flac"}


def _candidates_for_kind(kind: str) -> list[Path]:
    folder = SFX_ROOT / kind
    if not folder.is_dir():
        return []

    manifest = folder / "library.json"
    if manifest.is_file():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            entries = data.get("entries", [])
            paths = []
            for e in entries:
                f = e.get("file")
                if not f:
                    continue
                p = (folder / f).resolve()
                if p.is_file() and p.suffix.lower() in AUDIO_EXTS:
                    paths.append(p)
            if paths:
                return paths
        except (json.JSONDecodeError, OSError):
            pass

    return [p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in AUDIO_EXTS]


def pick_sfx(kind: str, seed: object) -> Path | None:
    """Deterministic per-seed pick from the kind's SFX library."""
    pool = _candidates_for_kind(kind)
    if not pool:
        return None
    rng = random.Random(hash(("sfx", kind, str(seed))) & 0xFFFFFFFF)
    return rng.choice(pool)


_HAS_ASSETS_CACHE: dict[str, bool] = {}


def has_assets(kind: str) -> bool:
    """True when assets/sfx/<kind>/ resolves at least one usable audio file
    (honors library.json aliases like boom -> ../impact/*). Cached per kind so
    the acoustic cue builder can cheaply pick the first available kind for a
    beat. See scripts/lib/sfx_cues.py."""
    k = str(kind or "").strip().lower()
    if k in _HAS_ASSETS_CACHE:
        return _HAS_ASSETS_CACHE[k]
    r = bool(k) and len(_candidates_for_kind(k)) > 0
    _HAS_ASSETS_CACHE[k] = r
    return r


def measure_program_db(src: str, start: float, duration: float,
                       *, timeout: int = 45) -> float | None:
    """Mean program loudness (dB) of the clip's audio segment, via ffmpeg
    volumedetect. The number that makes SFX gain PROGRAM-RELATIVE instead of
    absolute (owner feedback 2026-07-04: a 0 dB boom is buried under loud rap
    audio but pops on a quiet-mic clip — the fixed per-kind gains implicitly
    assumed one program level). Returns None on any failure (failure-soft)."""
    import re
    import subprocess
    try:
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", "-ss", str(float(start)), "-t",
             str(float(duration)), "-i", str(src), "-vn",
             "-af", "volumedetect", "-f", "null", "-"],
            capture_output=True, text=True, timeout=timeout)
        m = re.search(r"mean_volume:\s*(-?\d+(?:\.\d+)?)\s*dB", r.stderr or "")
        return float(m.group(1)) if m else None
    except Exception:
        return None


def adaptive_gain_db(src: str, start: float, duration: float,
                     *, ref_db: float | None = None,
                     max_boost_db: float | None = None) -> float:
    """Boost (dB, >= 0) to ADD to every SFX cue so effects stay audible over loud
    program audio. boost = clamp(program_mean - ref, 0, max). Boost-only by design:
    quiet clips (program below ref) stay untouched — the owner confirmed SFX are
    already audible there. Env knobs: CLIP_SFX_ADAPTIVE=0 kills it,
    CLIP_SFX_REF_DB (default -20: the owner's quiet-mic clip measured -24.9 dB so
    it still gets 0 boost, while the loud rap clips at ~-15 get ~+5),
    CLIP_SFX_ADAPT_MAX_DB (default 9)."""
    if os.environ.get("CLIP_SFX_ADAPTIVE", "1").strip().lower() in ("0", "false", "no", "off"):
        return 0.0
    if ref_db is None:
        try:
            ref_db = float(os.environ.get("CLIP_SFX_REF_DB", "-20"))
        except ValueError:
            ref_db = -20.0
    if max_boost_db is None:
        try:
            max_boost_db = float(os.environ.get("CLIP_SFX_ADAPT_MAX_DB", "9"))
        except ValueError:
            max_boost_db = 9.0
    mean = measure_program_db(src, start, duration)
    if mean is None:
        return 0.0
    return max(0.0, min(mean - ref_db, max_boost_db))


def _cue_volume(cue: dict, default_volume: float, adapt_db: float = 0.0) -> float:
    """Per-cue linear volume. A cue may carry `gain_db` (dB relative to the
    source audio, 0 = at speech level, negative = ducked under speech) — the
    research's per-kind mix policy that lets a Vine boom ride hot on a punchline
    while most SFX sit below speech. Falls back to the layer default volume.
    `adapt_db` (adaptive_gain_db) shifts EVERY cue up on loud clips so the mix
    policy holds relative to the actual program level; the linear ceiling is 4.0
    (~+12 dB) — raised from the old 1.5, which capped any boost at +3.5 dB and
    made loud-clip SFX inaudible even with a correct gain."""
    g = cue.get("gain_db")
    if g is None:
        return max(0.05, min(default_volume * 10.0 ** (adapt_db / 20.0), 4.0))
    try:
        return max(0.05, min(10.0 ** ((float(g) + adapt_db) / 20.0), 4.0))
    except (TypeError, ValueError):
        return default_volume


def build_sfx_layer(cues: list[dict], seed: object,
                    base_input_index: int = 1,
                    sfx_volume: float = 0.7,
                    adapt_db: float = 0.0) -> dict:
    """Resolve each cue to a concrete file + delay, returning the FFmpeg
    pieces the renderer needs to splice into its filter_complex.

    base_input_index: the FFmpeg input index of the FIRST sfx file (i.e.
        the next index after any source/VO/music inputs). Caller bumps this
        as it adds more inputs to the command.

    Returns:
      {
        "inputs":      [Path, ...],       # in the order their `-i` flags appear
        "filter_defs": "...",             # one [Ni:a]adelay=...,volume=...[sfxN] per cue
        "mix_inputs":  "[sfx0][sfx1]...", # to splice into a downstream amix
        "n_inputs":    int,
      }
    """
    inputs: list[Path] = []
    defs: list[str] = []
    mix_labels: list[str] = []

    for i, cue in enumerate(cues):
        kind = cue.get("kind")
        t = float(cue.get("t", 0.0))
        if t < 0 or not kind:
            continue
        path = pick_sfx(kind, seed=(seed, i))
        if path is None:
            continue
        inputs.append(path)
        delay_ms = int(round(t * 1000))
        idx = base_input_index + len(inputs) - 1
        label = f"sfx{i}"
        vol = _cue_volume(cue, sfx_volume, adapt_db)
        defs.append(
            f"[{idx}:a]adelay={delay_ms}|{delay_ms},"
            f"volume={vol:.4f}[{label}]"
        )
        mix_labels.append(f"[{label}]")

    return {
        "inputs":      [str(p) for p in inputs],
        "filter_defs": ";".join(defs),
        "mix_inputs":  "".join(mix_labels),
        "n_inputs":    len(inputs),
    }


def _cli() -> int:
    import argparse, json, sys
    ap = argparse.ArgumentParser()
    ap.add_argument("--cues", required=True,
                    help='JSON list of {"t":..,"kind":..} entries')
    ap.add_argument("--seed", default="0")
    ap.add_argument("--base-input-index", type=int, default=1)
    args = ap.parse_args()
    cues = json.loads(args.cues)
    print(json.dumps(build_sfx_layer(cues, args.seed, args.base_input_index), indent=2))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_cli())
