---
title: "AI Editing Profiles (Per-Category)"
type: concept
tags: [editing, profiles, style, originality, fingerprint, kinetic-captions, sfx, broll, memes, zoom-punch, freeze-frame, slow-mo, chat-overlay, stage-7]
sources: 0
updated: 2026-05-02
---

# AI Editing Profiles

A new render mode that swaps Stage 7's uniform blur-fill+originality look for **per-category editing templates**. Each clip category (hype/comedy/skill/reactive/controversy/emotional/storytime/irl/dancing/hot_take) drives a different mix of zoom punches, freeze frames, slow-mo (FPS-gated), meme cutaways, B-roll inserts, SFX cues, kinetic captions, and audio + container fingerprint perturbation.

Goal: **add "life" to every clip while satisfying TikTok's transformative-remix criterion** so finished clips are unlikely to trip the unoriginal-content classifier.

Toggled by `chk-style-profiles` (Originality panel, default OFF). When OFF, the legacy blur-fill renderer in [[concepts/clip-rendering]] runs unchanged.

---

## How it fits into the pipeline

```
Stage 6 (vision) → moments[*].category, .edit_plan?, .hook, .title, ...
                   │
                   ▼
Stage 7 render loop
   │
   ├── if CLIP_STYLE_PROFILES=true:
   │     write moment_<T>.json
   │     python scripts/lib/profile_render.py --moment-json ...  ←── this concept
   │       resolves category → profile (style_profiles.py)
   │       normalizes + synthesizes edit_plan (edit_plan.py + synthesizer)
   │       builds FFmpeg filter graph + amix layers
   │       runs FFmpeg with -map_metadata -1 + GOP/CRF jitter
   │     on success: continue to next clip
   │     on failure: warn + fall through to legacy
   │
   └── legacy render path (blur_fill + originality + Whisper subs)
```

Stage 6 is **not** modified — to avoid the HTTP 400 cascades we hit when stuffing the vision prompt before. The renderer **synthesizes** zoom punches and SFX cues from the resolved profile when no `edit_plan` field is present on the moment, so every clip gets meaningful effects regardless of vision behavior.

---

## Files

```
scripts/lib/
├── style_profiles.py     ← per-category templates + fingerprint params
├── edit_plan.py          ← Stage-6 edit-plan JSON schema + validator
├── profile_render.py     ← orchestrator (called by stage7_render.sh)
├── zoom_punch.py         ← split + scale + overlay enable= filter emitter
├── freeze_frame.py       ← split + trim + loop + concat for mid-clip freeze
├── slow_mo.py            ← FPS gate (≥50) + setpts/atempo segment
├── sfx_inject.py         ← amix layer builder, picks SFX from manifests
├── kinetic_captions.py   ← Whisper SRT → karaoke ASS, 5 presets
├── meme_pick.py          ← assets/memes/<cat>/library.json reader
├── broll_pick.py         ← assets/broll/<cat>/library.json reader
└── chat_overlay.py       ← Pillow chat-strip PNG renderer (Phase 5)

assets/caption_styles/
├── neon.ass.tpl          ← Komika Axis / cyan active word / scale-pop
├── bouncy.ass.tpl        ← yellow active / scale-pop
├── clean.ass.tpl         ← Arial / no color shift
├── news.ass.tpl          ← red active / 3-px shadow border
└── soft.ass.tpl          ← Helvetica / pale-yellow active

scripts/stages/stage7_render.sh   ← dispatch added inside per-clip loop
scripts/clip-pipeline.sh          ← CLIP_STYLE_PROFILES env var registered
dashboard/_state.py               ← DEFAULT_ORIGINALITY["style_profiles"]=False
dashboard/config_io.py            ← env mapping + payload extraction
dashboard/templates/index.html    ← chk-style-profiles + Scan Libraries
dashboard/static/modules/pipeline-ui.js  ← collectOriginality + scanLibraries
dashboard/static/app.js           ← scanLibraries on window
dashboard/routes/library_routes.py       ← /api/libraries/scan
```

---

## Profile templates (scripts/lib/style_profiles.py)

| Category | zoom_punch | freeze | slow_mo | meme | b-roll | mirror | shake | vignette | sfx_on_cuts | sfx_on_peak | caption preset | music | chat |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **hype** | 2–3 | 0% | 0% | 0% | 0% | 50% | 40% | 10% | whoosh, impact | impact, riser | neon | hype | – |
| **comedy** | 1–2 | 85% | 0% | 75% | 10% | 20% | 20% | 20% | scratch, ding | scratch | bouncy | funny | – |
| **skill** | 1–2 | 10% | 85% | 0% | 0% | 10% | 20% | 10% | impact, riser | impact | clean | hype | – |
| **reactive** | 1–2 | 30% | 0% | 20% | 0% | 50% | 25% | 15% | whoosh, ding | impact | bouncy | reactive | – |
| **controversy** | 1–2 | 50% | 0% | 0% | 10% | 0% | 10% | 30% | impact | riser | news | tension | yes |
| **hot_take** | 1–2 | 50% | 0% | 0% | 0% | 0% | 10% | 30% | impact | riser | news | tension | yes |
| **emotional** | 0–1 | 0% | 0% | 0% | 0% | 0% | 0% | 60% | – | – | soft | emotional | – |
| **storytime** | 0–1 | 10% | 0% | 0% | 85% | 0% | 0% | 20% | whoosh | – | clean | storytime | – |
| **irl** | 1–2 | 10% | 0% | 10% | 50% | 20% | 15% | 15% | whoosh, ding | – | clean | reactive | – |
| **dancing** | 3–5 | 0% | 0% | 0% | 0% | 50% | 30% | 10% | – | – | neon | hype | – |

