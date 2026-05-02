---
title: "Piper (local TTS)"
type: entity
tags: [tts, piper, voiceover, originality, wave-d, infrastructure, audio, stage-7]
sources: 0
updated: 2026-04-22
---

# Piper

Local, fast, on-CPU neural text-to-speech used by [[concepts/originality-stack]] wave D for the voiceover layer. Chosen because the TikTok research explicitly flags stock ElevenLabs / CapCut TTS voices as a low-effort detection signal — a locally-generated, lesser-known voice avoids that fingerprint.

- **Model**: `en_US-amy-low` baked into the image at `/root/.cache/piper/`. Small (~20 MB) and naturally neutral.
- **CLI wrapper**: `scripts/lib/piper_vo.py`. Called once per clip with `--text --out --placement --clip-duration --speed --tone`.
- **Placement**: `intro` / `peak` / `outro`. Implemented by prepending silence to the Piper WAV output so the final file is exactly `clip_duration` seconds long — Stage 7's FFmpeg mix needs no per-clip offset math.
- **Mix**: VO at ~2.3× gain, source audio ducked to 0.45 while VO plays. Zero dropout transitions.

### Why Piper over ElevenLabs / CapCut TTS

Research constraint (see [[concepts/originality-stack]]): "*standard ElevenLabs and CapCut TTS voices are now flagged as a low-effort signal*". Piper's voices aren't in TikTok's recognized-TTS corpus, and running locally means zero cloud cost + no API keys.

### Install / update

Inside the container:

```bash
python3 -m piper.download_voices en_US-amy-low --data-dir /root/.cache/piper
```

Change `PIPER_VOICE` env var to swap voices at runtime. `piper_vo.py` falls back to the pip-installed Python API when the CLI binary isn't on PATH.

### Related
- [[concepts/originality-stack]] — wave D
- [[concepts/vision-enrichment]] — `voiceover` prompt field
- [[concepts/clip-rendering]] — audio mix filter graph
