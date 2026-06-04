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


def build_sfx_layer(cues: list[dict], seed: object,
                    base_input_index: int = 1,
                    sfx_volume: float = 0.7) -> dict:
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
        defs.append(
            f"[{idx}:a]adelay={delay_ms}|{delay_ms},"
            f"volume={sfx_volume}[{label}]"
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
