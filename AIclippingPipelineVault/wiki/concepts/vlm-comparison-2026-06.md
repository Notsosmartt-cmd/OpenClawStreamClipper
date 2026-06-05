---
title: "VLM Head-to-Head (mid-2026)"
type: concept
tags: [vision, vlm, benchmark, comparison, qwen3-vl, gemma4, qwen35, model-selection, research]
sources: 2
updated: 2026-06-04
---

# VLM Head-to-Head — Qwen3-VL vs Gemma 4 vs Qwen3.5 (mid-2026)

Research pass on which open-weight VLM best fits the clipper's **specific** vision workload — pairwise multi-frame moment judgment ([[entities/vision-judge]] Stage 5.5) and frame-grounded enrichment ([[concepts/vision-enrichment]] Stage 6) on the [[concepts/bare-metal-windows]] rig (RTX 5060 Ti 16 GB CUDA / RX 6700 XT 12 GB Vulkan).

> [!note] Why a dedicated page
> The earlier [[concepts/model-split]] eval ("Qwen3-VL's edge is OCR-specific, not blanket") was too dismissive — it missed (a) Qwen3-VL's documented **ScreenSpot 94.7%** UI grounding (relevant to chrome_regions detection), (b) the multi-frame video-temporal nature of Stage 5.5, and (c) the open llama.cpp Gemma 4 vision bugs. This page is the corrected, benchmark-grounded view.

---

## The workload — what actually matters

Per [[entities/vision-judge]] and [[concepts/vision-enrichment]], the vision model has **8 distinct jobs** per moment, on 6 frames spanning T−2s → T+5s:

| Job | Output | Stage | Bench that signals fit |
|---|---|---|---|
| Selection | pairwise winner | 5.5 | multi-image preference; MVBench/VideoMME |
| Visual score | 1-10 boost | 6 | general visual reasoning; MMMU-Pro |
| Title / hook / description | text | 6 | grounded copy generation |
| Category | funny/hype/etc | 6 | scene understanding |
| Chrome regions | x/y/w/h of UI elements | 6 | **ScreenSpot / ScreenSpot-Pro** |
| Mirror-safe bool | text-direction reading | 6 | OCR-adjacent |
| Voiceover script | TTS text + tone | 6 | grounded creative |
| Visual pattern match | bool | 6 | cross-modal consistency |

OCR/chrome-text reading is **one job of eight**. The pipeline already runs [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) for hard on-screen text (BUG 40/41), so pure-OCR specialization isn't decisive — but **UI-element grounding** (ScreenSpot) for chrome_regions and **multi-image video-temporal** reasoning for the Vision Judge are.

---

## Benchmark table (best-available primary/secondary)

| Benchmark | Qwen3-VL-8B-Instruct | Qwen3-VL-30B-A3B-Instruct | Qwen3.5-9B (unified, contested) | Gemma-4-12B Unified | Gemma-4-26B-A4B |
|---|---|---|---|---|---|
| MMMU (val) | ~69-70 | ~70-72 (est.) | **78.4** (Alibaba) | unpublished | unpublished |
| MMMU-Pro | n/a | n/a | 70.1 | 69.1 | **73.8** |
| MMBench v1.1 EN | 85.0 | higher | **90.1** | n/a | n/a |
| OCRBench | 896 | **903** | 89.2 (norm) | unpublished | unpublished |
| DocVQA | ~96% | **95.0%** | parity claim | trails 26B (94.9) | **94.9** |
| RealWorldQA | ~71% | higher | n/a | n/a | n/a |
| **ScreenSpot (UI)** | ~94% | **94.7%** | n/a | **unpublished** | **unpublished** |
| VideoMME | ~71 | higher | n/a | n/a | n/a |
| MATH-Vision | n/a | n/a | n/a | 79.7 | 82.4 |
| Native multi-image | ✅ | ✅ | ✅ claimed | ✅ | ✅ |
| Native video timestamping | ✅ | ✅ | unclear | 60 s @ 1 fps cap | same cap |
| Native context | **256K** (→1M) | 256K (→1M) | — | smaller | smaller |

