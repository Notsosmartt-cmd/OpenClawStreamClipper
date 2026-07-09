---
title: "Serving-Stack Speed Plan — speculative decoding + prefill tuning (2026-07)"
type: concept
status: planned
tags: [performance, speed, lm-studio, serving, speculative-decoding, prefill, plan, stage-4]
sources: 0
updated: 2026-07-09
---

# Serving-Stack Speed Plan (2026-07)

**Why this page exists.** [[concepts/pipeline-speed-findings-2026-07]] §7 concluded "software
levers on the LLM portion are exhausted." That was true of **pipeline-code** levers (parallelism,
caching, threading — all measured). It was NOT true of the **LM Studio serving configuration**
under the 35B, which was never tuned. This plan covers the two quality-neutral serving levers,
grounded in controllability research done live on this machine 2026-07-09. Owner explicitly
excluded the low-quant CUDA-only-fit option (quality tradeoff) — not in this plan.

**The target**: Stage 4's ~48 sequential LLM calls (S4 median 1156 s), running at ~16% GPU util
because each forward pass over the cross-vendor Vulkan split (RTX 5060 Ti + RX 6700 XT) pays a
fixed bandwidth/coordination cost per generated token.

---

## 1. Controllability research results (2026-07-09, verified live)

The prior lesson (thinking/reasoning toggle was only reliable via UI) made "flags or UI?" the
first question. Answer per lever:

| Lever | Control surface | Status on this machine |
|---|---|---|
| Speculative decoding (draft) | **CLI**: `lms load --speculative-draft-simple --speculative-draft-model <m>` + `--speculative-draft-max-tokens/-min-tokens/-min-continue-probability` | Flags confirmed (CLI commit 6041ae0). Fully scriptable, no UI needed |
| Speculative decoding (MTP) | **CLI**: `lms load --speculative-draft-mtp` | Flag works but **current GGUF has no MTP head** (see below) |
| Flash attention | **Per-model config file** `llm.load.llama.flashAttention` | **ALREADY `true`** for qwen3.6-35b-a3b — nothing to gain |
| Context length / offload / parallel / TTL | CLI (`-c`, `--gpu`, `--parallel`, `--ttl`) + same config file | ctx 16384, offload 1.0 set |
| Prefill batch size (`evalBatchSize`) | GUI advanced load settings; **no CLI flag**; expected file key `llm.load.llama.evalBatchSize` (never set here — key name must be confirmed by GUI-set-once + file diff) | At llama.cpp default; tunable |
| GPU split strategy | **UI-only**, and Vulkan dual-GPU offers **only "split evenly"** | **NOT controllable → no-go** |
| Row split / per-card tensor ratio | Not exposed anywhere in LM Studio (UI, CLI, or config file) | **no-go** (llama.cpp-direct only = out of stack) |

**Key discovery — the per-model config file.** GUI load toggles persist to
`C:\Users\user\.cache\lm-studio\.internal\user-concrete-model-default-config\qwen\qwen3.6-35b-a3b.json`
(LM Studio home is `C:\Users\user\.cache\lm-studio`, pointer in `~\.lmstudio-home-pointer`;
model GGUFs on `G:\lm-studio`). This file holds `flashAttention: true`, `contextLength: 16384`,
**and `enableThinking: false`** — i.e. the historical "thinking is only controllable via UI"
mystery is solved: the UI writes this JSON, and any load (CLI or JIT) reads it. Editing the file
programmatically is equivalent to the UI toggle.

**MTP live test (definitive).**
- LM Studio's own GGUF metadata cache: `supportsMtp=false`, `nextnPredictLayers=null` for
  `Qwen3.6-35B-A3B-Q4_K_M.gguf`.
- Live load test: `lms load qwen/qwen3.6-35b-a3b --speculative-draft-mtp -y` →
  `Error: MTP speculative decoding requires a GGUF model with a bundled supported MTP head.`
  Fails fast at 0% — clean, no state change.

