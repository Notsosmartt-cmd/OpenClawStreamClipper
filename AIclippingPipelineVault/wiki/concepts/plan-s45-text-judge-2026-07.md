---
title: "Plan: S4.5 Batched Text Judge — extraction/judgment split at the phase boundary (2026-07)"
type: concept
tags: [plan, stage4, judgment, two-phase, quality, speed]
sources: 0
status: planned
updated: 2026-07-15
---

# Plan: S4.5 Batched Text Judge

Owner ask (2026-07-15): *"separate the S4 extraction and the S4 judgment to obtain the
highest model-size quality while keeping speed."* The design exploits the one architectural
freebie on this rig: the 9B→35B model swap **already happens** at the S4→S5.5 phase
boundary — judgment work batched to that boundary gets the 35B for free.

**Hardware law this obeys** ([[concepts/single-card-cuda-lane-2026-07]]): the 35B (~14 GB of
the 16 GB NVIDIA card) and the 9B CUDA lane can NEVER be co-resident (BUG 74); each extra
swap costs ~25 s. Therefore: no interleaving — **one batched judgment pass, at the boundary
that already exists.**

---

## The funnel — current vs shifted

**CURRENT (per ~3 h VOD):**
```
TEXT PHASE (9B on CUDA lane)
  S3 segment votes
  S4: chunk cards (~28) → Pass A/B/C + arc/anomaly lanes → candidates (~15-25)
      → grounding judges (9B) → Pass D rubric (9B — same-model echo)
      → scored_moments
── swap 9B → 35B (~25 s) ──
VISION PHASE (35B on dual-GPU Vulkan pool)
  S5  frame extraction for ALL candidates (ffmpeg, no LLM)
  S5.5 vision judge — pairwise FRAMES tournament (cap 20 pairs)
  S6  enrichment (+ D6 renders overlap) → S7 remainder
```
Structural hole: **the 35B never reads the transcript in a judging role** — all text-side
judgment is capped at 9B quality. Pass D is a correlated echo (same model re-scoring itself).

**SHIFTED:**
```
TEXT PHASE (9B) — extraction only
  S3 unchanged
  S4: chunk cards → Pass B in HIGH-RECALL posture (~2× candidates, lower threshold,
      evidence attached) → tier-1 grounding only
      → EVIDENCE PACKETS assembled per candidate (deterministic code, no LLM)
── the SAME swap (no new swaps) ──
VISION PHASE (35B)
  S4.5 TEXT JUDGE (NEW): batched comparative judgment over packets
       — groups of ~8: rank, kill non-clips, re-score, confirm subtype
       — ABSORBS Pass D (decorrelated second opinion at last) + tier-2 grounding
  S5  frame extraction for SURVIVORS ONLY (smaller)
  S5.5 vision judge — smaller tournament (survivors only)
  S6/S7 unchanged
```

## Evidence packets (the information-packaging core)

Per candidate, assembled **by code from artifacts the pipeline already has** (no LLM cost):
- verbatim transcript window ±30 s (NOT the 9B's summary — the judge re-derives; never
  trust the proposer), with speaker turns (diarization)
- audio-event markers in-window (laughter, crowd, booms)
- chunk-card context (open loops, entities), segment type
- the candidate's claim: boundaries, category, subtype, pattern id, why, 9B score
- (J7, later) the owner-approved shape-guide priors for its subtype — judge scores
  against species norms ([[concepts/reference-shape-guide-2026-07]])

Size cap: **≤ ~900 tokens/packet** (hard-capped in code). Group of 8 + instructions ≈
7–8 k prompt → fits the 35B's 32 k pool with workers=1 (BUG-73 arithmetic: 1 × (8k + ~1k
gen) ≪ 32k; batching-in-prompt, not concurrent requests).

## Budget (per 3 h VOD, at measured rates)

| Item | Cost / saving |
|---|---|
| Pass B recall bump (9B, more candidates emitted) | +1–2 min |
| Packet assembly (pure code) | ~0 |
| S4.5 judge: ~40–60 candidates → 5–8 batched 35B calls | +4–6 min (prefill-bound) |
| Pass D rubric calls REMOVED from the 9B phase | −2–3 min |
| Tier-2 grounding absorbed into the judge | −1–2 min |
| S5 frames + S5.5 tournament on survivors only | −2–4 min |
| **Net wall-clock** | **≈ −2 to +3 min (≈ neutral)** |

