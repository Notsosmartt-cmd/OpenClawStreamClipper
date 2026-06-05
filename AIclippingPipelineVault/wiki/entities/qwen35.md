---
title: "Qwen 3.5 / 3.6 (pipeline text family)"
type: entity
tags: [model, llm, alibaba, qwen, pipeline, infrastructure, stage-3, stage-4, text, vision-claim, hub]
sources: 3
updated: 2026-06-04
---

# Qwen 3.5 / 3.6 (pipeline text family)

Alibaba's Qwen text family. The current pipeline `text_model` is **`qwen/qwen3.5-9b`** (Stage 3 + Pass B + Pass D) — see `config/models.json`.

Other family members the user has installed locally (LM Studio, see [hardware-specs](C:\Users\user\.claude\projects\G--OpenClawStreamClipper\memory\hardware-specs.md)):

| Variant | Q4_K_M VRAM | Notes |
|---|---|---|
| `qwen/qwen3.5-9b` | 6.5 GB | **current `text_model`** — non-thinking by default; fits 16 GB CUDA |
| `qwen/qwen3.6-27b` | 17.5 GB | dense; quality candidate but needs Vulkan pool at Q4_K_M (UD-Q3_K_XL ~14.5 GB fits CUDA) |
| `qwen/qwen3.6-35b-a3b` | 22.1 GB | MoE ~3B active; needs Vulkan pool; **thinking can be disabled** via LM Studio Custom Fields toggle (verified 2026-06-04 — see narrowed [[concepts/bugs-and-fixes]] BUG 57) |

Served by [[entities/lm-studio]] at `localhost:1234`.

> [!note] This page replaces the older Ollama-era qwen35 doc (2026-04)
> Previous text mentioned ~11.2 GB at 32K context and Ollama-served calls. Both are stale — LM Studio replaced Ollama 2026-04-18 ([[entities/ollama]] retired), and the active model is the 6.5 GB Qwen 3.5 9B at Q4_K_M.

---

## Role: pipeline text analysis (current)

**Stage 3 — Segment classification** (`text_model`):
- Classifies each ~10-min transcript chunk into one of: `gaming`, `irl`, `just_chatting`, `reaction`, `debate`.
- Tiny prompt + JSON output. Fast.

**Stage 4 Pass B — LLM moment detection** (`text_model_passb` ?? `text_model`):
- Walks the transcript in chunks; emits JSON array of `{time, score, category, why, ...}` candidates.
- The **dominant** LLM stage by wall-clock — ~80-120 calls on a 4 h VOD, ~4-8K context each.
- Segment-aware prompts tailored to the Stage 3 classification.

**Stage 4 Pass D — Rubric judge** (same model):
- Per-candidate single-JSON-object scoring on a fixed rubric. ~30-80 calls per run.
- The only text stage where reasoning could plausibly help — currently kept thinking-OFF for safety (see [[concepts/model-split]] thinking policy).

---

## Qwen 3.6 multimodal — confirmed across the family (2026-06-04 third-pass research)

**Both Qwen 3.6 27B dense AND Qwen 3.6 35B-A3B MoE are natively multimodal.** No separate `Qwen3.6-VL` line exists — vision is baked into the base 3.6 weights. Confirmed via:
- HF model cards list `image-text-to-text` task class with vision encoder weights packaged
- vLLM Qwen3.5/3.6 recipes doc references the vision tower (~333 keys, ~100 MB), with `--language-model-only` flag to skip the encoder
- The QwenLM/Qwen3.6 GitHub README is **misleading** (it omits vision), but the deployed artifacts include it