The conspicuous gap in Gemma 4's row is real — Google's announcement leans on **MMMU-Pro + MATH-Vision** and does not publish ScreenSpot, MVBench, or OCRBench in primary sources. For the chrome_regions task you'd be trusting a general multimodal claim with no underlying bench.

Qwen3.5-9B's 78.4 MMMU comes from Alibaba's own card and has not been independently reproduced.

---

## VRAM table (Q4_K_M, RTX 5060 Ti 16 GB CUDA single-card)

| Model | Weights | mmproj | Total | Fits CUDA alone? |
|---|---|---|---|---|
| Qwen3-VL-8B-Instruct | 5.03 GB | ~0.6 GB | **~5.7 GB** | ✅ huge headroom for 256K KV cache |
| Qwen3-VL-30B-A3B-Instruct | 18.6 GB | ~1.0 GB | **~19.6 GB** | ❌ Vulkan pool (28 GB) or `--cpu-moe` |
| Qwen3.5-9B + community VLM mmproj | 5.63-6.0 GB | 918 MB | **~7 GB** | ✅ — but LM Studio support is "Partial" |
| Gemma-4-12B Unified | 7.38 GB | encoder-free | **~7.4 GB** | ✅ — but see llama.cpp bugs below |
| Gemma-4-26B-A4B | ~16-18 GB | encoder-free | **~16-18 GB** | ❌ borderline, needs pool or Q4_K_S |

---

## Architectural fit per family

### Qwen 3.6 (Apr 2026 — multimodal, the new contender — 2026-06-04 update)

**Qwen 3.6 is multimodal — both 27B dense AND 35B-A3B MoE.** Discovered 2026-06-04: there is no separate Qwen3.6-VL line; vision is baked into the base 3.6 weights. The QwenLM GitHub README is misleading (omits vision), but the HF model cards list `image-text-to-text` and ship vision encoder weights. Confirmed via HF cards, vLLM Qwen3.5/3.6 recipes doc, third-party benchmarks.

**Vision benchmarks for `qwen/qwen3.6-35b-a3b`** (Alibaba HF card, thinking-on):

| Benchmark | Score | vs Qwen3-VL-30B-A3B |
|---|---|---|
| MMMU | **81.7** | ahead |
| MMMU-Pro | **75.3** | competitive |
| MMBench EN-DEV-v1.1 | **92.8** | ahead |
| MathVista-mini | 86.4 | n/a |
| RealWorldQA | 85.3 | n/a |
| OmniDocBench 1.5 | 89.9 | competitive |
| VideoMME w/sub | 86.6 | competitive |
| VideoMMMU | 83.7 | n/a |
| ScreenSpot | unpublished | **Qwen3-VL wins (94.7%)** |
| OCRBench | unpublished | **Qwen3-VL wins (903)** |

