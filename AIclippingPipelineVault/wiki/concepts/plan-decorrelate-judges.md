---
title: "Plan — De-correlate the LLM judge panel"
type: concept
tags: [plan, models, decorrelation, pass-b, pass-d, vision-judge, model-split]
sources: 0
status: planned
updated: 2026-06-12
---

# Plan — De-correlate the LLM judge panel

Filed from the 2026-06-12 deep evaluation. Executes [[concepts/clipping-intelligence]] **Opportunity C**.

**Problem:** as of 2026-06-12 the config is maximum-correlation — **one model** (`qwen/qwen3.6-35b-a3b`) classifies segments (Stage 3), proposes (Pass B), confirms (Pass D rubric), tournament-judges (Stage 5.5), and titles (Stage 6). A bias toward e.g. `storytelling_arc` proposes, confirms, and visually re-confirms the same wrong label; the implied confidence is overstated. Only Pass A↔Pass B agreement is genuinely independent.

---

## Override matrix today

| Layer | Env var resolved | Config key | Decorrelatable via config alone? |
|---|---|---|---|
| Stage 3 segment classify | `TEXT_MODEL` | `text_model` | — (base) |
| Pass B propose | `TEXT_MODEL_PASSB` | `text_model_passb` (null → inherits `text_model`) | ✅ yes |
| **Pass D rubric** | `TEXT_MODEL_PASSB` (reused) | — | ❌ **hard-wired to Pass B's model** |
| Stage 6 vision enrich | `VISION_MODEL_STAGE6` | `vision_model_stage6` (null → inherits `vision_model`) | ✅ yes |
| **Stage 5.5 vision judge** | `VISION_MODEL_STAGE6` (reused via `vlm_judge.py` env resolution) | — | ❌ **hard-wired to Stage 6's model** |

The most valuable split — **proposer (B) vs confirmer (D)** — is exactly the one that's blocked.

---

## Plan (~2 h)

1. Add `text_model_passd` + `vision_model_judge` keys to `config/models.json` with the same null-inherits fallback pattern.
2. Thread through `scripts/lib/stages/stage4_rubric.py` and `stage5_5_judge.py` / `vlm_judge.py` (accept an optional model override instead of resolving `VISION_MODEL_STAGE6` directly).
3. Set Pass D = **Gemma 4 12B** while Pass B stays Qwen — [[concepts/text-comparison-2026-06]] already recommends Gemma 4 12B for the text slot (IFEval 88.9, strong instruction-following: right profile for a rubric judge).
4. VRAM is fine: the pipeline unloads between stages ([[concepts/vram-budget]]), so a second family costs disk, not VRAM.

## Cheap supplements

- Vary temperature/prompt framing where the same model must be reused — so agreements aren't the same call three times.
- **Down-weight B↔D↔vision agreement relative to A↔B agreement** in Pass C — one constant, fittable by [[concepts/plan-calibration-loop]].

## Related

- [[concepts/model-split]] — the Phase 5.1 override architecture this extends
- [[concepts/clipping-intelligence]] — weakness #2 ("cross-validation across LLM layers is weaker than it looks")
- [[concepts/text-comparison-2026-06]], [[entities/gemma4]], [[concepts/vram-budget]]
