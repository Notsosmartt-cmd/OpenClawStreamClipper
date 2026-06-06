---
title: "Pass B false negatives — sources & mitigations"
type: concept
tags: [pass-b, false-negatives, recall, stage-4, llm, dead-chunk-gate, grounding, pass-a, reliability, text]
sources: 1
updated: 2026-06-06
---

# Pass B false negatives — sources & mitigations

A **false negative** here = a clip-worthy moment Pass B fails to surface, so it never becomes a clip. Pass B (Stage 4 LLM moment detection) is structurally more FN-prone than [[concepts/highlight-detection]] Pass A because it is a **generative LLM making judgment calls under compression**, not a deterministic keyword/energy scan — so it inherits failure modes a keyword match cannot have.

> [!note] The core architectural mitigation
> Pass B is deliberately **paired with Pass A** (deterministic, high-recall keyword/energy scan that can't time out or self-limit, runs over the whole VOD regardless). The two are merged and agreement earns the cross-validation ×1.20 boost. **Pass A is the recall net under Pass B's misses** — that's why no single Pass B failure is fatal. The items below are about narrowing the residual gap.

---

## FN sources (mechanism → mitigation → status)

| # | Source | Mechanism | Mitigation | Status |
|---|---|---|---|---|
| 1 | **Dead-chunk gate** | Cheap heuristics decide which chunks the LLM never sees; a quiet-but-juicy verbal moment (no keyword/audio/chat/speaker/density) is skipped unseen (rakai Delaware). | Default `off` (zero FN). `multi` (6 signals) / `sample` (+1-in-N pass-through). | ✅ mitigated (default off) — see [[concepts/pipeline-optimizations-2026-06]] §4 |
| 2 | **Chunk-call failure drops ALL its moments** | timeout / HTTP 400 / connection-refused / empty-content → `Chunk N: LLM call failed` → the whole ~5-min window is lost (6/4 run lost chunks 5-6). | `call_llm` 3× inline retries; BUG 31 outage short-circuit; **end-of-pass re-queue** (2026-06-06, below). | ✅ closed 2026-06-06 (re-queue) |
| 3 | **Compression / chunk boundaries** | LLM sees one ~5-min window; a moment split across a boundary, or whose setup was earlier, is fragmented/missed. | Chunk overlap; Tier-1 Q1 prior-context summaries; A1 global arc pass; M3 callbacks; arc-aware extraction + Fix 5 (arcs now survive selection). | ✅ mitigated |
| 4 | **Model self-limits ("tidy output")** | Even told to be inclusive, the LLM returns a tidy handful (~3/chunk in the 6/6 run); a dense chunk under-reports. | Prompt now explicitly says **list EVERY distinct moment, don't stop at 2-3** (2026-06-06, below). | ✅ closed 2026-06-06 (prompt) |
| 5 | **Pattern-catalog framing + model quant** | Prompt primes specific pattern signatures; a novel moment fitting none can slip; a small Q4 model misses subtleties. | "Include with a low score when in doubt" instruction; catalog breadth; Pass A parallel net; model choice (35b > 9b, slower). | partial (inherent) |
| 6 | **Detection → selection funnel (Pass C)** | A *detected* moment can still fail to become a clip if it loses its time-bucket or scores low (the arc bug). | Selection-axis tuning; [[concepts/clip-quality-remediation-2026-06]] Fix 5 (arc cross_validated bug + bounded arc guarantee). | ✅ mitigated |
| 7 | **Upstream transcription errors** | Garbled ASR → Pass B can't see the moment. | WhisperX quality; streamer-slang `initial_prompt`. | partial (upstream) |

> [!note] Grounding does NOT cause moment-drop FNs
> The Pass B grounding cascade only **nulls the `why` field** of a moment whose summary fails (`stage4_moments.py` ~`:1729`, `m["why"] = ""`); the moment itself keeps its score and proceeds to Pass C. So grounding is a quality filter, not a recall filter.

---

## Gaps closed 2026-06-06

**Gap #1 — end-of-pass re-queue of failed chunks** (`stage4_moments.py`). Chunks whose LLM call fails mid-loop are captured in `_failed_chunks` instead of being silently dropped, and retried **once after the main loop**, when LM Studio has usually drained its queue / recovered from a transient stall. Best-effort recovery: recovered moments get the core scoring (segment boost + `segment_type`) + a **light grounding pass** (denylist + content-overlap vs the chunk, so a hallucinated `why` still can't reach Stage 6) and are tagged `requeued=True`; the per-chunk M1 speaker annotation + arc card are skipped (enrichments — recovering the moment matters more). Skipped entirely if a persistent outage (`llm_net_outage()`) is still in effect (Pass A still covers). Logs `[PASS B] Re-queue recovered N moment(s)`. This closes the single most impactful FN source (a transient blip used to permanently drop a 5-min window). Unit-tested: transient recovery / outage-skip / hallucinated-why-nulled / segment-boost.

**Gap #2 — de-tidy the prompt** (`stage4_moments.py`). The Pass B prompt now explicitly instructs: *"List EVERY distinct qualifying moment — do NOT stop at a tidy 2-3. A busy chunk can legitimately have 5+; a quiet one may have 0. Under-reporting a real moment is worse than including a weak one."* Counters the LLM's tendency to return a uniform small count on dense chunks.

**Gap #3 — keep the dead-chunk gate `off`** (default already). The gate's FN risk is opt-in; the default avoids it. If speed forces a gate on long VODs, prefer `multi`/`sample` over the legacy `strict`.

---

## Verifying recall

- `logtool dead` — what the gate skipped (and why), per-signal breakdown.
- `logtool selection` — Pass C candidate trace; confirms a detected moment's `final_score`/bucket (catches source #6).
- The new `[PASS B] Re-queue recovered N` log line — how many moments the re-queue saved.
- A `requeued=True` field on recovered moments in `llm_moments.json` / `scored_moments.json`.

## Related
- [[concepts/highlight-detection]] — Pass A/B/C; Pass A is the recall net
- [[concepts/pipeline-optimizations-2026-06]] — the dead-chunk gate (source #1) + its corrected mode labels
- [[concepts/clip-quality-remediation-2026-06]] — Fix 5 (source #6, arc selection)
- [[concepts/two-stage-passb]] / [[concepts/arc-aware-extraction]] — A1 cross-chunk pass (source #3)
- [[concepts/case-rap-battle-missed]] — the worked Delaware example of a Pass B/gate FN
- [[concepts/bugs-and-fixes]] — BUG 31 (outage short-circuit), BUG 61 (arc cross_validated strip)
