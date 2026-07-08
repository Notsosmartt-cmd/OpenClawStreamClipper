---
title: "Adaptive Clip Count — Plan A (bounds + tail floor) / Plan B (calibrated threshold)"
type: concept
tags: [plan, selection, clip-count, quota, calibration, ranker, pass-c, stage-4]
sources: 0
status: in-progress
updated: 2026-07-08
---

# Adaptive Clip Count — replace the duration quota

> [!note] Plan A SHIPPED 2026-07-08 (default-off) · Plan B still planned
> Plan A is implemented in `stage4_moments.py` behind `CLIP_COUNT_ADAPTIVE`
> (+ `CLIP_COUNT_SHADOW`, `CLIP_COUNT_TAU`), failure-soft, flag-off byte-identical.
> Offline validator `scripts/research/count_sweep.py` (+ `--self-test`) ships too. The
> τ sweep over the three frozen runs: **default τ=0.94 is safe + conservative** (trims 1
> unlabeled tail clip total), **τ=0.97 is the largest safe value** (trims 4, never a GOOD
> clip), **τ≥0.98 is UNSAFE** (cuts owner-liked clips). Remaining before non-shadow enable:
> a **shadow render** on a real VOD for owner review, then re-freeze so the sweep uses the
> true `pre_bucket_score` (the frozen runs predate that stamp → used the final_score proxy).

Owner question (2026-07-08): *is there a better implementation than the fixed
3-clips-per-hour quota?* This page is the detailed plan for the two-stage answer:
**Plan A** (buildable now, no labels needed) demotes the quota to bounds and adds a
relative tail-quality floor; **Plan B** (arrives with the calibrated ranker) makes the
count a pure consequence of content via an absolute threshold on a calibrated score.
A is the bridge and remains as the guardrail underneath B.

## The problem (verified in code + data)

