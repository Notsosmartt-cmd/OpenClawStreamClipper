---
title: "Segment Detection (Stage 3)"
type: concept
tags: [segment-detection, stream-profiling, classification, qwen35, stage-3, text]
sources: 2
updated: 2026-06-12
---

# Segment Detection (Stage 3)

The stream profiling stage. Classifies the stream into typed segments and builds a profile that downstream stages use to tailor their behavior.

Runs between transcription (Stage 2) and moment detection (Stage 4).

---

## What it does

Long streams shift between different content modes — a streamer might play a game for 90 minutes, then switch to just chatting, then react to videos. Different segment types have different "funny" thresholds and different signal types worth detecting. A clutch gameplay moment reads differently than an emotional chatting moment.

Stage 3 identifies these boundaries so Stage 4 can be smarter.

---

## Classification process

Implemented in `scripts/lib/stages/stage3_segments.py`.

1. Transcript chunked into windows of `CLIP_SEGMENT_CHUNK` seconds (**default 600 = 10 min**)
2. Each chunk: first ~600 words sent to the text model (`TEXT_MODEL`, the unified `qwen/qwen3.6-35b-a3b` as of 2026-06; see [[entities/qwen35]] for the historical 9B)
3. Prompt forces one-word output (`temperature=0.1`, prefixed `/no_think`); on thinking models the answer is recovered from `reasoning_content`/the truncated reasoning tail if `content` comes back empty
4. Model classifies into one of five types: `gaming` | `irl` | `just_chatting` | `reaction` | `debate`
5. Adjacent same-type chunks merged into contiguous segments
6. Optional `--type` hint (from Discord message) biases classification for known stream types — soft bias, individual segments can still differ. `variety` maps to "no hint."

Speed: ~1 second per chunk on a fast text model (more on thinking models, which can burn 1.5k–6k reasoning tokens per call — `max_tokens` is budgeted at 6000 to avoid mid-reasoning truncation).

> [!note] Window size is a tunable knob (Fix 1, 2026-06-06)
> `CLIP_SEGMENT_CHUNK` (default 600) sets the classification window. Smaller (e.g. `300`) gives finer granularity so a short off-type pocket — a 2-min debate inside a gaming stream — gets its own label instead of being absorbed, at ~2× the (cheap) classification calls. `CLIP_SEGMENT_OVERLAP` (default 0) adds read-context to each window without overlapping the recorded (nominal, non-overlapping) segments. Default left at 600 deliberately (measure-first; A/B 300 vs 600 via the env). See [[concepts/detection-improvements-plan]] Fix 1.

> [!note] Confidence voting + smoothing (2026-06-12, opt-in)
> Segment classification was the funnel's unguarded single point of failure ([[concepts/clipping-intelligence]] weakness #5; [[concepts/case-rap-battle-missed]] failure #2). `CLIP_SEGMENT_VOTES=N` (default 1 = old behavior) classifies each window N times at temperature 0.7, takes the majority, and stamps `confidence = top_votes/N` on the window (single-vote path keeps the deterministic 0.1 call; `confidence: null`). With voting on, a low-confidence window (< `CLIP_SEGMENT_SMOOTH_BELOW`, default 0.67) sandwiched between two neighbors that agree with each other is smoothed to the neighbor type (`CLIP_SEGMENT_SMOOTH=0` disables) — confidently-typed off-type pockets are never flattened. Merged segments keep the **lowest** member confidence (pessimistic). Cost at N=3 on a 3-h VOD: ~36 extra 1-word calls (~1 min). Validated against a scripted mock LLM: split-vote window (conf 0.33) between agreeing gaming neighbors correctly smoothed and merged.

---

## Segment types

| Type | Description | Examples |
|---|---|---|
| `gaming` | Active gameplay | FPS, MOBA, battle royale, speedrunning |
| `irl` | Real-world content | Walking outside, events, travel, cooking |
| `just_chatting` | Desktop chatting | Talking to chat, Q&A, storytime |
| `reaction` | Watching content | Reacting to clips, videos, other streams |
| `debate` | Argumentative discussion | Hot takes, opinion battles, drama |

---

## Stream profile

After classification, the pipeline generates `stream_profile.json` (percentages by total segment **duration**, not chunk count):

```json
{
  "dominant_type": "gaming",
  "dominant_pct": 65.0,
  "type_breakdown": {"gaming": 65.0, "just_chatting": 25.0, "irl": 10.0},
  "is_variety": false,
  "hint_used": "none"
}
```

`is_variety` is true when the dominant type holds < 60% of total duration.

The stream profile is used in Stage 6 (Vision Enrichment) to provide context to the vision model. A frame from a gaming segment gets analyzed differently than a frame from an IRL segment.

---

## How segment type affects Stage 4

Each segment type changes how Stage 4 behaves:

**Dynamic thresholds** (Pass A keyword scanning):
- `gaming`: threshold 3 — noisier content, need stronger signals
- `irl`: threshold 2 — quieter content, more sensitive detection
- `just_chatting`: threshold 2
- `reaction`: threshold 3
- `debate`: threshold 2

**Segment-specific weight multipliers** (Pass A):
- `funny` keywords in `irl` segments: 1.4× (IRL comedy is subtler)
- `controversial` in `reaction`/`debate`: 1.5×
- `storytime` in `just_chatting`: 1.5×

**Score boosts** (Pass B LLM analysis):
- Quieter segment types (`irl`, `just_chatting`) get +1 to their LLM scores so they can compete with louder gaming moments

**Segment-specific prompts** (Pass B):
- Each segment type gets a different system prompt emphasizing what makes that type clip-worthy

---

## Output files

- `segments.json`: array of `{start, end, type, confidence}` for each segment (`confidence` is `null` at the default 1-vote setting; min-of-members after merge)
- `stream_profile.json`: `dominant_type`, `dominant_pct`, `type_breakdown`, `is_variety`, `hint_used`

Both files persist in `/tmp/clipper/` during the pipeline run. The stream profile is read by Stage 6 when constructing vision prompts.

---

## Related
- [[concepts/clipping-pipeline]] — Stage 3 in context
- [[concepts/highlight-detection]] — Stage 4 that consumes the segment data
- [[concepts/vision-enrichment]] — Stage 6 that uses the stream profile
- [[entities/qwen35]] — model that does the classification
