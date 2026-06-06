---
title: "VRAM Budget and Model Orchestration"
type: concept
tags: [vram, gpu, memory, performance, models, orchestration, infrastructure, observability]
sources: 3
updated: 2026-06-05
---

> [!success] Cross-vendor VRAM observability shipped 2026-06-05
> `scripts/lib/vram_log.py` + `scripts/lib/model_registry.py` + `logtool vram` give per-stage VRAM trajectory across **both NVIDIA and AMD** cards on Windows, plus model-fit recommendations.
>
> - **NVIDIA**: `nvidia-smi` (total / used / free / util% / temp)
> - **AMD on Windows**: PowerShell `Get-Counter '\GPU Adapter Memory(*)\Dedicated Usage'` for used + registry `HardwareInformation.qwMemorySize` for total
> - **LM Studio**: `lms ps` + `lms ls` for loaded model IDs and on-disk sizes
> - **Per-stage snapshots**: hooked into `common.set_stage()`; writes `{TEMP_DIR}/vram_log.json` and a one-line `[VRAM] …` entry to `pipeline.log`
> - **Viewer**: `python scripts/logtool.py vram` shows current pool + last-run trajectory + per-model recommended context for both CUDA-single-card and Vulkan-pool fit modes
>
> Also: `python scripts/lib/model_registry.py recommend <model_id> <pool_mb>` computes the max context fitting weights + KV cache + 300 MB overhead + 500 MB safety margin on any hardware. Other users cloning the repo get the same prediction tooling for their adapters.

# VRAM Budget and Model Orchestration

The stream clipper uses three AI models across different pipeline stages. Only **one model occupies VRAM at a time**. The pipeline manages this via `unload_model()` in `clip-pipeline.sh`, which calls `POST /api/v1/models/unload` on [[entities/lm-studio]] at stage transitions. Whisper VRAM is managed separately (runs inside Docker, not through LM Studio).

---

## Per-model VRAM

Models are configured in `config/models.json` — the specific model ID and its VRAM footprint depend on what is loaded in LM Studio. Reference figures for common choices:

| Model | Weight VRAM | KV cache rate | Native max ctx |
|---|---|---|---|
| `qwen/qwen3.5-9b` (text or both slots), non-thinking | 6.5 GB (Q4) | ~130 KB/token | 256K |
| `qwen/qwen3.6-27b` dense | 17.5 GB (Q4) | ~130 KB/token | 256K |
| `qwen/qwen3.6-35b-a3b` MoE (~3B active) | 22.1 GB (Q4) | ~105 KB/token | 256K |
| `qwen/qwen3-vl-8b` | 6.2 GB (Q4) | ~115 KB/token | 256K |
| `qwen/qwen3-vl-30b` MoE | 19.6 GB (Q4) | ~110 KB/token | 256K |
| `google/gemma-4-12b` multimodal | 7.6 GB (Q4) | **~390 KB/token** (large head_dim) | 128K |
| `google/gemma-4-26b-a4b` MoE | 18.0 GB (Q4) | ~390 KB/token | 128K |
| `google/gemma-4-31b` dense | 19.9 GB (Q4) | ~390 KB/token | 128K |
| `openai/gpt-oss-20b` MXFP4 (~3B active MoE) | 12.1 GB | ~95 KB/token | 128K |
| `nvidia/nemotron-3-nano-4b` hybrid | 4.2 GB (Q4) | ~60 KB/token | 32K |
| [[entities/faster-whisper]] `large-v3-turbo` / `large-v3` | 3-4 / 6-7 GB | N/A (audio) | N/A |

KV cache math (used by `model_registry.recommend_context`):
`projected_total_mb = weights_mb + (ctx_tokens × kb_per_token / 1024) + 300 MB overhead + 500 MB safety`.
Run `python scripts/lib/model_registry.py recommend <model> <pool_mb>` for the live calculation.

**Key takeaway**: Gemma 4's KV cache is ~3× heavier per token than Qwen's due to its 256-dim head_dim. On the same 16 GB CUDA card, qwen3.5-9b fits ~65K context, but gemma-4-12b only fits ~16K. Pick the model first, then size context to fit.

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
