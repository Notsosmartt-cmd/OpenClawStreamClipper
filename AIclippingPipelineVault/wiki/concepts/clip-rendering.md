---
title: "Clip Rendering (Stage 7)"
type: concept
tags: [rendering, ffmpeg, blur-fill, smart-crop, captions, subtitles, 9:16, vertical, originality, stitch, stage-7, video, nvenc, gpu-encode]
sources: 3
updated: 2026-06-06
---

# Clip Rendering (Stage 7)

The final production stage. Converts approved moments into finished 1080×1920 vertical clips with burned-in captions, ready for TikTok/Reels/Shorts. As of the [[concepts/originality-stack]] additions, every clip is rendered through per-clip randomized parameters and one of four framing modes.

Uses [[entities/ffmpeg]] and [[entities/faster-whisper]] (for captions). Runs after [[concepts/vision-enrichment]] unloads the vision model (or skips the swap when text and vision use the same multimodal model).

---

## Stage 7 sub-steps

1. **Generate clip manifest** — vision-generated titles used as filenames (sanitized, e.g. `IRL_Fat_Sack_Checkout_Fiasco.mp4`), written to `clip_manifest.txt`. Columns now include `clip_start` + `clip_duration` so variable lengths propagate from Stage 4.
2. **Extract clip audio** — single FFmpeg pass extracts variable-length audio segments for all clips.
3. **Batch caption transcription** — single Whisper model load; transcribes all clip audio segments; outputs individual SRT files with word-level timestamps.
4. **Render solo + narrative clips** — per-clip FFmpeg pipeline with randomized params, framing mode, optional TTS voiceover + music bed. Stitch-group members are deferred to step 5.
4.5. **Transition animations (7d.5)** — optional, gated post-pass over the FINISHED clips: white-flash beats + jump-cut compression (drop dead air, `xfade=fadewhite`). Path-agnostic + caption-safe (captions are already burned). `scripts/lib/clip_cuts.py`, flags `CLIP_FLASH_CUTS` / `CLIP_JUMP_CUTS`. Off by default. See [[concepts/transition-animations]].
5. **Stage 7e — stitch render** — `scripts/lib/stitch_render.py` concatenates each stitch group's members with `xfade` transitions into one composite clip.
6. Unload Whisper, proceed to Stage 8.

---

## Framing modes (Wave B)

Controlled by `CLIP_FRAMING`. Four modes select a different base filter chain before the hook and subtitle layers are appended.

| Mode | Summary |
|---|---|
| `blur_fill` | Legacy look — full 16:9 foreground over blurred-fill background. Kept for backward compatibility. |
| `smart_crop` **(default)** | Uses vision-returned `chrome_regions` bboxes to crop out chat / logo / webcam border / alerts before the blur-fill composition. Falls back to `blur_fill` if no regions detected. |
| `centered_square` | Foreground 1080×810 centered at `y=555` over blurred-fill bg. Leaves space top and bottom for hook + captions. |
| `camera_pan` | Uses the precomputed face-track path from Stage 6.5 ([[entities/face-pan]]). Falls back to `blur_fill` per clip when no faces were found. |

### Legacy blur-fill FFmpeg chain

```
[input] split [bg][fg]
[bg] scale=1080:1920:force_original_aspect_ratio=increase, crop=1080:1920, boxblur=<R>:<P> [blurred_bg]
[fg] scale=1080:-2:force_original_aspect_ratio=decrease [fg_scaled]
[blurred_bg][fg_scaled] overlay=(W-w)/2:(H-h)/2 [video]
[video] eq=... , hue=h=<H>° [, vignette] [, shake] [, drawtext (hook)] [, subtitles] [output]
```

Where `<R>`, `<P>`, `<H>`, and the subsequent eq / hue / vignette / shake / hook / subtitle styling are all per-clip randomized — see wave A below. When `CLIP_ORIGINALITY=false` these collapse to the pre-April-2026 fixed values (`boxblur=25:5`, no eq stack, fixed hook/subtitle palette).

### Smart-crop specifics

The vision model returns `chrome_regions: [{x, y, w, h, label}, ...]` for chat / logo / webcam / alert / score UI. Stage 7 computes the largest remaining rectangle (x0/y0/x1/y1 walk — each region shaves off the side it sits on) and prepends `crop=W:H:X:Y` before the blur-fill chain so the cropped-out chrome never appears in the output. Minimum remaining size is 640×360 — below that Stage 7 reverts to `blur_fill` for safety.

