---
title: "Vision Enrichment (Stage 6)"
type: concept
tags: [vision, enrichment, non-gatekeeping, gemma-4, qwen35, scoring, titles, originality, grounding, stage-6, tier-3, a2]
sources: 4
updated: 2026-04-23
---

# Vision Enrichment (Stage 6)

The stage that uses a multimodal model to analyze extracted video frames, generate clip titles/descriptions/hooks, emit originality hints, and optionally boost scores for visually interesting moments.

Key design: **non-gatekeeping**. Vision can only help, never eliminate.

> [!note] Model swap (April 2026)
> Vision runs on the same multimodal model as text detection — Gemma 4 (`gemma-4-26b-a4b`) or Qwen 3.5 (`qwen3.5-9b` / `qwen3.5-35b-a3b`), both of which support image input and proper thinking-token budgeting. The previous single-purpose `qwen3-vl-8b` entry is retired; see [[entities/qwen3-vl]] for the historical record. When the text and vision model IDs match, Stage 5 → 6 skips the VRAM unload/reload cycle.

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

As of 2026-04-23 (Phase 0.1), the VLM receives **all 6 payoff-window frames in ONE call** instead of 2 separate single-frame calls:

| Frame | Offset from peak | Caption in prompt |
|---|---|---|
| 1 | T−2s | pre-peak setup |
| 2 | T+0s | peak |
| 3 | T+1s | — |
| 4 | T+2s | — |
| 5 | T+3s | typical payoff |
| 6 | T+5s | aftermath |

Stage 5 extracts these at exact offsets (one `ffmpeg -ss` per frame) rather than the old uniform `fps=1/5` sweep. The prompt tells the model "time moves forward across the sequence" and asks it to reason about the **change** between T−2 and T+5 — that delta is the clip.

> [!warning] Pre-2026-04-23: vision described the setup, not the payoff
> Old Stage 5 extracted at `fps=1/5` from T−15, producing frames at T−15..T+10. Stage 6 looped over only indices `03` and `04` (= T−5 and T+0), so the model never saw T+1..T+5 where punchlines and reactions actually land. This is BUG 25 in [[concepts/bugs-and-fixes]] and was the single highest-impact fix in the 2026 upgrade.

Additional context fed to the prompt:
- Stream context from Stage 3 profile: dominant type, current segment type, detection reason
- ±8 s verbatim transcript window around the peak — grounding against the streamer's actual words
- Transcript-grounded Pass-B `why` (after it has passed the Tier-1 grounding gate)

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

## Thinking model handling

Both Gemma 4 and the 35B Qwen 3.5 have permanently-enabled thinking mode in LM Studio. The vision call uses `max_tokens=6000` and sets `chat_template_kwargs={"enable_thinking": False}` where supported. When content comes back empty, the parser falls back to `message.reasoning_content` — larger thinking models stash the answer there when they finish naturally.

---

## Output

The model returns a single JSON blob per moment:

```json
{
  "score": 7,
  "category": "funny",
  "title": "IRL Fat Sack Checkout Fiasco",
  "description": "Streamer discovers unexpected item in checkout line, chat goes wild",
  "hook": "Wait — he said WHAT at the deli?",
  "mirror_safe": true,
  "chrome_regions": [
    {"x": 0, "y": 0, "w": 380, "h": 1080, "label": "chat"},
    {"x": 1680, "y": 900, "w": 240, "h": 60, "label": "logo"}
  ],
  "voiceover": {
    "text": "He said what at the deli — chat is gone",
    "placement": "intro",
    "tone": "deadpan",
    "duration_estimate_s": 2.8
  }
}
```

Downstream consumers:

- `title` → sanitized filename (`IRL_Fat_Sack_Checkout_Fiasco.mp4`)
- `description` → diagnostics + Discord summary
- `hook` → top-of-video overlay (drawtext with per-clip randomized palette)
- `mirror_safe` → Stage 7 uses it to gate horizontal flip
- `chrome_regions` → Stage 7 `smart_crop` framing uses it to crop chat / logo / cam out of the final frame
- `voiceover` → Stage 7 Piper TTS layer when `CLIP_TTS_VO=true`

All originality fields are optional — missing values produce safe defaults (`mirror_safe=false`, empty region list, no VO) so vision failures never break the render.

---

## Grounding cascade (2026-04-23, simplified to 2 tiers 2026-05-01)

Stage 6 runs the [[entities/grounding]] 2-tier cascade on every generated field in `{title, hook, description}` against `[±8 s transcript window, Pass-B why]`:

1. **Tier 1** (always) — regex denylist + content-word overlap + Phase 2.4d zero-count Twitch-event check (stdlib only, <5 ms).
2. **Judge** (when Tier 1 is ambiguous) — main-model LLM judge via LM Studio, returning a 5-dimensional 0-10 score collapsed to a weighted mean. Pass = weighted ≥ `pass_threshold`. The previous MiniCheck NLI (Tier 2) and Lynx-8B (Tier 3) sub-models were retired 2026-05-01 — see [[concepts/bugs-and-fixes#REMOVAL 2026-05-01b]].

### Regenerate-once policy (Phase 1.1)

When any generated field fails the cascade on the first VLM call, Stage 6 builds a **stricter retry prompt** that names the violation (e.g. "your previous response contained 'gifted subs' but the transcript never mentions subscriptions — rewrite using ONLY what's in the transcript") and makes exactly ONE more call using the same 6 frames. Fields that pass on the retry replace the failing ones; fields that also fail the retry are nulled.

The VLM call is factored into a local `_vision_call(prompt_text)` closure so the first attempt and the retry share all the parsing / reasoning-content / code-fence fallback logic. Both calls request `response_format: {type: json_object}` (LM Studio JSON mode) so the top-level reply is guaranteed to be a valid JSON object.

### Fallback ladder

- Nulled fields fall back to the transcript-only defaults seeded in `entry` (title = `f"Clip_T{T}"`, description = the Pass-B `why`, no hook).
- Every cascade failure logs to stderr with `[GROUND] Stage 6 null <field> T=<T> tier=<1|2|3> reason=<...>`.
- `parsed["grounding_fails"]` accumulates `{field}:tier{N}:{reason}` entries so the dashboard can surface questionable clips.
- `parsed["grounding_tier"]` records which tier ruled on each moment (useful for A/B measurement).

The gate never drops a clip — it only strips unsupported metadata and, when regeneration succeeds, replaces it with a transcript-faithful alternative.

---

## Timeout protection

Two layers:
1. **Stage timeout**: entire Stage 6 limited to 1 hour (enough for ~11 moments × 220 s on the 35B thinking model with some margin). Exceeded moments use transcript-only data.
2. **5-minute per-moment timeout**: each individual frame analysis bounded separately.

Keeps the pipeline from hanging on a slow LM Studio response or a stuck vision model.

---

## VRAM orchestration

When text and vision models are the same multimodal model (the default — see [[entities/lm-studio]]), the Stage 5→6 unload/reload cycle is skipped entirely. Otherwise the text model is unloaded and the vision model is loaded first. After Stage 6 the vision model is unloaded so Whisper can claim GPU for Stage 7 captions.

See [[concepts/vram-budget]].

---

## Related
- [[entities/qwen35]] — default multimodal model (also handles this stage)
- [[entities/lm-studio]] — inference server and same-model optimization
- [[concepts/originality-stack]] — consumer of the new vision output fields
- [[concepts/clipping-pipeline]] — Stage 6 in pipeline context
- [[concepts/clip-rendering]] — Stage 7 that uses the titles / hooks / chrome regions
- [[concepts/highlight-detection]] — Stage 4 that feeds candidates into this stage
