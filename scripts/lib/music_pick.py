#!/usr/bin/env python3
"""Pick the best-matching background-music track from a library folder.

Supports two tiers:

- Tier A (default): folder-convention + filename heuristic. If the library has
  subfolders matching a clip category (``hype/``, ``funny/``, ``emotional/``,
  ``storytime/``, ``neutral/``), pick at random from the matching folder.
  Otherwise pick at random from the whole folder. Zero dependencies.

- Tier C (opt-in): reads ``music_library.json`` produced by ``scan_music.py``
  (librosa feature extraction) and chooses the track whose tempo/energy
  profile best matches the clip's category, segment type, and duration.
  Falls back to tier A if the sidecar is missing.

Prints the chosen absolute path to stdout; nothing if no match is found.
"""
import argparse
import json
import os
import random
import sys
from pathlib import Path

AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".ogg", ".flac"}

# Category → preferred target profile for tier C matching.
# Values are floats in normalized units produced by scan_music.py.
CATEGORY_TARGETS: dict[str, dict[str, float]] = {
    "hype":          {"energy": 0.80, "tempo": 140.0, "brightness": 0.70},
    "reactive":      {"energy": 0.70, "tempo": 128.0, "brightness": 0.65},
    "funny":         {"energy": 0.55, "tempo": 110.0, "brightness": 0.60},
    "controversial": {"energy": 0.60, "tempo": 105.0, "brightness": 0.55},
    "hot_take":      {"energy": 0.50, "tempo": 100.0, "brightness": 0.55},
    "storytime":     {"energy": 0.30, "tempo":  85.0, "brightness": 0.40},
    "emotional":     {"energy": 0.20, "tempo":  72.0, "brightness": 0.30},
    "dancing":       {"energy": 0.85, "tempo": 128.0, "brightness": 0.75},
}


def walk_library(root: Path, category: str) -> list[Path]:
    """Tier A: collect candidate tracks, preferring a category subfolder."""
    subfolder = root / category
    if subfolder.is_dir():
        pool = [p for p in subfolder.rglob("*") if p.suffix.lower() in AUDIO_EXTS]
        if pool:
            return pool

    neutral = root / "neutral"
    if neutral.is_dir():
        pool = [p for p in neutral.rglob("*") if p.suffix.lower() in AUDIO_EXTS]
        if pool:
            return pool

    return [p for p in root.rglob("*") if p.suffix.lower() in AUDIO_EXTS]


def score_track(track: dict, target: dict[str, float], duration: float) -> float:
    """Lower is better. Composite distance over tempo/energy/brightness +
    a penalty for tracks shorter than the clip (they'd need to loop)."""
    try:
        tempo = float(track.get("tempo", 100.0))
        energy = float(track.get("energy_rms", 0.3))
        brightness = float(track.get("brightness", 0.5))
        track_dur = float(track.get("duration_s", 0.0))
    except (TypeError, ValueError):
        return 9e9

    dt = abs(tempo - target["tempo"]) / 80.0       # ~0 when matched, 1 per 80 BPM off
    de = abs(energy - target["energy"]) * 2.0      # energy is 0..1
    db = abs(brightness - target["brightness"]) * 1.5
    loop_penalty = 0.25 if track_dur < duration * 0.9 else 0.0
    return dt + de + db + loop_penalty


def pick_tier_c(library: Path, category: str, duration: float,
                seed: int) -> str | None:
    sidecar = library / "music_library.json"
    if not sidecar.is_file():
        return None
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"# music_library.json unreadable: {e}", file=sys.stderr)
        return None

    tracks = data.get("tracks") or []
    if not tracks:
        return None

    target = CATEGORY_TARGETS.get(category, CATEGORY_TARGETS["hype"])
    rng = random.Random(seed)

    scored = []
    for t in tracks:
        path = t.get("path")
        if not path:
            continue
        full = (library / path) if not os.path.isabs(path) else Path(path)
        if not full.is_file():
            continue
        dist = score_track(t, target, duration)
        # Add a small deterministic jitter so ties don't always pick the same
        # file across a batch — keeps variety while respecting the ranking.
        dist += rng.uniform(0.0, 0.15)
        scored.append((dist, full))

    if not scored:
        return None

    scored.sort(key=lambda x: x[0])
    # Pick from the top 3 at random to avoid always returning the same track.
    topk = scored[: min(3, len(scored))]
    return str(rng.choice(topk)[1])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--library", required=True, help="Music folder path")
    parser.add_argument("--category", default="hype")
    parser.add_argument("--segment", default="")
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--tier-c", default="false",
                        help="'true' to use librosa-scored tier C matching")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    library = Path(args.library)
    if not library.is_dir():
        return 1

    tier_c = args.tier_c.lower() == "true"
    if tier_c:
        chosen = pick_tier_c(library, args.category, args.duration, args.seed)
        if chosen:
            print(chosen)
            return 0
        print("# tier-c no library sidecar — falling back to tier A", file=sys.stderr)

    pool = walk_library(library, args.category)
    if not pool:
        return 1

    rng = random.Random(args.seed)
    print(str(rng.choice(pool)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
