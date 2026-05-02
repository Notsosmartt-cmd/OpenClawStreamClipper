---
title: "vocal_sep.py — Demucs vocal-stem separator"
type: entity
tags: [vocal-separation, demucs, music, phase-3, module, stage-2, audio]
sources: 1
updated: 2026-04-23
---

# `scripts/lib/vocal_sep.py`

Optional pre-transcription vocal-stem extractor via Demucs v4 (`htdemucs_ft`). Phase 3.3 of [[sources/implementation-plan]]. Only activates when `config/speech.json::vocal_separation.enabled = true`.

**When to enable**: streams where background music or game audio drowns out the streamer — DJ sets, music-game streams, IRL driving with car radio, concerts. Whisper's attention mechanism can otherwise burn on the music and either miss speech or hallucinate lyrics as dialogue.

**When NOT to enable**: plain gaming, just-chatting, IRL without loud music. Demucs adds ~60-120 s per hour of audio on a 4090, much longer on CPU — unnecessary for content where speech is already clean.

---

## API

```python
import vocal_sep

if vocal_sep.is_available():
    out = vocal_sep.separate(
        audio_path="/tmp/clipper/audio.wav",
        out_path="/tmp/clipper/audio_vocals.wav",
        model="htdemucs_ft",   # or any other demucs pretrained model
    )
    # out == "/tmp/clipper/audio_vocals.wav" on success, None on failure
```

CLI: `python3 scripts/lib/vocal_sep.py --audio in.wav --out vocals.wav`.

---

## How it works

Runs `python3 -m demucs --two-stems=vocals -n <model>` as a subprocess. Demucs writes its output to a nested directory structure (`{work_dir}/{model}/{stem}/vocals.wav`); the wrapper moves the file to the caller's `out_path` and cleans up the scratch dir.

Auto-detects CUDA via `torch.cuda.is_available()` and passes `-d cuda` / `-d cpu` explicitly so it doesn't default to CPU on mixed rigs.

---

## Graceful degradation

- `demucs` not installed (e.g. `SPEECH_STACK=slim`) → `is_available()` returns False; [[entities/speech-module]]'s caller sees `None` and falls back to raw audio.
- Demucs subprocess fails → logs stderr tail and returns None.
- Output file missing from the expected location → recursive glob for `vocals.wav`; if still not found, returns None.

---

## First-run weight download

The `htdemucs_ft` model (~1 GB) downloads from the Facebook AI Demucs S3 bucket on first use — cached under `~/.cache/torch/hub/checkpoints/`. Subsequent runs are local. When you rebuild the container and the cache directory is a volume, the weights persist across rebuilds.

---

## Related

- [[concepts/speech-pipeline]] — architectural overview
- [[entities/speech-module]] — consumer
- `config/speech.json::vocal_separation` — enable/disable + model selection
