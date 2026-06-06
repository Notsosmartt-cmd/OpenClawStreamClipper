---
title: "VRAM & Context-Fit Tooling"
type: concept
tags: [vram, gpu, context, kv-cache, gguf, observability, model-registry, dashboard, logtool, infrastructure, hub]
sources: 1
updated: 2026-06-06
---

# VRAM & Context-Fit Tooling

The subsystem that measures GPU/VRAM state across **any** vendor setup and recommends a `context_length` that fits the user's hardware while matching what the pipeline actually uses. Built across 2026-06-05 / 06. Companion to [[concepts/vram-budget]] (which holds the budget math + per-model tables); this page documents the **code, commands, and design decisions**.

> [!note] Guiding principle — engine-agnostic, workload-driven (2026-06-06)
> The recommendation does **not** care which inference engine (CUDA, Vulkan, ROCm, CPU) or GPU layout the user runs. It reads the **live VRAM pool** (whatever the backend reports) and recommends the context the **pipeline actually needs** (~32K — see "Workload-aware" below). It never tries to force or prefer a backend. Whatever engine + GPU/CPU split the user has chosen in LM Studio is taken as given; the tooling only answers "what context fits, and what does this pipeline use."

---

## The four pieces

| Module / surface | Role |
|---|---|
| `scripts/lib/vram_log.py` | Cross-vendor VRAM snapshot (NVIDIA + AMD + Intel) + per-stage trajectory logging |
| `scripts/lib/gguf_meta.py` | Reads exact KV-cache hyperparameters from a model's GGUF header |
| `scripts/lib/model_registry.py` | Maps `lms ls` models → GGUF files; computes deterministic VRAM/context predictions + recommendations |
| `logtool vram` + `/api/models/context-recommendation` | CLI + dashboard surfaces |

---

## 1. `vram_log.py` — cross-vendor snapshot

Probes every GPU adapter and the loaded-model list, returns a structured snapshot. **Engine-agnostic**: it reports whatever VRAM the host exposes, no matter the backend.

- **NVIDIA**: `nvidia-smi` CSV (total / used / free / util% / temp). Richest data when present.
- **AMD / Intel / anything on Windows**: PowerShell. Used VRAM from `Get-Counter '\GPU Adapter Memory(*)\Dedicated Usage'`; **total** VRAM from the registry `HardwareInformation.qwMemorySize` via the indirect `Enum\<InstanceId>.Driver → Control\Class\<Driver>` path (the `Win32_VideoController.AdapterRAM` field caps at 4 GB so it's useless for modern cards). Counter samples are paired to adapters by **descending-size sort** (the LUID↔PCI mapping isn't directly exposed; sort-pairing is reliable when adapters have distinct VRAM sizes, e.g. 16 GB NVIDIA + 12 GB AMD).
- **Loaded models**: `lms ps`.
- Verified on the dev box: RTX 5060 Ti 16311 MB + RX 6700 XT 12272 MB → **28583 MB pool**, single ~3 s probe.

**Pipeline hook**: `scripts/pipeline/common.py::set_stage()` calls `vram_log.stage_snapshot()` at every stage transition → a `[VRAM] …` line in `pipeline.log` + an entry in `{TEMP_DIR}/vram_log.json` (per-stage trajectory). Failure-soft: missing `nvidia-smi`/PowerShell never breaks the run.

Standalone: `python scripts/lib/vram_log.py --snapshot [--json]`.

---

## 2. `gguf_meta.py` — exact KV-cache from the model file

Minimal GGUF header parser (reads only the metadata KV block, ~5-20 ms even on a 22 GB file). Extracts `block_count`, `head_count_kv`, `key_length`, `value_length`, `sliding_window_pattern`, `key_length_swa`, `context_length`. `kv_cache_bytes(meta, ctx)` computes the exact cache size, handling three cases:

1. **Simple GQA** (Qwen, gpt-oss): `layers × kv_heads × (key_len + val_len) × 2 bytes × ctx`.
2. **Sliding-window attention** (Gemma): per-layer `head_count_kv` array + `sliding_window_pattern` — SWA layers cache only the window (1024 tokens), full-attention layers cache the whole context. This makes Gemma's KV cache MUCH smaller at large contexts.
3. **Fallback**: missing fields → `None`, caller uses the heuristic rate table.

> [!warning] Why this replaced the heuristic rate table (the honesty correction)
> The first version of `model_registry` (commit `6b2fec7`) used per-architecture KV rate *estimates*. They were formula-derived from representative params, NOT measured — and a code comment claiming they were "verified against LM Studio" was an overstatement. Reading the real GGUF metadata exposed large errors at 32K context:
> | Model | Old estimate | GGUF-exact | Error |
> |---|---|---|---|
> | qwen3.5-9b | 4160 MB | 4096 MB | +2% |
> | qwen3.6-35b-a3b | 3360 MB | 2560 MB | +31% |
> | gpt-oss-20b | 3040 MB | 1536 MB | +98% |
> | **gemma-4-12b** | **12792 MB** | **1152 MB** | **+1010% (11×)** |
> The Gemma 11× error is the SWA effect (40 of 48 layers cap at the 1024-token window). The heuristic said gemma-4-12b fit only 16K on a 16 GB card; the exact math says it fits the full 256K native.

