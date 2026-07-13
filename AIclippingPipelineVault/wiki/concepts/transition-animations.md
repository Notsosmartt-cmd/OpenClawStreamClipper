---
title: "Transition Animations (jump-cuts + white flashes)"
type: concept
tags: [rendering, transitions, jump-cut, flash, edit-plan, stage-7, ffmpeg, originality, engagement]
sources: 0
updated: 2026-07-13
---

# Transition Animations

Two TikTok-style editing effects, both optional, flag-gated, and failure-soft:

1. **White flashes** — brief, TRANSIENT white pops (full-frame `drawbox` gated by `enable='between(t,a,b)'`, rise→peak→fall over ~0.16 s) layered on a clip for engagement / pattern-interrupt. No content removed. **Must NOT use `fade=…:color=white`** — `fade` holds the colour outside its ramp window and paints the whole clip white (BUG 64).
2. **Jump-cuts** — DROP spans of dead air / rambling and concatenate the kept spans with `xfade=fadewhite`, so the clip skips to the payoff faster. The white fade masks each cut.

Both are driven by the per-moment **`edit_plan`** ([[concepts/captions]] neighbours; schema in `scripts/lib/edit_plan.py`) plus rule-based generators, and applied by a single Stage-7 post-pass.

---

## Architecture (the key decision: post-render pass)

Transitions run as **Stage 7d.5**, a pass over the **finished clip files**, AFTER the normal render (the [[concepts/clip-rendering|`stage7._render_clip` solo path]] or the [[concepts/style-profiles|`profile_render.py` profile path]]) and BEFORE 7e stitch. This one decision buys three things:

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

## Model inference (v2 — [[concepts/plan-jump-cuts-v2-2026-07]], shipped 2026-07-13)

