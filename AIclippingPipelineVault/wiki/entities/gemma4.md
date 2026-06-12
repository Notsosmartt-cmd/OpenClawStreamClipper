---
title: "Gemma 4 (Google multimodal family)"
type: entity
tags: [model, vision, multimodal, gemma, google, llm, stage-6, vision-judge, hub]
sources: 2
updated: 2026-06-12
---

# Gemma 4

> [!warning] Superseded as the vision model (2026-06-12)
> The pipeline **no longer uses Gemma 4 for vision**. `config/models.json` sets `vision_model` (and `text_model`) to the unified **`qwen/qwen3.6-35b-a3b`** — one model serves both roles, so there is no separate Gemma vision slot. Gemma 4 remains *installed* and a viable quality-tier alternative (26B-A4B), and the "why picked initially" rationale below is preserved as history. Sections that say "current `vision_model`" are pre-2026-06-12.

Google's Gemma 4 family — encoder-free unified multimodal models. Was the pipeline's `vision_model` (`gemma-4-12b`, Stage 6 + Vision Judge) until the 2026-06-12 swap to the unified `qwen3.6-35b-a3b`. Installed locally in LM Studio (see [hardware-specs](C:\Users\user\.claude\projects\G--OpenClawStreamClipper\memory\hardware-specs.md)): 12B (dense), 26B-A4B (MoE ~4B active), 31B (dense).

> [!note] Architecture (early-2026 family)
> Encoder-free unified: single matmul + positional embed for image tokens, no separate vision encoder graph. Reduces inference overhead vs traditional encoder+LLM VLMs. Native audio support (unused by this pipeline).

---

## Variants in use

| Variant | Q4_K_M VRAM | Role | Fits 16 GB CUDA alone? |
|---|---|---|---|
| `google/gemma-4-12b` | 7.6 GB | former `vision_model` (Stage 6 + Vision Judge) — superseded by `qwen3.6-35b-a3b` | ✅ |
| `google/gemma-4-26b-a4b` | 18.0 GB | quality candidate (MoE ~4B active) | ❌ Vulkan pool |
| `google/gemma-4-31b` | 19.9 GB | reference / quality ceiling | ❌ Vulkan pool |

The 12B is the only Gemma 4 variant that fits CUDA single-card at Q4. The 26B-A4B and 31B require the dual-GPU Vulkan pool ([[concepts/vram-budget]]).

---

## Strengths for this workload

- **General visual reasoning**: MMMU-Pro 69.1 (12B) / 73.8 (26B-A4B) — strong.
- **MATH-Vision**: 79.7 (12B) / 82.4 (26B-A4B).
- **DocVQA** (26B-A4B): 94.9 — solid OCR even though PaddleOCR handles overlay text.
- **Was working in the pipeline** — Stage 6 output grounded titles/hooks on production runs before the 2026-06-12 swap to the unified model.

---

## Weaknesses for this workload

- **Workload-specific benchmarks are unpublished by Google**: no ScreenSpot (UI grounding for `chrome_regions`), no MVBench (multi-frame video), no OCRBench primary numbers. You're trusting a general multimodal claim without the bench data the Stage 5.5 + Stage 6 jobs care about.
- **Video frame cap**: ~60 s at 1 fps per Unsloth docs. Fine for the 7 s clip card but no headroom.
- **llama.cpp vision bugs** (as of mid-2026 — may be partially patched in LM Studio's vendored build):

| Issue | Symptom |
|---|---|
| [#21402](https://github.com/ggml-org/llama.cpp/issues/21402) | SIGABRT on CUDA mmproj load |
| [#24085](https://github.com/ggml-org/llama.cpp/issues/24085) | SIGFPE on `gemma-4-12B-it-GGUF` |
| [#21497](https://github.com/ggml-org/llama.cpp/issues/21497) | Gemma-4-26B-A4B can't process images |
| [#21343](https://github.com/ggml-org/llama.cpp/issues/21343) | Tokenizer fix (Unsloth reuploads required) |
| [#21326](https://github.com/ggml-org/llama.cpp/issues/21326) | Chat template fix |
| (no issue) | Q4 quants → gibberish at default sampling → needs `minP=0.01`, TopK tweaks |

If you switch the vision model away from Gemma 4 ([[concepts/vlm-comparison-2026-06]] recommends [[entities/qwen3-vl]] 8B), these stop being concerns. If you stay on Gemma 4, watch LM Studio version changes — a vendored llama.cpp regression could break Stage 6.

---

## Thinking mode

Both 12B and 26B-A4B can emit reasoning content in LM Studio. Per [[concepts/model-split]]'s thinking policy: **OFF** for all this pipeline's vision work (Stage 5.5 judge + Stage 6 enrichment). Set via LM Studio Custom Fields → Enable Thinking OFF (verified working approach 2026-06-04 — see narrowed [[concepts/bugs-and-fixes]] BUG 57).

---

## Why it was picked initially (2026-06-04)

The earlier Tier-1 pivot (away from `qwen3.6-35b-a3b`) chose `gemma-4-12b` for vision because:
1. It fits the 16 GB CUDA budget with headroom for context.
2. It's multimodal out of the box (vs `qwen3.5-9b` whose mmproj is community-built and "Partial" in LM Studio).
3. The 7.6 GB size left room for the 6.5 GB `qwen3.5-9b` to potentially co-reside.
4. It was already installed.

The [[concepts/vlm-comparison-2026-06]] research subsequently found Qwen3-VL-8B is a stronger architectural fit for the specific Stage 5.5 + Stage 6 jobs (multi-image video temporal + ScreenSpot UI grounding), but Gemma 4 12B remains a defensible no-download status quo.

---

## Related

- [[entities/qwen3-vl]] — recommended migration target for vision
- [[entities/qwen35]] — text model paired with this for Stages 3-4
- [[entities/lm-studio]] — serves these models
- [[concepts/model-split]] — vision_model slot config
- [[concepts/vlm-comparison-2026-06]] — head-to-head with Qwen3-VL
- [[concepts/vram-budget]] — single-card vs Vulkan-pool fit
- [[entities/vision-judge]] / [[concepts/vision-enrichment]] — consumer stages