**Draft-vocab compatibility (from the metadata cache).** Draft-simple requires matching
speculation vocab. qwen3.6-35b-a3b vocab = **248320** = qwen3.5-9b's → the qwen3.5/3.6 family
is draft-compatible. Incompatible: qwen3-8b (151936), gemma (262144), nemotron (131072),
gpt-oss (201088). The only compatible draft on disk (9B) is **backwards** — the target is a
MoE with ~3B *active* params, so a 9B dense draft costs more compute than the model it drafts
for, and 6.5 GB extra doesn't fit the pool anyway.

**Ecosystem check (web, 2026-07-09).** The Qwen3.5 family ships small models (**0.8B / 2B /
4B**) — proper draft sizes, same vocab family. llama.cpp merged MTP speculative decoding
2026-05-16 (installed Vulkan runtime 2.23.1 postdates it), and
[unsloth publishes MTP-bundled repacks](https://huggingface.co/unsloth/Qwen3.6-35B-A3B-MTP-GGUF)
of the exact target model, including **UD-Q4_K_M at 22.7 GB** (fits the ~28 GB pool;
current Q4_K_M is 22.07 GB). `lms get` accepts full HF URLs.

---

## 2. Proposal S — speculative decoding (the big lever)

**Mechanism.** A cheap drafter proposes K tokens; the 35B verifies all K in ONE forward pass.
Accepted tokens cost one fixed cross-GPU pass instead of K. The accept rule is anchored to the
target model's own probabilities (greedy: exact argmax match; sampling: rejection scheme that
preserves the target distribution) — **the drafter can change speed, never what gets said**.
This is the opposite of the §3 landmine in [[concepts/pipeline-speed-findings-2026-07]]: no
co-batching of independent requests, one request at a time, serial pipeline unchanged.

**Why it fits this rig specifically:** the 16%-util floor means each pass is fixed-cost-bound —
exactly what K-tokens-per-pass amortizes. And Pass-B/judge outputs are structured JSON
(predictable text) → high draft acceptance. Typical published gains 1.5–2.5× on decode.

**Known unknown:** how much of each Stage-4 call is *decode* vs *prefill* (prompts are
thousands of tokens; spec decode does nothing for prefill). The bench measures both — if
prefill dominates, Proposal P matters more than S.

### Lane S1 — draft-simple with a small qwen3.5 (RECOMMENDED — target weights untouched)
The current production GGUF stays byte-identical; only a separate small drafter loads alongside.
Zero change to target weights = smallest possible quality surface.

- **Draft**: `qwen/qwen3.5-2b` (first choice; ~1.2–1.5 GB at Q4 — fits pool headroom of ~6 GB)
  or `qwen3.5-0.8b` (if 2B drafting overhead disappoints).
- **Load**: `lms load qwen/qwen3.6-35b-a3b --speculative-draft-simple --speculative-draft-model qwen/qwen3.5-2b -y`
- **Tuning knobs**: `--speculative-draft-max-tokens` (start default, try 4–8),
  `--speculative-draft-min-continue-probability`.

### Lane S2 — MTP repack (fallback, only if S1 gain < ~1.2× or fails)
Re-download the target as `unsloth/Qwen3.6-35B-A3B-MTP-GGUF` **UD-Q4_K_M** (22.7 GB) → MTP
head bundled → `--speculative-draft-mtp` works, no separate draft, near-zero VRAM overhead.
> [!warning] S2 changes the target weights
> UD-Q4_K_M (Unsloth Dynamic) ≠ current lmstudio-community Q4_K_M bit-for-bit — different quant
> recipe. Outputs WILL differ. This is a model change and gets the full model-change gate:
> A/B-variance yardstick + full-VOD owner review. That's why S1 (weights untouched) goes first.

---

## 3. Proposal P — prefill batch tuning (the complement)

`evalBatchSize` (llama.cpp `n_batch`) controls how many PROMPT tokens process per pass during
ingestion. Stage-4 prompts are large (chunk transcript + prior summaries); a bigger batch
saturates the split better during prefill — the phase spec decode can't touch. Expected gain
modest (1.1–1.3× on prefill share) but it's a one-setting A/B.

- **Control**: no CLI flag → set in GUI advanced load settings once (owner, 30 s), diff the
  per-model JSON to confirm the key name, then scriptable via file edit thereafter.
