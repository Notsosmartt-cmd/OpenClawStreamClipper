---
title: "Stage-Specific Model Split — Phase 5.1"
type: concept
tags: [models, vram, phase-5, configuration, stage-4, stage-6, infrastructure]
sources: 2
updated: 2026-06-04
---

# Stage-Specific Model Split (Phase 5.1)

Per `ClippingResearch.md` §8.1 and §8.9: running a single multimodal model (Gemma 4 26B, Qwen 3.5-35B) for both Pass B text classification AND Stage 6 vision enrichment is wasteful — Pass B runs ~30× more often than Stage 6, and vision models pay the image-encoder cost on every call even when no image is involved.

Phase 5.1 adds **optional** per-stage model overrides in `config/models.json`. Backwards-compatible: when the overrides are `null` (the default), every stage uses the unified `text_model` / `vision_model` exactly as before.

---

## Configuration

`config/models.json`:

```json
{
  "text_model":          "google/gemma-4-26b-a4b",   // used by Stage 3 segment classify (always)
  "vision_model":        "google/gemma-4-26b-a4b",   // fallback for Stage 6 when override is null
  "text_model_passb":    null,                        // overrides text_model for Pass B only
  "vision_model_stage6": null,                        // overrides vision_model for Stage 6 only
  "whisper_model":       "large-v3-turbo",
  "llm_url":             "http://host.docker.internal:1234",
  "context_length":      32768
}
```

**Stage → model mapping:**

| Stage | Model env var (pipeline) | Source |
|---|---|---|
| Stage 3 segment classify | `TEXT_MODEL` | always `text_model` |
| Stage 4 Pass B moment detect | `TEXT_MODEL_PASSB` | `text_model_passb` ?? `text_model` |
| Stage 6 vision enrichment | `VISION_MODEL_STAGE6` | `vision_model_stage6` ?? `vision_model` |
| Stage 2 / 7 Whisper | `WHISPER_MODEL` | always `whisper_model` |

**Dashboard** (`dashboard/app.py`) forwards the overrides as env vars when set, both for `docker exec` and direct-subprocess launches. No UI changes — edit `config/models.json` directly to opt in.

---

## Active config — 16 GB rig (2026-06-04)

On the real deployment GPU (**RTX 5060 Ti, 16 GB** — see [[concepts/vram-budget]]), running the unified `qwen3.6-35b-a3b` for every LLM stage was doubly slow: at **22.1 GB (Q4_K_M) it does not fit 16 GB** (spills ~6 GB to CPU), and as a Qwen *thinking* build it burns ~3–6k reasoning tokens per Pass B call — the pipeline's API `enable_thinking:false` is **ignored** ([[concepts/bugs-and-fixes]] BUG 57; LM Studio's per-model toggle is the only lever, and even that is uncertain on dedicated thinking builds). One VOD took **135.8 min, Stage 4 alone = 67 min / 49%** ([[concepts/observability]]).

Switched to models that **fully fit 16 GB** (GPU-resident = fast, no CPU spill):

| Role (config key) | Model | Q4 size | Notes |
|---|---|---|---|
| `text_model` (Stage 3 + Pass B + Pass D) | **`qwen/qwen3.5-9b`** | 6.5 GB | non-thinking by default; kills the Stage-4 thinking tax |
| `vision_model` (Stage 6 + Vision Judge) | **`google/gemma-4-12b`** | 7.6 GB | multimodal; strong OCR. (`qwen3.5-9b` is also vision-capable → unified-both is the zero-swap alt) |

`text_model_passb` / `vision_model_stage6` left null → inherit the above. The two total **14.1 GB**, so LM Studio may hold both **co-resident** in 16 GB → no swap between the text and vision stages either.

**Which installed models fit 16 GB (5060 Ti alone, CUDA — fastest path):** ✅ gpt-oss-20b 12.1 (reasoning, dialable effort), gemma-4-12b 7.6, qwen3.5-9b 6.5, nemotron-4b 4.2 · the 27B/26B-A4B/31B/35B (17.5–22.1 GB) do NOT fit the NVIDIA card alone.

