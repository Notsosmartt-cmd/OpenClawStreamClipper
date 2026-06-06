---
title: "VRAM Budget and Model Orchestration"
type: concept
tags: [vram, gpu, memory, performance, models, orchestration, infrastructure, observability]
sources: 3
updated: 2026-06-05
---

> [!success] Cross-vendor VRAM observability + deterministic context prediction (2026-06-05)
> Three modules + a dashboard feature give per-stage VRAM trajectory across **both NVIDIA and AMD** cards plus GGUF-exact context recommendations.
>
> - **NVIDIA**: `nvidia-smi` (total / used / free / util% / temp)
> - **AMD on Windows**: PowerShell `Get-Counter '\GPU Adapter Memory(*)\Dedicated Usage'` for used + registry `HardwareInformation.qwMemorySize` for total (verified: RTX 5060 Ti 16311 MB + RX 6700 XT 12272 MB → 28583 MB pool)
> - **Per-stage snapshots**: `vram_log.stage_snapshot()` hooked into `common.set_stage()`; writes `{TEMP_DIR}/vram_log.json` + a `[VRAM] …` line to `pipeline.log`
> - **Viewer**: `python scripts/logtool.py vram` — current pool + last-run trajectory + per-model recommended context (CUDA-only + pool modes, with `gguf`/`heuristic` source flag)
> - **Dashboard**: the Context Window card now shows a GPU-aware recommendation that updates whenever you change the text/vision model dropdown, with an "Apply" button. Calls `/api/models/context-recommendation`.
>
> **The KV-cache math is now deterministic** (`scripts/lib/gguf_meta.py`): it reads the exact `block_count`, `head_count_kv`, `key_length`, `value_length`, and `sliding_window_pattern` from each model's GGUF header instead of a per-architecture rate guess. This corrected large errors — see the KV-cache section below.

# VRAM Budget and Model Orchestration

The stream clipper uses three AI models across different pipeline stages. Only **one model occupies VRAM at a time**. The pipeline manages this via `unload_model()` in `clip-pipeline.sh`, which calls `POST /api/v1/models/unload` on [[entities/lm-studio]] at stage transitions. Whisper VRAM is managed separately (runs inside Docker, not through LM Studio).

---

## Per-model VRAM

Models are configured in `config/models.json` — the specific model ID and its VRAM footprint depend on what is loaded in LM Studio. Reference figures for common choices:

| Model | Weight VRAM | KV cache rate | Native max ctx |
|---|---|---|---|
| `qwen/qwen3.5-9b` (text or both slots), non-thinking | 6.5 GB (Q4) | **128 KB/tok** (32L × 4kv × 256) | 256K |
| `qwen/qwen3.6-27b` dense | 17.5 GB (Q4) | ~128 KB/tok | 256K |
| `qwen/qwen3.6-35b-a3b` MoE (~3B active) | 22.1 GB (Q4) | **80 KB/tok** (40L × 2kv × 256) | 256K |
| `qwen/qwen3-vl-8b` | 6.2 GB (Q4) | ~115 KB/tok | 256K |
| `qwen/qwen3-vl-30b` MoE | 19.6 GB (Q4) | ~110 KB/tok | 256K |
| `google/gemma-4-12b` multimodal | 7.6 GB (Q4) | **~36 KB/tok @ 32K** (SWA: 40/48 layers cap at 1024-tok window) | 256K |
| `google/gemma-4-26b-a4b` MoE | 18.0 GB (Q4) | ~36 KB/tok @ 32K (SWA) | 256K |
| `google/gemma-4-31b` dense | 19.9 GB (Q4) | ~36 KB/tok @ 32K (SWA) | 256K |
| `openai/gpt-oss-20b` MXFP4 (~3B active MoE) | 12.1 GB | **48 KB/tok** (24L × 8kv × 64) | 128K |
| `nvidia/nemotron-3-nano-4b` hybrid | 4.2 GB (Q4) | ~40 KB/tok (heuristic — GGUF not matched) | 32K |
| [[entities/faster-whisper]] `large-v3-turbo` / `large-v3` | 3-4 / 6-7 GB | N/A (audio) | N/A |

