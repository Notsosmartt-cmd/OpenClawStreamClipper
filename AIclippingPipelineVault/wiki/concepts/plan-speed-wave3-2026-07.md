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

**Owner mandate (2026-07-14, added same day)**: **WhisperX must be the REAL default —
including multi-speaker diarization** — not just the preferred-on-paper backend that
every recent run silently fell back from ([[entities/faster-whisper]] /
[[entities/diarization]] warnings). This is Wave 0 below: it ships FIRST because it is
a correctness/quality mandate (better word timing + speaker labels) that also changes
the Stage-2 profile Wave A then optimizes. It is allowed to COST a little time before
Wave A recovers it.

**Owner amendment 2 (2026-07-14): hardware-adaptive defaults.** These optimizations
must be DEFAULT on the owner's dual-vendor rig but must not interfere with CPU-only,
NVIDIA-only, or AMD-only installs — the dashboard and pipeline stay usable everywhere.
See §2b (hardware profiles): a detection layer resolves a `gpu_profile`, and every
machine-specific behavior (the CUDA runtime dance above all) activates conditionally
under `auto`, with a visible dashboard status and a manual override.

**Owner amendment 3 (2026-07-14): NO mid-wave owner gates.** Owner: *"implement the
full plan, review and evaluate whatever you can on your side for metrics, and at the
end I will see the final outputted clips and go back for fine-tuning."* This
consciously overrides the default RED-rubric promotion flow FOR THIS PLAN: waves ship
default-on (conditioned on hardware profile), the agent self-evaluates each wave
(timings, smoke tests, mechanical clip-set diffs, Reference-Lab carding as a quality
proxy), and the single owner review happens at the END on the final output clips,
followed by a fine-tuning pass. Kill switches stay on everything regardless.

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
| W0.1 | pin pipeline spawn to the repo `.venv` interpreter | **GO (Wave 0, owner-mandated)** | ends interpreter roulette; makes WhisperX + diarization actually run |
| W0.2 | consolidate deps into `.venv` (scenedetect, easyocr, better-profanity) | GO (Wave 0) | one interpreter with the FULL dep set (Lab decompose detectors included) |
| W0.3 | WhisperX + alignment + diarization verification run | GO (Wave 0) | owner mandate: wav2vec2 word times + multi-speaker labels default-on |
| W0.4 | re-measure S2 on the WhisperX path | GO (Wave 0) | the 4.1 min/VOD-h S2 baseline was measured on the FALLBACK path |
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

### Wave 0 — the WhisperX mandate (owner-directed, ships FIRST)
Owner 2026-07-14: *"I want whisperx and default on pipeline with the multi-speaker
detection stuff."* Everything is already configured (speech.json `backend=whisperx`,
`alignment.enabled=true`, `diarization.enabled=true`, HF_TOKEN in .env, whisperx +
pyannote installed in `.venv`) — recent runs just never reached it because the
dashboard spawns the pipeline with `sys.executable` and a system-python dashboard has
no whisperx.
- **W0.1 — interpreter pin** (`dashboard/routes/pipeline_routes.py:120/190`, and
  `reference_routes.py:135` for consistency): resolve the repo venv python explicitly
  (`REPO/.venv/Scripts/python.exe`, fall back to `sys.executable` if absent) instead of
  trusting the dashboard's own chain. Kills the whole "which interpreter launched the
  dashboard" failure class.
- **W0.2 — dep consolidation**: `pip install scenedetect easyocr better-profanity`
  into `.venv` (they currently live only in the system python) so the pin doesn't
  DEGRADE Reference-Lab decompose. One interpreter, full dep set, forever.
- **W0.3 — verification run**: one VOD through Stage 2; log must show `WhisperX
  backend` (not the fallback line), alignment, and diarization; `transcript.json`
  segments carry non-None `speaker` fields; captions/SFX anchors consume the improved
  word times unchanged. This re-activates: wav2vec2 word timing (±30-60 ms vs
  ±0.2-0.5 s), Pass A/C banter boosts, Pass-B speaker annotation.
- **W0.4 — re-baseline S2**: WhisperX = VAD-batched ASR (usually faster than the
  fallback) + alignment pass + diarization (~25-30% of S2, CPU-bound). Expect S2
  ~12-15 min sequential on a fresh 3 h VOD until Wave A hides the CPU passes.
