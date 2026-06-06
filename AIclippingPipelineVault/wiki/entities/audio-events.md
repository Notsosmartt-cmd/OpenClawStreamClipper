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

The scanner loads the audio file ONCE up-front and slices in-memory per window. Per-window cost is dominated by `librosa.effects.hpss()` (STFT + harmonic/percussive masking).

| VOD length | Load time | Serial scan (pre-2026-06-04) | Parallel scan (8 workers, post-2026-06-04) |
|---|---|---|---|
| 1 hour | ~3 s | ~5-7 min | **~1 min** |
| 3 hours | ~10 s | ~20-25 min | **~3-4 min** |
| 6 hours | ~20 s | ~40-50 min | **~6-8 min** |

> [!warning] Measured 2026-06-04 (pre-parallelization): ~0.8 win/s on i9-13900K
> Despite the in-memory load, the per-window scan rate measured **~0.8 windows/sec** on the i9-13900K — `librosa.effects.hpss()` STFT + median-filter dominates. CPU usage looked invisibly low because the scanner was **single-threaded** (1 core out of 24 = ~4% of total, often unobservable in Task Manager); GPU usage is zero (librosa is pure NumPy/SciPy, no CUDA). A 3.2 h VOD took ~25 min for this stage alone.

> [!success] RMS-gate early-exit implemented 2026-06-04 (round 2)
> Added a cheap energy check (one numpy reduction, ~50 µs) before invoking the expensive HPSS detector. Silent windows (RMS < 0.01) early-exit with a zero result, skipping the ~700 ms HPSS cost entirely. **Knobs**: `AUDIO_EVENTS_RMS_GATE` env var; default 0.01; set to 0 to disable. **Expected**: 1.5-3× on top of multiprocessing, varying with the share of silence in the VOD. **Risk**: low — the gate is conservative (normal speech ≈ 0.05-0.15). Tunable if false negatives appear.

> [!success] Audio-load fast path 2026-06-05 — `soundfile.read` + polyphase resample
> Same run that surfaced the BLAS-pin issue also measured **53 s** for the `librosa.load(audio_path, sr=22050, mono=True)` startup on a 3.2 h 16 kHz WAV. The dominant cost is librosa's default `res_type="kaiser_best"` resampler (high quality, but ~5-7× slower than `scipy.signal.resample_poly`). For HPSS / onset / RMS detection the quality difference is inaudible. Fix: `_load_audio_fast()` now does `soundfile.read(dtype="float32")` → optional mono mix-down → `librosa.resample(res_type="polyphase")`. Wrapped in try/except so any non-OOM failure falls back to the original `librosa.load`. The `[AUDIO_EVENTS] loaded …` log line now reports `method=soundfile+polyphase` or `method=librosa.load` so the operator can confirm which path ran. Expected: **53 s → ~7-10 s** on the same input.

> [!success] BLAS-thread pinning fix 2026-06-05 — closes the under-scaling gap
> First production run with the multiprocessing path (2026-06-05 16:08-16:19) measured **1.7 win/s** with 8 workers — only **2.1× over the 0.8 win/s serial baseline** instead of the predicted 6-8×. Diagnosed as BLAS thread oversubscription: NumPy's BLAS library (used by librosa's HPSS STFT) defaults to ``cpu_count`` threads. With 8 worker processes × ~24 BLAS threads each = **~192 threads competing for 24 cores** — massive context switching kills parallel scaling. Fix: ``_worker_init`` now sets ``OMP_NUM_THREADS``/``OPENBLAS_NUM_THREADS``/``MKL_NUM_THREADS``/``NUMEXPR_NUM_THREADS``/``VECLIB_MAXIMUM_THREADS`` to ``1`` **BEFORE** importing NumPy (env vars are cached on first NumPy BLAS import; later changes ignored). Uses ``setdefault`` so an operator can override via env for a smaller worker count. Expected to bring 8-worker rate up to ~6-8 win/s = the originally predicted 8× speedup. **A 3 h VOD should drop from 11.6 min (observed) to ~2-3 min.**

> [!success] Multiprocessing implemented 2026-06-04 (round 1) — auto-scales to N workers
> `scan_audio_events()` now spawns a `multiprocessing.Pool` with the audio array in `multiprocessing.shared_memory` (zero pickle copies). Each worker imports librosa once and processes windows via `pool.imap(chunksize=4)`. Tunables (module top of `scripts/lib/audio_events.py`):
> - `PARALLEL_MIN_WINDOWS = 20` — below this, serial is faster (worker spawn cost dominates)
> - `PARALLEL_DEFAULT_CAP = 8` — auto-resolved as `min(8, cpu_count - 2)`
> - `PARALLEL_CHUNKSIZE = 4` — amortizes per-task IPC overhead
>
> Worker count resolution order: `n_workers=` argument → `AUDIO_EVENTS_WORKERS` env var → auto. CLI: `--workers N` (0=auto, 1=force serial, ≥2=that many).
>
> Backend field in the JSON output reflects the path actually used (`librosa` for serial, `librosa+mp<N>` for parallel) so downstream consumers can audit which run did what. Window output is byte-identical between paths — the same `_run_detectors` is called in both.
>
> Failure-soft: if shared-memory creation, pool spawn, or worker librosa import fails, the parallel path returns `(empty, fail-flag)` and the serial loop runs as fallback with `y_full` still in scope.

> [!warning] Pre-2026-04-28: ``librosa.load()`` per window
> The original implementation called ``librosa.load(audio_path, offset=t, duration=window_size)`` once per window, re-opening the audio file ~1160 times for a 3-hour VOD. This hung the pipeline at "Tier-2 M2: scanning audio events..." for many minutes with no visible progress. Fixed by loading the file once and slicing the in-memory waveform; the bash invocation also switched to ``python3 -u`` + ``2> >(tee ...)`` so progress logs flow in real time.

Memory budget: a 4-hour 22050 Hz mono float32 buffer is ~1.3 GB. On VODs >~6 hours the loader catches `MemoryError` and writes an empty events file so the pipeline degrades cleanly.

---

## Skipped reasons (observed 2026-06-04)

| `skipped_reason` | Trigger | Recovery |
|---|---|---|
| `librosa_missing` | librosa not importable (slim image build) | Install librosa or accept Pass A runs unboosted |
| `zero_duration` | `librosa.get_duration()` returned 0 | Verify the audio file is valid |
| `load_oom` | MemoryError loading full audio | VOD too long for in-memory scan (>~6 h); chunk into halves manually |
| `load_failed` | Generic librosa load exception | Check audio codec / file integrity |
| **`no_audio_source`** | **Audio file path missing or empty** — typical on cached-transcript re-runs where the audio.wav was deleted post-transcription | **Re-extract audio before audio_events when transcript is cached but events JSON is missing (see [[concepts/case-rap-battle-missed]] §Diagnosis 4)** |

The `no_audio_source` case is a **silent miss**: cached transcription re-runs reach Pass A without any Tier-2 M2 boosts, and Pass A's freestyle/dance/crowd-reaction detection regresses to keyword-only. This was the rakai 2026-04-24 run's situation per [[concepts/case-rap-battle-missed]].

---

## Related

- [[concepts/highlight-detection]] — Pass A receives the boost
- [[entities/librosa]] — feature extraction backend
- [[concepts/clipping-pipeline]] — pipeline ordering (between Stage 2 and Stage 3)
- [[concepts/moment-discovery-upgrades]] — Tier-2 hub
- [[entities/diarization]] — sibling Tier-2 module (M1, speaker labels)
- [[entities/callback-module]] — sibling Tier-2 module (M3, long-range callbacks)