- **Test ladder**: default → 1024 → 2048 (watch VRAM: bigger batch = bigger compute buffers).

---

## 4. Validation protocol (lessons from the #5/#6 campaign applied)

**Bench harness** (new, `scripts/research/bench_serving.py`): replay ~5 representative prompts
from the committed golden manifest (`learning/passb_baseline/2xRaKai_temp0_workers1.json`)
**serially** via the OpenAI API; record per-call wall, prefill time, decode tok/s (LM Studio
`stats` block / server logs), and draft acceptance rate. Compare configs pairwise, same prompts.

**Quality gates:**
1. **S1 diagnostic**: temp-0 replay with/without draft — expect near-identical text (greedy
   accept = argmax match). Not a hard byte-gate (batch-verify FP reorder can flip rare
   borderline argmax — same physics as findings §3), so treat as a diagnostic, not pass/fail.
2. **The real gate = A/B variance yardstick** (findings §3-reframe): serial-vs-serial baseline
   is 5/10 clip overlap at temp 0.3. A full-VOD run with the draft enabled must land within
   that band (≥5/10 overlap vs a no-draft run of the same VOD) — i.e. within existing noise.
3. **Full-run + owner spot-check** before any default flips (owner rubric:
   **default-off = RED**; a lane only counts as integrated when it's the standard load config
   for every run).

**Success criteria to go GREEN**: ≥1.3× on the Pass-B bench decode AND ≥15% full-run
wall-clock reduction AND no VRAM spill (`lms ps` + `nvidia-smi` during run) AND gate 2 passes
AND owner spot-check OK.

**Ops bounds**: every bench/run bounded per the standing rule; watchdog `--timeout ≥5400`,
`--stall ≥900` (findings §6); LM Studio app stays up throughout (loads/unloads via `lms` only).

---

## 5. Iterations

| # | Step | Gate (DoD) |
|---|---|---|
| I-S1.0 | Download draft: `lms get qwen/qwen3.5-2b` (~1.5 GB) | model on disk; metadata cache shows vocab 248320 |
| I-S1.1 | Load pair: 35B + `--speculative-draft-simple --speculative-draft-model qwen/qwen3.5-2b`; verify VRAM fit | load OK; `lms ps` shows both; no spill |
| I-S1.2 | Build + run `bench_serving.py`: baseline (no draft) vs draft, 5 golden prompts × 2 reps | decode tok/s ratio measured; acceptance rate logged; prefill/decode split of a Stage-4 call known |
| I-S1.3 | Tune draft-max-tokens (4/8) + min-continue-prob if ratio < 1.5× | best config picked on data |
| I-S1.4 | Full 2xRaKai run with draft (bounded) vs latest no-draft run: wall time + variance-yardstick overlap | ≥15% faster; overlap ≥5/10; owner spot-check |
| I-S1.5 | If GREEN: make the draft flags the standard load (pre-run `lms load` step or documented owner load); update wiki scorecard | default-on = integrated; else archive lane (RED) with numbers |
| I-P.0 | Owner sets evalBatchSize in GUI once → diff per-model JSON → confirm key | key name confirmed programmatic |
| I-P.1 | Bench prefill at default/1024/2048 (same harness, prefill timing) | best value or "no effect" recorded |
| I-S2.* | ONLY if S1 < 1.2×: download UD-Q4_K_M MTP repack, repeat S1.1–S1.5 with the model-change gate (full owner review) | same gates + explicit weights-change signoff |

**Sequencing note**: S1 first (no weights change), P second (independent, cheap), S2 only as
fallback. Nothing here touches pipeline code — rollback for every step is
`lms unload --all` + plain `lms load qwen/qwen3.6-35b-a3b` (per-model JSON edits reversible).

---

## 6a. Second sweep — remaining quality-neutral candidates (researched 2026-07-09)

Owner asked for any further non-quality-degrading paths before execution. Probed live:

