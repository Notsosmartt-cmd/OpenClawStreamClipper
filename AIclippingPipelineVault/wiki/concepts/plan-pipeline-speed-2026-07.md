---
title: "Pipeline Speed Plan — Stage 2/4/5.5/6 levers without the Windows-spawn hazard"
type: concept
tags: [plan, performance, speed, stage-2, stage-4, stage-6, vision-judge, concurrency, audio-events]
sources: 0
status: planned
updated: 2026-07-08
---

# Pipeline Speed Plan (2026-07)

Owner question: how do we make the pipeline faster — specifically Stage 2 and Stage 6 —
**without reintroducing the concurrency hazard** that caused the 58-min zombie
(multiprocessing spawn + shared_memory + librosa import on Windows)? Formulated from the
one fully-measured run (Activation-Wave Run 1, 2xRaKai 3.22 h → 82m37s with `--force` +
`--audio-workers 1`).

## Measured baseline (the only persisted full timing — see the metrics gap below)

| Stage | Time | Scales with | Notes |
|---|---|---|---|
| 2 Transcription (whisper) | ~500 s | VOD length | cached across runs → paid once per VOD |
| 2 Audio-events scan | **~970 s** | VOD length | **NOT cached — paid EVERY run**; serial (`--audio-workers 1`) |
| 3 Segments | 163 s | VOD length | |
| 4 Moment Detection | **1974 s** | VOD length | Pass B chunks run SEQUENTIALLY (LM Studio has PARALLEL=4 slots) |
| 5.5 Vision Judge | 660 s | clip count | ≤30 pairwise comparisons, 2 workers, ~22 s/comparison |
| 6 Vision Enrichment | 401 s | clip count | 10 moments, 2 thread workers, ~40 s/moment wall |
| 7 Render | 237 s | clip count | NVENC; fine |
| 1/4.5/5/8 | ~22 s | fixed | frames already 960×540 q2 |

Re-run of a cached-transcript VOD today ≈ **74 min** (everything except whisper repeats).

## Do the two quoted designs cost speed? YES, quantified
- **Stage 2 serial scan** (the deliberate no-zombie trade): ~16 min per 3.2 h VOD, every run.
- **Stage 6 workers=2**: caps vision at ~2× serial; LM Studio is provisioned for 4 slots.
- Neither is wrong today — the plan below removes the *cost* without the *hazard*.

## Why the fixes below carry NO Windows-spawn risk
The hang class was **processes**: spawn semantics re-import librosa in fresh children +
`multiprocessing.shared_memory` handshakes — uncatchable when they wedge. Everything here
is either **no concurrency at all** (cache, vectorize) or **threads in one process**
(no spawn, no re-import, no SHM; numpy/scipy FFT release the GIL). The only real
thread-side risk is BLAS oversubscription — already solved in `audio_events.py` (OMP/BLAS
pinning before numpy import); rule: `threads × BLAS_threads ≤ cores`.

## The path (ordered by value ÷ risk)

### P0 — Cache audio-events per VOD (zero risk, biggest paid-every-run win)
`stage2.py` already caches transcripts to `vods/.transcriptions/`; mirror that:
after a successful scan, copy `audio_events.json` to `vods/.transcriptions/
<basename>.audio_events.json` (or a sibling `.audio_events/` dir); on the next run,
copy it back instead of rescanning (keyed by basename + duration; `--force` still
rescans). **Saves ~16 min on every re-run of a 3 h VOD. No concurrency involved.**
Also fixes the asymmetry that the transcript is cached but the (deterministic) audio
features are not.

### P1 — Vectorize the scan itself (single-thread, 5–15× on first-scan cost)
The per-window cost is `librosa.effects.hpss` + onset detection recomputed
**per 10 s window, 1,162 times** — overlapping FFT work done from scratch each time.
Restructure to **block processing**: load a 10-min block, compute ONE STFT/HPSS for the
block, then derive the 3 dials (rhythmic/crowd/music) per window by slicing the
precomputed spectrogram/onset envelope. Estimated 970 s → **60–180 s** single-threaded.
First-scan cost only (P0 covers re-runs). Medium effort; verify per-window outputs match
the current scanner on a reference VOD (tolerance ~1e-3) before swapping in.

