---
title: "LM Studio"
type: entity
tags: [inference-server, llm, windows, gpu, openai-compatible, infrastructure, hub]
sources: 0
updated: 2026-04-19
---


# LM Studio

Native Windows application for running local LLM models. Replaced [[entities/ollama]] as the LLM inference backend for this project as of 2026-04-18.

---

## Role in the system

- Serves [[entities/qwen35]] (`qwen/qwen3.5-9b`) for **both** text (Stages 3–4) **and** vision (Stage 6) — this is the recommended setup, avoiding an unnecessary VRAM swap between stages
- Alternative vision models: `qwen/qwen3-vl-8b`, `qwen/qwen2.5-vl-7b` (set `vision_model` in dashboard if preferred)
- Exposes an **OpenAI-compatible HTTP API** on port 1234
- Runs natively on Windows — accessed from the Docker container via `http://host.docker.internal:1234`

> [!note] LM Studio model ID format
> LM Studio uses `organization/model-name` IDs (e.g., `qwen/qwen3.5-9b`). These must match exactly in `config/models.json` and `config/openclaw.json`. The dashboard's Models panel calls `/v1/models` live to populate the picker.

---

## Why LM Studio replaced Ollama

| Problem with Ollama-in-Docker | LM Studio solution |
|---|---|
| WSL2 Vulkan drivers required for AMD GPU | Runs natively; native NVIDIA+AMD support |
| Vulkan ICD injection often failed silently → CPU inference | No Vulkan required; uses CUDA+ROCm/DirectML natively |
| CUDA+Vulkan couldn't easily share VRAM across both GPUs | LM Studio 0.3.14+ has native multi-GPU support |
| `docker-compose.yml` needed complex GPU mounts | Single `stream-clipper` container; LM Studio is separate |

---

## API endpoints used

| Endpoint | Method | Purpose |
|---|---|---|
| `/v1/models` | `GET` | List loaded models (OpenAI format: `{"data": [...]}`) |
| `/v1/chat/completions` | `POST` | Text and vision inference; supports `chat_template_kwargs` extension |
| `/api/v1/models/load` | `POST` | Load a model with specific `context_length` |
| `/api/v1/models/unload` | `POST` | Force-unload a model from VRAM (`{"instance_id": "model-id"}`) |

The pipeline uses the **OpenAI-compatible** endpoint (`/v1/chat/completions`). The LM Studio native endpoint (`/api/v1/chat`) is not used.

---

## Request format

Text inference (Stages 3–4):

```json
{
  "model": "qwen/qwen3.5-9b",
  "messages": [{"role": "user", "content": "<your prompt here>"}],
  "stream": false,
  "temperature": 0.3,
  "max_tokens": 3000,
  "chat_template_kwargs": {"enable_thinking": false}
}
```

Vision inference (Stage 6):

```json
{
  "model": "qwen/qwen3.5-9b",
  "messages": [{"role": "user", "content": [
    {"type": "text", "text": "<your prompt here>"},
    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,<b64>"}}
  ]}],
  "stream": false,
  "temperature": 0.3,
  "max_tokens": 2000,
  "chat_template_kwargs": {"enable_thinking": false}
}
```

Response parsing:

```python
msg = result["choices"][0]["message"]
content = msg.get("content") or ""
finish_reason = result["choices"][0].get("finish_reason", "?")

if not content and finish_reason == "stop":
    # Fallback for 35B+ models that ignore chat_template_kwargs:
    # they put the answer in reasoning_content with empty content.
    content = msg.get("reasoning_content", "")

# Strip stray <think> tags (safety net)
content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
```

> [!note] `reasoning_content` fallback
> When `content` is empty and `finish_reason == "stop"` (model finished naturally), the pipeline falls back to `reasoning_content` at all three call sites (Stages 3, 4, 6). This handles models that always route answers through `reasoning_content`. Only `finish_reason == "length"` (token budget exhausted mid-think) triggers a retry — and the solution there is increasing `max_tokens`, not retrying.

---

## Thinking mode: 9B vs 35B-A3B behavior

These two models behave **oppositely** regarding thinking:

| Property | `qwen3.5-9b` | `qwen3.5-35b-a3b` |
|---|---|---|
| Architecture | Dense 9B | Sparse MoE: 35B total, ~3B active |
| Thinking default | **Disabled** by default | **Enabled** by default |
| `chat_template_kwargs: {"enable_thinking": false}` | Works (but is a no-op — already disabled) | **Does not work** in LM Studio's endpoint |
| Typical reasoning tokens | ~0 (thinking is off) | ~3,000–6,000 per call |
| Required `max_tokens` for Stage 4 | ~1,000–2,000 | **8,000** |
| Required `max_tokens` for Stage 6 | ~500–1,000 | **6,000** |

