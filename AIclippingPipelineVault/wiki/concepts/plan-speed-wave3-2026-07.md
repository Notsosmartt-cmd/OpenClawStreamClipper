---
title: "Speed Wave 3 — fresh 3h VOD to 30–45 min, same hardware (plan)"
type: concept
tags: [performance, speed, plan, stage-2, stage-4, stage-6, stage-7, cuda, vulkan, overlap, vision, pass-b, wave-3]
sources: 0
status: planned
updated: 2026-07-14
---

# Speed Wave 3 — fresh 3h VOD: ~90 min → 30–45 min (no hardware changes)

**Owner target (2026-07-14)**: process a fresh 3 h VOD in **30–45 min**. **Hard
constraint: no physical machine changes** — same RTX 5060 Ti 16 GB + RX 6700 XT 12 GB,
same LM Studio serving. Every quality-touching item ships default-off and promotes only
through an owner gate (RED rubric: default-off = not integrated).

Baseline (measured, [[concepts/pipeline-speed-findings-2026-07]] §10 — fresh 3 h talk
VOD, ~10 clips, healthy LM Studio ≈ **90 min**):

| stage | time | bound by | notes |
|---|---|---|---|
| S2 transcribe + audio scan | 12.5 m | GPU whisper, then CPU | sequential today (`stage2.py`) |
| S3 segment votes | 3 m | LLM (35B) | |
| S4 cards + moments + judges + rubric | **47 m** | LLM (35B) | cards ~12 m + moment loop ~30 m; ~190 tok/s raw Vulkan prefill is the villain |
| S5.5 + S6 vision judge + enrichment | 20 m | LLM (35B, image prefill) | ~6-8 frames/call at ~720p ≈ ~7 k image tokens |
| S7 edit/export | 6 m | CPU filters + NVENC | clip renders ALREADY ThreadPool-parallel |
| S1/S4.5/S5/S8 + misc | ~1.5 m | — | already trivial |

LLM stages = ~70 of 90 min → the plan is: **make the text phase cheap (9B lane), make
the vision phase lean (image diet), overlap everything that can overlap.**

---

## 1. Full proposal scorecard (everything evaluated 2026-07)

| # | proposal | verdict | why |
|---|---|---|---|
| W1/W2 | audio cache, threaded scan, C1 prefetch, C3/C4, moment-parallel=2, numba fix | ✅ SHIPPED | the current 90-min baseline already includes them |
| S7 parallel renders | — | ✅ ALREADY SHIPPED | `stage7.py` ThreadPool — verified 2026-07-14, do not re-propose |
| A1 | audio-analysis under whisper (S2 overlap) | **GO (Wave A)** | byte-safe, −3.5 m |
| A2 | S6↔S7 per-clip overlap | **GO (Wave A)** | NVENC-vs-LLM contention measured +0.1% (C1 A/B) |
| A3 | LM Studio reload between batch VODs | **GO (Wave A)** | kills the measured +70% decay tail (batch reliability) |
| B1 | 9B text lane, config-only (`text_model_passb`) | **GO (Wave B)** — THE big rock | measured 3.6×/call; swap machinery exists (`stage4.py:19`, `stage6.py:20`) |
| B2 | + CUDA runtime wrapper (`lms runtime select`) | **GO (Wave B)** | measured extra 1.8× (6.4× total); [[concepts/single-card-cuda-lane-2026-07]] |
| B3 | S3 on the 9B too | GO (Wave B, after B1 gate) | −2 m, removes one swap |
| C1 | vision image diet (frames ~360p, 6→4 for judge) | **GO (Wave C)** | ~4× fewer image tokens; S5.5+S6 20→~11 m |
| C2 | judge comparison cap 30→~20 | GO (Wave C, same gate) | −1-2 m |
| D1 | moment-parallel 2→4 **re-benched on the 9B lane** | Wave D (measure) | the 1.15×@2 finding was 35B-Vulkan (bandwidth-bound); CUDA has headroom — does NOT transfer, re-measure |
| D2 | batch [GROUND] judge calls (3-5/call) | Wave D | −2-4 m on 35B, less on 9B |
| D3 | cards output trim / card-only-alive-chunks | Wave D | stacks with the lane |
| D4 | spec-decode ON the 9B-CUDA (2B draft, `lms load --speculative-draft-simple`) | Wave D (optional bench) | single-card resolves the Vulkan catch-22; decode is only ~40% of the 9B call → modest |
| D5 | production prefix-reuse instrumentation (TTFT per Pass-B call) | Wave D (measure-first) | unknown hit-rate with interleaved judges/workers; fix ordering only if measured bad |
| Q | hybrid triage (9B triages chunks, 35B deep-passes top-K) | **FALLBACK only** | if B1's quality A/B FAILS; ~half of B1's savings, keeps 35B as finder |
| — | dead-chunk gate (multi/strict) | ❌ REJECTED | measured 0 skips on talk VODs ([[concepts/pipeline-optimizations-2026-06]] §4) |
| — | chunk 480→720 s | ❌ DEFERRED | −10 m but seam/quality semantics; dominated by B1/B2 |
| — | spec-decode on the 35B-Vulkan | ❌ CLOSED | measured 8× regression — don't re-litigate |
| — | transcript/prompt compression | ❌ REJECTED | breaks verbatim-quote grounding + cut_inference time-mapping (recall risk) |
| — | Pass-B early exit / budget cap | ❌ REJECTED | Pass C time-buckets need whole-VOD coverage (story-time doctrine) |
| — | GPU split tuning, vectorized scan, KV-quant-as-headroom | ❌ CLOSED/marginal | split not tunable; scan dominated by threaded default; MoE KV too small to matter |
| — | hardware upgrade | ❌ EXCLUDED | owner constraint 2026-07-14 |

