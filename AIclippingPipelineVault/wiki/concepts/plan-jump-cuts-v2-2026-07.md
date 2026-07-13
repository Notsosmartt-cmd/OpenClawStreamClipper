---
title: "Plan: jump cuts v2 — beat-aware smart+silence (unify cut timing with the SFX/VFX beat machinery)"
type: concept
tags: [plan, jump-cut, transitions, sfx, beat-map, stage-7, rendering, llm, categories]
status: shipped
updated: 2026-07-13
---

# Plan: jump cuts v2 — beat-aware smart+silence

> [!note] SHIPPED 2026-07-13 (J0–J6 built + unit/integration-tested; global default still OFF pending the owner's live gaps run)
> All six phases landed in one session. What's live (all failure-soft, kill-switch = shipped v1 behavior):
> - **J0** `scripts/lib/beat_map.py` — the tuned SFX timing primitives (`refined_payoff`,
>   `laughter_times`, `prominent_transients`, NEW `breath_points`, `build`) extracted from
>   `sfx_cues.py`; sfx_cues now DELEGATES (byte-identical — its cue output is the gate, verified).
> - **J1** `clip_cuts.py`: refined-payoff no-cut **halo** (`CLIP_CUT_PROTECT_PAYOFF_S`=2.0),
>   laughter/transient **veto** + **leave-a-beat** (0.45 s residual), **effect-aware joins**
>   (`JOIN_CLEAR` halo round placed SFX + `sfx_cues_remapped`/`sfx_cues_dropped_by_cut` in the
>   effects log so the Lab ground truth stays true), **category fixes** (`CATEGORY_MAX_DROP`
>   gained controversial 0.25 + dancing 0.0, emotional 0.40→0.20; NEW `CATEGORY_CUT_POLICY`:
>   dancing=off, controversial+emotional=silence-only), breath-point edge snapping. `compute_keep_spans`
>   gained `protect_spans` (interval-subtracted) + `veto_times`.
> - **J2** `scripts/lib/cut_inference.py` — text-only micro-call: the model QUOTES verbatim
>   substrings, mapped to time deterministically (char-interp, self-verifying). Replaced the
>   Stage-6 vision-prompt cuts field (`stage6_vision.py`; the mega-prompt now infers flashes only).
> - **J3** coherence gate (payoff content-words must survive) + optional LLM fidelity judge
>   (`CLIP_CUT_JUDGE`) — both in `cut_inference`, run in Stage 6 where the model is loaded.
> - **J4** `CLIP_CUT_STYLE=auto|hard|fadewhite` (default **auto** = hard cuts + alternating ±5%
>   punch-in via concat, no white-flash template tell); `_build_filter`/`apply_transitions` rewritten.
> - **J5** `CLIP_CUT_FILLERS` (default off) pause-adjacent filler micro-lane (`cut_inference.filler_cuts`
>   + `load_word_srt` off the per-clip word SRT).
> - **J6** `CLIP_AB_CUTS_EXPERIMENT` (Stage 7d.6 — compress the **B** variant only → labelable
>   A-vs-B pair); Lab `duration_med` metric promoted in `corpus_diff.py`; dashboard **Editing style**
>   dropdown + rewritten jump-cut tooltip.
>
> **Deviations from the plan (deliberate):**
> - **`CLIP_CUT_JUDGE` defaults OFF**, not "on when llm" — the judge would run in Stage 7 where the
>   model is UNLOADED (a ~1.5–2 min reload per call). The judge instead lives in `cut_inference`
>   (Stage 6, model loaded) and is opt-in; the **deterministic** coherence gate is the always-on line.
> - **Category-gated default flip DEFERRED** — the global `CLIP_JUMP_CUTS` default stays `off`
>   (RED rubric: default-off = not promoted). Flipping it to gaps-for-storytime/informational is the
>   owner's decision AFTER the live gaps run + eyeball (the standing J1 gate). The A/B experiment lane
>   is the measurement path to that decision.
> - **Join-time whoosh (`CLIP_CUT_WHOOSH`) NOT built** — the visual punch-in is what hides the seam;
>   mixing a whoosh at N joins needed audio-input plumbing out of proportion to its value. Left as a
>   one-item follow-up (the `transition` beat is already stocked in `sfx_cues.json`).
> - **J1 effect-aware "dropped_by_cut"** in practice stays empty when cues sit on protected beats
>   (they do — SFX anchors on payoff/laughter/transient) — the field + remap are logged regardless
>   so the Lab is correct if a cue ever is swallowed.
>
> **Validation:** all three module selftests PASS (beat_map incl. the byte-identical sfx_cues
> delegation gate; clip_cuts J1/J4; cut_inference J2/J3/J5). A bounded **real-ffmpeg integration
> test** (on a reference clip) PASSED: HARD concat+punch-in keeps 720×1280 @ ~18 s; FADEWHITE xfade
> ~17.6 s; full `process_clip_transitions` gaps mode 40 s→36 s with the SFX cue remapped (1 kept/0
> dropped); dancing policy = off. **Remaining gate: the owner's live `CLIP_JUMP_CUTS=gaps` pipeline
> run + eyeball** before any default flip.

# Plan: jump cuts v2 — beat-aware smart+silence

Owner questions (2026-07-13) that seeded this plan:
1. *Do smart+silence cuts correlate with the earlier SFX/VFX placement-timing iterations?*
   **No code is shared today — and there's one latent conflict** (§Findings 1–3).
2. *Can the tuned inference/detection timing machinery help the cuts?* **Yes — six concrete
   reuse points**; unifying them is the core of this plan (§Beat-map thesis).
3. *Are smart cuts relevant for every clip category?* **Unevenly** — posture table in §Per-category,
   including two categories missing from the config map today.

Supersedes/absorbs the ranked list in [[concepts/transition-animations]] §Improvement evaluation
(items 1–6 there map onto phases J1–J6 here).

---

## Findings — evaluation of the shipped implementation

What exists (all default-off, failure-soft): `scripts/lib/clip_cuts.py` keep-span engine
(±1 s Whisper-segment snap, 2 s tail guard, `CATEGORY_MAX_DROP`, longest-first budget,
no-op fallback), `gaps_to_cuts` silence detection (≥1.2 s transcript gaps), LLM cuts as one
field of the Stage-6 mega-prompt, applied post-render at `stage7.py` 7d.5
(`process_clip_transitions`, trim+`xfade=fadewhite` 0.22 s) so burned captions stay in sync.

Each weakness below anchors a phase:

1. **Timing intelligence is siloed (→ J0/J1).** Every placement-timing iteration the owner
   tuned for SFX lives inside `sfx_cues.py` and is invisible to `clip_cuts`:
   `_refine_payoff` (payoff_delay 0.35 s + onset snap ≤1.2 s — "effects came in too early",
   2026-07-04), `payoff_rescue` (detection stamp = setup, real beat found up to ~17 s later —
   Hot Cheeto), `boom_after_line`'s RMS speech-gap finder (dip <35% of peak ≥150 ms — "line →
   breath → boom"), `_secondary_peaks` (RMS-flux prominence ≥0.55), `_laughter_times`
   (transcript laughter markers). Cuts snap only to Whisper segment edges.
2. **Payoff protection is positional, not semantic (→ J1).** `GUARANTEE_TAIL` protects the
   *last 2 s*; `payoff_rescue` proved the real payoff often sits mid-clip. A mid-clip payoff
   has no protection from a drop span except the LLM's own judgment.
3. **Cut ↔ SFX conflict (→ J1).** 7d.5 runs AFTER SFX/zooms are baked in
   (`stage7.py:851` passes no render plan): a drop span swallows placed cues, a join within
   ~0.22 s of a cue chops it mid-sound, and `effects_log` ground-truth times go stale on a
   compressed clip → [[concepts/reference-lab]] measurement skew if cuts ever default-on.
4. **Silence ≠ droppable (→ J1).** Whisper-gap = "dead air" also matches comedic pauses,
   wheeze-laughs, silent reactions — timing the SFX taxonomy itself treats as a beat
   (awkward_silence → crickets). Current residual after a gap drop is ~0.25 s (0.15+0.10).
5. **LLM timestamp imprecision (→ J2).** The mega-prompt asks for absolute seconds; LLMs are
   poor at word→second arithmetic; edges survive only via the ±1 s snap. (The page's old
   "P2 deferred" note.)
6. **Config gaps (→ J1).** `CATEGORY_MAX_DROP` misses `dancing` and `controversial` → both
   silently get the DEFAULT 0.45 — *more aggressive than funny's 0.30*. `informational` is in
   the map but may not match the live Stage-4 category vocabulary
   (`hype|funny|emotional|hot_take|storytime|reactive|dancing|controversial`) — verify at J1.
7. **Seam styling is a template tell (→ J4).** Every join = identical 0.22 s white fade,
   stacking with `CLIP_FLASH_CUTS` pops. Lab report #1: our cut *density* ≈ reference — the
   gap is cut *style*.

---

## The beat-map thesis (Q1/Q2 — correlation & reuse)

Cuts and SFX solve the same problem — *find the true beat in the audio* — at the same stage,
from the same `temp_dir`. Reuse map:

| Tuned machinery (sfx_cues.py) | Cut application |
|---|---|
| `_refine_payoff` (delay + onset snap + rescue) | Protected zone = refined payoff ±2 s (not raw `t`, not just the tail) |
| `boom_after_line` RMS-dip speech-gap finder | Cut edges land in true breaths (finer than segment edges) |
| `_secondary_peaks` RMS-flux prominence | True dead air (low flux) vs non-verbal action (high flux) → veto |
| `_laughter_times` | Veto any drop overlapping a laughter marker |
| Onset snapping | Joins land ON acoustic onsets — the Lab's `cut_alignment` card metric measures exactly this |
| The render plan (Stage 7 built it) | Joins keep ≥0.5 s from placed cues/zooms; swallowed cues re-logged; ground truth remapped via `remap_time()` |

---

## Per-category posture (Q3)

| Category | Cap today | v2 posture |
|---|---|---|
| storytime | 0.50 | **Aggressive** gaps+smart — the biggest winner (rambles); coherence gate mandatory |
| informational* | 0.50 | Same as storytime (*verify the vocabulary actually emits it) |
| hot_take | 0.30 | Moderate; completeness guard on the take itself |
| funny | 0.30 | Cautious; laughter/pause veto ESSENTIAL — the pause is the joke |
| reactive | 0.35 | Cautious; veto reaction noises |
| hype | 0.25 | Silence-only + RMS check (gaps are crowd/game noise, not dead air) |
| emotional | 0.40 → **0.20** | Minimal, gaps-only, leave-a-beat — pauses ARE the content (SFX already keeps emotional clean) |
| controversial | *missing* → 0.45 | **ADD 0.25, silence-only, NO smart cuts** — an LLM cut that drops a qualifier creates an out-of-context edit |
| dancing | *missing* → 0.45 | **ADD 0.0 (off)** — joins chop music continuity |

Plus a cross-category guard: any clip with a music bed in its render plan → gaps-only
tighter, or off.

---

## Phases

### J0 — `beat_map.py` extraction (0.5 session)
New `scripts/lib/beat_map.py`: pure extraction of the timing primitives from `sfx_cues.py`
(refine_payoff, laughter_times, speech_gaps from the boom_after_line RMS-dip logic,
secondary_peaks, onset snap) + `build(temp_dir, clip_start, duration, moment, render_plan)`
aggregator. `sfx_cues` delegates to it — **byte-identical behavior**, its selftest is the
gate. `clip_cuts` imports it.
**Gate:** sfx_cues selftest unchanged; new beat_map selftest.

### J1 — safety + correctness layer (1 session) — *prerequisite for ANY default-on*
- **Payoff-anchored protection**: drop spans may not overlap refined-payoff
  ±`CLIP_CUT_PROTECT_PAYOFF_S` (default 2.0) — in addition to the tail guard.
- **Reaction veto + leave-a-beat**: veto spans overlapping laughter markers or
  ≥0.55-prominence flux events; long silences compress to a **0.45 s residual** (was ~0.25 s).
- **Effect-aware joins**: joins ≥0.5 s from render-plan cue/zoom times; cues inside dropped
  spans → `effects_log` rows marked `dropped_by_cut` + surviving cue times remapped
  (`remap_time`) so Lab ground truth stays true.
- **Category map fixes**: add controversial 0.25 (silence-only enforced) + dancing 0.0;
  verify `informational`; music-bed guard.
- **Edges**: prefer beat_map RMS-dip breath points over raw segment snap when within window.
**Gate:** extended selftest + the page's outstanding **first live validation run**
(`CLIP_JUMP_CUTS=gaps` on a real VOD) + owner eyeball.

### J2 — text-anchored smart cuts micro-call (1 session)
New `scripts/lib/cut_inference.py`: ONE text-only LLM call per eligible clip (posture ≠
silence-only, duration ≥20 s). Input: word-timestamped transcript window. Output:
`[{"quote": "<verbatim substring to delete>", "reason": "filler|false_start|tangent|repetition"}]`.
Deterministic quote→word-span mapping against the word SRT (unmatched quotes discarded +
logged — self-verifying). Replaces the Stage-6 `edit_directive` when `CLIP_JUMP_CUTS=llm|on`
(mega-prompt loses the cuts field → simpler prompt). Few-shots embedded; thinking off
(`/no_think` + template kwargs, house pattern); failure-soft → gaps only.
**Gate:** 10-clip trace eyeball; quote-match rate logged (expect >80%); J3 pass rate.

### J3 — coherence gate (0.5 session, pairs with J2)
Deterministic: kept text must contain the payoff words (refined-payoff ±2 s window + the
moment's `why`/quote keywords). Optional `CLIP_CUT_JUDGE=1` (default on when llm): one
caption_judge-pattern call — "does the compressed transcript still read setup→payoff?
fidelity 0–10", <6 → fall back to gaps-only spans → else no-op.
**Gate:** fires on a synthetic butchered case; live false-positive rate eyeballed.

### J4 — seam styling (1 session)
`CLIP_CUT_STYLE=auto|fadewhite|hard` (default `auto`): hard cuts with **alternating ~±5%
punch-in** per span (crop/scale in `_build_filter`; zoom-punch precedent in
[[concepts/style-profiles]]); `fadewhite` reserved for tangent/topic-shift joins (J2 reason
tags); optional stocked `transition` whoosh at joins (`CLIP_CUT_WHOOSH=1`, ducked).
**Gate:** same-clip A/B owner eyeball (fadewhite vs auto).

### J5 — filler micro-lane (0.5 session)
Deterministic word-level: configurable lexicon (um/uh/like/you-know/I-mean), only clusters
**adjacent to ≥0.4 s pauses** forming a merged span ≥0.5 s (per-lane min_drop), cap 4/clip,
never inside the payoff zone. `CLIP_CUT_FILLERS` default 0 until eyeballed.
**Gate:** eyeball on a rambly storytime clip.

### J6 — rollout + measurement (0.5 session + owner gates)
- **Category-gated default**: after the J1 validation run passes, flip `CLIP_JUMP_CUTS`
  default off→`gaps` for storytime/informational ONLY; everything else stays off.
- **A/B experiment lane**: `CLIP_AB_CUTS_EXPERIMENT=1` → eligible rambly clips render their
  B variant WITH cuts (instead of only the seed offset) → owner GOOD/BAD labels become
  direct cut-value data.
- **Lab tie-in**: promote `duration_med` per category (already filed in
  [[concepts/plan-reference-deconstruction-2026-07]]) to measure compression;
  `cuts_per_30s` + `cut_alignment` already tracked; `dropped_by_cut` rows keep the
  ground truth honest.
- Dashboard: same 3-option select, tooltip text update only.

---

## Flags (all failure-soft; kill switch = shipped behavior)

| Flag | Default | Meaning |
|---|---|---|
| `CLIP_JUMP_CUTS` | off (J6: `gaps` for story/info) | unchanged semantics |
| `CLIP_CUT_PROTECT_PAYOFF_S` | 2.0 | refined-payoff no-cut halo |
| `CLIP_CUT_JUDGE` | 1 when llm | coherence fidelity judge |
| `CLIP_CUT_STYLE` | auto | hard+punch-in / fadewhite-on-tangents |
| `CLIP_CUT_WHOOSH` | 0 | whoosh at joins |
| `CLIP_CUT_FILLERS` | 0 | filler micro-lane |
| `CLIP_AB_CUTS_EXPERIMENT` | 0 | B variant = compressed |

## Non-goals
- A dedicated editing model — no-training doctrine + the §7 serving floor.
- Pre-render cut refactor — post-render design stays (caption sync preserved; crf-20
  re-encode loss negligible). The cut↔SFX conflict is solved by *effect-aware joins +
  ground-truth remap*, not by reordering the render.
- Beat-synced music-video editing — different feature.

## Sequencing & effort
```
J0 beat_map (0.5) → J1 safety (1, GATE: first live gaps validation run + owner eyeball)
  → J2 micro-call (1) + J3 coherence gate (0.5)
  → J4 seam styling (1) → J5 fillers (0.5) → J6 rollout/measurement (0.5 + owner gates)
```
≈ 4.5–5 sessions. J0+J1 alone are worth shipping even if nothing else proceeds — they fix
the payoff-protection hole, the comedic-pause bug, the SFX conflict, and the
missing-category config gap, and they close the page's standing "needs a validation run" item.

## Related
- [[concepts/transition-animations]] — the shipped v1 this upgrades (+ the ranked evaluation this plan absorbs)
- [[concepts/sfx-cue-taxonomy-2026-06]] — the timing machinery J0 extracts
- [[concepts/style-profiles]] — zoom infra J4 borrows
- [[concepts/plan-reference-deconstruction-2026-07]] — the Lab metrics J6 leans on
- [[concepts/captions]] — why cuts stay post-render
