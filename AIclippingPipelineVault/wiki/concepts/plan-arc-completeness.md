---
title: "Selection Sub-Plan A — Arc Completeness (self-contained setup→payoff)"
type: concept
tags: [plan, selection, clip-worthiness, arc-completeness, pattern-catalog, conversation-shape, vision-judge, stage-4, stage-5-5, future-session]
sources: 2
updated: 2026-06-04
---

# Selection Sub-Plan A — Arc Completeness

> [!note] Status — research/implementation brief for a FUTURE session
> One of four per-axis selection sub-plans under [[concepts/clipping-quality-overhaul]]. Onboard by
> reading that page + [[concepts/clipping-intelligence]] first. Axes chosen by the user 2026-06-04.
> Global constraint: **virality weight = light platform-awareness** (borrow polish, not taste — never
> let "what's trending" drive selection).

## The metric
A good clip is a **complete, self-contained moment**: a clear setup that lands a payoff, understandable
with **zero prior context**, not starting mid-thought or ending before the beat resolves. Reward
completeness; penalize fragments and dangling arcs.

## Why this is differentiated
Commercial virality scores reward *hook + energy*; they don't model whether the arc actually
*completes*. We already own a **structure-first Pattern Catalog** ([[concepts/highlight-detection]]) and
conversation-shape analysis — direct assets for scoring completeness that template-matching tools lack.

## Signals & mechanisms to research
- **Structural completeness from `conversation_shape.py`**: opener marker + payoff/closer present within
  the window; monologue-run coherence; topic-boundary density (a clip spanning a topic boundary is
  likely two half-moments).
- **Pattern-signature satisfaction** (`config/patterns.json`): does the clip fully satisfy a pattern's
  signature (setup→contradiction→concession) or only part of it?
- **Judge axis** (Stage 5.5): pairwise "which clip is more self-contained — clearer beginning, resolved
  payoff, understandable cold?"
- **Boundary co-design**: completeness ⇔ boundaries that contain the whole arc — couples with the
  Phase-2.a hook/reaction boundary work; a completeness score can gate boundary widening.

## Where it plugs into the pipeline
- A cheap text-side `arc_completeness` score (0-1) in/near Pass A or Pass C
  (`scripts/lib/stages/stage4_moments.py`), written onto each moment and into the Stage 5.5 judge card.
- A judge comparison criterion in `scripts/lib/vlm_judge.py` / `scripts/lib/stages/stage5_5_judge.py`.
- Reuse: `conversation_shape.py`, `patterns.json`, `boundary_detect.py`.

## Composition with the other axes
Combines with **Reaction-worthy** inside the judge rubric (completeness × payoff). Independent of
**Batch-diversity** (post-selection). Feeds the boundary stage (a complete arc defines where to cut).

## Open research questions
- Can `conversation_shape` reliably detect *resolution*, or does completeness need the transcript/VLM?
- How to credit a great payoff whose setup is only implied?
- Does requiring completeness hurt intentionally setup-light reactive clips? → likely make it
  category-aware.

## Verification
Sample N clips; rate "starts cleanly? / pays off? / understandable cold?"; track the fraction that start
mid-sentence or lack a payoff, before vs after.

## Related
- [[concepts/clipping-quality-overhaul]] · [[concepts/clipping-intelligence]] · [[concepts/highlight-detection]] · [[concepts/boundary-snap]]
- Sibling axes: [[concepts/plan-reaction-worthy]], [[concepts/plan-baseline-contrast]], [[concepts/plan-batch-diversity]]