## 2. The waves

### Wave A — structural overlaps (byte-safe, no owner gate, ship first)
- **A1 — S2 overlap** (`scripts/pipeline/stages/stage2.py`): start the CPU audio-events
  scan (+ any other CPU audio analysis) in a thread as soon as `audio.wav` exists, join
  before S3 consumes events. Whisper (GPU) and the scan (CPU threads) don't contend.
  S2 12.5 → ~9 m. Validation: byte-identical events JSON + transcript vs serial run.
- **A2 — S6↔S7 overlap** (`stage6.py`/`stage7.py`): render clip N while clip N+1
  enriches. Keep the vision model loaded until the last enrichment (move the
  `stage7.py:751` unload later); NVENC/CPU-filter contention already measured ~0.
  −4-6 m. Validation: identical clip outputs (per-clip commands unchanged), timing.
- **A3 — batch hygiene** (`run_pipeline.py` batch loop): `lms unload --all` + reload
  between VODs + a 5 s health probe; abort-with-message if LM Studio unresponsive.
  Protects the healthy 26–30 min/VOD-h rate (S4 decayed 14.4→24.9 min/VOD-h over the
  07-13 batch's final 6 h) and prevents the crash mode diagnosed 07-14.

### Wave B — the 9B text lane (the big rock; owner-gated)
Full detail: [[concepts/single-card-cuda-lane-2026-07]].
- **B1 (config-only)**: `models.json: "text_model_passb": "qwen/qwen3.5-9b"` → S4 runs
  on the 9B via the EXISTING Phase-5.1 swap. S4 47 → ~17–22 m.
  **GATE: owner clip-set A/B** — same VOD, unified-35B run vs lane run, review both
  clip sets (this is the finder; quality is the whole question).
- **B2 (+CUDA wrapper)**: `common.load_model(..., runtime=...)` wrapping the stage-4
  load in `lms runtime select cuda → load → select vulkan` with restore on EVERY error
  path; `CLIP_PASSB_RUNTIME=cuda9b` default-off; unload-first (never trust
  skip-if-loaded across runtimes). Pre-warm note: first-ever CUDA load of a model ≈
  6.5 m one-time (Blackwell JIT), 4.7 s warm after. S4 → ~13–18 m.
- **B3**: `text_model` → 9B as well (S3 on the lane, vision stays explicit 35B). −2 m.
- **Fallback Q** (only if B1's A/B fails): 9B triages all chunks, 35B deep-passes the
  top-K — keeps the 35B as finder at ~half the savings.

### Wave C — vision phase diet (owner eyeball gate)
- **C1 — image diet** (`stage5.py` extraction size, `vlm_judge.py`, `stage6_vision.py`):
  extract/downscale frames to ~360-480p for LLM calls (rendering still uses originals),
  judge rounds on 4 frames instead of 6. ~4× fewer image tokens → S5.5+S6 20 → ~11 m.
  GATE: owner eyeballs one run's titles/hooks + judge ranking spot-check vs a control.
- **C2 — judge budget**: max_comparisons 30 → ~20 (Swiss on ≤12 clips converges by
  round 4). −1–2 m, same gate.

### Wave D — polish / measure (only if 30–35 is wanted after A+B+C)
D1 re-bench moment-parallel at 2/4 on the 9B-CUDA lane; D2 batch grounding judges;
D3 cards trim; D4 optional 2B-draft spec-decode bench on the 9B (CUDA single-card —
the Vulkan no-go does not apply, but decode is only ~40% of the 9B call); D5 TTFT
instrumentation of a real run to measure true prefix-reuse before touching prompt order.

## 3. Composite arithmetic (fresh 3 h talk VOD, ~10 clips)

| rung | S2 | S3 | S4 | S5.5+S6 | S7 | total |
|---|---|---|---|---|---|---|
| today (healthy) | 12.5 | 3 | 47 | 20 | 6 | **~90 m** |
| + Wave A | 9 | 3 | 47 | 20 | ~2 (overlapped) | **~78 m** |
| + B1 (9B Vulkan) | 9 | 3 | ~19 | 20 | ~2 | **~50 m** |
| + B2 (CUDA) + B3 | 9 | 1 | ~15 | 20 | ~2 | **~45 m** ← target's upper edge |
| + Wave C | 9 | 1 | ~15 | ~11 | ~2 | **~36 m** ← inside target |
| + Wave D (if pursued) | 9 | 1 | ~11–13 | ~11 | ~2 | **~31–34 m** |

Floor honesty: S2's ~9 min of whisper + S7's render are real work with no remaining
big lever (S7 already parallel; whisper already the fast distil model on CUDA) — so
**~30 min is the practical floor** on this machine, and it requires most of Wave D.
The robust landing zone for A+B+C is **~36–45 min**, squarely in the owner's target.

## 4. Validation protocol
- Wave A: byte-identical artifacts (events/transcript/clip files) vs a control run +
  `run_metrics.jsonl` stage rows. No owner time needed.
- Wave B: ONE A/B pair on a known VOD (2xRaKai or Thetylilshow — cached transcripts
  make the text phase the only variable). Owner reviews both clip sets; `logtool
  selection` diff for rank churn. Promote via models.json default only on a pass.
- Wave C: control-vs-diet run, owner eyeballs titles/hooks; judge-ranking overlap
  metric (top-10 set intersection) as the objective check.
- Always: stage timings land in `run_metrics.jsonl` (cleanup()) — verify each rung's
  predicted saving actually materialized before starting the next.

## 5. Risk register
- **Runtime-select global window** (B2): restore-on-error in a `finally`; never flip
  outside the swap helper; pre-warm each model×CUDA pairing once.
- **Skip-if-loaded footgun**: `common.load_model` skips by id — across runtimes/ctx it
  must unload-first (or verify instance properties, not just the id).
- **Quality drift watch** (B/C): the owner label pool + Reference-Lab cards give an
  independent read — card a lane run and diff against the corpus like any other run.
- **LM Studio version drift**: backend dirs are versioned (vulkan 2.24.0 / cuda 2.23.1
  today); re-verify the select alias after app updates.
- **Non-determinism doctrine**: D1 raises LLM-call concurrency = clip-set variance —
  same acceptance rule as moment-parallel=2's promotion (owner reviewed output, not hashes).

## Related
[[concepts/single-card-cuda-lane-2026-07]] (Wave B canonical) ·
[[concepts/pipeline-speed-findings-2026-07]] (§10 baseline, §9b serving facts) ·
[[concepts/plan-serving-stack-2026-07]] (§11 runtime mechanics; Wave 2 shipped) ·
[[concepts/plan-pipeline-speed-2026-07]] (Wave 1 shipped) ·
[[concepts/pipeline-optimizations-2026-06]] (gate verdict) ·
[[concepts/pass-b-false-negatives]] (why B1's gate is sacred) · [[concepts/vram-budget]]
