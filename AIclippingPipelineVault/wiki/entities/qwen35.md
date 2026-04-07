---
title: "qwen3.5:9b"
type: entity
tags: [model, llm, alibaba, qwen, reasoning, pipeline]
sources: 2
updated: 2026-04-07
---

# qwen3.5:9b

Alibaba's Qwen 3.5 9B parameter model. Used as the **pipeline text model** in the stream clipper — handles segment classification (Stage 3) and LLM moment detection (Stage 4 Pass B).

Not the same as the Discord agent model. See [[entities/qwen25]] for the Discord bot model.

Quantization: default Ollama GGUF. VRAM: ~11.2GB at 32K context. Served by [[entities/ollama]].

---

## Role: Pipeline text analysis

**Stage 3 — Segment classification:**
- Classifies each 10-minute transcript chunk into one type: `gaming`, `irl`, `just_chatting`, `reaction`, or `debate`
- Uses a cheap prompt with `num_predict=10` — outputs a single word
- Very fast (~1 second per chunk)

**Stage 4 Pass B — LLM moment analysis:**
- Analyzes 5-minute transcript chunks with 30-second overlap
- Segment-specific prompts tailored to the classified stream type
- Looks for: setup+payoff, storytelling, situational irony, social dynamics, quotable moments
- Returns JSON: `[{time: "MM:SS", score: 1-10, category, why}]`
- Lower detection threshold (score 3–5 included) — the selection algorithm makes the final call

> [!note] think=false required
> Must be called with `think=false`. When thinking mode is enabled, the 9B model exhausts its token budget on internal reasoning and never produces output. This is a known issue with the current Ollama build for this model.

---

## Why not use qwen3.5:9b for the Discord bot?

The Discord agent uses [[entities/qwen25]] (qwen2.5:7b) instead of qwen3.5:9b because:
- Small models (7B) with minimal system prompts produce more **consistent structured tool calls** (JSON)
- qwen3.5:9b, despite being more capable for analysis, tends to describe what it wants to do instead of making the tool call
- For the pipeline's analysis tasks (not tool calling), qwen3.5:9b's superior reasoning matters; for Discord dispatch, reliability matters more

qwen3.5:9b found 3 contextual moments in benchmark tests where qwen2.5:7b found 0 — making it significantly better for moment detection. But it's ~2x slower per call.

---

## Segment-aware prompting

Different system prompts are used depending on the classified segment type:

| Segment type | Detection focus |
|---|---|
| `gaming` | Clutch plays, epic wins/losses, rage quits, skill moments |
| `irl` | Funny stories, emotional moments, surprising encounters |
| `just_chatting` | Hot takes, funny stories, emotional vulnerability, audience interaction |
| `reaction` | Strong reactions, controversial takes, emotional responses |
| `debate` | Persuasive arguments, heated exchanges, mic-drop moments |

Style-aware hints from the `--style` flag are appended to the prompt to bias detection.

---

## Context window management

- Full context: 262,144 tokens (Qwen 3.5 architecture)
- Pipeline caps to: **32K tokens** via `OLLAMA_CONTEXT_LENGTH=32768`
- VRAM at 32K context: ~11.2GB

The 32K cap is deliberate — transcript chunks are well within this limit, and a larger context would push VRAM past 16GB.

---

## VRAM orchestration

qwen3.5:9b stays loaded from Stage 3 through Stage 4:
```
Stage 3: Load qwen3.5:9b (~11.2GB) → Classify segments → Keep loaded
Stage 4: qwen3.5:9b still loaded → LLM moment analysis
Stage 6 prep: Unload qwen3.5:9b → Load qwen3-vl:8b
```

See [[concepts/vram-budget]].

---

## Known issues

> [!warning] Vision inference broken in Ollama (2026)
> Despite qwen3.5:9b's architecture supporting vision via early fusion, its GGUF multimodal projector is not handled correctly by Ollama. All vision tasks are routed to [[entities/qwen3-vl]] instead.

---

## Related
- [[entities/qwen25]] — the Discord agent model (qwen2.5:7b)
- [[entities/qwen3-vl]] — the vision model used in Stage 6
- [[entities/ollama]] — serves this model
- [[concepts/clipping-pipeline]] — Stages 3 and 4
- [[concepts/segment-detection]] — Stage 3 in detail
- [[concepts/highlight-detection]] — Stage 4 in detail
- [[concepts/vram-budget]] — memory orchestration