### Camera-pan specifics

Stage 6.5 emits a `crop=w:h:x='<piecewise-linear expr over t>':y='<expr>',scale=1080:1920:flags=lanczos` filter string per clip. Stage 7 splices it in place of the blur-fill filter. Up to 32 keyframes per clip.

---

## Output specifications

| Property | Value |
|---|---|
| Resolution | 1080×1920 (9:16 vertical) |
| Video codec | **NVENC H.264 (`h264_nvenc`) by default** when GPU encode is available; `libx264` fallback. Profile High, `yuv420p` |
| Quality | NVENC `-rc vbr -cq 20`; libx264 `CRF 20, preset slow`. Both: 18 Mbps target / 20 Mbps max / 40 Mbps bufsize |
| Frame rate | 30 fps (CFR) |
| Audio codec | AAC, 192 kbps |
| Duration | Per-category variable: hype/reactive 18–25 s, funny 20–30 s, emotional 40–55 s, storytime 50–80 s (narrative groups up to 90 s) |
| Subtitles | Burned-in (not soft). **CapCut word-box style by default** (bold Montserrat Black, white + black outline, active word in a yellow box advancing word-by-word) — see [[concepts/captions]] |

The old defaults (CRF 23, preset medium, 128 kbps audio) are still used by the legacy fallback render path when the primary render fails.

