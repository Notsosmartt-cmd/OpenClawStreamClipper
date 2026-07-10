---
title: "Clip Rendering (Stage 7)"
type: concept
tags: [rendering, ffmpeg, blur-fill, smart-crop, captions, subtitles, 9:16, vertical, originality, stitch, stage-7, video, nvenc, gpu-encode]
sources: 3
updated: 2026-07-09
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

## Companion punchline-only shorts (`CLIP_COMPANION_SHORTS`, default off — 2026-07-09)

Owner req (clip review): for a long clip with a strong late payoff, ALSO emit a short,
payoff-only version — "post the full clip AND a small ending clip for quick sharing" (the
motivating example was the 'Yo!' Freestyle, punchline "grab your balls twist them pop them").
Implemented in `stage7.py:_maybe_companion_short`, called right AFTER the full clip renders
and BEFORE `_maybe_cold_open`.

- **Mechanism (deliberate):** the short is a **straight sub-cut of the FINISHED clip** (ffmpeg
  `-ss/-t` + NVENC re-encode), NOT a re-render. So it inherits burned-in captions, blur-fill,
  colors, 9:16 framing — and the captions stay ALIGNED for free. A separate re-render would
  misalign captions because `clip_<T>.srt` is 0-based per-clip (transcribed from
  `clip_audio_<T>.wav`), so a different window would need SRT surgery. Running before
  cold-open keeps the payoff offset clean (no teaser prefix).
- **Window:** payoff-centered `[T−lead, T+tail]` in rendered time
  (`payoff_r=(T−clip_start)/speed`), default lead 5 s / tail 10 s; start snapped back to a
  word boundary via `clip_<T>.srt` (`_snap_short_start`, so it doesn't open mid-word). Output
  `"<title> (Short).mp4"`, recorded to `clips_made` (delivery posts it alongside the full clip).
- **Gates:** flag on; full clip ≥ `CLIP_COMPANION_MIN_FULL_S` (default **30 s** — the owner's
  36 s example must qualify); short ≥ `CLIP_COMPANION_MIN_S` (6 s) AND < 75% of the full;
  category/segment NOT in `CLIP_COMPANION_EXEMPT` (default `storytime,emotional` — a
  payoff-only cut loses their buildup; note rap/freestyle are NOT exempt here, unlike P-TIGHT,
  because they're the owner's use case).
- **Safety:** ADDITIVE (never touches the full clip) + failure-soft. Env tuning:
  `CLIP_COMPANION_LEAD_S`, `_TAIL_S`, `_MIN_FULL_S`, `_MIN_S`, `_EXEMPT`. Verified on the real
  36 s 'Yo!' clip → 11 s ending short; storytime + <30 s correctly skip.

> [!note] Follow-ups if enabled in production
> Titles are inherited from the full clip, so a short may carry a title referencing trimmed
> setup — same title-decoherence class as the P-TIGHT head-cut warning. A smarter version
> could use the P-TIGHT payoff locator for a tighter punchline boundary.

---

## A/B variant clips + platform post kit (`CLIP_AB_VARIANTS`, `CLIP_POST_KIT`, default off — 2026-07-10)

Owner req (trial-reel A/B testing across platforms). Two additive Stage-7 outputs, both default off,
gated behind their env flags / dashboard checkboxes. Full plan: [[concepts/plan-captions-and-ab-variants-2026-07]].

- **A/B variant B** (`stage7.py:_maybe_ab_variant`, after the primary render): a **full INDEPENDENT
  profile render** — NOT a sub-cut — of an eligible clip, using the alternate-angle hook that Stage 6
  generated (`hook_variants`, P2.1) AND a **perturbed seed** (`profile_render.py --seed-offset 1`,
  `CLIP_VARIANT_SEED_OFFSET`). The perturbed seed makes B's SFX pick + profile/fingerprint draws
  differ from A while beat PLACEMENT stays anchored on the real timestamp — so A and B differ in
  hook + sound + visual effects (owner's explicit choice: varied AV per side, not caption-only).
  Output `"<title> (B).mp4"`. Classic A/B = 2 variants (A is the primary). Gated to the top-N clips
  (`CLIP_AB_VARIANTS_TOP_N`, default 5). **Requires profile mode** (`CLIP_STYLE_PROFILES`) for the AV
  variety — a hook-only B is logged-skipped. Consequence: the "shared master + drawtext-only" cost
  optimization is INVALID (a master can't carry per-variant AV), so B is a full NVENC render.
- **Platform post kit** (`stage6_vision.py:_generate_post_kit` → `stage7.py:_maybe_write_post_kit`):
  a `"<title>.post.json"` sidecar with ready-to-paste copy for TikTok / Instagram / YouTube Shorts —
  **no hashtags** (owner decision). Generated in Stage 6 (model resident) because Stage 7 runs with
  the model UNLOADED; Stage 7 just writes the file. Includes both A/B hooks + a `trial_reel` marker.
- **Safety:** both ADDITIVE + failure-soft; a failed variant/kit never touches the primary clip.

---

## Related
- [[entities/ffmpeg]] — does the rendering
- [[entities/faster-whisper]] — generates word-level SRT subtitles
- [[concepts/captions]] — subtitle style, hook card, per-clip palette randomization
- [[concepts/speed-control]] — setpts + rubberband speed-up, SRT rescaling
- [[concepts/vision-enrichment]] — Stage 6 that generates titles and hook text
- [[concepts/clipping-pipeline]] — Stage 7 in context
- [[concepts/open-questions]] — variable clip length discussion
