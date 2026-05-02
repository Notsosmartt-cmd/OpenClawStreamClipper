---
title: "chrome_mask.py — UI chrome + overlay OCR (REMOVED 2026-05-01)"
type: entity
tags: [chrome, mog2, paddleocr, phase-4, module, stage-5, vision, removed, tombstone]
sources: 1
updated: 2026-05-01
---

> [!warning] REMOVED 2026-05-01
> The `scripts/lib/chrome_mask.py` module was deleted from the codebase on 2026-05-01 alongside the rest of Phase 4.1 (chrome heredoc, `config/chrome.json`, `requirements-chrome.txt`, the `CHROME_STACK` Dockerfile build arg). Root causes: [[concepts/bugs-and-fixes#BUG 49]] (PaddleOCR wedge truncating the pipeline) and [[concepts/bugs-and-fixes#BUG 50]] (MOG2 frame-spacing mismatch left the detector dead-code).
>
> See [[concepts/chrome-masking]] for the historical design and removal rationale. The original API documentation is preserved below for reference.

---

# `scripts/lib/chrome_mask.py` (historical)

Phase 4.1 UI chrome detection + overlay-text extraction, introduced 2026-04-24. See [[concepts/chrome-masking]] for the full architecture picture.

---

## API

```python
import chrome_mask

cfg = chrome_mask.load_chrome_config()   # /root/.openclaw/chrome.json
result = chrome_mask.process_moment(
    frame_paths=["/tmp/clipper/frames_120_t0.jpg", ...],
    out_dir="/tmp/clipper/masked_120",
    vod_basename="lacy_valorant_2024-10-15.mp4",
    config=cfg,
)
# result = {
#   "bboxes": [(x, y, w, h), ...],
#   "source": "obs_override" | "mog2" | "none",
#   "masked_frame_paths": [path, ...],
#   "overlay_text": "USER gifted 5 subs | Subscribe!",
#   "overlay_text_records": [{"frame": ..., "text": ..., "confidence": ...}],
# }
```

Helpers:
- `load_streamer_override(vod_basename, streamers_dir)` — first `{channel}_chrome.json` whose `channel` matches a substring of `vod_basename`.
- `detect_transient_overlays(frame_paths, cfg)` — MOG2 across the window → `[(x, y, w, h)]`.
- `scale_bboxes(regions, from_res, to_res)` — rescale OBS scene bboxes to the Stage 5 frame resolution.
- `apply_mask(src, out, bboxes, method)` — `method in {"blur", "black", "keep"}`.
- `extract_overlay_text(frame_paths, cfg)` — PaddleOCR on unmasked frames.
- `summarize_overlay_text(records, max_chars=240)` — de-duplicate + join for prompt injection.

CLI: `python3 scripts/lib/chrome_mask.py --frames f1.jpg f2.jpg ... --out-dir /tmp/masked --vod basename.mp4`.

---

## Wire point

`scripts/clip-pipeline.sh` runs a single PYCHROME heredoc between Stage 5 and Stage 6 that calls `process_moment` per moment and writes `/tmp/clipper/chrome_<T>.json`. Stage 6 reads those files per-moment to inject `overlay_text` into the VLM prompt + cascade references.

Masked frames are swapped in place over the Stage 5 originals — Stage 6 doesn't need to know about two sets of frame paths.

---

## Fallback ladder

1. `opencv` not importable → return `source="none"`, copy originals unchanged.
2. OBS override file missing or `channel` mismatch → fall through to MOG2.
3. MOG2 finds no contours above `min_contour_area` → `bboxes=[]`, no mask.
4. MOG2 would mask > `max_masked_area_ratio` of frame → reject, no mask.
5. `paddleocr` not importable → `overlay_text=""`.
6. PaddleOCR runtime error on a frame → skip that frame, continue.

---

## Related

- [[concepts/chrome-masking]] — architectural overview
- [[concepts/vision-enrichment]] — Stage 6 consumer
- [[entities/grounding]] — cascade integration via `refs`
- `config/chrome.json`, `config/streamers/*_chrome.json` — runtime config
