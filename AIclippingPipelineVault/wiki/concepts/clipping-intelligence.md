---
title: "Clipping Intelligence — Prompt Engineering & Heuristics (evaluation)"
type: concept
tags: [prompt-engineering, heuristics, scoring, llm, pass-a, pass-b, pass-c, pass-d, vision, grounding, pattern-catalog, evaluation, hub]
sources: 3
updated: 2026-06-04
---

# Clipping Intelligence — Prompt Engineering & Heuristics

A holistic map **and critical evaluation** of every place the pipeline "decides what is clip-worthy." This page ties together the per-stage pages ([[concepts/segment-detection]], [[concepts/highlight-detection]], [[concepts/vision-enrichment]]) into a single view of the *decision system*, then evaluates it: what's strong, what's fragile, and what to improve.

> [!note] Scope
> This is the **intelligence layer** — the prompts and the scoring math. It is independent of the bare-metal port ([[concepts/bare-metal-windows]]); the same prompts and constants run on Docker or native. Frozen source/config snapshot lives in `archive/clipping-intelligence-2026-06-04/` (see end of page).

---

## The decision system at a glance

The pipeline never makes one "is this a clip?" call. It runs a **funnel of cheap→expensive proposers** and a **stack of multiplicative re-rankers**, governed by one principle:

> **Heuristics and LLMs only PROPOSE and RE-RANK. Only hard validation REMOVES.** Vision, grounding, and the rubric can strip metadata or nudge a score — they can never delete a surviving candidate. (See the non-gatekeeping note in [[concepts/vision-enrichment]].)

```
Stage 3  Segment classification (1-word LLM)  → routes ALL downstream weights/prompts
Stage 4  Pass A  keyword scan      (heuristic, no LLM)   ─┐
         Pass B  per-chunk LLM     (Pattern Catalog)      ├─ propose
         Pass B-global  arc skeleton (1 LLM call)         │
         Pass B+  callbacks (embeddings + LLM judge)      ─┘
         Pass C  merge → cross-validate → bucket → select (heuristic re-rank)
         Pass D  rubric judge (7-dim LLM)                 ─ re-score
Stage 6  Vision enrichment (multimodal, non-gatekeeping)  ─ boost + title
   ↑     Grounding cascade (regex + LLM faithfulness judge) runs on every generated "why"/title
```

Every arrow except Pass A is an LLM call. A 3-hour VOD makes **dozens to low-hundreds** of model calls across these layers.

---

## Layer 1 — Segment classification (the router)

`scripts/lib/stages/stage3_segments.py`. 10-min windows → first ~600 words → 9B model with `num_predict=10` → exactly one of `gaming | irl | just_chatting | reaction | debate`. Adjacent same-type windows merge. `--type` is a soft bias.

This one word **routes everything**: Pass A keyword weights + thresholds, Pass B chunk size + instructions + score boost, and Stage 6 context. It is the highest-leverage and cheapest decision in the system — and (see evaluation) its biggest unguarded single point of failure.

---

## Layer 2 — Pass A keyword scan (pure heuristic)

`stage4_moments.py:288-500`. 30 s window / 10 s step. The only LLM-free proposer.

| Mechanism | Where | What it does |
|---|---|---|
| 8 keyword categories | `KEYWORD_SETS` :197-266 | literal substring lists (hype/funny/emotional/hot_take/storytime/reactive/dancing/controversial) |
| Segment weight multipliers | `SEGMENT_KEYWORD_WEIGHTS` :270-276 | e.g. `funny`×1.4 in `irl`, `storytime`×1.5 in `just_chatting` |
| Dynamic threshold | `SEGMENT_THRESHOLD` :280-286 | gaming/reaction = 3 signals; irl/just_chatting/debate = 2 |
| Universal signals | :327-375 | exclamation clusters, ALL-CAPS streaks, rapid-fire, laughter, question clusters, long-pause-then-burst, multi-category bonus |
| Optional signal boosts | :377-462 | diarization speaker-change (M1), audio events (M2), conversation-shape markers (4.2) — all additive, all degrade silently |
| Score normalization | :468-474 | `((signals − threshold) / 8) ** 0.8` — S-curve, capped at 1.0 |
| Dedup | :491-500 | merge candidates within 20 s, keep higher score |

**Design intent:** cheap, high-recall, low-precision. It's allowed to be noisy because Pass C and cross-validation filter it.

---

## Layer 3 — Pass B per-chunk LLM (the heart of the prompt engineering)

