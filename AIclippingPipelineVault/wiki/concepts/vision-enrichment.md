---
title: "Vision Enrichment (Stage 6)"
type: concept
tags: [vision, enrichment, non-gatekeeping, qwen3-vl, scoring, titles]
sources: 2
updated: 2026-04-07
---

# Vision Enrichment (Stage 6)

The stage that uses a vision model to analyze extracted video frames, generate clip titles/descriptions, and optionally boost scores for visually interesting moments.

Key design: **non-gatekeeping**. Vision can only help, never eliminate.

---

## The non-gatekeeping design

> [!warning] Vision was originally a gate — it was removed
> In early versions, the vision model acted as a filter: moments scoring below a visual threshold were dropped. This eliminated 90%+ of valid clips. Livestream frames are often visually boring even when the audio content is clip-worthy: a person at a desk, a dark room, a chat overlay, a static game UI. Making vision a gate was the wrong design.

The current design:
- **Every moment that survived Stage 4 WILL be rendered** — regardless of vision score
- Vision provides metadata (title, description) and score boosts
- Vision can never eliminate a candidate

---

## What the model receives

- The **middle 2 frames** (of 6 extracted) from each candidate moment — most representative of the peak
- Stream context from Stage 3 profile: dominant type, current segment type, detection reason

The context makes vision prompts more accurate. "This is a gaming segment, detected because of 'clutch' + exclamation cluster" lets the model evaluate the frames in the right context.

---

## Score blending

Vision scores are blended additively into transcript scores:

| Vision score | Effect on transcript score |
|---|---|
| ≥ 7 | + 2 (capped at 10) |
| ≥ 5 | + 1 |
| < 5 | unchanged |

If vision fails (bad JSON, timeout, model error): transcript score used unchanged. Clips still render.

---

## Thinking model requirements

[[entities/qwen3-vl]] is a thinking model. This stage must call it with:
- `think: true`
- `num_predict >= 600` (allows ~300–500 thinking tokens + ~100–200 content tokens)
- Context capped to 8K (vision prompts are short; larger context wastes VRAM)

The pipeline's `call_ollama()` function detects empty content output (model exhausted tokens on thinking) and retries with a larger `num_predict` budget.

---

## Output

For each moment, the model returns:
```json
{
  "score": 7,
  "category": "funny",
  "title": "IRL Fat Sack Checkout Fiasco",
  "description": "Streamer discovers unexpected item in checkout line, chat goes wild"
}
```

Vision-generated titles are used as clip filenames (sanitized for filesystem safety):
- `IRL_Fat_Sack_Checkout_Fiasco.mp4`
- `Gaming_Clutch_1v4_Comeback.mp4`

---

## Timeout protection

Two layers:
1. **20-minute total stage timeout**: entire Stage 6 limited to 20 minutes; if exceeded, remaining moments use transcript-only data and the stage moves on
2. **90-second per-moment timeout**: each individual frame analysis bounded separately

This prevents the pipeline from hanging on a slow Ollama response or a vision model that's stuck.

---

## VRAM orchestration

Before Stage 6: `qwen3.5:9b` is unloaded, `qwen3-vl:8b` is loaded.
After Stage 6: `qwen3-vl:8b` is unloaded, Whisper is loaded for Stage 7 captions.

See [[concepts/vram-budget]].

---

## Related
- [[entities/qwen3-vl]] — the model that runs this stage
- [[concepts/clipping-pipeline]] — Stage 6 in pipeline context
- [[concepts/clip-rendering]] — Stage 7 that uses the titles/descriptions from this stage
- [[concepts/highlight-detection]] — Stage 4 that feeds candidates into this stage
