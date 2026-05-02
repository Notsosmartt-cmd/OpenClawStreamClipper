---
title: "Moment Detection (Stage 4)"
type: concept
tags: [highlight-detection, transcription, scoring, heuristics, llm, three-pass, grounding, stage-4, pass-a, pass-b, pass-c, hub, text]
sources: 2
updated: 2026-04-27
---

# Moment Detection (Stage 4)

The three-pass hybrid detection engine that identifies clip-worthy moments from the transcript. Runs after [[concepts/segment-detection]] (Stage 3) and before frame extraction (Stage 5).

This is the core of what makes the pipeline intelligent — it combines cheap fast heuristics with expensive contextual LLM analysis and then merges them with score cross-validation.

---

## Three-pass architecture

```
Pass A: Keyword scanning     ← instant, no LLM, catches explicit signals
         +
Pass B: LLM chunk analysis   ← expensive, contextual, catches subtle moments
         ↓
Pass C: Merge → Deduplicate → Time-bucket → Select
```

---

## Pass A — Keyword Scanning (instant)

Slides a **30-second window** across the transcript with a **10-second step**.

**Six keyword categories:**

| Category | Example triggers |
|---|---|
| `hype` | "oh my god", "no way", "clip that", "let's go", "holy shit", "clutch", "poggers" |
| `funny` | "i'm dead", "bruh", "that's so bad", "you're trolling", "i'm crying" |
| `emotional` | "i love you", "thank you so much", "from the bottom of my heart", "mental health" |
| `hot_take` | "hot take", "unpopular opinion", "fight me", "hear me out", "controversial" |
| `storytime` | "so basically", "let me tell you", "you won't believe", "long story short", "true story" |
| `reactive` | "what is wrong with", "are you kidding", "i'm so done", "rage", "tilted", "look at this" |

**Universal signals** (add to any category):
- Exclamation clusters (2+)
- ALL CAPS streaks (3+ words)
- Rapid-fire short sentences (4+ in quick succession)
- Laughter markers
- Question clusters (3+)
- Long pauses followed by speech bursts
- **Tier-2 M1 speaker change** (2026-04-27): `speaker_count >= 2 and dominant_speaker_share < 0.7` in the window → +1 to `funny` and `controversial`. Requires diarization (see [[entities/diarization]]).
- **Tier-2 M2 audio events** (2026-04-27): `rhythmic_speech >= 0.7` → +1 to `dancing` and `hype`; `crowd_response >= 0.5` → +1 to `funny` and `hype`; `music_dominance >= 0.6` → +1 to `dancing`. Requires librosa (see [[entities/audio-events]]).

**Segment-specific weight multipliers:**
- "funny" keywords in `irl` segments: 1.4× (IRL comedy is subtler than gaming)
- "controversial" in `reaction`/`debate` segments: 1.5×
- "storytime" in `just_chatting` segments: 1.5×
- etc.

**Dynamic thresholds by segment type:**
- `gaming`: 3 signal points required
- `irl`: 2 signal points required
- `just_chatting`: 2 signal points required
- `reaction`: 3 signal points required
- `debate`: 2 signal points required

Multi-category hits (moment matches 2+ categories) get a bonus point.

**Deduplication**: candidates within 20 seconds of each other merged.

---

## Pass B — LLM Chunk Analysis ([[entities/qwen35]])

Splits transcript into **per-segment-typed chunks** (Tier-1 Q4, 2026-04-27). Window size and overlap are picked from the segment type at `chunk_start + 150 s` (a coarse midpoint peek):

| Segment | Chunk window | Overlap |
|---|---|---|
| `just_chatting`, `irl` | 480 s (8 min) | 60 s |
| `debate` | 360 s (6 min) | 45 s |
| `reaction`, `gaming` | 300 s (5 min) | 30 s |

Defaults remain 300/30 when the segment type is unknown. The wider windows on `just_chatting`/`irl` give storytimes and arguments room to fit in a single chunk instead of getting cut in half. Edge case: a chunk that straddles a segment-type boundary uses the +150 s side's window — accepted tradeoff vs. iterating to a fixed point.

Each chunk is sent to the configured LLM with a segment-specific system prompt.

**Segment-specific prompts:**

