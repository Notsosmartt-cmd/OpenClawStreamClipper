---
title: "Pipeline Speed — Empirical Findings (validation testing, 2026-07)"
type: concept
tags: [performance, speed, testing, validation, findings, reference, llm-determinism, gpu, whisper, stage-4, audio-events]
sources: 0
updated: 2026-07-15
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

> [!warning] 2026-07-09 CORRECTION — "exhausted" was over-broad
> The claim above is true of **pipeline-code** levers. The **LM Studio serving configuration**
> under the 35B was never tuned and holds at least one untested quality-neutral-by-construction
> lever: **speculative decoding** (draft+verify preserves the target's output distribution —
> categorically different from the §3 co-batching landmine). Controllability was researched
> live 2026-07-09 (§9); the plan is [[concepts/plan-serving-stack-2026-07]].

## 7b. Moment-parallel A/B (2026-07-09) — the LLM floor is now FULLY closed

Owner approved wiring Speed #5 cut-over 2 (moment calls with 2 in flight, two-phase
prior-context from precomputed cards) and the gated A/B ran on 2xRaKai
(`moment_parallel_ab.py`, verdict `moment_ab_20260709_141429.json`):

| Arm | Stage 4 | Selected clips |
|---|---|---|
| serial (refactored path — validated live) | 1107 s | 10 |
| workers=2 | 959 s | 10 |

- **Speedup 1.15×** (gate ≥1.4× → FAIL). **Overlap 4/10 ±20 s** (gate ≥5/10 → FAIL).
- **Why so little:** LM Studio co-batches the 2 concurrent requests into shared forward
  passes, and on the cross-vendor Vulkan split each pass is dominated by fixed
  coordination costs that batching barely amortizes — the same floor that killed
  speculative decoding (§9b) and made card-parallel ~4%. The theoretical
  2-stage-pipeline 2× never materializes because the bottleneck isn't idle compute,
  it's the per-pass sync.