Standalone: `python scripts/lib/gguf_meta.py <path.gguf> --context 32768`.

---

## 3. `model_registry.py` — predictions + recommendations

- **Portable models-root discovery**: `CLIP_LMSTUDIO_MODELS_DIR` env → `~/.cache/lm-studio/models` / `~/.lmstudio/models` → drive-root scan for `\lm-studio` (found `G:\lm-studio` on the dev box — the user relocated it off the default). Validated by presence of `.gguf` files.
- **ID → GGUF path**: normalized substring match (`qwen/qwen3.6-35b-a3b` → `…/Qwen3.6-35B-A3B-GGUF/…Q4_K_M.gguf`), skipping mmproj + later shards. Cached.
- `predict_vram(id, ctx)` → `{weights_mb, kv_cache_mb, total_mb, kv_source}` (`kv_source` = `gguf` exact or `heuristic` fallback).
- `recommend_context(id, pool_mb, cuda_card_mb=0)` → workload-aware recommendation (below).
- `recommend_context_combo(text, vision, pool, cuda_card_mb)` → shared context for split configs = the more-constrained model (since `context_length` is one config value but the pipeline swaps models one at a time). Consolidation (text==vision) = that one model.

### Workload-aware recommendation (the key design decision, 2026-06-06)

`recommend_context` returns the **workload-optimal** context, NOT the maximum that fits. Constants: `WORKLOAD_FLOOR_CONTEXT = 16384`, `WORKLOAD_COMFORT_CONTEXT = 32768`.

`recommended = min(32768, native_max, max_fits)` — plus `max_fits` (the capability ceiling) and `cuda_single_card_fits` (informational: does the model at the recommended context fit one card vs needing the pool — the user can ignore this; it's not a backend push).

**Why not "max that fits"**: the pipeline is chunked. Peak single-call demand is ~14K tokens (Pass B worst case). Even the whole-stream Tier-3 A1 arc pass uses only ~3KB of summaries. So 32K is 2× headroom and nothing benefits from more — and recommending the max would reserve KV cache the pipeline never fills (e.g. 128K on qwen3.5-9b reserves 16 GB of KV that Pass B fills 4.6% of). Full reasoning in [[concepts/vram-budget]] §"Why bigger context ≠ better clips".

CLI: `python scripts/lib/model_registry.py {list|predict|recommend|combo} …`.

---

## 4. Surfaces — `logtool vram` + dashboard

**`python scripts/logtool.py vram`** — three sections: live adapter summary (per-card + pool), per-stage VRAM trajectory from the last run's `vram_log.json` (color-coded by occupancy), and a per-installed-model table with `rec ctx` (workload 32K) / `ceiling` (max that fits) / `CUDA@rec` (fits one card?) / `KVsrc` (gguf|heuristic).

**Dashboard** — `/api/models/context-recommendation?text_model=…&vision_model=…` ([dashboard/routes/models_routes.py](dashboard/routes/models_routes.py)) computes the combo recommendation against the live pool. `models-panel.js::fetchContextRecommendation()` fires on every model-dropdown change and renders a line under the Context Window card: *"💡 Recommended context: 32K … could hold up to {ceiling} but the pipeline never uses it"* + an Apply button. See [[concepts/bugs-and-fixes]] BUG 61 (the misleading static "8192 ⭐ recommended" this replaced).

---

## Can the pipeline control the engine / GPU split? No — and it doesn't need to

Investigated 2026-06-06. The project interacts with LM Studio only via the HTTP API + `lms` CLI. Neither exposes backend or per-GPU selection:

- **`lms load --gpu <ratio>`** controls the **offload ratio** (GPU vs CPU), NOT which GPU or which engine.
- **`lms runtime select <alias>`** switches the *global* engine (e.g. Vulkan ⟷ CUDA) — `lms runtime ls` shows the installed engines (dev box: `llama.cpp-win-x86_64-vulkan-avx2@2.20.1 ✓` selected, CUDA engines installed but not selected). This is a **global LM Studio setting**, not per-API-call.
- **GPU strategy** ("Split evenly" / "Priority order") + per-model GPU assignment live in LM Studio's **Hardware GUI**, not the API.

**Conclusion (2026-06-06 user direction)**: the project stays **engine-agnostic**. It does not try to force CUDA-only or change the split — whatever engine + GPU/CPU layout the user picks in LM Studio is taken as given. The tooling's job is only to predict context-fit against the resulting VRAM pool and match the pipeline's ~32K workload. Backend choice (and its speed tradeoffs) is the operator's call, made in LM Studio.

---

## Installed quantizations (GGUF `general.file_type`, 2026-06-06)

All main pipeline models are **Q4_K_M** except `openai/gpt-oss-20b` (**MXFP4** native — don't re-quant) and `nvidia/nemotron-3-nano-4b` (**Q8_0**). The on-disk GGUF size ≈ the weight VRAM footprint at that quant.

---

## Related
- [[concepts/vram-budget]] — the budget math, per-model KV tables, per-stage max_tokens, "why bigger context ≠ better"
- [[entities/lm-studio]] — the inference server + `lms` CLI lifecycle + GPU/runtime control
- [[concepts/bugs-and-fixes]] — BUG 61 (misleading static context recommendation)
- [[concepts/bare-metal-windows]] — the native Windows runtime this all targets