**Vision benchmarks for `qwen/qwen3.6-35b-a3b`** (thinking-on, Alibaba's HF card):

| Benchmark | Score |
|---|---|
| MMMU | **81.7** |
| MMMU-Pro | **75.3** |
| MMBench EN-DEV-v1.1 | **92.8** |
| MathVista-mini | 86.4 |
| RealWorldQA | 85.3 |
| OmniDocBench 1.5 | 89.9 |
| VideoMME w/sub | 86.6 |
| VideoMMMU | 83.7 |

These are top-of-leaderboard numbers — competitive with or ahead of Qwen3-VL-30B-A3B on most general visual benches (Qwen3-VL still wins specifically on ScreenSpot 94.7% UI grounding and OCRBench 903).

**Implication**: `qwen/qwen3.6-35b-a3b` is plausibly the **single best model for the entire pipeline** — top-tier text AND vision in one ~22 GB MoE (~3B active = fast on Vulkan pool), with the [[concepts/bugs-and-fixes]] BUG 57 toggle workaround verified. See [[concepts/vlm-comparison-2026-06]] and [[concepts/model-split]] for the consolidation strategy.

## Qwen 3.5-9B multimodal — verified by LM Studio (2026-06-04, second-pass correction)

**Confirmed working.** LM Studio's Hub tags `qwen/qwen3.5-9b` with **Capabilities: Vision, Tool Use, Reasoning** and a **Staff Pick** badge (1.99M downloads, last updated 1 day before 2026-06-04). LM Studio's team explicitly verifies multimodal support before applying the Vision tag — this is the ground-truth signal, not the bare HuggingFace repo's mmproj packaging state.

- **Architecture**: early-fusion multimodal (Alibaba HF card claims MMMU 78.4, OCRBench 89.2 — single-source; not third-party verified).
- **LM Studio install**: bundled mmproj, works out of the box. Vision support is **NOT "Partial"** as an earlier note here claimed.
- **Quick verification test**: load `qwen/qwen3.5-9b` in LM Studio, drag an image into the chat, ask "describe this image." A coherent description = vision works. If a user reports otherwise, that flips the recommendation.

> [!warning] Earlier first-pass eval was wrong
> The first version of this section (2026-06-04 morning) claimed "text-only in production" based on stale HF repo state. The user surfaced LM Studio's Staff Pick + Vision tag screenshot the same day, refuting that. Treat the multimodal capability as **verified in LM Studio**; treat the MMMU 78.4 number as **single-source pending independent reproduction**.

→ **Consolidation opportunity**: with vision working, `qwen3.5-9b` could fill BOTH slots (`text_model` AND `vision_model`) — single 6.5 GB model, zero text↔vision swap, fits CUDA single-card with 10 GB of headroom. The trade-off is Gemma 4 12B's documented **IFEval 88.9** (best JSON-extraction reliability at this size class) — quantifiable in head-to-head testing. See [[concepts/text-comparison-2026-06]] for the call.

---

## Thinking policy

- `qwen3.5-9b` is **non-thinking by default** — no special flag needed. Stage 3, Pass B, and Pass D all run fast.
- `qwen3.6-35b-a3b` has thinking ON by default but the **LM Studio app-side toggle disables it** (verified 2026-06-04). The OpenAI-compat API param `enable_thinking:false` is still ignored (BUG 57).
- Per-stage policy in [[concepts/model-split]] §Thinking: OFF for Stage 3, Pass B, Pass D, Stage 6; only candidate for ON is the [[entities/vision-judge]] (Stage 5.5) and that's still untested.

---

## VRAM choreography

`qwen3.5-9b` at 6.5 GB Q4_K_M + 32K context KV cache fits comfortably on the RTX 5060 Ti 16 GB CUDA. Paired with the 7.6 GB `gemma-4-12b` vision model (14.1 GB combined) the two may even co-reside, eliminating the text↔vision swap entirely. See [[concepts/vram-budget]].

---

## Why not 3.6 dense / MoE for `text_model`?

Per [[concepts/model-split]] tier table:

- **Speed pick** — `qwen3.5-9b` (current). Best throughput for the heavy Pass B workload.
- **Balanced** — `openai/gpt-oss-20b` (installed, 12.1 GB MXFP4 CUDA-fit, runtime-tunable `reasoning_effort` Low/Med/High that actually works).
- **Quality** — `qwen3.6-35b-a3b` with **Enable Thinking OFF** (Vulkan pool, MoE 3B active keeps it fast). Newly viable post-BUG-57-narrowing.

---

## Related

- [[concepts/model-split]] — text-slot tier table and thinking policy
- [[concepts/vlm-comparison-2026-06]] — verifies the qwen3.5-9b vision claim is practically fragile
- [[concepts/bugs-and-fixes]] — BUG 57 (narrowed); BUG 20 (token exhaustion)
- [[entities/gemma4]] — current vision slot
- [[entities/qwen3-vl]] — recommended migration target for vision
- [[entities/lm-studio]] — serves the GGUF
- [[concepts/clipping-pipeline]] — Stages 3 and 4
- [[concepts/segment-detection]] — Stage 3
- [[concepts/highlight-detection]] — Stage 4 detail
- [[concepts/vram-budget]] — memory orchestration
