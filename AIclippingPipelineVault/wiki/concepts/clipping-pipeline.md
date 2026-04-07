---
title: "Clipping Pipeline"
type: concept
tags: [pipeline, architecture, workflow, stages]
sources: 2
updated: 2026-04-07
---

# Clipping Pipeline

The 8-stage sequential workflow that transforms a raw VOD into finished vertical video clips. Triggered by a Discord command or [[entities/dashboard]] button. Each stage completes fully before the next begins. Implemented in `scripts/clip-pipeline.sh` (~1,700 lines).

---

## Stage map

```
Discord / Dashboard trigger
         ↓
1. Discovery          — find VOD, verify size, get duration
         ↓
2. Transcription      — chunked GPU Whisper, cached to disk
         ↓
3. Segment Detection  — classify stream type per 10-min window, build stream profile
         ↓
4. Moment Detection   — Pass A: keywords | Pass B: LLM | Pass C: merge + time-bucket
         ↓
5. Frame Extraction   — 6 JPEGs per candidate (960×540)
         ↓
6. Vision Enrichment  — score boosts, titles, descriptions (non-gatekeeping)
         ↓
7. Editing & Export   — blur-fill 9:16, batch Whisper captions, FFmpeg render
         ↓
8. Logging            — processed.log, diagnostics JSON, Discord report
```

---

## Stage 1 — Discovery

- Scans `/root/VODs/` (mapped to `vods/` on host) for `.mp4` and `.mkv` files
- Checks `processed.log` to skip already-processed VODs (unless `--vod` or `--force` used)
- Verifies file is complete (size check with delay)
- Gets VOD duration via `ffprobe`
- `--vod <keyword>` targets a specific file by name match (bypasses processed.log)
- `--force` re-processes the latest VOD without naming it
- `--list` returns JSON inventory of all VODs with size, duration, status, transcription cache
- When a VOD isn't found, error includes full list of available filenames

---

## Stage 2 — Chunked Transcription

- **Unloads all Ollama models** from VRAM (`keep_alive=0`) so Whisper gets full GPU
- FFmpeg extracts audio to 16kHz mono WAV
- Audio split into **20-minute chunks** (prevents faster-whisper degenerate loop on long files)
- Each chunk transcribed: GPU float16 first, CPU int8 fallback
- Beam search 5, word-level timestamps enabled
- Chunks merged with offset-corrected timestamps; degenerate segments filtered
- **Cached** to `vods/.transcriptions/` — re-clips skip this stage
- Output: `transcript.json` + `transcript.srt`

Performance: ~40–60 min for 3.5-hour audio on RTX 5060 Ti. This stage dominates total time.

---

## Stage 3 — Segment Detection

See [[concepts/segment-detection]] for full detail.

- Chunks transcript into 10-minute windows
- [[entities/qwen35]] classifies each chunk (`num_predict=10` — outputs one word): `gaming`, `irl`, `just_chatting`, `reaction`, or `debate`
- Merges adjacent same-type segments
- Optional `--type` hint biases classification
- Builds `stream_profile.json`: dominant type, percentage breakdown, variety detection
- Output: `segments.json` + `stream_profile.json`

Fast: ~1 second per chunk.

---

## Stage 4 — Three-Pass Moment Detection

See [[concepts/highlight-detection]] for full detail.

**Pass A — Keyword scanning (instant, no LLM)**:
- Slides 30-second window (10-second step) across transcript
- 6 keyword categories: hype, funny, emotional, hot_take, storytime, reactive
- Segment-specific weight multipliers
- Universal signals: exclamation clusters, ALL CAPS, rapid sentences, laughter, question clusters, long pauses
- Dynamic threshold per segment type

**Pass B — LLM analysis** ([[entities/qwen35]] with segment-specific prompts):
- 5-minute chunks with 30-second overlap
- Context-aware: setup+payoff, irony, social dynamics, storytelling
- Returns JSON: `[{time, score, category, why}]`
- `think=false` required; applies segment score boosts for quieter segment types