- **Disposition (FINAL 2026-07-09): DEFAULT-ON.** First reverted on a 1.4× bar → owner
  lowered it to **1.1×** (the measured 1.15× clears it) + reclassified overlap as advisory
  ("different-but-good is fine; overlap ≠ quality") → owner spot-checked all 6 parallel-only
  clips: **4 good ('Fake Girls' Freestyle, Principal Addresses, 'Yo!' Freestyle, 'Samurai
  Slicer'), 2 needs-work (both "not bad"), ZERO bad** → the concurrent draw does not surface
  junk → `CLIP_PASSB_MOMENT_WORKERS` default **2** (kill switch `=1`). The behavior-identical
  serial arm (1107 s ≈ the 1156 s historical median) also confirmed the instrument. 4 positive
  labels filed + frozen from the review.
- **Consequence:** §7's ceiling still holds — parallelism only buys ~1.15× because serving
  co-batching barely amortizes the cross-vendor per-pass sync (spec-decode 0.12×, cards ~4%
  are the same floor). The 15% is real but modest and concentrated on LONG VODs (Lacy-class
  Stage-4 ~3 h → ~30 min; typical 2-3 h VOD → ~3 min). Beyond it, the only Stage-4 levers
  remain hardware (single card that fits the model) or a model-tier change.

## 8. Validation-coverage caveat + final #5/#6 disposition

- **Single-VOD caveat:** every FULL-pipeline validation run this session used 2xRaKai
  (deliberate — controlled A/B needs the VOD held constant). #6's scan validation used 2
  VODs (scan only). Broad generalization of the speed/quality findings rests on 1–2 VODs;
  spread future validation runs across VODs.
- **Final disposition:** #5 card-parallel built + default-off (**~4%, marginal — cards are
  cheap; the real Stage-4 lever is moment-parallel, which stays HELD** on the §3
  reproducibility tradeoff, enable-by-owner-review only). #6 built + zero-flip-validated +
  default-off (**dominated by #2**). Production speed gains come from **#1 + #2 only**.

## 9. Serving-stack controllability facts (researched live 2026-07-09)

Measured/verified on this machine while scoping [[concepts/plan-serving-stack-2026-07]]:

> [!danger] 2026-07-09 RESOLVED — speculative decoding is a MEASURED 8× REGRESSION (no-go)
> Full arc: (1) the CLI flag `--speculative-draft-simple` is REJECTED by the native Vulkan
> runtime ("only supported by the llama.cpp engine protocol runtime"); the API path is also
> dead (`draft_model`=HTTP 400, `speculative_decoding` obj ignored). (2) The owner enabled
> **Settings → "Use LM Studio Engine protocol"** → the CLI flag then LOADS fine. (3) But the
> **benchmark killed it: 6.0 tok/s with the qwen3.5-2b draft vs 50 baseline (8× slower).**
> Isolated: engine-protocol-with-**no**-draft = **51.7 tok/s** (the runtime switch is neutral;
> the DRAFT is the regression). Root cause = §7: the cross-vendor Vulkan split is
> coordination/bandwidth-bound, so the draft's extra per-step cross-GPU passes cost more than
> they save. **S1 is RED — do not enable the draft.** The engine-protocol setting is safe
> either way. Only revisit if the model runs on a SINGLE card. See [[concepts/plan-serving-stack-2026-07]] §3a.

- **`lms load` exposes speculative-decoding CLI flags** (`--speculative-draft-simple
  --speculative-draft-model <m>`, `--speculative-draft-mtp`, plus max/min-tokens and
  min-continue-probability; CLI commit 6041ae0) — but see the warning above: the flags parse
  yet the Vulkan runtime refuses them at load time. NOT scriptable on this stack.
- **MTP is DEAD for the current GGUF**: metadata cache says `supportsMtp=false` for
  `Qwen3.6-35B-A3B-Q4_K_M.gguf`, and a live `--speculative-draft-mtp` load fails fast at 0%
  with `Error: MTP speculative decoding requires a GGUF model with a bundled supported MTP
  head.` (unsloth publishes MTP repacks of the same model — but that's a weights change.)
- **Draft vocab compatibility** (from LM Studio's gguf metadata cache): qwen3.6-35b-a3b and
  qwen3.5-9b share speculation vocab **248320** → the qwen3.5/3.6 family inter-drafts.
  qwen3-8b (151936), gemma (262144), nemotron (131072), gpt-oss (201088) are incompatible.
  Qwen3.5 ships 0.8B/2B/4B smalls — proper draft sizes, none on disk yet.
- **Per-model GUI toggles persist to a plain JSON**:
  `<LMS home>\.internal\user-concrete-model-default-config\qwen\qwen3.6-35b-a3b.json`
  (LMS home = `C:\Users\user\.cache\lm-studio`, pointer `~\.lmstudio-home-pointer`; GGUFs on
  `G:\lm-studio`). For the 35B it holds **`flashAttention: true` (already on — no gain
  available there)**, `contextLength: 16384`, and `enableThinking: false` — which retroactively
  explains the old "thinking only controllable via UI" finding: the UI writes this file and
  every load (CLI/JIT) reads it, so editing the file ≡ the UI toggle.
- **GPU split is NOT tunable in LM Studio on Vulkan dual-GPU**: UI offers only "split evenly";
  no CLI flag; no config key found on disk (`hardware-config.json` holds only
  `gpuStrictVramCap`). Row-split / per-card tensor ratios aren't exposed at all.
- **Prefill batch (`evalBatchSize`)** has no CLI flag and no on-disk instance (never set) —
  controllable via GUI advanced load settings; the file key must be confirmed by
  set-once-and-diff before scripting it.

## 9b. Serving BENCHMARKS (measured 2026-07-09, `scripts/research/bench_serving.py`)

Serial requests to the loaded 35B (`-c 16384`, Vulkan dual-GPU), native `/api/v0` `stats`.
Prompts = synthetic-realistic (real `config/patterns.json` catalog ~740 tok + real
transcript windows), ~2700 tok each, `max_tokens=512`, temp 0.

- **Decode throughput = 50 tok/s** (steady, 8 calls). Healthier than the 16%-util reading
  implied — the 3B-active MoE decodes fine; the "floor" is mostly the fixed per-call cost.
- **Prefill is ~42% of a COLD call** (~6.7 s to prefill ~2700 tok ≈ **~520 tok/s prefill**;
  large-prompt check: ~15 k tok cold-prefill = 29 s ≈ 523 tok/s). Decode of ~370 out-tokens
  ≈ 7.4 s. So a cold Pass-B call ≈ 6.7 s prefill + 7.4 s decode ≈ 14 s — prefill and decode
  are comparable, so BOTH S1 (decode) and C2/P (prefill) target real shares.
- **KV prefix-cache IS ALREADY ON and effective:**
  - Identical prompt re-sent → prefill **6.7 s → 0.1 s (98% cached)**.
  - Different tail, SAME static prefix → prefill **6.7 s → 3.5 s (~48% saved)** vs a
    changed-prefix control at 6.7 s. **Reuse fires across different requests.**
  - **Survives alternation:** a foreign (card-shaped) prompt between two shared-prefix calls
    did NOT evict the prefix (still 3.5 s after) — because the model loads with **PARALLEL=4**
    and llama.cpp routes to the longest-common-prefix slot. This is what makes C2b viable.
  - Production caveat: today the Stage-4 prompt puts the static catalog LAST, so consecutive
    chunks share ~nothing → the cold 6.7 s prefill is paid every call. C2b (static-first)
    exposes the ~catalog+template (~1000-1200 tok) as a shared prefix → ~40% of prefill
    reusable after call 1 ≈ ~2.7 s/call ≈ ~1 min/run across ~24 moment calls. Modest, real.
- **JSON-retry rate = 0.49%** across 409 chunk-calls / 12 runs (`retry_audit.py`); the 2
  events were network timeouts, **zero JSON-parse failures** → C6 (grammar-constrained
  decoding) CLOSED (no waste to recover; it would only alter the token distribution).
- **Speculative decoding A/B** (engine protocol on, same prompts): no-draft **51.7 tok/s** vs
  qwen3.5-2b-draft **6.0 tok/s** = **8× REGRESSION** → S1 RED (see §9 danger box).
  - **WHY (the economics inversion):** speculative decoding wins only when
    `draft_cost ≪ target_cost` — normally a 2B drafts ~15-20× faster than a 35B, so you draft
    K tokens cheaply and the big model verifies all K in one pass. On this rig per-pass time is
    set by the **fixed cross-GPU coordination** (PCIe activation shuffle + NVIDIA↔AMD sync),
    not by matmul size (that's the §7 floor / 16% util). So a 2B forward pass costs almost as
    much wall-time as a 35B pass → **`draft_cost ≈ target_cost`**, the core assumption is
    violated. Speculative decoding then just multiplies passes (K draft + 1 verify + redo on
    rejects) with no compute savings → ~8× the work per emitted token. Numbers fit: baseline
    ~20 ms/tok (1 pass) → draft ~167 ms/tok (~8 near-full-cost passes). Plus the draft shares
    the pool's VRAM bandwidth.
  - **Catch-22 (why it's unfixable on THIS hardware):** the only fix is to make the draft
    genuinely cheap = run it on a SINGLE fast GPU (no cross-vendor hop). But the 35B doesn't
    fit on the 16 GB NVIDIA card alone — which is the entire reason for the cross-vendor split.
    So the condition that makes the draft cheap is the same condition the split exists to avoid.
    Revisit only if the target model ever fits one card.
- **C1 prefetch = validated + DEFAULT-ON.** Audio-events **BYTE-IDENTICAL** through the isolated
  prefetch path vs the normal Stage-2 scan (live gate); the transcript compare was confounded by
  a `whisperx→faster-whisper` symlink-privilege fallback (env non-determinism, affects normal
  Stage 2 identically — not a C1 defect). **Contention A/B settled the benefit:** NVENC render
  median **16.9 s alone vs 16.9 s during a concurrent whisper job at 88% GPU util = +0.1%** —
  the NVENC encoder ASIC and whisper's CUDA cores are different silicon, so the prefetch
  overlaps the ~5.6 min render window essentially free. Saves ~5.6 min per VOD transition in a
  batch → promoted default-on (`CLIP_BATCH_PREFETCH`). Chose the micro-bench over a ~2.5 h full
  batch because C1 is byte-safe, so contention was its only failure mode.

> [!warning] §10's rates are the PRE-Wave-3 baseline — superseded by §11 (2026-07-15)
> Keep §10 for the before/after record; the CURRENT stage rates live in §11 below.

## 10. Fresh-VOD baseline + LM Studio decay curve (measured 2026-07-14, 9-VOD batch)

Mined from the 20260713_223856 batch (`pipeline_stages.log` stage marks + persistent log;
post-BUG-71c, qwen3.6-35b-a3b, style-profiles + A/B on). 7 VODs completed before the LM
Studio death; VODs 5–7 were fully FRESH (no cached transcript/audio/segments).

| VOD (order run) | len | cache | wall | min/VOD-h |
|---|---|---|---|---|
| Thetylilshow | 3.1h | transcript | 81m | 25.9 |
| 2xRaKai | 3.2h | t+audio+segments | 74m | 22.8 |
| Jynxzi | 6.4h | t+audio | 149m | 23.4 |
| Lacy 0703 | 4.0h | transcript | 103m | 25.6 |
| tbvnks 0706 | 6.3h | **FRESH** | 184m | 29.3 |
| AliOnAirr | 3.7h | **FRESH** | 139m | 37.5 ⚠ |
| tbvnks 0708 | 5.6h | **FRESH** | 224m | 40.3 ⚠ |

- **Healthy fresh baseline ≈ 26–30 min wall per VOD-hour (~2.2× realtime)** — a fresh 3h VOD
  ≈ 80–95 min. Warm caches ≈ 23–26 min/VOD-h. Additive model: S2 transcribe+audio-scan
  **4.1 min/VOD-h** (flat across all fresh runs — the post-71c scan is a solved problem),
  S3 ~1, S4 moments **14.4–15.8 min/VOD-h** (≈60% of wall = the dominant cost), plus
  ~2.5–3 min per exported clip (judge ~35 s + vision ~80 s + edit ~40 s).
- **⚠ The LM Studio decay curve (new failure signature):** S4's per-hour rate was stable
  14.4–15.8 min/VOD-h for the first ~12 h of the batch, then climbed 20.5 → 24.1 → 24.9
  (+70%) over the final ~6 h — while S2/S3 (non-LLM) stayed flat, isolating the slowdown to
  the server, not the pipeline. The 11:02 timeout death was the END of an hours-long
  prodrome, not a sudden event. Implication: a per-VOD LM Studio health probe (or scheduled
  model reload between VODs) in long batches would both recover the lost throughput on late
  VODs and likely prevent the crash. (Idea filed, not built — owner gate.)
- **Batch bookkeeping gotcha:** batch runs write NO `run_metrics.jsonl` rows per VOD —
  `cleanup()` fires once at process exit, so a crashed batch leaves zero rows and the
  history under-represents exactly the long runs that stress the system. Per-VOD rows would
  need a `cleanup()`-equivalent inside the batch loop.

## 11. CURRENT stage rates — post-Wave-3 final system (measured 2026-07-14/15)

Sources: Raud 3.47 h (fresh S2 run + the clean lane run + the D6 run) and FirstFullAudio
1.19 h (2 runs). System: WhisperX+diarization default, A1 S2-overlap, 9B-CUDA text lane
(pool-fixed), 640:360 frames, judge cap 20, master-slice captions, D6 render-overlap.

| stage | rate (per VOD-hour) | notes / per-unit |
|---|---|---|
| S1 discovery | ~8 s fixed | |
| S2 transcribe (FRESH) | **2.4–2.5 min/VOD-h** | WhisperX VAD-ASR + wav2vec2 align + diarization + events scan, scan overlapped (A1). Cached: seconds. |
| S3 segment votes (9B) | **~0.4 min/VOD-h** | 89.7 s / 3.47 h (was 274 s on the 35B — B3 3.1×). C4 cache hit → ~0. |
| S4 moments (9B-CUDA lane) | **3.9–5.0 min/VOD-h** | talk VOD 3.9, gaming ~4.8–5.0 (denser chunks). Zero failures post-BUG-73/74 fixes. |
| S6 model load | 30–70 s fixed | 35B Vulkan warm load |
| S5.5 vision judge | **1.9–2.9 min/VOD-h** | ≈37–55 s per shortlisted moment at cap 20 / 640:360 frames. ⚠ open item: one run logged "40 comparisons" vs the 20 cap + 601 s — accounting/behavior to check. |
| S6 enrichment **+ absorbed renders (D6)** | **~5.3–5.8 min/VOD-h** | ≈80–110 s per clip INCLUDING its render (D6 folds S7 in). Carried the BUG-74 CPU-9B ghost in the 07-15 run — expect −1-2 min next run. |
| S7 residual | **~8 s fixed** | D6 leaves only manifest/stitch/post steps (was 6-7 min + variants) |
| S8 summary | ~2.5 s | |

**Aggregates:** fresh ≈ **14.5–16.5 min per VOD-hour** (3 h VOD ≈ 44–49 min; ~40–45 once
the BUG-74 pin applies) vs the §10 pre-wave 26–30. Cached re-run measured **13.9 min/VOD-h**
(Raud D6 run, 48 m 14 s). Quality additions not present in the §10 baseline: speaker
diarization, wav2vec2 word timing, caption-transcript consistency (master-slice).

## Bottom line

The quality-safe speed gains are the ones that DON'T touch LLM generation:
- **Wave 1 (shipped):** #1 audio-events cache, #2 threaded scan, #7 metrics — byte-identical.
- **Wave 2 (shipped 2026-07-09):** **C3** reload hygiene, **C4** segment cache, **C1** cross-VOD
  batch prefetch — all default-on, all byte-safe (caches replay deterministic work; C1's overlap
  measured contention-free). Reach production automatically via the dashboard's bare-metal path
  ([[concepts/plan-serving-stack-2026-07]] §0a).

Everything that touches LLM *generation* is either a reproducibility tradeoff (Stage-4
cards/moments parallelism — changes the clip set within temp-variance) or a measured **loss**:
**speculative decoding = 8× slower** on the cross-vendor split (§9b — the coordination floor
inverts the draft economics). The floor itself (§7) is a hardware property; no software lever
moves it. Net: batches are faster from caching + overlap, not from making the model generate
faster — that would need different serving hardware (single-card fit) or a smaller model.

Related: [[concepts/plan-serving-stack-2026-07]] (Wave 2 + serving stack) ·
[[concepts/plan-pipeline-speed-2026-07]] · [[concepts/plan-speed56-execution-2026-07]] ·
[[concepts/vram-budget]] · [[concepts/multimodal-fusion-2026-07]] (the omni/LM-Studio serving
constraints) · [[concepts/bugs-and-fixes]]