| Segment type | What the model looks for |
|---|---|
| `gaming` | Clutch plays, epic wins/losses, rage quits, skill moments |
| `irl` | Funny stories, emotional moments, surprising encounters |
| `just_chatting` | Hot takes, funny stories, emotional vulnerability, audience interaction |
| `reaction` | Strong reactions, controversial takes, emotional responses |
| `debate` | Persuasive arguments, heated exchanges, mic-drop moments |

Style hints from `--style` flag appended to prompt.

**Context-aware detection** (beyond keyword matching):
- Setup + payoff (story begins, then lands)
- Situational irony
- Social dynamics between streamer and audience/guests
- Quotable one-liners
- Narrative arcs

Model returns JSON: `[{time: "MM:SS", score: 1-10, category, why}]`

Lower detection threshold: scores 3–5 are included (let Pass C make the final call).

**Segment score boosts**: quieter segment types (`irl`, `just_chatting`) get +1 so they compete fairly against louder `gaming` moments.

**Thinking model note**: `think=false` required for `qwen3.5:9b`. Thinking mode exhausts the token budget on reasoning without producing output.

**Retry on token exhaustion**: `call_ollama()` detects empty content output and retries with a larger `num_predict` budget automatically.

**`/no_think` sentinel** (2026-04-23): Pass B prompts are prefixed with `/no_think\n` — a Qwen chat-template convention the model honors even when LM Studio's `chat_template_kwargs={enable_thinking: False}` is ignored (as it is on Qwen3.5-35B-A3B). No-op on 9B / Gemma. Reclaims ~2–4 k reasoning tokens per chunk on 35B per `ClippingResearch.md` Additional topic 1.

