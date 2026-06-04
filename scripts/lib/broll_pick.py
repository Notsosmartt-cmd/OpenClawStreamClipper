#!/usr/bin/env python3
"""Pick a B-roll video from `assets/broll/<category>/library.json` by
matching transcript-derived nouns against entry tags.

Stage 6 emits `broll_inserts: [{at, noun, duration}, ...]`. This module
resolves each noun to a concrete file. Tag matching is exact-first,
substring-second, then random in-folder, then random in any folder.

Returns absolute paths so the caller (Stage 7) can pass them to FFmpeg
as `-i` inputs.
"""
from __future__ import annotations

import json
import os
import random
from pathlib import Path

ASSETS_ROOT = Path(__file__).resolve().parent.parent.parent / "assets"
BROLL_ROOT = ASSETS_ROOT / "broll"
VIDEO_EXTS = {".mp4", ".webm", ".mov", ".mkv"}


def _load(folder: Path) -> list[dict]:
    if not folder.is_dir():
        return []
    manifest = folder / "library.json"
    if manifest.is_file():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            out: list[dict] = []
            for e in data.get("entries", []):
                f = e.get("file")
                if not f:
                    continue
                p = (folder / f).resolve() if not os.path.isabs(f) else Path(f)
                if p.is_file() and p.suffix.lower() in VIDEO_EXTS:
                    out.append({**e, "_resolved": p})
            return out
        except (json.JSONDecodeError, OSError):
            pass
    return [
        {"file": p.name,
         "tags": [p.stem.replace("_", " ").lower()],
         "_resolved": p}
        for p in folder.rglob("*")
        if p.is_file() and p.suffix.lower() in VIDEO_EXTS
    ]


def _score(entry: dict, noun: str) -> int:
    tags = [str(t).lower() for t in entry.get("tags", [])]
    if noun in tags:
        return 100
    if any(noun in t or t in noun for t in tags):
        return 50
    return 0


def pick(noun: str, seed: object = None,
         preferred_subfolder: str | None = None) -> Path | None:
    rng = random.Random(hash(("broll", noun, str(seed))) & 0xFFFFFFFF)
    candidates: list[tuple[int, Path]] = []

    if preferred_subfolder:
        for entry in _load(BROLL_ROOT / preferred_subfolder):
            s = _score(entry, noun)
            if s > 0:
                candidates.append((s, entry["_resolved"]))

    if not candidates and BROLL_ROOT.is_dir():
        for sub in BROLL_ROOT.iterdir():
            if not sub.is_dir():
                continue
            for entry in _load(sub):
                s = _score(entry, noun)
                if s > 0:
                    candidates.append((s, entry["_resolved"]))

    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        top_score = candidates[0][0]
        return rng.choice([p for s, p in candidates if s == top_score])

    # Fallback: random video anywhere under broll/
    all_videos = [p for p in BROLL_ROOT.rglob("*")
                  if p.is_file() and p.suffix.lower() in VIDEO_EXTS]
    if all_videos:
        return rng.choice(all_videos)
    return None


def _cli() -> int:
    import argparse, sys
    ap = argparse.ArgumentParser()
    ap.add_argument("--noun", required=True)
    ap.add_argument("--seed", default="0")
    ap.add_argument("--prefer", default=None,
                    help="prefer this subfolder of assets/broll/")
    args = ap.parse_args()
    p = pick(args.noun, args.seed, args.prefer)
    if p:
        print(p)
        return 0
    return 1


if __name__ == "__main__":
    import sys
    sys.exit(_cli())
