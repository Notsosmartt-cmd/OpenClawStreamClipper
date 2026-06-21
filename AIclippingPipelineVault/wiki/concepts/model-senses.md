---
title: "Pipeline Perception Inventory — what each model 'senses'"
type: concept
tags: [perception, senses, models, audio, vision, detection, reference, gaps]
sources: 0
updated: 2026-06-13
---

# Pipeline Perception Inventory

What each model/module in the clipper can actually *perceive*. Filed 2026-06-13 so the perceptual surface (and its blind spots) is explicit when planning new detection/manipulation features. The key takeaway: **the pipeline transcribes speech and reads three crude audio dials, but has no semantic audio recognition and its vision sees stills, not motion.**

---

## The senses, component by component

| Component | Sense | What it actually perceives | Blind to | Where |
|---|---|---|---|---|
| **Whisper** (faster-whisper large-v3-turbo) | Hearing → **words only** | Speech → text + word-level timestamps | Every non-speech sound — a Vine boom, a quack, a music swell are *not words*, so they're invisible | [[entities/speech-module]] / `scripts/lib/speech.py` |
| **Text LLM** (Qwen, Pass B/D, Stage 3) | Reading | The transcript string + prompts | Anything not in text | `stage4_moments.py`, `stage4_rubric.py`, `stage3_segments.py` |
| **Vision model** (Stage 6 + 5.5 judge) | Sight → **6 still frames** | 6 JPEGs per moment (T−2…T+5) | Motion, the run between frames, the rest of the clip — it sees a slideshow, not video | [[concepts/vision-enrichment]], [[entities/vision-judge]] |
| **`audio_events.py`** (librosa DSP) | Hearing → **3 crude dials** | `rhythmic_speech`, `crowd_response`, `music_dominance` — hand-thresholded scalars over 10 s windows | *What* the sound is — it's signal energy, not recognition (the "thresholding" ceiling) | [[entities/audio-events]] |
| **diarization** (pyannote) | Hearing → **who** | Per-segment speaker labels | What is said/played | [[entities/diarization]] |
| **chat_features** | Metadata | Twitch chat event counts (sub/bit/raid/donation) | The video/audio itself | [[entities/chat-features]] |

---

## Two structural blind spots

> [!warning] 1. No semantic audio recognition
> Nothing in the pipeline can name a non-speech sound. It cannot tell that an editor dropped a suspenseful music sting, a quack over a curse, or a boom after a punchline. Audio perception = "transcribe speech + three energy dials." This is the single biggest perceptual gap and the root cause behind the missed competitor clips ([[concepts/case-incongruity-comedy]]) and the inability to reverse-engineer competitor editing. The fix is a semantic audio-sensing layer — see [[concepts/plan-clip-forensics]].

> [!warning] 2. Vision sees stills, not motion
> Stage 6/5.5 see 6 sampled JPEGs per moment, not the video. Physical comedy, motion spikes, a failed push, camera chaos — the *between-frame* signal — is invisible. Optical-flow / frame-diff motion sensing is the visual half of the same gap.

---

## Why it matters (proposal-time vs enrichment-time)

These senses are also **sequenced**: the transcript is consulted at *proposal* time (Pass A/B decide what's clip-worthy), while audio dials, diarization, and vision are all **boost-only / enrichment-time** — they can only nudge or describe a moment the transcript already proposed. So a moment carried by sound or motion with a thin transcript can never be *surfaced*, only (at best) boosted if something else surfaced it. The architectural consequence is detailed in [[concepts/case-incongruity-comedy]] (the anomaly-proposer lane) and the sensing upgrade in [[concepts/plan-clip-forensics]].

## Related
- [[concepts/plan-clip-forensics]] — the plan to add semantic audio/visual sensing + competitor-clip decomposition
- [[concepts/case-incongruity-comedy]] — the missed-clip class these blind spots cause
- [[concepts/clipping-intelligence]] — the decision-system hub
- [[entities/audio-events]] — the DSP layer that the semantic sensing would upgrade
