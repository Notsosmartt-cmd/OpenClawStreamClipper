---
title: "Arc-Aware Chunk Extraction (plan)"
type: concept
tags: [pass-b, arcs, a1, callbacks, summarization, chain-of-density, plan, research, tier-3, stage-4, text]
sources: 1
updated: 2026-06-06
---

# Arc-Aware Chunk Extraction (plan)

A research-backed plan to fix the [[concepts/two-stage-passb]] §Evaluation weakness: the **15-word per-chunk summary** that feeds the Tier-3 A1 global arc pass systematically discards the *minor-at-the-time* setups that long-range arcs hinge on. Researched 2026-06-06.

> [!note] Phases 1 + 2 SHIPPED 2026-06-06
> The per-chunk **structured "chunk card"** extractor (Phase 1) and the **type-grouped register** for the A1 global pass (Phase 2) are now live in `scripts/lib/stages/stage4_moments.py`. The 15-word summary is gone; A1 now reads grouped CLAIMS / PREDICTIONS / OPEN LOOPS / TOPICS with substring-verified quotes. Cards are dumped to `chunk_cards.json` for inspection. Phases 0 (baseline instrument) and 3 (precision measurement via `judge_tournament`) remain to be run against real VODs. See §"Phased implementation plan" below and `[[concepts/two-stage-passb]]` §"A1+ arc-aware extraction (shipped)".

> [!success] Verified in production 2026-06-06 (rakai 193-min VOD)
> Confirmed running end-to-end in the `20260606_071210_20260424_2xRaKai` session log (`qwen3.6-35b-a3b`): `[A1+] Wrote 25 arc-aware chunk cards (72 claims, 25 predictions)`, `A1 sending grouped-register (25 cards, 8244 chars)`, and **A1 produced 5 arcs** with real beats — irony (setup T=64s → payoff T=11224s), prediction ("there ain't gonna be a school lockdown"), **fulfillment** ("I got something right here in my backpack" T=6304→6784s), **exposure** ("Where's Gavin at?" → "Who's Gavin" T=7264→7564s). These are exactly the buried minor-setup arcs the 15-word summary could not surface — so the **detection** half of the plan works as designed.
> **Phase-3 caveat (precision):** none of the 5 arcs survived into the final 10 clips — the arc category's 1.4× boost lost to keyword/llm moments under the per-time-bucket (1 clip/bucket) distribution. So arc-aware extraction is improving *recall of candidate arcs* but not yet *winning selection*. That is the open Phase-3 tuning question (boost weight vs bucket cap); honor the "quality > quantity" decision rule before raising the boost.
>
> **Phase-3 SHIPPED 2026-06-06** ([[concepts/clip-quality-remediation-2026-06]] Fix 5): the root cause was a **bug**, not just tuning — the dedup loop hard-reset `cross_validated=False` on standalone arcs (`stage4_moments.py:2240`), stripping their 1.20× boost despite the "first-class … cross_validated=True" intent. Fixed via `setdefault`, plus a bounded **Phase 2.5 arc guarantee** (the single best arc gets a slot if none won, one swap, spacing-safe, quality-floored at `CLIP_ARC_GUARANTEE_MIN_RATIO`=0.6; `CLIP_ARC_GUARANTEE=0` disables).
>
> **VALIDATED 2026-06-06** (run `20260606_201401`): A1 produced 5 arcs; `[ARC] Phase 2.5 guaranteed arc T=11224 (kind=irony, score=0.977) over weakest clip T=1828 (1.387)` — and crucially the **Stage 5.5 vision judge independently ranked that guaranteed arc #5 of 10** (`T=11224(#5)`). So the guarantee is *not* forcing in a weak clip — an orthogonal judge confirms the arc is a legitimate top-half clip. The Phase-3 decision rule ("keep only if guaranteed arcs win pairwise vs the clip they evict") is **satisfied** at the default 0.6 floor. (Earlier run `20260606_185751` also fired the guarantee but the arc didn't rank as high; one more cross-check on a different VOD wouldn't hurt before declaring it permanent.)

