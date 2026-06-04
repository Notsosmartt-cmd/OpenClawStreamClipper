#!/usr/bin/env python3
"""Pick a reaction-meme image from `assets/memes/<category>/library.json`.

Stage 6 vision emits `{at: <s>, tag: "<word>"}`. This module resolves
the tag against the category manifest. Falls back to the `generic/`
folder, then to a random image in any category, then returns None when
the library is empty.

Manifest format (created by scripts/fetch_assets.py and editable by users):

    {
      "version": 1,
      "entries": [
        {"file": "../generic/skull.png", "tags": ["dead","lol","rip"], ...},
        ...
      ]
    }

Files referenced by relative paths (../generic/...) are resolved relative
to the manifest's containing folder.
"""
from __future__ import annotations

import json
import os
import random
from pathlib import Path

ASSETS_ROOT = Path(__file__).resolve().parent.parent.parent / "assets"
MEME_ROOT = ASSETS_ROOT / "memes"
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


def _load(folder: Path) -> list[dict]:
    """Read entries from library.json, or auto-generate from filenames if missing."""
    if not folder.is_dir():
        return []
    manifest = folder / "library.json"
    if manifest.is_file():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            entries: list[dict] = []
            for e in data.get("entries", []):
                f = e.get("file")
                if not f:
                    continue
                p = (folder / f).resolve() if not os.path.isabs(f) else Path(f)
                if p.is_file() and p.suffix.lower() in IMAGE_EXTS:
                    entries.append({**e, "_resolved": p})
            return entries
        except (json.JSONDecodeError, OSError):
            pass
    # Auto-scan
    out: list[dict] = []
    for p in folder.rglob("*"):
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS:
            out.append({
                "file": p.name,
                "tags": [p.stem.replace("_", " ").lower()],
                "_resolved": p,
            })
    return out


def _score(entry: dict, tag: str) -> int:
    tags = [str(t).lower() for t in entry.get("tags", [])]
    if tag in tags:
        return 100
    # Substring match either way
    if any(tag in t or t in tag for t in tags):
        return 50
    return 0


def pick(category: str, tag: str, seed: object = None) -> Path | None:
    """Return a Path to a meme image matching `tag` in `category`,
    falling back to generic, then random in any category."""
    rng = random.Random(hash(("meme", category, tag, str(seed))) & 0xFFFFFFFF)

    candidates: list[tuple[int, Path]] = []
    cat_folder = MEME_ROOT / (category or "")
    for entry in _load(cat_folder):
        s = _score(entry, tag)
        if s > 0:
            candidates.append((s, entry["_resolved"]))
    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        top_score = candidates[0][0]
        top = [p for s, p in candidates if s == top_score]
        return rng.choice(top)

    # Fallback 1: generic folder
    for entry in _load(MEME_ROOT / "generic"):
        s = _score(entry, tag)
        if s > 0:
            candidates.append((s, entry["_resolved"]))
    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        return rng.choice([p for s, p in candidates if s == candidates[0][0]])

    # Fallback 2: random image anywhere under memes/
    if MEME_ROOT.is_dir():
        all_images = [p for p in MEME_ROOT.rglob("*")
                      if p.is_file() and p.suffix.lower() in IMAGE_EXTS]
        if all_images:
            return rng.choice(all_images)
    return None


def _cli() -> int:
    import argparse, sys
    ap = argparse.ArgumentParser()
    ap.add_argument("--category", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--seed", default="0")
    args = ap.parse_args()
    p = pick(args.category, args.tag, args.seed)
    if p:
        print(p)
        return 0
    return 1


if __name__ == "__main__":
    import sys
    sys.exit(_cli())