- **Gate**: none for the flip (owner already mandated it); W0.3 is a smoke test, and
  the first WhisperX run's clips deserve one normal owner glance since segmentation
  shifts slightly.

### Wave 2b — hardware profiles & conditional activation (owner amendment 2)

New module `scripts/lib/hw_profile.py` + dashboard surface. Detection (cached per
process, override-able):
- NVIDIA: `nvidia-smi` (count, VRAM) — same probe `scripts/lib/vram_log.py` uses.
- AMD: `Win32_VideoController` / vram_log's cross-vendor probe.
- CUDA-for-torch: whisper's own `cuda→cpu` fallback already handles this per-device.
- Resolved `gpu_profile`: `dual_vendor` | `nvidia_only` | `amd_only` | `cpu_only`,
  stored/overridable in `config/hardware.json` (`"gpu_profile": "auto"` default).

Activation matrix (what turns on where — everything else is universal):

| feature | dual_vendor (owner) | nvidia_only | amd_only | cpu_only |
|---|---|---|---|---|
| W0 interpreter pin + WhisperX + diarization | ✅ | ✅ | ✅ (align/diar on CPU) | ✅ (slower, works) |
| A1/A2 overlaps, A3 reload hygiene | ✅ | ✅ | ✅ | ✅ (A2 still helps: CPU encode) |
| B1 model split (passb=9B) | ✅ default via models.json | ✅ (same file — their choice) | ✅ | ✅ |
| **B2 CUDA runtime dance** (`CLIP_PASSB_RUNTIME=auto`) | **✅ auto-ACTIVE** (needs: both vendors present + `lms` CLI + a cuda runtime pack installed + passb model fits NVIDIA VRAM) | ⛔ auto-INERT (already native CUDA — a select would be a no-op) | ⛔ auto-INERT (no CUDA) | ⛔ auto-INERT |
| C vision diet | ✅ | ✅ | ✅ | ✅ |
| NVENC encode | ✅ (existing fallback to libx264 stays) | ✅ | ⛔ → libx264 (existing) | ⛔ → libx264 |