Quality delta: every candidate gets 35B text judgment before frames; the rubric becomes a
genuinely decorrelated second opinion; mis-scored/false-positive candidates die before
costing frames + tournament slots.

## Work items

- [ ] **J1. `evidence_packets.py`** (new lib module): deterministic packet builder from
  existing artifacts (words/master transcript slice, audio events, cards, moment entry).
  Hard token cap; self-test with synthetic moments.
- [ ] **J2. Pass-B recall posture**: `CLIP_PASSB_RECALL=high` — emission threshold lowered
  + prompt nudge ("emit borderline moments; a judge reviews them"), config-gated, default
  unchanged until flip.
- [ ] **J3. `s45_text_judge.py`**: batched comparative judgment — groups of ~8 packets,
  verdict JSON {keep|kill, score_0_10, rationale, subtype_confirm}; model id **phase-pinned
  to `vision_model_stage6`** (BUG-74 law — never an env fallback); retries per group;
  failure-soft: any group failure = keep that group unjudged (never lose candidates to an
  outage).
- [ ] **J4. Funnel rewiring** (`stage5.py`/`stage6.py` orchestration): run S4.5 as the FIRST
  vision-phase step; S5 extracts frames for survivors only; scored_moments updated with
  judge scores (blend rule: judge score replaces Pass-D slot in the existing scoring
  formula); Pass D + tier-2 grounding call sites short-circuited when the judge ran.
  **Cull floor**: the judge may kill at most ~50% of candidates and never below
  min(8, n) survivors — a bad judge day cannot empty a VOD.
- [ ] **J5. Flags**: `CLIP_S45_JUDGE=1|0` (single kill switch reverts the WHOLE feature —
  recall posture included); default **OFF** until J6 passes + owner flips.
- [ ] **J6. A/B validation** (agent-side, no owner eyes): same VOD, judge on vs off —
  diff selected clip sets, score distributions, stage timings (passb_equiv-style report
  into `.diagnostics`). Acceptance: wall-clock within +5 min; no crash; cull decisions
  carry rationales.
- [ ] **J7. Shape-prior injection** (after the owner approves Track E guide lines): packet
  header gains the subtype's approved norms. Blocked on Phase-2 markup.

## Risks & mitigations

- **Judge kills good clips** → comparative framing (rank within group, not absolute),
  cull floor (≥min(8,n) survive, ≤50% killed), rationales logged per kill, first judged
  run lands in the normal owner review flow.
- **False negatives remain** (9B can't propose what it can't see) → recall posture
  attacks the threshold class; deterministic lanes (Pass A acoustic, arc lane) remain
  model-free proposers; residual documented — no cascade escapes its proposer's blind
  spots. The 69-clip review bounds how big this class actually is.
- **BUG-73 pool overflow** → batch-in-prompt (one request at a time), 8k ≪ 32k pool.
- **BUG-74 ghost co-residence** → judge model id hard-pinned to the vision-phase model.
- **Packet bloat** → hard token cap in J1, enforced by truncation with a logged warning.
- **Swap thrash** → S4.5 lives strictly inside the vision phase; ordering guard asserts
  the text model is already unloaded.

## Sequencing & the learn boundary

Timing: **Phase-3 flagship candidate** of [[concepts/plan-fine-tuning-round-2026-07]] —
build after the owner's Phase-2 sit-down (the 69-clip review tells us whether S4's failure
mode is judgment (this plan's target) or recall (tune J2 harder) — and the approved shape
lines feed J7). Default-off + A/B first; the flip to default is an owner call on the A/B
numbers + their clip impressions. Per the learn boundary, the judge's prompt criteria are
static reviewed text — the Lab never edits them autonomously.

## Related
- [[concepts/plan-fine-tuning-round-2026-07]] — parent plan (Phase 3)
- [[concepts/single-card-cuda-lane-2026-07]] — the no-co-residence / swap laws
- [[concepts/bugs-and-fixes#BUG 73]] / [[concepts/bugs-and-fixes#BUG 74]] — the two laws J3/J4 obey
- [[concepts/reference-shape-guide-2026-07]] — J7's prior source
- [[concepts/quality-leverage-ranking-2026-07]] — why S4 judgment is worth a 35B
