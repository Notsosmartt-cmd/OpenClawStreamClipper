---
title: "lmstudio.py — minimal LM Studio HTTP client"
type: entity
tags: [lmstudio, http-client, grounding, judge, module, text]
sources: 1
updated: 2026-05-01
---

# `scripts/lib/lmstudio.py`

A ~90-line HTTP client for LM Studio's OpenAI-compatible `/v1/chat/completions` endpoint. Introduced 2026-04-23 to back the grounding cascade's external-judge calls so the cascade module doesn't have to reimplement HTTP + JSON extraction + reasoning-content fallback from scratch. After the 2026-05-01 cascade simplification (MiniCheck + Lynx retired), it now backs the cascade's main-model LLM judge call.

**It is intentionally small.** The inlined LLM callers in stage modules (Stage 3 classify, Pass B `call_llm`, Stage 6 `_vision_call` closure) each have their own tuned retry / token-budget / parse logic that's been iterated over many bug fixes — bundling them all into this wrapper would be too much blast radius. For now, this module is **only** used by `grounding.llm_judge`.

---

## API

```python
from lmstudio import chat

reply = chat(
    prompt="You are a judge. Respond with JSON ...",
    model="google/gemma-4-26b-a4b",            # whatever CLIP_TEXT_MODEL resolves to
    url="http://host.docker.internal:1234",    # default from $CLIP_LLM_URL
    timeout=30.0,
    temperature=0.0,
    max_tokens=400,
    response_json=True,        # NO-OP since 2026-04-27 (BUG 33). Kept for API compat — caller-side JSON extraction handles freeform output.
)
# → cleaned string, or None on any failure
```

Returns `None` on any network / HTTP / protocol error so callers can degrade cleanly. The grounding cascade's judge uses this — a `None` reply means "judge unavailable, fall back to the Tier 1 verdict".

**Behavior details:**
- Always sets `chat_template_kwargs: {enable_thinking: False}` — this module is designed for judge / format tasks, never reasoning-heavy ones.
- Strips `<think>...</think>` tags from the response.
- Falls back to `message.reasoning_content` when `content` is empty (Qwen3.5-35B-A3B emits its answer there when it finishes naturally).
- Non-blocking in the module-level sense: each call is synchronous, but failures don't raise — they return `None` and print a single-line `[LMSTUDIO] call failed: ...` stderr log.
- **`response_format` no longer forwarded** (2026-04-27, [[concepts/bugs-and-fixes#BUG 33]]). Pre-fix, `response_json=True` added `response_format: {type: json_object}` for stricter JSON output. Gemma 4 (and several other non-Qwen models) reject that field with HTTP 400 — the judge then silently disabled itself, but spammed the log. Caller-side JSON extraction (`text.find("{")` / `rfind("}")` + `json.loads`) is robust enough on its own.

---

## CLI mode

```
python3 scripts/lib/lmstudio.py \
    --prompt 'Hello' \
    --model qwen/qwen3.5-9b \
    --url http://host.docker.internal:1234
```

Useful for quick smoke-testing that LM Studio is reachable from inside the container.

---

## Why not use `openai` or `requests`

- `openai` adds a ~12 MB dependency for what is 30 lines of `urllib.request`.
- `requests` is only useful if we were doing streaming, retries, or connection pooling — none of which this wrapper needs.
- `urllib.request` is stdlib, so this module has zero install footprint on top of the base Python image.

---

## Related

- [[entities/grounding]] — `llm_judge` calls this module.
- [[entities/lm-studio]] — the server this module talks to.
- [[concepts/vision-enrichment]] — Stage 6 does NOT use this module; it has its own inlined urllib call with richer fallback logic (image payloads, 6000-token budget, reasoning_content handling tuned for the 35B model).
