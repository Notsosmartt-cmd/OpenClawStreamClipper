---
title: "Captions and Hook Text"
type: concept
tags: [captions, subtitles, hook, rendering, ffmpeg, originality, stage-7, video]
sources: 2
updated: 2026-06-06
---

# Captions and Hook Text

The two text overlay layers burned into every rendered clip. Both are drawn by [[entities/ffmpeg]] as part of the Stage 7 filter chain. Detail on the surrounding render pipeline: [[concepts/clip-rendering]].

> [!note] Hook-text + density guidance (2026-06-12 research)
> [[concepts/hook-engineering-2026-06]] gives the evidence-backed rules for the **hook card**: keep on-screen text ≤ **5–10 words/sec** (TikTok-official), make the clip's proposition **readable by the 3 s mark** (hook by 6 s), and fill the hook line from a **category-keyed template library** (storytime / informative / hot-take / authority / funny). The cold-open teaser that would precede the hook card is planned in [[concepts/plan-unoriginality-audio-layer]] P2.

---

## Subtitle style — CapCut word-box captions (default, 2026-06-06)

The default caption is a **CapCut-style word box**: a short phrase is on screen in bold white text and the **currently-spoken word sits in a colored (yellow) box that advances word-by-word**. Built by `scripts/lib/kinetic_captions.py` (`render_box`) → an ASS file → burned by FFmpeg's `subtitles` filter.

**Look** (see the sample frame in [[concepts/clip-rendering]]):
- **Font**: **Montserrat Black**, bundled at `assets/fonts/Montserrat-Black.ttf` (OFL; license alongside). Burned via `subtitles=…:fontsdir=assets/fonts` so libass uses it even though it isn't installed system-wide — verified `fontselect: (Montserrat Black, 700, 0) -> Montserrat-Black`.
- **Base words**: white fill, **black outline** (`BorderStyle=1, Outline=4`) + soft shadow — readable on any background.
- **Active word**: a second ASS style **`Box`** (`BorderStyle=3` opaque box) in the accent color with dark text; the renderer switches to it for the current word via the `{\rBox}word{\r}` inline tag. The box advances word-by-word because each word gets its own `Dialogue` event tiling the phrase's time span with no gaps.
- **Case**: sentence case (default) — `CLIP_CAPTION_CAPS=true` for ALL CAPS.
- **Position**: bottom-center, `MarginV≈220` (lower third); font size 84 at 1080×1920.
- **Grouping**: ~3 words per phrase line.

**Env dials** (read by `stage7.py` and `profile_render.py`):
- `CLIP_CAPTION_PRESET` (default `capcut`) — set to a legacy preset (`neon`/`bouncy`/`clean`/`news`/`soft`) for the old word-reveal styles instead.
- `CLIP_CAPTION_ACCENT` (default `yellow`) — `yellow`/`green`/`red`/`pink`/`orange`/`cyan`/`white` or an `RRGGBB` hex.
- `CLIP_CAPTION_CAPS` (default `false`).

Both render paths use it: the **solo** path (`stage7.py`) and the **AI-editing-profiles** path (`profile_render.py`) both call `kinetic_captions` with the bundled font + `fontsdir`.

> [!note] Four bugs this fixed (2026-06-06)
> The captions looked bad before for concrete reasons, all now fixed:
> 1. **No bundled font** — presets named `Komika Axis`/`Arial Black`/`Helvetica` which aren't installed → libass fell back to an ugly default face. Now Montserrat Black ships in `assets/fonts` and is passed via `fontsdir`.
> 2. **Solo path never used the karaoke engine** — `stage7.py` burned the raw SRT with a flat `force_style` (no word-by-word, no animation). The `kinetic_captions.py` engine only ran when AI-editing-profiles was on. Now both paths use it.
> 3. **The SRT wasn't actually word-level** — `stage7_transcribe.py` set `word_timestamps=True` but wrote one block per *segment* (whole sentences), ignoring `seg.words`. Now it emits one SRT block per word (segment fallback when a segment lacks word timing).
> 4. **Preset styling** didn't match CapCut. New `capcut` preset built from scratch.

