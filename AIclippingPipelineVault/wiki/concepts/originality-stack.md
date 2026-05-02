---
title: "Originality Stack (TikTok 2025 defense)"
type: concept
tags: [originality, rendering, fingerprinting, tiktok, framing, stitch, tts, music, camera-pan, stage-7, video, hub]
sources: 1
updated: 2026-04-22
---

# Originality Stack

A composable set of render-time transformations added in April 2026 to defeat TikTok's September 2025 unoriginal-content detection. The baseline 9:16 blur-fill + burned-captions + speed-shift output is now a documented TikTok flag pattern ([[sources/tiktok-originality-2026]]) — every feature on this page exists to differentiate each rendered clip from the bare source and from every other clip in a batch.

Shipped as five coordinated additions to [[concepts/clipping-pipeline]]:

| Wave | What | Where | Flag |
|---|---|---|---|
| A | Per-clip randomized blur / mirror / color / hook / subtitle palette + export spec bump | Stage 7 | `CLIP_ORIGINALITY` |
| B | Four framing modes — `blur_fill` (legacy), `smart_crop`, `centered_square`, `camera_pan` | Stage 6 prompt + Stage 7 filter select | `CLIP_FRAMING` |
| C | MomentGroup data model — narrative arcs (60–90 s) and stitch bundles (3–4 short clips) | New Stage 4.5 + Stage 7e | `CLIP_STITCH`, `CLIP_NARRATIVE` |
| D | Piper local TTS voiceover + optional music-bed (tier A folder convention or tier C librosa scoring) | Stage 6 prompt + Stage 7 audio mix | `CLIP_TTS_VO`, `CLIP_MUSIC_BED`, `CLIP_MUSIC_TIER_C` |
| E | Active-speaker camera pan — OpenCV face detection + smoothed virtual crop path | New Stage 6.5 + `camera_pan` framing | `CLIP_CAMERA_PAN` |

All waves are independently toggleable through the **Originality & Render** panel in the [[entities/dashboard]].

---

## Wave A — Per-clip randomization

Controlled by `CLIP_ORIGINALITY` (default `true`). The Stage 7 render loop calls `scripts/lib/originality.py <timestamp> <orig> <mirror_safe> <framing> <category>` and `eval`s the emitted shell `KEY=VALUE` lines. The seed is MD5-derived from the timestamp so re-renders are stable, but every moment in a batch gets a different look.

What varies:

- **Blur**: `boxblur=R:P` with `R ∈ [18,32]`, `P ∈ [3,6]` (baseline was fixed `25:5`).
- **Mirror**: horizontal flip on ~45 % of clips, only when vision returned `mirror_safe=true`.
- **Color stack**: `eq + hue + optional vignette`. Subtle envelope — `saturation ∈ [0.92,1.18]`, `contrast ∈ [0.95,1.15]`, `gamma ∈ [0.93,1.08]`, `hue ∈ ±6°`.
- **Micro-shake**: 35 % of clips get a tiny time-varying `crop` with `sin(t)/cos(t)` offsets — breaks per-frame hashing.
- **Hook palette**: 6 color combinations (black-on-white, yellow, pink, mint, etc.), randomized Y position (45–130 px), fontsize (36–46 pt).
- **Subtitle palette**: 5 `force_style` variants with different colors, outlines, margins.
- **Export spec bump**: `-crf 20 -preset slow -profile:v high -level 4.2 -pix_fmt yuv420p -r 30 -b:v 18M -maxrate 20M -bufsize 40M -c:a aac -b:a 192k +faststart`. Matches the 15–20 Mbps / High@4.2 profile research calls for.

Setting `CLIP_ORIGINALITY=false` returns to the deterministic legacy look (blur 25:5, single palette).

---

## Wave B — Framing modes

Controlled by `CLIP_FRAMING` (default `blur_fill`). Two modes, selectable in the dashboard:

| Mode | What it does | When to use |
|---|---|---|
| `blur_fill` **(default)** | Full-width 16:9 foreground over a blurred-fill background. Nothing is cropped out of the source. | Every clip unless you specifically want speaker-tracking |
| `camera_pan` | Uses the precomputed face-track path from Stage 6.5. Falls back to `blur_fill` per clip if no faces were found. | When the source has close-up cam faces and you want the reality-TV pan look |

> [!note] `smart_crop` and `centered_square` removed (2026-04-23)
> The previous version exposed four framing modes. `smart_crop` depended on vision-returned `chrome_regions` bboxes that proved too unreliable in practice, and its prompt bloat contributed to detection drift (see [[concepts/bugs-and-fixes]] and `CLIPPING_DIAGNOSTIC.md`). `centered_square` offered no measurable fingerprint benefit over `blur_fill` + per-clip randomization. Legacy configs that still save `framing=smart_crop` or `centered_square` map silently to `blur_fill` via the case-statement default.

---

## Wave C — MomentGroup data model

Adds a grouping layer between [[concepts/highlight-detection]] and [[concepts/clip-rendering]]. `scripts/lib/moment_groups.py` reads `hype_moments.json` (Pass C output) and emits `moment_groups.json` plus an updated `hype_moments.json` where each moment now carries `group_id` + `group_kind`.

### Group kinds

