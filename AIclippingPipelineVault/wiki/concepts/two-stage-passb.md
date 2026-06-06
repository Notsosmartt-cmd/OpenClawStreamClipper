---
title: "Two-stage Pass B — local + global (Tier-3 A1)"
type: concept
tags: [pass-b, callbacks, arcs, long-range, tier-3, a1, skeleton, stage-4, text]
sources: 1
updated: 2026-06-06
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
Compose stream skeleton — as of 2026-06-06 a type-grouped REGISTER built
  from per-chunk arc cards (== CLAIMS / PREDICTIONS / OPEN LOOPS / TOPICS ==);
  falls back to the legacy flat form "[MM:SS-MM:SS] (chunk i/N) <summary>"
  only when no cards were produced. See §"A1+ arc-aware extraction (shipped)".
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

## Evaluation: is the 15-word summary enough? (2026-06-06)

> [!warning] The 15-word summary under-provisions A1's stated specialty
> A1 consumes a 15-word "main topic" summary per ~8-min chunk (`stage4_moments.py:1599-1627`). That's a **~77:1 compression** (a 480 s chunk holds ~1,150 words). The question isn't "is 15 words a fair chunk summary" — it's "does it preserve the *arc-relevant* signal," a harder bar that it often fails.

**What 15 words handles fine**: theme-level / dominant-thread arcs — when the whole chunk *is about* the setup (an 8-min bet hype → later outcome). The "main topic" line captures both halves.

**Where it structurally fails** — exactly A1's stated kinds (irony / contradiction / exposure / prediction):
1. **Buried minor-at-the-time setups.** The canonical "penthouse" example the A1 prompt itself cites: the setup is often a 2-second offhand brag inside a chunk that's *mostly about something else*. A summarizer told to keep "the **main** claim/topic" systematically discards it — the exact selection bias that defeats arc detection. A setup is, by definition, unremarkable when said; "summarize the main thing" filters out the un-main detail that later pays off.
2. **Specific entities genericized.** "I'm well aware you're from Delaware" → "streamer does a rap battle." The matchable token (Delaware, the penthouse, the named bet) is gone, so A1 has nothing to link setup↔payoff on.
3. **Multi-topic chunks → one survivor.** A busy chunk has 3-4 threads; only one reaches the summary. If the setup is in thread #3, it's invisible to A1.

**The backstop and its limit**: [[concepts/callback-detection]] (M3) does NOT use the summaries — it embeds the *real* transcript windows + FAISS cosine + LLM judge, so it catches *lexically/semantically similar* callbacks A1 dropped ("penthouse"↔"penthouse"). But M3 **misses low-similarity conceptual/ironic arcs** (payoff worded differently than setup). So the **residual gap = conceptual/ironic arcs with a buried minor setup** — which is precisely the category A1 exists to add unique value on.

**Why it's a tradeoff, not a clean bug**: A1 is **boost-only** (a missed arc costs a clip-that-could-have-been, never a wrong clip — low blast radius); a *terser* skeleton is easier for the global pass to scan than a verbose one; and the 15-word cap keeps the per-chunk summary call cheap. So widening it naively (just "more words") risks **attention dilution** ("Lost in the Middle") at A1.

**Verdict**: 15 words is under-provisioned for A1's mission, and the fix is near-free on the dimension everyone fears (VRAM/context is not the constraint — see [[concepts/vram-budget]] §"Why bigger context ≠ better clips"). The high-leverage version is **arc-aware extraction** (preserve concrete claims/predictions/entities, not just the topic), not generic longer summaries. Full research-backed plan: **[[concepts/arc-aware-extraction]]**.

## A1+ arc-aware extraction (shipped 2026-06-06)

The 15-word summary is **gone**. Phases 1 + 2 of [[concepts/arc-aware-extraction]] are live in `scripts/lib/stages/stage4_moments.py`:

- **Per-chunk (was the 15-word summary call):** `_build_chunk_card()` makes the *same one call per chunk* but extracts a structured **chunk card** — `{topic, claims[≤3], predictions[≤2], entities[≤5], open_loops[≤2]}`. Every quoted string is **substring-verified** against the chunk text (`_arc_verify_quotes()`, whitespace-normalized + case-insensitive) so a small model can't smuggle in a hallucinated "setup". Cards live in a new `chunk_cards` dict; a flattened `_card_to_oneliner()` still populates `chunk_summaries`, so Tier-1 Q1's prior-context block is byte-for-byte unchanged. Card extraction failure is non-fatal — falls back to first ~12 transcript words.
- **Global A1 (was the flat skeleton):** `_build_arc_register()` reformats every card into a **type-grouped register** (`== CLAIMS ==` / `== PREDICTIONS ==` / `== OPEN LOOPS ==` / `== TOPICS ==`, one `ci MM:SS "quote"` per line). This counters "Lost in the Middle" (Liu 2023) — arc detection becomes near-neighbour scanning *within a register* and the section headers give the model a structural prior on what an arc looks like (claim↔claim contradiction, prediction↔outcome). The A1 prompt now says *"Match on MEANING, not shared words … a real arc has a BEAT."* If every card failed, A1 falls back to the original flat `[MM:SS-MM:SS] (chunk i/N) summary` skeleton.
- **Unchanged downstream:** the `{"arcs":[…]}` contract, chunk-order + in-range-timestamp validation, the 1.4× boost, and `cross_validated=True` are all the same. Cards are also dumped to `{TEMP_DIR}/chunk_cards.json` for observability.

Cost: same call *count*; ~2-4× output tokens on the per-chunk call (~80 vs ~15 words). Zero added VRAM. Phases 0 (baseline) and 3 (precision via `judge_tournament`) are still to be run.

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
