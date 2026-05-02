---
title: "Speed Control"
type: concept
tags: [speed, rendering, ffmpeg, rubberband, audio, subtitles, dashboard, stage-7, video, interface]
sources: 2
updated: 2026-04-25
---

# Speed Control

Per-clip playback speed applied during Stage 7 rendering. Controlled from the [[entities/dashboard]] Clip Controls panel via a dropdown. Detail on the surrounding render pipeline: [[concepts/clip-rendering]].

---

## Speed values

| Setting | Effect |
|---|---|
| `1×` (default) | No speed change |
| `1.1×` | Barely perceptible; good for padding slow moments |
| `1.25×` | Noticeable but natural-feeling — sweet spot for just-chatting clips |
| `1.5×` | Aggressive; best for very slow speakers or filler-heavy segments |

---

## How it works

### Video

`setpts=PTS/N` is prepended to the blur-fill filter chain so both background and foreground branches receive adjusted presentation timestamps. The `-t CLIP_LENGTH` input seek flag still controls how much source material to read; output duration = `CLIP_LENGTH / speed`.

### Audio

`rubberband=tempo=N:pitch=N` — pitch and tempo are both set to the same speed ratio. The Rubber Band Library does high-quality time-stretching. Because pitch rises in proportion to speed, the result sounds like a natural fast-talker rather than either a chipmunk (pitch too high vs tempo) or a slowed-down voice (pitch=1.0).

`rubberband` is compiled into Ubuntu 22.04's `apt install ffmpeg` (`--enable-librubberband`) — no special build required.

### SRT subtitle rescaling

When speed ≠ 1.0, all SRT timestamps are divided by the speed factor via `rescale_srt()` before the render command runs. This keeps subtitles in sync with the sped-up video. A per-clip `clip_${T}_scaled.srt` is written to `$TEMP_DIR`; the original is left untouched.

---

## Dashboard control

Passed through `app.py` → `spawn_pipeline()` as the `CLIP_SPEED` env var. Default `1.0`. The dropdown updates on apply — no page reload needed.

---

## Related
- [[concepts/clip-rendering]] — Stage 7 render pipeline that applies speed
- [[concepts/captions]] — SRT subtitle style; rescaling is applied here before render
- [[entities/ffmpeg]] — executes `setpts` and `rubberband` filters
- [[entities/dashboard]] — exposes the Speed dropdown in Clip Controls
