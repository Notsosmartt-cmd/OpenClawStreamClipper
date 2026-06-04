---
title: "Selection Sub-Plan B — Reaction-Worthy (genuine, earned peak)"
type: concept
tags: [plan, selection, clip-worthiness, reaction, multimodal, audio-events, chat-features, diarization, vision-judge, stage-4, stage-5-5, future-session]
sources: 2
updated: 2026-06-04
---

# Selection Sub-Plan B — Reaction-Worthy

> [!note] Status — research/implementation brief for a FUTURE session
> One of four per-axis selection sub-plans under [[concepts/clipping-quality-overhaul]]. Onboard via that
> page + [[concepts/clipping-intelligence]]. Axes chosen 2026-06-04. Global constraint: **virality weight
> = light platform-awareness** (polish, not taste).

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
