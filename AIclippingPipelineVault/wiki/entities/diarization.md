---
title: "Speaker diarization (Tier-2 M1)"
type: entity
tags: [diarization, whisperx, pyannote, hf-token, pass-a, pass-c, tier-2, m1, module, stage-2, stage-4, audio, signals]
sources: 1
updated: 2026-04-27
---

# Speaker diarization

WhisperX + pyannote-audio integration that assigns a `speaker` label (e.g. `SPEAKER_00`, `SPEAKER_01`) to every Whisper segment after alignment. Lets the pipeline distinguish a 60 s solo monologue from 60 s of streamer + friend banter — different content profiles that previously looked identical to Pass A keyword scanning.

Introduced 2026-04-27 as Tier-2 M1 of the [[concepts/moment-discovery-upgrades]]. Lives inside [[entities/speech-module]] (`_maybe_diarize()` helper); not a separate file because it's a thin WhisperX wrapper.

---

## Activation

Requires three things to be true; falls through unchanged when any is missing:

1. `HF_TOKEN` (or `HUGGING_FACE_HUB_TOKEN`) env var is set
2. The token has access granted to `pyannote/speaker-diarization-3.1` on Hugging Face
3. WhisperX exposes `DiarizationPipeline` (or `whisperx.diarize.DiarizationPipeline` on newer releases) with `pyannote-audio` installed

When skipped, `[SPEECH] M1: HF_TOKEN unset; skipping diarization` (or a similar message) is logged and the rest of Stage 2 proceeds without speaker labels.

---

## Config

`config/speech.json::diarization` (defaults shown):

```json
{
  "diarization": {
    "enabled": true,
    "model": "pyannote/speaker-diarization-3.1",
    "min_speakers": null,
    "max_speakers": null
  }
}
```

`min_speakers` / `max_speakers` are optional hints passed to pyannote when set; unset = let pyannote decide.

---

## Output shape

Each Whisper segment in `transcript.json` may now carry a `speaker` field:

```json
{
  "start": 14.21, "end": 18.05,
  "text": "this is my penthouse",
  "speaker": "SPEAKER_00",
  "words": [{"word": "this", "start": 14.21, "end": 14.36}, ...]
}
```

Segments without a confident assignment simply omit the field.

---

## How signals propagate

| Stage | Use of `speaker` |
|---|---|
| Pass A (`keyword_scan`) | counts distinct speakers in each 30 s window. When `speaker_count ≥ 2` AND `dominant_speaker_share < 0.7`, fires +1 signal in `funny` and `controversial`. Records `dominant_speaker` / `speaker_count` / `dominant_speaker_share` on the moment. |
| Pass B post-parse | annotates each LLM moment with the same speaker fields from its ±15 s payoff window. |
| Pass C ranking | multiplicative ×1.15 boost to any moment with `speaker_count ≥ 2 and dominant_speaker_share < 0.7`. Smaller than the cross-validation 1.20× so a true keyword+LLM agreement still outranks. |

All uses are boost-only. Diarization mistakes (mis-merged similar voices, missed brief interjections) only nudge scores; they never gate moments.

---

## Cost

- Wall time: pyannote diarization is CPU-bound; WhisperX runs it after alignment, adding roughly 25-30 % to Stage 2 wall time on a typical VOD.
- VRAM: zero (CPU pipeline).
- New dependency: `pyannote-audio` (~150 MB model + transitive Torch bits).

---

## Failure modes

- HF token missing or revoked → graceful skip with stderr log; transcript has no `speaker` fields; Pass A/C boosts disable.
- pyannote import error → graceful skip.
- Per-VOD diarization runtime error → graceful skip; transcript still written.

---

## Related

- [[entities/speech-module]] — host module
- [[concepts/speech-pipeline]] — Stage 2 architecture
- [[concepts/highlight-detection]] — Pass A speaker signals + Pass C boost
- [[concepts/moment-discovery-upgrades]] — original spec (Tier 2 M1)
- [[entities/audio-events]] — sibling Tier-2 module (M2, librosa signals)
- [[entities/callback-module]] — sibling Tier-2 module (M3, long-range callbacks)
