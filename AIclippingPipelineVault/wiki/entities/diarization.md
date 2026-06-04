---
title: "Speaker diarization (Tier-2 M1)"
type: entity
tags: [diarization, whisperx, pyannote, hf-token, pass-a, pass-c, tier-2, m1, module, stage-2, stage-4, audio, signals]
sources: 1
updated: 2026-06-04
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

### Setup on bare metal (2026-06-04)

The token lives in the **gitignored `.env`**, so a cloner *without* one runs
normally — diarization just stays off. `run_pipeline.py` calls
`paths.load_dotenv()` at startup, which loads `.env` into the environment so
`speech.py` sees `HF_TOKEN`. To enable:

1. Create a free token (Read scope): https://huggingface.co/settings/tokens
2. Accept the gated-model terms at `pyannote/speaker-diarization-3.1` **and**
   `pyannote/segmentation-3.0`.
3. Put it in `.env`: `HF_TOKEN=hf_...`
4. Re-transcribe (clear the VOD's cache in `vods\.transcriptions\` or run with
   `--force`) — cached transcripts don't gain speaker labels retroactively.

The token only authenticates the **one-time weight download**; inference is 100%
local CPU thereafter. `config/speech.json::diarization.enabled` is `true` by
default, so the token is the only switch.

> [!note] torchcodec / pyannote 4.x
> The diarization call passes **preloaded audio** (`whisperx.load_audio` → ffmpeg)
> into the pipeline, so it doesn't depend on `torchcodec` (removed to keep
> `sentence-transformers` working on torch 2.8). If a pyannote-4.x / whisperx
> version mismatch surfaces at runtime it fails soft (skips) — the pipeline is
> never blocked, so it stays safe to leave enabled.

> [!warning] whisperx 3.8.x token kwarg + classic HF token (found while validating)
> Two gotchas surfaced bringing this live on bare metal:
> - **whisperx 3.8.x / pyannote 4.x renamed `use_auth_token` → `token`.**
>   `_maybe_diarize` now selects the right kwarg via signature inspection, so
>   both old and new whisperx builds work.
> - **Use a classic "Read" HF token, not a fine-grained one.** Fine-grained
>   tokens don't grant gated-repo download access by default → the model fetch
>   401/403s *even though `model_info` reports OK* (`model_info` only reads public
>   metadata; `auth_check` is the authoritative test). You must also click
>   "Agree and access repository" on **all three** gated pyannote models —
>   `speaker-diarization-3.1`, `segmentation-3.0`, **and**
>   `speaker-diarization-community-1` (pyannote v4's 3.1 pipeline pulls the last
>   one for its embedding/PLDA weights; `wespeaker-voxceleb-resnet34-LM` is public).
>
> `scripts/validate_diarization.py [--full]` downloads the weights + runs
> diarization (and optionally the full `speech.transcribe`) on a short sample to
> prove the whole chain end-to-end.

> [!note] Validated working (2026-06-04)
> Confirmed end-to-end via `validate_diarization.py --full`: a 3-minute
> plaqueboymax sample produced **3 speakers** (`SPEAKER_00/01/02`) with labels on
> **77/79** segments, diarization in ~5 s on GPU. To get labels on a real VOD,
> re-transcribe it (`--force` or clear its `.transcriptions` cache) so Stage 2 reruns.

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

- Wall time: with `device=cuda` (default when a GPU is present) diarization is **fast** — ~5 s for a 3-minute sample (measured 2026-06-04), a few percent of Stage 2, not the CPU-bound 25-30 % originally assumed here. CPU-only rigs see the slower figure.
- VRAM: small; shares the GPU with WhisperX and runs after ASR + alignment.
- Gated models (one-time HF download, ~360 MB): `speaker-diarization-3.1`, `segmentation-3.0`, `speaker-diarization-community-1`, plus the public `wespeaker-voxceleb-resnet34-LM` embedding.

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