> [!note] GPU encode (NVENC) — 2026-06-06
> Stage 7 encodes with **`h264_nvenc` (GPU) by default**. The model is already unloaded before rendering (`run()` calls `common.unload_model`), so the full GPU is free for the NVENC ASIC — which is several × faster than `libx264 -preset slow` AND offloads the CPU so the parallel filter work (blur-fill, captions) runs faster too. **Reliability:** encoder is chosen by `_resolve_encoder()` (`STAGE7_ENCODER`=`auto`|`nvenc`|`libx264`, default `auto`) — `auto` runs a one-shot 0.1 s NVENC test-encode and only uses it if it actually works; and **each clip falls back to `libx264` if its NVENC render fails** (session limit / driver), so a flaky session never drops a clip. NVENC `-rc vbr -cq 20` + the 18 Mbps cap targets ~the libx264 CRF-20 quality. Note: only the *encode* is GPU-accelerated; the per-clip *filtering* stays on CPU, so the speedup depends on the filter/encode split. See [[concepts/bugs-and-fixes]] and `stage7.py`.
>
> **Extended to stitch + style-profile paths (2026-06-06 later).** Originally only the solo-clip render (`stage7.py`'s `_ffmpeg_render`) used NVENC; the stitch (7e, `stitch_render.py`) and style-profile (`profile_render.py`) render paths still hard-coded `libx264`. They now share a single helper — **`scripts/lib/venc.py`** (`venc.encoder()` + `venc.video_args(crf, preset_libx264)`) — so the *whole* of Stage 7 is GPU-encoded when NVENC is available. `venc` resolves the encoder once per process (same `STAGE7_ENCODER` env + 0.1 s probe), maps `crf`→`-cq` for NVENC vbr, and prints `[VENC] video encoder: …`. These two paths render **sequentially** (no concurrent NVENC sessions → no session-limit risk) and are already failure-soft (a failed render just skips that group/profile), so they use the probe-gated encoder *without* the per-clip libx264 retry that the parallel solo path needs. The `-c copy` single-member stitch passthrough is unchanged (no re-encode). `stage7.py`'s solo path keeps its own inline encoder switch (untouched — it works and has the per-clip fallback).

---

## Wave A — Per-clip randomization

Every clip in a batch is rendered with its own deterministic-but-unique set of parameters seeded from the moment timestamp. `scripts/lib/originality.py` emits the shell vars; Stage 7 `eval`s them into the filter graph. See [[concepts/originality-stack]] §Wave A for the full table.

- Blur radius `[18, 32]`, passes `[3, 6]`
- Mirror 45 % of clips when `mirror_safe=true`
- `eq` stack: `brightness ±0.05`, `saturation [0.92, 1.18]`, `contrast [0.95, 1.15]`, `gamma [0.93, 1.08]`, `hue ±6°`
- 30 % chance of `vignette=angle=PI/5`
- 35 % chance of a micro-`shake` via time-varying crop (`sin(t)/cos(t)` offsets)
- Hook palette rotated from 6 combinations (color / box / border)
- Subtitle style rotated from 5 variants (color / outline / margin)

---

## Wave C — Stitch rendering

`scripts/lib/stitch_render.py` handles `group_kind=stitch` (see [[concepts/originality-stack]] §Wave C).

1. Render each group member to a short intermediate MP4 through the same framing chain (with its own per-member randomization — each sub-clip can flip, boost saturation differently, etc.).
2. Concatenate with `xfade` transitions (0.35 s, selected from a 7-element pool keyed by originality seed). Falls back to `concat` demuxer when a member is shorter than the transition duration.
3. Apply the first member's hook text as a `drawtext` overlay on the concat.
4. Write to `clips/<Title>.mp4` and append to `clips_made.txt` so Stage 8 picks it up.

---

## Audio layers (Wave D)

When `CLIP_TTS_VO=true`, Stage 7 invokes `scripts/lib/piper_vo.py` which calls [[entities/piper]] and pads the WAV to exactly `clip_duration` with silence positioning the utterance according to `placement`. The render loop builds a `filter_complex` amix graph:

```
[0:a]volume=0.45[src_audio];                       # source ducked to -8 dB
[1:a]volume=2.3,apad=whole_dur=<DUR>[vo_audio];    # VO boosted ~+7 dB
[2:a]atrim=0:<DUR>,volume=0.08[music_audio];       # music bed at -22 dB (looped via -stream_loop -1)
[src_audio][vo_audio][music_audio]amix=inputs=3:duration=first:dropout_transition=0[aout]
```

Music-bed path is chosen via `scripts/lib/music_pick.py` (tier A folder convention by default, tier C [[entities/librosa]]-scored when `CLIP_MUSIC_TIER_C=true`). If neither VO nor music is enabled, the simple `-af rubberband=...` path is used as before.

---

## Captions and hook text

The default caption is a **CapCut-style word box** — bold **Montserrat Black** (bundled in `assets/fonts`, burned via `fontsdir`), white text + black outline, with the currently-spoken word in a **yellow box that advances word-by-word**. Built by `scripts/lib/kinetic_captions.py` (`render_box`) → ASS → FFmpeg `subtitles`; both the solo (`stage7.py`) and AI-profile (`profile_render.py`) render paths use it. Dials: `CLIP_CAPTION_ACCENT`, `CLIP_CAPTION_CAPS`, `CLIP_CAPTION_PRESET`. Full detail — incl. the four bugs this fixed and the legacy `force_style` fallback — in [[concepts/captions]].

Sample (FFmpeg burn test, the active word boxed):

```
        but don't ┃worry┃        ← "worry" in a yellow box, white outlined text
```

---

## Output filenames

Clip filenames use the AI-generated title with spaces — e.g. `Epic Clutch Play.mp4`, `Chat Goes Wild.mp4`. Title sanitization removes `/`, `\`, `|`, `"`, `'`; allows alphanumerics, spaces, and hyphens; capped at 50 characters.

---

## Speed control

Dashboard **Speed** dropdown (`1×` / `1.1×` / `1.25×` / `1.5×`) applies `setpts` to video and `rubberband` (pitch-tracked) to audio; SRT timestamps are rescaled to match. Full details: [[concepts/speed-control]].

---

## Clip timing

Source window: `T - 22s` to `T + 23s` where T is the peak moment timestamp from Stage 4.

Fixed 45-second duration. The clip is not dynamically sized to the content — it's always 45 seconds. This is a known limitation.

> [!todo] Variable clip length (open question)
> Should clip length be variable (15–60 seconds) based on content type? A storytime segment might need 60 seconds to include the payoff. A quick reaction might need only 15 seconds. See [[concepts/open-questions]] for analysis.

---

## Output files

Saved to `clips/` on host (= `~/VODs/Clips_Ready/` in container). Filenames from vision-generated titles:
- `IRL_Fat_Sack_Checkout_Fiasco.mp4`
- `Gaming_Clutch_1v4_Comeback.mp4`

Diagnostic JSON saved to `clips/.diagnostics/` for post-hoc analysis.

---

## Related
- [[entities/ffmpeg]] — does the rendering
- [[entities/faster-whisper]] — generates word-level SRT subtitles
- [[concepts/captions]] — subtitle style, hook card, per-clip palette randomization
- [[concepts/speed-control]] — setpts + rubberband speed-up, SRT rescaling
- [[concepts/vision-enrichment]] — Stage 6 that generates titles and hook text
- [[concepts/clipping-pipeline]] — Stage 7 in context
- [[concepts/open-questions]] — variable clip length discussion
