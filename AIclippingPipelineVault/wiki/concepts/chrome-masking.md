---
title: "UI Chrome Masking + Overlay OCR — Phase 4.1 (REMOVED 2026-05-01)"
type: concept
tags: [chrome, mog2, paddleocr, overlay, phase-4, grounding, stage-5, vision, removed, tombstone]
sources: 2
updated: 2026-05-01
---

> [!warning] REMOVED 2026-05-01
> Phase 4.1 (chrome masking + PaddleOCR overlay text) was deleted from the pipeline on 2026-05-01.
>
> - **MOG2** was structurally dead code per [[concepts/bugs-and-fixes#BUG 50]] — Stage 5's `[-2, 0, +1, +2, +3, +5]s` frame layout is too sparse for background subtraction; the `max_masked_area_ratio=0.35` safeguard caught every misfire and returned `[]`.
> - **PaddleOCR** was the wedge source per [[concepts/bugs-and-fixes#BUG 49]] — once-per-VOD, a `predict()` call would hang inside the C++ extension and truncate the pipeline before Stages 6/7/8 ran. The defense layers (SIGALRM 30s + heartbeat + outer `timeout 600`) helped but couldn't fully bound the wedge because C++ extensions can swallow Python signals.
> - **Hard-event grounding** (the actually load-bearing part) is preserved in Pass B + Stage 6 by reading chat sub/bit/raid/donation counts from [[entities/chat-features]] directly. The "gifted subs" hallucination check still works without Phase 4.1.
>
> Files removed: `scripts/lib/chrome_mask.py`, `config/chrome.json`, `requirements-chrome.txt`, the `CHROME_STACK` Dockerfile build arg, the chrome heredoc + Stage 6 consumer in `scripts/clip-pipeline.sh`. Stage 5 frames now flow directly to Stage 6 unmodified.
>
> The original architecture is preserved below as a historical record.

---

# UI Chrome Masking + Overlay OCR (historical)

Per `ClippingResearch.md` §Additional topic 3: Twitch/Kick streams have dense UI chrome (webcam crop, chat panel, sub alerts, donation banners, logo overlays) that competes with the actual moment for the VLM's attention. Phase 4.1 ships two light-weight subsystems that together cover the 80 % case:

1. **OpenCV MOG2 transient-overlay detection** — finds overlays that pop in mid-clip (sub alerts, follower toasts, bit rain)
2. **PaddleOCR overlay-text extraction** — reads the text ON those overlays and hands it to Stage 6 as hard evidence

Florence-2 persistent-overlay auto-calibration and SigLIP 2 image-text cosine are documented in `config/chrome.json::deferred` and deferred until a proper eval harness exists.

---

## Data flow

```
Stage 5 extracts 6 payoff-window frames per moment
        │
        ▼
chrome_mask.process_moment(frame_paths, out_dir, vod_basename)
        │
        ├─(1) OBS scene override ──▶ config/streamers/{channel}_chrome.json
        │                            (manual bbox overrides; WIN over MOG2)
        │                                        │
        │  OR                                    │
        │                                        │
        ├─(2) OpenCV MOG2 background subtraction
        │     @ 2 fps across the 6 frames
        │     → {(x, y, w, h) bboxes}
        │
        ├─(3) Mask application                   ← blur / black-fill per chrome.json
        │     (in-place swap of masked frames    ← the VLM picks them up unchanged)
        │     over originals)
        │
        └─(4) PaddleOCR PP-OCRv5 on UNMASKED frames
              → overlay_text_records
              → concatenated into overlay_text
                    │
                    ▼
            /tmp/clipper/chrome_<T>.json
                    │
                    ▼
  Stage 6 prompt ←────┤
  │                    │
  │                    ▼
  Cascade references ← append overlay_text to [transcript, why]
```

No Stage 2 / Stage 3 / Stage 4 / Stage 4.5 changes — this plugs in strictly between Stage 5 and Stage 6.

---

## 1. OBS scene overrides

Users who know their stream layout drop a `{channel}_chrome.json` into `config/streamers/` with the persistent-region bboxes. The file's `channel` field is matched case-insensitively as a substring of the VOD basename; first match wins. Overrides **always win over MOG2**.

Example file (see `config/streamers/README.md` for the full schema):

```json
{
  "version": 1,
  "channel": "lacy",
  "resolution": [1920, 1080],
  "persistent_regions": [
    {"label": "webcam",     "x": 1500, "y": 720,  "w": 380, "h": 340,  "mask": true},
    {"label": "chat_panel", "x": 0,    "y": 0,    "w": 380, "h": 1080, "mask": true}
  ]
}
```

Bboxes are automatically scaled from the file's `resolution` to Stage 5's frame resolution (960 × 540 by default). `mask: false` entries are remembered but not applied — useful for documenting a region without actually hiding it.

---

## 2. MOG2 transient detection

When no override matches, OpenCV's MOG2 background-subtractor runs across the 6 payoff-window frames at effective 2 fps (one call per frame). Contours larger than `min_contour_area` (default 2500 px²) become candidate overlay bboxes. Safety clamp: if the total masked area would exceed `max_masked_area_ratio` (default 35%) of the frame, the detector is considered misfired and NO mask is applied — better to let the VLM see the whole frame than to black out most of it.

> [!note] First-frame priming (BUG 42 fix, 2026-04-30)
> MOG2's GMM has no learned background on the very first `apply()` call, so without priming it returns near-100 % foreground for the seed frame. That mask used to be OR'd into the accumulated mask and would dominate the total area — which trips the `max_masked_area_ratio=0.35` safeguard on every window, silently disabling chrome detection across the entire VOD. The fix: feed the first frame `5×` (matching `history=5`) to converge the GMM before any measurement, then accumulate masks only from `imgs[1:]`. The seed frame's mask is intentionally discarded — it carries no signal.

> [!warning] Frame-spacing structural limitation (BUG 50, 2026-04-30)
> Even with the BUG 42 priming fix, MOG2 misfires 100 % on Stage 5's typical window because frames are spaced 1-3 seconds apart (offsets `[-2, 0, +1, +2, +3, +5]`), not the sub-second adjacency MOG2 was designed for. Natural streamer movement between samples accumulates to >35 % "foreground" easily, the safeguard catches the misfire, and MOG2 returns `[]`. **OBS overrides + PaddleOCR are the canonical chrome-detection paths**; MOG2 is best-effort for streams with very static backgrounds (e.g. fixed Just Chatting setups with minimal movement) where the misfire ratio doesn't trip. See [[concepts/bugs-and-fixes#BUG 50]].

MOG2 runs entirely on the existing `opencv-python-headless` dep — no new packages.

---

## 3. Mask application

Configurable via `chrome.json::transient_detection.mask_method`:

| Method | Effect |
|---|---|
| `blur` (default) | Gaussian blur with kernel proportional to bbox size. Preserves frame structure — the VLM can still see "something was there" without reading the overlay. |
| `black` | Solid black rectangle. Most aggressive. Use when blur isn't enough. |
| `keep` | Debug only — draws a red outline but doesn't mask. Useful for validating detection. |

Masked frames are written to a scratch directory, then swapped in place over the Stage 5 originals so Stage 6 picks them up unchanged.

---

## 4. PaddleOCR overlay text

PaddleOCR PP-OCRv5 runs on up to `max_frames_per_moment` (default 2) **unmasked** frames per moment. Text above `min_confidence` (default 0.6) is collected into `overlay_text_records`, de-duplicated, and joined into a single `overlay_text` string:

```
"USER gifted 5 subs | Subscribe! | CLUTCH"
```

This string is injected into Stage 6's prompt as a dedicated block:

```
Overlay text visible on the frames (from OCR — treat as hard evidence):
  USER gifted 5 subs | Subscribe! | CLUTCH
Use this text when it directly describes the moment. Do not use it when it
contradicts what the streamer is saying in the transcript.
```

And added to the `refs` list that the [[entities/grounding]] cascade checks title/hook/description against. This is the **asymmetric signal** that complements Phase 2's chat-event ground truth: Phase 2 catches claims that INVENT events; Phase 4.1 SUPPORTS claims when overlay pixels literally say the thing.

PaddleOCR is opt-in — the default Docker build installs it via `CHROME_STACK=full`. `CHROME_STACK=slim` skips it cleanly; MOG2 still runs.

> [!warning] PaddleOCR can wedge on rare frames (BUG 49, 2026-04-30)
> Once-per-VOD on long runs, PaddleOCR has been observed to hang indefinitely inside a single `predict()` call (likely C++ extension stuck in oneDNN's CPU thread pool, or a transient memory-pressure event during the angle-classifier sub-model swap). The chrome heredoc has no escape on its own — bash blocks until python exits. Three layers of containment now bound the chrome stage:
> 1. **SIGALRM per-call timeout** in `extract_overlay_text` (default 30 s, configurable via `chrome.json::ocr.per_call_timeout_seconds`). A wedged call raises `_OCRTimeout`, the per-moment try/except resumes with the next moment.
> 2. **Per-frame heartbeat callback** plumbed through `process_moment(heartbeat=...)` → `extract_overlay_text(heartbeat=...)`. The chrome heredoc passes a closure that bumps STAGE_FILE on every per-frame OCR call so the dashboard's BUG-31 staleness gate can't trip even on a full-timeout 30 s hang.
> 3. **Outer `timeout 600 env ...`** on the heredoc invocation. If the python child wedges below the Python signal layer, bash regains control after 600 s and falls through `|| warn` into Stage 6.
>
> See [[concepts/bugs-and-fixes#BUG 49]].

---

## Cost / risk

| Dimension | Cost |
|---|---|
| Pipeline wall time | +5-15 s per VOD on a 10-moment run with OCR enabled |
| VRAM | zero (PaddleOCR defaults to CPU; MOG2 is CPU-only opencv) |
| Image size | ~1.2 GB added when `CHROME_STACK=full` |
| Risk | low — every layer degrades gracefully: no override → MOG2; no opencv → skip masking; no paddleocr → skip OCR; the Stage 6 `chrome_<T>.json` missing → overlay_text="" and Stage 6 behaves as post-Phase-3 |

---

## Related

- [[entities/chrome-mask-module]] — implementation
- [[concepts/vision-enrichment]] — Stage 6 consumer
- [[entities/grounding]] — cascade integration (overlay_text as reference)
- `config/chrome.json` / `config/streamers/README.md` — runtime config
- `IMPLEMENTATION_PLAN.md` — Phase 4.1 definition; Florence-2 calibration + SigLIP 2 deferred
