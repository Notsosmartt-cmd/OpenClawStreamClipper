---
title: "Plan: S4.5 Batched Text Judge — extraction/judgment split at the phase boundary (2026-07)"
type: concept
tags: [plan, stage4, judgment, two-phase, quality, speed]
sources: 0
status: shipped
updated: 2026-07-16
---

> [!success] SHIPPED — DEFAULT ON since 2026-07-16 (owner flip on the bench evidence).
> `CLIP_S45_JUDGE=0` is the kill switch (reverts to the legacy path incl. Pass D).
> Net economics: ~34 s of 35B judging per VOD; the swap is recovered from stage 6; each
> culled junk candidate saves ~60-70 s of enrichment+render — a culling run is net FASTER.
> Owner ritual: skim the per-run kill list in `clips/.diagnostics/s45_judge_*` (timestamps
> + rationales — kills are auditable, unlike S4 misses). Recall was removed; J7
> (shape-prior packets) remains blocked on the Phase-2 markup.

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

- [x] **J1. `evidence_packets.py` — DONE 2026-07-15**: deterministic packets (verbatim
  speaker-turn window + audio marks + claim), tolerant transcript loader (segments or flat
  words), hard cap 3,600 chars w/ middle-truncation. Selftest PASS (turns, cap, both
  loader shapes). v1 omits chunk-card open-loops (deferred — needs a card artifact path).
- [x] **J2. Recall posture — BUILT, REJECTED by its own A/B, then REMOVED (owner
  2026-07-16)**: judge-implied recall cost **4× on S4** (3,703 s vs 930 s, Runiktvlive
  5.31 h) and produced the **SAME 16 candidates** (boundaries jittered by seconds) —
  borderline emissions all paid generation + grounding, then died in selection (S4 is
  output-token-bound; "0-5 + borderline" was a pure output tax). Owner ordered full
  removal: `CLIP_PASSB_RECALL` knob deleted, prompt restored to 0-3, bench `--recall`
  flag dropped. **Lesson kept in-code**: if recall ever returns it must attack the
  proposer's BLIND SPOTS (new detection lanes), not the emission threshold.
- [x] **J3. `s45_text_judge.py` — DONE**: groups of 8 batch-in-prompt, verdicts
  {keep, score 0-10, subtype-confirm, rationale}; model = explicit ARG (BUG-74);
  failure-soft groups; cull floor (≤50%, ≥min(8,n), floor-rescues marked). Selftest PASS
  (annotation, subtype override, kill-all→floor, outage→all survive, garbage→unjudged).
- [x] **J4. Funnel rewiring — DONE**: `stage5._s45_text_judge` runs BEFORE frame
  extraction (does the 9B→35B swap itself; `ctx.s45_swapped` makes stage 6 skip its
  duplicate swap); survivors → hype_moments; kept moments carry score_passb + judge score
  (score = judge/10) + s45_judge block; decision report → `clips/.diagnostics/s45_judge_*`.
  **Pass D absorbed** (skipped in stage4 when the flag is on). *v1 scope note: tier-2
  grounding still runs in S4 (absorb deferred to v1.1 — smaller saving, larger blast
  radius).*
- [x] **J5. Flag — DONE**: `CLIP_S45_JUDGE=1` enables judge + recall + Pass-D skip;
  `=0`/unset = byte-identical legacy path. Default **OFF**.
