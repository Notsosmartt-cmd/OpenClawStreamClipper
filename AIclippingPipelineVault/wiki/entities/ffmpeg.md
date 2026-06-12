---
title: "FFmpeg"
type: entity
tags: [video, audio, processing, ffmpeg, blur-fill, rendering, nvenc, infrastructure, stage-7]
sources: 2
updated: 2026-06-12
---

# FFmpeg

Video/audio processing tool used throughout the clipping pipeline. All FFmpeg calls use `-nostdin` (the pipeline still uses it after the bare-metal port to avoid stdin conflicts when many `ffmpeg` invocations run concurrently — see Stage 7 parallelism below).

> [!note] Encoder is NVENC-when-available (2026-06)
> Since the Stage 7 NVENC switch (2026-06-06) the primary video encoder is **`h264_nvenc`** when it actually encodes on this machine, with **`libx264`** as the per-clip fallback. The model is unloaded before Stage 7, so the GPU is free for hardware encode. Selection logic lives in `scripts/lib/venc.py` (shared) and `scripts/pipeline/stages/stage7.py` (solo render); `STAGE7_ENCODER=auto|nvenc|libx264` overrides (default `auto` probes NVENC with a 0.1 s null-muxed test encode). NVENC accelerates the *encode* only — the per-clip filtering (blur-fill, captions, color) stays on CPU.

---

## Uses in the pipeline

### Stage 2 — Audio extraction (for Whisper)
Extracts audio track, resamples to 16kHz mono WAV:
```bash
ffmpeg -nostdin -i input.mp4 -ar 16000 -ac 1 -y audio.wav
```
Also used to split audio into 20-minute chunks for safer transcription.

### Stage 5 — Frame extraction
Extracts 6 JPEG frames from a 30-second window around each candidate moment:
- Resolution: 960×540 (half-res for speed)
- Quality: `q:v 2` (high quality JPEG)
- Rate: 1 frame per 5 seconds (`fps=0.2`)

### Stage 7 — Clip audio extraction
Extracts 45-second audio segments for all clips in a single pass (before Whisper batch captioning).

### Stage 7 — Blur-fill 9:16 rendering (main render)

The full render pipeline per clip (`scripts/pipeline/stages/stage7.py`). Key parameters:
- Source window: per-moment `clip_start` / `clip_duration` from the manifest (default ≈ `T - 22s` for 45 s; Stage 4 may set a shorter/variable window)
- Codec (primary): **`h264_nvenc`** `-preset p5 -rc vbr -cq 20 -profile:v high -b:v 18M -maxrate 20M -bufsize 40M`. Falls back per-clip to **`libx264 -crf 20 -preset slow`** if NVENC fails or `STAGE7_ENCODER=libx264`. (The older `libx264 -crf 23 -preset medium` path now only survives as the legacy/last-ditch fallback `_ffmpeg_legacy`.)
- Audio: AAC 192kbps, `-movflags +faststart`
- Captions: burned-in — CapCut-style word-box ASS by default (see below), flat SRT burn as fallback

**Filter chain (blur-fill technique):**
```
[input] split [bg][fg];
[bg] scale=1080:1920:force_original_aspect_ratio=increase,
     crop=1080:1920,
     boxblur=<radius>:<passes> [blurred_bg];
[fg] scale=1080:-2:force_original_aspect_ratio=decrease [fg_scaled];
[blurred_bg][fg_scaled] overlay=(W-w)/2:(H-h)/2,<color eq>[,<shake>] [video];
[video] <hook drawtext> <captions subtitles/ass> [output]
```

What this produces:
- Background: blurred + zoomed version of the 16:9 frame fills the entire 1080×1920 canvas
- Foreground: the full original 16:9 frame, scaled to 1080px wide with **auto-computed even height** (`scale=1080:-2`), centered vertically
- No content is cropped — every pixel of the original frame is visible

> [!note] Blur radius/passes are originality-driven
> `boxblur` radius and passes (and the color `eq`/`hue`, optional `vignette`, optional micro-`shake`) come from `originality.py` per clip rather than the fixed `boxblur=25:5` in earlier versions — see [[concepts/originality-stack]]. The legacy fallback path still uses the hardcoded `boxblur=25:5`.

> [!note] Alternative framing: camera_pan
> When `CLIP_FRAMING=camera_pan`, the foreground is replaced by a face-tracked crop/pan (`face_pan.py`) instead of the blur-fill letterbox. Blur-fill remains the default. See [[concepts/clip-rendering]].

> [!note] Why blur-fill instead of hard crop
> Hard crop loses ~44% of horizontal content. A streamer visible on the right side of frame gets cut out. Blur-fill shows everything. The blurred background also looks better than solid black bars on social media platforms.

> [!note] Stage 7 renders in parallel (2026-06-04)
> Clip-audio extraction (7b) and the render loop (7d) are dispatched across a `ThreadPoolExecutor` (default 4 workers, `STAGE7_WORKERS` to tune, `1` = serial). Each clip is an independent `ffmpeg` invocation, so 4 concurrent encodes saturate the i9-13900K without oversubscription.

### Stage 7 — Caption burn-in

**Default: CapCut-style word-box captions.** A word-level SRT (from per-clip Whisper) is converted to an ASS by `kinetic_captions.py` and burned with `subtitles='…':fontsdir='…'` so libass finds the bundled **Montserrat Black** font (`assets/fonts/`). Preset/accent/case are tunable: `CLIP_CAPTION_PRESET` (default `capcut`), `CLIP_CAPTION_ACCENT` (default `yellow`), `CLIP_CAPTION_CAPS` (default `false`).

**Fallback: flat SRT burn.** If ASS generation fails (e.g. empty SRT), Stage 7 burns the raw SRT with `force_style` parameters sourced from `originality.py` (`FontSize`, `Bold=1`, `PrimaryColour`, `OutlineColour`, `Outline`, `Alignment=2`, `MarginV`).

Captions are always burned in (not soft/embedded) — they display correctly on all platforms without requiring player subtitle support. A separate top **hook caption** (`drawtext`, also Montserrat Black) is drawn when `CLIP_HOOK_CAPTION` is enabled and the moment has a hook line.

### Stage 7.5 — Transition animations (jump-cuts + white flashes)

Optional post-render pass (`scripts/lib/clip_cuts.py`) that runs on the *finished* clips so burned captions stay in sync. `CLIP_JUMP_CUTS=off|gaps|llm|on` drops dead-air/rambling spans and concatenates the kept spans with `xfade=transition=fadewhite`; `CLIP_FLASH_CUTS=off|on` overlays transient white pops.

> [!warning] White flashes use `drawbox`, not `fade` (BUG 64)
> The flash is built from `drawbox=...:t=fill:color=white@α:enable='between(t,a,b)'` so it only draws inside its window. `fade=t=out/in:color=white` was tried first but `fade` **holds** the colour outside its ramp, painting the whole clip white — that was the BUG 64 all-white regression (fixed 2026-06-07). See [[concepts/transition-animations]] and [[concepts/bugs-and-fixes]].

---

## Also: `ffprobe`

Used in Stage 1 to get VOD duration. Required alongside FFmpeg.

---

## Related
- [[entities/faster-whisper]] — produces the SRT files that FFmpeg burns into clips
- [[concepts/clipping-pipeline]] — Stages 2, 5, and 7
- [[concepts/clip-rendering]] — full Stage 7 rendering detail (NVENC, framing, audio mix)
- [[concepts/originality-stack]] — supplies the per-clip blur/color/caption parameters
- [[concepts/transition-animations]] — Stage 7.5 jump-cuts and white flashes (BUG 64)