Every probabilistic / range field resolves per-clip via a deterministic seed (the moment timestamp) — same category + same seed always produces the same effect set, but two same-category clips never render identically.

`get_profile()` returns the resolved dict; `fingerprint_params()` returns the always-on perturbation knobs.

---

## Edit-plan JSON schema (scripts/lib/edit_plan.py)

When Stage 6 emits an `edit_plan` field on a moment, the renderer normalizes + uses it. Fields the LLM omits are filled from the resolved profile. Schema:

```json
{
  "profile":          "comedy",
  "zoom_punches":     [{"t": 3.2, "scale": 1.15, "hold": 0.30}, ...],
  "freeze_at":        {"t": 4.2, "duration": 0.5},
  "slow_mo":          {"start": 6.8, "end": 7.6, "rate": 0.5},
  "meme_cutaway":     {"t": 8.0, "tag": "laugh", "duration": 1.0},
  "broll_inserts":    [{"t": 9.5, "noun": "rocket", "duration": 1.5}, ...],
  "sfx_cues":         [{"t": 3.2, "kind": "scratch"}, ...],
  "caption_emphasis": [3, 12, 18],
  "caption_preset":   "bouncy",
  "chat_overlay":     true
}
```

The validator coerces lone numbers to `t`-only objects, drops unknown SFX kinds and unknown presets, clamps numeric ranges. Anything missing falls back to profile defaults.

---

## Plan synthesis (when no `edit_plan` on the moment)

`profile_render._synthesize_plan()` runs whenever the moment has no explicit edit_plan. It fills in:

- **zoom_punches**: `profile.zoom_punch_count` distributed evenly across the clip, ±0.15 s jitter, avoiding a 0.8 s buffer at start/end.
- **sfx_cues**: one cue per zoom punch (kind randomly drawn from `profile.sfx_on_cuts`), plus one mid-clip peak (`profile.sfx_on_peak`).
- **caption_preset**: from `profile.caption_preset` if not already set.

Other fields (freeze, slow-mo, meme, B-roll) are NOT synthesized — they need specific content cues from vision (or from the user via the Stage-6 prompt-extension followup) to be meaningful. They render only when `edit_plan` explicitly contains them.

---

## Slow-mo FPS gate (scripts/lib/slow_mo.py)