`stage4_moments.py`. The prompt is assembled per chunk (:1098-1136 for the Pattern-Catalog form; :1141-1184 legacy fallback). Anatomy:

1. **`/no_think` sentinel** — Qwen chat-template convention to suppress reasoning even when LM Studio ignores `enable_thinking:false` (the BUG 57 leak). No-op on 9B/Gemma.
2. **Role + segment frame** — "You are a stream clip scout. This is a `{SEGMENT}` segment."
3. **Segment-specific instructions** — `SEGMENT_PROMPTS` :875-927 (what counts as a clip per segment type).
4. **Style hint** — `style_prompts` :939-950 (from `--style`).
5. **Prior-context block** (Tier-1 Q1) — last 2 chunk one-line summaries + an explicit "look for setup→payoff arcs across chunks" nudge.
6. **Conversation-shape block** (Tier-4 4.2) — turn graph, off-screen intrusions, monologue runs, discourse markers.
7. **Pattern Catalog** (Tier-4 4.3, `config/patterns.json`) — 10 named interaction shapes with one-paragraph *signatures*. The model is told to **match a signature, not keywords**, set `primary_pattern`, and justify in `why` by naming the satisfied signature.
8. **Boundary + JSON schema** — `time/start_time/end_time/score 1-10/category/primary_pattern/secondary_patterns/why`; clip 15 s min, 150 s storytime/emotional else 90 s.

Post-processing: defensive JSON parse (`parse_llm_moments` :697) with BUG-35 duplicate-timestamp drop, score → `(s−1)/9`, category canonicalization map, duration clamp, pattern-ID validation; `SEGMENT_SCORE_BOOST` (+0.10 for irl/just_chatting); then the **grounding cascade nulls any hallucinated `why`** (:1204-1272); then speaker annotation (M1).

**Two more LLM sub-passes hang off Pass B:**
- **Per-chunk summary** (:1316-1342) — one line, feeds the prior-context block and the A1 skeleton. Token budget bumped to 4000 to survive Gemma's permanent thinking.
- **Pass B-global / A1** (:1429-1451) — a *single* call over the whole-stream skeleton asking for cross-chunk setup-payoff arcs (`irony|contradiction|fulfillment|theme_return|exposure|prediction`), ×1.4 boost.
- **Pass B+ / M3 callbacks** (:1603-1632) — sentence-transformer + FAISS retrieval gated by a small LLM judge, ×1.5 boost.

---

## Layer 4 — Pass C selection (pure heuristic re-rank)

`stage4_moments.py:1636-2091`. No LLM. This is where the proposals become a ranked, time-spread shortlist. It is a **chain of multiplicative factors** plus an anti-bias distribution.

