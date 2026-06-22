---
title: "audio_sense.py — semantic audio sensing (CLAP + PANNs) + installed models"
type: entity
tags: [audio, clap, panns, faster-whisper, forensics, models, install, module, reference]
sources: 0
updated: 2026-06-13
---

# `scripts/lib/audio_sense.py`

The shared semantic audio-sensing layer for clip-forensics ([[concepts/clip-forensics-research-2026-06]], [[concepts/plan-clip-forensics]]). Replaces the three librosa DSP dials in [[entities/audio-events]] with models that can NAME sounds. Offline-first, CPU-default, lazy-import, failure-soft.

## API
- `sense_events(media, *, window_s=1.0, hop_s=0.5, device=None, cache_path=None) -> [{t,end,label,score,source}]` — CLAP zero-shot (open vocab) + PANNs framewise (opt-in), merged + deduped.
- `transcribe_words(media, *, model_size=None, cache_path=None) -> [{word,start,end}]` — faster-whisper word timings (censor detection).
- `onset_times(media) -> [float]` — pure-numpy energy-flux onsets (abrupt music-start flag).
- `music_segments(media)` — inaSpeechSegmenter (optional; not installed by default).

## Installed models (2026-06-13, this rig — RTX 5060 Ti, win32, base Python 3.12)

| Model | Role | On disk | Size | License |
|---|---|---|---|---|
| **CLAP** `laion/clap-htsat-unfused` (transformers `ClapModel`) | zero-shot open-vocab audio events (the default backend) | `~/.cache/huggingface/hub/models--laion--clap-htsat-unfused` | ~1.2 GB | Apache-2.0 (commercial-OK) |
| **faster-whisper `base`** (`Systran/faster-whisper-base`) | word timings for censor detection | `~/.cache/huggingface/hub/models--Systran--faster-whisper-base` | ~142 MB | MIT |
| **PANNs CNN14 SED** `Cnn14_DecisionLevelMax.pth` | AudioSet framewise tagger (OPT-IN — see caveat) | `~/panns_data/Cnn14_DecisionLevelMax.pth` (+ `class_labels_indices.csv`) | 327 MB | MIT |

## How they were installed (reproduce)

```bash
# 1) pip deps (offline lane; commercial-safe). scenedetect installed --no-deps to reuse existing cv2.
pip install --no-deps scenedetect && pip install click platformdirs tqdm
pip install transformers faster-whisper better-profanity panns-inference torchlibrosa

# 2) PANNs needs its files PRE-PLACED — panns_inference shells out to `wget` (absent on
#    Windows) on first use, so fetch via Python instead:
python -c "import urllib.request,os,pathlib; d=pathlib.Path(os.path.expanduser('~'))/'panns_data'; d.mkdir(exist_ok=True); \
urllib.request.urlretrieve('https://raw.githubusercontent.com/qiuqiangkong/audioset_tagging_cnn/master/metadata/class_labels_indices.csv', d/'class_labels_indices.csv'); \
urllib.request.urlretrieve('https://zenodo.org/record/3987831/files/Cnn14_DecisionLevelMax_mAP%3D0.385.pth?download=1', d/'Cnn14_DecisionLevelMax.pth')"

# 3) CLAP + faster-whisper download to the HF cache on first model construction:
python -c "from transformers import ClapModel,ClapProcessor; ClapModel.from_pretrained('laion/clap-htsat-unfused'); ClapProcessor.from_pretrained('laion/clap-htsat-unfused')"
python -c "from faster_whisper import WhisperModel; WhisperModel('base', device='cpu', compute_type='int8')"
```

## Environment caveats discovered (2026-06-21) — these drive the defaults

> [!warning] PANNs stalls on torch ≥ 2.9 → opt-in only
> `panns_inference 0.1.1` + `torchlibrosa` **deadlocks during `SoundEventDetection.__init__`** on this rig's `torch 2.9.1+cu130` — on **both CUDA and CPU**, even with OpenMP guards. A stall isn't catchable by `try/except`, so `_panns_events` is **gated behind `CLIP_AUDIO_SENSE_PANNS=1` (default OFF)**. CLAP covers the common classes (music/laughter/applause/beep/quack) via prompts, so the default stack is CLAP-only and works. Re-enable PANNs on a saner torch build (the pipeline's own `.venv` may differ from base Python).

> [!warning] CPU default for the offline tool
> CLAP/whisper run on **CPU by default** in `clip_forensics.py` (torch checkpoint loads to CUDA hung on this rig — the live pipeline works around Windows CUDA/cuDNN with a DLL bootstrap the standalone tool lacks). `--cuda` opts in. CLAP on CPU is ~0.25 s/window (fast enough offline); faster-whisper base on CPU (int8) is a few seconds for a short clip.

> [!warning] OpenMP guard + librosa onset replaced
> Run with `KMP_DUPLICATE_LIB_OK=TRUE` to avoid duplicate-OpenMP issues. `librosa`'s onset detector **hung** here (numba/peak_pick), so `onset_times()` was rewritten as a **pure-numpy energy-flux peak picker** (no librosa/numba). HF symlink warnings on Windows are benign.

> [!note] CLAP thresholds are LOW + uncalibrated → now PER-LABEL
> Raw CLAP audio↔text cosines top out ~0.26–0.32 for foreground SFX; `clap_threshold` default is **0.30**. But a **background music bed under speech scores even lower** — verified on a real clip, the suspense bed peaked at **0.267 (under 0.30) yet was clearly present**, because speech dominates the window. So `config/audio_sense_labels.json` now supports an **optional per-label `threshold`** (falls back to `clap_threshold`): **music 0.18, suspense_music 0.20**. Speech-only windows score *negative* on the music prompt, so the lower floor is safe; a **sustained-run gate** in `clip_forensics._music_bed` (≥1.5 s / ≥2 windows) suppresses lone-blip false positives. Calibrate per-corpus against `reference_clips/*.notes.json`. Essentia (mood) and Demucs weights are deliberately NOT used (license — see [[concepts/clip-forensics-research-2026-06]]).

> [!warning] TikTok download outro pollutes analysis — trim it
> Clips downloaded from TikTok carry a ~3 s auto-appended **outro** (TikTok logo + creator @handle). Its whoosh/logo animation and persistent @handle caption get mis-logged as real editing cues. Use `clip_forensics.py --trim-end 4` (or `CLIP_FORENSICS_TRIM_END` for a batch) to drop it; the tool window-filters every signal to `[start, dur−end]` before deriving music/censor.

## Verified (2026-06-21)
Real run on `reference_clips/ReemKnocksClip.MP4` (17.7 s): CLAP → **14 events** (`bruh` cluster 1–7.5 s, `boing`/`whoosh` 14–16.5 s); faster-whisper → **18 words**; scenedetect → **6 cuts**; full timeline written. Phase-2 censor + music-bed detector logic unit-verified (word+SFX, bleeped-gap, music added/not-added).

## Related
- [[concepts/plan-clip-forensics]] · [[concepts/clip-forensics-research-2026-06]] · [[entities/audio-events]] · [[concepts/model-senses]]
