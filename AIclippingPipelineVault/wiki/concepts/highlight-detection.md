---
title: "Moment Detection (Stage 4)"
type: concept
tags: [highlight-detection, transcription, scoring, heuristics, llm, three-pass]
sources: 2
updated: 2026-04-07
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

Splits transcript into **5-minute chunks with 30-second overlap**. Each chunk sent to `qwen3.5:9b` with a segment-specific system prompt.

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

---

## Pass C — Merge, Select, Distribute

1. **Normalize keyword scores**: capped at 8 (prevents keyword-dense but mediocre moments from dominating)

2. **Cross-validation boost**: moments detected by **both Pass A and Pass B** get +1.5 score and a `cross_validated=true` flag. Strong signal that a moment is genuinely noteworthy.

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
