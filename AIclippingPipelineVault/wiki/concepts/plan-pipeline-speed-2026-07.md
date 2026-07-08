---
title: "Pipeline Speed Plan — 7 quality-neutral optimizations (detailed implementation)"
type: concept
tags: [plan, performance, speed, stage-2, stage-4, stage-6, stage-7, vision-judge, concurrency, audio-events, metrics]
sources: 0
status: planned
updated: 2026-07-08
---

# Pipeline Speed Plan (2026-07) — detailed implementation per proposal

Owner directive: make the pipeline faster for BOTH fresh and pre-processed VODs
**without sacrificing quality** and without the Windows-spawn hazard (the 58-min zombie:
multiprocessing spawn + shared_memory + child librosa import). This page is the detailed
implementation plan for the 7 accepted proposals. Quality-touching ideas (judge diet,
naive Pass-B threading, whisper batching, frame downscaling) are explicitly EXCLUDED —
see §Excluded at the bottom.

> [!note] Ground truth: measured distribution across 21 runs (stage_timings in diagnostics)
> Medians: Stage 4 Moment Detection **1156 s** · Stage 2 whisper **781 s** (fresh only;
> cached after) · Stage 2 audio-events scan **~500–970 s (EVERY run — uncached)** ·
> Stage 7 render **338 s** · Vision Judge **261 s** · Stage 6 vision **204 s** ·
> Stage 3 **165 s**. Realtime ratio n=18: median **0.262**, range 0.18–0.82.
> Two corrections vs the first draft of this page: (1) timing IS persisted (the "one
> measured run" claim was wrong — 21 runs have it); (2) **Pass-B chunks are NOT
> independent** — chunk N's prompt embeds a `prior_context_block` from chunks N-1/N-2's
> card summaries (`stage4_moments.py:1818-1834`); naive chunk threading would degrade the
> setup→payoff callback class. Proposal #5 is the quality-preserving restructure.

## Why none of this reintroduces the spawn hazard
Every lever is (a) caching, (b) **threads in one process** (no spawn, no SHM, no child
re-import; numpy/scipy FFT release the GIL), (c) independent **ffmpeg subprocesses**
(full processes, not Python multiprocessing — no shared interpreter state to wedge), or
(d) pure vectorization. The BLAS-oversubscription rule (`threads × BLAS_threads ≤ cores`)
is already handled by `audio_events.py`'s OMP/MKL pinning.

---

## #1 — Cache audio-events per VOD  · re-runs · zero risk · S effort

**Problem:** the scan's output is deterministic per VOD, yet only the transcript is
cached — stage2 rescans on EVERY run (~970 s serial on a 3.2 h VOD) and writes only to
the cleaned work dir.

**Implementation** (`scripts/pipeline/stages/stage2.py`, the events block at ~line 109):
1. `cached_events = p.transcriptions_dir / f"{stem}.audio_events.json"` (same dir as the
   transcript cache — already exists, gitignored, regenerable).
2. Reuse semantics MIRROR the transcript exactly: reuse when
   `cached_events.exists() and (not ctx.force or CLIP_REUSE_TRANSCRIPT)`; on `--force`
   unlink the stale cache and rescan (same `_reuse_transcript` flag — the deterministic
   rationale is identical).
3. On cache hit: `shutil.copyfile(cached_events, events)` + log
   `"Found cached audio events — skipping scan"`.
4. On scan success: cache it — but **only if valid** (JSON parses, has `windows`, no
   `skipped_reason`) so a scanner error is never immortalized.
5. **Do NOT skip `audio.wav` extraction** on the hit path — Stage 7 (`clip_tighten`,
   `sfx_cues`) reads the work-dir `audio.wav`; only the scan is skipped.

**Verify / DoD:** run the same VOD twice; second run logs the cache hit, Stage 2 drops
by the scan time, and the copied events file equals the cached one byte-for-byte.
`--force` rescans. **Gain: −10–16 min on every re-run.**

## #2 — Threaded audio scan (`AUDIO_EVENTS_THREADS`) · fresh VODs · zero quality risk · S-M effort

**Problem:** first-scan cost is a serial loop (`audio_events.py:729-755`) over ~1160
windows; each calls the pure function `_run_detectors(y_full[s:e], sr, ...)`.

**Implementation** (`scripts/lib/audio_events.py`):
1. New `_scan_threads(y_full, sr, tasks, n_threads, ...)`:
   `ThreadPoolExecutor(max_workers=n_threads).map()` over `tasks`, each worker slicing
   the shared **read-only** `y_full` and calling the SAME `_run_detectors`. `map()`
   preserves submission order → identical output ordering; identical math → identical
   values. Fires counted on the collected results; progress line tagged `[threads]`.
2. Knobs: env `AUDIO_EVENTS_THREADS` + CLI `--threads N`. Default **0 = off**.
   Precedence: `threads>=2` → thread path; else the existing process-pool/serial logic
   is untouched (the flaky mp pool stays available-but-non-default).
3. GIL note: hpss/onset are scipy-FFT/median-filter heavy → GIL released; expect 2–3×
   at 4 threads. BLAS pinning already in-module; keep `threads × BLAS ≤ cores`.
**Verify / DoD:** equivalence harness — synthetic 60 s wav, serial vs `--threads 4`,
assert **identical** windows JSON; then one real first-scan timing. **Gain: 970 s →
~350–500 s on first scans (superseded later by #6).**

## #3 — Vision-slot bench, then raise STAGE6/JUDGE workers · both · quality-neutral · S effort

**Problem:** LM Studio runs `PARALLEL=4` slots; Stage 6 and the judge both cap at 2.
Whether the mtmd vision encoder serializes server-side is **empirical** — bench, don't guess.

**Implementation:**
1. `scripts/research/bench_vision_slots.py` (NEW, research-side): take N frame files from
   any completed run, fire the SAME vision request at concurrency 1/2/3/4, report
   latency + throughput per level. (Load qwen3.6, bounded, ~5 min.)
2. **KV-headroom check first:** `context_length=32768` × 4 slots share the KV budget —
   confirm via LM Studio the 4-slot allocation fits (provisioning check; Pass-B/vision
   prompts are few-k tokens so truncation is unlikely, but verify — this is the ONE
   quality-relevant failure mode of more workers).
3. If scaling holds at 3–4: set `STAGE6_WORKERS`/`JUDGE_WORKERS` in the standard run
   env (both knobs already exist — no pipeline code change).
**Verify / DoD:** throughput curve filed here; worker bump validated on one run (same
clip set, judge verdicts sane, stage times drop). **Gain: judge+vision ~465 s → ~250–320 s
if slots scale; ~0 code.**

## #4 — Parallel clip renders (`CLIP_RENDER_WORKERS`) · both · quality-identical · M effort

**Problem:** Stage 7 renders ~10 clips sequentially (median 338 s); each render is an
independent ffmpeg pipeline.

**Implementation** (`scripts/pipeline/stages/stage7.py`):
1. ThreadPool over the solo-clip rows calling `_render_clip` (each worker just drives
   ffmpeg/profile_render subprocesses — I/O waits). Flag `CLIP_RENDER_WORKERS`
   default **1** (= today's serial loop).
2. **Shared-state audit (the real work):** effects_log JSONL appends → guard with one
   lock (or queue rows, flush at end); log lines interleave (acceptable — prefix has T);
   work-dir temp files are keyed by `T` (`clip_{T}.srt`, `moment_{T}.json`) → no
   collisions; cold-open move is per-clip file → safe.
3. **Keep sequential:** stitch/group member handling (deferred rows + group assembly)
   and the P-TIGHT audio reads (read-only, safe).
4. Bound workers at **2** initially: NVENC sessions (~8 hw cap, ~100–300 MB VRAM each —
   only ~2.2 GB free while qwen stays loaded) + CPU blur-fill filter load.
**Verify / DoD:** 2-worker run produces the same clip set with identical render
parameters (commands logged identical); no effects_log row lost; VRAM stays under
budget. **Gain: 338 s → ~180–220 s.**

## #5 — P2′: Two-phase Pass B (`CLIP_PASSB_WORKERS`) · both · quality-preserving BY CONSTRUCTION · L effort

**Problem:** Stage 4 is the boss (median 1156 s) and its ~24 chunk iterations each make
2 sequential LLM calls (moment call + card call) while 4 server slots sit idle. Naive
threading is REJECTED: chunk N's prompt needs chunks N-1/N-2's summaries.

**The dependency, precisely:** `prior_context_block(N) = f(summaries[N-2], summaries[N-1])`;
`summary(K) = one-liner(card(K))`; `card(K) = f(chunk_text(K))` **only**. So cards are
chunk-local → precompute them all, and every Pass-B prompt becomes constructible upfront
with content IDENTICAL to today's.

**Implementation** (`scripts/lib/stages/stage4_moments.py`, Pass B region ~1700–2160):
1. **Refactor step (no behavior change):** extract the chunk builder into a pre-pass
   materializing `[{ci, chunk_start, chunk_end, seg_type, chunk_text, chunk_segs}]`,
   and the loop body into `_chunk_card_phase(chunk)` / `_chunk_moments_phase(chunk,
   prior_block)` functions. Run serially → verify a run is unchanged.
2. **Phase A (cards):** ThreadPool(workers) over `_build_chunk_card(chunk_text)` for all
   chunks → `chunk_cards` + `chunk_summaries` (including today's exact fallback:
   first-12-words when a card fails). Quote verification is pure/per-chunk.
3. **Phase B (moments):** ThreadPool(workers) over chunks; each builds its
   `prior_context_block` from the precomputed `summaries[ci-2:ci]` (byte-equivalent to
   today's), calls the LLM, parses, runs the grounding cascade. **Collect results into
   `results[ci]` and extend `llm_moments` in ascending `ci` order** → assembly identical
   to the serial loop.
4. **Invariants to preserve:** BUG-31 outage breaker (shared counter under a lock;
   stop submitting once tripped); `_failed_chunks` end-of-pass retry (thread-safe append,
   retry stays serial); arc register (built AFTER, from `chunk_cards` — unchanged);
   grounding shared state audit (denylist maps are read-only; judge calls are HTTP —
   verify no module-level mutation during implementation).
5. Flag `CLIP_PASSB_WORKERS` default **1 = the existing serial loop runs untouched**
   (zero-risk fallback); `>=2` activates two-phase.
**Verify / DoD:** (a) workers=1 → byte-identical behavior; (b) workers=3 vs serial on the
same VOD: same chunk count, per-chunk `prior_context_block` hashes IDENTICAL (log a hash
per chunk in both modes — this is the quality proof), moment counts within normal
temp-0.3 run-to-run variance; (c) outage drill (kill LM Studio mid-pass → breaker trips).
**Gain: Stage 4 ~1156 s → ~550–750 s. Biggest absolute lever. Own session.**

## #6 — Block-vectorized audio scan · fresh VODs · needs equivalence validation · M-L effort

**Problem:** the per-window cost is `librosa.effects.hpss` + onset detection recomputed
per 10 s window — 1162 independent STFT/median-filter runs over overlapping audio.

**Implementation** (`scripts/lib/audio_events.py`, behind `AUDIO_EVENTS_VECTOR=1`):
1. Process in ~600 s blocks (bounded RAM): ONE STFT + ONE HPSS + ONE onset-envelope per
   block; derive each window's 3 dials by slicing the block-level arrays.
2. HPSS median filtering is time-local → block-slices ≈ per-window results except at
   block edges; **overlap blocks by one window** and take interior windows only.
3. Keep the old path as default until validation passes; flag flips default later.
**Verify / DoD:** reference-VOD comparison old vs new: per-window dial deltas ≤ 0.02
absolute AND identical fire counts at the thresholds (0.7/0.5/0.6) — the dials feed
threshold gates, so sub-threshold jitter is inert. **Gain: first-scan 970 s → ~60–180 s
single-threaded; supersedes #2. Do LAST (needs the most careful validation).**

## #7 — Durable run metrics + reader · observability · zero risk · S effort

**Problem:** stage timings live inside `last_run_*.json` diagnostics — which
`prune_traces` deletes. Speed history should survive cleanup and be trivially queryable.

**Implementation:**
1. `common.cleanup` (after the diag dict is built): extract
   `{run, ts, vod, vod_seconds (pass_c.max_time_s), clips (clips_made count),
   total_seconds, exit_code, stages:{label: seconds}}` → append ONE line to
   `clips/.diagnostics/run_metrics.jsonl` (append-only, failure-soft, ~15 lines of code).
2. `scripts/research/run_metrics.py` (NEW): `--backfill` scans existing `last_run_*.json`
   (recovers the 21 historical rows before any prune), `report` prints medians/trends
   per stage + realtime ratios, flags regressions (>1.5× median).
3. Add `run_metrics.jsonl` to the prune-safe set (it's not a `last_run_*` glob — already
   safe from `prune_traces`).
**Verify / DoD:** backfill yields ≥21 rows matching the distribution above; the next run
appends. **Gain: permanent speed history; the "what's our average" question becomes a
one-command answer.**

---

## Sequencing

| Order | Items | Why |
|---|---|---|
| 1 | **#1 + #7** | trivial, zero-risk, immediate; #7's backfill also locks in history |
| 2 | **#2** | small, hazard-free, helps every fresh VOD |
| 3 | **#3** | bench runs alongside any normal run; env-only change after |
| 4 | **#4** | contained Stage-7 change, real win |
| 5 | **#5** | the big refactor — own session, staged (refactor→verify→parallelize) |
| 6 | **#6** | highest validation burden; #1+#2 already blunt the cost it targets |

## Projected effect (3.2 h VOD, 10 clips, medians)

| Scenario | Today | After #1–#4 | +#5 | +#6 |
|---|---|---|---|---|
| Re-run (cached transcript+events) | ~50 min | ~35 min | **~25 min** (0.13×) | — |
| Fresh VOD | ~66 min | ~52 min | ~42 min | **~33 min** (0.17×) |

## Excluded (would trade quality — owner constraint)
- **Judge diet** (frames 4→3, comparisons 30→24): real ranking-resolution trade.
- **Naive Pass-B chunk threading**: breaks the prior-context callback memory (superseded by #5).
- **Whisper batched-inference / distil models**: near-neutral but not bit-identical at
  segment boundaries; revisit only if fresh-VOD transcription (781 s, once per VOD) matters.
- **Frame downscaling**: frames are already 960×540 q2 — nothing to gain.

## Threading-vs-quality note (owner Q 2026-07-08, verified)
Threading changes no per-request output: each request is an independent forward pass
(continuous batching computes each sequence's logits independently); Pass B already runs
`temperature=0.3` (stochastic run-to-run today — threading adds no new variance); chunk
results are extend+re-sorted by timestamp (order-independent assembly); the judge fixes
each round's pairings up-front (parallel == serial by design). The one genuine risk is
**KV/context contention at high worker counts** — a provisioning check (#3.2), not a
correctness issue.

Related: [[concepts/plan-activation-wave-2026-07]] (Run-1 timing source) ·
[[concepts/bugs-and-fixes]] (the spawn-hang incident + BLAS pinning) ·
[[entities/audio-events]] · [[concepts/vram-budget]] (LM Studio PARALLEL=4 slots) ·
[[concepts/case-rap-battle-missed]] (why the prior-context block must survive #5)