- [~] **J6. Validation — REDESIGNED per owner directive (2026-07-15: "do not process any
  full VODs for testing — test up to the sections needed, measure timing respectively")**:
  NEW `scripts/research/bench_s45.py` drives the PRODUCTION stage code sectionally —
  stages 1-4 (+judge) then STOPS: no frames, no S5.5, no renders, no processed.log, no
  clips. Per-section timing report (`bench_s45_<stamp>.json`); saves a pre-judge moments
  snapshot so later benches run **judge-only with ZERO VOD processing** (transcript+events
  materialized from the per-VOD caches). The stage helper itself now logs+persists a
  packets-vs-judge timing split (the new section-metric device).
  **First sectional run DONE (2026-07-16, Runiktvlive 5.31 h fresh)**:
  s1 7.9 s · **s2 fresh 395 s = 1.24 min/VOD-h** (≈half the Wave-0 fresh rate — batched
  CLAP + GPU compounding) · s3 128 s · **s4 recall-on 3,703 s = 11.6 min/VOD-h** ·
  **judge 102.7 s total** (swap ~68 s + judge 34.3 s for 16 candidates in 2 groups;
  packets 0.0 s). Verdicts: 12 kept (median 8.0), **4 culled with crisp rationales**
  ("premature cut — hype setup lacks payoff", "pure filler; mundane tech
  troubleshooting", …) — exactly the mis-score class the judge was built to kill; cull
  25% ≪ the 50% floor. Recall mode did NOT inflate the final candidate count (16 on
  5.31 h ≈ Raud's per-hour rate).
  **RESOLVED by the same-VOD recall-OFF baseline: s4 = 930 s = 2.9 min/VOD-h** (healthy —
  the new VOD isn't slow; the 11.6 was ENTIRELY the recall posture → J2 rejected).
  **J6 ACCEPTANCE: PASSED for the judge** — wall-clock +~100 s/VOD gross (swap ~68 s is
  largely recovered by stage 6 skipping its duplicate swap + 4 culled candidates' frames
  saved → net ≈ +35 s), zero crashes, every kill carries an evidence-citing rationale.
  **Ship posture: `CLIP_S45_JUDGE=1` = judge WITHOUT recall.** Owner flip pending
  (their call, with these numbers + the Phase-2 clip impressions).
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

## Finder A/B via judge arbitration (2026-07-16, owner query: gemma-4-12b vs qwen3.5-9b)

Method (all sectional, zero full runs): detect-section bench per finder on the same VOD,
then BOTH candidate snapshots through the same 35B judge in judge-only mode (~3 min each).

| | qwen3.5-9b | gemma-4-12b-qat |
|---|---|---|
| S3 / S4 sections | 128 s / **930 s** | 154 s (+20%) / **1,215 s (+31%)** |
| Candidates → judge-kept | 16 → 11 | 16 → 13 |
| Judge scores (med / mean) | **7.0** / 5.9 | 6.5 / 5.8 |
| Exclusive picks' judge scores | **mean 6.2 — headliners 9, 9, 8, 8** | mean 5.1 — best 8 |

**Verdict: qwen keeps the finder seat** — Gemma is ~30% slower and its unique finds are
weaker (qwen's exclusives carry the headliners). Prompt-fit + measured JSON discipline stay
with the incumbent.

**The bigger discovery: the two finders only overlapped on 6/16 moments (±90 s).** Each
surfaced 10 exclusives on the same 5.31 h VOD — and **7 of Gemma's 10 exclusives survived
the judge** (scores up to 8). That is the first MEASURED bound on the finder's
false-negative class: qwen's finder misses ≥7 judge-approvable moments per ~5 h VOD that a
different-family finder sees. It also empirically confirms the recall lesson: extra
candidates must come from a DIFFERENT lens (new lanes / other families), not from lowering
the incumbent's threshold (which yielded nothing at 4× cost).

> [!todo] Candidate follow-up (owner's call, filed — NOT built): **dual-finder union mode**
> — run both finders, union the candidate sets (~26/VOD), let the judge arbitrate all of
> it. Cost ≈ +20 min/5 h VOD (gemma S4) for ~+7 judged-keepable moments incl. 7-8-scored
> ones. Natural as an opt-in "deep scan" flag, not a default.

## Related
- [[concepts/plan-fine-tuning-round-2026-07]] — parent plan (Phase 3)
- [[concepts/single-card-cuda-lane-2026-07]] — the no-co-residence / swap laws
- [[concepts/bugs-and-fixes#BUG 73]] / [[concepts/bugs-and-fixes#BUG 74]] — the two laws J3/J4 obey
- [[concepts/reference-shape-guide-2026-07]] — J7's prior source
- [[concepts/quality-leverage-ranking-2026-07]] — why S4 judgment is worth a 35B
