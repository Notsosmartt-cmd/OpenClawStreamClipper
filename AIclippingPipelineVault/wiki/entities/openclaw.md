---
title: "OpenClaw"
type: entity
tags: [agent-framework, orchestration, discord, nodejs, infrastructure, interface, hub]
sources: 2
updated: 2026-04-17
---

# OpenClaw

An open-source autonomous AI agent framework (Node.js) that serves as the orchestration layer for the stream clipper. It maintains the Discord bot connection, interprets natural-language commands, and invokes the pipeline script via the `exec` tool.

---

## Role in the system

- Runs as a **persistent daemon** inside the `stream-clipper` container
- Connects to Discord through the official bot gateway API (WebSocket)
- Listens for messages in configured channels
- Routes inference requests to [[entities/ollama]] at `http://ollama:11434`
- Uses the `exec` tool to run `clip-pipeline.sh` inside the container
- Polls the process for completion, then relays the JSON summary to Discord

The [[entities/qwen25]] model (`qwen2.5:7b`) powers the agent's responses and tool-calling.

---

## Configuration: `config/openclaw.json`

Key sections:

| Section | Purpose |
|---|---|
| `models.providers.ollama` | Points to `http://ollama:11434`; lists all models with capabilities, context, compat flags |
| `agents.defaults.model` | Primary model: `qwen2.5:7b`; fallbacks |
| `agents.defaults.compaction` | Token compaction settings (8K reserve, 6K recent) |
| `channels.discord` | Bot behavior: `historyLimit: 10`, streaming settings |
| `session.reset` | `idleMinutes: 60` — fresh session after 1hr idle |
| `session.maintenance` | Prune after 7d, max 200 entries |
| `agents.defaults.heartbeat` | `every: "0m"` — heartbeat disabled |

**Compatibility flags** (required on all local models — prevents silent failures):
```json
"compat": {
  "supportsDeveloperRole": false,
  "supportsReasoningEffort": false
}
```

See [[concepts/context-management]] for full token management detail.

---

## Configuration: `config/exec-approvals.json`

Controls which shell commands the agent can run via the `exec` tool. Without this file, `exec` is not exposed to the model at all.

Default (allow all):
```json
{"*": {"allowlist": [{"pattern": "*"}]}}
```

If `exec` isn't exposed, the bot will describe commands instead of running them — a common failure mode.

---

## Agent behavior files

OpenClaw reads two workspace markdown files as its operating instructions:

**`workspace/AGENTS.md`** — The agent's core identity and rules:
- Only job: run `exec` when asked to clip
- Always use the `exec` tool — never just reply with text
- Keep Discord messages to 1–2 sentences
- Infer style and type from user's words
- Poll until pipeline completes, then report results

**`workspace/skills/stream-clipper/SKILL.md`** — Skill definition (version 3.2.0):
- Trigger phrases: clip, process, highlight, harvest, VOD, re-clip, list VODs, streamer name + action
- Exact JSON tool call templates for each scenario (named VOD, generic, list, with type hint)
- Style flag mapping from natural language
- Post-exec polling instructions

---

## The exec flow

1. User messages Discord: "clip the lacy stream"
2. [[entities/qwen25]] interprets intent, builds exec call:
   ```json
   {"tool": "exec", "command": "bash /root/scripts/clip-pipeline.sh --style auto --vod lacy 2>&1", "yieldMs": 5000}
   ```
3. OpenClaw runs the command inside the container
4. `exec` returns a session name (pipeline runs async)
5. Agent polls with `process` tool until complete
6. Reports back to Discord: clip count, titles, categories, scores

---

## Startup sequence

`entrypoint.sh` inside the container:
1. Waits for Ollama healthcheck to pass (Ollama must be ready before the agent starts)
2. Replaces `__DISCORD_BOT_TOKEN__` placeholder in `openclaw.json` from the `DISCORD_BOT_TOKEN` env var
3. Pulls Ollama models if not already present (`qwen3.5:9b`, `qwen2.5:7b`, `qwen3-vl:8b`)
4. Starts dashboard Flask app in background on port 5000
5. Starts OpenClaw gateway

---

## Fixing a non-responsive bot

If the bot stops calling `exec` (describes instead of doing):
```bash
# Clear stale sessions
docker exec stream-clipper bash -c "rm -f /root/.openclaw/agents/main/sessions/*.jsonl"
docker restart stream-clipper
# Wait ~15s for Discord reconnection
```

If it ran a hallucinated/broken command, run the pipeline manually:
```bash
docker exec -d stream-clipper bash -c "bash /root/scripts/clip-pipeline.sh --style auto --vod NAME"
```

---

## Related
- [[entities/qwen25]] — the model OpenClaw uses for Discord interactions
- [[entities/discord-bot]] — the interface OpenClaw operates
- [[entities/ollama]] — inference server OpenClaw calls
- [[entities/dashboard]] — the secondary interface (Flask, not OpenClaw)
- [[concepts/context-management]] — how token overflow is prevented
- [[concepts/clipping-pipeline]] — the workflow OpenClaw triggers