> [!note] One-line thesis
> Replace the free-text 15-word "main topic" summary with a fixed-size **structured "chunk card"** that preserves concrete claims / predictions / named entities / open loops (the *arc-bait*), and feed A1 a **type-grouped register** instead of a prose blob. Context/VRAM is not the constraint (see [[concepts/vram-budget]]); the only real cost is ~2-4× generation tokens on the one summary call already made per chunk.

---

## Established techniques this builds on

| Technique | Source | Why it fits |
|---|---|---|
| **Chain-of-Density (CoD)** — iteratively add "missing salient entities" while holding length fixed (~80 words), targeting ~0.15 entities/token. Salient entity = Relevant, Specific (≤5 words), Novel, **Faithful (in source)**, Anywhere. | Adams et al. 2023, [arXiv:2309.04269](https://arxiv.org/abs/2309.04269) | Directly attacks our failure: a normal summary is **entity-sparse + lead-biased** and drops exactly the minor entities (penthouse/Delaware/the bet). We want CoD's *output target* (fixed-size, entity-dense) in **one** call — not the 5-step iteration. |
| **Claim / committed-belief / event-factuality extraction** — classify what a speaker *asserts as true* (modality/veridicality). | FactBank (Saurí & Pustejovsky); Committed-Belief (Prabhakaran et al.); de Marneffe 2012 | The setup half of most arcs IS a claim/brag/prediction. A targeted "list boasts/claims/predictions with the exact quote" preserves the *assertion + verifiable anchor* a generic topic summary throws away. |
| **Recursive/map-reduce summarization** + the **"Lost in the Middle"** constraint that bounds it (U-shaped accuracy; relevant info mid-prompt is degraded). | Wu et al. 2021 [arXiv:2109.10862](https://arxiv.org/abs/2109.10862); Liu et al. 2023 [arXiv:2307.03172](https://arxiv.org/abs/2307.03172) | A1 is already the "reduce" step. The constraint: a *longer/verbose* 60-line skeleton makes A1 **worse** at finding a setup buried in entry #27. Mitigation: keep entries terse + **group/rerank** so candidate setups aren't stranded mid-prompt. |

> [!warning] Critical nuance (adversarially verified) — constrain the EMISSION, not the REASONING
> The structured-output benchmark ([arXiv:2501.10868](https://arxiv.org/html/2501.10868v1)) finds constrained decoding *helps* (+3.3-3.7%), but "Let Me Speak Freely?" (EMNLP 2024 Findings, [arXiv:2408.02442](https://arxiv.org/html/2408.02442v1)) finds **10-15% degradation when models must reason *inside* strict JSON**. Reconciliation: let the model deliberate in free text, then emit JSON **last**; apply a GBNF grammar (if available in LM Studio) only to the final object. The pipeline already strips prose/fences defensively (`callbacks.py:223-237`, `stage4_moments.py:1798-1819`).

---

## Recommended approach

### Per-chunk: structured "chunk card" (replaces the 15-word summary)

One call per chunk (same call count as today), capped at **~60-80 tokens** total (mirrors CoD's ~80-word target):

```json
{"topic": "<=12 words",
 "claims":      ["exact short quote of any brag/claim/assertion"],     // 0-3
 "predictions": ["exact short quote of any 'watch this' / 'it'll…'"],  // 0-2
 "entities":    ["penthouse", "Delaware", "the $500 bet"],             // 0-5 specifics
 "open_loops":  ["unresolved question or dangling stake"]}             // 0-2
```

This = CoD's entity-density target + committed-belief's quote anchoring, in one call. Across a 3 h VOD (~30-45 chunks) the skeleton grows ~3 KB → ~12-18 KB — trivial for 32K. **Per-line density is what helps A1, not raw length.**

### Global pass A1: type-grouped register (replaces the prose skeleton)

Don't concatenate 45 verbose lines (Lost-in-the-Middle). Feed **type-grouped sections** so arc detection becomes near-neighbour scanning *within a register*:

```
== CLAIMS (chunk:time — quote) ==
3  10:24  "this is my penthouse, I own the whole floor"
17 44:50  "I never said it was mine"
== PREDICTIONS ==   …
== OPEN LOOPS ==    …
```

Grouping gives the model a structural prior on what an arc looks like (claim↔claim contradiction, prediction↔outcome) and counters attention dilution. Keep the existing `{"arcs":[…]}` JSON contract that A1's parser at `stage4_moments.py:1829` already consumes.

### Keep M3
M3 catches lexical/semantic callbacks; the structured cards catch the conceptual/ironic ones (different wording). Complementary — the residual-gap arcs are the low-cosine ones A1-on-claims can now see because both halves are preserved as assertions.

---

## Prompt sketches (tuned for 9-35B Q4 local)

**Per-chunk extractor** (replaces `stage4_moments.py:1601-1609`):
```
/no_think
From this stream transcript chunk, extract anything that could PAY OFF later —
a brag, claim, prediction, named stake, or dangling question. Quote EXACTLY from
the text; if nothing of a type exists, use []. Prefer specific nouns over topics.

Transcript:
{chunk_text}

Output ONLY this JSON (no prose):
{"topic":"…","claims":[],"predictions":[],"entities":[],"open_loops":[]}
```

**Global arc pass** (replaces the A1 prompt at `:1742`):
```
/no_think
Below are CLAIMS, PREDICTIONS and OPEN LOOPS pulled from a stream, each tagged
(chunk:time). Find SETUP->PAYOFF arcs that span chunks: a claim later
contradicted/exposed, a prediction that lands or fails, a loop that closes.
Match on MEANING, not shared words. A real arc has a beat (irony, contradiction,
fulfillment, exposure) — not just a shared topic.

{grouped_register}

Output ONLY: {"arcs":[{"setup_chunk":int,"payoff_chunk":int,"setup_time":"MM:SS",
"payoff_time":"MM:SS","arc_kind":"irony|contradiction|fulfillment|theme_return|exposure|prediction",
"score":1-10,"why":"name BOTH halves, quoting each"}]}
Rules: setup earlier than payoff; quality > quantity; 0 arcs is valid.
```

---

## Risks + mitigations

| Risk | Real? | Mitigation |
|---|---|---|
| Attention dilution at A1 (longer skeleton → harder) | Yes (Liu 2023) | Type-grouped register + per-type top-N cap (drop to top claims by an existing salience/score signal on huge VODs); keep entries terse |
| Format restriction degrades reasoning | Yes (EMNLP 2024) | Reason in free text, emit JSON last; GBNF grammar only on the final object; existing fence/prose stripping stays |
| Hallucinated quotes from a small model | Yes | **Substring-verify** each `claims`/`entities` string against `chunk_text` (whitespace-normalized), drop non-matches. Makes `setup_text` trustworthy for the Stage-5 setup frame + A1's quote-both-halves. (Deterministic Quoting; "According to…" [arXiv:2305.13252](https://arxiv.org/pdf/2305.13252)) |
| More arc-bait → more false-positive arcs | Yes | Keep "quality > quantity, 0 is valid" + chunk-order + in-range timestamp checks (`:1837-1849`) + the bounded 1.4× boost; optionally gate new arcs through the M3-style judge or `arc_completeness` before they become first-class moments |
| Small-model JSON adherence | Moderate | Already handled by the defensive extractor; the card schema is shallow (4 string-array keys) which small models handle better than nested objects |

---

## Phased implementation plan

- **Phase 0 — instrument first.** *(Not yet run.)* Dump current per-chunk summaries + A1 arcs for 3-5 VODs via `logtool`. Baseline: arcs/VOD + hand-labelled recall on a few known buried-setup arcs (the penthouse/Delaware archetypes). This is the before/after denominator. As of the Phase 1+2 ship, the per-chunk cards are persisted to `{TEMP_DIR}/chunk_cards.json` (total_cards / total_claims / total_predictions + the full per-chunk cards) — the raw material this baseline needs.
- **Phase 1 — swap the extractor only. ✅ SHIPPED 2026-06-06.** Per-chunk call → structured card via `_build_chunk_card()`; `_arc_verify_quotes()` substring-verifies every claim/prediction/entity/open_loop against the chunk (whitespace-normalized, case-insensitive, ≥3 chars) and caps each list (3/2/5/2). Card stored in the new `chunk_cards` dict; a flattened `_card_to_oneliner()` still feeds `chunk_summaries` so the Tier-1 Q1 prior-context block is untouched. JSON parsed by `_arc_extract_json_obj()` (fence-strip + outermost `{...}`). Reuses the existing `max_tokens=4000` budget; `max_retries=0` (a missing card is non-fatal — falls back to first ~12 transcript words). **Low blast radius** — `chunk_summaries` stayed the single integration point.
- **Phase 2 — grouped-register A1. ✅ SHIPPED 2026-06-06.** `_build_arc_register(chunk_cards, chunk_time_map)` emits `== CLAIMS ==` / `== PREDICTIONS ==` / `== OPEN LOOPS ==` / `== TOPICS ==` sections (each line `ci MM:SS "quote"`). A1 prefers the register (`_skeleton_kind="grouped-register"`); if every card failed it falls back to the old flat `[MM:SS-MM:SS] (chunk i/N) summary` skeleton (`_skeleton_kind="flat-summary-fallback"`). A1 prompt rewritten to "Match on MEANING, not shared words … a real arc has a BEAT". The `{"arcs":[…]}` contract + downstream validation (chunk-order, in-range timestamps, 1.4× boost) are unchanged.
- **Phase 3 — measure precision with the existing harness.** *(Not yet run.)* Route new arc moments through `judge_tournament` (does an arc clip win pairwise vs a non-arc clip?) and watch `axis_report` `at_ceil` (arc moments piling at the score ceiling = over-boosting / false positives). `logtool axes` reads both.

**Decision rule**: keep Phase 2 only if buried-setup recall rises **without** the `judge_tournament` win-rate for arc clips falling — honor the pipeline's "quality > quantity" contract.

**Cost**: unchanged call *count* (one extraction/chunk); ~2-4× *output tokens* on that call (~80 vs ~15 words) — the cheap-VRAM / more-generation tradeoff. Zero added VRAM.

---

## Sources
- Chain-of-Density: [arXiv:2309.04269](https://arxiv.org/abs/2309.04269) · [ACL Anthology](https://aclanthology.org/2023.newsum-1.7/)
- Lost in the Middle: [arXiv:2307.03172](https://arxiv.org/abs/2307.03172)
- Recursively Summarizing Books: [arXiv:2109.10862](https://arxiv.org/abs/2109.10862)
- Narrative event chains: [Chambers & Jurafsky 2008](https://aclanthology.org/P08-1090/)
- FactBank / committed belief: [FactBank](https://www.researchgate.net/publication/220147734_FactBank_A_corpus_annotated_with_event_factuality) · [Committed Belief Tagging](https://www-cs.stanford.edu/~vinod/papers/exprom2015.pdf)
- Structured output: [Benchmark 2025](https://arxiv.org/html/2501.10868v1) · [Let Me Speak Freely? EMNLP 2024](https://arxiv.org/html/2408.02442v1)
- Long-context recall: [R&R 2024](https://arxiv.org/pdf/2403.05004)
- Hallucination-safe quotes: [Deterministic Quoting](https://mattyyeung.github.io/deterministic-quoting) · ["According to…" 2023](https://arxiv.org/pdf/2305.13252)

## Related
- [[concepts/two-stage-passb]] — the A1 pass this improves (§Evaluation has the problem statement)
- [[concepts/callback-detection]] — M3, the complementary embedding-based detector (kept)
- [[concepts/moment-discovery-upgrades]] — Tier-2/3 hub
- [[concepts/highlight-detection]] — Pass B-local that produces the per-chunk summary
- [[concepts/observability]] — `logtool axes` / `judge_tournament` for the Phase-3 eval