Slow-mo only renders cleanly on ≥50 fps source. Below the threshold (Twitch's 30/60-fps mix sometimes lands on 30) the helper auto-downgrades to a zoom punch centered on the slow-mo midpoint:

```
plan_slow_mo(...) →
  {"mode": "slow_mo", "fragment": "<filter>", "out_label": "..."}
  OR
  {"mode": "downgrade", "reason": "...", "zoom_punch": {"t": ..., ...}}
```

`profile_render` checks the mode and either splices the slow-mo filter or appends the downgrade zoom punch to the clip's punches list. Source FPS is probed once per clip via ffprobe.

---

## Audio + container fingerprint perturbation (always-on when profile mode is on)

Every clip in profile mode gets:

| Knob | Range | How |
|---|---|---|
| pitch_cents | ±2 – 5¢ | `rubberband=pitch=...` (sub-perceptual) |
| eq_tilt_db | ±0.2 – 0.6 dB at ~3 kHz | `firequalizer` (currently passed via fingerprint dict; firequalizer wiring deferred) |
| GOP | 240 – 360 frames | `-g <N>` |
| CRF jitter | ±1 around base 20 | `-crf <18..21>` |
| metadata strip | always | `-map_metadata -1 -fflags +bitexact` |
| encoder token | random | `-metadata comment=oc<hex>` |

These perturbations break the per-bit fingerprint TikTok / Reels use to flag re-uploaded content. They're seeded deterministically per clip.

> [!note] firequalizer not yet wired
> The pitch jitter and metadata strip are active. The firequalizer EQ tilt is computed but not yet inserted into the audio chain — minor follow-up. Container GOP/CRF jitter, pitch jitter, and metadata strip are the most impactful of the five and are working.

---

## Chat overlay (Phase 5 — controversy/hot_take)

When `profile.chat_overlay` is True, `chat_overlay.py` searches for a chat dump (`$TEMP_DIR/chat.json`, `<vod>.chat.json`, Chatty `.chatty.txt`, or `$CHAT_PATH`), loads messages within the clip window, and renders them into a vertical PNG strip via Pillow. The renderer overlays the PNG on the right column of the clip. When chat data or Pillow are unavailable the layer is skipped silently — the rest of the profile still renders.

---

## Dashboard surface

Single checkbox in the Originality panel: **"AI editing profiles (per-category)"**. Default OFF.

A second button: **"Scan Libraries"** — runs `scripts/seed_libraries.py --scan` to rebuild `library.json` files under `assets/` after the user drops in their own SFX / memes / B-roll / music. Backed by `/api/libraries/scan` (`dashboard/routes/library_routes.py`).

Both persist via the existing `/api/originality` PUT endpoint (`originality.json` on disk).

---

## Filter graph (one clip in profile mode)

The actual FFmpeg `filter_complex` chain built by `profile_render`:

```
[0:v] setpts/null
      , split[bg][fg]
      ; [bg] scale,crop,boxblur [blurred]
      ; [fg] scale,hflip-or-null [sharp]
      ; [blurred][sharp] overlay,eq[v_base]
[v_base] vignette [v_vig]                    (if profile.vignette_prob)
[v_vig]  crop+sin/cos shake [v_shake]        (if profile.shake_prob)
[v_shake] (slow-mo concat)... [v_slow]       (if slow_mo + fps≥50)
[v_slow]  (zoom-punch overlay) [v_zoom]      (always when zps>0)
[v_zoom]  (freeze concat) [v_freeze]         (if freeze_at)
[v_freeze](meme overlay 300×, top-right) [v_ov_*]
          (broll overlay 420×, lower third)
          (chat overlay 320×, right column)
[v_*]     drawtext hook [v_hook]
[v_hook]  subtitles=ASS [v_caps]
[v_caps]  null [vout]

audio:
[0:a] rubberband(tempo=speed,pitch=jitter) [src_audio]
[1:a] adelay,volume [sfx0]
[2:a] adelay,volume [sfx1]
[N:a] atrim,volume=0.10 [music_audio]
[src_audio][sfx0][sfx1]...[music_audio]
   amix=inputs=N:normalize=0 [a_mixed]
[a_mixed] volume=0.95 [aout]
```

Output: 1080×1920 H.264 CRF 19–21 (jittered), AAC 192 kbps. 4-px crop shrinkage when shake is on (matches existing legacy behavior).

---

## Smoke-tested

End-to-end profile_render against a synthetic 30-s test source (lavfi `testsrc` + `sine`) produced a 3.1 MB MP4 with:
- Comedy category → 2 zoom punches + 3 SFX cues + bouncy preset, hook overlay
- 1080×1920 → 1076×1916 (shake on)
- Exit code 0, output passes ffprobe

A second hype-profile run produced 3 zoom punches + 4 SFX cues + neon preset → also 0.

---

## Asset dependencies

The profile renderer consumes:

- `assets/sfx/<kind>/library.json` ← seeded by [[concepts/asset-libraries]]
- `assets/music/<category>/library.json` ← seeded
- `assets/memes/<category>/library.json` + `generic/` ← seeded with Twemoji
- `assets/broll/<sub>/library.json` ← seeded with 9 air-travel snippets
- `assets/caption_styles/*.ass.tpl` ← shipped (5 presets)

Missing assets degrade silently — e.g., empty meme library → no cutaway, empty B-roll → no insert, empty music folder → no music bed.

---

## Open follow-ups

1. **Stage 6 prompt extension** — currently the `edit_plan` field is synthesized by the renderer when vision doesn't supply one. A future change can extend the Stage 6 vision prompt to ask for an edit_plan, giving vision-aware effects like punchline-targeted freeze frames and noun-grounded B-roll. Skipped for now to avoid the HTTP 400 cascades documented in BUG 51.
2. **firequalizer EQ tilt** — `fingerprint_params` computes the tilt; wiring into the audio chain is a one-line `firequalizer=...` insertion. Pitch jitter + container fingerprint already cover most of the fingerprint surface.
3. **Pillow** — `chat_overlay.render_overlay_mp4` no-ops when Pillow is missing. Add Pillow to the container `requirements.txt` whenever Phase 5 is fully enabled.

---

## Related

- [[concepts/asset-libraries]] — CC0 asset seed pack feeding the profile renderer
- [[concepts/clip-rendering]] — Stage 7 legacy renderer (used when the toggle is off)
- [[concepts/originality-stack]] — Wave A–E originality plan (profiles overlap with Wave A but extend with editing-style fingerprint)
- [[concepts/captions]] — existing Whisper subtitle burn-in (replaced by kinetic ASS when profiles are on)
- [[concepts/bugs-and-fixes]] — BUG 51 context for why Stage 6 wasn't extended
