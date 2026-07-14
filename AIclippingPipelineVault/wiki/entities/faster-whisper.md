---
title: "faster-whisper large-v3"
type: entity
tags: [model, transcription, whisper, whisperx, speech-to-text, gpu, cuda, infrastructure, stage-2, stage-7, audio, text]
sources: 2
updated: 2026-07-14
---

# faster-whisper large-v3

OpenAI's Whisper speech recognition model running through the `faster-whisper` engine — a CTranslate2 reimplementation that runs 4–6x faster than the original OpenAI implementation with identical accuracy.

Default model (2026-06-04): **`large-v3-turbo`** — a distilled large-v3 (decoder pruned 32→4 layers, encoder unchanged; ~809M params, ~1.6 GB) that runs **~2.5x faster for <1% WER loss**. **`large-v3`** stays selectable as the accuracy ceiling for noisy / accented / overlapping-speech audio. Runs **natively** (bare-metal Windows venv) on **GPU (float16) when available**, auto-falling back to CPU (int8).

> [!note] Corrected: not CPU-only
> Earlier documentation incorrectly stated Whisper always runs on CPU. In practice, the pipeline explicitly unloads all Ollama models from VRAM before Stage 2 so Whisper can claim the full GPU. Whisper uses ~6–7GB VRAM at float16.

---

## How transcription works on bare-metal Windows (current)

> [!note] Authoritative, current spec
> As of the [[concepts/bare-metal-windows]] migration (2026-06-04) transcription
> runs natively in the project venv (no Docker). The sections further down
> (20-minute chunks, Docker image pre-bake, Ollama) describe the **legacy
> faster-whisper-only** path and are kept for history.

> [!warning] Interpreter roulette silently disables WhisperX (found 2026-07-14)
> Every fresh transcription in the 20260713 9-VOD batch logged `whisperx package not
> available; falling back to faster-whisper` — yet `import whisperx` works FINE in the
> repo `.venv`. Cause: the dashboard spawns the pipeline with `sys.executable`
> (`pipeline_routes.py:120/190`), so WHICH interpreter chain launched the dashboard
> decides the speech backend. A dashboard started with the bare system python
> (`C:\Program Files\Python312` — has faster-whisper but NOT whisperx) ran the whole
> batch on the fallback: **Whisper-attention word timestamps (±0.2–0.5 s drift) instead
> of WhisperX's wav2vec2 forced alignment (~±30–60 ms)** — quality that captions, SFX
> beat anchoring, and jump-cut quote mapping all inherit. A `.venv`-launched dashboard
> (the current venvlauncher chain) uses WhisperX. Fix candidate (not yet built): pin the
> pipeline command to the repo venv interpreter instead of `sys.executable`. Note the
> §10-of-speed-findings S2 rate (4.1 min/VOD-h) was measured on the FALLBACK path — the
> WhisperX path (VAD-batched ASR + alignment pass) needs its own measured run.

### Two backends, one module ([[entities/speech-module]] / `scripts/lib/speech.py`)

Stage 2 calls `speech.py`, which picks a backend from `config/speech.json::backend`
(default `whisperx`) and **auto-falls back** on import/runtime failure:

1. **WhisperX (primary)** — VAD-based chunking (silero/pyannote VAD finds real
   speech boundaries, replacing the old arbitrary 20-minute splits) → **batched
   faster-whisper** ASR → **wav2vec2 forced alignment** → frame-accurate
   word-level timestamps. Optional **pyannote diarization** (Tier-2 M1) when
   `HF_TOKEN` is set + `pyannote-audio` installed + enabled in config.
2. **faster-whisper (fallback)** — the pre-Phase-3 CTranslate2 path, preserved
   verbatim. Fires when WhisperX isn't importable or raises mid-run. No diarization.

Fallback ladder: WhisperX → faster-whisper(GPU) → faster-whisper(CPU int8) →
hard fail (`transcription_failed`). See [[concepts/speech-pipeline]].

### Two runtimes (this is the key bare-metal detail)

| Concern | Runtime | Needs |
|---|---|---|
| ASR transcription (both backends) | **CTranslate2** (faster-whisper) | cuDNN 9 + cuBLAS for CUDA 12 |
| WhisperX VAD + wav2vec2 alignment + pyannote | **PyTorch** | torch CUDA build |

