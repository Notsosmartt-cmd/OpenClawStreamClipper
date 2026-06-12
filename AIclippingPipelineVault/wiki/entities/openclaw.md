---
title: "OpenClaw"
type: entity
tags: [agent-framework, orchestration, discord, nodejs, infrastructure, interface, hub]
sources: 2
updated: 2026-06-12
---

# OpenClaw

An open-source autonomous AI agent framework (Node.js) that serves as the orchestration layer for the stream clipper. It maintains the Discord bot connection, interprets natural-language commands, and invokes the pipeline via the `exec` tool.

> [!note] Bare-metal Windows (2026-06)
> Since the bare-metal port (2026-06-04), OpenClaw runs **natively on Windows**, not inside the `stream-clipper` Docker container, and routes inference to native **LM Studio** at `http://localhost:1234`, not Ollama. The agent model is now `qwen/qwen3.5-9b` (fallback `qwen/qwen3-vl-8b`), and `exec` runs `clip.cmd` (a wrapper over `scripts/run_pipeline.py`) rather than `bash clip-pipeline.sh`. The Docker/Ollama wording below is preserved as the legacy form. See [[concepts/bare-metal-windows]].

---

## Role in the system

- Runs as a **persistent daemon** (native Windows process; legacy: inside the `stream-clipper` container)
- Connects to Discord through the official bot gateway API (WebSocket)
- Listens for messages in configured channels
- Routes inference requests to the LM Studio provider at `http://localhost:1234/v1` (legacy: [[entities/ollama]] at `http://ollama:11434`)
- Uses the `exec` tool to run `clip.cmd` / `run_pipeline.py` (legacy: `clip-pipeline.sh` inside the container)
- Polls the process for completion, then relays the JSON summary to Discord

The agent's primary model is `qwen/qwen3.5-9b` (config key `agents.defaults.model.primary`); the original [[entities/qwen25]] (`qwen2.5:7b`) is the historical default.

---

## Configuration: `config/openclaw.json`

Key sections:

| Section | Purpose |
|---|---|
| `models.providers.lmstudio` | Points to `http://localhost:1234/v1`; lists models (`qwen/qwen3.5-9b`, `qwen/qwen3-vl-8b`, `qwen/qwen2.5-vl-7b`) with capabilities, context, compat flags. (Legacy: `models.providers.ollama` → `http://ollama:11434`.) |
| `agents.defaults.model` | Primary: `lmstudio/qwen/qwen3.5-9b`; fallback `qwen/qwen3-vl-8b` |
| `agents.defaults.compaction` | Token compaction settings (8K reserve, 6K recent) |
| `agents.defaults.timeoutSeconds` | `3600` — long-running pipeline ceiling |
| `tools.exec` | `host: gateway`, `security: full` — exec runs on the gateway with no allowlist gating |
| `channels.discord` | Bot behavior: `historyLimit: 10`, token, streaming `off` |
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

> [!note] Also gated inline (2026-06)
> Current `openclaw.json` additionally sets `tools.exec.security: "full"` and a `tools.deny` list (denies `read`/`edit`/`write`/`web_search`/`memory_*`/etc.) so the agent's surface is `exec` + `process` only. `exec-approvals.json` and the inline `tools.exec` config work together.

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
2. The agent model interprets intent, builds an exec call (per `workspace/skills/stream-clipper/SKILL.md`):
   ```json
   {"tool": "exec", "command": "clip.cmd --style auto --vod lacy 2>&1", "yieldMs": 5000}
   ```
   (Legacy Docker form: `bash /root/scripts/clip-pipeline.sh --style auto --vod lacy 2>&1`.)
3. OpenClaw runs the command (native Windows process; legacy: inside the container)
4. `exec` returns a session name (pipeline runs async)
5. Agent polls with `process` tool until complete
6. Reports back to Discord: clip count, titles, categories, scores

---

## Startup sequence

On bare-metal Windows (2026-06), OpenClaw is launched natively with `config/openclaw.json` (token already present in `channels.discord.token`); LM Studio must be running with the agent model loaded, and the dashboard Flask app runs separately on port 5001 (default; `DASHBOARD_PORT`/`PORT` override, auto-rolls forward if taken).

The legacy Docker `entrypoint.sh` inside the container did:
1. Waited for the Ollama healthcheck to pass (Ollama ready before the agent starts)
2. Replaced the `__DISCORD_BOT_TOKEN__` placeholder in `openclaw.json` from the `DISCORD_BOT_TOKEN` env var
3. Pulled Ollama models if not already present (`qwen3.5:9b`, `qwen2.5:7b`, `qwen3-vl:8b`)
4. Started the dashboard Flask app in background on port 5000
5. Started the OpenClaw gateway

---

## Fixing a non-responsive bot

If the bot stops calling `exec` (describes instead of doing), clear stale session `.jsonl` files and restart OpenClaw. On bare-metal the sessions live under `%USERPROFILE%\.openclaw\agents\main\sessions\`; the legacy Docker form is:
```bash
# legacy (Docker):
docker exec stream-clipper bash -c "rm -f /root/.openclaw/agents/main/sessions/*.jsonl"
docker restart stream-clipper
```

If it ran a hallucinated/broken command, run the pipeline manually:
```bash
# bare-metal:
clip.cmd --style auto --vod NAME
```

---

## Related
- [[entities/qwen25]] — the original Discord agent model (now superseded by `qwen3.5-9b`)
- [[entities/lm-studio]] — the native Windows inference server OpenClaw now calls
- [[entities/discord-bot]] — the interface OpenClaw operates
- [[entities/ollama]] — legacy inference server (pre bare-metal port)
- [[entities/dashboard]] — the secondary interface (Flask, not OpenClaw)
- [[concepts/bare-metal-windows]] — the native-Windows architecture OpenClaw runs under
- [[concepts/context-management]] — how token overflow is prevented
- [[concepts/clipping-pipeline]] — the workflow OpenClaw triggers
