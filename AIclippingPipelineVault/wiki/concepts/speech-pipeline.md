---
title: "Speech Pipeline (Stage 2) — Phase 3"
type: concept
tags: [speech, whisper, whisperx, asr, transcription, vocal-separation, streamer-prompts, phase-3, stage-2, audio, text]
sources: 2
updated: 2026-04-23
---

# Speech Pipeline (Stage 2)

As of 2026-04-23 Phase 3, Stage 2 transcription is a thin shell wrapper around [[entities/speech-module]] — the implementation lives in `scripts/lib/speech.py` rather than an inline heredoc. Swaps the pre-Phase-3 fragile 20-minute chunking for WhisperX's VAD-based chunking + forced alignment, defaults to `large-v3-turbo` (2.5× speedup over `large-v3` with < 1% WER loss), and applies per-channel streamer-slang biasing.

Per `ClippingResearch.md` §8.3: "Path of least resistance is WhisperX (BSD-2), which bundles VAD + batched-faster-whisper + forced-alignment + pyannote diarization as one library — an afternoon of work for ~80% of the gain." That's exactly what Phase 3 ships.

---

## Backend selection

Controlled by `config/speech.json::backend`:

| Backend | When it fires | What you get |
|---|---|---|
| `whisperx` (default) | Package importable AND runtime succeeds | VAD-based chunking → batched faster-whisper → optional wav2vec2 forced alignment. Frame-accurate word timestamps. No 20-minute chunking. |
| `faster-whisper` | Fallback path. Fires when `whisperx` can't be imported (image built with `SPEECH_STACK=slim`) or when WhisperX hits a runtime error mid-transcription. | Legacy pre-Phase-3 code path: ffmpeg splits audio into 20-minute chunks, beam-5 faster-whisper decode per chunk, offset-corrected segment merge. Preserves word-level segments when the model supports `word_timestamps=True`. |

Switching backends is a config change only — no code changes needed.

---

## Data flow

```
Stage 1b chat discovery
        │
        ▼
ffmpeg → 16 kHz mono WAV at /tmp/clipper/audio.wav
        │
        ├─[optional, flag-gated]──▶ vocal_sep.separate()  (Demucs v4 htdemucs_ft)
        │                                  │
        │                                  ▼
        │                         /tmp/clipper/audio_vocals.wav
        │                                  │
        └──────────────────────────────────┤
                                           ▼
                                 speech.transcribe()
                                           │
                      ┌────────────────────┼──────────────────┐
                      │                    │                  │
                      ▼                    ▼                  ▼
           pick_initial_prompt        WhisperX ASR      Fallback: faster-whisper
           (filename-substring          + VAD + batch       (20-min chunks + merge)
           match vs                   + align (wav2vec2)
           streamer_prompts.json)
                                           │
                                           ▼
                             transcript.json {segments, words?}
                             transcript.srt  (SubRip)
```

Caching is unchanged: if `vods/.transcriptions/{basename}.transcript.{json,srt}` already exist, Stage 2 copies them into `/tmp/clipper/` and skips transcription entirely.

---

## Output schema

`transcript.json` is a list of segment records, compatible with every Stage 3+ consumer:

```json
[
  {
    "start": 12.4,
    "end": 16.8,
    "text": "okay guys we just hit ranked three point zero that was insane",
    "words": [
      {"word": "okay", "start": 12.4, "end": 12.68},
      {"word": "guys", "start": 12.68, "end": 13.02}
    ]
  }
]
```

`words` is present when WhisperX alignment succeeds or when the faster-whisper fallback decoded with `word_timestamps=True` and returned per-word data. Consumers that don't need word-level can ignore the field.

`transcript.srt` is the usual SubRip format burned by Stage 7's `-vf subtitles=...` filter.

---

## Streamer-slang biasing (Phase 3.5)

Whisper's `initial_prompt` field (≤ 224 tokens) biases decoding toward rare / channel-specific vocabulary. `speech.pick_initial_prompt(vod_basename)` matches the filename against `config/streamer_prompts.json::channels[*].filename_substrings` (first-match wins, case-insensitive) and returns the matched `initial_prompt`. When no channel matches, the `default_prompt` is used.

