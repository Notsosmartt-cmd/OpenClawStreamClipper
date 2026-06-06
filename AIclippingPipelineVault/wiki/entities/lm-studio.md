---
title: "LM Studio"
type: entity
tags: [inference-server, llm, windows, gpu, openai-compatible, infrastructure, hub]
sources: 0
updated: 2026-06-04
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

## Pipeline ↔ LM Studio interaction (native bare metal — current)

> [!note] This is the authoritative, current spec
> As of the [[concepts/bare-metal-windows]] migration (2026-06-04) the pipeline
> runs natively (no Docker), so the URL is **`http://localhost:1234`**
> (`config/models.json::llm_url`; the dashboard rewrites any
> `host.docker.internal` → `localhost`). The pipeline talks to LM Studio **two
> ways**: OpenAI-compatible **HTTP** for inference, and the **`lms` CLI** for
> model lifecycle (load/unload). The Docker-era sections lower down are kept for
> history.

### 1. Inference — HTTP `/v1/chat/completions`

Every text call (Stage 3 classify, Stage 4 Pass B/Pass D) and vision call
(Stage 6) is a `POST /v1/chat/completions`. `scripts/lib/lmstudio.py::chat()` is
a shared minimal client (used by the grounding judge); the stage modules
(`stage3_segments.py`, `stage4_moments.py::call_llm`, `stage6_vision.py::_vision_call`)
keep their own tuned retry / token-budget / JSON-extraction logic — too much
blast radius to unify.