**Why `chat_template_kwargs` fails on 35B**: LM Studio's OpenAI-compatible `/v1/chat/completions` endpoint does not correctly forward this parameter to the 35B model's chat template. The model always enters its reasoning phase regardless. This is a confirmed LM Studio limitation (bug tracker issue #1559). The model's default thinking budget is ~8,192 tokens.

**What does NOT work**:
- `/no_think` user-message prefix — Qwen3 feature dropped in Qwen3.5
- `chat_template_kwargs: {"enable_thinking": false}` — forwarding fails for 35B in LM Studio
- System prompt instructions to skip thinking — ineffective on 35B

**The only working strategy for 35B**: Set `max_tokens` high enough for the full reasoning phase (~3,000–6,000 tokens) plus the answer. The model WILL think; give it room to finish. Think-tag stripping and `reasoning_content` fallback handle both output modes.

```json
{
  "model": "qwen/qwen3.5-35b-a3b",
  "messages": [{"role": "user", "content": "...prompt..."}],
  "stream": false,
  "temperature": 0.3,
  "max_tokens": 8000,
  "chat_template_kwargs": {"enable_thinking": false}
}
```

> [!warning] LM Studio UI toggle
> "Separate reasoning_content and content" in LM Studio's inference settings controls whether thinking appears in `reasoning_content` (toggle ON) or inline in `content` as `<think>` tags (toggle OFF). The pipeline handles both. This toggle does NOT affect whether the model thinks at all.

---

## Context length management

Context length is set at model load time via the LM Studio management API (distinct from the OpenAI-compatible `/v1/` endpoints):

```
POST /api/v1/models/load
{"model": "qwen/qwen3.5-9b", "context_length": 8192}
```

The pipeline calls this via `load_model()` before Stage 3 and (if text ≠ vision model) before Stage 6. The value comes from `CLIP_CONTEXT_LENGTH` env var, which is read from `config/models.json → context_length`.

**VRAM budget for KV cache** (9B model ~5 GB weights at Q4_K_M):

| context_length | KV cache VRAM | Total VRAM needed |
|---|---|---|
| 4 096 | ~2 GB | ~7 GB |
| 8 192 | ~4 GB | ~9 GB (default) |
| 16 384 | ~8 GB | ~13 GB |
| 32 768 | ~16 GB | ~21 GB |

> [!note] If the model is already loaded with a different context length, LM Studio will return an error and the pipeline will log a warning then continue (using the existing loaded context). To change context length, unload the model in LM Studio first.

---

## Model unloading

The pipeline unloads models between stages by calling:

```
POST http://host.docker.internal:1234/api/v1/models/unload
{"instance_id": "model-id"}
```

This is handled by the `unload_model()` bash function in `scripts/clip-pipeline.sh`. Failures are non-fatal (`|| true`) — if the model isn't loaded, the call is a no-op.

---

## Connectivity

- LM Studio listens on `0.0.0.0:1234` (or `localhost:1234` with LAN access enabled)
- The Docker container reaches the Windows host at `host.docker.internal` — set via `extra_hosts: ["host.docker.internal:host-gateway"]` in `docker-compose.yml`
- `scripts/entrypoint.sh` polls `GET /v1/models` on startup and warns (non-fatal) if LM Studio is unreachable

---

## GPU configuration

GPU assignment is managed through LM Studio's GUI — the user selects which GPU(s) to use per model load. The pipeline has no control over this. For best results:

- Load the text model (`qwen/qwen3.5-9b`) on the primary GPU before running the pipeline
- LM Studio's JIT loading will auto-load models on first request if not pre-loaded (adds latency)
- Enable "Keep model loaded" or use long TTL in LM Studio to avoid reload between pipeline stages

---

## Dashboard integration

`dashboard/app.py` calls:
- `GET /v1/models` to list available models (shown in Models panel with ⭐ guidance toward suggested models)
- `check_lm_studio()` to show LM Studio connectivity status in the status bar — **result is cached for 30 s** to avoid flooding LM Studio's logs (previously polled every 3 s)

---

## Related
- [[entities/qwen35]] — primary text model (pipeline stages 3–4)
- [[entities/qwen3-vl]] — vision model (stage 6)
- [[entities/qwen25]] — Discord agent model
- [[concepts/vram-budget]] — model VRAM and stage-by-stage orchestration
- [[concepts/deployment]] — setup instructions including LM Studio startup
