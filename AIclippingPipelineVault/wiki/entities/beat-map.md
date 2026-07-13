---
title: "beat_map.py — shared acoustic/transcript timing primitives"
type: entity
tags: [beat-map, sfx, jump-cut, timing, audio, rms, stage-7, shared]
sources: 0
updated: 2026-07-13
---

# beat_map.py

`scripts/lib/beat_map.py` — the tuned beat-detection primitives shared by the **SFX
placer** (`sfx_cues.py`) and the **jump-cut compressor** (`clip_cuts.py`). Extracted
2026-07-13 (jump-cuts-v2 phase J0, [[concepts/plan-jump-cuts-v2-2026-07]]) so cuts stop
snapping only to Whisper segment edges and instead reuse the same owner-tuned timing the
sound effects already use. Failure-soft throughout (safe fallback / `[]` on any error).

## Why it exists

Before J0, every placement-timing iteration the owner tuned (the "effects came in too
early" fix, the Hot-Cheeto payoff rescue, the Shower-Bluff after-line shift, the
secondary-peak scanner) lived inside `sfx_cues.py` and was invisible to the cutter. The
cutter therefore had no idea where the real payoff was or which "silence" was actually a
comedic beat. beat_map is the single source of truth both consume.

## API

| Function | Returns | Used for |
|---|---|---|
| `refined_payoff(payoff_rel, clip_start, dur, temp_dir, …)` | refined payoff (s) | SFX payoff anchor; cut **payoff halo** |
| `laughter_times(temp_dir, start, end)` | absolute laughter-marker times | SFX punchline; cut **veto** |
| `prominent_transients(start, dur, temp_dir, …)` | strong RMS-flux times | SFX secondary hits; cut **veto** (real action ≠ dead air) |
| `breath_points(temp_dir, start, dur, …)` | sustained RMS-dip times | cut **edge snapping** (finer than segment edges) |
| `build(temp_dir, start, dur, moment)` | `{payoff_rel, laughter_rel, transient_rel, breath_rel}` | one-call beat map for `clip_cuts` |

`refined_payoff` / `prominent_transients` are the DSP bodies moved **verbatim** from
`sfx_cues.py`; `sfx_cues._refine_payoff` / `_laughter_times` / `_secondary_peaks` now
delegate (reading the config knobs and threading them through), so SFX behavior is
**byte-identical** — the sfx cue output on a fixed input is the extraction gate
(`beat_map.py --selftest` asserts the delegation). `breath_points` + `build` are new (J0).

## Related
- [[concepts/sfx-cue-taxonomy-2026-06]] — the SFX side that these primitives were extracted from
- [[concepts/plan-jump-cuts-v2-2026-07]] — the cut side that now reuses them
- [[concepts/transition-animations]] — where the cut guards (halo/veto/breath) are applied
