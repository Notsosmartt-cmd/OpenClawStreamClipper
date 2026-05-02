---
title: "Clip Rendering (Stage 7)"
type: concept
tags: [rendering, ffmpeg, blur-fill, smart-crop, captions, subtitles, 9:16, vertical, originality, stitch, stage-7, video]
sources: 3
updated: 2026-04-25
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
| Video codec | H.264 (`libx264`), profile High@4.2, `yuv420p` |
| Quality | CRF 20, preset `slow`, 18 Mbps target / 20 Mbps max / 40 Mbps bufsize |
| Frame rate | 30 fps (CFR) |
| Audio codec | AAC, 192 kbps |
| Duration | Per-category variable: hype/reactive 18–25 s, funny 20–30 s, emotional 40–55 s, storytime 50–80 s (narrative groups up to 90 s) |
| Subtitles | Burned-in (not soft subtitles) |

The old defaults (CRF 23, preset medium, 128 kbps audio) are still used by the fallback render path when the primary render fails.

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

Subtitle style (ASS font/color/margin), per-clip palette randomization, and the AI-generated hook card rendered at the top of the video are all covered in [[concepts/captions]].

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
