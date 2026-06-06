---
title: "Segment & Moment Detection — end-to-end walkthrough"
type: concept
tags: [stage-3, stage-4, segment-detection, moment-detection, pass-a, pass-b, pass-c, a1, m3, walkthrough, overview, text]
sources: 1
updated: 2026-06-06
---

# Segment & Moment Detection — end-to-end walkthrough

Consolidated view of how Stage 3 (segment detection) and Stage 4 (moment detection) actually work and connect. Detailed sub-mechanisms live in [[concepts/segment-detection]], [[concepts/highlight-detection]], [[concepts/two-stage-passb]], [[concepts/callback-detection]], [[concepts/clip-duration]]. Mapped 2026-06-06 from `scripts/lib/stages/stage3_segments.py` + `stage4_moments.py`.

## Stage 3 — Segment Detection (plumbing for Stage 4)

Classifies the VOD *timeline* into stream types so later stages adapt.

1. Window the timeline into **10-min (600 s)** chunks (`stage3_segments.py:49`).
2. Condense each to **~600 words** → one text-LLM call classifying into **one of 5 types**: `gaming`, `irl`, `just_chatting`, `reaction`, `debate` (temp 0.1, answer-only, `:82-93`). An optional **stream-type hint** biases (not forces) it (`:35-46`). Handles thinking-model quirks (`/no_think` + `enable_thinking:false`, 6000-token budget, `reasoning_content` fallback, `:127-150`).
3. **Merge adjacent same-type windows** (`:166-171`) → build a **stream profile**: `dominant_type`, `dominant_pct`, `type_breakdown`, `is_variety` (<60 %).

**Outputs:** `segments.json` + `stream_profile.json`. **Why it matters:** the segment map drives Stage 4's **chunk sizing** (just_chatting/irl 8 min, gaming/reaction 5 min), **segment-aware thresholds + score boosts** in Pass A, and variety handling. It is *not* itself a clip selector — only roughly-right plumbing.

## Stage 4 — Moment Detection (layered, multi-signal)

Five components feed one selection step. Design philosophy: **Pass A = deterministic recall net; Pass B/A1/M3 = semantic precision + cross-chunk reach; Pass C = arbiter.**

### Pass A — keyword/heuristic scan (high recall, deterministic)
Sliding **30 s window / 10 s step** (`:429-430`). Accumulates `total_signals` from independent sources: literal **keyword hits** across 8 categories (hype/funny/emotional/hot_take/storytime/reactive/dancing/controversial) with per-category ceilings (Q3); **conversation-shape** signals (off-screen intrusions→controversial, pushback, story_opener+monologue→storytime, claim_stake+≥2 speakers, `:560-579`); **audio events M2** (rhythmic→dancing/hype, crowd→funny/hype, music→dancing, `:587-601`); **diarization M1** speaker context. Clears a **segment-specific threshold** → emits a `keyword` moment (S-curve normalized, top category, `:604-626`). Can't time out or self-limit → the safety net under the LLM.

### Pass B — LLM chunked detection (high precision, semantic)
Walks the VOD in per-segment chunks (480/360/300 s + overlap) with the **Pattern Catalog** prompt + a **prior-context block** (last 2 chunk summaries). Model returns `start/end/category/score/why/pattern`; `why` runs the **grounding cascade** (denylist+overlap+judge) to null hallucinations; M1 speaker annotation added. Hardening: dead-chunk gate (default off), end-of-pass **re-queue** of failed chunks, **de-tidy** prompt, **arc-aware chunk cards** per chunk.

### A1 — two-stage global pass (cross-chunk arcs)
One global LLM call over a type-grouped register of all chunk cards → **setup→payoff arcs spanning chunks** (irony/contradiction/fulfillment/exposure/prediction/theme-return) → payoff-centered `arc` moments (cross_validated, 1.4× boost, Phase 2.5 guarantee).

### M3 — callback detection (cross-chunk, embedding-based)
sentence-transformers + FAISS find an earlier window ≥5 min back semantically similar to a payoff, confirmed by an LLM judge → `callback` moments.

### Pass C — merge + select (the funnel)
1. **Dedup** within 25 s (LLM boundaries win on merge; marked cross_validated).
2. **Score**: `base × style × cross-val(1.2) × speaker(1.15) × clamp(axes A/B/C/E [0.8,1.35]) × length_penalty × position_weight`, then 70/30 within-bucket blend. Axes: A arc-completeness, B reaction, C baseline-contrast, E engagement.
3. **Distribute**: time buckets (~2/hour), top per bucket + round-robin overflow, duration-aware spacing, + the Phase 2.5 arc guarantee.

Then **Pass D** (rubric judge), **boundary-snap** (Stage 4.5), **Stage 5.5** (vision-judge re-rank) refine the chosen set.

## How they connect
Stage 3 answers *"what content, where?"*; Stage 4 answers *"which exact moments?"* — reading Stage 3's map for chunk size, thresholds, weighting. No single failure drops a moment: Pass A guarantees recall, the LLM passes add precision + cross-chunk reach, Pass C arbitrates.

## Known limitations (improvement targets — see [[concepts/detection-improvements]])
1. **Segment classification is coarse** (10-min granularity) — a short off-type pocket (2-min debate in a gaming stream) is absorbed into the dominant label. *Bounded:* moment-level detection is type-agnostic, so the pocket's clips are still found; only Pass A thresholds + chunk sizing are slightly off.
2. **Pass A keyword lists are literal/hand-tuned** — brittle, language/streamer-specific (only the keyword *term*; the shape/audio/speaker signals aren't crude).
3. **Cross-chunk arcs/callbacks emit payoff-centered clips** — setup kept as metadata, not a contiguous setup→payoff span.
4. **The funnel skews ~30 s** — from the per-category default fallback + the `length_penalty` (see [[concepts/clip-duration]]).

## Related
- [[concepts/segment-detection]] · [[concepts/highlight-detection]] · [[concepts/clip-duration]]
- [[concepts/two-stage-passb]] · [[concepts/arc-aware-extraction]] · [[concepts/callback-detection]]
- [[concepts/moment-discovery-upgrades]] — the Tier 1/2/3 upgrade hub