The smart cut decision is now a **dedicated text-only call** (`scripts/lib/cut_inference.py`), NOT a field of the Stage-6 vision mega-prompt. The model QUOTES verbatim substrings to delete; `cut_inference` maps each quote to time **deterministically** (segment start/end + intra-segment char interpolation), so the LLM never does word→second arithmetic and a hallucinated quote (not found in the transcript) is discarded — self-verifying. The call runs in Stage 6 (model loaded); the vision prompt now only infers flashes. A **J3 coherence gate** (the payoff's content-words must survive the cut) rejects gutting cuts; an optional LLM fidelity judge (`CLIP_CUT_JUDGE`, default off) adds a second check.

Cuts reuse the **tuned SFX timing** via `scripts/lib/beat_map.py` (extracted from `sfx_cues.py`): the refined payoff (protect a halo around the REAL payoff, which `payoff_rescue` proved is often mid-clip), laughter markers + prominent transients (veto — never cut a comedic pause/laugh), and breath points (cut edges land on natural breaths, finer than Whisper segment edges).

The **rule-based `gaps` mode still needs no LLM** (silence-drop from the transcript + `leave-a-beat`), so the feature is useful immediately and degrades gracefully.

---

## Modes & policy

| Env var | Values | Meaning |
|---|---|---|
| `CLIP_JUMP_CUTS` | `off` (default) · `gaps` · `llm` · `on` | `gaps`=silence only (safe); `llm`=text-anchored cuts; `on`=both |
| `CLIP_CUT_STYLE` | `auto` (default) · `hard` · `fadewhite` | join look — `auto`/`hard`=hard cut + alternating ±5% punch-in; `fadewhite`=the v1 white-flash join |
| `CLIP_CUT_PROTECT_PAYOFF_S` | `2.0` | no-cut halo around the refined payoff |
| `CLIP_CUT_JUDGE` | `0` (default) · `1` | optional LLM fidelity judge on the compressed transcript |
| `CLIP_CUT_FILLERS` | `0` (default) · `1` | pause-adjacent filler-word micro-lane (word-level) |
| `CLIP_AB_CUTS_EXPERIMENT` | `0` (default) · `1` | compress the **(B)** variant only → labelable A-vs-B pair (measurement) |
| `CLIP_FLASH_CUTS` | `off` (default) · `on` | seeded cadence flashes + any model-picked beats |

**Per-category posture** (`clip_cuts.CATEGORY_CUT_POLICY` + `CATEGORY_MAX_DROP`): storytime/informational compress most (0.50); funny/hot_take 0.30, reactive 0.35, hype 0.25; **emotional** 0.20 **silence-only** (pauses ARE the content); **controversial** 0.25 **silence-only** (an LLM cut dropping a qualifier = an out-of-context edit); **dancing** = **off** (joins chop music/motion).

[[entities/dashboard|Dashboard]] (Originality & Render panel): **"Jump-cut compression"** select (Off / Silence only / Smart + silence) + **"Editing style"** select (Auto / Hard / White fade) → `config_io.originality_to_env`.

**Guardrails** (so a cut can't wreck a clip): the per-category caps + policy above; a protected **payoff halo** (`CLIP_CUT_PROTECT_PAYOFF_S`, refined via beat_map) AND the protected tail (`GUARANTEE_TAIL` 2 s); **veto** of any drop straddling a laughter marker or prominent transient; **effect-aware joins** (a `JOIN_CLEAR` 0.5 s halo round every placed SFX cue, and the surviving cue times remapped into the effects log so the Reference Lab's ground truth stays honest); `MIN_KEEP` 1.5 s slivers skipped; edges snapped to breaths/pauses; flashes capped at 6/clip.

---

## Cost & limitations

- One extra NVENC re-encode per modified clip (~3–5 s each, only when enabled). Runs sequentially after the parallel render loop.
- The effects-log `transitions` row now records `duration_before`/`duration_after` + remapped/dropped SFX cue times, so the compressed length is recoverable (the old "duration is a pre-cut estimate" caveat is resolved for cut clips).
- **Not built:** join-time whoosh (`CLIP_CUT_WHOOSH`) — the visual punch-in hides the seam; a whoosh needs audio-input plumbing out of proportion to its value (one-item follow-up).

> [!note] Owner validation gate still open
> All layers pass unit + a real-ffmpeg integration test (both join styles, full `process_clip_transitions` gaps path with SFX remap). The remaining gate before any **default flip** is the owner's live `CLIP_JUMP_CUTS=gaps` pipeline run + eyeball. Default-off, so normal runs are unaffected. See [[concepts/plan-jump-cuts-v2-2026-07]] J6.

---

## Improvement evaluation (2026-07-13, owner: "any substantial improvements to smart+silence?")

> [!note] Expanded into a full development plan
> This ranked list grew into **[[concepts/plan-jump-cuts-v2-2026-07]]** (phases J0–J6: shared
> beat_map extraction, payoff-anchored protection, cut↔SFX conflict fix, text-anchored
> micro-call, coherence gate, seam styling, category-gated rollout). That page is now the
> authoritative roadmap; the list below is the original evaluation.

Ranked by impact/cost, each grounded in a specific shipped weakness. The "P2 deferred" note
above (dedicated edit-pass) is subsumed by #1. None violate the no-training doctrine — all are
prompt/config/deterministic-code changes.

1. **Text-anchored LLM cuts** — fixes the core "smart" weakness (timestamp imprecision).
   Today the Stage-6 mega-prompt asks for `drop_start/drop_end` in absolute seconds; LLMs are
   poor at word→second arithmetic, and the edges only survive because `compute_keep_spans`
   snaps them ±1 s. Instead: ask for the exact transcript **substrings to delete**, then map
   text → word-level SRT timestamps deterministically (we own word timings). Frame-accurate
   edges, zero timestamp hallucination, and trivially verifiable (the quoted text must exist in
   the window; must not overlap the payoff). Best shipped as the deferred **dedicated
   micro-call** (text-only, few-shot ramble→deletions) so the edit stops competing with ~15
   other JSON fields.
2. **Reaction-aware gap veto** — fixes the core "silence" weakness (the funny pause IS
   content). `gaps_to_cuts` sees only Whisper-segment gaps, so a comedic awkward pause, a
   wheeze-laugh, or a silent facial reaction reads as dead air and gets cut — destroying the
   timing the SFX taxonomy explicitly treats as a beat (awkward_silence→crickets).
   `sfx_cues._laughter_times()` already extracts laughter markers from the SAME
   `temp_dir/transcript.json` the transitions pass receives → vetoing (or shrinking) any drop
   that overlaps one is ~15 lines. Refinement: **leave-a-beat** — compress long pauses to
   ~0.4–0.5 s instead of the current ~0.25 s residue, the way editors "tighten" rather than
   delete.
3. **Hide the seam: hard cuts + alternating punch-in** — every join today is the same 0.22 s
   `fadewhite`; 4–6 identical white pops per clip is a template tell (and stacks with
   `CLIP_FLASH_CUTS`' pops). Real short-form jump cuts are mostly HARD cuts disguised by a
   slight zoom alternation (~100%↔105% across joins — standard talking-head technique; zoom
   infra already exists in [[concepts/style-profiles]]), optionally + the stocked `transition`
   whoosh beat. Makes compression read as edited-on-purpose. (Reference Lab report #1 showed
   our cut DENSITY already ≈ reference — the gap is cut *style*, which is this item.)
4. **Kept-transcript coherence gate** — after `compute_keep_spans`, reconstruct the kept text;
   deterministic check that the moment's payoff words survived + optionally one caption_judge-
   style fidelity call before accepting the compression. Catches "dropped the setup" wholesale.
5. **Filler-word micro-lane** — word-level SRT already exists: drop "um/uh/like/you-know"
   clusters **adjacent to pauses** (merged span crosses MIN_DROP), Descript-style; keep
   isolated fillers (machine-gun micro-cuts look worse than the filler).
6. **Rollout & measurement** — category-gated default-on first (`gaps` for
   storytime/informational, the MAX_DROP=0.5 categories), or wire into the A/B lane
   (B = compressed) so owner GOOD/BAD labels measure it; the Lab already tracks `cuts_per_30s`.

Non-goals: a dedicated editing model (no-training doctrine + the §7 serving floor); moving
cuts pre-render (the crf-20 re-encode loss is negligible vs re-introducing caption-sync
complexity — the post-render design above stays).

---

## Related

- [[concepts/clip-rendering]] — the legacy Stage-7 solo render this pass runs *after* (and the 7e stitch that follows it — see [[concepts/bugs-and-fixes]] BUG 63 for the stitch-never-fired fix)
- [[concepts/style-profiles]] — the alternate `profile_render.py` render path the transition pass is also path-agnostic to
- [[concepts/originality-stack]] — white flashes + jump-cuts are part of the engagement/originality layer
- [[entities/dashboard]] — Originality & Render panel exposes both toggles
- [[concepts/captions]] — captions are already burned in before 7d.5, which is why no SRT remap is needed
- [[concepts/bugs-and-fixes]] — BUG 64 (white-flash painted the whole clip white) is the regression this design avoids with transient `drawbox`