**Code.** `stage4_moments.py:328-330`: `MAX_CLIPS = max(3, min(ceil(vod_hours × 3), 20))`
— duration is the only input. Selection (Phase 1 per-bucket guarantees ~line 3173,
Phase 2 round-robin ~line 3198) fills to `MAX_CLIPS` with **no quality floor anywhere**.
Consequences: **quota-padding** on thin VODs (weak tail picks — the owner's
"Gym Class was lackluster" class) and **cap-truncation** on dense VODs (candidate #21
of a great stream loses to another stream's #5).

**Data (frozen-run simulation, 2026-07-08).** Elbow/score-curve analysis over the three
frozen runs:

| Run | Quota picked | Score curve | Owner labels (rank:verdict) |
|---|---|---|---|
| rakai 010127 | 10 | flat (2% max drop, top-16 span 1.60→1.38) | #7 #8 #9 GOOD |
| rakai 074956 | 10 | flat (3% max drop) | **#1 BAD**, #6 #8 #10 GOOD |
| Tylil 185754 | 10 | **cliff at 4** (1.67/1.67/1.66/1.61 → 1.52, 5% drop) | — |

Three findings that shape the design:
1. **Flat curves are structural, not rounding.** Scoring computes in float64; only the
   stored values are rounded (`final_score` 4 dp at line 2998, base 3 dp at line 1141).
   The flatness comes from **signal granularity**: the base is an integer 1–10 LLM rubric
   rating (÷9 → ~10 discrete levels) dressed with axis multipliers clustered near 1.0.
   ~10 real information bins → no natural cliff for a threshold to cut at. More decimals
   magnify the flatness; they don't remove it.
2. **Rank ≠ owner taste yet** (074956's #1 is owner-BAD) — so any count rule on the
   hand-tuned score must be *relative within a run*, never absolute across runs.
3. **Some VODs do cliff** (Tylil) — a tail floor has real bite exactly where padding is
   worst, and correctly does nothing where the pipeline genuinely can't distinguish
   rank 10 from 14.
4. **Bucket-norm hides tail weakness**: line 3070 blends
   `final = 0.70·final + 0.30·bucket_norm`, deliberately lifting dead-bucket moments for
   time spread — good for coverage, but it masks exactly the weakness a floor needs to
   see. The floor must compare **pre-bucket-norm** scores.

---

## Plan A — bounds + relative tail floor (no labels required)

**Goal:** the duration formula becomes a *ceiling* (budget), not a target; selection may
stop under it when the marginal candidate is weak **relative to this run**. Kills
quota-padding; widens the ceiling for dense VODs. Can only *remove* weak tail picks —
asymmetric-safe by construction.

### Design

- **Flags** (all default-off / house rules):
  - `CLIP_COUNT_ADAPTIVE=1` — master switch.
  - `CLIP_COUNT_TAU` (default **0.94**) — tail floor = τ × median(pre-bucket score of
    the provisional top-`old_target` picks). Start tight: the curves are flat, so 0.85
    would never fire (verified: on 010127, 0.85×median ≈ 1.27 < every top-16 score).
  - `CLIP_COUNT_SHADOW=1` — compute + log trims, **trim nothing** (validation mode).
  - Ceiling when adaptive: `max(3, min(ceil(hours × 5), 24))`; `old_target` stays
    `ceil(hours × 3)` for Phase-1 bucket math (keeps time-spread behavior unchanged).
- **New stamp:** `pre_bucket_score` on every moment — `final_score` *after* position
  weight, *before* the bucket-norm blend. This is the floor's comparison key (and lands
  in the trace for offline sweeps).
- **Where it acts — post-selection trim** (cleanest integration): run today's selection
  (Phase 1 → Phase 2 → Phase 2.5 arc guarantee → category caps) up to the widened
  ceiling, then trim the final list from the tail: drop any pick with
  `pre_bucket_score < τ × median(pre_bucket_score of top old_target picks)`, respecting:
  - **min bound 3** (never trim below),
  - **arc-guarantee exemption** (Phase 2.5's pick already has its own quality floor
    `MIN_RATIO × weakest`; the miss-costs-more-than-false-positive doctrine holds),
  - trim log: `[COUNT] adaptive: ceiling=N picked=K trimmed=J floor=X.XXX (tau=0.94)`
    + per-trim lines with title/rank/score, mirrored into the trace
    (`count_trimmed: [...]`) so labels can grade trims later.
- **Why post-selection, not in-loop:** Phase 1/2 logic (bucket guarantees, spacing,
  round-robin, BUG-36 balancing) stays byte-identical; the trim is one bounded block
  that is trivially shadow-able and revertible.

### Implementation steps
1. Stamp `pre_bucket_score` (1 line at the bucket-norm blend, + trace whitelist).
2. Trim block after category-cap backfill in `stage4_moments.py` (flag-gated, both modes).
3. `scripts/research/count_sweep.py` — offline τ sweep over `learning/frozen_runs/`
   (+ any traces): per τ ∈ [0.88..0.99], report trims/run and check the **label
   constraint**: never trims a GOOD-labeled selected clip (e.g. 010127 floor must sit
   below rank 9); flag any τ that would.
4. Docs: this page → `status: in-progress`; bugs-and-fixes untouched (no bug).

### Validation / DoD
- Unit: synthetic candidate lists (flat curve → 0 trims at τ≤0.94; cliff curve → trims).
- τ sweep report over frozen runs; pick τ satisfying the label constraint.
- **Shadow run** on the next real VOD (`CLIP_COUNT_ADAPTIVE=1 CLIP_COUNT_SHADOW=1`):
  owner reviews the would-trim list — every would-trim should be a clip the owner
  wouldn't post. Then enable for real.
- Flag-off run stays byte-identical (house invariant).

### Expected improvement (honest)
- **Tylil-class VODs** (real cliff): ~10 → ~4–7 clips; the trimmed ones are precisely
  the lackluster tail. Fewer bad posts, less review time.
- **Flat rakai-class runs:** little/no trimming — *correct*, not a failure; the score
  genuinely can't distinguish the tail there (see Plan B).
- **Dense VODs:** ceiling ×5/24 recovers material the ×3/20 cap discarded.
- **Cannot fix:** a taste miss that scored high (074956 #1-BAD) — that's ordering, not
  count → Plan B. Cannot conjure gaps the signal doesn't contain.

---

## Plan B — calibrated absolute threshold ("count gate")

**Goal:** count becomes a pure consequence of content. Requires the fitted ranker
([[concepts/calibration-ranker-2026-07]]) to pass its gate — same fuel as everything
else: **labels** (3–5+ rated VODs; the gate currently REJECTs at 2, correctly).

### Why the ranker fixes the resolution problem at the source
`maybe_rescore` returns `sigmoid(Σ wᵢ·xᵢ + b)` over the full stamped feature set —
continuous signals the hand-tuned path crushes into the integer-anchored product:
reaction/keyword/motion scores, decomposed axis parts, log-factors of every multiplier,
cross-modal interactions, `is_anomaly`. A logistic fit on labels makes the output
≈ **P(highlight | features)** — continuous (no 1–10 bottleneck) *and* calibrated
(comparable across VODs). Only then does an absolute rule — "keep everything ≥ θ" —
mean the same thing on every stream: a dead 4-hour VOD yields 3, a chaotic 1-hour VOD
yields 14. The no-fixed-anything endgame.

### Design

- **Extend the gate in `fit_ranker.py` with a COUNT verdict** (the existing gate tests
  *ranking* — recall@N; it does NOT test count selection). Leave-one-VOD-out, per held
  run: sweep θ over held-out sigmoid scores; ENABLE-COUNT only if a θ band exists that
  (a) keeps every held-out positive, (b) excludes held-out negatives at least as well as
  quota selection did, (c) is stable — θ chosen on the train folds works on the held run.
  Emit `count_threshold` (band midpoint) into `config/selection_ranker.json`
  (`{"count_threshold": θ, "count_mode": "absolute"}`).
- **Pipeline behavior** (only when a fitted file with `count_threshold` is present —
  double-gated by design): select `sigmoid ≥ θ`, then apply the existing spacing rule +
  bucket representation as *soft* constraints + **Plan A's bounds as the backstop**
  (min 3, ceiling ×5/24, relative floor stays armed beneath in case the fit is
  miscalibrated on a new channel).
- **Cross-channel caution:** the identity anchor protects *ordering* on unseen channels,
  but the absolute *level* of a calibrated score can shift on a new channel's
  distribution. Mitigations: the clamped bounds; per-run sanity log when θ selects
  ceiling-many or min-few; the count gate re-runs at the L4 cadence as labels accumulate
  ([[concepts/plan-learning-activation-2026-07]]).
- **Time-spread preservation:** position_weight is already inside the deployment key;
  spacing stays hard; bucket coverage becomes soft (a genuinely dead bucket no longer
  gets a pity pick — that *is* the feature).

### Implementation steps
1. `fit_ranker.py`: count-gate sweep + verdict + `count_threshold` emission (~50 lines,
   research-side only).
2. `stage4_moments.py`: threshold selection path when `count_mode=absolute` (reuses the
   Plan-A trim/bounds plumbing; flag interplay documented in code).
3. Shadow comparison mode: log quota-list vs threshold-list side by side for owner review
   before the switch flips.

### Validation / DoD
- Count gate ENABLE across ≥3 labeled VODs (leave-one-out, θ stable).
- Shadow run: owner agrees the threshold list ⊇ the keepers and ⊉ the padding.
- Bounds/backstop verified by forcing a miscalibrated fit in a test (clamps engage).

### Expected improvement (honest)
- **Count and ordering improve from the same fit**: the 074956 #1-BAD label becomes
  gradient pushing that clip class down (no count rule can); Mockingbird-class
  reaction-carried moments get up-weighted; the continuous spread finally gives
  thresholds real gaps to cut at.
- Scale: unmeasured until the gate can run — which is the honest status of the whole
  learning loop; every rated run moves it.

---

## Sequencing & interplay

1. **Now:** Plan A steps 1–3, shadow mode + τ sweep (no labels needed).
2. **Continuously:** label runs via `rate_run` (Path B — [[concepts/label-paths-and-store-2026-07]]).
3. **At 3–5 labeled VODs:** run the ranking gate + the new count gate.
4. **On ENABLE:** Plan B takes over count; Plan A's bounds+floor stay as the permanent
   backstop under it.

A without B: padding fixed where detectable, flat tails untouched. B without A: no
backstop against miscalibration on new channels. Together: content-driven count with a
relative-sanity net — consistent with the P-TIGHT doctrine (bounds are guardrails,
content decides; nothing fixed).

## File map

| File | Plan | Change |
|---|---|---|
| `scripts/lib/stages/stage4_moments.py` | A, B | `pre_bucket_score` stamp; post-selection trim block; threshold path |
| `scripts/research/count_sweep.py` | A | NEW — offline τ sweep vs frozen runs + labels |
| `scripts/research/fit_ranker.py` | B | count-gate sweep + `count_threshold` emission |
| `config/selection_ranker.json` | B | gains `count_threshold` / `count_mode` (only via gate) |
| `learning/frozen_runs/` | A, B | already banks everything both validations need |

Related: [[concepts/calibration-ranker-2026-07]] · [[concepts/plan-learning-activation-2026-07]] ·
[[concepts/label-paths-and-store-2026-07]] · [[concepts/multimodal-fusion-2026-07]] (§interaction
features) · [[concepts/bugs-and-fixes]] (BUG 36/37 — the saturation this works around)
