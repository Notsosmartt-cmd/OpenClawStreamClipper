---
title: "Stage-Specific Model Split — Phase 5.1"
type: concept
tags: [models, vram, phase-5, configuration, stage-4, stage-6, infrastructure]
sources: 2
updated: 2026-06-12
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

## Active config — historical 16 GB-rig split (2026-06-04, since reverted)

> [!note] Current config (2026-06-12): back to the unified model
> `config/models.json` now sets BOTH `text_model` and `vision_model` to **`qwen/qwen3.6-35b-a3b`** again. The 16 GB-fit split below was a stopgap; once the **AMD RX 6700 XT 12 GB** joined the NVIDIA card to form a **~28 GB dual-GPU Vulkan pool** ([[concepts/vram-budget]]), the 22 GB MoE fits with the thinking toggle off, so the split's motivation (not fitting 16 GB) no longer applies. The split section below is retained as the decision record + the fallback config for a CUDA-only 16 GB rig.

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

> [!note] Updated 2026-06-04 — the earlier "Qwen3-VL OCR-specific, not blanket" note was too dismissive
> Full head-to-head benchmark data and architectural analysis now lives in [[concepts/vlm-comparison-2026-06]]. Highlights below; read that page for the table + sources.

- **Qwen3-VL is a defensible upgrade for the vision slot.** Earlier framing called it OCR-only-redundant because PaddleOCR already handles overlay text. That missed two facts: (a) Qwen3-VL's published **ScreenSpot 94%+** UI grounding directly serves the `chrome_regions` job (chat/logo/cam bbox detection in [[concepts/vision-enrichment]] §What the model outputs), and (b) Stage 5.5 ([[entities/vision-judge]]) is a multi-frame video-temporal task that Qwen3-VL's native multi-image + Text-Timestamp Alignment is purpose-built for. Gemma 4 has **no published ScreenSpot / MVBench / OCRBench** in primary sources, so picking it for this workload trusts a general multimodal claim without the workload-specific bench. **`Qwen3-VL-8B-Instruct` is the recommended migration target** (~5.7 GB Q4 fits CUDA, multi-image native, ScreenSpot 94%). Status quo `gemma-4-12b` is still defensible but watches several open llama.cpp CUDA bugs ([[entities/gemma4]] §Known issues).
- **MoE = quality of total params at compute of active params.** `gemma-4-26b-a4b` (~4B active) / `qwen3.6-35b-a3b` (~3B active) are higher-quality than 9–12B dense at near-small-model compute, and fit the ~28 GB Vulkan pool. Caveats: a MoE is slightly below a same-total *dense* model ("dense 27B > 35B-A3B"), and a multi-GPU split adds memory-access overhead (~30-40% throughput loss per [llama.cpp #16767](https://github.com/ggml-org/llama.cpp/issues/16767)). **The 35B-A3B's 67-min Stage 4 was the *thinking*, not the MoE** — with thinking OFF it's cheap.
- **BUG 57 narrowed 2026-06-04** — the LM Studio app-side Custom Fields → Enable Thinking toggle DOES work on `qwen3.6-35b-a3b` (verified `reasoning_tokens=0`). Only the API param is broken. So the 35B-A3B is **back on the menu as a quality text candidate** via the app-side toggle. → **Quality tier (text) = a big MoE with app-side thinking off.**
- **CUDA beats Vulkan on prompt processing (~ties on generation).** The pipeline is prompt-heavy (long Pass B chunks, image inputs), so CUDA/NVIDIA-only helps where it matters — for models that fit 16 GB. NVIDIA Vulkan is ~2× slower than CUDA single-card per [llama.cpp #10879](https://github.com/ggml-org/llama.cpp/discussions/10879).
- **Qwen3.5-9B's "multimodal" claim is text-only in practice.** Mainline LM Studio GGUF ships text-only (no mmproj); community VLM build is marked "Partial" support. Treat as text-only in production. See [[entities/qwen35]] §The multimodal claim.

### Best per-role picks (research-grounded, 2026-06-04)

**Text role** (`text_model`: Stage 3 + Pass B + Pass D), descending by tier — see [[concepts/text-comparison-2026-06]] for full benchmark table:

| Tier | Pick | VRAM | Backend | Notes |
|---|---|---|---|---|
| Speed | **`google/gemma-4-12b`** | 7.6 GB Q4 | CUDA single-card | **IFEval 88.9** = best small JSON emitter; no thinking mode = zero leak risk |
| Speed (consolidation) | `qwen/qwen3.5-9b` (current) | 6.5 GB Q4 | CUDA single-card | Non-thinking by default. **Vision also verified working in LM Studio** ([[entities/qwen35]]) — sets up the dual-slot consolidation play |
| Balanced | `openai/gpt-oss-20b` @ `Reasoning: low` | 12.1 GB MXFP4 | CUDA single-card | Runtime-tunable `reasoning_effort` Low/Med/High (works); flip to Medium for Pass D |
| Quality | **`Qwen3-30B-A3B-Instruct-2507`** (download) | ~16.8 GB Q4 | Vulkan pool | Structurally **no thinking** — sidesteps the upstream Qwen3 hybrid chat-template bug. MoE 3B active = fast even on pool |
| Quality (workaround) | `qwen/qwen3.6-35b-a3b` w/ **app-side Enable Thinking OFF** | 22.1 GB | Vulkan pool | Works via LM Studio toggle (verified, [[concepts/bugs-and-fixes]] BUG 57) — but the structurally cleaner option is the 2507 Instruct variant above |

> [!warning] Avoid hybrid Qwen3.x for Pass B
> `enable_thinking=false` is broken upstream in llama.cpp's chat-template path across the entire Qwen3 hybrid family (5+ open issues). Use `-Instruct-2507` variants OR Gemma 4 OR gpt-oss — see [[concepts/text-comparison-2026-06]] Hard rule.

**Vision role** (`vision_model`: Stage 6 + Vision Judge), descending by tier — see [[concepts/vlm-comparison-2026-06]] for full benchmark table:

| Tier | Pick | VRAM | Backend | Notes |
|---|---|---|---|---|
| Speed / status quo | `google/gemma-4-12b` (current) | 7.6 GB Q4 | CUDA single-card | Works today; best general visual reasoning (MMMU-Pro 69.1); watch llama.cpp Gemma 4 vision bugs |
| Speed (consolidation) | `qwen/qwen3.5-9b` | 6.5 GB Q4 | CUDA single-card | **Multimodal verified by LM Studio Staff Pick**; Alibaba claims MMMU 78.4 (single-source); same model as Speed-consolidation text → zero text↔vision swap |
| Balanced (recommended) | **`Qwen3-VL-8B-Instruct`** (download) | ~5.7 GB Q4 | CUDA single-card | Multi-image + video native; ScreenSpot 94% (UI grounding for chrome_regions); cleanest llama.cpp packaging |
| Quality | `Qwen3-VL-30B-A3B-Instruct` (download) | ~19.6 GB Q4 | Vulkan pool or `--cpu-moe` | Best workload-specific benches (ScreenSpot 94.7%, OCRBench 903); MoE 3B active |

See [[concepts/vlm-comparison-2026-06]] for vision; [[concepts/text-comparison-2026-06]] for text.

---

## Unified "best models" table (2026-06-04 third pass — Qwen 3.6 multimodal discovery)

| Strategy | Text slot | Vision slot | Total VRAM | Backend | Trade-off |
|---|---|---|---|---|---|
| **🆕 Max-quality single-model consolidation (recommended)** | `qwen3.6-35b-a3b` w/ Enable Thinking OFF | same | 22 GB | Vulkan pool | **One model, zero swap, top-tier both modalities.** MMMU 81.7 / MMBench 92.8 / OmniDocBench 89.9. MoE 3B active offsets ~half the pool tax. BUG 57 toggle verified. |
| **CUDA-only consolidation (smallest)** | `qwen3.5-9b` | `qwen3.5-9b` | 6.5 GB | CUDA single-card | Smallest VRAM, fastest per-token. Lower quality ceiling; vision benches single-source (Alibaba MMMU 78.4). |
| **16 GB-rig fallback** (was running 2026-06-04 → 06-12) | `qwen3.5-9b` | `gemma-4-12b` | 14.1 GB | CUDA single-card | Defensible CUDA-only option; both fit 16 GB. Superseded by the unified `qwen3.6-35b-a3b` on the 28 GB Vulkan pool. |
| **IFEval-max for Pass B (small)** | `gemma-4-12b` | `gemma-4-12b` | 7.6 GB | CUDA single-card | Best JSON adherence in small class (anchored to Gemma 3 27B IFEval 90.4; Gemma 4 unpublished). Lower vision benches than Qwen 3.6 35B-A3B. |
| **IFEval-max for Pass B (big)** | `gemma-4-26b-a4b` | `gemma-4-26b-a4b` | 18 GB | Vulkan pool | MoE 4B active. Lower vision benches than Qwen 3.6 35B-A3B. |
| **Best-per-slot specialists (with downloads)** | `gemma-4-26b-a4b` | `Qwen3-VL-30B-A3B-Instruct` (download) | swap | Both Vulkan pool | ScreenSpot 94.7% UI grounding for chrome_regions. Requires download. |
| **Reasoning-tunable CUDA-only** | `gpt-oss-20b` @ Low/Med/High | `qwen3.5-9b` | 13.6 + 6.5 GB | Both CUDA | Runtime reasoning_effort knob. No Vulkan pool penalty. |

> [!note] Why qwen3.6-35b-a3b is the new top recommendation
> Discovered 2026-06-04 ([[concepts/vlm-comparison-2026-06]]): **Qwen 3.6 27B and 35B-A3B are both natively multimodal** — no separate VL line, vision is baked in. The 35B-A3B's vision benches (MMMU 81.7, MMBench 92.8, MathVista 86.4, OmniDocBench 89.9, VideoMME w/sub 86.6) are top-of-leaderboard. Combined with the verified [[concepts/bugs-and-fixes]] BUG 57 LM Studio app-side thinking toggle, it becomes the single best installed model for the entire pipeline. The only Qwen3-VL edge is for `chrome_regions` UI grounding specifically (ScreenSpot 94.7% documented vs Qwen 3.6 unpublished). MoE 3B active keeps per-token speed competitive even with the ~30-40% Vulkan dual-GPU pool tax.

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