CTranslate2 is **independent of torch** — that's why `scripts/validate_gpu.py`
confirmed GPU transcription *before* torch was even installed. Practical
consequences on Windows:

- **cuDNN/cuBLAS DLLs** must be on the Windows DLL search path for CTranslate2.
  Handled by `scripts/lib/cuda_bootstrap.py` (`os.add_dll_directory` over the
  venv's `nvidia/*/bin`) imported by `speech.py` + `stage7_transcribe.py`, and by
  `paths.child_env()` putting those dirs on `PATH`. Without this you get
  `Could not locate cudnn_ops64_9.dll`-style errors.
- **torch must be the CUDA build**. The venv uses **torch 2.8.0+cu128** (the
  version whisperx/pyannote pin, CUDA 12.8 supports the RTX 5060 Ti / Blackwell
  sm_120). ⚠️ Installing `whisperx` pulls **CPU** torch from PyPI and clobbers
  CUDA — reinstall `torch==2.8.0+cu128` from the cu128 index afterward. If torch
  ends up CPU-only, ASR still runs on GPU (CTranslate2) but WhisperX alignment
  falls back to CPU (slower), or speech.py degrades to the faster-whisper backend.

### Device / model / cache resolution

- **Device & compute**: `config/speech.json::device`/`compute_type` default
  `auto` → `cuda`+`float16` when `torch.cuda.is_available()`, else `cpu`+`int8`.
  Env `CLIP_WHISPER_DEVICE` / `CLIP_WHISPER_COMPUTE` override.
- **Model**: **`large-v3-turbo`** by default (from `config/models.json::whisper_model`
  → `CLIP_WHISPER_MODEL`, exported by the orchestrator; this overrides speech.py's
  own `cfg["model"]`, which also defaults to turbo). Selectable sizes in the dashboard
  dropdown (`dashboard/_state.py::WHISPER_MODELS`): **large-v3-turbo, large-v3,
  large-v2, medium, small, base, tiny**. faster-whisper 1.2.1 resolves
  `large-v3-turbo`/`turbo` → `mobiuslabsgmbh/faster-whisper-large-v3-turbo`.
- **Model weights cache**: `WHISPER_MODEL_DIR` → **`<repo>\models\whisper`**
  (HuggingFace layout, e.g. `models--mobiuslabsgmbh--faster-whisper-large-v3-turbo`
  ~1.6 GB and `models--Systran--faster-whisper-large-v3` ~3 GB). Sizes download on
  first use; turbo + large-v3 are pre-cached in this repo. **Windows symlink fix**:
  HF Hub's snapshot→blob symlinks raise `WinError 1314` ("required privilege not
  held") without admin / Developer Mode, so `paths.child_env()` sets
  `HF_HUB_DISABLE_SYMLINKS=1` (copy mode); without it a first-use download of an
  uncached size hard-fails mid-download (BUG 59). (Replaces the Docker image pre-bake.)

### Output

`transcript.json` — list of `{start, end, text}` (seconds); with alignment each
segment also carries `words: [{word, start, end}]` (and `speaker` when diarized).
`transcript.srt` — SubRip for FFmpeg subtitle burn-in. **Streamer-prompt biasing**:
`config/streamer_prompts.json` matches the VOD filename to an `initial_prompt`
that nudges decoding toward channel-specific game/emote/jargon vocabulary.

### Transcription cache (skips Stage 2 on re-clips)

Full-VOD results are cached to **`<repo>\vods\.transcriptions\<stem>.transcript.{json,srt}`**.
If both exist, Stage 2 copies them into the work dir and skips transcription
entirely (the dashboard's VOD list shows `transcription_cached`). `--force` or an
explicit `--vod` re-runs it. All 5 sample VODs in this repo are already cached.

### Stage 7 captions (separate, faster-whisper directly)

Clip-level subtitles are produced by `scripts/lib/stages/stage7_transcribe.py`,
which loads **faster-whisper** (not WhisperX) **once** for all clips and writes a
`clip_<T>.srt` per clip. It reads `CLIP_WHISPER_MODEL` / `CLIP_WHISPER_DEVICE`
/ `CLIP_WHISPER_COMPUTE` and uses the same `WHISPER_MODEL_DIR` cache + cuDNN DLL
bootstrap.

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
