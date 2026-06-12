---
title: "Selection Sub-Plan C — Baseline Contrast (deviates from the streamer's own norm)"
type: concept
status: in-progress
tags: [plan, selection, clip-worthiness, novelty, anomaly-detection, baseline, audio-events, conversation-shape, vision-judge, stage-4, stage-5-5, future-session]
sources: 2
updated: 2026-06-12
---

# Selection Sub-Plan C — Baseline Contrast

> [!note] Status — research/implementation brief for a FUTURE session
> One of four per-axis selection sub-plans under [[concepts/clipping-quality-overhaul]]. Onboard via that
> page + [[concepts/clipping-intelligence]]. Axes chosen 2026-06-04. Global constraint: **virality weight
> = light platform-awareness** (polish, not taste).

> [!done] BUILT 2026-06-04 — Pass-C pre-signal shipped (judge criterion deferred)
> `scripts/lib/baseline_contrast.py` (`compute_baseline` + `evaluate`, boost-only, `--selftest` PASS) is
> wired into Pass C of `stage4_moments.py`: the per-VOD baseline is computed **once** before the scoring
> loop (speaking-rate mean/std over rolling windows + modal segment-type + topic boundaries flattened from
> `CONVO_SHAPE_INDEX`), then each moment is scored on **two-sided rate z-deviation + a start-aligned topic
> pivot + a genre (segment-type) shift**. Per the pre-build evaluation, C is given the **most authority of
> the axes (ceil 1.18)** — it is the corrective for energy bias. The `[unusual-for-streamer]` **judge
> criterion is DEFERRED** to the first live judge run (lean-judge-prompt discipline). The topic signal is
> deliberately **start-aligned** (a pivot INTO a topic), not a mid-clip crossing, so it does **not** fight
> Plan A (which penalizes mid-clip crossings). Config: the `baseline_contrast` block in
> `config/selection_axes.json`. Diagnostics: `baseline_contrast`/`baseline_multiplier` + `bc=` in the
> `[PASS C]` log; the per-VOD baseline is logged once at `[BASELINE]`.

## Implementation plan (C-MVP)

**Mechanism:** compute the streamer's *normal* once per VOD; **boost moments that break it** (boost-only —
absence of deviation is neutral). The most novel axis; counters energy-bias (a quiet beat can win on a
hype streamer).

- **C1 — `scripts/lib/baseline_contrast.py`** (new, mirrors `arc_completeness.py`):
  `compute_baseline(segments, convo_shape_index, segment_map, cfg)` (one-time: speaking-rate mean/std over
  30 s/10 s windows + flattened topic-boundary times from `conversation_shape`) and
  `evaluate(moment, segments, *, baseline, segment_map, cfg)` → `{contrast_score, multiplier, signals}`.
  Signals (deliberately **orthogonal to the Tier-2 M1 speaker boost**): **rate deviation** (two-sided z vs
  baseline, cold-start guarded), **topic shift** (TextTiling boundary inside the window), **genre shift**
  (segment-type change). **Boost-only** `1 + gain·score` ∈ `[1.0, ~1.15]`. Failure-soft; `--selftest`.
- **C2 — Pass C** (`scripts/lib/stages/stage4_moments.py`): compute the baseline once before the scoring
  loop; apply `styled_score *= baseline_mult` right after the arc block; stamp
  `baseline_contrast`/`baseline_signals` on the moment + output entry (diagnostics + `[PASS C]` log).
- **C3 — Judge** (`scripts/lib/vlm_judge.py` + `scripts/lib/stages/stage5_5_judge.py`): add "deviations
  from how this streamer normally behaves are more interesting" to the shared `_INSTRUCTION`; append a
  `[unusual-for-streamer: 0.NN]` card hint.
- **C4 — config**: a `baseline_contrast` block in `config/selection_axes.json` (weights/thresholds/gain/
  ceil/`min_windows`), same robust fallback as Plan A.

**Decisions:** global baseline MVP (per-segment-type = Phase 2); audio-loudness + semantic-drift deferred;
boost-only; orthogonal to M1 (no double-count); two-sided rate (fast *and* slow are "breaks").

**Verify:** `baseline_contrast.py --selftest` (anomalous moment > typical; cold-start neutral; degraded
neutral) + `py_compile`; `stage5_5_judge.py --selftest` still passes; live run shows `baseline=` in the
Pass C log + `baseline_contrast` in `clips/.diagnostics/`.

## The metric
A good clip is **unexpected relative to how THIS streamer usually is** — a sudden shift in energy,
affect, volume, or topic against their own baseline. Clip the moment that breaks the pattern: a calm
person suddenly losing it; a hype streamer going quiet and sincere.

## Why this is differentiated
This is the **most novel mechanism** in the roadmap and nothing commercial does it: **relative-to-self
anomaly detection** instead of absolute viral templates. It's inherently anti-sameness (you can't clone
it across channels) and aligns with the academic unsupervised-highlight literature (audio-visual
recurrence / "less is more"). It directly counters the energy-bias the user dislikes — a *quiet* moment
can be the highlight if quiet is abnormal for that streamer.

## Signals & mechanisms to research
- **Per-VOD baseline** of features: speaking rate, loudness/RMS, pitch variance, sentiment, segment-type
  mix, facial affect — then score each window by its **deviation** (z-score / outlier) from baseline.
- **Semantic surprise**: topic-shift magnitude vs the streamer's running theme (sentence-transformers,
  already used by `callbacks.py`).
- **Sudden multi-speaker / interruption** via diarization as a structural deviation.
- **Per-channel baseline** (persisted across VODs) as a later extension → ties into the personalization
  in [[concepts/clipping-quality-overhaul]] Plan 3 (trained reward model).

## Where it plugs into the pipeline
- New analyzer `scripts/lib/baseline_contrast.py`: compute the VOD baseline + per-window deviation
  score; feed Pass A as an additive signal and into the Stage 5.5 judge card.
- Judge axis: "which clip is more surprising relative to how this streamer normally behaves?"
- Reuse: `audio_events.py` (loudness/rhythm), `conversation_shape.py`, sentiment (light LLM call or
  model), `diarization`.

## Composition with the other axes
A strong **pre-signal** that boosts otherwise-borderline candidates; pairs naturally with
**Batch-diversity** (deviations are intrinsically varied) and amplifies **Reaction-worthy** (a reaction
that's also abnormal for the streamer).

## Open research questions
- Baseline window/granularity (whole-VOD vs rolling); cold-start on short VODs.
- Avoid rewarding mere noise/glitches/audio artifacts as "deviations."
- Per-channel baseline persistence + storage design.
- Interaction with segment classification (deviation *within* a segment type vs across).

## Verification
Spot-check that high-contrast picks are genuine shifts (vs a representative "baseline" stretch that
should score low). Confirm at least some *quiet* highlights surface on a high-energy streamer.

## Related
- [[concepts/clipping-quality-overhaul]] · [[concepts/clipping-intelligence]] · [[entities/audio-events]] · [[entities/callback-module]] · [[entities/diarization]]
- Sibling axes: [[concepts/plan-arc-completeness]], [[concepts/plan-reaction-worthy]], [[concepts/plan-batch-diversity]]
