---
title: "Context and Token Management"
type: concept
tags: [context, tokens, compaction, session, openclaw, qwen25]
sources: 2
updated: 2026-04-07
---

# Context and Token Management

The Discord bot ([[entities/openclaw]] running [[entities/qwen25]]) uses a 32K token context window. Without management, conversation history accumulates and crowds out the system prompt and tool descriptions — causing the model to describe actions instead of calling tools.

Configuration is in `config/openclaw.json`.

---

## The problem

When a 7B model's context fills up:
- System prompt + tool definitions start getting truncated
- Model loses its identity and behavior instructions
- Result: bot describes what it would do instead of calling `exec` ("I'll run the pipeline for you..." but never does)

---

## Token budget breakdown

For a typical clip request:

| Component | Tokens |
|---|---|
| System prompt + AGENTS.md + SKILL.md | ~3,000 |
| Tool definitions (exec, read, write, etc.) | ~2,000 |
| Discord history (last 10 messages) | ~1,000–3,000 |
| Reserved for output | ~8,192 |
| **Available for conversation** | **~18,000–20,000** |

The pipeline script runs as a subprocess via `exec` tool — its output doesn't consume the agent's context unless the bot explicitly reads it back.

---

## Configuration settings

### Compaction (`agents.defaults.compaction`)

```json
{
  "reserveTokens": 8192,
  "keepRecentTokens": 6000
}
```

- `reserveTokens: 8192` — keeps 8K tokens free for system prompt + next model output
- `keepRecentTokens: 6000` — when compaction triggers, preserves the most recent ~6K tokens of conversation; older history is summarized
- Compaction fires when context exceeds ~24K tokens (32K − 8K reserve)

### Session reset (`session.reset`)

```json
{
  "idleMinutes": 60
}
```

- After 60 minutes of no Discord messages, a fresh session starts with clean context
- Prevents stale conversations from carrying over between clipping sessions

### Session maintenance (`session.maintenance`)

```json
{
  "mode": "enforce",
  "pruneAfter": "7d",
  "maxEntries": 200
}
```

- `mode: "enforce"` — actively prunes old session data (not just warnings)
- `pruneAfter: "7d"` — deletes session files older than 7 days
- `maxEntries: 200` — caps stored session entries

### Discord history limit (`channels.discord`)

```json
{
  "historyLimit": 10
}
```

Only the last 10 Discord messages are loaded into context when processing a new message. Prevents channel history from consuming the entire context window.

### Heartbeat disabled

```json
{
  "every": "0m"
}
```

Heartbeat (periodic "Read HEARTBEAT.md" task) is disabled. When enabled, it would add noise to the context every 30 minutes.

---

## Model compatibility flags

All local Ollama models require these flags to prevent silent failures:

```json
"compat": {
  "supportsDeveloperRole": false,
  "supportsReasoningEffort": false
}
```

Without these, OpenClaw sends OpenAI-style `developer` role messages and `reasoning_effort` parameters that Ollama doesn't understand, causing API errors.

---

## Fixing a context-bloated bot

If the bot starts responding with text instead of running the pipeline:

```bash
# Clear stale sessions
docker exec stream-clipper-gpu bash -c "rm -f /root/.openclaw/agents/main/sessions/*.jsonl"
docker restart stream-clipper-gpu
# Wait ~15 seconds for Discord reconnection
```

---

## Related
- [[entities/qwen25]] — the Discord agent model with the 32K context window
- [[entities/openclaw]] — the agent framework that implements these settings
- [[entities/discord-bot]] — the interface where context issues manifest