> [!note] Gemma's "KB/tok" is context-dependent
> Because Gemma's sliding-window layers cache a fixed 1024-token window, its effective KB/token DROPS as context grows: ~56 @ 16K, ~36 @ 32K, ~26 @ 65K, ~21 @ 131K. The Qwen/gpt-oss rates are flat (full attention on all layers). The recommendation tooling computes the exact piecewise value per context tier, so don't extrapolate a single rate for Gemma.

### KV-cache: deterministic from GGUF (corrected 2026-06-05)

The KV-cache rates above are **measured from each model's GGUF header**, not estimated. `scripts/lib/gguf_meta.py::kv_cache_bytes()` reads `block_count × head_count_kv × (key_length + value_length) × 2 bytes` per token, and for sliding-window models (Gemma) it computes the SWA-aware cache exactly (most layers cap at the window, not the full context).

**Why this matters — the old heuristic was wildly wrong for two architectures:**

| Model @ 32K | Old flat-rate estimate | **GGUF-exact** | Error |
|---|---|---|---|
| qwen3.5-9b | 4160 MB | 4096 MB | +2% (lucky) |
| qwen3.6-35b-a3b | 3360 MB | 2560 MB | +31% |
| gpt-oss-20b | 3040 MB | 1536 MB | +98% |
| **gemma-4-12b** | **12792 MB** | **1152 MB** | **+1010%** ⚠️ |

Gemma 4's 11× error comes from its **sliding-window attention**: 40 of 48 layers only cache a 1024-token window, so its KV cache barely grows with context. The flat-rate heuristic treated all layers as full-attention. **Consequence**: the old tool said gemma-4-12b could only fit 16K context on a 16 GB card; the GGUF-exact math correctly says it fits the full **256K native** with room to spare.

Projection formula: `total_mb = weights_mb + kv_cache_mb(ctx) + 300 MB overhead + 500 MB safety`.
Live calculation: `python scripts/lib/model_registry.py recommend <model> <pool_mb>` or `predict <model> <ctx>`.
Per-model GGUF dump: `python scripts/lib/gguf_meta.py <path_to.gguf> --context 32768`.

**Model combos**: `model_registry.recommend_context_combo(text, vision, pool)` returns the SHARED context for a split config — the more-constrained of the two models, since `context_length` is a single config value but each model loads separately. When text==vision (consolidation), it's just that model.

Whisper never co-resides with the LLM (the pipeline unloads LM Studio before Stage 2), so turbo's smaller footprint doesn't add LLM headroom — but it loads faster and transcribes ~2.5x quicker. See [[entities/faster-whisper]].

Peak VRAM usage: determined by the configured LLM. **Two-GPU reality:** the box has the **16 GB RTX 5060 Ti + a 12 GB AMD RX 6700 XT**, and LM Studio's **Vulkan** backend pools them → **≈28 GB**. So the big models (27B/26B-A4B/31B/35B, 17.5–22.1 GB) fit *in VRAM* across both cards — they do **not** spill to CPU (an earlier note here said they did; corrected). But "fits ≠ fast": models load **one at a time** (the pipeline swaps per stage, so each gets the full pooled budget), and a model split across both cards on the Vulkan runtime is typically **slower per token** than one that fits the 5060 Ti alone on **CUDA** (cross-vendor backend + inter-GPU PCIe transfer; bigger model also = slower reload per swap). For max speed: **CUDA / NVIDIA-only with a ≤16 GB model**; for capacity (a Q4 Qwen3-VL-30B, or a big MoE on the Judge): **Vulkan / both**. The current `qwen3.5-9b` + `gemma-4-12b` (6.5 + 7.6 GB) both fit the NVIDIA card alone. See [[concepts/model-split]] §Active config for the per-role + thinking guidance.

---

## Stage-by-stage VRAM state

> [!note] Two separate VRAM pools
> LLM models run in **LM Studio on Windows** (native GPU VRAM).
> Whisper runs **inside the Docker container** (NVIDIA CUDA via Container Toolkit).
> The pipeline calls `POST /api/v1/models/unload` before loading Whisper to prevent both occupying VRAM simultaneously.

### Unified model (single multimodal — e.g. Gemma-4)

When `text_model_passb` and `vision_model_stage6` are both `null` in `config/models.json`, the same model is used for all LLM stages (Pass B, Stage 6, Pass B grounding). No model swap needed between Stages 3–6.

