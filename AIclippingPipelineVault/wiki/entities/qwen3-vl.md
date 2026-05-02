---
title: "qwen3-vl:8b (retired)"
type: entity
tags: [model, vision, retired, qwen, historical, infrastructure, stage-6]
sources: 2
updated: 2026-04-22
---

# qwen3-vl:8b — retired

> [!warning] Retired April 2026
> This dedicated vision model is no longer used by the pipeline. Stage 6 now calls the same multimodal model used for Stages 3–4 (Gemma 4 `gemma-4-26b-a4b` or Qwen 3.5 `qwen3.5-9b` / `qwen3.5-35b-a3b`, both of which ship with built-in vision and proper thinking-token budgeting). The unified-model design skips the Stage 5→6 VRAM swap entirely.
>
> This page is kept as historical context for debugging older diagnostics and for anyone running a custom config where the vision model is still dedicated. The dashboard still allows selecting `qwen/qwen3-vl-8b` or `qwen/qwen2.5-vl-7b` as the vision model if you prefer a dedicated one.

Alibaba's third-generation vision-language model. Previously used in Stage 6 (Vision Enrichment) to analyze video frames, generate clip titles/descriptions, and boost scores for visually interesting moments.

Quantization: default Ollama GGUF. VRAM: ~11.1GB. Previously served by [[entities/ollama]] (itself retired — see [[entities/lm-studio]]).

---

## Role: Vision Enrichment (Non-Gatekeeping)

> [!warning] Critical design decision: vision can only help, never hurt
> Vision enrichment is **non-gatekeeping**. Every moment that survived Stage 4 detection **will be rendered**, regardless of vision score. The vision model can only boost scores upward — it cannot eliminate candidates.
>
> **Why**: Livestream frames are often visually uninteresting (a face at a desk, a chat overlay, a dark room) even when the *audio* content is genuinely clip-worthy. Making vision a gatekeeper eliminated 90%+ of valid moments in early testing.

Score blending (vision is additive):
- Vision score ≥ 7: transcript score + 2 (capped at 10)
- Vision score ≥ 5: transcript score + 1
- Vision score < 5: transcript score unchanged
- If vision fails entirely (bad JSON, timeout, model error): transcript data used as-is

---

## What the model receives

- The **middle 2 frames** of the 6 extracted for each moment (most representative)
- Stream context from Stage 3 profile: dominant type, current segment type, detection reason
- A prompt asking for: `{score: 1-10, category, title: "viral clip title", description: "one sentence"}`

The stream context makes vision prompts more accurate — the model knows whether it's analyzing a gaming clip vs. an IRL moment vs. a reaction.

---

## Thinking model handling

qwen3-vl:8b is a **thinking model** that requires special handling:

- Must be called with `think: true` and `num_predict >= 600`
- Internal reasoning consumes ~300–500 tokens before producing output (~100–200 content tokens)
- The pipeline's `call_ollama()` function detects when a model exhausts `num_predict` on thinking tokens (returns empty content) and **automatically retries with a larger budget**

If called without `think: true` or with insufficient `num_predict`, the model produces degraded output.

---

## Timeout protection

Stage 6 has two timeout layers:
- **20-minute total stage timeout**: if the entire vision enrichment takes more than 20 minutes, the stage aborts and clips proceed with transcript-only scores
- **90-second per-moment timeout**: each individual frame analysis is bounded

This prevents the pipeline from hanging indefinitely if Ollama is slow or unresponsive.

---

## VRAM usage

~11.1GB. The pipeline unloads `qwen3.5:9b` before Stage 6 and loads `qwen3-vl:8b`. After Stage 6, it unloads the vision model to make room for Whisper in Stage 7.

```
Stage 6: Unload qwen3.5:9b → Load qwen3-vl:8b (~11.1GB) → Vision enrichment
Stage 7: Unload qwen3-vl:8b → Load Whisper (~6-7GB) → Batch captions
```

See [[concepts/vram-budget]].

---

## Why qwen3-vl over alternatives

It's the smallest Qwen vision model that produces usable frame analysis. Earlier models produced too many parsing errors or insufficient quality descriptions for clip titles.

> [!warning] Ollama vision inference works correctly for qwen3-vl
> Note: `qwen3.5:9b` (the text model) also has vision capabilities, but its GGUF vision inference is **broken in Ollama** as of early 2026. The pipeline routes all vision tasks to `qwen3-vl:8b` exclusively.

---

## Related
- [[entities/ollama]] — serves this model
- [[entities/qwen35]] — text model used alongside this one; vision broken in its Ollama GGUF
- [[concepts/clipping-pipeline]] — Stage 6
- [[concepts/vision-enrichment]] — full Stage 6 design and logic
- [[concepts/vram-budget]] — memory orchestration
