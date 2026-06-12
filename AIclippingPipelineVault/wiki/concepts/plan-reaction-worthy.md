---
title: "Selection Sub-Plan B — Reaction-Worthy (genuine, earned peak)"
type: concept
status: in-progress
tags: [plan, selection, clip-worthiness, reaction, multimodal, audio-events, chat-features, diarization, vision-judge, stage-4, stage-5-5, future-session]
sources: 2
updated: 2026-06-12
---

# Selection Sub-Plan B — Reaction-Worthy

> [!note] Status — research/implementation brief for a FUTURE session
> One of four per-axis selection sub-plans under [[concepts/clipping-quality-overhaul]]. Onboard via that
> page + [[concepts/clipping-intelligence]]. Axes chosen 2026-06-04. Global constraint: **virality weight
> = light platform-awareness** (polish, not taste).

> [!done] BUILT 2026-06-04 — Pass-C pre-signal shipped (judge criterion deferred)
> `scripts/lib/reaction_signals.py` (boost-only intensity scorer, `--selftest` PASS) is wired into Pass C
> of `stage4_moments.py` as part of the accumulated, globally-clamped selection-axis product. **Deviations
> from the original plan, per the pre-build evaluation** ([[concepts/clipping-quality-overhaul]] §Cross-axis
> design guardrails): ceiling **lowered to 1.10** (B is the most-redundant axis — energy is already
> rewarded — so it gets the *smallest* boost, not the ≤1.20 originally planned); the **judge criterion +
> `[reaction: 0.NN]` card hint are DEFERRED** to the first live judge run (lean-judge-prompt discipline);
> the **diarized-interruption signal was dropped** to avoid double-counting the existing M1 speaker boost
> (B leans on audio `crowd_response` + the post-beat chat-breadth spike, gated on `unique_chatters`).
> Config: the `reaction` + `global` blocks in `config/selection_axes.json`. Diagnostics: `reaction_score`,
> `reaction_multiplier`, `axis_multiplier` on each moment + `rx=`/`ax=` in the `[PASS C]` log.

## Implementation plan (B-MVP)

**Split:** reaction *intensity* = a cheap pre-signal; reaction *authenticity* = the [[entities/vision-judge]]
criterion. *Cheap signals say a reaction happened; the multimodal judge says it's worth sharing.*

- **B1 — `scripts/lib/reaction_signals.py`** (new, mirrors `arc_completeness.py`):
  `evaluate(moment, segments, *, audio_events, chat_features, shape_module, markers, cfg)` →
  `{reaction_score 0-1, multiplier, signals}`. Components over `[clip_start, clip_end]` + peak `T`: audio
  `crowd_response` (+ small `rhythmic_speech`); chat `z_score`/`burst_factor` at `T..T+12` + a small
  subs/bits legitimacy floor; diarized `interruptions`/overlap. **Boost-only** category-aware multiplier
  `1 + gain·score` ∈ `[1.0, ~1.20]` (absence never penalizes — calm clips are A/E territory).
  Failure-soft; `--selftest`.
- **B2 — Pass C** (`scripts/lib/stages/stage4_moments.py`): import + apply `styled_score *= reaction_mult`
  right after the arc-completeness block; stamp `reaction_score`/`reaction_multiplier`/`reaction_signals`
  on the moment and the output entry (diagnostics + `[PASS C]` log).
- **B3 — Judge** (`scripts/lib/vlm_judge.py` + `scripts/lib/stages/stage5_5_judge.py`): add a "GENUINE,
  EARNED reaction — avoid forced/overacted hype" priority to the shared `_INSTRUCTION`; compute the
  pre-signal per shortlisted clip in `run_judge()` and append a compact `[reaction: 0.NN]` hint in
  `_clip_text_block`.
- **B4 — config**: a `reaction` block in `config/selection_axes.json` (weights/thresholds/gain/ceil/
  category lists), same robust env→repo→defaults fallback Plan A uses.

**Decisions:** authenticity = the judge's job (signals only measure intensity); **boost-only**; one shared
`selection_axes.json` + one accruing judge prompt; facial-expression detector **deferred** (the judge
already sees faces in the Stage-5 frames). **Double-count:** weight audio modestly (Pass A already gates
`crowd_response≥0.5` for keyword moments); lean on the novel chat-spike + co-laughter signals + the judge.

**Verify:** `reaction_signals.py --selftest` + `py_compile`; `stage5_5_judge.py --selftest` still passes;
live run shows `reaction=` in the Pass C log + `reaction_score` in `clips/.diagnostics/`; degrades cleanly
without librosa/chat.

## The metric
A good clip has a **genuine, earned emotional or comedic peak** — funny / shocking / moving enough that a
fan would actually send it to a friend. **Authentic, not manufactured hype or keyword density.**

## Why this is differentiated
The web research's sharpest critique of commercial scorers: "virality" collapses to **high-energy
delivery / keyword density**, missing the genuinely interesting quieter exchange. This axis rewards the
*real reaction* and explicitly uses the **multimodal + audio + chat signals selection currently ignores**
(today's pipeline selects on transcript text alone).

## Signals & mechanisms to research
- **Vocal affect** (`scripts/lib/audio_events.py` / librosa): laughter, pitch/loudness spikes, crowd
  response — already partly computed; promote from a Pass-A nudge to a first-class reaction signal.
- **Facial-expression change** (vision): reuse the Stage-5 frames / `face_pan.py` to detect a visible
  expression delta around the peak.
- **Audience reaction** (`scripts/lib/chat_features.py`): chat burst / emote-density spikes as
  corroboration (note the latency caveat — chat lags the moment).
- **Co-laughter / interruption** via diarization ([[entities/diarization]]).
- **Genuine vs performative**: the "is this earned or generic hype?" judgment is VLM/LLM territory — a
  judge axis, not a heuristic.

## Where it plugs into the pipeline
- Audio/face/chat **pre-signals** → Pass A additive + the Stage 5.5 judge card.
- Judge axis in `vlm_judge.py`: "which clip has the more genuine, earned reaction a fan would share?"
- Reuse: `audio_events.py`, `chat_features.py`, `face_pan.py` / Stage-5 frames, `diarization`.

## Composition with the other axes
Multiplies with **Arc-completeness** in the judge (a complete arc with a strong earned reaction is the
ideal). A reaction that *also* breaks the streamer's baseline (**Baseline-contrast**) is gold.

## Open research questions
- Reliably separating authentic reaction from performance ("oh my god" said constantly).
- Cross-modal weighting (audio vs face vs chat) and graceful handling of **no-facecam** streams
  (audio/chat only).
- Avoiding double-counting with Pass A's existing audio-event boosts.

## Verification
Do selected clips contain a clear reaction beat? Blind "would I send this to a friend?" spot-check vs the
current pipeline's picks.

## Related
- [[concepts/clipping-quality-overhaul]] · [[concepts/clipping-intelligence]] · [[entities/audio-events]] · [[entities/chat-features]] · [[entities/diarization]]
- Sibling axes: [[concepts/plan-arc-completeness]], [[concepts/plan-baseline-contrast]], [[concepts/plan-batch-diversity]]
