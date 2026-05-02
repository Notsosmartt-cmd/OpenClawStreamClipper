---
title: "audio_events.py — Tier-2 M2 audio-event detector"
type: entity
tags: [audio, librosa, pass-a, tier-2, m2, signals, module, stage-4]
sources: 1
updated: 2026-04-27
---

# `scripts/lib/audio_events.py`

Boost-only audio-feature scanner that surfaces three classes of clip-worthy signal invisible to the Whisper transcript:

| Signal | What it catches | Threshold |
|---|---|---|
| `rhythmic_speech` | freestyles, chants, song delivery — beat-aligned regularity from librosa onset detection | ≥ 0.7 |
| `crowd_response` | sudden RMS spike + laughter/cheer spectrum (high zero-crossing rate) | ≥ 0.5 |
| `music_dominance` | HPSS percussive/total ratio — high = music playing | ≥ 0.6 |

Introduced 2026-04-27 as Tier-2 M2 of the [[concepts/moment-discovery-upgrades]].

---

## Pipeline integration

After Stage 2 (transcription) finishes and before Stage 3, the bash pipeline runs:

```bash
python3 "$LIB_DIR/audio_events.py" --audio "$AUDIO_FILE" --out "$TEMP_DIR/audio_events.json"
```

The resulting JSON has shape `{"windows": [{"start", "end", "rhythmic_speech", "crowd_response", "music_dominance"}], "window_size": 30, "step": 10, "duration": <s>, "backend": "librosa"}`.

Pass A (`keyword_scan`) imports the module to call `load_events()` once and `lookup_window()` per Pass A 30 s / 10 s window. When a signal exceeds its threshold, the matching keyword categories get +1:

| Signal fires | Categories boosted |
|---|---|
| `rhythmic_speech` ≥ 0.7 | `dancing` +1, `hype` +1 |
| `crowd_response` ≥ 0.5 | `funny` +1, `hype` +1 |
| `music_dominance` ≥ 0.6 | `dancing` +1 |

Boost-only by design: a missed signal silently leaves current behavior intact.

---

## Graceful degradation

- `librosa` not importable (image built `ORIGINALITY_STACK=slim`) → scanner writes `{"windows": [], "skipped_reason": "librosa_missing"}`. Pass A loads zero events and runs unchanged.
- Audio file missing (cached re-clip path where `audio.wav` was deleted) → bash skips the scanner and writes the empty file directly.
- Per-window librosa errors fall through to `0.0` for that detector.

---

## Public API

```python
import audio_events
summary = audio_events.scan_audio_events(
    audio_path, out_path,
    window_size=30, step=10, duration_hint=None,
)
events = audio_events.load_events(out_path)
window_events = audio_events.lookup_window(events, window_start=0.0, window_size=30)
```

---

## CLI

```bash
python3 scripts/lib/audio_events.py --audio audio.wav --out events.json [--window 30 --step 10]
```

Prints summary `{"windows": N, "backend": "librosa", "rhythmic_fires": N, ...}` to stdout.

---

## Cost

The scanner loads the audio file ONCE up-front and slices in-memory per window. Per-window cost is dominated by `librosa.effects.hpss()` (STFT + harmonic/percussive masking) at ~200-300 ms per 30 s window on CPU.

| VOD length | Load time | Scan time | Total |
|---|---|---|---|
| 1 hour | ~3 s | ~1.5 min | ~1.5 min |
| 3 hours | ~10 s | ~5 min | ~5 min |
| 6 hours | ~20 s | ~10 min | ~10 min |

> [!warning] Pre-2026-04-28: ``librosa.load()`` per window
> The original implementation called ``librosa.load(audio_path, offset=t, duration=window_size)`` once per window, re-opening the audio file ~1160 times for a 3-hour VOD. This hung the pipeline at "Tier-2 M2: scanning audio events..." for many minutes with no visible progress. Fixed by loading the file once and slicing the in-memory waveform; the bash invocation also switched to ``python3 -u`` + ``2> >(tee ...)`` so progress logs flow in real time.

Memory budget: a 4-hour 22050 Hz mono float32 buffer is ~1.3 GB. On VODs >~6 hours the loader catches `MemoryError` and writes an empty events file so the pipeline degrades cleanly.

---

## Related

- [[concepts/highlight-detection]] — Pass A receives the boost
- [[entities/librosa]] — feature extraction backend
- [[concepts/clipping-pipeline]] — pipeline ordering (between Stage 2 and Stage 3)
- [[concepts/moment-discovery-upgrades]] — Tier-2 hub
- [[entities/diarization]] — sibling Tier-2 module (M1, speaker labels)
- [[entities/callback-module]] — sibling Tier-2 module (M3, long-range callbacks)
