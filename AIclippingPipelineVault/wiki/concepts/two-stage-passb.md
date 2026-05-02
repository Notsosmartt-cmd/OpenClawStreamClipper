---
title: "Two-stage Pass B — local + global (Tier-3 A1)"
type: concept
tags: [pass-b, callbacks, arcs, long-range, tier-3, a1, skeleton, stage-4, text]
sources: 1
updated: 2026-04-27
---

# Two-stage Pass B (Tier-3 A1)

Pass B-local (the existing chunk-by-chunk path described in [[concepts/highlight-detection]]) processes one ~5-8 minute chunk at a time. Tier-1 Q1's prior-context block extends its horizon by 2 chunks, but anything spanning more than ~15 minutes is still invisible. The architecturally correct solution is a SECOND Pass B call that operates on the entire stream skeleton at once — that's A1.

Introduced 2026-04-27 as Tier-3 A1 of the [[concepts/moment-discovery-upgrades]].

---

## Algorithm

```
Pass B-local finishes (chunk_summaries cache fully populated by Tier-1 Q1)
        ↓
Build chunk_index → (start_time, end_time) map by re-walking the
  timeline with Tier-1 Q4's per-segment chunk window logic
        ↓
Compose stream skeleton — N lines, one per chunk:
  "[MM:SS-MM:SS] (chunk i/N) <summary text>"
        ↓
ONE Gemma call with the full skeleton + a focused prompt asking for
  cross-chunk arcs (irony / contradiction / fulfillment / theme_return /
  exposure / prediction)
        ↓
Parse {"arcs": [...]} response. For each valid arc:
  - Validate setup_chunk < payoff_chunk
  - Validate timestamps fall inside their declared chunks (±60s slack)
  - Normalize 1-10 score to 0-1
  - Apply 1.4× boost (capped at 1.0)
  - Default clip window: 35s centered on payoff (boundary-snap will tighten)
  - Mark cross_validated=true (skeleton-level evidence is high-signal)
        ↓
Append arcs to llm_moments with category="arc"
```

The skeleton is small — even a 4-hour stream produces ~30-50 lines (~3-5 KB), well under any context limit. So this is a single cheap Gemma call (~30-60 s wall time on a thinking-leaky 35B).

---

## Why this works

Pass B-local has one job: identify clip-worthy moments inside its own 5-8 minute window. It can't be asked to also look across the stream — the prompts would conflict and the chunk transcript is already large.

The skeleton is a different artifact: ~12-word summaries of every chunk. The model gets a high-level view of the whole stream and is asked exactly one question: "what arcs span chunks?". Tier-1 Q1 already pays for the per-chunk summary calls — A1 reuses them at zero marginal cost.

---

## Output integration

Arcs are appended to `llm_moments` BEFORE [[concepts/callback-detection]] (M3) runs. They go through Pass C alongside local moments, callbacks, and Pass A signals. Each arc carries:

- `primary_category="arc"`
- `setup_time` / `setup_chunk` / `payoff_chunk`
- `arc_kind` (irony, contradiction, fulfillment, theme_return, exposure, prediction)
- `cross_validated=True`

The `setup_time` field also activates Tier-3 A2 in Stage 5/6 (extra setup frames + setup-aware vision prompt).

---

## Relationship to M3

M3 (callback detection) and A1 attack the same problem from different angles:

| | M3 callbacks | A1 arcs |
|---|---|---|
| Mechanism | embedding cosine + per-pair LLM judge | one global LLM call over the skeleton |
| Granularity | individual transcript windows | whole-chunk summaries |
| Strength | catches callbacks the chunk skeleton misses (subtle phrasings) | catches arcs spanning many chunks (long storytelling, slow-burn irony) |
| Cost | ~1-2 min per VOD (embedding + 20 judge calls) | ~30-60 s per VOD (one Gemma call) |

Per the [[concepts/moment-discovery-upgrades]] §12 open question 1, after both ship, evaluate whether M3 still adds value beyond A1.

---

## Failure modes

| Failure | Effect |
|---|---|
| `chunk_summaries` empty (Tier-1 Q1 never ran or all summaries failed) | A1 silently skips |
| LM Studio outage during the global call | A1 silently skips (logged) |
| Model returns malformed JSON | parse falls through, no arcs added |
| Model hallucinates an arc that doesn't pass validation (out-of-range timestamps, setup ≥ payoff) | arc dropped silently |
| Skeleton too short (<3 chunks) | A1 skipped (no horizon for cross-chunk arcs) |

All failure paths preserve the rest of the pipeline; A1 is purely additive.

---

## Cost summary

- Embeddings: 0 (A1 doesn't use embeddings; M3 does)
- Skeleton build: <1 s (CPU)
- One Gemma call: ~30-60 s on Qwen3.5-35B-A3B (thinking leaky); ~10 s on Gemma 4-26B
- VRAM: 0 (reuses already-loaded text model)

---

## Related

- [[concepts/highlight-detection]] — Pass B-local context
- [[concepts/callback-detection]] — sibling cross-chunk mechanism (M3)
- [[concepts/clipping-pipeline]] — pipeline placement (after Pass B, before M3)
- [[concepts/moment-discovery-upgrades]] — original spec (Tier 3 A1)