### Legacy flat burn (fallback only)

If ASS generation returns non-zero (e.g. an empty SRT), `stage7.py` falls back to the old flat burn: `subtitles=SRT:force_style='FontSize=…,Bold=1,PrimaryColour=…,OutlineColour=…,Outline=…,Alignment=2,MarginV=…'`. The Wave-A 5-variant `force_style` palette rotation in `scripts/lib/originality.py` (`SUB_*` vars, seeded by the moment timestamp) now applies **only** to this fallback.

### Caption toggle

Captions can be disabled via the **Clip Controls** panel in [[entities/dashboard]] (Captions checkbox, on by default). When disabled, the subtitle filter is skipped entirely.

Implemented via `CLIP_CAPTIONS` env var (`true`/`false`). The pipeline reads `CAPTIONS_ENABLED="${CLIP_CAPTIONS:-true}"` at startup; when `false`, `RENDER_VF` is set to the blur/framing filter only.

---

## Hook caption (top-of-video title card)

A punchy AI-generated one-liner displayed at the **top** of the video in a styled box — the TikTok/Reels hook-card pattern.

### Where it comes from

Stage 6 vision enrichment prompts the model for a `hook` field: *"punchy 1-line hook, max 8 words, written in the voice and slang of a `{stream_type}` content creator, no hashtags"*. Stored in `scored_moments.json`, written into the clip manifest.

If vision enrichment fails, the clip title is used as fallback.

### FFmpeg filter

```
drawtext=textfile='…/clip_{T}_hook.txt'
  :fontsize=<42–52, per-clip>
  :fontcolor=<palette fg: white|black>
  :fontfile=assets/fonts/Montserrat-Black.ttf      # bundled, matches captions
  :borderw=<0|4|5>:bordercolor=<palette>@0.9        # contrast-aware outline
  :box=1:boxcolor=<palette box>:boxborderw=<22–26>
  :x=(w-text_w)/2 :y=<45–120> :line_spacing=8
```

The hook text is written to a per-clip temp file (avoids shell quoting issues with apostrophes). `textwrap.wrap(hook, 18)` wraps to max 3 lines (tightened from 22 — Montserrat Black is wider).

> [!note] Font — Montserrat Black (2026-06-06)
> The hook card now uses the **bundled `assets/fonts/Montserrat-Black.ttf`** (same face as the CapCut subtitle captions), resolved by `stage7._resolve_font()` and `profile_render._resolve_hook_font()` with installed-bold fallbacks (Segoe UI Black → Arial Bold → DejaVu). This fixed a real bug: `profile_render.py` hard-coded the **Linux** path `/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf`, which doesn't exist on the Windows bare-metal host, so the hook silently fell back to an ugly default face. (`fonts-dejavu-core` is still only relevant to the in-Docker render path.)

### Per-clip palette randomization (Wave A)

When `CLIP_ORIGINALITY=true`, hook rendering varies per clip (`scripts/lib/originality.py`):
- **6 box/text combinations**: white text on dark box, black on white/yellow/teal, white on pink, etc.
- **Contrast-aware text outline** (2026-06-06): white text → black outline (4–5 px, crisp like the captions); black text → **no outline** (the box already gives contrast; a black outline on black text just muddies the glyphs). Emitted as `HOOK_BORDER_COLOR` / `HOOK_BORDER_W`.
- **Y position**: 45–120 px (randomized)
- **Font size**: 42–52 pt (randomized; bumped from 36–46 for more punch)

### Toggle

Controlled by `CLIP_HOOK_CAPTION` env var (default `true`). Dashboard "Hook caption" checkbox. Passes through `app.py → spawn_pipeline()` the same way as captions and speed.

---

## Related
- [[concepts/clip-rendering]] — Stage 7 render pipeline that applies these filters
- [[concepts/originality-stack]] — Wave A randomization of palette / position
- [[concepts/vision-enrichment]] — Stage 6 that generates the hook text
- [[entities/faster-whisper]] — generates word-level SRT for subtitles
- [[entities/ffmpeg]] — executes the drawtext and subtitle filters
