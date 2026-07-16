---
title: "Fine-Tuning Round Plan (post-Wave-3, 2026-07)"
type: concept
tags: [plan, quality, reference-lab, sfx, music, gates, subtype]
sources: 0
status: in-progress
updated: 2026-07-15
---

# Fine-Tuning Round Plan (2026-07) — v2

**v2 (2026-07-15, owner directives):** (1) the irl SUBTYPE layer must be implemented in BOTH
the Reference Lab AND the main pipeline; (2) the "learn" stays SEPARATE — the Lab produces a
comparison, the owner adds their modifications, and hands off to an agent to apply; **never a
straight/automatic modification of the main pipeline**; (3) the fewest possible gates that
need the owner's eyes → everything consolidates into ONE review handoff; (4) **no driver
updates, no memory tests** (removed; the crash forensics stay in [[concepts/bugs-and-fixes#BUG 75]]-era
notes as reference only).

**Standing owner constraints:** keep the current frequent-SFX density; channels non-monetized;
no model training ever (learning = reviewable prompt/config artifacts, retrieval few-shot,
at most the gated linear ranker).

---

## The learn boundary (standing rule)

```
sensors → cards → comparison report + DRAFT proposals   (Lab, agent-side, auto)
                       ↓
        OWNER reads, edits, approves/rejects — "my modifications"   (the ONE gate)
                       ↓
        agent applies the owner-marked set to the pipeline           (agent-side)
                       ↓
        re-measure via the Lab loop; deltas reported                 (agent-side)
```

The Lab NEVER writes into pipeline prompts/config on its own. Every Lab→pipeline change
passes through the owner handoff — including Track E shape guidance.

---

## Phase 1 — Agent build (no owner eyes needed)

- [x] **1a. Reference re-decompose (A1) — DONE 2026-07-15**: 101/101, 0 failures, ~50 min
  batched-CUDA. Source events 3,350 → **974** (bruh 2,458 → 108 high-conf); metrics STABLE
  vs the post-hoc fixes (sfx 1.10/30s, music 50%, caption wps 2.44 ≈ speech rate) = counting
  policy validated at the source. Outro census: **76 detected / 20 certain-no (each
  recovered ~4 s of real tail) / 5 fallback** — the owner's "most but not all", measured.
  Boundary-cut epsilon fix shipped (the TikTok splice was counted as a cut when the window
  ended exactly ON it: ref cuts 1.98 → 3.68 → **2.46** honest). 15 never-analyzed clips got
  timelines (cards arrive with the 1c re-card).
- [x] **1b. Cuts-metric audit (A2) — DONE 2026-07-15**: raw-VOD A/B on the two highest-cut
  clips. Gaming (Lacy): raw **15.6/30s** vs rendered 14.4 → **source-native game cams**.
  Reaction (tbvnks): raw 6.5 vs rendered **10.7** → the excess is OUR render effects
  (zoom punches / freezes / meme cutaways read as shot changes by scenedetect). Metric
  re-documented as "visual cut rate the video CARRIES"; the cuts gap's honest lever =
  style-profile effect density, NOT clip_cuts/jump-cuts. Cuts verdicts unblocked for the
  Phase-2 sit-down.
- [~] **1c. SUBTYPE layer — Reference Lab side (RUNNING)**: v3 card prompt with the
  ANTI-LAZY design (owner 2026-07-15: keep a generic bucket but stop lazy dumping):
  `banter_roast / prank_public / freakout_overreaction / performance_rap / wholesome /
  irl_other`, where **irl_other is a LAST RESORT** requiring the model to reject each
  specific subtype first, plus a mandatory `subtype_why` justification (makes lazy choices
  visible). First cards verify the design: banter clip → banter_roast with real reasoning;
  solo-monologue clips → irl_other WITH proper rejection text ("no banter, prank, or
  performance; purely solo advice") — early signal a `solo_monologue` species may deserve
  its own name (the 1f draft will count irl_other justifications to decide). Re-card of
  all 101 running on the owner's 35B. OLD our-card dirs NOT re-carded (owner's compare
  targets the NEW run; optional later for merged compares).
- [x] **1d. SUBTYPE layer — pipeline side — DONE 2026-07-15**: Pass-B element schema emits
  `subtype` (same vocabulary, 'other' as the generic; anti-lazy wording in-prompt),
  validated against the fixed set (unknown → dropped, like patterns), carried
  hype_moments → Stage 6 entry → Stage 7 → effects_log — the exact emit-or-lose pathway
  BUG 66 documented. **Label-only: zero scoring/behavior change.**
- [x] **1e. Music ground truth — DONE 2026-07-15**: render_plan log moved AFTER the music
  decision and now records `music: {added, track, category}` + the moment's `subtype` —
  future compares separate added vs stream-native music per clip without raw-VOD A/B.
- [x] **1f. Shape-guide DRAFT — DONE 2026-07-15** → [[concepts/reference-shape-guide-2026-07]].
  Subtypes differentiate hard: banter payoff@84%/30s vs freakout FRONT-loaded@34% vs
  performance@100%/22s vs solo-story 54s/low-music. 7 candidate guidance lines (window
  placement, per-species durations, hook mandate, `solo_monologue` promotion — 12-13/16
  irl_other justifications describe it). All soft priors, n≥8 floor, NOT applied.
  1c note: re-card 100/101 (1 card failed — non-blocking); anti-lazy held (irl_other 19%,
  all with real rejection reasoning); story/controversy categories collapsed into
  irl_moment on re-card (their SHAPE now visible via arc + subtype instead).
- [x] **1g. Pipeline session — DONE 2026-07-15**: `--all --force`, 4 VODs, **69 clips under
  ONE stamp `20260715_145230`** (tbvnks 22 / Lacy 18 / Raud 15 / RaKai 14), 3 h 57 m total
  (full re-detection, not the cached-moments path). **Music GT: 69/69 `added: false`** —
  stream-native beds now proven per clip by the log. **Pipeline subtype flowing**: 26/69
  labeled (banter_roast 11, other 9, performance_rap 5, wholesome 1); 43 `None` = non-LLM
  detection lanes (arc/anomaly) + 9B omissions → **known limitation 1d-bis** (emit subtype
  from the other lanes + prompt emphasis) — harmless for the Compare, which joins on the
  CARD-side subtype. caption_judge_multi: no adverse log evidence; detailed validation =
  owner review.
  **Samples deferred with reasons**: jump-cuts sample would OVERWRITE the fresh Raud clips
  in `clips/` (title-based filenames) — run it AFTER the owner reviews the vanilla set, or
  add an out-dir knob first. News sample = one dashboard click (`News Compile (N)`) on the
  new run's clips at the sit-down (kokoro ear-check happens then anyway).
  → **READY: the owner combines run `20260715_145230` + the v3 reference cards into a
  Compare — the Phase-2 entry point.**

## Phase 2 — THE owner review handoff (the only eyeball gate)

One sit-down, one bundle, owner returns "my modifications":
1. **Clip review**: the 15-clip set + the jump-cuts variants + the news sample — complaints
   tagged by stage via [[concepts/quality-leverage-ranking-2026-07]]'s routing table.
2. **Gap-report verdicts** (25 items, recommendations pre-attached): sfx = not-a-problem
   (owner keeps frequent SFX); cuts items pre-resolved by 1b; duration items informed by the
   jump-cuts sample just watched; casing → voice bank; chat overlay + story music bed =
   owner want/skip calls.
3. **Shape-guide markup**: approve / edit / strike lines of the 1f draft.

No other gate requires owner eyes. Curation (voice bank, labels) stays an optional trickle,
never a blocker.

## Phase 3 — Agent apply + re-measure (no owner eyes)

> [!note] Phase-3 flagship candidate (filed 2026-07-15):
> [[concepts/plan-s45-text-judge-2026-07]] — split S4 extraction (9B, high-recall) from
> judgment (35B batched evidence-packet judge riding the EXISTING phase-boundary swap);
> ≈ wall-clock-neutral, decorrelates the rubric, culls before frames. Build after the
> sit-down: the review tells us whether S4's failure mode is judgment (build this) or
> recall (tune the proposer instead).

- [ ] Apply the owner-marked shape-guide lines + approved gap levers to Stage-4 guidance,
  rubric wording, duration constants, Stage-6 hook guidance, and any per-subtype config the
  owner approved.
- [ ] Re-run the Lab comparison on the next clip runs; report deltas against the Phase-1
  baselines (that's the whole point of the deterministic metric set).
- [ ] R5 retrieval few-shot stays gated until after one full Phase-3 cycle proves the
  static guidance out.

## Removed per owner (2026-07-15)

- ~~NVIDIA driver update~~ and ~~memtest~~ — owner explicitly declined; crash-forensics
  reference stays in the bug registry only.

## Parked (Track D, unchanged)

R5 retrieval few-shot (until a Phase-3 cycle completes); roast-cadence / disbelief-fail beat
wiring (pure config, pools stocked); fry_timer promotion; spec-decode (single-card only);
multi-clip parallel decompose workers (post-batching bottleneck is CPU stages; rare job class).

## Related
- [[concepts/reference-lab]] — measurement policy (sfx v2, caption dedup, music-bed, outro, device policy)
- [[concepts/bugs-and-fixes#BUG 75]] — why the report numbers changed this week
- [[concepts/quality-leverage-ranking-2026-07]] — complaint→stage routing for the review
- [[concepts/plan-jump-cuts-v2-2026-07]] / [[concepts/plan-news-compilation-2026-07]] — folded into the Phase-2 bundle
