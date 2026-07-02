---
title: "Multimodal Fusion — can the LLM 'understand' audio+visual jointly? (2026-07 evaluation)"
type: concept
tags: [research, multimodal, fusion, audio, vision, incongruity, evaluation, scoring, reference]
sources: 0
status: reference
updated: 2026-07-02
---

# Multimodal Fusion — converging the senses for inference (2026-07 evaluation)

Owner's question (2026-07-02): *"Is there any way to converge the two senses of audio and visual so the LLM 'understands' their relation — for the ReemKnocks and GeorgeBush clips, either sense alone makes the clip seem useless or unfunny. Is model inferencing only converging the senses via post-segmented scoring?"* Evaluation only — **no code changed**. Companion to [[concepts/model-senses]] (the perception inventory) and [[concepts/case-incongruity-comedy]] (the motivating clips).

---

## Verdict on the owner's assumptions

| Assumption | Verdict | Evidence |
|---|---|---|
| "Transcription is rated, then vision is rated" | **TRUE for detection, nuanced for enrichment** | Stage 4 (Pass A/B) proposes moments from **transcript-only** LLM prompts; `audio_events` feeds Pass A as *numeric boosts*, never prompt content (`stage4_moments.py:101-114, 787`). Vision never proposes. But Stage 5.5 and Stage 6 **do** run genuinely joint prompts (below). |
| "Senses only converge via post-segmented scoring" | **Mostly TRUE in effect** | Cross-modal combination is mostly arithmetic (multiplier chain — cross-val ×1.5, axis multipliers, vision boost). The two joint-prompt points exist but (a) only see moments that already survived transcript-only proposal, (b) represent audio **as words**, (c) see stills, not motion. So conjunction-carried moments die before any joint inference happens. |
| "Bus/GeorgeBush clips need the conjunction; one sense alone = not funny" | **TRUE — and it's the documented blind spot** | [[concepts/model-senses]] §blind spots: no semantic audio at proposal time; vision = 6 stills. [[concepts/case-incongruity-comedy]] exists precisely because these clips are **cross-modal incongruity** (banal words + screaming + physical chaos) and the pipeline cannot perceive the conjunction. |

## Where the senses actually meet today (verified)

| Place | Modalities in ONE inference | Fusion type | Limitation |
|---|---|---|---|
| Stage 4 Pass A | transcript + 3 librosa dials | **arithmetic boost** (no LLM) | dials are energy, not meaning |
| Stage 4 Pass B/B-global/B+ | transcript only | none | the proposal gate — conjunction moments never surface |
| **Stage 5.5 Vision Judge** | 4 time-ordered frames **+ transcript window** per clip, pairwise (`vlm_judge.py:131-142`) | **joint prompt** (real fusion) | re-*ranks* only; stills; audio=words |
| **Stage 6 enrichment** | 6 frames + context hint (stream type, category, Pass-B why, clip transcript, chat) (`stage6_vision.py:406-411`) | **joint prompt** | boost-only; same stills/words limits |
| Pass C / axes | unimodal scores | arithmetic (clamped product) | no interaction terms |

**The structural problem is sequencing, not just scoring:** joint inference exists, but only *after* a transcript-only proposal gate. A moment whose clip-worthiness lives in the audio×visual conjunction (screams + shove + mundane words) is never proposed, so the joint-prompt stages never see it. Secondarily, even at the joint stages the model never *hears* — audio is reduced to Whisper words + 3 dials — and never sees *motion*.

---

## Can the senses be converged? Yes — five options, ranked by feasibility on this rig

1. **Symbol-level timeline fusion (RECOMMENDED — near-term, local, cheap).** Build one **time-aligned event stream** interleaving transcript words, CLAP audio events, motion spikes, and cuts — exactly what [[concepts/plan-clip-forensics]]'s offline timeline already produces — and hand *that* to the text LLM: `[t=6.2] AUDIO screaming(0.46) | MOTION 6.3× | TEXT "chill buddy, chill"`. The LLM then reasons over the **conjunction** ("screaming + violent motion + banal words = physical-comedy incongruity") instead of each sense separately. This is the [[concepts/case-incongruity-comedy]] anomaly-proposer: propose where audio/motion signal *contradicts or exceeds* the words, then let Stage 5.5/6 verify. Fixes the sequencing gap at the proposal end. All sensors are already built and verified ([[entities/audio-sense-module]], [[entities/visual-sense-module]]); the missing piece is the timeline-builder + prompt + `src=ANOMALY` lane (flag-gated).
2. **Interaction features in scoring (cheap complement).** The multiplier chain (and the future [[concepts/plan-calibration-loop]] logistic ranker) is late fusion with **no interaction terms**. Add fitted conjunction features — e.g. `motion_high × words_banal`, `audio_event_strength × low_keyword_score` — so even the arithmetic layer can reward cross-modal incongruity. Trivial once the ranker exists.
3. **Richer joint prompts at 5.5/6 (improve the existing fusion point).** Feed the judge/enricher the same event timeline alongside the frames (and/or a timestamped frame strip) so the one place that already does joint inference sees *named audio* + *pseudo-motion*, not just words + stills. Low cost; helps ranking but not proposal.
4. **Cross-modal embedding incongruity signal (experimental).** CLAP puts audio and text in one embedding space; a large audio↔text (or audio↔frame-caption) *distance* during a high-energy window is a numeric "the senses disagree" detector — a possible new selection axis. Caveat: CLAP cosines run low/uncalibrated on this corpus (verified 2026-06-21) → real calibration burden.
5. **True omni models (audio+video in, one model) — the "real" answer, not practical here yet.** Models like Qwen-Omni-class ingest raw audio+video jointly — genuine sensor-level fusion. Blockers today: 16 GB VRAM budget, LM Studio's input support (images yes; audio/video-in effectively no), token-count explosion for video. Revisit when the local stack supports it; until then symbol-level fusion (1) is the honest local approximation.

> [!note] What "convergence" realistically means locally
> Option 1 is *symbol-level* fusion — the LLM reasons over **named** events, not raw signals. That is weaker than true joint perception (5), but for incongruity comedy it is likely sufficient: "screaming + 6× motion spike + calm words" is enough for a text LLM to recognize the *shape* of the joke. The forensics lane proved the naming works on the exact motivating clips (ReemKnocks: `bruh` cluster 1–7.5 s + 9 motion spikes + "chill buddy" words + suspense bed 6–14 s).

## Recommended sequence (when the owner green-lights implementation)

1. Timeline-builder (merge transcript + `audio_sense` + `visual_sense.motion_events` into one ordered stream) → 2. anomaly-proposer producing `src=ANOMALY` candidates (flag-gated, failure-soft, boost-only entry into Pass C) → 3. pass the timeline into the Stage 5.5 judge prompt → 4. interaction features once the calibration ranker lands. Steps 1–3 reuse only already-verified components.

## Related
- [[concepts/model-senses]] · [[concepts/case-incongruity-comedy]] · [[concepts/plan-clip-forensics]] · [[entities/audio-sense-module]] · [[entities/visual-sense-module]] · [[entities/vision-judge]] · [[concepts/plan-calibration-loop]]
