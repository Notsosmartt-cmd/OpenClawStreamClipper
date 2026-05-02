---
title: "Clipping Pipeline"
type: concept
tags: [pipeline, architecture, workflow, stages, hub, stage-1, stage-2, stage-3, stage-4, stage-5, stage-6, stage-7, stage-8]
sources: 2
updated: 2026-05-01
---

<!-- updated 2026-04-27d for Tier-1 moment-discovery upgrades (Q1-Q5) -->

# Clipping Pipeline

The sequential workflow that transforms a raw VOD into finished vertical video clips. Triggered by a Discord command or [[entities/dashboard]] button. Each stage completes fully before the next begins. Implemented as a thin orchestrator (`scripts/clip-pipeline.sh`, 147 lines as of 2026-05-01) that sources shared helpers from `scripts/lib/pipeline_common.sh` and per-stage bash files from `scripts/stages/stage{1..8}.sh`. The Python logic embedded in each stage (Pass A/B/C, vision enrichment, transcription, summary) lives in `scripts/lib/stages/*.py` and is invoked via env-prefixed `python3` calls. See [[concepts/modularization-plan]] for the full layout.

Two optional sub-stages were added with the [[concepts/originality-stack]] in April 2026: **Stage 4.5** (moment groups) and **Stage 6.5** (camera-pan prep). Both short-circuit unless their feature flag is on, so they're invisible in the minimal/default pipeline.

---

## Stage map

