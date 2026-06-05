---
title: "Qwen3-VL (vision-language family)"
type: entity
tags: [model, vision, qwen, multimodal, alibaba, stage-6, vision-judge, hub]
sources: 3
updated: 2026-06-04
---

# Qwen3-VL

Alibaba's dedicated vision-language model family, released **Dec 2025** ([arXiv 2511.21631](https://arxiv.org/abs/2511.21631v2)). The recommended migration target for the pipeline's vision slot per [[concepts/vlm-comparison-2026-06]] — purpose-built for the multi-frame video-temporal workload that Stage 5.5 and Stage 6 actually run.

> [!note] Un-retired 2026-06-04
> An earlier note marked this entity as retired in favor of unified multimodal LLMs (Gemma 4, Qwen3.5). That framing has been reversed — [[concepts/vlm-comparison-2026-06]] confirms Qwen3-VL is the stronger architectural fit for this pipeline's specific jobs (multi-image preference judgment in Stage 5.5, frame-grounded copy + UI-element localization in Stage 6).

---

## Family

Released as a family with explicit latency-quality trade-offs:

| Variant | Type | Total params | Active params | Native context | LM Studio Q4_K_M VRAM |
|---|---|---|---|---|---|
| Qwen3-VL-2B-Instruct | dense | 2B | 2B | 256K | ~2 GB |
| Qwen3-VL-4B-Instruct | dense | 4B | 4B | 256K | ~3 GB |
| **Qwen3-VL-8B-Instruct** | **dense** | **8B** | **8B** | **256K (→1M)** | **~5.7 GB** ← **rec for this pipeline** |
| Qwen3-VL-32B-Instruct | dense | 32B | 32B | 256K | ~20 GB (needs pool) |
| **Qwen3-VL-30B-A3B-Instruct** | **MoE** | **30B** | **~3B** | **256K** | **~19.6 GB** (pool / `--cpu-moe`) |
| Qwen3-VL-235B-A22B-Instruct | MoE | 235B | ~22B | 256K | far too big |

A separate **Thinking** edition exists per family member (`-Thinking` suffix) — thinking mode is selected by **model variant**, not runtime toggle. For the pipeline's vision work, default to the **Instruct** variants.

---

## Strengths for the clipper workload

- **Native multi-image** input — Stage 5.5 cards (4-6 frames) and Stage 6 enrichment (6 frames) are first-class, not stitched.
- **Native video** with explicit **Text-Timestamp Alignment** — temporal indexing across the T−2s → T+5s payoff window.
- **256K native context** (extendable to 1M) — multi-card tournament rounds and long-form prompts without re-encoding.
- **ScreenSpot 94% (8B) / 94.7% (30B-A3B)** — first-class UI-element grounding for `chrome_regions` (chat / logo / cam bbox detection).
- **2D/3D grounding** is a first-class capability.
- **OCRBench 896 (8B) / 903 (30B-A3B)** — Qwen wins the OCR tier even though PaddleOCR already handles overlay text.
- **Officially packaged** by `lmstudio-community` at llama.cpp b6890+ — clean install, no community-mmproj fragility.

---

## Benchmarks (vs the alternatives)

See [[concepts/vlm-comparison-2026-06]] for the full side-by-side. Highlights:

| | Qwen3-VL-8B | Qwen3-VL-30B-A3B | Gemma-4-12B (current) | Gemma-4-26B-A4B |
|---|---|---|---|---|
| MMMU-Pro | ~ | ~ | 69.1 | **73.8** |
| OCRBench | 896 | **903** | unpublished | unpublished |
| DocVQA | ~96% | **95.0%** | trails | 94.9 |
| ScreenSpot | ~94% | **94.7%** | **unpublished** | unpublished |
| MVBench | ~ | higher | n/a | n/a |
| Native context | 256K | 256K | smaller | smaller |

---

## Roles in the pipeline (when adopted)

| Stage | Role | Output |
|---|---|---|
| Stage 5.5 ([[entities/vision-judge]]) | Pairwise tournament winner | `{winner: A\|B, confidence, reason}` over 4-6 frame cards |
| Stage 6 ([[concepts/vision-enrichment]]) | Frame-grounded enrichment | `{score, category, title, description, hook, mirror_safe, chrome_regions, voiceover}` |

Both stages benefit from the multi-image-native + video-temporal architecture. Stage 6's `chrome_regions` field in particular is the one where Qwen3-VL's documented ScreenSpot performance is most decisive.

---

## VRAM choreography (8B is the simplest)

`Qwen3-VL-8B-Instruct` at Q4_K_M = **~5.7 GB** weights + mmproj on the RTX 5060 Ti 16 GB. Leaves ~10 GB for KV cache + 256K context.

`Qwen3-VL-30B-A3B-Instruct` at Q4_K_M = **~19.6 GB** total → exceeds 16 GB CUDA; needs the dual-GPU Vulkan pool (~28 GB combined) or `llama.cpp --cpu-moe` to keep the experts in system RAM. MoE's 3B active params keep per-token throughput acceptable even with the Vulkan dual-GPU penalty (~30-40% throughput loss vs single-card per [llama.cpp #16767](https://github.com/ggml-org/llama.cpp/issues/16767)).

See [[concepts/vram-budget]].

---

## Known issues

- **llama.cpp CUDA BF16 im2col** unsupported in older builds — vision encoder may CPU-fall-back. Requires llama.cpp **b6890 or newer**. The `lmstudio-community` GGUF was built at that version.
- **mmproj file** required — installed alongside the weights GGUF in the LM Studio model dir.
- **Instruct vs Thinking is a model-variant choice**, not a runtime toggle. Don't expect to flip thinking mode at request time on this family.

---

## Adoption checklist

1. Download `lmstudio-community/Qwen3-VL-8B-Instruct-GGUF` (Q4_K_M) via LM Studio.
2. Verify mmproj file is co-located.
3. Set `config/models.json::vision_model` to the new ID.
4. Run one VOD with current Gemma vision; rerun with Qwen3-VL on the same input.
5. Compare via [`logtool axes`](AIclippingPipelineVault/wiki/concepts/observability.md): `judge_tournament.json` rank churn, Stage 6 grounding-tier rates, title quality.

---

## Related

- [[concepts/vlm-comparison-2026-06]] — the head-to-head research that motivates this pick
- [[entities/gemma4]] — the incumbent it would replace
- [[entities/qwen35]] — text model paired with it
- [[entities/lm-studio]] — serves the GGUF + mmproj
- [[entities/vision-judge]] — Stage 5.5 consumer
- [[concepts/vision-enrichment]] — Stage 6 consumer
- [[concepts/model-split]] — vision_model slot config
- [[concepts/vram-budget]] — Q4 single-card fit
