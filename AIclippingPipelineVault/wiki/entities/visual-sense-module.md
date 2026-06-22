---
title: "visual_sense.py — motion + caption OCR (clip-forensics Phase 3)"
type: entity
tags: [visual, motion, ocr, easyocr, opencv, forensics, module, phase3, reference]
sources: 0
updated: 2026-06-21
---

# `scripts/lib/visual_sense.py`

The visual half of clip-forensics "essence" ([[concepts/plan-clip-forensics]] Phase 3): the things [[concepts/model-senses]] flagged the pipeline can't perceive — motion and burned-in caption text. Sibling to [[entities/audio-sense-module]]; same conventions (lazy import, **failure-soft**, CPU-friendly, offline research lane).

## API
- `motion_events(media, *, sample_fps=8, width=160, z=3.0, ...) -> [{t, kind:"motion", energy, rel}]` — camera/edit motion punches (zoom, shake, fast action). Samples frames, downscales to grayscale, takes mean abs frame-diff, emits points above a **robust threshold** (median + z·1.4826·MAD). **No model, no download** — cv2 is already present. A spike on a `cut` = hard transition; off a cut = camera motion. **Default ON.**
- `caption_ocr(media, *, sample_fps=2, max_frames=120, gpu=False, min_conf=0.4, band=0) -> {available, n_text_frames, total_words, words_per_s, samples[]}` — burned-in caption OCR via **EasyOCR**. Returns a **words/sec** estimate (informs the 5–10 wps anchor in [[concepts/captions]] / [[concepts/hook-engineering-2026-06]]). **OPT-IN** (downloads ~75 MB on first use, slower) — the CLI gates it behind `--ocr`. CPU by default.

## Install (EasyOCR — protect the rig's torch)
EasyOCR pulls the torch ecosystem; this rig's CUDA pipeline depends on `torch 2.9.1+cu130` + `torchvision 0.24.1+cu130` (**already present**). Install it **`--no-deps`** so pip can't re-resolve/downgrade torch, then add only the missing pure-Python deps:
```bash
pip install --no-deps easyocr
pip install scikit-image shapely pyclipper python-bidi   # tifffile pulled by skimage
```
Verified: `torch` unchanged (2.9.1+cu130 before & after). Detector/recogniser weights (`craft_mlt_25k.pth` ~80 MB + `english_g2.pth` ~15 MB) download to `~/.EasyOCR/` on the first `easyocr.Reader(['en'], gpu=False)`.

## Verified (2026-06-21) — on `ReemKnocksClip.MP4`
- **motion_events:** 9 spikes at ~6–7× median energy (the chaotic 1.6–2.4 s and 5.9–6.3 s moments). <2 s, no model.
- **caption_ocr:** `available:True`, 36 text frames, 232 words, **13.08 words/sec**; real burned-in text recovered — e.g. *"boy has to be stopped TikTok @reemknocks"* (some OCR noise on motion-blurred frames). 13 wps is above the 5–10 wps anchor → a real "dense captions" signal.

## Watchdog (why a run can't hang for hours)
Every heavy stage in [clip_forensics.py](scripts/research/clip_forensics.py) now runs under `_with_deadline` — a daemon worker with a hard wall-clock cap (`_STAGE_DEADLINES`: audio_sense 600 s, transcribe 300 s, onset 60 s, scenedetect 180 s, motion 180 s, caption_ocr 600 s, style_profile 150 s; scale with `--deadline-scale`). On overrun the worker is **abandoned** (it dies with the process — the only way to bound C-extension hangs like the old PANNs/librosa stalls, which ignore Python signals) and the stage returns its default so the run finishes with a **partial result**. Per-stage status (`ok|timeout|error|skipped`) is recorded in the output `_stages` block and a `WARNING` is logged for any timeout/error. **Total runtime ≤ sum of caps.** Unit-verified: a 10 s hang under a 0.5 s cap returns in 0.50 s.

## Related
- [[entities/audio-sense-module]] · [[concepts/plan-clip-forensics]] · [[concepts/clip-forensics-research-2026-06]] · [[concepts/captions]] · [[concepts/model-senses]]