```
Discord / Dashboard trigger
         ↓
1. Discovery            — find VOD, verify size, get duration
         ↓
2. Transcription        — chunked GPU Whisper, cached to disk
         ↓
3. Segment Detection    — classify stream type per 10-min window, build stream profile
         ↓
4. Moment Detection     — Pass A: keywords | Pass B: LLM | Pass C: merge + time-bucket
                          | Pass D: rubric judge (Tier-4) | MMR diversity rank (Tier-4)
         ↓
4.5 Moment Groups       — (wave C) narrative arcs + stitch bundles (optional)
         ↓
5. Frame Extraction     — 6 JPEGs per candidate (960×540)
         ↓
6. Vision Enrichment    — score boosts, titles, descriptions, originality hints
         ↓
6.5 Camera Pan Prep     — (wave E) OpenCV face track → per-clip camera path (optional)
         ↓
7. Editing & Export     — framing / originality / stitch render, batch captions
         ↓
8. Logging              — processed.log, diagnostics JSON, Discord report
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

## Tier-2 M1 — Speaker diarization (inside Stage 2)

When `HF_TOKEN` is set + `pyannote-audio` is installed, [[entities/speech-module]] runs speaker diarization right after WhisperX alignment. Each segment in `transcript.json` gains a `speaker` field. Skipped silently otherwise. See [[entities/diarization]].

---

## Tier-2 M2 — Audio events (between Stage 2 and Stage 3, 2026-04-27)

Right after Stage 2 finishes, the pipeline runs [[entities/audio-events]] (`scripts/lib/audio_events.py`) on the same audio file Whisper used. Produces `/tmp/clipper/audio_events.json` with per-30s-window scores for `rhythmic_speech`, `crowd_response`, and `music_dominance`. Pass A reads this file once and applies boost-only signals. When librosa isn't available the scanner writes an empty file and Pass A no-ops.

---

## Stage 2 — Transcription

As of 2026-04-23 (Phase 3), Stage 2 is a thin shell wrapper around [[entities/speech-module]] rather than an inline heredoc. Two backends with automatic fallback; see [[concepts/speech-pipeline]] for the full picture.

- **Unloads the LM Studio text + vision models** from VRAM so Whisper gets full GPU
- FFmpeg extracts audio to 16 kHz mono WAV at `/tmp/clipper/audio.wav`
- **Optional** vocal-stem separation via Demucs v4 (`config/speech.json::vocal_separation.enabled`, off by default)
- **Per-channel streamer-prompt biasing** — `config/streamer_prompts.json` matches the VOD filename and picks an `initial_prompt` that nudges decoding toward channel-specific vocabulary
- **Primary backend — WhisperX**: VAD-based chunking (no arbitrary 20-minute splits), batched faster-whisper inference, optional wav2vec2 forced alignment → frame-accurate word-level timestamps
- **Fallback backend — faster-whisper**: pre-Phase-3 code path preserved verbatim. Fires when WhisperX isn't installed (image built `SPEECH_STACK=slim`) or when WhisperX hits a runtime error mid-transcription
- **Default model** — `large-v3-turbo` (2.5× faster than `large-v3` with < 1 % WER loss)
- **Cached** to `vods/.transcriptions/` — re-clips skip this stage
- Output: `transcript.json` + `transcript.srt`

Performance: WhisperX + `large-v3-turbo` → **~12-18 min for a 2-hour VOD** on RTX 5060 Ti (roughly 3× faster than the pre-Phase-3 `large-v3` + 20-minute-chunks path). This stage no longer dominates total wall time.

> [!note] Backwards compat
> The pre-Phase-3 env vars `CLIP_WHISPER_MODEL` / `CLIP_WHISPER_DEVICE` / `CLIP_WHISPER_COMPUTE` still work — they win over `config/speech.json` when set. The dashboard Models panel continues to control Whisper without any changes.

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
- **Per-segment chunk windows** (Tier-1 Q4, 2026-04-27): 480 s / 60 s overlap on `just_chatting`+`irl`, 360 s / 45 s on `debate`, 300 s / 30 s on `reaction`+`gaming`. Window picked from segment type at `chunk_start + 150 s`.
- Context-aware: setup+payoff, irony, social dynamics, storytelling
- **Few-shot examples** (Tier-1 Q2, 2026-04-27): 3 transcript→JSON examples in the prompt cover setup-payoff w/ off-screen voice, long-form storytime, hot take w/ pushback
- **Prior-chunk context** (Tier-1 Q1, 2026-04-27): each chunk's prompt includes a one-line summary of the previous 2 chunks so the model can spot setup-payoff arcs that cross chunk boundaries (the canonical Lacy-penthouse callback)
- **Variable clip cap** (Tier-1 Q5, 2026-04-27): 150 s max for `storytime`/`emotional`, 90 s otherwise
- Returns JSON: `[{time, score, category, why}]`
- `think=false` required; applies segment score boosts for quieter segment types
- `call_llm()` per-call `timeout = 240 s` (was 600 s pre-2026-04-27 — see [[concepts/bugs-and-fixes#BUG 32]])
- **Network outage fail-fast**: 3 consecutive network-shaped failures (`Errno 101`, `Errno 111`, `Connection refused`, `Network is unreachable`, `timed out`, `Read timed out`, `Name or service not known`) trip `_LLM_NET_FAIL_STREAK ≥ 3`; the chunk loop logs `[PASS B] Aborting after chunk N: persistent LM Studio outage detected` and `break`s. Pass A keyword moments still flow through to Pass C, so the pipeline still produces clips — just without the AI moment-detection layer. Operator restarts Docker Desktop / LM Studio and reruns with `--force`. See [[concepts/bugs-and-fixes#BUG 32]].

**Pass B-global — Two-stage Pass B** (Tier-3 A1, 2026-04-27):
- Runs immediately after Pass B-local writes `llm_moments.json`
- Builds a stream skeleton from Tier-1 Q1's `chunk_summaries` (one line per chunk: `[MM:SS-MM:SS] (chunk i/N) <summary>`)
- ONE Gemma call asks for cross-chunk SETUP-PAYOFF arcs (irony / contradiction / fulfillment / theme_return / exposure / prediction)
- Validated arcs added to `llm_moments` with `category="arc"`, `cross_validated=True`, 1.4× score boost, plus `setup_time` / `setup_chunk` / `payoff_chunk` / `arc_kind`
- See [[concepts/two-stage-passb]]

**Pass B+ — Long-range callback detection** (Tier-2 M3, 2026-04-27):
- Runs after Pass B-global, before Pass C
- Embeds transcript with `sentence-transformers/all-MiniLM-L6-v2` (CPU), builds FAISS IndexFlatIP
- For each top-K Pass B candidate, searches for setups ≥ 5 min earlier with cosine ≥ 0.6
- Each surviving pair gated by a small Pass-B' LLM judgment ("is this a real callback?")
- Survivors added to `llm_moments` with `category="callback"`, `cross_validated=True`, ×1.5 score boost
- See [[concepts/callback-detection]]

**Pass C — Merge, deduplicate, time-bucket, select**:
- Normalizes keyword scores: per-category ceiling (Tier-1 Q3, 2026-04-27) — `storytime` 0.90, `hot_take`/`emotional`/`controversial` 0.85, `hype`/`reactive` 0.75, `funny`/`dancing` 0.70
- **Speaker boost** (Tier-2 M1, 2026-04-27): multi-speaker moments (`speaker_count >= 2 and dominant_speaker_share < 0.7`) get ×1.15
- Cross-validates: moments found by both passes get +1.5 boost + `cross_validated` flag
- Style weighting from `--style` flag
- Category cap: no single category > 60% of candidates (for `auto` style)
- **Time-bucket distribution**: VOD divided into equal buckets (2/hour, 3-10 range); Phase 1 guaranteed picks from each bucket; Phase 2 fills overflow slots; Phase 3 style re-ranking
- Enforces minimum 45-second gap between final selected clips
- Selects up to `MAX_CANDIDATES` (2× target clip count)

---

## Stage 4.5 — Moment Groups (optional, wave C)

Runs when `CLIP_STITCH=true` or `CLIP_NARRATIVE=true` (dashboard Originality panel). Calls `scripts/lib/moment_groups.py` with the final Pass C output.

- **Narrative group**: 2+ adjacent moments in `{storytime, emotional, hot_take}` within 120 s of each other, merged into one 45–90 s long clip.
- **Stitch group**: 3–4 short moments in `{funny, hype, reactive, dancing}`, each capped at 12 s, total ≈28 s. Rendered in Stage 7e as a concatenated composite.

Writes `/tmp/clipper/moment_groups.json` and patches `hype_moments.json` with `group_id` + `group_kind` (`solo` / `narrative` / `stitch`). Skipped entirely when both flags are false.

See [[concepts/originality-stack]] §Wave C.

---

## Phase 4.2 — Boundary snap (between Pass C and Stage 4.5)

As of 2026-04-24, right after Pass C writes `hype_moments.json`, a PYSNAP heredoc calls [[entities/boundary-detect-module]] to snap each moment's `(clip_start, clip_end)` to nearby Whisper word boundaries + silence gaps. See [[concepts/boundary-snap]].

---

## Tier-3 A2 — Setup-frame extraction (inside Stage 5)

For moments tagged `callback` (Tier-2 M3) or `arc` (Tier-3 A1), Stage 5 extracts 2 additional frames at `setup_time-1` and `setup_time+1`. Stage 6 prepends them to the 6 standard payoff frames so the VLM can verify visual continuity (same person / scene drives both halves of the arc). Falls through silently for moments without `setup_time`.

---

## Stage 5 — Frame Extraction

As of 2026-04-23 (Phase 0.1), Stage 5 extracts **6 frames at targeted payoff-window offsets** around each peak `T`:

| Filename | Offset |
|---|---|
| `frames_${T}_tminus2.jpg` | T−2s (pre-peak setup) |
| `frames_${T}_t0.jpg` | T+0s (peak) |
| `frames_${T}_tplus1.jpg` | T+1s |
| `frames_${T}_tplus2.jpg` | T+2s |
| `frames_${T}_tplus3.jpg` | T+3s (typical payoff) |
| `frames_${T}_tplus5.jpg` | T+5s (aftermath) |

Implementation: one `ffmpeg -ss <absolute>` call per offset — fast seek, single frame, `scale=960:540 q:v 2`. Offsets are defined in `FRAME_OFFSETS` in `scripts/clip-pipeline.sh`. When `T + offset < 0`, the offset clamps to 0. `-nostdin` prevents stdin conflicts in the bash loop.

> [!warning] Pre-2026-04-23: uniform-fps sweep, only 2 frames used downstream
> The old implementation ran one `ffmpeg -vf fps=1/5 -frames:v 6` starting at T−15, producing frames at T−15..T+10. Stage 6 then only fed frames `03`/`04` (= T−5 and T+0) to the VLM. Recorded as BUG 25 in [[concepts/bugs-and-fixes]].

---

## ~~Phase 4.1 — UI chrome masking + overlay OCR~~ (REMOVED 2026-05-01)

Phase 4.1 was deleted from the pipeline on 2026-05-01 after [[concepts/bugs-and-fixes#BUG 49]] (PaddleOCR wedging the pipeline mid-frame) and [[concepts/bugs-and-fixes#BUG 50]] (MOG2 frame-spacing mismatch made the detector dead code). Stage 5 frames now flow directly to Stage 6 unmodified. The grounding cascade still hard-fails sub/bit/raid/donation claims when chat shows zero events — that channel was always the load-bearing piece, not OCR. See [[concepts/chrome-masking]] (tombstoned) for the historical design.

---

## Stage 6 — Vision Enrichment

See [[concepts/vision-enrichment]] for full detail.

- Sends all 6 payoff-window frames (T-2 / T+0 / T+1 / T+2 / T+3 / T+5) to a multimodal model (Gemma 4 or Qwen 3.5 — see [[entities/qwen35]] and [[entities/lm-studio]]) via the LM Studio OpenAI-compatible API
- **Tier-3 A2** (2026-04-27): for callback/arc moments, prepends 2 SETUP frames; prompt becomes setup-aware ("frames 1-2 are earlier setup, 3+ are payoff"); model returns a `callback_confirmed` 0-10 score that multiplies `final_score` by `[0.85, 1.20]` — the only Stage 6 path that can PENALIZE a moment (vision_score is bonus-only otherwise) because for callbacks, the visual continuity IS the substantive evidence
- Unified prompt returns `{score, category, title, description, hook, mirror_safe, chrome_regions, voiceover, callback_confirmed?}` — the last three drive the [[concepts/originality-stack]]
- Score blending (additive only — can never reduce a score)
- 20-minute stage timeout + 90-second per-moment timeout
- **Every moment that survived Stage 4 WILL be rendered** — vision cannot eliminate candidates
- **Network outage fail-fast** (mirror of Pass B): `_VISION_NET_FAIL_STREAK ≥ 3` flips `skip_vision = True` for every remaining moment. Each moment still renders with its transcript-baseline title/description and the keyword-blended score; only the AI title/description boost is bypassed. See [[concepts/bugs-and-fixes#BUG 32]].

---

## Stage 6.5 — Camera Pan Prep (optional, wave E)

Runs when `CLIP_CAMERA_PAN=true` **and** `CLIP_FRAMING=camera_pan`. Invokes `scripts/lib/face_pan.py prepare` once per non-stitch moment:

- OpenCV Haar cascade detects faces at 2 fps across the clip window.
- Tracker smooths a virtual crop path (608×1080) that pans between detected speakers, rotating targets every ~4 s to break per-frame visual hashing.
- Writes `/tmp/clipper/clip_<T>_campath.json`; Stage 7's `camera_pan` filter case reads it via `face_pan.py --emit-filter`.

When zero faces are detected across the clip, the file isn't written and Stage 7 falls back to `blur_fill` for that clip.

---

## Stage 7 — Editing and Export

See [[concepts/clip-rendering]] for full detail.

1. Generate clip manifest — vision titles used as filenames (sanitized), 9 columns incl. `clip_start` + `clip_duration`
2. Extract all clip audio segments (single FFmpeg pass)
3. Batch caption transcription (single Whisper model load for all clips)
4. **Render all clips** — per-clip randomized params (via `scripts/lib/originality.py`), framing-mode select (`blur_fill` / `smart_crop` / `centered_square` / `camera_pan`), optional Piper TTS voiceover mix, optional music-bed mix. Export at 1080×1920 H.264 CRF 20 preset slow High@4.2 18 Mbps + AAC 192 k
5. **Stage 7e — stitch groups**: `scripts/lib/stitch_render.py` renders each stitch group as one composite (members concatenated with `xfade` transitions)
6. Unload Whisper, proceed to Stage 8

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
