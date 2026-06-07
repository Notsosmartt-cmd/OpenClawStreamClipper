---
title: "Transition Animations (jump-cuts + white flashes)"
type: concept
tags: [rendering, transitions, jump-cut, flash, edit-plan, stage-7, ffmpeg, originality, engagement]
sources: 0
updated: 2026-06-06
---

# Transition Animations

Two TikTok-style editing effects, both optional, flag-gated, and failure-soft:

1. **White flashes** — brief, TRANSIENT white pops (full-frame `drawbox` gated by `enable='between(t,a,b)'`, rise→peak→fall over ~0.16 s) layered on a clip for engagement / pattern-interrupt. No content removed. **Must NOT use `fade=…:color=white`** — `fade` holds the colour outside its ramp window and paints the whole clip white (BUG 64).
2. **Jump-cuts** — DROP spans of dead air / rambling and concatenate the kept spans with `xfade=fadewhite`, so the clip skips to the payoff faster. The white fade masks each cut.

Both are driven by the per-moment **`edit_plan`** ([[concepts/captions]] neighbours; schema in `scripts/lib/edit_plan.py`) plus rule-based generators, and applied by a single Stage-7 post-pass.

---

## Architecture (the key decision: post-render pass)

Transitions run as **Stage 7d.5**, a pass over the **finished clip files**, AFTER the normal render (`stage7._render_clip` solo path or `profile_render.py` profile path) and BEFORE 7e stitch. This one decision buys three things:

- **Path-agnostic** — works for both render paths without touching either filter graph.
- **Caption-safe** — captions/effects are already burned (pixels) by 7d.5, so cutting the clip cuts the captions *with* it. **No SRT remap** of the live timeline is needed — the hardest part of the original plan is sidestepped entirely.
- **Decoupled** — all the logic lives in one new, fully unit-tested module (`scripts/lib/clip_cuts.py`); the complex `profile_render` chain is untouched.

The pass is in `scripts/pipeline/stages/stage7.py` (`run()`, section `7d.5`), wrapped in try/except + per-clip guards, so any failure leaves the original clips untouched.

```
7d render (solo|profile) → finished clips → 7d.5 transitions (per clip) → 7e stitch
                                              │
            clip_cuts.process_clip_transitions(clip_path, …) in place via temp
```

---

## `clip_cuts.py`

Pure, testable helpers (`python clip_cuts.py --selftest`):

| Function | Role |
|---|---|
| `compute_keep_spans(cuts, start, end, boundaries, …)` | drop-spans → ordered KEEP spans; snaps cut edges to transcript pauses, protects the tail (payoff), caps total drop fraction (drops the *longest* dead spans first), skips sub-`min_keep` slivers |
| `remap_time` / `remap_srt` | map absolute VOD time → compressed timeline (kept for completeness / future live-timeline use) |
| `gaps_to_cuts(segments, …)` | rule-based: drop SILENCES (gaps between speech) — the safest cut |
| `flash_cadence(start, end, seed)` | rule-based: deterministic engagement flashes at a seeded cadence (no LLM) |
| `apply_transitions(in, out, keep_rel, flash_rel)` | the FFmpeg: `trim`+`xfade=fadewhite` for cuts, **transient `drawbox`+`enable`** white pops for flashes (`white_flash_boxes`), NVENC via `venc.py` |
| `process_clip_transitions(clip_path, …)` | orchestrator entry: combine rule + LLM picks, apply in place, return True if modified |

**Verified** with FFmpeg render tests: a 12 s clip with a 3–6 s drop renders to exactly 7.70 s with the content jump + white fade at the join. The flash is checked with a **control frame** — before-flash `YAVG=122` (normal), flash `209` (bright), after-flash `121` (normal) — i.e. ONLY the flash window is white (the original chained-`fade` flash was white *everywhere*; see [[concepts/bugs-and-fixes]] BUG 64).

---

## Model inference

The cut decision ("which rambling to drop") is a transcript-reasoning task, so it's added to the **Stage 6 vision call**, which already sees the clip's transcript. When a transition mode is on, the prompt is given the **absolute-timestamped** transcript and asked for `cuts` / `flashes` (stored on `edit_plan`). **Gated**: when the flags are off (default), the prompt is byte-identical to before — zero risk to normal runs. The validator (`edit_plan.normalize`) + the keep-span caps tolerate sloppy model output.

Crucially, the **rule-based modes work without the LLM**: `gaps` (silence-drop) and the flash cadence need only the transcript, so the feature is useful immediately and degrades gracefully.

---

## Modes & policy

| Env var | Values | Meaning |
|---|---|---|
| `CLIP_JUMP_CUTS` | `off` (default) · `gaps` · `llm` · `on` | `gaps`=drop silence only (safe); `llm`=model-inferred cuts; `on`=both |
| `CLIP_FLASH_CUTS` | `off` (default) · `on` | seeded cadence flashes + any model-picked beats |

Dashboard toggles (Originality & Render panel): **"White-flash transitions"** checkbox + **"Jump-cut compression"** select (Off / Silence only / Smart + silence) → `config_io.originality_to_env`.

**Guardrails** (so a cut can't wreck a clip): per-category max-drop fraction (`clip_cuts.CATEGORY_MAX_DROP` — storytime/informational 0.5, hype 0.25, …), a protected tail (`GUARANTEE_TAIL` 2 s — never cut the payoff), `MIN_KEEP` 1.5 s slivers skipped, cut edges snapped to natural pauses, flashes capped at 6/clip with a min spacing.

---

## Cost & limitations

- One extra NVENC re-encode per modified clip (~3–5 s each, only when enabled). Runs sequentially after the parallel render loop.
- The recorded/summary duration is the pre-cut estimate (the clip file is correct; the logged number is cosmetic).
- **Future (P2 deferred):** a dedicated text edit-pass (separate LLM call over the full clip transcript) would reason about cuts better than folding it into the vision call. The current Stage 6 route + rule-based gaps are sufficient for v1.

> [!note] Needs a validation run
> All layers are unit/FFmpeg-tested in isolation, but the full Stage-6→7 path needs an end-to-end run with a flag on (`CLIP_JUMP_CUTS=gaps` is the safest first try) to confirm in production. Default-off, so normal runs are unaffected.
