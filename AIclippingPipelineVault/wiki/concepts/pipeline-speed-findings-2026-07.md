---
title: "Pipeline Speed — Empirical Findings (validation testing, 2026-07)"
type: concept
tags: [performance, speed, testing, validation, findings, reference, llm-determinism, gpu, whisper, stage-4, audio-events]
sources: 0
updated: 2026-07-09
---

# Pipeline Speed — Empirical Findings

Durable record of what the 2026-07 speed work actually MEASURED (as opposed to
[[concepts/plan-pipeline-speed-2026-07]] / [[concepts/plan-speed56-execution-2026-07]],
which are the plans). These are facts a future agent should not have to re-derive — several
are load-bearing constraints, one is a landmine.

## 1. Timing baseline (21 runs, from `run_metrics.py`)

Per-stage seconds across 21 real runs (medians; `stage_timings` recovered from diagnostics
by `scripts/research/run_metrics.py --backfill`):

| Stage | Median | Notes |
|---|---|---|
| 4 Moment Detection (LLM) | **1156 s** | the dominant cost; ~24 chunks × 2 LLM calls (moment + arc-card) |
| 2 Audio Transcription (stage total) | 781 s | *mostly the audio-events scan, NOT whisper* — see §4 |
| 7 Editing / render | 338 s | NVENC; fine |
| 5.5 Vision Judge | 261 s | pairwise tournament; runs SILENT ~11 min (logs batched to the end) |
| 6 Vision Enrichment | 204 s | 2 workers default |
| 3 Segment Detection | 165 s | |
| 1/4.5/5/8 | <10 s each | fixed |

**Realtime ratio (processing ÷ VOD length):** median **0.262**, mean 0.319, range 0.18–0.82
(the 0.82 is a 6.8 h VOD). So a normal run processes at ~0.26× realtime (~16 min per VOD-hour).
Timing is now persisted per run to `clips/.diagnostics/run_metrics.jsonl` (survives
`prune_traces`); query with `run_metrics.py report`.

## 2. Byte-identical speed wins (PROVEN safe, shipped)

- **#2 threaded audio-events scan** — serial vs 4-thread on a 300 s wav: **byte-identical
  windows**, 27.4 s → 8.4 s (**3.3×**). Now DEFAULT (`AUDIO_EVENTS_THREADS`=min(4,cores-2)).
  Safe because it's pure DSP (no LLM), in-process threads (no spawn/SHM → no Windows-spawn
  hang), and BLAS is pinned via `threadpoolctl` (`threads × BLAS ≤ cores`, no oversubscription).
- **#1 audio-events cache** — deterministic scan output cached per VOD → re-runs skip it
  entirely (mirrors the transcript cache).
- **#7 run-metrics** — the measurement instrument above.

These touch NO LLM inference, so they are genuinely reproducible. That is the dividing line
(§3).

## 2c. #3 vision-slot bench — INCONCLUSIVE (effect is noise-dominated → no change)

