# Wiki Index

Content catalog. Updated on every ingest. Read this first when answering queries — find relevant pages here, then drill in.

---

## Overview
- [[overview]] — Full synthesis: architecture, pipeline, models, interfaces, design decisions
- [[concepts/moment-discovery-upgrades]] — Hub for the Tier-1/2/3 moment-discovery upgrade plan (Q1–Q5, M1–M3, A1–A3)
- [[concepts/tier-4-conversation-shape]] — Tier-4 plan: conversation shape detection + Pass D rubric judge (per-phase 4.1–4.8)
- [[sources/implementation-plan]] — Hub for the Phase 0–5 implementation plan (frame sampling, grounding, chat, speech, masking, model split)

## Entities

### Models
- [[entities/faster-whisper]] — Speech-to-text; large-v3 on GPU (float16) or CPU (int8); Stages 2 and 7
- [[entities/qwen35]] — Default multimodal model (`qwen/qwen3.5-9b` / `qwen3.5-35b-a3b` or Gemma 4 `gemma-4-26b-a4b`); text + vision; Stages 3–4 and 6
- [[entities/qwen3-vl]] — *retired* — previous dedicated vision model; kept for historical context
- [[entities/qwen25]] — Discord agent model (older reference; current setup uses same LM Studio model for agent and pipeline)
- [[entities/piper]] — local CPU TTS for wave-D voiceover layer
- [[entities/librosa]] — audio feature extraction for tier-C music matching
- [[entities/face-pan]] — OpenCV Haar face tracker for wave-E camera pan

### Infrastructure
- [[entities/openclaw]] — Agent framework (Node.js); Discord gateway; runs exec tool to invoke pipeline
- [[entities/lm-studio]] — LLM inference server (native Windows); OpenAI-compatible API on port 1234; 9B vs 35B behavior; reasoning_content fallback
- [[entities/ollama]] — *Retired* — former LLM inference container; replaced by LM Studio as of 2026-04-18
- [[entities/ffmpeg]] — Video/audio processing; blur-fill 9:16 rendering; subtitle burn-in
- [[entities/discord-bot]] — Primary user interface; natural-language commands; delivers clip attachments
- [[entities/dashboard]] — Web UI (Flask, port 5000); 8-stage monitor; SSE streaming; docker exec bridge; Models + Hardware panels
- [[entities/grounding]] — 2-tier grounding cascade (regex denylist + content overlap → main-model LLM judge); used by Pass B and Stage 6
- [[entities/lmstudio]] — minimal HTTP client used by the grounding cascade's LLM judge call
- [[entities/chat-fetch]] — VOD chat acquisition (anonymous Twitch GraphQL + TwitchDownloader importer)
- [[entities/chat-features]] — stdlib feature extractor for Pass A' chat scoring and prompt grounding
- [[entities/speech-module]] — Stage 2 transcription wrapper (WhisperX primary, faster-whisper fallback)
- [[entities/vocal-sep-module]] — optional Demucs v4 vocal-stem separator for music-heavy streams
- [[entities/chrome-mask-module]] — *removed 2026-05-01* — Phase 4.1 UI overlay detection (MOG2) + overlay-text extraction (PaddleOCR); see BUG 49/50
- [[entities/boundary-detect-module]] — Phase 4.2 clip boundary snap to sentence + silence gaps
- [[entities/self-consistency-module]] — Phase 5.2 N-candidate ranking (USC + reference grounding)
- [[entities/bootstrap-twitch-clips]] — Phase 5.3 research tool for bootstrapping a Twitch-clip eval dataset
- [[entities/audio-events]] — Tier-2 M2 librosa scanner: rhythmic / crowd / music boost-only signals
- [[entities/diarization]] — Tier-2 M1 WhisperX/pyannote speaker labeling for Pass A and Pass C boost
- [[entities/callback-module]] — Tier-2 M3 long-range setup→payoff detector (sentence-transformers + FAISS + LLM judge)

## Concepts

### Pipeline
- [[concepts/clipping-pipeline]] — All stages (incl. optional 4.5 and 6.5) with detail, performance table, temp files
- [[concepts/segment-detection]] — Stage 3: 5-type classification, stream profile, segment-aware weighting
- [[concepts/highlight-detection]] — Stage 4: three-pass (keywords + LLM + merge/time-bucket/select)
- [[concepts/vision-enrichment]] — Stage 6: non-gatekeeping design, score blending, originality hints
- [[concepts/clip-rendering]] — Stage 7: framing modes, per-clip randomization, stitch concat, audio mix
- [[concepts/captions]] — Subtitle style, hook card (top-of-video), per-clip palette/position randomization
- [[concepts/speed-control]] — Dashboard speed dropdown (1×–1.5×), setpts + rubberband, SRT rescaling
- [[concepts/originality-stack]] — TikTok 2025 defense: waves A (randomize) + B (framing) + C (groups) + D (TTS/music) + E (camera pan)

### System
- [[concepts/vram-budget]] — Per-model VRAM, stage-by-stage orchestration, explicit unloading sequence
- [[concepts/context-management]] — Token compaction, session reset, history limit, compat flags
- [[concepts/deployment]] — Hardware requirements, LM Studio setup, Docker setup, step-by-step guide
- [[concepts/image-slimming]] — Externalized model caches, requirements files, ORIGINALITY_STACK build arg, Asset Cache panel
- [[concepts/modularization-plan]] — 4-phase plan to break clip-pipeline.sh, dashboard/app.py, dashboard/static/app.js into focused modules

### Reference
- [[concepts/bugs-and-fixes]] — 37 bugs documented; quick-nav table by category; symptoms, root causes, solutions
- [[concepts/open-questions]] — Score normalization, variable clip length, model switcher UI, known gaps
- [[concepts/chat-signal]] — Phase 2 Pass A' architecture: Twitch chat → burst / emote density / hard event counts
- [[concepts/speech-pipeline]] — Phase 3 Stage 2 architecture: WhisperX VAD + batched ASR + forced alignment, with faster-whisper fallback
- [[concepts/chrome-masking]] — *removed 2026-05-01* — Phase 4.1 UI overlay masking + OCR; tombstoned with historical record; see BUG 49/50
- [[concepts/boundary-snap]] — Phase 4.2 pragmatic variable-length windows via sentence + silence gap snapping
- [[concepts/model-split]] — Phase 5.1 optional per-stage model overrides (Pass B text-only, Stage 6 vision-specialist)
- [[concepts/self-consistency]] — Phase 5.2 N-candidate ranking for hallucination suppression
- [[concepts/callback-detection]] — Tier-2 M3 architecture: cosine search + LLM judgment for cross-chunk arcs
- [[concepts/two-stage-passb]] — Tier-3 A1 architecture: per-chunk skeleton + single global Gemma call for arc detection
- [[concepts/moment-discovery-upgrades]] — Tier-1/2/3 hub page: how Q1–Q5, M1–M3, A1–A3 fit together

## Sources
- [[sources/openclaw-stream-clipper-summary]] — Full system architecture doc (project summary)
- [[sources/development-summary]] — Feature list, 10 bugs, file inventory, current state (2026-04-04)
- [[sources/fix-txt]] — User questions: score normalization, variable clip length, model switcher
- [[sources/implementation-plan]] — Phase 0–5 roadmap synthesizing `ClippingResearch.md` against the codebase
