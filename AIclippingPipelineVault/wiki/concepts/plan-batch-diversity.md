---
title: "Selection Sub-Plan D — Batch Diversity (the delivered set is varied)"
type: concept
tags: [plan, selection, clip-worthiness, diversity, mmr, embeddings, anti-sameness, stage-4-diversity, vision-judge, stage-4, stage-5-5, future-session]
sources: 2
updated: 2026-06-04
---

# Selection Sub-Plan D — Batch Diversity

> [!note] Status — research/implementation brief for a FUTURE session
> One of four per-axis selection sub-plans under [[concepts/clipping-quality-overhaul]]. Onboard via that
> page + [[concepts/clipping-intelligence]]. Axes chosen 2026-06-04. Global constraint: **virality weight
> = light platform-awareness** (polish, not taste).

## The metric
The **delivered set** of clips is varied — no five near-identical hype clips. Optimize the *set*, not
just each clip in isolation. Different patterns, categories, moods, and moments across the batch.

## Why this is differentiated
Commercial tools rank clips **independently** and happily emit five variations of the same loud moment.
We optimize the **portfolio**: maximize coverage of distinct moment-types per VOD. (This is the
set-level complement to per-clip axes A/B/C.)

## Signals & mechanisms to research
- **MMR (Maximal Marginal Relevance)** — already seeded: `mmr_lambda` in `config/rubric.json` and
  `scripts/lib/stages/stage4_diversity.py`. Extend it from text-only to **multimodal** similarity.
- **Embedding similarity**: text (sentence-transformers, already used by `callbacks.py`), visual (frozen
  CLIP/SigLIP on the Stage-5 frames — new), audio. Penalize a candidate similar to one already selected.
- **Categorical spread**: pattern / category / segment-type / time-bucket coverage (time-buckets already
  exist in Pass C).

## Where it plugs into the pipeline
- Extend `scripts/lib/stages/stage4_diversity.py` / Pass C to run **multimodal MMR over the shortlist**,
  applied **after** the Stage 5.5 tournament (so quality is decided first, then de-duplicated for
  variety).
- Reuse: `stage4_diversity.py`, sentence-transformers; add frozen CLIP/SigLIP embeddings (small VRAM).

## Composition with the other axes
**Post-selection re-rank** on top of the A/B/C-scored shortlist — orthogonal to the per-clip axes. It is
the natural last step of the Stage 5.5 judge pipeline. Note overlap with the existing originality/MMR
diversity work — consolidate rather than duplicate.

## Open research questions
- Similarity-metric weighting (visual vs text vs audio) and the quality-vs-diversity lambda.
- Embedding-model choice + VRAM budget alongside the loaded VLM.
- Hard variety quotas (≥1 per pattern) vs soft penalties.
- Reconciling with the existing time-bucket spread + `moment_groups.py`.

## Verification
Measure pairwise similarity of the delivered clips (text + visual); confirm broader spread across
pattern/category/time vs the current pipeline.

## Related
- [[concepts/clipping-quality-overhaul]] · [[concepts/clipping-intelligence]] · [[concepts/highlight-detection]]
- Sibling axes: [[concepts/plan-arc-completeness]], [[concepts/plan-reaction-worthy]], [[concepts/plan-baseline-contrast]]
