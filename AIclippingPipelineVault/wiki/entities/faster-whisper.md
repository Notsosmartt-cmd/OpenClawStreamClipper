---
title: "faster-whisper large-v3"
type: entity
tags: [model, transcription, whisper, speech-to-text, gpu]
sources: 2
updated: 2026-04-07
---

# faster-whisper large-v3

OpenAI's Whisper speech recognition model running through the `faster-whisper` engine — a CTranslate2 reimplementation that runs 4–6x faster than the original OpenAI implementation with identical accuracy.

Model: `large-v3` (not turbo). Runs inside the `stream-clipper` container. Runs on **GPU (float16) when available**, automatically falls back to CPU (int8) if not.

> [!note] Corrected: not CPU-only
> Earlier documentation incorrectly stated Whisper always runs on CPU. In practice, the pipeline explicitly unloads all Ollama models from VRAM before Stage 2 so Whisper can claim the full GPU. Whisper uses ~6–7GB VRAM at float16.

---

## Role in the pipeline

Used in **two stages**:

**Stage 2 — Full VOD transcription:**
- FFmpeg extracts audio to 16kHz mono WAV
- Audio split into **20-minute chunks** before transcription (prevents degenerate looping on long files)
- Each chunk transcribed with beam search 5, word-level timestamps enabled
- Chunks merged with offset-corrected timestamps; degenerate segments filtered during merge
- Output: `transcript.json` + `transcript.srt`
- **Cached** to `vods/.transcriptions/` — re-clips of the same VOD skip this stage entirely

**Stage 7 — Clip-level subtitle generation:**
- Whisper loaded again after unloading the vision model
- All clip audio segments transcribed in a **single model load** (batch mode)
- Produces word-level SRT files for subtitle burn-in by FFmpeg

---

## Why 20-minute chunks?

faster-whisper has a known bug on long audio files: it enters a degenerate loop outputting repetitive dots ("... ... ...") or the word "you" indefinitely. Splitting into 20-minute segments sidesteps this entirely. Results are merged with timestamp offsets applied to each chunk.

---

## Performance

| Setup | 3.5-hour audio |
|---|---|
| RTX 5060 Ti (float16) | ~40–60 minutes |
| CPU-only (int8) | Several hours |

Transcription is the **dominant time cost** in the pipeline. On GPU, a 2-hour VOD typically transcribes in 30–45 minutes.

---

## Model weights

Model weights (~3GB) are **pre-downloaded during Docker image build** and baked into the image layer. No first-run download needed. This is unlike the Ollama-served models which download on first pull.

The Docker image base is `nvidia/cuda:12.3.2-cudnn9-runtime-ubuntu22.04`, which provides the CUDA libraries needed for GPU transcription.

---

## VRAM usage

~6–7GB at float16 on GPU. The pipeline unloads all Ollama models before Stage 2 to make room:

```
Stage 2: Unload ALL Ollama models → Load Whisper (GPU, ~6-7GB) → Transcribe → Whisper exits
Stage 7: Unload qwen3-vl:8b → Load Whisper (GPU, ~6-7GB) → Batch captions → Whisper exits → FFmpeg
```

See [[concepts/vram-budget]].

---

## Transcription cache

Location: `vods/.transcriptions/` (host) = `/root/VODs/.transcriptions/` (container)

If a `.json` cache file exists for a VOD, Stage 2 is skipped entirely. The dashboard shows which VODs have cached transcriptions. Use `--force` or name the VOD explicitly to force re-transcription.

---

## Related
- [[entities/ffmpeg]] — extracts audio for this model to process; burns in subtitles output from this model
- [[concepts/clipping-pipeline]] — Stages 2 and 7
- [[concepts/highlight-detection]] — what happens to the transcript after Stage 2
- [[concepts/vram-budget]] — VRAM orchestration with Ollama models