```
Start           : No LLM loaded in LM Studio (JIT load on first request)
                  ↓
Stage 2 prep    : unload_model() → POST /api/v1/models/unload (clear any stale model)
Stage 2         : Whisper (Docker) loads → ~6-7GB used → exits after transcription
                  ↓
Stage 3–4       : LLM loads via LM Studio → stays loaded (same model for text + vision)
Stage 5         : No model needed (FFmpeg only)
Stage 6         : Same LLM still loaded → vision enrichment runs without swap
                  ↓
Stage 7 prep    : unload_model() → POST /api/v1/models/unload
Stage 7         : Whisper (Docker) loads → ~6-7GB used → exits after captions
Stage 7 FFmpeg  : 0GB → render clips
                  ↓
Stage 8         : 0GB
```

### Split model (separate text + vision models)

When `text_model_passb` or `vision_model_stage6` is set to a different model ID in `config/models.json`, the pipeline unloads the text model before Stage 6 and loads the vision model.

```
Stage 3–4   : text model loaded
Stage 6 prep: unload text model → load vision model
Stage 6     : vision model used
Stage 7 prep: unload vision model
```

Discord agent loads its own model on demand outside the pipeline stages — no VRAM conflict during pipeline execution.

---

## Installed model quantizations (read from GGUF `general.file_type`, 2026-06-06)

| Model | Quant | Weights | Notes |
|---|---|---|---|
| `qwen/qwen3.6-35b-a3b` | **Q4_K_M** | 22.1 GB | current text + vision |
| `qwen/qwen3.6-27b` | Q4_K_M | 17.5 GB | |
| `qwen/qwen3.5-9b` | Q4_K_M | 6.5 GB | |
| `qwen/qwen3-vl-30b` | Q4_K_M | 19.6 GB | |
| `qwen/qwen3-vl-8b` | Q4_K_M | 6.2 GB | |
| `google/gemma-4-12b` / `26b-a4b` / `31b` | Q4_K_M | 7.6 / 18.0 / 19.9 GB | |
| `openai/gpt-oss-20b` | **MXFP4** | 12.1 GB | native 4-bit MoE format |
| `nvidia/nemotron-3-nano-4b` | **Q8_0** | 4.2 GB | 8-bit |