`CLIP_PASSB_RUNTIME`: `auto` (default — the conditions above decide) | `off` | `cuda`
(force). UI/UX: the dashboard **Hardware panel** shows the detected profile and a
plain-language line per conditional feature ("CUDA text-lane: ACTIVE — dual-GPU setup
detected" / "not needed — native CUDA" / "unavailable — no NVIDIA GPU"), plus the
override dropdown. Failure-soft everywhere: if any probe errors, resolve to the most
conservative profile and log it — a detection bug must never break a run.

### Wave A — structural overlaps (byte-safe, ship after Wave 0)
- **A1 — S2 overlap** (`scripts/pipeline/stages/stage2.py`): start the CPU audio-events
  scan (+ any other CPU audio analysis) in a thread as soon as `audio.wav` exists, join
  before S3 consumes events. Whisper (GPU) and the scan (CPU threads) don't contend.
  With Wave 0's diarization on, the overlap surface GROWS: the events scan and the
  pyannote diarization pass are both CPU-heavy (24 threads available) — run them
  concurrently with/after the GPU phases. Target S2 ≈ 9-11 m instead of Wave 0's
  ~12-15 m sequential. Validation: byte-identical events JSON + transcript vs serial run.
- **A2′ — master-slice captions (REPLACED the S6↔S7 overlap, 2026-07-14)**: during
  implementation, two blockers surfaced on the original A2 — Stage 7's caption step
  loaded Whisper (GPU claim → VRAM conflict with a still-loaded 35B) and the clip
  manifest depends on Stage 6's titles. The better move: Stage 7 now SLICES each
  clip's word-SRT from the Stage-2 master transcript (`clip_windows.json` manifest →
  `stage7_transcribe.py` master mode; wav2vec2-aligned since Wave 0) instead of
  re-transcribing every clip. Removes S7's whole Whisper load (−1.5-2.5 m),
  deterministic, captions match the text detection read, works on every hardware
  profile (CPU-only saves most). Legacy path kept: `CLIP_CAPTION_SOURCE=whisper`
  forces it; a missing slice output auto-falls-back. Self-tested (offsets, block
  fallback, window exclusion, fallback decision). The full S6∥S7 render overlap
  moved to **D6** (needs a design pass vs stage-6 window trimming; the VRAM blocker
  is now gone, so it's feasible later).
- **A3 — batch hygiene** (`run_pipeline.py` batch loop): `lms unload --all` + reload
  between VODs + a 5 s health probe; abort-with-message if LM Studio unresponsive.
  Protects the healthy 26–30 min/VOD-h rate (S4 decayed 14.4→24.9 min/VOD-h over the
  07-13 batch's final 6 h) and prevents the crash mode diagnosed 07-14.

### Wave B — the 9B text lane (the big rock; owner-gated)
Full detail: [[concepts/single-card-cuda-lane-2026-07]].
- **B1 (config-only)**: `models.json: "text_model_passb": "qwen/qwen3.5-9b"` → S4 runs
  on the 9B via the EXISTING Phase-5.1 swap. S4 47 → ~17–22 m.
  **Self-eval (amendment 3 — no mid-wave owner gate)**: run the same VOD unified-35B vs
  lane; mechanically diff the clip sets (count, categories, time-bucket coverage, score
  distributions, judge rankings via `logtool selection`) and card BOTH runs through the
  Reference Lab → `corpus_diff` as an objective quality proxy. Findings go in the final
  report; the owner judges the END clips.
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
  Self-eval: control-vs-diet on one run — title/hook fidelity via the existing
  `caption_judge` scores + top-10 judge-ranking overlap; owner sees the end clips.
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
| today (healthy, S2 = fallback path) | 12.5 | 3 | 47 | 20 | 6 | **~90 m** |
| + Wave 0 (WhisperX + diarization) | ~13-15 | 3 | 47 | 20 | 6 | **~91-93 m** (a quality BUY, recovered next rung) |
| + Wave A (overlaps) | ~9-11 | 3 | 47 | 20 | ~2 | **~78-81 m** |
| + B1 (9B Vulkan) | ~10 | 3 | ~19 | 20 | ~2 | **~52 m** |
| + B2 (CUDA) + B3 | ~10 | 1 | ~15 | 20 | ~2 | **~46 m** ← target's upper edge |
| + Wave C | ~10 | 1 | ~15 | ~11 | ~2 | **~37 m** ← inside target |
| + Wave D (if pursued) | ~10 | 1 | ~11–13 | ~11 | ~2 | **~33–35 m** |

Floor honesty: S2's whisper+alignment+diarization and S7's render are real work with
no remaining big lever (S7 already parallel; whisper already the fast distil model on
CUDA; diarization is the owner's explicit quality choice and worth its cost) — so
**~31–33 min is the practical floor** on this machine with the WhisperX mandate in,
and it requires most of Wave D. The robust landing zone for 0+A+B+C is **~37–46 min**,
squarely in the owner's target. W0.4's measured S2 number replaces the ~estimates
above before Wave B starts.

## 4. Validation protocol (amendment 3: agent self-eval per wave, owner review at END)
- Wave 0: smoke test — Stage-2 log shows `WhisperX backend` (NOT the fallback line) +
  alignment + diarization; `transcript.json` has non-None `speaker` fields; word-SRT
  consumed by captions unchanged; S2 wall time recorded (the new baseline).
- Wave A: byte-identical artifacts (events/transcript/clip files) vs a control run +
  `run_metrics.jsonl` stage rows.
- Wave B: ONE A/B pair on a known VOD (cached transcript isolates the text phase);
  mechanical clip-set diff + `logtool selection` rank churn + Reference-Lab carding of
  both runs → `corpus_diff` as the quality proxy. Result recorded, not gated.
- Wave C: control-vs-diet caption_judge fidelity scores + top-10 judge-ranking overlap.
- Hardware profiles: unit-test the matrix (mock probes for all 4 profiles → expected
  activations); on this box assert `dual_vendor` + CUDA-lane ACTIVE.
- Always: stage timings land in `run_metrics.jsonl` — verify each rung's predicted
  saving materialized before the next.
- **END: the owner deliverable** — a full fresh-VOD run with everything on → final
  clips for owner review + a consolidated metrics report (per-wave measured savings,
  self-eval findings, anything flagged for the fine-tuning pass).

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
