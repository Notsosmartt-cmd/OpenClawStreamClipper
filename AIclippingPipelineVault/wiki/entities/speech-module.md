---
title: "speech.py — Stage 2 transcription module"
type: entity
tags: [speech, whisperx, faster-whisper, phase-3, diarization, m1, module, stage-2, audio, text, tier-2, hub]
sources: 1
updated: 2026-04-27
---

# `scripts/lib/speech.py`

Encapsulates all of Stage 2 transcription: backend selection, model loading, VAD chunking (via WhisperX), forced alignment, streamer-prompt biasing, and (optional) vocal-stem separation hand-off. Introduced 2026-04-23 as Phase 3.1 + 3.2 + 3.5 of [[sources/implementation-plan]].

See [[concepts/speech-pipeline]] for the full data-flow picture.

---

## API

```python
import speech

summary = speech.transcribe(
    audio_path="/tmp/clipper/audio.wav",
    out_json="/tmp/clipper/transcript.json",
    out_srt="/tmp/clipper/transcript.srt",
    vod_basename="lacy_valorant_2024-10-15.mp4",  # drives streamer-prompt match
)
# summary = {
#   "duration_min": 118.4, "segments": 2847, "words": 18203,
#   "backend": "whisperx", "language": "en"
# }
```

Internal helpers:
- `load_speech_config(path=None)` — merge `config/speech.json` with defaults and env-var overrides.
- `pick_initial_prompt(vod_basename, path=None)` — filename-substring match against `config/streamer_prompts.json`.
- `resolve_device(cfg)` — `(device, compute_type)` honoring `auto` + CUDA availability.
- `transcribe_whisperx(audio, cfg, prompt)` — WhisperX pipeline (VAD → ASR → align → optional diarize).
- `_maybe_diarize(segments, audio, device, diar_cfg, whisperx)` — Tier-2 M1 helper; runs WhisperX/pyannote diarization and assigns speakers. Falls through unchanged on missing HF_TOKEN, missing pyannote, or runtime error. See [[entities/diarization]].
- `transcribe_faster_whisper(audio, cfg, prompt)` — legacy 20-minute chunked path, preserved verbatim. **Does not diarize.**
- `write_srt(segments, out_path)` — SubRip writer.

CLI: `python3 scripts/lib/speech.py --audio path.wav --out-json t.json --out-srt t.srt --vod basename.mp4`.

---

## Wire point

`scripts/clip-pipeline.sh` Stage 2 no longer contains inline Python. After the ffmpeg audio extraction, Stage 2 just calls:

```bash
python3 "$LIB_DIR/speech.py" \
    --audio    "$AUDIO_FILE" \
    --out-json "$TEMP_DIR/transcript.json" \
    --out-srt  "$TEMP_DIR/transcript.srt" \
    --vod      "$VOD_BASENAME"
```

Summary JSON goes to stdout; the script is expected to leave exit code 0 with `transcript.json` and `transcript.srt` written. Non-zero exit aborts the pipeline with `transcription_failed`.

---

## Env-var overrides (backwards compat)

Pre-Phase-3 code paths (including the dashboard Models panel) set these env vars to pin behavior. Phase 3's module still honors them — they win over `config/speech.json`:

| Env var | Overrides |
|---|---|
| `CLIP_WHISPER_MODEL` | `cfg["model"]` |
| `CLIP_WHISPER_DEVICE` | `cfg["device"]` |
| `CLIP_WHISPER_COMPUTE` | `cfg["compute_type"]` |
| `CLIP_SPEECH_BACKEND` | `cfg["backend"]` |

---

## Fallback ladder

1. WhisperX import succeeds → try WhisperX.
2. WhisperX raises during load/transcribe → fall back to faster-whisper.
3. faster-whisper GPU load fails → retry on CPU int8.
4. faster-whisper CPU load fails → exit non-zero with `transcription_failed`.

Each escalation is logged once with a single `[SPEECH] …` stderr line.

---

## Tier-2 M1 — speaker diarization (2026-04-27)

When `HF_TOKEN` is set + `pyannote-audio` is installed + `config/speech.json::diarization.enabled` is true, Stage 2 adds a `speaker` field per segment in `transcript.json`. Pass A and Pass C use this for speaker-change boost. Full detail: [[entities/diarization]].

---

## Related

- [[concepts/speech-pipeline]] — architectural overview
- [[entities/diarization]] — Tier-2 M1 speaker labeling
- [[entities/vocal-sep-module]] — optional Demucs pre-processing
- [[entities/faster-whisper]] — fallback backend (kept; no diarization)
- `config/speech.json` / `config/streamer_prompts.json` — runtime config