- **`solo`** — single-moment clip, same behavior as before. Default when neither `CLIP_STITCH` nor `CLIP_NARRATIVE` is set (the stage 4.5 call short-circuits).
- **`narrative`** — 2+ adjacent moments in categories `{storytime, emotional, hot_take}` within 120 s of each other. Merged into one long clip (45–90 s). Solves the user-reported need for storytime clips that span multiple segments.
- **`stitch`** — 3–4 short moments in `{funny, hype, reactive, dancing}`, each capped at 12 s, total target ≈28 s. Rendered by `scripts/lib/stitch_render.py` which produces each member through the same framing pipeline (with its own randomized per-segment params), then concatenates with `xfade` transitions (picked from a 7-element pool: fade / wiperight / slideup / circlecrop / distance / slideleft / radial), then applies the hook overlay.

### Stage-7 behavior

- Solo + narrative: rendered inline through the main Stage 7 loop (narrative uses the merged `clip_start`/`clip_end`).
- Stitch: the Stage 7 loop skips stitch members (`continue` when `group_kind=stitch`). After the loop, a **Stage 7e** pass invokes `stitch_render.py` once to render every stitch group as a single composite file.

See [[concepts/clip-rendering]] §Stitch rendering for the FFmpeg concat details.

---

## Wave D — Voiceover + Music bed

Two audio layers that mix over (and duck) the source stream audio.

### Voiceover (Piper)

- Vision model is now prompted to return a `voiceover: {text, placement, tone, duration_estimate_s}` field along with the hook (see [[concepts/vision-enrichment]]).
- Research constraint: 8–14 words, creator-POV, does not restate what the streamer said, no hashtags, no stock TTS monotone.
- When `CLIP_TTS_VO=true`, Stage 7 calls `scripts/lib/piper_vo.py` to synthesize via the [[entities/piper]] CLI and pad/trim the WAV to exactly `clip_duration`. Placement (`intro` / `peak` / `outro`) is implemented by inserting leading silence inside the WAV so the downstream FFmpeg mix is trivial.
- Mix: source audio is ducked to 0.45 gain while VO plays at 2.3× (≈+7 dB). Dropout transition is zero (no silence gap).

### Music bed

- User provides a folder via the dashboard (`CLIP_MUSIC_BED`). Per-clip selection runs `scripts/lib/music_pick.py`.
- **Tier A** (default): folder convention. Subfolders named after categories (`hype/`, `funny/`, `emotional/`, `storytime/`, `neutral/`) are preferred; else random pick from the whole folder. Deterministic seeded by timestamp.
- **Tier C** (opt-in via `CLIP_MUSIC_TIER_C=true`): reads a `music_library.json` sidecar produced by `scripts/lib/scan_music.py` ([[entities/librosa]] feature extraction — tempo, RMS energy, spectral centroid, duration). Scores every track against a category-target profile and picks randomly from the top 3 closest matches.
- **Dashboard button** *Scan Music* triggers the scanner endpoint `POST /api/music/scan`. Runs inside the container when invoked from the Windows host (where librosa isn't installed).
- Mixed at ~−22 dB under the streamer audio and VO, looped across the clip with `stream_loop -1` so short tracks still fill the window.

---

## Wave E — Active-speaker camera pan

Runs as a conditional Stage 6.5 when `CLIP_CAMERA_PAN=true` **and** `CLIP_FRAMING=camera_pan`.

Pipeline (`scripts/lib/face_pan.py prepare`):

1. OpenCV `VideoCapture` opens the VOD. For each 0.5 s sample across the clip window, read the frame and run Haar cascade face detection.
2. Pick the tracked face: nearest to the previous center for continuity, except on a 4-second cycle it rotates to the next-largest detected face — this reality-TV swing between speakers is the fingerprint-breaker.
3. EMA-smooth the crop-box center (α=0.30) so the pan looks intentional, not jittery.
4. Emit a JSON keyframe list to `/tmp/clipper/clip_<T>_campath.json`.

Render-time (`scripts/lib/face_pan.py --emit-filter <path>`):
- Builds a piecewise-linear FFmpeg `crop=w:h:x='<nested if()>':y='<nested if()>'` expression across up to 32 anchor keyframes, followed by `scale=1080:1920:flags=lanczos`.
- Stage 7 interpolates this string into its `camera_pan` case.

Fallbacks:
- Haar cascade missing → returns rc=1, clip falls back to `blur_fill`.
- Source already portrait → rc=2, nothing to do.
- Zero faces across the entire clip → rc=3, clip falls back to `blur_fill`.

Cost: +2–4 s CPU per clip for detection, +1–2 s for the per-frame-expression render. Requires `opencv-python-headless` (added in the [[entities/dockerfile]]).

---

## Dashboard controls

The **Originality & Render** panel (added to [[entities/dashboard]]) exposes every flag above. Selections persist to `config/originality.json` via `PUT /api/originality` on every change, and are forwarded as `CLIP_*` env vars through `spawn_pipeline` for both direct and Docker-exec invocations.

## Related
- [[concepts/clipping-pipeline]] — Stage 4.5 and Stage 6.5 additions
- [[concepts/clip-rendering]] — framing-mode filter chains, stitch concat, audio mix
- [[concepts/vision-enrichment]] — chrome_regions, mirror_safe, voiceover fields
- [[entities/piper]] — TTS voice
- [[entities/librosa]] — tier-C music feature extraction
- [[entities/face-pan]] — OpenCV face tracking helper
- [[entities/dashboard]] — Originality panel
- [[entities/ffmpeg]] — new filter graphs