Ships with two examples: a generic Valorant prompt (activates on any VOD with `valorant` / `val_` / `vct` in the filename) and a generic League of Legends prompt. Add channel-specific entries by editing the file — changes are picked up on next pipeline run with no rebuild.

---

## Vocal separation (Phase 3.3, opt-in)

`config/speech.json::vocal_separation.enabled = true` turns on pre-transcription vocal-stem extraction via [[entities/vocal-sep-module]] (Demucs v4 `htdemucs_ft`). Adds ~60-120 s per hour of audio on a GPU (much longer on CPU), but drops Whisper's biggest failure mode on music-heavy content: attention burned on non-speech audio, causing missed speech or lyrics hallucinated as dialogue.

Default: **off**. Enable for DJ sets, music-game streams, IRL driving with car music, concerts, or when `faster-whisper` fallback is producing strange output on specific VODs.

---

## Device / compute resolution

| `device` | `compute_type` | Behavior |
|---|---|---|
| `auto` (default) | `auto` (default) | CUDA if `torch.cuda.is_available()`, compute_type = float16; else CPU int8. |
| `cuda` | `float16` / `int8_float16` | Pinned to GPU. |
| `cpu` | `int8` | Pinned to CPU — for debugging or low-VRAM rigs. |

The pre-Phase-3 env vars `CLIP_WHISPER_MODEL` / `CLIP_WHISPER_DEVICE` / `CLIP_WHISPER_COMPUTE` still work — they win over `speech.json` when set. This keeps the dashboard Models panel working without changes.

---

## Performance expectations

| Backend | 2-hour VOD wall time (RTX 5060 Ti) | Word timestamps | Degenerate-loop risk |
|---|---|---|---|
| Pre-Phase-3 (`large-v3` + 20-min chunks) | ~35-45 min | segment-level only | low (mitigated by chunking) |
| WhisperX `large-v3-turbo` + align | **~12-18 min** | frame-accurate per word | none (VAD drops silence) |
| WhisperX `large-v3` + align | ~25-35 min | frame-accurate per word | none |
| `faster-whisper` fallback `large-v3-turbo` | ~15-25 min | word-level when requested | low |

The `large-v3-turbo` model is a free 2.5× speedup over `large-v3` with < 1% WER loss (per `ClippingResearch.md` §8.3). Recommended default.

---

## Graceful degradation

Every layer fails safely:

1. **WhisperX import fails** (e.g. `SPEECH_STACK=slim` build) → automatic fallback to faster-whisper.
2. **WhisperX runtime error** (missing alignment weights, CUDA OOM) → automatic fallback to faster-whisper.
3. **Alignment error only** → keep WhisperX ASR segments, drop word-level data.
4. **Vocal separation fails** → log and proceed with raw audio.
5. **Streamer prompt file missing** → empty prompt, no biasing.
6. **GPU load fails in fallback path** → CPU int8 fallback.

The only scenario where Stage 2 hard-fails is if faster-whisper itself can't be loaded — at which point transcription is impossible regardless of backend choice.

---

## Related

- [[entities/speech-module]] — implementation
- [[entities/vocal-sep-module]] — Demucs wrapper
- [[entities/faster-whisper]] — legacy backend (kept as fallback)
- [[concepts/clipping-pipeline]] — Stage 2 in context
- `IMPLEMENTATION_PLAN.md` — Phase 3 definition; 3.4 (TalkNet-ASD) deferred

---

## What Phase 3 did NOT ship

Per scope decisions in `IMPLEMENTATION_PLAN.md`:

- **3.4 Active-speaker detection** (TalkNet-ASD on webcam crop, AND-gated with audio VAD) — requires face-tracking integration and per-channel webcam-region calibration. Complex; deferred.
- **Parakeet-TDT-0.6B-v3** — NeMo-specific runtime; only worth integrating if WhisperX+turbo becomes throughput-bound.
- **Mel-Band RoFormer** as vocal separator — the Demucs v4 baseline is enough; revisit if users report quality gaps on music-heavy streams.