- **Payload**: `{model, messages, stream:false, temperature, max_tokens, chat_template_kwargs:{enable_thinking:false}}`. Pass-B prompts are also prefixed with `/no_think`.
- **Response parse**: `choices[0].message.content`; when that is empty, fall back to `message.reasoning_content` (thinking models stash the answer there); then strip `<think>…</think>`. See the reasoning_content note below.
- **Fail-fast**: 3 consecutive network-shaped failures (`timed out`, `Connection refused`, `Errno 101/111`, …) trip the per-stage streak guard and abort that LLM layer (Pass B / Stage 6) — keyword/transcript baselines still flow through, so the run still produces clips. See [[concepts/bugs-and-fixes#BUG 32]].

### 2. Model availability — HTTP `GET /v1/models`

Returns the **downloaded** models (not only the loaded ones), OpenAI shape
`{"data":[{"id":…}]}`. `verify_models()` (`scripts/pipeline/common.py`) hits this
once at startup and **aborts with exit 2** if any configured ID
(`text_model` / `vision_model` / `text_model_passb` / `vision_model_stage6`) is
missing — saves hours of HTTP-400 fallbacks from a typo. Skipped for `--list`.

### 3. Model lifecycle — the `lms` CLI (load/unload)

The pipeline assumes **one model in VRAM at a time** (16 GB GPU). It unloads /
loads at stage boundaries in `scripts/pipeline/common.py`:

| Point | Action |
|---|---|
| Stage 2 (transcription) | unload **all** LLMs so Whisper gets the GPU |
| Stage 3 (segments) | load `text_model` |
| Stage 4 (Pass B) | if `text_model_passb != text_model`, unload+load to swap |
| Stage 6 (vision) | if `vision_model_stage6 != text_model_passb`, swap to vision model (**skipped** in the unified config where text == vision) |
| Stage 7 (captions) | unload the vision model before Whisper |

> [!warning] Use the `lms` CLI — the REST unload endpoint 404s on some versions
> LM Studio's REST unload path is **version-dependent**: **0.4.14 returns HTTP
> 404 for `/api/v1/models/unload`**, so REST-based unloads silently no-op and
> models strand in VRAM. Symptom: two models co-resident (e.g. `qwen3.6-27b`
> 17 GB + `qwen3.5-9b` 6 GB = 24 GB on a 16 GB card → spills to system RAM →
> very slow). The pipeline therefore **prefers the bundled `lms` CLI**:
> - `lms ps` — list loaded model identifiers
> - `lms load <model> -c <ctx> -y --ttl <s>` — load with context length, non-interactive, idle TTL
> - `lms unload <id>` / `lms unload --all`
>
> It falls back to the REST API (`POST /api/v1/models/{load,unload}`) only when
> `lms` is not found. `lms` is located via PATH or `~/.cache/lm-studio/bin/lms.exe`.

### 4. Pre-load, JIT, and idle TTL

- **`load_model()` is an optimization** — it pre-loads the next model so the
  first inference call doesn't pay the load latency. It **skips when `lms ps`
  shows the model already resident** (no duplicate instances). A heartbeat thread
  touches the stage marker during the (blocking) load so the dashboard's BUG-31
  staleness gate can't trip.
- **JIT**: LM Studio auto-loads a model on the first chat request if it isn't
  loaded, so even a failed pre-load still works (just slower on the first call).
- **Idle TTL**: `lms load --ttl` (env `CLIP_MODEL_TTL`, default **3600 s**) is
  set on every load so an abandoned/crashed run's model auto-evicts instead of
  stranding VRAM. Belt-and-suspenders: enable LM Studio's own
  **"Idle TTL and Auto-Evict"** (or set JIT *max loaded models = 1*).

### 5. Thinking models = the main speed lever

The configured `qwen/qwen3.6-27b` (and `qwen3.5-35b-a3b`) have thinking only
*partially* disableable in LM Studio → thousands of reasoning tokens per call →
slow Pass B / vision (observed: one Pass-B call "Reasoned for 70.77 s"). The
much smaller **`qwen3.5-9b`** runs with ≈0 reasoning tokens and is dramatically
faster. The pipeline sends `chat_template_kwargs:{enable_thinking:false}` + the
`/no_think` prefix — honored by 9B / Gemma, ignored by the big thinking models
(give those `max_tokens` headroom and rely on the `reasoning_content` fallback).
**Model choice is a speed/quality tradeoff** set in `config/models.json`; for
fast iteration use the 9B, for best moment quality use the 27B/35B. See the
"Thinking mode" table below.

### 6. Connectivity

- Inference URL `http://localhost:1234`. `check_lm_studio()` caches the
  `/v1/models` probe for 30 s to avoid flooding LM Studio's logs.
- `start.ps1` polls `GET /v1/models` (30 × 2 s) before starting the gateway and
  warns (non-fatal) if unreachable.

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

> [!warning] Stale KV table removed (2026-06-06) — use the GGUF-exact tooling
> An older table here gave generic per-context KV figures (e.g. "8192 → ~4 GB → ~9 GB default"). Those were flat-rate estimates and were **wrong by up to 11×** for sliding-window models like Gemma. KV-cache size is now computed exactly per model from GGUF metadata — see [[concepts/vram-context-tooling]] and the corrected per-model table + per-stage `max_tokens` floor in [[concepts/vram-budget]]. The pipeline default is **32768** (covers Pass B's ~14K peak with 2× margin); 8192 is too small (Pass B truncation — [[concepts/bugs-and-fixes]] BUG 61).
>
> Run `python scripts/lib/model_registry.py recommend <model> <pool_mb>` or `logtool vram` for the live, exact recommendation on the current hardware.

> [!note] If the model is already loaded with a different context length, LM Studio will return an error and the pipeline will log a warning then continue (using the existing loaded context). To change context length, unload the model in LM Studio first.

---

## Model unloading

> [!warning] Superseded — see "Pipeline ↔ LM Studio interaction" above
> On bare metal the pipeline unloads via the **`lms` CLI** (`lms unload <id>`),
> because LM Studio 0.4.14 returns HTTP 404 for the REST path below. The REST
> call is now only a fallback when `lms` isn't installed.

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

## GPU / engine configuration — operator-controlled, project-agnostic

GPU assignment, the inference **engine** (CUDA / Vulkan / ROCm / CPU), and the multi-GPU **strategy** are all LM Studio settings. **The pipeline cannot and does not control them** — it stays engine-agnostic and works with whatever the operator has set (confirmed 2026-06-06; see [[concepts/vram-context-tooling]] §"Can the pipeline control the engine").

What's controllable, and by whom:

| Control | Where | Project can set it? |
|---|---|---|
| Offload **ratio** (GPU vs CPU) | `lms load --gpu <off\|max\|0..1>` | Yes, but the pipeline leaves it at LM Studio's auto default |
| Inference **engine** (Vulkan ⟷ CUDA ⟷ ROCm) | `lms runtime select <alias>` or GUI → Runtime | **No** — global app setting; not a per-API-call parameter |
| Multi-GPU **strategy** ("Split evenly" / "Priority order") | GUI → Hardware → Strategy | **No** — GUI only |
| Per-model GPU assignment | GUI model-load dialog | **No** |

`lms runtime ls` shows installed engines with the selected one marked `✓` (dev box: Vulkan selected across both the RTX 5060 Ti + RX 6700 XT; CUDA engines installed but unselected). Switching to a CUDA engine would force NVIDIA-only (true CUDA speed) but cap at ~16 GB — a tradeoff the operator makes in LM Studio, not something the pipeline touches.

For best results regardless of engine:
- Pre-load the configured model (the pipeline does this via `lms load`), or rely on JIT (adds first-call latency).
- Set context to **32K** (the pipeline's workload size) so a CUDA-fittable model isn't needlessly pushed onto a multi-GPU pool by an oversized KV cache — see [[concepts/vram-budget]] §"Why bigger context ≠ better clips".
- Use a long TTL / "Keep model loaded" to avoid reloads between stages.

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
- [[concepts/vram-context-tooling]] — the GGUF-exact VRAM/context recommendation subsystem + `lms` CLI control findings
- [[concepts/deployment]] — setup instructions including LM Studio startup
