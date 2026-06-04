---
title: "Selection Sub-Plan C — Baseline Contrast (deviates from the streamer's own norm)"
type: concept
tags: [plan, selection, clip-worthiness, novelty, anomaly-detection, baseline, audio-events, conversation-shape, vision-judge, stage-4, stage-5-5, future-session]
sources: 2
updated: 2026-06-04
---

# Selection Sub-Plan C — Baseline Contrast

> [!note] Status — research/implementation brief for a FUTURE session
> One of four per-axis selection sub-plans under [[concepts/clipping-quality-overhaul]]. Onboard via that
> page + [[concepts/clipping-intelligence]]. Axes chosen 2026-06-04. Global constraint: **virality weight
> = light platform-awareness** (polish, not taste).

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
