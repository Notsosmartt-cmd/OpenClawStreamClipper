---
title: "qwen2.5:7b"
type: entity
tags: [model, llm, alibaba, qwen, discord, agent, tool-calling, infrastructure, text]
sources: 2
updated: 2026-06-12
---

# qwen2.5:7b

> [!warning] Superseded as the agent model (2026-06-04)
> This is **no longer the live Discord agent model**. Since the bare-metal port, the agent's primary model is `qwen/qwen3.5-9b` (fallback `qwen/qwen3-vl-8b`) served by native **LM Studio** — see `config/openclaw.json` (`agents.defaults.model.primary`), [[entities/openclaw]], and [[entities/discord-bot]]. The historical rationale below (small model = reliable tool-calling) still explains *why* a smaller agent model is used; only the specific model and runtime moved. [[entities/ollama]] is also retired.

Alibaba's Qwen 2.5 7B parameter model. Was the **Discord bot agent model** — handled user interaction, style/type inference, and tool calling that triggers the pipeline.

Not the pipeline analysis model. See [[entities/qwen35]] for the text model used inside the pipeline.

VRAM: ~8.8GB. Was served by [[entities/ollama]] (both retired in favour of [[entities/lm-studio]] + `qwen/qwen3.5-9b`).

---

## Role: Discord agent

The Discord bot ([[entities/discord-bot]]) runs on `qwen2.5:7b` via [[entities/openclaw]]. Every time a user sends a message, this model:

1. Interprets the intent (does this trigger clipping? asking a question? listing VODs?)
2. Infers clip style from natural language (`--style funny`, `--style hype`, etc.)
3. Infers stream type hint from natural language (`--type irl`, `--type gaming`, etc.)
4. Extracts VOD name if mentioned (`--vod lacy`)
5. Calls the `exec` tool to run `clip-pipeline.sh` with the right flags
6. Polls for completion and reports results back

---

## Why qwen2.5:7b and not qwen3.5:9b?

| | qwen2.5:7b | qwen3.5:9b |
|---|---|---|
| Tool call reliability | High | Moderate |
| Moment detection quality | Lower | Much higher |
| Speed | ~2x faster | ~2x slower |
| VRAM | ~8.8GB | ~11.2GB |

For Discord dispatch, **reliability > capability**. Small models with minimal system prompts produce more consistent structured JSON tool calls. qwen3.5:9b occasionally describes what it wants to do instead of making the call.

For pipeline analysis (Stage 3, Stage 4), qwen3.5:9b's superior reasoning is essential — it found 3 contextual moments where qwen2.5:7b found 0.

---

## Context window

- Context: 32,768 tokens
- Token budget breakdown for a typical clip request:

| Component | Tokens |
|---|---|
| System prompt + AGENTS.md + SKILL.md | ~3,000 |
| Tool definitions | ~2,000 |
| Discord history (last 10 messages) | ~1,000–3,000 |
| Reserved for output (`reserveTokens`) | ~8,192 |
| Available for conversation | ~18,000–20,000 |

See [[concepts/context-management]] for compaction settings.

---

## VRAM

~8.8GB. This model is loaded by Ollama for Discord bot interactions. It's not used during the pipeline stages themselves — the pipeline uses `qwen3.5:9b` and `qwen3-vl:8b`.

Since `OLLAMA_MAX_LOADED_MODELS=1`, if the pipeline is actively running with `qwen3.5:9b` loaded, the agent can't respond to Discord messages with `qwen2.5:7b` at the same moment. In practice, the bot posts status updates before and after the pipeline, not during.

---

## Related
- [[entities/openclaw]] — runs on top of this model
- [[entities/discord-bot]] — the interface this model operates
- [[entities/qwen35]] — pipeline model, better for analysis
- [[concepts/context-management]] — compaction, history limits, session reset
