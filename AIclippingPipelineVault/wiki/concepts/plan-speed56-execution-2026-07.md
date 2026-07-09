---
title: "Execution Plan — Speed #5 (two-phase Pass B) & #6 (vectorized scan) with per-iteration validation"
type: concept
tags: [plan, performance, pass-b, stage-4, audio-events, vectorization, validation, concurrency]
sources: 0
status: in-progress
updated: 2026-07-08
---

# Execution Plan: Speed #5 & #6 — iteration-by-iteration, validation-gated

> [!note] Execution status 2026-07-08 — #5 engine PROVEN (logic); #6 harness done + ROI re-called
> **#5 — equivalence engine SHIPPED + PROVEN in pure logic (the hard part):**
> `scripts/lib/passb_driver.py` (serial + two-phase, dependency-injected) +
> `scripts/research/passb_equiv.py` (mock proof, **PASS**: 6 sizes × 4 worker counts →
> identical prompts/moments/summaries; prior-context window; card-failure fallback;
> failed-chunk set + retry; happy-path breaker). The proof FOUND a real bug in pure logic
> before any live run: serial creates a chunk's summary only after its moment call succeeds,
> so a transient moment-call failure changes later prior-context — fixed with an exact
> **reconciliation pass** (rebuild+re-run only succeeded chunks whose prior-window held a
> failed chunk; zero cost on the happy path). Two-phase is now byte-exact to serial even
> under transient failures.
> **I5.0 SHIPPED 2026-07-08:** prompt-hash instrumentation added to the LIVE Pass-B loop
> (`_PASSB_PROMPT_HASHES` → `passb_prompt_hashes.json`; additive, zero behavior change) +
> `CLIP_PASSB_DETERMINISTIC=1` greedy-decode validation flag (call_llm temp 0). **Baseline
> temp-0 run COMPLETE (exit 0):** golden manifest captured + saved durably to
> `learning/passb_baseline/2xRaKai_temp0_workers1.json` — 25 chunks, fingerprint
> `8e0316624c64089a`. This is the reference the two-phase cut-over is gated against.
> (Watchdog note: `--force` temp-0 + the ~11-min judge pushed the run past 60 min — a false
> timeout, not a hang; the manifest is written mid-Stage-4 so it was captured well before.)
> **Reproducibility caveat (fold into the cut-over's first run):** the strategy assumes temp-0
> gives identical prompts run-to-run. The FIRST cut-over run must be workers=1 temp-0 and
> match this fingerprint — proving temp-0 reproducibility — BEFORE trusting the workers=3
> comparison (else temp-0 kernel noise can't be told apart from a cut-over bug).
> **Loop-complexity finding (reshapes I5.2):** reading the real loop showed it is FAR more
> stateful than passb_driver's clean model — a signal GATE with dead-streak SAMPLING state
> (`_PASSB_DEAD_STREAK`), per-chunk `conversation_shape` mutating a shared index, skip-records,
> per-chunk signals — ALL before the prompt/moments/card work the driver models. So the
> cut-over must extract a SEQUENTIAL cheap pre-pass (gate + shape + signals → the alive-chunk
> list) and hand only alive chunks to the driver's two-phase. This is exactly why it's
> incremental-with-a-live-gate, NOT a one-shot rewrite.
> **CUT-OVER 1 (card-parallel) SHIPPED 2026-07-08:** instead of the full risky two-phase
> (whose prompt-hash gate would NOT cover the grounding/scoring code — a false-confidence
> trap I caught before shipping), the first cut-over parallelizes only the chunk-LOCAL
> arc-card calls (`CLIP_PASSB_CARD_WORKERS`, default 1=off): all cards precomputed in
> parallel before the loop, looked up by chunk_text with an INLINE FALLBACK (correctness
> guaranteed even if the windowing walk drifts). Prompts / moment calls / grounding /
> summary-gating are UNTOUCHED, so this removes the ~24 card calls from the sequential
> critical path (~35% of Stage 4) with no exposure on the risky code. Validation run in
> flight (temp-0, `CLIP_PASSB_CARD_WORKERS=4`): the prompt-hash manifest must equal the
> golden baseline `8e0316624c64089a` (cards deterministic at temp-0 → identical summaries →
> identical prompts). **Remaining:** confirm the hash match → CUT-OVER 2 (parallel MOMENT
> calls via a sequential grounding post-pass — the bigger win; needs output-level validation
> since the hash gate can't see grounding) → outage drill → soak → enable.
> **#6 — harness SHIPPED (`scripts/research/vector_equiv.py`, I6.0 old-vs-old PASS: 0
> deltas/0 flips), full vectorization NOT built — ROI reassessed:** #2 (threaded scan, now
> DEFAULT 4, 3.3×) + #1 (cache, skips re-run scans) already collapsed the scan cost #6
> targeted. #6 would take a FRESH scan from ~5 min (threaded) to ~2 min — a ~3 min gain on
> fresh VODs only — for a large, quality-surfaced DSP refactor of `_run_detectors`.
> **Recommendation: DEFER #6 indefinitely.** The harness stays as the ready gate if the
> fresh-scan cost ever becomes the bottleneck again.

Owner directive: a detailed structured implementation plan for the two staged speed items,
with the validation testing built into each iteration (not bolted on at the end). Designs
live in [[concepts/plan-pipeline-speed-2026-07]]; this page is HOW to build them safely.
House rules apply throughout: flag-gated, default = today's behavior, failure-soft, every
iteration ends compilable + committed.

> [!warning] Validation-design correction (supersedes the earlier "prompt-hash proof" as stated)
> The earlier #5 plan said "run workers=1 vs workers=3 and compare prompt hashes." That is
> **confounded**: chunk cards are LLM calls through `call_llm` (hardcoded `temperature: 0.3`,
> `stage4_moments.py:979`), so summaries — and therefore Pass-B prompts — differ run-to-run
> even on CORRECT code. Two fixes, both in this plan:
> 1. **Mock-injected determinism (the real proof):** extract the Pass-B driver into an
>    importable module with injected `call_llm`/card functions; a harness runs the SERIAL
>    driver and the TWO-PHASE driver over the same chunks with canned deterministic
>    responses → prompt bytes and assembly order must be IDENTICAL. Pure logic, no LM
>    Studio, catches every assembly bug.
> 2. **Live temp-0 integration check:** a `CLIP_PASSB_DETERMINISTIC=1` validation flag
>    forces `temperature 0` for Pass-B/card calls so a live workers=1 vs workers=3 hash
>    comparison becomes meaningful (greedy decoding; rare token wobble possible → any
>    mismatch is deep-diffed and must attribute to summary WORDING only, never structure).

---

# Part A — #5 Two-phase Pass B (`CLIP_PASSB_WORKERS`)

**Goal:** Stage 4's ~24 chunk iterations each make 2 sequential LLM calls against a server
with 4 idle slots. Precompute all chunk cards (chunk-local by construction), then run all
moment calls concurrently with byte-equivalent `prior_context_block`s. Median 1156 s →
~550–750 s. Quality-neutral by design; the iterations below prove it stays that way.

**Session budget:** one dedicated build session (I5.0–I5.4) + one validation session
(I5.5–I5.7, needs LM Studio + ~2 Stage-4-only runs). Stage-4-only loop trick:
`CLIP_REUSE_TRANSCRIPT=1` + the #1 events cache → only Stages 3+ re-run (~35 min/run).

### I5.0 — Instrumentation + golden baseline (no behavior change)
- Add per-chunk prompt hashing to the EXISTING serial loop: `[PASSB] chunk N
  prompt_hash=<sha1[:12]>` to stderr + `passb_prompt_hashes.json` in the work dir (the
  diagnostics snapshot picks it up automatically).
- Add the `CLIP_PASSB_DETERMINISTIC=1` validation flag: routes Pass-B + card calls at
  `temperature 0` (validation-only; never default).
- **Gate G5.0:** py_compile; one live temp-0 Stage-4 run → hash manifest written, moments
  count normal. This manifest is the GOLDEN BASELINE for every later gate. Commit.

### I5.1 — Chunk materialization pre-pass (no behavior change)
- Extract chunk construction into `_build_passb_chunks() -> list[{ci, chunk_start,
  chunk_end, seg_type, chunk_text, chunk_segs}]`; the serial loop iterates the list.
  Chunking is text-only → deterministic.
- **Gate G5.1:** live temp-0 run → **hash manifest IDENTICAL to G5.0** (hard equality —
  chunk text and order are deterministic, so any diff = a bug in the pre-pass). Commit.

### I5.2 — Extract the driver into an importable module (the testability keystone)
- New `scripts/lib/passb_driver.py`: pure functions with INJECTED callables —
  `run_serial(chunks, llm_fn, card_fn, ground_fn, cfg)` reproducing today's exact order
  (moments call → grounding → card call → summary append → next chunk), and the prompt
  assembler `build_prompt(chunk, summaries, cfg)` shared by both drivers.
  `stage4_moments.py` keeps only thin adapters (its real `call_llm`, `_build_chunk_card`,
  grounding closure) and calls the driver. All BUG-31 outage-breaker checks and
  `_failed_chunks` collection move into the driver with injected probes.
- **Gate G5.2:** (a) live temp-0 run → hashes IDENTICAL to G5.0; (b) NEW
  `scripts/research/passb_equiv.py --self-test`: runs `run_serial` with canned mock
  responses → asserts prompt bytes, summary fallbacks (card-failure → first-12-words),
  failed-chunk queueing, and breaker trip (mock 3 consecutive outages) all match the
  spec. Commit. *This is the highest-risk mechanical step — the gate pair (live hashes +
  mock unit) is what makes it safe.*

### I5.3 — Phase A: parallel cards
- `run_two_phase(chunks, llm_fn, card_fn, ground_fn, cfg, workers)`: Phase A =
  ThreadPool(workers) over `card_fn(chunk_text)` for ALL chunks → `summaries[ci]`
  (identical fallback semantics). Phase B still SERIAL, but builds each prompt from the
  precomputed `summaries[ci-2:ci]`.
- **Gate G5.3:** (a) `passb_equiv.py`: mock serial vs mock two-phase(workers=4) →
  **prompt bytes IDENTICAL per chunk** + identical summary set (the core equivalence
  theorem, now proven in pure logic); (b) live temp-0 run workers=2 → hashes vs G5.0:
  expect identical; any diff deep-diffed and must be summary-WORDING-only (benign
  greedy-decode wobble), never structural. Commit.

### I5.4 — Phase B: parallel moments
- Phase B = ThreadPool(workers) over chunks; per-chunk results into `results[ci]`;
  `llm_moments` extended in ascending `ci` (assembly identical to serial). Outage breaker:
  lock-guarded consecutive-failure counter; once tripped, stop SUBMITTING (in-flight
  finish; completed chunks keep — matches serial "abort remaining" semantics as closely
  as concurrency allows; the delta is documented as outage-path-only). `_failed_chunks`
  appended under lock; end-of-pass retry stays serial. Grounding shared-state audit:
  denylist maps read-only ✔; judge calls HTTP ✔; verify no module-global mutation.
- **Gate G5.4:** (a) mock harness: serial vs full two-phase → identical prompts AND
  identical `llm_moments` assembly given canned responses (order-independence proof);
  (b) live temp-0 run workers=3 vs G5.0 manifest (same benign-diff rule); (c) moments /
  categories / grounding-null counts within normal run variance; (d) Stage-4 wall-clock
  via `run_metrics.py report` — expect the LLM section ≥1.8× faster. Commit.

### I5.5 — Outage drill
- Start a workers=3 run; stop LM Studio mid-Phase-B.
- **Gate G5.5:** breaker trips within ~3 failures; pool drains (no hung threads — bound
  by the existing per-call timeout=240 s); pipeline degrades to Pass-A moments and exits
  cleanly, matching the serial BUG-31 behavior. Restart LM Studio after.

### I5.6 — Full-pipeline soak (normal temperature)
- One complete end-to-end run, `CLIP_PASSB_WORKERS=3`, normal temp, all standard flags.
- **Gate G5.6:** phase_runner evaluate PASS; clips render; owner spot-checks the batch
  (same review flow as the Activation Wave); `run_metrics` shows the Stage-4 drop with no
  regression flags elsewhere.

### I5.7 — Enable
- Code default stays `workers=1` (= untouched serial path). The standard run env gains
  `CLIP_PASSB_WORKERS=3`. Rollback = drop the env var.

---

# Part B — #6 Vectorized audio scan (`AUDIO_EVENTS_VECTOR`)

**Goal:** replace 1,162 independent per-window librosa computations with per-BLOCK
transforms sliced per window. First-scan 970 s → ~60–180 s. **This is the one proposal
with a real quality surface**: block-level STFT/HPSS ≠ per-window results at window edges
(librosa `center=True` pads each window's own boundaries), so **byte-equality is
impossible by construction** — unlike #2/#5. The dials feed BINARY gates
(`stage4_moments.py:827-837`; anomaly lane ≥0.40), so the ship gate is **per-window fire
equality**, guaranteed by a hybrid fallback (below), not hoped for.

**Session budget:** one build session (I6.0–I6.2) + validation runs that need only CPU +
the cached `audio.wav` files (no LM Studio) — cheap to iterate.

### I6.0 — Test-first: the equivalence harness + detector characterization
- NEW `scripts/research/vector_equiv.py`: runs two scanner paths over the same audio →
  per-window table (3 dials each), max/mean absolute deltas, and a **fire-flip report**
  at ALL consumed thresholds (0.7 / 0.5 / 0.6 / 0.40): lists every window whose gate
  membership differs. Modes: `--old-vs-old` (sanity), `--old-vs-new`, `--wav`/`--vod`.
- Document `_run_detectors` internals while reading them (which transforms are
  window-local vs context-dependent — onset peak-picking context is the known hazard).
- **Gate G6.0:** old-vs-old on a synthetic wav → zero deltas, zero flips (harness proven).
  Commit.

### I6.1 — Block-transform core (flag-gated, default off)
- `_scan_vectorized(y_full, sr, tasks, block_s=600)` behind `AUDIO_EVENTS_VECTOR=1`:
  per block (overlapped by ≥1 window), compute STFT/mel/onset-envelope/HPSS ONCE;
  per window, slice frames and compute the three dials with the SAME scoring math,
  refactored to accept precomputed transforms. Interior windows only per block (the
  overlap absorbs edge effects).
- **The fire-equality guarantee — hybrid near-threshold recompute:** any window whose
  ANY dial lands within ±0.05 of its consumed threshold is **recomputed exactly with the
  old per-window path**. Near-threshold windows are rare, so the cost is negligible —
  and gate flips become impossible ANYWHERE the two paths could disagree by ≤0.05.
  Residual risk is only a window vectorized-vs-exact delta >0.05 — caught by I6.2.
- **Gate G6.1:** synthetic multi-tone wav → all deltas ≤0.02, zero flips; py_compile;
  the flag OFF path byte-identical (threads default untouched). Commit.

### I6.2 — Real-VOD validation (the ship gate)
- `vector_equiv --old-vs-new` on ≥2 real VODs (rakai 3.2 h + Tylil — `audio.wav`
  re-extractable; scans are CPU-only).
- **Gate G6.2 (hard):** **ZERO fire flips across every window of both VODs** + max delta
  ≤0.02 outside the hybrid band + hybrid-recompute count logged (expect <5% of windows).
  Any flip → widen block overlap / widen the hybrid band / fix the specific detector —
  and re-run. Do not proceed on "close enough."

### I6.3 — Wire + benchmark
- Precedence when `AUDIO_EVENTS_VECTOR=1`: vectorized > threads > process-pool > serial.
  Timing benchmark on a real first-scan; `run_metrics` comparison.
- **Gate G6.3:** ≥5× vs the threaded path on a real VOD; the wired path passes
  `vector_equiv` one final time end-to-end. Commit.

### I6.4 — Default-flip decision (owner)
- After 2–3 clean production runs with the flag on (fire counts consistent in logs),
  owner decides the default flip; the old path stays as `AUDIO_EVENTS_VECTOR=0` fallback
  forever (it is the reference implementation the hybrid recompute depends on).

---

# Cross-cutting

| | #5 | #6 |
|---|---|---|
| Order | FIRST (biggest lever, quality-neutral by design) | second (needs #6's win less now — #1 cache + #2 threads already landed) |
| Equivalence type | EXACT (prompt bytes, via mock harness) | TOLERANCED (fire equality guaranteed via hybrid) |
| Validation cost | 2 Stage-4-only runs (~35 min each, LM Studio) + mock harness (free) | CPU-only scans of 2 VODs (no LM Studio) |
| Rollback | unset `CLIP_PASSB_WORKERS` (default 1 = old loop) | unset `AUDIO_EVENTS_VECTOR` (default off) |
| Failure honesty | outage-path coverage may differ from serial (documented, drill-tested) | edge-window math differs by construction (bounded, hybrid-guaranteed at gates) |

- Every iteration ends: `py_compile` clean + gate evidence in the commit message + wiki
  log entry per session (not per iteration).
- Metrics: `run_metrics.py report` before/after each enable — the #7 tool is the
  measurement instrument for both.
- If any gate fails twice after a genuine fix attempt: stop, file the failure in this
  page, and re-plan — do not ratchet tolerances to pass.

Related: [[concepts/plan-pipeline-speed-2026-07]] (designs + measured baseline) ·
[[concepts/bugs-and-fixes]] (BUG 31 breaker; the spawn-hang class) ·
[[concepts/case-rap-battle-missed]] (why prior-context must survive #5) ·
[[entities/audio-events]]