All the main pipeline candidates are Q4_K_M. gpt-oss ships in its native MXFP4 (don't re-quant — degrades sharply). Read live with `python scripts/lib/gguf_meta.py <path.gguf>` (it surfaces `general.file_type`) or infer from the filename quant suffix.

## Per-stage `max_tokens` (output budget, independent of context)

Each pipeline call to LM Studio has an output-token budget. These are **output limits, not context limits** — they cap how many tokens the LLM generates, NOT how much it can read. Increasing `context_length` doesn't require touching these. But if you reduce `context_length` to fit tight VRAM, you need to confirm `prompt_tokens + max_tokens ≤ context_length` at the chunk's call site.

| Stage / call site | max_tokens | Source |
|---|---|---|
| Stage 3 segment classify | 6000 | `stage3_segments.py:109` |
| Stage 4 Pass B main call (per chunk) | 8000 | `stage4_moments.py:659` (default `call_llm`) |
| Stage 4 Pass B summary call (per chunk) | 4000 | `stage4_moments.py:1610` |
| Stage 4 Tier-3 A1 global skeleton | 6000 | `stage4_moments.py:1774` |
| Stage 4 Pass C dedup-merge LLM call | 3000 | `stage4_moments.py:1473` (call_llm default) |
| Stage 4 Pass D rubric judge | 1000 | `stage4_rubric.py:217` |
| Stage 5.5 Vision Judge (pairwise) | 1200 | `stage5_5_judge.py:46` |
| Stage 6 Vision Enrichment | 8000 | `stage6_vision.py:487` |
| `lmstudio.py` (used by grounding cascade tier 2 judge) | 800 | `lmstudio.py:31` (default) |
| `grounding.py` LLM judge | 400 | `grounding.py:349` |
| `callbacks.py` callback LLM judge | 400 | `callbacks.py:216` |

**Rules of thumb**:

- The 8000-token Pass B and Stage 6 budgets are sized for the Qwen3.5-35B-A3B thinking budget; non-thinking models finish in ~200-500 tokens and just leave the rest unused. **Do not lower below 4000** without verifying — reasoning models can spike unexpectedly even when thinking-off is set ([[concepts/bugs-and-fixes]] BUG 57 history).
- Pass B chunk is up to ~6000 prompt tokens (transcript + catalog + prior context). With `max_tokens=8000` output, the worst case is ~14000 tokens used. So `context_length` must be ≥ 16K for Pass B to not get truncated. Below 16K context, **Pass B will fail silently** on long chunks (the prompt gets clipped to the context window).
- Stage 6 vision call: ~3000 prompt tokens + 6 frames + 8000 output budget → context_length ≥ 16K. Same constraint.
- Stage 5.5 Vision Judge: ~1500 prompt + 8 frames + 1200 output → context_length ≥ 8K is fine. This stage has the smallest context demand.

## Portable hardware → recommended config

For another user cloning the repo with different hardware, here are sensible defaults that keep everything in VRAM (no CPU spill). Verify on your box with `python scripts/logtool.py vram` after pulling.

| Hardware | text_model | vision_model | context_length | Why |
|---|---|---|---|---|
| **16 GB single CUDA** (RTX 4060 Ti / 5060 Ti) | `qwen3.5-9b` (or unified for both) | `qwen3.5-9b` (consolidation) or `gemma-4-12b` | 16K-32K | Both fit single card; 32K ctx leaves ~4 GB headroom for KV |
| **16 GB single CUDA, Qwen3-VL** | `qwen3.5-9b` | `qwen3-vl-8b` (download) | 32K | Two CUDA-fit models with one swap per VOD |
| **24 GB single CUDA** (RTX 4090 / 3090) | `gpt-oss-20b` @ Reasoning Low | `gemma-4-12b` or `qwen3-vl-8b` | 32K | Runtime-tunable reasoning + headroom |
| **28 GB Vulkan pool** (NVIDIA 16 GB + AMD 12 GB, like the dev box) | `qwen3.6-35b-a3b` (both slots) | (same) | 32K | Single-model consolidation; MoE 3B active stays fast on pool |
| **12 GB single** (RTX 3060) | `qwen3.5-9b` (or `nemotron-3-nano-4b`) | (same) | 16K | Tight; cut chunk size if Pass B truncates |
| **8 GB single** | `nemotron-3-nano-4b` | (same) | 8K | Bare-minimum config; quality suffers but pipeline runs |

For other configs, run `python scripts/lib/model_registry.py recommend <model_id> <pool_mb>` — it computes the largest safe context for ANY model + pool combination including KV-cache rate per architecture (Gemma's is 3× heavier than Qwen's per token).

## Minimum GPU requirements

| Setup | Minimum VRAM | Notes |
|---|---|---|
| Full pipeline, CUDA (NVIDIA) | 12 GB | qwen3.5:9b needs ~11.2GB; 12GB is tight |
| Full pipeline, comfortable | 16 GB | ~5GB headroom for VRAM fluctuation |
| Full pipeline, Vulkan (AMD) | 12 GB | Same model sizes; Whisper runs on CPU |
| CPU-only | 16 GB RAM | All inference on system RAM |

The tested hardware is an RTX 5060 Ti (16GB). An RTX 3090 (24GB) or RTX 4090 would have significant headroom.

**8GB VRAM is borderline**: works with qwen2.5:7b and qwen3-vl but may OOM with qwen3.5:9b at 32K context.

**Vulkan note**: Whisper does not have a Vulkan/CTranslate2 backend. In Vulkan mode, Whisper always runs on CPU (int8). This is enforced by the `entrypoint.sh` hardware config reader — `CLIP_WHISPER_DEVICE` is forced to `cpu` when `gpu_backend` is `vulkan` or `cpu`.

---

## Whisper vs LM Studio memory separation

[[entities/faster-whisper]] runs via Python directly inside the Docker container (not through LM Studio). Its VRAM is allocated by NVIDIA Container Toolkit independently of LM Studio's model pool. The pipeline calls LM Studio's unload API before loading Whisper — otherwise both could be in VRAM simultaneously and OOM.

---

## Related
- [[entities/lm-studio]] — the LLM server; GPU assignment via its GUI; `unload` endpoint
- [[entities/faster-whisper]] — uses GPU VRAM separately from LM Studio (runs in Docker)
- [[entities/qwen35]] — highest VRAM consumer
- [[concepts/deployment]] — hardware requirements table