`scripts/research/bench_vision_slots.py` fires the same 4-image judge-shaped request at
concurrency 1–4 against qwen3.6-35B on the pooled GPUs. Two runs CONTRADICTED each other on
the sign: run 1 (reps=4) had conc=2 at **0.67×** conc=1 (slower); run 2 (reps=6) had conc=2
at **1.57×** (faster). At n=4–6 with a JIT-loaded 35B on a shared Vulkan pool the concurrency
effect is buried in variance. **Verdict: not actionable — the current `STAGE6_WORKERS` /
`JUDGE_WORKERS`=2 default stays** (don't change a config on noise; if the effect were real
and large it wouldn't flip sign). The "raise workers 2→4" lever from the speed plan is thus
retired as marginal. A rigorous answer would need many reps + controlled warmup, but the
noise itself shows the gain is small — not worth chasing vs the shipped wins (§2).

## 3. ⚠️ LANDMINE: LLM-call parallelization is NOT byte-reproducible (even at temp 0)

**The central negative result of the speed work.** Discovered validating the Stage-4
card-parallel cut-over (`CLIP_PASSB_CARD_WORKERS`).

- **Setup:** two temp-0 runs of the same VOD (2xRaKai), transcript+events reused, gate off.
  Baseline = arc-cards generated SEQUENTIALLY (inline). Card-parallel = same cards generated
  4-CONCURRENTLY. `CLIP_PASSB_DETERMINISTIC=1` forced greedy decoding on both.
- **Result:** prompt-hash manifests differ — baseline `8e0316624c64089a` vs card-parallel
  `78594f460395861e`, **23/25 chunks different**. Diagnostically clean: **chunk 1 (no
  prior-context) matches EXACTLY**; every diff is in the prior-context summaries only; the
  ONLY changed variable was card concurrency.
- **Root cause:** concurrent requests hit LM Studio's continuous batching, which **reorders
  floating-point reductions** vs a single unbatched request → different logits → different
  tokens, *even at temperature 0*. Inherent to batched inference on ANY server; not a bug.
- **Consequence (what actually matters):** different cards → different summaries → different
  prompts → **a different SELECTED-CLIP SET: 7/10 shared (±20 s), 3 swapped** (baseline dropped
  T=3269/9299/10529, added 2048/7749/10481).

**Implications for all future work:**
1. **No LLM-parallel optimization can be byte-validated.** The prompt-hash gate strategy is
   moot for anything that batches LLM calls (cards OR moments). Only OUTPUT-level / owner
   quality review can judge such a change.
2. It also means **temp-0 is not a reliable determinism lever** whenever concurrency is in
   play — do not build a validation strategy that assumes it.
3. Stage-4 LLM parallelism (the ~2× lever) is therefore a genuine **speed-vs-reproducibility
   tradeoff**, not a free win. Kept DEFAULT-OFF; enabling is an owner call, not a gate.

**⟶ 2026-07-09 REFRAME — the yardstick measurement (changes implication 3):**
Two SERIAL production-temp (0.3) runs of the SAME rakai VOD (frozen runs
`20260705_010127` vs `20260705_074956`, same selection config) overlap only
**5/10 clips (±20 s)** — the pipeline's NORMAL run-to-run sampling variance. The
card-parallel concurrency effect measured **7/10 at temp 0** — i.e. **concurrency-induced
variance is SMALLER than the sampling variance every production run already has.** At
production temp the sampling noise dominates and card-parallel changes outputs by *less
than a plain re-run does*. So the "changes the clip set" caveat is not a NEW quality risk —
it's within (in fact under) existing noise. Caveats: n=1 per comparison; the two serial
runs' render flags differed (selection-irrelevant). Residual risk is only "different draw,
same distribution" — exactly what a re-run produces today. This makes enabling
`CLIP_PASSB_CARD_WORKERS` defensible with a one-batch owner spot-check, and the same
A/B-variance method (serial-vs-serial yardstick, then concurrent-vs-serial) is the correct
gate for any future moment-parallel work.

## 4. Whisper is already batched — transcription is ~200 s, not 781 s

The 781 s "Stage 2" bucket is dominated by the audio-events scan, not Whisper. Measured on a
3.2 h VOD: WhisperX **ASR 107 s + word-align 75 s ≈ ~200 s incl. load** (~45–55× realtime),
running `batch=16 float16 large-v3-turbo` on CUDA — i.e. **batched inference is already on**.
So "enable batched Whisper" = ~0 gain; a distil swap = ~0–10% (turbo already runs at
distil-class speed, doesn't touch the separate wav2vec align step, and loses accuracy on
slang). Verdict: not worth it; the scan (§2) was the real Stage-2 cost and is already handled.

## 4b. #6 vectorized scan — BUILT + VALIDATED, but dominated by the threaded scan

`_scan_vectorized` (`AUDIO_EVENTS_VECTOR`, default off): one block-HPSS per ~600 s block
sliced per window for music_dominance (the ~700 ms/window dominant + only context-dependent
detector); crowd + rhythmic recomputed EXACT per-window (byte-identical → can't flip);
near-threshold music windows recomputed exactly (hybrid `band`); straddle windows fall back.

- **Zero-flip validated** on 2 real VODs (2xRaKai + Tylil, 30-min segments, `vector_equiv.py`):
  crowd/rhythmic max-delta 0.000; music max-delta **0.015 (rakai) / 0.146 (Tylil)**.
- The 0.146 delta forced a design fix: the hybrid **band was raised 0.05 → 0.15** (must be ≥
  the max block-HPSS error, else zero-flip is luck not a guarantee). The large-delta windows
  that remain are far from the 0.6 gate → harmless.
- **Benchmark = the verdict:** vectorized single-thread **~1.9× over serial (2.1 vs 1.1 win/s)
  — SLOWER than the shipped DEFAULT threaded scan (§2, ~3.3×).** Vectorizing only de-dups the
  overlapping HPSS work (~3× on HPSS) but leaves crowd/rhythmic per-window; threading beats it.
- **Verdict: correct + validated, but default-off — dominated by #2.** Would only win combined
  with threading (block-parallel), not worth building since the threaded scan is already ~5 min
  and #1 (cache) skips it entirely on re-runs. Kept as a proven option, not enabled.

## 5. Dual-GPU reality (clarifies a common confusion)

LM Studio (llama.cpp/Vulkan) **pools both cards into one ~28 GB space and tensor-splits ONE
model** across them: qwen 3.6-35B-A3B (~22 GB) = ~14.5 GB on the RTX 5060 Ti (16 GB) + ~11 GB
on the RX 6700 XT (12 GB). It is **one engine spread over two cards**, not two independent
engines — the 35B cannot fit on either card alone, so both collaborate on every inference.
Therefore the §3 non-determinism is a property of **request batching**, NOT of having two
cards (a single GPU would batch identically). Byte-reproducible LLM parallelism would require
two SEPARATE single-request engines — which needs a model small enough to fit one card (e.g.
9B), a model downgrade with its own quality cost. See [[concepts/vram-budget]].

## 6. Validation-method learnings

- **The prompt-hash gate only covers prompt assembly + which chunks reach the LLM** — NOT the
  moment parse/scoring/grounding cascade (which runs after the call). A refactor bug in
  grounding would pass a hash gate silently. So grounding must not be refactored blind; any
  moment-parallel cut-over needs output-level validation.
- **`passb_equiv.py` proves the two-phase driver ≡ serial in pure LOGIC** (mock-injected
  deterministic call_llm; 6 sizes × 4 worker counts). That proof stands — the real server just
  isn't deterministic under concurrency (§3), so logic-equivalence ≠ byte-equivalence live.
- **Reconciliation finding (caught by the mock harness, pre-live):** Stage-4 serial creates a
  chunk's summary ONLY after its moment call succeeds (the card build is inside the
  `if response:` block), so a transient moment-call failure shortens later chunks'
  prior-context. Any two-phase path must reproduce this (the driver does, via a reconciliation
  pass) or it diverges under transient failures.
- **Watchdog / ops:** Stage 5.5 (Vision Judge) legitimately runs SILENT for ~11 min (it batches
  its logging), and `--force` temp-0 runs exceed 60 min — both triggered false stall/timeout
  alarms. Set the phase_runner watchdog `--timeout` ≥ 5400 s and `--stall` ≥ 900 s for full runs.

## 7. The LLM floor (why runs are ~50 min and will stay there on this stack)

Observed live during Stage 4: **GPU util ~16%** while the 35B is fully loaded (13.8 GB
NVIDIA + 11.6 GB AMD) — the cross-vendor Vulkan tensor-split is **bandwidth/coordination-
bound, not compute-bound**; the hardware mostly *waits*. A run makes ~**48 sequential LLM
calls in Stage 4 alone** (24 chunks × moment call + card call) plus ≤30 judge comparisons
(~8 images each) + 10 vision calls. Dense IRL/gaming chunks at temp-0 paced **~200 s/chunk**
(vs ~80 s typical). With LLM-call parallelism ruled out for byte-safety (§3), marginal in
practice (card-parallel = ~4% — cards are cheap vs moment calls), and vision concurrency
inconclusive (§2c), **software levers on the LLM portion are exhausted. The remaining big
speed lever is a model-tier or serving-hardware change** (smaller/faster model, or a
single-GPU CUDA setup that fits the model) — a quality/cost tradeoff, not a code fix.

## 8. Validation-coverage caveat + final #5/#6 disposition

- **Single-VOD caveat:** every FULL-pipeline validation run this session used 2xRaKai
  (deliberate — controlled A/B needs the VOD held constant). #6's scan validation used 2
  VODs (scan only). Broad generalization of the speed/quality findings rests on 1–2 VODs;
  spread future validation runs across VODs.
- **Final disposition:** #5 card-parallel built + default-off (**~4%, marginal — cards are
  cheap; the real Stage-4 lever is moment-parallel, which stays HELD** on the §3
  reproducibility tradeoff, enable-by-owner-review only). #6 built + zero-flip-validated +
  default-off (**dominated by #2**). Production speed gains come from **#1 + #2 only**.

## Bottom line

The quality-safe speed gains are the non-LLM ones (**#1 cache, #2 threaded scan, #7 metrics** —
shipped, byte-identical). Every LLM-parallel lever (Stage-4 cards/moments, #6 is a red herring
since the scan is already fast) trades reproducibility for speed and changes the clip set within
temp-variance — a real tradeoff for the owner, surfaced with data, not a silent regression.

Related: [[concepts/plan-pipeline-speed-2026-07]] · [[concepts/plan-speed56-execution-2026-07]] ·
[[concepts/vram-budget]] · [[concepts/multimodal-fusion-2026-07]] (the omni/LM-Studio serving
constraints) · [[concepts/bugs-and-fixes]]
