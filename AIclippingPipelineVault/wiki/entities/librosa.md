---
title: "librosa (tier-C music matching)"
type: entity
tags: [music, librosa, features, tier-c, originality, wave-d, infrastructure, audio, stage-7]
sources: 0
updated: 2026-04-22
---

# librosa

Audio feature extraction library used *optionally* by [[concepts/originality-stack]] wave D to power tier-C music-bed matching. Without librosa the pipeline falls back to the tier-A folder-convention picker (no features, deterministic random pick from a category subfolder).

### When to enable

Turn on the **Tier C music matching** checkbox in the [[entities/dashboard]] Originality panel only if your `CLIP_MUSIC_BED` folder has more than ~20 tracks and you want per-clip fit to improve. Below that, tier A is faster and equally good.

### Scanner

One-shot tool: `scripts/lib/scan_music.py --library <folder>`. Called from the dashboard's **Scan Music** button, which posts `library` to `POST /api/music/scan`. Writes `<library>/music_library.json` with these per-track fields:

| Field | Meaning | Range |
|---|---|---|
| `tempo` | Beat-tracked BPM | 60–200 typical |
| `energy_rms` | Mean RMS, scaled | 0.0–1.0 |
| `brightness` | Spectral centroid / Nyquist | 0.0–1.0 |
| `duration_s` | Track length | seconds |

Loads at 22050 Hz mono to halve the scan time. Idempotent — rerunning overwrites the sidecar.

### Picker scoring

`scripts/lib/music_pick.py` compares each track against a per-category target profile:

```
hype:       energy 0.80  tempo 140  brightness 0.70
storytime:  energy 0.30  tempo  85  brightness 0.40
emotional:  energy 0.20  tempo  72  brightness 0.30
...
```

Distance is a weighted sum of `|delta_tempo|/80 + 2·|delta_energy| + 1.5·|delta_brightness| + loop_penalty`. Ties are broken by a small seeded jitter and a final random pick from the top 3 closest tracks.

### Install

Added to the [[entities/dockerfile]] as `pip install librosa soundfile`. CPU-only — no GPU usage.

### Related
- [[concepts/originality-stack]] — wave D
- [[entities/dashboard]] — Originality panel, Scan Music button