**Pass C — Merge, deduplicate, time-bucket, select**:
- Normalizes keyword scores (capped at 8)
- Cross-validates: moments found by both passes get +1.5 boost + `cross_validated` flag
- Style weighting from `--style` flag
- Category cap: no single category > 60% of candidates (for `auto` style)
- **Time-bucket distribution**: VOD divided into equal buckets (2/hour, 3-10 range); Phase 1 guaranteed picks from each bucket; Phase 2 fills overflow slots; Phase 3 style re-ranking
- Enforces minimum 45-second gap between final selected clips
- Selects up to `MAX_CANDIDATES` (2× target clip count)

---

## Stage 5 — Frame Extraction

- 6 JPEG frames per selected moment from a 30-second window centered on the peak
- Resolution: 960×540 (half-res for speed)
- Quality: FFmpeg `q:v 2` (high quality)
- All FFmpeg calls use `-nostdin` to prevent stdin conflicts in bash loops

---

## Stage 6 — Vision Enrichment

See [[concepts/vision-enrichment]] for full detail.

- Sends middle 2 frames (of 6) to [[entities/qwen3-vl]] via Ollama vision API
- Uses `think: true`, `num_predict: 800` for thinking model
- Returns: `{score, category, title, description}`
- Score blending (additive only — can never reduce a score)
- 20-minute stage timeout + 90-second per-moment timeout
- **Every moment that survived Stage 4 WILL be rendered** — vision cannot eliminate candidates

---

## Stage 7 — Editing and Export

See [[concepts/clip-rendering]] for full detail.

1. Generate clip manifest — vision titles used as filenames (sanitized)
2. Extract all clip audio segments (single FFmpeg pass)
3. Batch caption transcription (single Whisper model load for all clips)
4. Render all clips with blur-fill 9:16:
   - Source: `T - 22s` to `T + 23s` (45-second window)
   - Filter chain: `split[bg][fg] → scale+crop+boxblur(25:5) → overlay(centered)` + subtitle burn-in
   - Output: `./clips/` on host
5. Unload Whisper, proceed to Stage 8

---

## Stage 8 — Logging

- Appends to `processed.log`: VOD name, timestamp, clip count, style
- Saves `clips/.diagnostics/<vod>_diagnostics.json`: keyword_moments, llm_moments, scored_moments, segments, transcript sample, clips_made
- Prints JSON summary to stdout (relayed to Discord by [[entities/openclaw]])
- Cleans up temp files in `/tmp/clipper/`

---

## Performance summary

| Stage | GPU mode | CPU-only mode |
|---|---|---|
| Stage 2: Transcription (2-hr VOD) | ~30–45 min | Several hours |
| Stage 3: Segment detection | ~1–2 min | ~3–5 min |
| Stage 4: Moment detection | ~5–15 min | ~20–40 min |
| Stage 5: Frame extraction | ~1–2 min | ~1–2 min |
| Stage 6: Vision enrichment | ~2–10 min | ~20–60 min |
| Stage 7: Render | ~3–8 min | ~3–8 min |
| **Total (2-hr VOD, 6 clips)** | **~45–90 min** | **Several hours** |

Transcription dominates total time in all cases.

---

## Temp files

All temp files written to `/tmp/clipper/` inside the container:
- `pipeline.log` — always written; readable via `docker exec ... tail -f`
- `pipeline_stage.txt` — current stage name for dashboard polling
- `pipeline_stages.log` — stage history with timestamps
- `transcript.json`, `segments.json`, `stream_profile.json`, frame JPEGs, clip audio WAVs

---

## Related
- [[concepts/highlight-detection]] — Stage 4 detail
- [[concepts/segment-detection]] — Stage 3 detail
- [[concepts/vision-enrichment]] — Stage 6 detail
- [[concepts/clip-rendering]] — Stage 7 detail
- [[concepts/vram-budget]] — how models are loaded/unloaded between stages