| Factor | Where | Value |
|---|---|---|
| Per-category keyword ceiling | `KEYWORD_CEILING` :1650 | 0.70–0.90 (rare/specific phrases get higher cap) |
| Dedup window | :1665-1745 | merge within 25 s |
| **Cross-validation boost** (A∩B) | :1691, :1808 | ×1.25 at merge, ×1.20 at ranking (the system's strongest lever) |
| LLM-authoritative merge | :1672-1733 (BUG 56) | when keyword+LLM merge, LLM owns peak T / boundaries / why / pattern |
| Length penalty | `length_penalty` :1752 | 1.0 (≤30 s) → 0.65 (>75 s) |
| Style weighting | `weight_map` :1789 | ×1.3 to the on-style category |
| Speaker-change boost (M1) | :1815 | ×1.15 multi-speaker, no dominant voice |
| Position weighting | `position_weight` :1850 | 0.88 (cold open) → 1.05 (prime 30-70%) → 0.92 (outro) |
| Time-bucket distribution | :1830-1971 | 2 buckets/hr (3-10); Phase-1 guaranteed pick/bucket, Phase-2 round-robin overflow (BUG 36), within-bucket 70/30 normalization, Phase-3 style re-rank |
| Category cap (auto) | :1998 | no category > 50% of clips |
| Soft-cap | BUG 37, :1820-1827 | rank on *raw* score (can exceed 1.0); clamp to [0,1] only at the display field |
| **Selection axes** (A arc, B reaction, C baseline, E engagement) | `arc_completeness.py` / `reaction_signals.py` / `baseline_contrast.py` / `engagement_signals.py` | each a bounded, failure-soft `×mult` (A may demote to 0.85; B ≤1.10; C ≤1.18; E ≤1.12, all boost-only) accumulated into one `axis_mult` product. C's per-VOD baseline is computed once before the loop |
| **Global axis-product clamp** | overhaul eval #1 | the accumulated A-E axis product is clamped to **[0.80, 1.35]** before being applied once — the coordinating guardrail so correlated axes can't compound and run away |

> [!note] Selection axes (Plans A-E) — the 2026-06-04 overhaul
> Plans A (arc-completeness), B (reaction-worthy), C (baseline-contrast), and E (engagement/discussion) are live as Pass C pre-signals; D deferred. E also adds an `engagement` style + the `media-pause-commentary` Stage 6 vision archetype.
> They no longer each multiply `styled_score` independently — they **accumulate into one clamped product**.
> See [[concepts/clipping-quality-overhaul]] §Cross-axis design guardrails for the compounding analysis and
> the rebalanced ceilings. (This resolved the "uncalibrated multiplier chain" weakness noted below.)

---

## Layer 5 — Pass D rubric judge (LLM, structured)

`stage4_rubric.py`. Re-scores every Pass C survivor on a **7-dimension 0-10 rubric** (prompt :159-207): `setup_strength, payoff_strength, originality, broad_appeal, replay_value, audio_quality, self_contained`. Weighted (`config/rubric.json`, payoff 0.25 is the heaviest) into `rubric_score∈[0,1]`, then blended: **`final = 0.6·pass_c + 0.4·rubric`**. Confirms/overrides the Pattern Catalog label. Failure-soft (keeps Pass C score on error; 3 network failures abort). MMR diversity re-rank in `stage4_diversity.py` (`mmr_lambda=0.7`).

---

## Layer 6 — Vision enrichment (multimodal, non-gatekeeping)

`stage6_vision.py:374-414`. 6 frames (T−2…T+5) in one call. Prompt enforces **grounding rules** ("describe the payoff / what the transcript literally says; the overlay is ambient, not the subject"), asks the model to **reason about change** across frames, and emits render hints (`mirror_safe`, `voiceover`, `interaction_shape`, `pattern_match`, `gaze_direction`) + an A2 callback-continuity check. Score boost only (×1.15 / ×1.08); never gates. Regenerate-once on grounding failure.

---

## Cross-cutting — the grounding cascade

`grounding.py`. Runs on every generated `why`/`title`/`hook`. **Tier 1** (always, stdlib): regex denylist + content-word overlap + zero-count hard-event check (kills "gifted subs" when chat shows zero subs). **Tier 2** (ambiguous cases): a 5-dimension LLM **faithfulness judge** (prompt :273-297 — grounding / setup_payoff / speaker / conceptual / callback, weighted mean vs `pass_threshold`). It only strips unsupported text; it never drops a clip.

---

## Evaluation

### What's strong

> [!note] 1. Non-gatekeeping funnel is the right posture
> For highlight detection, a missed clip is invisible and a bad clip is just skipped by the human — so recall matters more than precision. Making everything except hard validation *advisory* (propose/re-rank, never delete) is the correct, and well-executed, core decision.

> [!note] 2. Cross-validation is principled
> Agreement between an independent cheap detector (keywords) and an expensive one (LLM) is genuine Bayesian evidence, and it's wired in as the dominant lever (×1.20-1.25). Sound in principle.

> [!note] 3. Segment-conditioning is high-leverage domain knowledge
> One cheap classification routes weights, thresholds, chunk sizes, and prompts. Encodes real insight ("IRL comedy is subtler → lower the bar; gaming is noisy → raise it").

> [!note] 4. The Pattern Catalog is the most sophisticated piece
> Moving from "find funny moments" to "match one of 10 named interaction shapes with a structural signature" gives the model a *taxonomy to reason against*. It cuts vague matches, makes `why` auditable, makes the system user-extensible via config, and aligns Pass B → Pass D → vision on a shared vocabulary.

> [!note] 5. Anti-bias machinery and operational hardening are mature
> Time-bucketing + round-robin overflow + within-bucket normalization + position weighting directly attack early-VOD and score-saturation bias. The BUG 36/37 fixes (round-robin, soft-cap ranking), `/no_think` + `reasoning_content` fallback + fail-fast streaks show the system was hardened against real model behavior, not theory.

### What's fragile

> [!warning] 1. The scoring math is plausible but UNCALIBRATED
> The final score is a product of ~6 hand-tuned multipliers (×1.20 cross-val, ×1.15 speaker, ×1.3 style, 0.65-1.0 length, 0.88-1.05 position, 0.70/0.30 bucket blend, the `**0.8` S-curve, 0.6/0.4 rubric blend). Nobody has measured whether ×1.20 cross-val is correctly sized relative to ×1.15 speaker — there is **no ground-truth calibration loop**. The numbers are individually reasonable and collectively unprincipled. This is the single biggest latent weakness. The eval tool ([[entities/bootstrap-twitch-clips]]) exists but isn't wired into a feedback loop that fits these constants.

> [!warning] 2. "Cross-validation" across LLM layers is weaker than it looks
> Pass B proposes a pattern, Pass D confirms it, Stage 6 visually confirms it — **all the same base model**. A model biased toward `storytelling_arc` will propose, confirm, and visually-confirm the same wrong label. These agreements are **correlated**, so the confidence they imply is overstated. (Pass A↔Pass B cross-val is genuinely independent; Pass B↔D↔vision is not.)

> [!warning] 3. Keyword lists are brittle, English-only, and substring-matched
> `"pog" in combined` fires on "pogo stick"; no word boundaries. The slang is of-its-moment and will rot. Low precision is intentional, but it inflates the cross-validation denominator with junk co-fires.

> [!warning] 4. "What's a clip" knowledge is spread across 4+ surfaces that can drift
> `SEGMENT_PROMPTS` (code constant), `style_prompts` (code constant), `config/patterns.json` (catalog), `config/streamer_prompts.json` (Whisper), plus the legacy 6-rule fallback prompt carried inline. No single source of truth; the fallback can silently diverge from the catalog path.

> [!warning] 5. Segment classification is an unguarded single point of failure
> One word from a 9B on the first 600 words of a 10-min window, no confidence score, no smoothing beyond same-type merge. A chatting break mislabeled "gaming" silently applies the wrong threshold/weights/prompt to that whole window.

> [!warning] 6. The Catalog's best patterns depend on optional signals
> `setup_external_contradiction` / `reading_chat_reaction` lean on conversation-shape regex + diarization + vision gaze — all of which degrade silently when the HF token, librosa, or chat are absent. The most valuable patterns are only as reliable as the optional signal stack underneath them.

### Opportunities (ranked by leverage)

> [!todo] A. Close the calibration loop (highest value)
> Wire [[entities/bootstrap-twitch-clips]] real-Twitch-clip timestamps in as positive labels, build an offline scorer (recall@N + ranking correlation), and *fit* the multipliers (even a coarse grid search) instead of hand-setting them. Converts the whole stack from "vibes" to "measured."

> [!todo] B. Move to additive log-space scoring
> Replace the multiplier chain with summed log-weights (a tiny logistic model). Same behavior, but interpretable, fittable from (A), and free of the saturation that forced the BUG-37 soft-cap hack.

> [!todo] C. De-correlate the LLM panel
> Where Pass B/D/vision "agree," vary prompt framing or temperature so they aren't the same call three times — or explicitly down-weight same-model agreement relative to Pass A↔B agreement.

> [!todo] D. Cheap precision + maintainability wins
> Word-boundary keyword matching; per-channel keyword packs mirroring `streamer_prompts.json`; unify `SEGMENT_PROMPTS`/`style_prompts`/catalog into one editable config; add a confidence (logprobs or 2-of-3 vote) + boundary smoothing to segment classification; surface low-confidence/grounding-nulled detections to the dashboard instead of only stderr.

---

## Tuning quick-reference (where to change behavior)

| Want to change… | Edit |
|---|---|
| What each segment treats as clip-worthy | `SEGMENT_PROMPTS` in `stage4_moments.py:875` |
| The named interaction shapes | `config/patterns.json` |
| Cross-val / speaker / style / length / position multipliers | Pass C constants in `stage4_moments.py:1650-1864` |
| Rubric dimension weights + Pass C/rubric blend | `config/rubric.json` |
| Style → pattern boosts | `config/style_pattern_weights.json` |
| Whisper slang biasing | `config/streamer_prompts.json` |
| Grounding strictness | `config/grounding.json`, `config/denylist.json` |

---

## Related
- [[concepts/segment-detection]] — Layer 1 detail
- [[concepts/highlight-detection]] — Layers 2-4 detail (Pass A/B/C)
- [[concepts/two-stage-passb]] — Pass B-global / A1
- [[concepts/callback-detection]] — Pass B+ / M3
- [[concepts/tier-4-conversation-shape]] — Pass D rubric + conversation shape + Pattern Catalog origin
- [[concepts/vision-enrichment]] — Layer 6
- [[entities/grounding]] — the cross-cutting cascade
- [[concepts/open-questions]] — score normalization is an open question this page now scopes
- [[entities/bootstrap-twitch-clips]] — the eval dataset tool that opportunity (A) would wire in