### P2 — Stage 4 concurrent Pass-B chunks (threads + HTTP, the Stage-6-safe pattern)
Stage 4 sends chunk prompts to LM Studio **sequentially**; the server runs PARALLEL=4
slots. Add a small ThreadPool (2–3 in flight) over chunk requests — identical failure
profile to Stage 6's existing pool (I/O threads, mutex on shared counters), which has
run clean for weeks. Chunks are independent; ordering restored on collection.
Flag `CLIP_PASSB_WORKERS` default 1 (=today), validated at 2–3.
Estimated 1974 s → **~1000–1300 s**. This is the single biggest absolute lever.

### P3 — Vision-side: measure the slot ceiling, then trim the judge
1. **Bench, don't guess:** `STAGE6_WORKERS`/`JUDGE_WORKERS` 2 vs 3 vs 4 on a fixed
   moment set. The "encoder may serialize" caveat is empirical — if the mtmd encoder is
   a server-side mutex, gains cap near 2×; if not, ~3.5×. One bench answers it.
2. **Judge diet** (5.5 costs more than 6): `frames_per_clip` 4→3 and
   `max_comparisons` 30→24 (Swiss on 10 items converges by ~5 rounds). Ranking-quality
   trade — validate on one run against the current ordering.
3. (Rejected: frame downscaling — frames are already 960×540 q2, little to gain.)

### P4 — Metrics so "average" becomes measurable (the gap this question exposed)
Timing lives only in `pipeline.log`, overwritten per run → only ONE run has full
numbers. Stage 8 should append one JSONL row per run to
`clips/.diagnostics/run_metrics.jsonl`: `{run, vod, vod_seconds, clips, total_seconds,
per_stage}`. ~10 lines, no flag. After a few runs the speed questions get real averages
and regressions become visible.

## Does threading reduce inference QUALITY? (owner Q 2026-07-08) — No, with one caveat

Threading the requests does **not** change what the model returns per request. Verified:
- **Each request is an independent forward pass.** LM Studio's continuous batching packs
  multiple sequences but computes each one's logits independently — co-batching does not
  alter a given prompt's output (only negligible batch-kernel FP noise).
- **Pass B already runs at `temperature=0.3`** (`stage4_moments.py:979`) — it is
  non-deterministic run-to-run *serially* today. Threading adds no new variance to any
  single prompt; the stochasticity is pre-existing and unchanged.
- **Assembly is order-independent by construction.** Chunk results are `.extend()`-ed then
  **re-sorted by timestamp** (`all_moments.sort` line 2602), and each moment carries its own
  `chunk_start/chunk_end` from `parse_llm_moments`. Arrival order is erased → concurrent
  collection yields the identical candidate set. (Required invariant: collect-then-sort, which
  holds; do NOT introduce order-dependent cross-chunk state.)
- **The judge is explicitly parallel==serial**: `vlm_judge.swiss_tournament` fixes each
  round's pairings from the round-start ranking (comment at the fn) — re-ranking only happens
  *between* rounds, sequentially. Parallel folds the same comparisons faster.

**The real risk is NOT threading — it's context/VRAM contention.** `context_length=32768`
(config/models.json) with `PARALLEL=4` means N concurrent slots share the KV cache. If prompts
were large enough that N full contexts don't fit, the server could truncate/evict context →
*that* would degrade output. Pass B prompts are small (a ~480 s transcript window, few-k
tokens), so 2–3 workers are safe; **before pushing to 4, confirm KV headroom** (a provisioning
check, not a correctness one). This is why P2/P3 default to today's value and validate upward.

**Distinct: the "judge diet" (P3.2, frames 4→3 / comparisons 30→24) IS a real quality trade** —
less visual context / tournament resolution. That is a *parameter* change, independent of
threading, and optional; evaluate it on its own against the current ordering.

## Projected effect (3.2 h VOD, 10 clips)

| Scenario | Today | After P0 | +P2 | +P3 | All |
|---|---|---|---|---|---|
| Re-run (cached transcript) | ~74 min | ~58 min | ~43 min | **~37 min** | 0.19× realtime |
| First-time VOD | ~83 min | ~83 min | ~68 min | ~62 min | +P1 → **~49 min** |

## Sequencing
P0 + P4 first (tiny, zero-risk, immediate). P2 next (one flag, big win, proven pattern).
P3 bench alongside any normal run. P1 last (real refactor; needs output-equivalence
verification). All flag-gated / default-current per house rules.

Related: [[concepts/plan-activation-wave-2026-07]] (Run-1 timing source) ·
[[concepts/bugs-and-fixes]] (the spawn-hang incident + BLAS pinning) ·
[[entities/audio-events]] · [[concepts/vram-budget]] (LM Studio PARALLEL=4 slots)