> [!note] Correction — dual-GPU Vulkan changes the VRAM math (2026-06-04)
> The box also has an **AMD RX 6700 XT (12 GB)**, and LM Studio runs the **Vulkan** backend across **both** GPUs → pooled budget **≈ 28 GB**. So the 35B (22 GB) was **not** spilling to CPU as first stated — it was split across the two cards (in VRAM). The slowness was instead: (1) the **thinking** tax (BUG 57 — can't disable via API; the token-exhaustion of BUG 20), and (2) the Vulkan/multi-GPU runtime being slower than CUDA-on-NVIDIA-alone (cross-vendor backend + inter-GPU PCIe transfer each forward pass). **Models load one at a time** (the choreography below swaps per stage), so each gets the *full* pooled budget — text+vision never need to co-reside, but each swap costs a reload. **For max speed prefer CUDA + NVIDIA-only with models ≤16 GB; use Vulkan/both only for capacity** (e.g. a Q4 Qwen3-VL-30B vision model or a big MoE on the Judge).

### Thinking: off almost everywhere (research-backed, 2026-06-04)

External evidence (a study of thinking mode in local agent workflows + extraction-model research) is unambiguous: reasoning/thinking gives **little benefit and should be OFF for JSON extraction, classification, summarization, and structured/tool output**, and the thinking block can eat the entire `max_tokens` before any answer — the exact failure of **BUG 20** (35B-A3B thinking consumed all tokens). Mapped to stages: **off** for Stage 3 (classify), Pass B (extract — the one that bit us), Pass D (evaluate), Stage 6 (generate). The **only** candidate for thinking-ON is the **Vision Judge** (pairwise judgment = a decision task), but it's bundled with Stage 6 and runs ×N comparisons, so keep it off and only experiment there. Because the API thinking-toggle is ignored (BUG 57), the reliable lever is **model choice** (non-thinking-by-default models), not the flag.

### Evaluation notes (2026-06-04 — calibrating the picks)

- **Qwen3-VL's edge is OCR-specific, not blanket.** Benchmarks: Qwen3-VL-8B ~96% DocVQA / ~90% OCRBench ("Qwen wins at every OCR tier"), but **Gemma 4 31B leads general visual reasoning (MMMU-Pro ~77)**. For the Judge's holistic "which clip is better," Gemma is competitive. **And the pipeline already runs PaddleOCR** (BUG 40/41) for hard on-screen text → the VLM's OCR edge is *partly redundant*. So Qwen3-VL is a reasonable grounding **test, not a must-do**; installed gemma-4-12b / qwen3.5-9b are fine.
- **MoE = quality of total params at compute of active params.** `gemma-4-26b-a4b` (~4B active) / `qwen3.6-35b-a3b` (~3B active) are **higher-quality than 9–12B dense** at near-small-model compute — the right *quality* lever (the user's actual complaint), and they fit the ~28 GB pool. Caveats: a MoE is slightly below a same-total *dense* model ("dense 27B > 35B-A3B"), and a multi-GPU split adds memory-access overhead. **The 35B-A3B's 67-min Stage 4 was the *thinking*, not the MoE** — with thinking OFF it's cheap. → **Quality tier = a big MoE with thinking off.**
- **CUDA beats Vulkan on *prompt processing* (~ties on generation).** The pipeline is prompt-heavy (long Pass B chunks, image inputs), so CUDA/NVIDIA-only helps where it matters — for models that fit 16 GB. Verify CUDA runs cleanly on the Blackwell sm_120 card.

### Best per-role picks (research-grounded)

- **Text role** (`text_model`: Stage 3 + Pass B + Pass D) — non-thinking, fits the NVIDIA card: `qwen3.5-9b` (6.5, fastest) or `gpt-oss-20b` at low effort (12.1, strongest extraction that still fits 16 GB).
- **Vision role** (`vision_model`: Stage 6 + Judge) — a **Qwen3-VL** vision specialist is the research winner for OCR/UI grounding (reads Twitch chrome → better titles + less hallucination, BUG 26): **`Qwen3-VL-8B`** (~10 GB, the model this note already recommends) or **`Qwen3-VL-30B-A3B`** (MoE ~2.4 B active; Q3_K 14 GB fits NVIDIA alone, Q4 18 GB needs the pool). `gemma-4-12b` (7.6, installed) is the no-download fallback. Both need a download for Qwen3-VL.

---

## Recommended split (48 GB VRAM rig)

Per the research doc's §8.9 cost analysis:

```json
{
  "text_model_passb":    "qwen/qwen3-32b",
  "vision_model_stage6": "qwen/qwen3-vl-8b"
}
```

- **Qwen3-32B text-only** for Pass B: ~28 GB BF16, non-thinking (explicitly off via `/no_think` sentinel from Phase 0.2), 128K YaRN context. ~3× cheaper than the unified multimodal model for Pass B's pure classification workload.
- **Qwen3-VL-8B FP8** for Stage 6: ~10 GB, Apache 2.0, strongest on Twitch UI OCR per OCRBench 896 (vs 885 for the unified Gemma), ScreenSpot-Pro 61.8 validates UI grounding.

The two can be **co-resident** on a 48 GB GPU (38 GB total) — no hot-swap needed between Stage 4 and Stage 6. On 24 GB rigs, stick with the unified config.

Optional: keep `text_model = "google/gemma-4-26b-a4b"` for Stage 3 segment classification, where Gemma's better cross-lingual robustness can help VODs with multilingual chat overlays.

---

## VRAM choreography

The pipeline's model-loading logic (lines 117 / 126) compares effective stage-specific names:

```
# Stage 2 prep: unload ALL four possible model slots before Whisper gets GPU.
unload_model "$TEXT_MODEL"
unload_model "$VISION_MODEL"
[ "$TEXT_MODEL_PASSB" != "$TEXT_MODEL" ]   && unload_model "$TEXT_MODEL_PASSB"
[ "$VISION_MODEL_STAGE6" != "$VISION_MODEL" ] && unload_model "$VISION_MODEL_STAGE6"

# Stage 3 prep: load TEXT_MODEL.
load_model "$TEXT_MODEL"

# Stage 3 → Stage 4 swap: if Pass B's model differs, swap now.
[ "$TEXT_MODEL_PASSB" != "$TEXT_MODEL" ] && {
    unload_model "$TEXT_MODEL"
    load_model "$TEXT_MODEL_PASSB"
}

# Stage 5 → Stage 6 swap: same check.
[ "$TEXT_MODEL_PASSB" != "$VISION_MODEL_STAGE6" ] && {
    unload_model "$TEXT_MODEL_PASSB"
    load_model "$VISION_MODEL_STAGE6"
}

# Stage 6 → Stage 7 prep: unload vision before Whisper caption transcription.
unload_model "$VISION_MODEL_STAGE6"
```

Unified config (all four names resolve to the same model) → every `!=` check is false, and every swap is a no-op. Zero behavior change.

---

## Not shipped in Phase 5.1

Per `IMPLEMENTATION_PLAN.md`:

- **Stage 6a text classifier** — a text-only pass BEFORE Stage 6 that produces `{what_happened, category, confidence}` from transcript + chat + OCR, then Stage 6 vision treats that as a hard constraint and generates style-consistent title/hook/description. Requires a real Stage 6 rewrite (currently Stage 6 is a single call). Deferred.
- **Top-5 % vision escalation** — swap to Qwen3-VL-32B AWQ INT4 for the highest-scoring 5 % of candidates. Needs orchestration for hot-swap; defer until there's a cost/quality eval harness (Phase 5.3's bootstrap dataset).

---

## Related

- [[entities/lm-studio]] — model loader
- [[concepts/vram-budget]] — per-model VRAM costs
- [[concepts/highlight-detection]] — Pass B consumer
- [[concepts/vision-enrichment]] — Stage 6 consumer
- `config/models.json` — runtime config
- `IMPLEMENTATION_PLAN.md` Phase 5.1 — definition + deferred Stage 6a
