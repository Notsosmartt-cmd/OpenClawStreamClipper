---
title: "Text-LLM Head-to-Head (mid-2026)"
type: concept
tags: [llm, text-model, benchmark, comparison, qwen3, gpt-oss, gemma4, model-selection, research, hybrid-thinking-broken]
sources: 2
updated: 2026-06-04
---

# Text-LLM Head-to-Head — Qwen 3.6 vs gpt-oss-20b vs Gemma 4 (mid-2026)

Companion to [[concepts/vlm-comparison-2026-06]] — focused on the **text role** (Stage 3 + Pass B + Pass D). Same hardware ceiling (RTX 5060 Ti 16 GB CUDA + RX 6700 XT 12 GB Vulkan pool), same workload framing.

> [!warning] The Qwen3 hybrid line is broken (upstream, not just LM Studio)
> `enable_thinking=false` is silently ignored across the entire Qwen3 hybrid family at the **llama.cpp chat-template layer**. Five+ open issues: [#13160](https://github.com/ggml-org/llama.cpp/issues/13160), [#13189](https://github.com/ggml-org/llama.cpp/issues/13189), [#20182](https://github.com/ggml-org/llama.cpp/issues/20182), [#20409](https://github.com/ggml-org/llama.cpp/issues/20409), [#22255](https://github.com/ggml-org/llama.cpp/issues/22255). Alibaba acknowledged this and split Qwen3 into separate `-Instruct-2507` and `-Thinking-2507` variants in July 2025 ([The Register](https://www.theregister.com/2025/07/31/alibaba_qwen3_hybrid_thinking/)). **Use `-Instruct-2507` variants — never the hybrid Qwen3.5 / Qwen3.6 builds — for Pass B.** Belt-and-suspenders: the LM Studio app-side toggle works on `qwen3.6-35b-a3b` (verified, see [[concepts/bugs-and-fixes]] BUG 57) but it's a workaround.

---

## The text workload (recap)

- **Stage 3** segment classify: 1 call/VOD, trivial.
- **Pass B** moment extraction: 80-120 calls/VOD, ~4-8K context each, JSON-array output. **Dominant by wall-clock.** Thinking MUST be OFF — verified by BUG 20/57 (token exhaustion).
- **Pass D** rubric judge: 30-80 calls, single JSON object. The one stage where thinking-Low could plausibly help.

The single most predictive metric for THIS workload is **IFEval** (instruction following) — it's the closest published proxy for JSON-extraction adherence at the schema level.

---

## Benchmark table (updated 2026-06-04 with verified Qwen 3.6 + corrected gpt-oss numbers)

| Benchmark | gpt-oss-20b (High) | Qwen3.6-27B dense (thinking-on) | Qwen3.6-35B-A3B (thinking-on) | Gemma 4 31B | Gemma 4 26B-A4B | Gemma 4 12B | Gemma 3 27B (anchor) |
|---|---|---|---|---|---|---|---|
| MMLU-Pro | n/a published | **86.2** | 85.2 | 85.2 | n/a published | 77.2 | 67.5 |
| MMLU | 85.3 | n/a | n/a | 87.1 (aggregator) | n/a | n/a | n/a |
| GPQA Diamond | 71.5 | **87.8** | 86.0 | 84.3 | 79.2-82.3 | 78.8 | n/a |
| AIME 2026 | 91.7 (AIME 25) | **94.1** | 92.7 | 89.2 | n/a | n/a | n/a |
| LiveCodeBench v6 | n/a | **83.9** | 80.4 | 80.0 | 77.1 | 72 | n/a |
| BBEH | n/a | n/a | n/a | 74.4 | n/a | 53 | n/a |
| IFEval | n/a published | **n/a published** | n/a published | n/a published | n/a published | n/a published | **90.4** (anchor) |
| BBH | n/a | n/a | n/a | n/a | n/a | n/a | 87.6 |
| MATH | (~92 secondary) | n/a | n/a | n/a | n/a | 79.7 | 89.0 |
| LMArena Elo | could not verify May 2026 | n/a | n/a | n/a | n/a | n/a | 1338 |

**Important gaps surfaced 2026-06-04**:
- **Google did NOT publish IFEval, MMLU-Pro, or BBH for Gemma 4 12B/26B-A4B/31B specifically** — Gemma 3 27B numbers are still the most defensible anchor for IFEval (90.4).
- **Qwen 3.6 model cards did NOT publish IFEval, BBH, or LiveBench** — the closest IFEval proxy is Qwen3.5-35B-A3B at 0.919 per llm-stats. Qwen 3.6 is positioned as an iteration so IFEval should be in the same band.
- **OpenAI's gpt-oss card did NOT publish IFEval** — earlier 65/76 numbers were unsourced secondary derivations. What IS in the OpenAI paper (arXiv 2508.10925): Low/Med/High AIME, GPQA, MMLU.
- **Qwen 3.6 scores above are THINKING-ON**. Thinking-OFF will be ~10-15 points lower (approximately Qwen3-Instruct-2507 territory).

**Sources**: [Qwen3.6-27B HF card](https://huggingface.co/Qwen/Qwen3.6-27B), [Qwen3.6-35B-A3B HF card](https://huggingface.co/Qwen/Qwen3.6-35B-A3B), [gpt-oss model card (arXiv 2508.10925)](https://arxiv.org/html/2508.10925v1), Google Gemma 4 12B blog post + secondary developer write-ups (labellerr, lushbinary, aurigait).

**Anchor**: Gemma 3 27B numbers remain the most defensible primary IFEval baseline. Gemma 4 inherits the same playbook with marginal lifts on the published benches (BBEH 53→74.4 size scaling is real).

**Headline (revised)**: Gemma 3/4 likely still owns IFEval (best small-class JSON adherence assumed), but **Qwen 3.6 has top-tier MMLU-Pro/GPQA/AIME/LiveCodeBench** when thinking is on, and is also multimodal — making it a stronger consolidation candidate than the earlier analysis suggested.

---

## JSON output reliability (per family)

| Family | Reliability | Notes |
|---|---|---|
| **gpt-oss-20b @ Low reasoning** | Good (community-tested, untested by formal bench) | Harmony chat format puts reasoning in a separate channel; Low minimizes pre-JSON token bloat. Use `--jinja` flag in llama.cpp for the harmony format. |
| **Qwen3.6-27B / 35B-A3B hybrid, thinking-off** | Excellent **when the toggle engages** | But the toggle is **broken in llama.cpp** (5+ issues). Use the LM Studio app-side toggle (verified working on 35B-A3B) OR switch to a 2507 Instruct variant. |
| **Qwen3-Instruct-2507 variants** | Excellent + structurally safe | Thinking is architecturally absent → impossible for reasoning to leak into output. The right pick if you want Qwen quality without the foot-gun. |
| **Gemma 3/4 12B + 26B-A4B** | **Best** — IFEval 88.9-90.4 (highest in the field) | Structured outputs + function calling are first-class. Gemma 4 26B-A4B has a thinking mode (Gemma 3 didn't) — pass `enable_thinking=false` to be safe. Gemma 4 12B dense has no thinking mode = zero leak risk. |

---

## Thinking-mode policy per family

| Family | Policy | Recommendation for Pass B |
|---|---|---|
| **Qwen3 / 3.5 / 3.6 hybrid** | Deprecated by Alibaba July 2025 (split into 2507 variants). Runtime `enable_thinking=false` ignored by llama.cpp. | **Avoid hybrid builds.** Use `-Instruct-2507` or work around via LM Studio app-side toggle (verified per-model only). |
| **Qwen3-Instruct-2507** | Thinking structurally absent. | Safe by design. |
| **gpt-oss-20b** | Single model, runtime `reasoning_effort` Low/Med/High via system prompt. Low ≈ 1.2-1.5× output tokens of pure non-reasoner; Med ≈ 2-3×; High ≈ 5-8×. | Set `Reasoning: low` for Pass B; flip to `Medium` for Pass D rubric judge. |
| **Gemma 3** | **No thinking mode** — pure instruction tuning, no `<think>` blocks. | Zero risk. |
| **Gemma 4 12B dense** | No thinking mode. | Zero risk. |
| **Gemma 4 26B-A4B** | Has thinking; can leak into `reasoning_content`. | Pass `enable_thinking=false`. |

---

## MoE-vs-dense at near-equal active params

Published anchor: Qwen's own positioning — **"30B-A3B for speed; 32B for quality."** Dense 32B beats 30B-A3B on raw bench quality at the same VRAM (~3-5 IFEval points, ~5 MMLU-Pro points). Same expected for Gemma 4: dense 31B > MoE 26B-A4B on per-token quality; 26B-A4B wins ~3-4× throughput.

For Pass B's volume (80-120 calls), **MoE is the right pick IF JSON adherence holds** — the IFEval gap is small enough not to matter for a constrained-output task.

---

## VRAM at Q4_K_M (cross-checked unsloth / lmstudio-community / willitrunai)

| Model | Q4_K_M weights | KV @ 8K ctx | Total | Fits 16 GB CUDA? |
|---|---|---|---|---|
| Gemma 4 12B dense | 7.6 GB | ~1.5 GB | **~9.1 GB** | ✅ huge headroom |
| gpt-oss-20b (MXFP4 native) | 12.1 GB | ~1.5 GB | **~13.6 GB** | ✅ comfortable |
| Qwen3.6-27B dense Q3_K_M | ~13.6 GB | ~1.8 GB | ~15.4 GB | Tight; OK with small ctx |
| Qwen3-30B-A3B-Instruct-2507 Q4 | ~16.8 GB | ~1.8 GB | ~18.6 GB | ❌ Vulkan pool |
| Qwen3.6-27B dense Q4_K_M | 17.5 GB | ~1.8 GB | ~19.3 GB | ❌ Vulkan pool |
| Gemma 4 26B-A4B Q4 | ~18 GB | ~1.5 GB | ~19.5 GB | ❌ Vulkan pool |
| Qwen3.6-35B-A3B Q4 | 22.1 GB | ~1.8 GB | ~23.9 GB | ❌ Vulkan pool (fits 28 GB) |
| Gemma 4 31B dense Q4 | 19.9 GB | ~1.8 GB | ~21.7 GB | ❌ Vulkan pool |

---

## Known issues (llama.cpp / LM Studio, as of mid-2026)

- **gpt-oss-20b**: MoE FFN weights MUST stay MXFP4 (further quantizing degrades sharply); K/V cache quantization hurts; `--jinja` flag REQUIRED for harmony format; Vulkan backend has open Jinja-template failures on the 120b variant (20b less affected).
- **Qwen3 hybrid family**: `enable_thinking=false` ignored across 5+ llama.cpp issues; root cause is chat-template path. **Only fix is `-Instruct-2507` variants** OR the LM Studio app-side toggle (verified per-model).
- **Gemma 4**: Open llama.cpp issue [#24085](https://github.com/ggml-org/llama.cpp/issues/24085) — `gemma-4-12B-it-GGUF` floating-point crash in vision path; **text-only path appears unaffected** (Gemma 3 text-only worked while vision broke — same pattern expected for Gemma 4). Unsloth `#5070` tokenizer special-token export bug affects only user-fine-tuned exports, not stock GGUFs.

---

## Final 3-tier text-slot pick

| Tier | Pick | Rationale |
|---|---|---|
| **Speed** | **`google/gemma-4-12b`** Q4_K_M | 7.6 GB weights → 9 GB total. IFEval 88.9 (best small JSON emitter). No thinking mode = zero leak risk. Best for Pass B's 80-120 calls. Already installed. |
| **Balanced** | **`openai/gpt-oss-20b`** MXFP4 at `Reasoning: low` | 13.6 GB total fits CUDA single-card. Native MXFP4 = full quality. Low adds ~30% tokens vs pure non-reasoner — acceptable for Pass B; flip to `Medium` for Pass D. Apache 2.0 = clean. Already installed. |
| **Quality** | **`Qwen3-30B-A3B-Instruct-2507`** Q4 (download) | Structurally no thinking — sidesteps BUG 20/57's root cause. MoE 3B active = fast even on Vulkan pool (~30-40% throughput hit). Strongest LiveBench/MMLU-Pro per VRAM dollar. |

> [!warning] Hard rule
> **Never deploy any hybrid-mode Qwen3 / Qwen3.5 / Qwen3.6 for Pass B** unless you've verified the LM Studio app-side toggle works AND confirmed no `reasoning_content` leaks under load. The upstream llama.cpp bug is unresolved. Either use Instruct-2507 variants (clean) or use Gemma 4 / gpt-oss instead.

---

## Sources

Primary:
- [OpenAI: Introducing gpt-oss](https://openai.com/index/introducing-gpt-oss/)
- [HuggingFace: openai/gpt-oss-20b](https://huggingface.co/openai/gpt-oss-20b)
- [HuggingFace: Welcome Gemma 3 blog](https://huggingface.co/blog/gemma3)
- [arXiv: Gemma 3 Technical Report](https://arxiv.org/html/2503.19786v1)
- [arXiv: Qwen3 Technical Report](https://arxiv.org/pdf/2505.09388)
- [arXiv: GPT-OSS-20B Deployment Analysis](https://arxiv.org/pdf/2508.16700)
- [arXiv: JSONSchemaBench](https://arxiv.org/abs/2501.10868)
- [The Register: Alibaba acknowledges Qwen3 hybrid thinking was a mistake](https://www.theregister.com/2025/07/31/alibaba_qwen3_hybrid_thinking/)
- [Qwen Blog](https://qwenlm.github.io/blog/qwen3/)

llama.cpp issues (enable_thinking broken):
- [#13160](https://github.com/ggml-org/llama.cpp/issues/13160)
- [#13189](https://github.com/ggml-org/llama.cpp/issues/13189)
- [#20182](https://github.com/ggml-org/llama.cpp/issues/20182)
- [#20409](https://github.com/ggml-org/llama.cpp/issues/20409)
- [#22255](https://github.com/ggml-org/llama.cpp/issues/22255)
- [#24085 Gemma 4 12B vision crash](https://github.com/ggml-org/llama.cpp/issues/24085)
- [discussion #15396 gpt-oss llama.cpp guide](https://github.com/ggml-org/llama.cpp/discussions/15396)

Secondary:
- [InsiderLLM: Structured Output Local LLMs](https://insiderllm.com/guides/structured-output-local-llms/)
- [LiveBench leaderboard](https://llm-stats.com/benchmarks/livebench)
- [llm-stats Gemma 3 compare](https://llm-stats.com/models/compare/gemma-3-12b-it-vs-gemma-3-27b-it)
- [unsloth Qwen3 docs](https://unsloth.ai/docs/models/tutorials/qwen3-how-to-run-and-fine-tune)
- [Artificial Analysis: Gemma 3 27B](https://artificialanalysis.ai/models/gemma-3-27b)

---

## Related

- [[concepts/vlm-comparison-2026-06]] — companion vision-slot head-to-head
- [[concepts/model-split]] — text/vision slot config; tier table sourced from here
- [[concepts/bugs-and-fixes]] — BUG 57 (now deeper-rooted) + BUG 20 (token exhaustion)
- [[entities/qwen35]] — current text_model (qwen3.5-9b); multimodal verified by LM Studio
- [[entities/gemma4]] — recommended speed/IFEval text pick (also current vision_model)