**Grounding cascade on `why`** (2026-04-23, simplified to 2 tiers 2026-05-01): after `parse_llm_moments` returns, each moment's `why` field is checked by [[entities/grounding]]`.cascade_check()` against a tight ±90 s transcript window plus the full chunk. The cascade runs Tier 1 (regex denylist + overlap + Phase 2.4d zero-count event check) always; escalates to a main-model LLM judge for borderline cases. If the cascade fails, `why` is nulled and `grounding_fail` / `grounding_tier` are attached. The moment itself stays for Pass C scoring — the gate never drops a clip, only prevents a hallucinated summary from propagating into Stage 6's prompt. See [[concepts/bugs-and-fixes]] BUG 26 and the [[concepts/bugs-and-fixes#REMOVAL 2026-05-01b]] retirement of the previous MiniCheck + Lynx tiers.

**Pass B JSON mode** (2026-04-23, Phase 1.2): the Pass B prompt now asks for a top-level `{"moments": [...]}` object instead of a bare `[...]` array, and the `call_llm()` helper requests `response_format: {type: json_object}`. This constrains LM Studio's decoder (llama.cpp / mlx backends) to emit a valid JSON object, dropping the estimated 2-5 % silent parse failures caused by models that occasionally emit text after the closing bracket or drop a comma. `parse_llm_moments` accepts both the legacy bare-array shape and the new wrapped shape, plus common alias keys (`clips`, `highlights`, `items`, `results`) as a safety net.

**Few-shot examples in the prompt** (2026-04-27, Tier-1 Q2): the Pass B prompt now includes three explicit `transcript → JSON moment` examples right before the live transcript: (1) setup-payoff with off-screen voice (the canonical Lacy archetype), (2) long-form storytime with payoff (~90 s), (3) hot take with audience pushback. LLMs follow concrete examples more reliably than prose instructions, especially on smaller / thinking-leaky models. Examples are diverse to avoid clone bias and reinforce that 'why' should describe the SITUATION not the words.

**Prior-chunk context block** (2026-04-27, Tier-1 Q1): after each chunk's Pass B parse + grounding, the pipeline asks the LLM for a one-line summary of that chunk (`max_tokens=200`, `/no_think`, falls back to first ~12 transcript words on failure). The last 2 summaries are injected at the top of every subsequent chunk's Pass B prompt as `Earlier in this stream:` with chunk indices (e.g. "(3/22)"), and the prompt explicitly tells the model to look for SETUP-PAYOFF arcs that span chunks and to name the callback in `why`. Closes the canonical Lacy-penthouse gap where a setup ~10 minutes before the payoff was previously invisible to Pass B.

**Variable clip duration cap** (2026-04-27, Tier-1 Q5): the Pass B prompt now states `Maximum: 150 seconds for storytime/emotional, 90 seconds for everything else`, and `parse_llm_moments`' duration clamp picks `max_dur = 150 if category in ("storytime", "emotional") else 90`. Lets genuine multi-minute storytimes survive instead of being trimmed to 90 s mid-arc.

**Speaker annotation on LLM moments** (2026-04-27, Tier-2 M1): after parsing, each LLM moment is annotated with `dominant_speaker` / `speaker_count` / `dominant_speaker_share` from the segments overlapping its ±15 s payoff window. Pass C uses these for the speaker-change boost.

---

## Pass B-global — Two-stage Pass B ([[concepts/two-stage-passb]])

Runs immediately after Pass B-local finishes (Tier-3 A1, 2026-04-27). Reuses Tier-1 Q1's `chunk_summaries` to build a one-line-per-chunk skeleton, then makes a SINGLE Gemma call asking for cross-chunk arcs (irony, contradiction, fulfillment, theme_return, exposure, prediction). Validated arcs are appended to `llm_moments` with `category="arc"`, `cross_validated=True`, and a 1.4× score boost.

Closes the long-range narrative gap that Pass B-local + Tier-1 Q1's 2-chunk window can't reach. Cheap (~30-60 s on a single Gemma call) and skips silently when no chunk_summaries are available.

---

## Pass B+ — Long-range callback detection ([[entities/callback-module]])

Runs after Pass B-global, before Pass C. Embeds the transcript with sentence-transformers, FAISS-searches for setup windows ≥ 5 min before each top-K candidate's payoff, and gates each pair through a small Pass-B' LLM judgment. Surviving callbacks are appended to `llm_moments` with `category="callback"`, `cross_validated=True`, and a 1.5× score boost. See [[concepts/callback-detection]] for the full picture.

Skipped silently when `sentence-transformers` isn't installed or Pass B produced zero anchor moments.

---

## Pass C — Merge, Select, Distribute

1. **Normalize keyword scores**: per-category ceiling (Tier-1 Q3, 2026-04-27). High-noise categories (`hype`, `reactive` 0.75; `funny`, `dancing` 0.70) keep the old conservative cap because their keyword phrases ("bruh", "lmao", "let's go") are weak signal in isolation. Categories whose keyword phrases are RARE and semantically specific (`storytime` 0.90; `hot_take`, `emotional`, `controversial` 0.85) get a higher cap — a cluster of "let me tell you" / "unpopular opinion" phrases is high signal even without LLM cross-validation.

2. **Cross-validation boost**: moments detected by **both Pass A and Pass B** get a multiplicative ×1.20 boost and a `cross_validated=true` flag. Strong signal that a moment is genuinely noteworthy.

   **Tier-2 M1 speaker boost** (2026-04-27): on top of cross-validation, multi-speaker moments (`speaker_count >= 2 and dominant_speaker_share < 0.7`) get an additional ×1.15 boost. Captures off-screen voice / friend interruption / banter patterns without crowding out cross-validated moments (M1 boost is smaller).

3. **Style weighting**: multipliers applied based on `--style` flag (e.g., `--style funny` gives funny moments 1.4× multiplier)

4. **Category cap**: for `auto` style, no single category can exceed 60% of final candidates

5. **Time-bucket distribution** (prevents early-VOD bias):
   - VOD divided into equal time buckets: `2 per hour`, range 3–10 buckets
   - **Phase 1**: guaranteed clip from each bucket (ensures spread across full VOD)
   - **Phase 2**: overflow slots filled by highest-scoring remaining moments
   - **Phase 3**: style-aware re-ranking (variety style = round-robin by category; specific style = re-sort by weighted score)

6. **Temporal spread**: minimum 45-second gap between any two final selected clips

7. **MAX_CANDIDATES**: selects up to 2× the target clip count (some may not survive vision; all actually do since vision is non-gatekeeping, but the over-selection provides margin)

---

## The filtering funnel

```
Full transcript
      ↓ Pass A keyword scan
Raw keyword candidates (many)
      +
      ↓ Pass B LLM analysis
Raw LLM candidates (fewer, higher quality)
      ↓ Pass C: normalize, cross-validate, time-bucket, select
Final candidates (target count × 2)
      ↓ Stage 6 vision enrichment (score boosts only)
Rendered clips (all candidates rendered)
```

---

## Related
- [[concepts/segment-detection]] — Stage 3; segment types used in Stage 4 prompts and weights
- [[concepts/vision-enrichment]] — Stage 6; what happens after candidates are selected
- [[concepts/clipping-pipeline]] — full pipeline context
- [[entities/qwen35]] — the LLM used in Pass B
