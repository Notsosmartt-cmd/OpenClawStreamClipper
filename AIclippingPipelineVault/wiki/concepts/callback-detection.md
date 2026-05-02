---
title: "Callback detection (Tier-2 M3)"
type: concept
tags: [callbacks, embeddings, semantic-search, long-range, tier-2, m3, stage-4, pass-b, text]
sources: 1
updated: 2026-04-27
---

# Callback detection

Pass B operates one chunk at a time (per [[concepts/highlight-detection]]). Anything set up minutes earlier and paid off in a later chunk is invisible — Pass B literally cannot see the connection. This is the structural reason the canonical Lacy penthouse moment was missed: setup at ~14 min, payoff at ~25 min, two different chunks, no shared context.

Callback detection ([[entities/callback-module]]) closes that gap with semantic search + a focused LLM judgment.

---

## Why semantic search + LLM judgment, not just one

Either signal alone is insufficient:

- **Cosine similarity alone**: surfaces semantically related pairs, but lots of related pairs aren't actually callbacks. Two windows about "money" can be 0.7 cosine without one referencing the other.
- **LLM alone**: would need to read the entire transcript at once to spot all setup-payoff pairs — too long even for 32K context, and the `Pass B-global` approach (Tier 3 A1) is the architectural fix for that.

So M3 uses cosine to *propose* candidate pairs and the LLM to *judge* them. Cosine handles the "find me distant transcript windows that share topic"; the judge handles "but is it actually a callback?".

---

## Algorithm

```
After Pass B finishes (callback_moments = [])
        ↓
Aggregate raw Whisper segments into ~30 s overlapping windows
        ↓
Embed every window — sentence-transformers/all-MiniLM-L6-v2, L2-normalized
        ↓
Build FAISS IndexFlatIP (numpy fallback if FAISS missing)
        ↓
Take top-20 Pass B moments by score
        ↓
For each candidate:
   - Embed payoff (±15 s window)
   - FAISS-search for setups ≥ 5 min earlier with cos ≥ 0.6
   - If a setup found:
       - Send (setup, payoff) to a small Pass-B' LLM prompt
       - Parse JSON verdict {is_callback, kind, clip_start, clip_end, why}
       - If is_callback: add as callback moment, score ×1.5, cross_validated=True
   - Stop after 5 callbacks
```

---

## Output integration

Callback moments are appended to `llm_moments` before Pass C runs. They:

- Have `primary_category="callback"` (a new category not in `parse_llm_moments`'s VALID_CATEGORIES because they bypass that function)
- Carry `setup_time` / `setup_text` / `callback_kind` / `callback_cosine` for downstream consumers
- Inherit the deduplication logic in Pass C — if a callback's payoff coincides with an existing LLM moment (within 25 s), they merge with the callback's `why` winning
- Get the standard cross-validation 1.20× boost on top of M3's own 1.5×

Pass C's category-cap rule (no category > 60 % of candidates) applies — but with `max_callbacks=5` and typical target counts of 9-12 clips, this is a non-issue.

---

## Failure modes

| Failure | Effect |
|---|---|
| `sentence-transformers` not installed | M3 silently no-ops, pipeline runs as if M3 weren't enabled |
| `faiss-cpu` not installed | numpy brute-force fallback (slower but correct) |
| Cosine threshold too low | judge LLM rejects most pairs → low yield (acceptable) |
| Cosine threshold too high | misses real callbacks with looser semantic ties (acceptable) |
| Judge LLM hallucinates "is_callback: true" with weak rationale | callback added with weak `why` — caught by Tier 1 grounding cascade in [[entities/grounding]] which still runs |

---

## Validation

Per the [[concepts/moment-discovery-upgrades]] §7:

- The Lacy penthouse VOD should produce a callback moment naming both the penthouse-bragging setup AND the off-screen exposure payoff in `why`.
- Hand-label 3 known callback moments on 3 different VODs; the detector should surface ≥ 2 of 3.

---

## Cost

~1-2 min added wall time per VOD (embedding + judge calls). Detailed breakdown in [[entities/callback-module]] §Cost.

---

## Relationship to Tier-3 A1

Tier-3 A1 (two-stage Pass B: local + global) would *replace* M3 with a unified mechanism — first build a stream skeleton, then have the LLM identify multi-chunk arcs from the skeleton. M3 is a cheaper proxy via embedding search; A1 is the architecturally correct solution. Per the plan §12 open question 1, after A1 ships, evaluate whether M3 still adds value beyond A1.

---

## Related

- [[entities/callback-module]] — implementation
- [[concepts/highlight-detection]] — Pass B context
- [[concepts/clipping-pipeline]] — pipeline placement (after Pass B, before Pass C)
- [[concepts/moment-discovery-upgrades]] — original spec
