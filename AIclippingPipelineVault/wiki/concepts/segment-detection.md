---
title: "Segment Detection (Stage 3)"
type: concept
tags: [segment-detection, stream-profiling, classification, qwen35, stage-3, text]
sources: 2
updated: 2026-04-07
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

1. Transcript chunked into **10-minute windows**
2. Each chunk: first ~600 words sent to [[entities/qwen35]] (`qwen3.5:9b`)
3. Prompt is cheap: `num_predict=10` — model outputs exactly one word
4. Model classifies into one of five types: `gaming` | `irl` | `just_chatting` | `reaction` | `debate`
5. Adjacent same-type chunks merged into contiguous segments
6. Optional `--type` hint (from Discord message) biases classification for known stream types — soft bias, individual segments can still differ

Speed: ~1 second per chunk.

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

After classification, the pipeline generates `stream_profile.json`:

```json
{
  "dominant_type": "gaming",
  "breakdown": {"gaming": 0.65, "just_chatting": 0.25, "irl": 0.10},
  "variety": true
}
```

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

- `segments.json`: array of `{start, end, type}` for each segment
- `stream_profile.json`: dominant type, percentage breakdown, variety flag

Both files persist in `/tmp/clipper/` during the pipeline run. The stream profile is read by Stage 6 when constructing vision prompts.

---

## Related
- [[concepts/clipping-pipeline]] — Stage 3 in context
- [[concepts/highlight-detection]] — Stage 4 that consumes the segment data
- [[concepts/vision-enrichment]] — Stage 6 that uses the stream profile
- [[entities/qwen35]] — model that does the classification
