#!/usr/bin/env python3
"""One-shot music-library scanner. Produces ``music_library.json`` inside
the target folder, which tier C of ``music_pick.py`` consumes.

Uses librosa to extract per-track tempo (BPM), RMS energy, spectral centroid
(brightness proxy), and duration. This is only worth running if you have more
than ~20 tracks and want richer matching — for small libraries the folder
convention of tier A is already sufficient.

Invocation (usually from the dashboard admin panel):

    python3 scan_music.py --library /path/to/music

The scanner is idempotent: rerunning it overwrites the sidecar. Missing
librosa degrades gracefully (the scanner reports and exits non-zero).
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".ogg", ".flac"}


def analyze_track(path: Path) -> dict | None:
    """Run librosa feature extraction on a single track. Returns a dict of
    normalized features, or None if the file cannot be read."""
    try:
        import librosa
        import numpy as np
    except ImportError:
        print("librosa is not installed. Install with: pip install librosa soundfile",
              file=sys.stderr)
        raise

    try:
        # Load at reduced sample rate — features are fine at 22050, and
        # reading at full 44.1 kHz doubles the scan time for no gain.
        y, sr = librosa.load(str(path), sr=22050, mono=True)
        if y.size == 0:
            return None

        duration = float(len(y) / sr)

        # Tempo
        try:
            tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
            tempo = float(tempo) if tempo is not None else 0.0
        except Exception:
            tempo = 0.0

        # Energy (RMS) — normalized roughly into 0..1 assuming pop music loudness.
        rms = librosa.feature.rms(y=y).mean()
        energy_rms = float(min(rms * 6.0, 1.0))

        # Brightness proxy: spectral centroid normalized against Nyquist.
        centroid = librosa.feature.spectral_centroid(y=y, sr=sr).mean()
        brightness = float(min(centroid / (sr / 2.0), 1.0))

        return {
            "path": path.name if path.parent == Path("") else str(path),
            "duration_s": round(duration, 1),
            "tempo": round(tempo, 1),
            "energy_rms": round(energy_rms, 3),
            "brightness": round(brightness, 3),
        }
    except Exception as e:
        print(f"  skipped {path.name}: {e}", file=sys.stderr)
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--library", required=True)
    args = parser.parse_args()

    root = Path(args.library)
    if not root.is_dir():
        print(f"library not found: {root}", file=sys.stderr)
        return 1

    tracks: list[dict] = []
    started = time.time()
    count = 0
    for p in sorted(root.rglob("*")):
        if p.suffix.lower() not in AUDIO_EXTS:
            continue
        count += 1
        rel = p.relative_to(root)
        print(f"[{count}] {rel}", file=sys.stderr)
        feat = analyze_track(p)
        if feat is None:
            continue
        feat["path"] = str(rel).replace("\\", "/")
        tracks.append(feat)

    elapsed = time.time() - started
    sidecar = {
        "generated_at": int(time.time()),
        "tracks": tracks,
        "count": len(tracks),
        "scan_seconds": round(elapsed, 1),
    }
    out = root / "music_library.json"
    out.write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
    print(f"\nScanned {len(tracks)} track(s) in {elapsed:.1f}s → {out}",
          file=sys.stderr)
    print(json.dumps({"count": len(tracks), "sidecar": str(out)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