**Closed by probes (already optimal — don't re-investigate):**
- **Disk**: G: = NVMe Samsung 990 EVO Plus (C: = NVMe SN770) → model load + VOD I/O fine.
- **ReBAR**: ON (BAR1 = full 16384 MiB on the 5060 Ti).
- **PCIe width**: 5060 Ti shows x8 — that is the card's native electrical max (RTX 5060 Ti is
  a PCIe 5.0 ×8 design), not a slot misconfig. Gen reads 1 at idle (normal power saving);
  one under-load `nvidia-smi -q` check during the next bench confirms it ramps to Gen 5.
  AMD 6700 XT link unverified (chipset-slot risk) — optional one-off HWiNFO check.
- **Server logs carry no prefill-reuse stats** — prefix-cache behavior must be measured via
  repeated-prefix TTFT (folded into `bench_serving.py`).

**New candidates (ranked, all measure-first):**
| # | Candidate | Mechanism | Win | Risk class |
|---|---|---|---|---|
| C1 | **Cross-VOD overlap in batch runs** | run VOD i+1's CPU stages (scan/whisper/S3) during VOD i's LLM stages (4–7); orchestrator change in `run_pipeline.py --all` | ~7–10 min × (N−1) per fresh batch | byte-identical per VOD (no LLM concurrency — §3 landmine not triggered); caveat: whisper-CUDA VRAM window vs resident 35B (~1.5 GB free) → serialize just the whisper span with a lock |
| C2 | **KV/prefix-cache exploitation (S4 + judge)** | today the ~1–2k-token static catalog sits AFTER variable content (`seg_type` in sentence 1, prior-context above catalog) → near-zero prefix reuse. (a) measure reuse via TTFT repeat; (b) static-first prompt reorder (+ optional `--parallel 2` LCP slot routing to survive moment/card alternation) | cuts prefill share of ~48 calls | (a) free/diagnostic; (b) reorder changes token order → outputs change → **variance-yardstick-gated** (same class as card-parallel, within-noise defensible) |
| C3 | **Model-residency pin (TTL)** | ensure 35B never TTL-evicts mid-run/between runs; `--ttl 0` in the pre-run load step this plan already adds; verify via call-1-vs-call-2 timing in bench | avoids ~30–60 s JIT reloads | zero |
| C4 | **Stage-3 segment cache** | cache segments keyed by transcript-hash + detection-config-hash (mirrors #1 audio-events cache) | ~165 s on re-runs | byte-identical on hit |
| C5 | **GPU HPSS for fresh scans** | torch/CUDA HPSS for music_dominance; `vector_equiv.py` zero-flip harness already exists | ~1–2 min fresh-only | needs zero-flip pass (FP differs from librosa) |
| C6 | **JSON-retry audit** | measure Stage-4/6 parse-retry rate from diagnostics; if >0, grammar-constrained decoding becomes a candidate | unknown until measured | constrained decoding alters token distribution → only pursued if retries are material |

**Excluded on the quality rubric** (decided): KV-cache quantization (approximation, not
distribution-preserving), dropping rationale/why output fields (reasoning-by-writing effect),
fewer/smaller judge images, faster NVENC presets, model swaps/distils.

Priority stays: **S1 spec decode first** (biggest expected win), C3 rides along free, C2(a)
measured by the same bench, then C1 (batch-only), C4 (trivial), C2(b)/C5/C6 opportunistic.

## 6. No-go lanes (decided, don't re-litigate)

- **GPU split-mode tuning** — Vulkan dual-GPU UI offers only "split evenly"; no CLI flag, no
  config key on disk. Row-split / per-card ratio not exposed at all. Would require running
  llama.cpp directly (second serving stack) → rejected.
- **Flash attention** — already `true` for the 35B; no action available.
- **qwen3.5-9b as draft** — vocab-compatible but compute-backwards vs a ~3B-active MoE target
  and doesn't fit VRAM alongside it.
- **MTP on the current GGUF** — no bundled head; live-tested error. Only the S2 repack path
  can use MTP.
- **Low-quant CUDA-only single-card fit** — owner-excluded (quality tradeoff).

Related: [[concepts/pipeline-speed-findings-2026-07]] (§7 amended, §9 research facts) ·
[[concepts/plan-pipeline-speed-2026-07]] (the shipped pipeline-code campaign) ·
[[concepts/vram-budget]] · [[entities/qwen35]]