**Knock-on for THIS pipeline**: this is the single best installed model for the entire workload — top-tier in both text and vision. Combined with the verified BUG 57 toggle workaround, it becomes the **single-model consolidation pick** at ~22 GB Vulkan pool (MoE 3B active = fast despite the pool tax). The only Qwen3-VL edge is for the specific `chrome_regions` UI grounding task (where ScreenSpot 94.7% is documented and Qwen 3.6's is unpublished).

### Qwen3-VL (Dec 2025 → mid-2026)
**Purpose-built VLM family — still wins specific UI-grounding tasks.**

- **Native multi-image + video** with explicit **Text-Timestamp Alignment** — purpose-built for the T−2s → T+5s window in Stage 5.5.
- **256K native context** (extendable to 1M) — multi-card tournament rounds without re-encoding.
- **ScreenSpot 94%+** (8B) / **94.7%** (30B-A3B) → first-class UI grounding for chrome_regions.
- **2D/3D grounding** is a first-class capability, not emergent.
- 30B-A3B is MoE with ~3B active per token → near-8B inference speed at substantially better visual reasoning.
- Officially packaged by `lmstudio-community` at llama.cpp b6890+.

### Qwen3.5-9B unified (multimodal — VERIFIED by LM Studio, single-source on benches)

**Vision works in LM Studio.** Hub tags `qwen/qwen3.5-9b` with **Capabilities: Vision, Tool Use, Reasoning** and a **Staff Pick** badge (1.99M downloads, ~1 day freshness as of 2026-06-04). LM Studio's team verifies multimodal before tagging — this overrides the earlier "Partial / mmproj-missing" framing (which was based on stale HF repo state).

- Alibaba's HF card claims early-fusion multimodal training (MMMU **78.4**, OCRBench 89.2). If real, it would outperform Qwen3-VL-8B AND Gemma 4 12B at this size class.
- Caveat: **single-source benchmarks** from Alibaba's own model card — no third-party reproductions yet found. Treat as best-evidence pending independent runs.
- **Consolidation potential**: 6.5 GB Q4 covering BOTH text + vision slots, zero text↔vision swap, fits CUDA with 10 GB headroom. Compare against Gemma 4 12B's documented IFEval 88.9 (best JSON-extraction reliability) — see [[concepts/text-comparison-2026-06]].

> [!warning] First-pass eval here was wrong
> The earlier version of this section ("Partial in LM Studio, text-only in production") was based on stale info. LM Studio's Staff Pick + Vision tag is the authoritative signal. The user surfaced the correction the same day. Quick verification path: drag an image into a `qwen/qwen3.5-9b` chat in LM Studio and ask "describe this image" — coherent response = vision verified for this install.

### Gemma 4 (early-mid 2026)
**Strong general visual reasoning, weak in workload-specific benches, fragile llama.cpp story.**

- Encoder-free unified architecture (single matmul + positional embed); tiny encoder overhead at inference.
- Strong MMMU-Pro and MATH-Vision; native audio (unused here).
- **No published ScreenSpot, MVBench, or OCRBench** in primary sources.
- Documented video frame cap of "60 seconds at 1 fps" per Unsloth docs — fine for 7 s clip cards but no headroom.
- Active llama.cpp issues (as of mid-2026):
  - [#21402](https://github.com/ggml-org/llama.cpp/issues/21402) SIGABRT on CUDA mmproj load
  - [#24085](https://github.com/ggml-org/llama.cpp/issues/24085) SIGFPE on `gemma-4-12B-it-GGUF`
  - [#21497](https://github.com/ggml-org/llama.cpp/issues/21497) Gemma-4-26B-A4B can't process images
  - [#21343](https://github.com/ggml-org/llama.cpp/issues/21343) / [#21326](https://github.com/ggml-org/llama.cpp/issues/21326) tokenizer + template fixes (Unsloth reuploads required)
  - Q4 quants reportedly produce gibberish at default sampling → needs `minP=0.01`, TopK tweaks
- LM Studio's vendored llama.cpp build may patch some of these (which is why it currently runs in the pipeline) — but the bug surface is large.

---

## Recommendation for the clipper (mid-2026, single-card 16 GB CUDA priority)

| Tier | Pick | Rationale |
|---|---|---|
| **Speed** | `Qwen3-VL-8B-Instruct` Q4_K_M | 5.7 GB total → 10 GB headroom for 256K context; multi-image + video native; ScreenSpot 94%. Lowest-risk swap-in. |
| **Balanced (recommended)** | **`Qwen3-VL-8B-Instruct`** | Same model. Nothing else in the Q4 single-card class matches it on vision-benchmark transparency + multi-image/video + llama.cpp packaging maturity. |
| **Quality** | `Qwen3-VL-30B-A3B-Instruct` Q4_K_M | MoE 3B active keeps it fast even with the 30-40% Vulkan dual-GPU penalty. Best published ScreenSpot 94.7%, OCRBench 903, DocVQA 95.0%. `--cpu-moe` is a viable CUDA-fallback. |
| **Status quo** | `google/gemma-4-12b` (current) | Not bad — fits CUDA, MMMU-Pro 69.1, currently working. But unpublished workload-specific benches and open llama.cpp CUDA crashes make it the higher-risk continued pick. |

### What I'd actually do (per the May/June 2026 decision)
1. **Swap `vision_model` to `Qwen3-VL-8B-Instruct`** as the simplest upgrade. Same VRAM class as Gemma-4-12B, fits CUDA, demonstrated multi-image + ScreenSpot.
2. **Run a side-by-side on one VOD** — capture `judge_tournament.json` and Stage 6 outputs from both models on the same input. Use [`logtool axes`](AIclippingPipelineVault/wiki/concepts/observability.md) to diff rank churn and Stage 6 grounding-tier rates.
3. **If quality still feels limiting**, escalate to `Qwen3-VL-30B-A3B-Instruct` on the Vulkan pool (or `--cpu-moe` CUDA-fallback).
4. **Drop the qwen3.5-9b "multimodal" claim** in the dashboard hint / memory — treat it as text-only in production.

---

## Sources

Primary:
- [Qwen3-VL Technical Report (arXiv 2511.21631)](https://arxiv.org/abs/2511.21631v2)
- [Qwen3-VL-8B-Instruct model card](https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct)
- [Qwen3-VL-30B-A3B-Instruct model card](https://huggingface.co/Qwen/Qwen3-VL-30B-A3B-Instruct)
- [lmstudio-community/Qwen3-VL-8B-Instruct-GGUF](https://huggingface.co/lmstudio-community/Qwen3-VL-8B-Instruct-GGUF)
- [lmstudio-community/Qwen3-VL-30B-A3B-Instruct-GGUF](https://huggingface.co/lmstudio-community/Qwen3-VL-30B-A3B-Instruct-GGUF)
- [Qwen3.5-9B model card](https://huggingface.co/Qwen/Qwen3.5-9B)
- [jc-builds/Qwen3.5-9B-VLM-Q4_K_M-GGUF](https://huggingface.co/jc-builds/Qwen3.5-9B-VLM-Q4_K_M-GGUF)
- [lmstudio-community/Qwen3.5-9B-GGUF (text-only)](https://huggingface.co/lmstudio-community/Qwen3.5-9B-GGUF)
- [Google blog: Introducing Gemma 4 12B](https://blog.google/innovation-and-ai/technology/developers-tools/introducing-gemma-4-12b/)
- [lmstudio-community/gemma-4-12B-it-GGUF](https://huggingface.co/lmstudio-community/gemma-4-12B-it-GGUF)
- [Unsloth Gemma 4 docs](https://unsloth.ai/docs/models/gemma-4)

llama.cpp issues:
- [#21402 Gemma 4 mmproj CUDA SIGABRT](https://github.com/ggml-org/llama.cpp/issues/21402)
- [#24085 gemma-4-12B-it-GGUF SIGFPE](https://github.com/ggml-org/llama.cpp/issues/24085)
- [#21497 Gemma-4-26B-A4B cannot process images](https://github.com/ggml-org/llama.cpp/issues/21497)
- [#21268 Qwen3.5 CLIP graph unsupported ops](https://github.com/ggml-org/llama.cpp/issues/21268)

Secondary:
- [llm-stats Qwen3-VL-8B-Instruct](https://llm-stats.com/models/qwen3-vl-8b-instruct)
- [Codersera Qwen3-VL 2026 guide](https://codersera.com/blog/qwen3-vl-8b-instruct-vs-qwen3-vl-8b-thinking-2025-guide/)
- [binaryverse Qwen3-VL local install guide](https://binaryverseai.com/qwen3-vl-benchmarks-local-installation-guide-use/)
- [LM Studio Structured Output docs](https://lmstudio.ai/docs/developer/openai-compat/structured-output)

---

## Related
- [[entities/qwen3-vl]] — updated entity page (un-retired 2026-06-04)
- [[entities/qwen35]] — updated to note text-only-in-production for vision claim
- [[entities/gemma4]] — new entity page for the Gemma 4 family
- [[concepts/model-split]] — text+vision slot config; Evaluation notes rewritten using this page's data
- [[entities/vision-judge]] — Stage 5.5 consumer
- [[concepts/vision-enrichment]] — Stage 6 consumer
- [[concepts/bugs-and-fixes]] — BUG 57 narrowed (LM Studio app-side toggle DOES work)
