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

## Can the senses be converged? Yes — five options (expanded 2026-07-02)

> [!note] The dual-GPU distinction (read first — it decides what fits where)
> The rig has **two serving lanes with different GPU reach**:
> - **LM Studio (llama.cpp, Vulkan)** pools BOTH GPUs — RTX 5060 Ti **16 GB** (NVIDIA) + RX 6700 XT **12 GB** (AMD) ≈ **28 GB** — which is how the 35B-A3B MoE runs today ([[concepts/vram-budget]]).
> - **PyTorch/CUDA lane** (faster-whisper, CLAP, EasyOCR, or any model served via transformers/vLLM) sees **only the 16 GB NVIDIA card** — no usable ROCm on Windows, so the AMD card is invisible to torch.
>
> Consequence: anything that must be served *outside* LM Studio is 16 GB-bound; anything served *through* LM Studio gets 28 GB but only LM Studio's input types (text + images — **no audio-in**). This tension bites hardest on option 5.

### 1. Symbol-level timeline fusion — RECOMMENDED (near-term, local, cheap)
Build one **time-aligned event stream** interleaving transcript words, CLAP audio events, motion spikes, and cuts — exactly what [[concepts/plan-clip-forensics]]'s offline timeline already produces — and hand *that* to the text LLM: `[t=6.2] AUDIO screaming(0.46) | MOTION 6.3× | TEXT "chill buddy, chill"`. The LLM reasons over the **conjunction** instead of each sense separately. This is the [[concepts/case-incongruity-comedy]] anomaly-proposer: propose where audio/motion *contradicts or exceeds* the words, then let Stage 5.5/6 verify. Fixes the sequencing gap at the proposal end. All sensors already built + verified ([[entities/audio-sense-module]], [[entities/visual-sense-module]]); missing = timeline-builder + prompt + `src=ANOMALY` lane (flag-gated, boost-only).
**Rig fit:** no new VRAM — the LLM call goes through the existing LM Studio 28 GB pool; sensors run CPU-default. **Limit:** the model reasons over *named* events, not raw signals — symbol-level, not perception-level. For incongruity comedy that's likely sufficient (the forensics run on ReemKnocks named everything needed: `bruh` cluster 1–7.5 s, 9 motion spikes, "chill buddy" words, suspense bed 6–14 s).

### 2. Interaction features in scoring (cheap complement)
The multiplier chain (and the future [[concepts/plan-calibration-loop]] logistic ranker) is late fusion with **no interaction terms**. Add fitted conjunction features — `motion_high × words_banal`, `audio_event_strength × low_keyword_score` — so even the arithmetic layer rewards cross-modal incongruity. **Rig fit:** CPU sklearn, trivial. **Limit:** captures only the conjunctions you hand-name as features.

### 3. Richer joint prompts at Stage 5.5/6 (upgrade the existing fusion point)
Feed the judge/enricher the same event timeline alongside the frames (and/or a timestamped frame strip for pseudo-motion) so the one place that already does joint inference sees *named audio* + *motion*, not just words + stills. **Rig fit:** same models as today (LM Studio pool); only prompt cost. **Limit:** improves ranking/verification, not proposal — still downstream of the transcript-only gate unless (1) exists.

### 4. Cross-modal embedding incongruity signal (experimental)
CLAP embeds audio and text in one space; a large audio↔text (or audio↔frame-caption) *distance* during a high-energy window is a numeric "the senses disagree" detector — a candidate selection axis. **Rig fit:** CLAP is small and runs on the CPU/CUDA-16 GB lane already. **Limit:** CLAP cosines run low/uncalibrated on this corpus (verified 2026-06-21) → real per-corpus calibration burden before this is trustworthy.

### 5. True omni models — the "real" answer, blocked by tooling not VRAM
One network ingests **raw audio waveforms + video frames + text in the same context window**; fusion happens inside the model. Qwen-Omni-class models use **TMRoPE** (time-aligned multimodal rotary position embeddings) — audio and video tokens interleaved on a shared timeline — i.e. option 1's timeline done natively at the token level. The model would *perceive* the bus-clip conjunction (panic prosody + shove motion + banal words), not infer it from labels.

| Candidate | Size | Senses in | Fits which lane? |
|---|---|---|---|
| Qwen2.5-Omni | 7B | audio+video+image+text | ~6–7 GB 4-bit → fits the **16 GB CUDA lane** (transformers/vLLM, audio-in works) |
| Qwen3-Omni | 30B-A3B MoE (~3B active) | audio+video+image+text | ~18 GB 4-bit → **needs the 28 GB LM Studio pool** — but LM Studio has no audio-in |
| MiniCPM-o 2.6 / Phi-4-multimodal | ~6–8B | audio(+video)+image+text | 16 GB CUDA lane |
| GPT-4o / Gemini | cloud | everything | violates local-only design |

**The dual-GPU catch-22 (today):** the server that can reach the 28 GB pool (LM Studio) **can't hear** (no audio/video content parts in its OpenAI-compat API), and the servers that can hear (transformers/vLLM) **can't reach the pool** (CUDA-only → 16 GB → only 7B-class omni fits → markedly weaker reasoner than the 35B text MoE). Additional blockers: token explosion (audio ≈ 25 tok/s, frames = hundreds of tokens each → a 45 s window ≈ 10–20k tokens — fine per-candidate, ruinous per-VOD) and unproven comedy judgment at 7B scale. llama.cpp's multimodal layer gained audio support in 2025, so the tooling gap is closing — **re-check LM Studio audio-in status when revisiting**.
**Realistic endgame:** hybrid — option 1 proposes cheaply across the whole VOD; an omni model *verifies* the top-N 30–60 s windows with true perception, as/alongside the Stage 5.5 judge.

## Recommended sequence (when the owner green-lights implementation)

1. Timeline-builder (merge transcript + `audio_sense` + `visual_sense.motion_events` into one ordered stream) → 2. anomaly-proposer producing `src=ANOMALY` candidates (flag-gated, failure-soft, boost-only entry into Pass C) → 3. pass the timeline into the Stage 5.5 judge prompt → 4. interaction features once the calibration ranker lands. Steps 1–3 reuse only already-verified components.

## Related
- [[concepts/model-senses]] · [[concepts/case-incongruity-comedy]] · [[concepts/reference-humor-2026-07]] · [[concepts/plan-clip-forensics]] · [[entities/audio-sense-module]] · [[entities/visual-sense-module]] · [[entities/vision-judge]] · [[concepts/plan-calibration-loop]] · [[concepts/vram-budget]]
