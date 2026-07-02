# Wiki Index

Content catalog. Updated on every ingest. Read this first when answering queries — find relevant pages here, then drill in.

> **Searching this wiki**: current state → [[hot]]; recent activity → `grep "^## \[" log.md | head`; a bug → quick-nav at top of [[concepts/bugs-and-fixes]]; plan lifecycle → `grep -rl "^status: in-progress" concepts/`; health check → `python scripts/wiki_lint.py`.

---

## Overview
- [[hot]] — **Start here**: bounded current-state digest (models, in-flight work, recent changes, landmines)
- [[overview]] — Full synthesis: architecture, pipeline, models, interfaces, design decisions
- [[concepts/moment-discovery-upgrades]] — Hub for the Tier-1/2/3 moment-discovery upgrade plan (Q1–Q5, M1–M3, A1–A3)
- [[concepts/tier-4-conversation-shape]] — Tier-4 plan (**status: planned**): conversation shape detection + Pass D rubric judge (per-phase 4.1–4.8)
- [[sources/implementation-plan]] — Hub for the Phase 0–5 implementation plan (frame sampling, grounding, chat, speech, masking, model split)

## Entities

### Models
- [[entities/faster-whisper]] — Speech-to-text; WhisperX (VAD+align) + faster-whisper fallback; CTranslate2 + cuDNN on GPU; Stages 2 and 7
- [[entities/qwen35]] — Qwen text family; pipeline runs the **unified `qwen3.6-35b-a3b`** (text+vision, multimodal, ~3B active) per `config/models.json`; `qwen3.5-9b` is the separate Discord agent model
- [[entities/qwen3-vl]] — **un-retired 2026-06-04** — dedicated VLM family (8B Instruct + 30B-A3B Instruct); the agent's fallback model
- [[entities/gemma4]] — Google's unified multimodal family; was the `vision_model` (12B), **now superseded by `qwen3.6-35b-a3b`**; 26B-A4B is the quality-tier alternative
- [[entities/qwen25]] — *Superseded* — original Discord agent model (`qwen2.5:7b` on Ollama); agent is now `qwen3.5-9b` on LM Studio
- [[entities/piper]] — local CPU TTS for wave-D voiceover layer
- [[entities/librosa]] — audio feature extraction for tier-C music matching
- [[entities/face-pan]] — OpenCV Haar face tracker for wave-E camera pan

### Infrastructure
- [[entities/openclaw]] — Agent framework (Node.js); Discord gateway; runs exec tool to invoke pipeline
- [[entities/pipeline-orchestrator]] — `scripts/run_pipeline.py`; post-bare-metal-port orchestrator that drives the 8 stages; `--vod`/`--vods`/`--all`/`--force` semantics, persistent-log slugs
- [[entities/lm-studio]] — LLM inference server (native, localhost:1234); HTTP inference + **lms-CLI** model load/unload; 9B vs 35B thinking; reasoning_content fallback
- [[entities/ollama]] — *Retired* — former LLM inference container; replaced by LM Studio as of 2026-04-18
- [[entities/ffmpeg]] — Video/audio processing; blur-fill 9:16 rendering; subtitle burn-in
- [[entities/discord-bot]] — Primary user interface; natural-language commands; delivers clip attachments
- [[entities/dashboard]] — Web UI (Flask, native port 5001; 5000 legacy); 8-stage monitor; SSE streaming; Models + Hardware panels
- [[entities/grounding]] — 2-tier grounding cascade (regex denylist + content overlap → main-model LLM judge); used by Pass B and Stage 6
- [[entities/lmstudio]] — `lmstudio.py` HTTP **client module** for the grounding judge call (not the [[entities/lm-studio]] server)
- [[entities/vision-judge]] — **Stage 5.5** multimodal tournament re-ranker; lets vision *select* which moments win (Plan 1.a)
- [[entities/chat-fetch]] — VOD chat acquisition (anonymous Twitch GraphQL + TwitchDownloader importer)
- [[entities/chat-features]] — stdlib feature extractor for Pass A' chat scoring and prompt grounding
- [[entities/speech-module]] — Stage 2 transcription wrapper (WhisperX primary, faster-whisper fallback)
- [[entities/vocal-sep-module]] — optional Demucs v4 vocal-stem separator for music-heavy streams
- [[entities/chrome-mask-module]] — *removed 2026-05-01* — Phase 4.1 UI overlay detection (MOG2) + overlay-text extraction (PaddleOCR); see BUG 49/50
- [[entities/boundary-detect-module]] — Phase 4.2 clip boundary snap to sentence + silence gaps
- [[entities/self-consistency-module]] — *removed 2026-06-12* — Phase 5.2 N-candidate ranker; never imported by any stage (orphan)
- [[entities/bootstrap-twitch-clips]] — Phase 5.3 research tool for bootstrapping a Twitch-clip eval dataset
- [[entities/audio-events]] — Tier-2 M2 librosa scanner: rhythmic / crowd / music boost-only signals
- [[entities/audio-sense-module]] — `audio_sense.py`: CLAP/PANNs/faster-whisper semantic sensing + installed-models record (CLAP 1.2GB, whisper 142MB, PANNs 327MB opt-in) + install steps & env caveats
- [[entities/visual-sense-module]] — `visual_sense.py`: clip-forensics Phase 3 — cv2 motion punches + EasyOCR caption OCR (wps); EasyOCR --no-deps install; the clip_forensics hang-proof watchdog
- [[entities/diarization]] — Tier-2 M1 WhisperX/pyannote speaker labeling for Pass A and Pass C boost
- [[entities/callback-module]] — Tier-2 M3 long-range setup→payoff detector (sentence-transformers + FAISS + LLM judge)

## Concepts

### Pipeline
- [[concepts/evaluation-status-2026-06]] — **Consolidated tracker**: the whole 2026-06 originality+calibration evaluation in one file with a verified done/not-done audit
- [[concepts/model-senses]] — Perception inventory: what each model "senses" (speech-only / 3 audio dials / 6 still frames) + the two blind spots
- [[concepts/multimodal-fusion-2026-07]] — Evaluation: where audio/vision converge today + 5 fusion options expanded w/ the dual-GPU (28GB pool vs 16GB CUDA) serving distinction
- [[concepts/reference-humor-2026-07]] — Evaluation: clipping jokes whose context lives outside the VOD (George Bush meme) — proxy lane, chat mining, recognition probe, format library
- [[concepts/plan-clip-forensics]] — Plan + research handoff: semantic audio/visual sensing (CLAP/PANNs…) + decompose curated reference clips → style profiles (**shipped 2026-06-21**: Phase 1-3 + 4b LLM essence + watchdog; only Phase 4a exact-SFX deferred)
- [[concepts/clip-forensics-research-2026-06]] — Research output: verified tool matrix + license flags + architecture + engineering prompt; **Phase 1-3+4b built + verified 2026-06-21** (`audio_sense.py` + `visual_sense.py` + `clip_forensics.py`)
- [[concepts/clipping-intelligence]] — **Hub + evaluation** of the whole prompt-engineering & heuristics stack (Pass A→D + vision + grounding): how each layer decides "clip-worthy", strengths/weaknesses/opportunities
- [[concepts/plan-calibration-loop]] — Fit the ~50 hand-tuned multipliers vs Twitch-clip labels: offline re-scorer + fitter (**planned 2026-06-12**)
- [[concepts/plan-decorrelate-judges]] — Split Pass D / vision-judge onto a different model family via 2 config keys (**planned 2026-06-12**)
- [[concepts/clipping-quality-overhaul]] — **Roadmap** (**status: in-progress**) to fix bad clips: promote the multimodal model to *judge*, arc-driven duration, hook boundaries, kinetic captions; differentiation stance vs commercial clippers
- Selection sub-plans (per north-star axis): [[concepts/plan-arc-completeness]], [[concepts/plan-reaction-worthy]], [[concepts/plan-baseline-contrast]], [[concepts/plan-engagement-discussion]] (**in-progress**, axis scorers built 2026-06-04) + [[concepts/plan-batch-diversity]] (**planned**, not yet built)
- [[concepts/observability]] — **Diagnostics & axis tuning**: `axis_report`/`stage_timings`/`judge_tournament` JSON, rank churn, and the `logtool axes` tune→run→diff view
- [[concepts/clipping-pipeline]] — All stages (incl. optional 4.5 and 6.5) with detail, performance table, temp files
- [[concepts/segment-detection]] — Stage 3: 5-type classification, stream profile, segment-aware weighting
- [[concepts/highlight-detection]] — Stage 4: keywords + LLM + Pass C re-rank (selection axes A/B/C/E) + Pass D rubric → Stage 5.5 [[entities/vision-judge]]
- [[concepts/detection-walkthrough]] — **End-to-end walkthrough** of Stage 3 (segment) + Stage 4 (moment) detection and how they connect
- [[concepts/detection-improvements]] — **Design answers** (**shipped**): finer segments, embedding keywords, stitched setup→payoff, length-neutral duration
- [[concepts/detection-improvements-plan]] — File:line-anchored implementation plans for those 4 fixes (**all 4 shipped 2026-06-06**)
- [[concepts/plan-youtube-informative]] — Storytime 1.5–3 min fixes + YouTube/informative ingest (`--source youtube`, new category) (**planned 2026-06-12**)
- [[concepts/clip-duration]] — How clip length is decided (no hard 30s clamp; default-fallback + length_penalty), chunk windowing, cross-chunk limits
- [[concepts/vision-enrichment]] — Stage 6: non-gatekeeping design, score blending, originality hints
- [[concepts/clip-rendering]] — Stage 7: framing modes, per-clip randomization, stitch concat, audio mix
- [[concepts/captions]] — Subtitle style, hook card (top-of-video), per-clip palette/position randomization
- [[concepts/transition-animations]] — White flashes + LLM/rule jump-cut compression (Stage 7d.5); flag-gated
- [[concepts/speed-control]] — Dashboard speed dropdown (1×–1.5×), setpts + rubberband, SRT rescaling
- [[concepts/originality-stack]] — TikTok 2025 defense: waves A (randomize) + B (framing) + C (groups) + D (TTS/music) + E (camera pan)
- [[concepts/plan-unoriginality-audio-layer]] — Why clips still get flagged: audio is the un-perturbed channel; SFX/VO/music plan (**planned 2026-06-12**)
- [[concepts/sfx-cue-taxonomy-2026-06]] — Research **+ shipped**: beat→sound→offset→mix cue taxonomy; `config/sfx_cues.json` + `sfx_cues.py` acoustic anchors, per-kind mix (**shipped 2026-06-13**)
- [[concepts/tiktok-originality-mechanics-2026-06]] — Research: how TikTok's unoriginal flag works; ranked Tier A/B/C transforms; VO > SFX/music, account-level risk (**reference 2026-06-12**)
- [[concepts/hook-engineering-2026-06]] — Research **+ shipped**: cold-open teaser (`cold_open.py`, `CLIP_COLD_OPEN`) + category hook-text templates (`hook_templates.json`) (**shipped 2026-06-13**)

### System
- [[concepts/vram-budget]] — Per-model VRAM, stage-by-stage orchestration, explicit unloading sequence; GGUF-exact KV + per-stage max_tokens + "bigger context ≠ better"
- [[concepts/vram-context-tooling]] — Cross-vendor VRAM observability + GGUF-exact context recommendation (vram_log / gguf_meta / model_registry / logtool vram / dashboard); engine-agnostic, workload-aware
- [[concepts/context-management]] — Token compaction, session reset, history limit, compat flags
- [[concepts/bare-metal-windows]] — **native Windows (no Docker)**: Python orchestrator, venv, dashboard + Discord native mode (2026-06-04)
- [[concepts/deployment]] — Hardware requirements, LM Studio setup, Docker setup, step-by-step guide (legacy; superseded by bare-metal-windows)
- [[concepts/image-slimming]] — Externalized model caches, requirements files, ORIGINALITY_STACK build arg, Asset Cache panel
- [[concepts/modularization-plan]] — 4-phase plan to break clip-pipeline.sh, dashboard/app.py, dashboard/static/app.js into focused modules (**shipped 2026-05-01**)
- [[concepts/asset-libraries]] — CC0 SFX/music/B-roll/Twemoji seed pack and `scripts/seed_libraries.py`; data layer for the editing-profile plan
- [[concepts/style-profiles]] — Per-category AI editing profiles (zoom punches, freeze frames, slow-mo, meme cutaways, B-roll inserts, SFX cues, kinetic captions, fingerprint perturbation); dispatched by Stage 7 when `chk-style-profiles` is on

### Reference
- [[concepts/bugs-and-fixes]] — Bug + removal registry (64 bugs + 2 removals as of 2026-06-12; latest BUG 64 white-flash regression, BUG 63 stitch never fired); complete quick-nav by category at top
- [[concepts/open-questions]] — Score normalization, variable clip length, model switcher UI, known gaps
- [[concepts/chat-signal]] — Phase 2 Pass A' architecture: Twitch chat → burst / emote density / hard event counts
- [[concepts/speech-pipeline]] — Phase 3 Stage 2 architecture: WhisperX VAD + batched ASR + forced alignment, with faster-whisper fallback
- [[concepts/chrome-masking]] — *removed 2026-05-01* — Phase 4.1 UI overlay masking + OCR; tombstoned with historical record; see BUG 49/50
- [[concepts/boundary-snap]] — Phase 4.2 pragmatic variable-length windows via sentence + silence gap snapping
- [[concepts/model-split]] — Phase 5.1 optional per-stage model overrides (Pass B text-only, Stage 6 vision-specialist); updated 2026-06-04 tier tables
- [[concepts/vlm-comparison-2026-06]] — Head-to-head: Qwen3-VL vs Gemma 4 vs Qwen3.5 for the clipper workload; recommends Qwen3-VL-8B
- [[concepts/text-comparison-2026-06]] — Head-to-head: Qwen 3.6 hybrid (avoid) vs gpt-oss-20b vs Gemma 4 for the text slot; recommends Gemma 4 12B (IFEval 88.9)
- [[concepts/case-rap-battle-missed]] — Case study: rakai 2026-04-24 Delaware freestyle missed by Pass A + Pass B + audio_events; concrete keyword/prompt/diarization tuning recommendations
- [[concepts/case-incongruity-comedy]] — Case study: competitor reference clips reveal the cross-channel incongruity blind spot; anomaly-proposer + micro-clip plan (**planned 2026-06-12**)
- [[concepts/pipeline-optimizations-2026-06]] — Parallelization + RMS-gate + dead-chunk pre-filter sweep; implemented Stage 5/7 ffmpeg parallel + Pass B pre-filter + audio events RMS gate; ~1.6× combined wall-clock lift expected
- [[concepts/clip-quality-remediation-2026-06]] — **Plan** from the 6/6 session review: fix vision REGEN→garbage titles, gate/parallelize Stage 5.5 (620s), score-display saturation, torchcodec; file:line-anchored
- [[concepts/pass-b-false-negatives]] — Why Pass B (LLM detection) drops clip-worthy moments + mitigations; failed-chunk re-queue + de-tidy prompt shipped 2026-06-06
- [[concepts/self-consistency]] — *removed 2026-06-12* — Phase 5.2 N-candidate ranking; architectural record kept for any revival
- [[concepts/callback-detection]] — Tier-2 M3 architecture: cosine search + LLM judgment for cross-chunk arcs
- [[concepts/two-stage-passb]] — Tier-3 A1 architecture: per-chunk skeleton + single global Gemma call for arc detection (+ §Evaluation: 15-word-summary weakness)
- [[concepts/arc-aware-extraction]] — Fix A1's 15-word bottleneck with structured "chunk cards" (Chain-of-Density + claim/prediction extraction) (**in-progress**: phases 1–3 shipped)
- [[concepts/moment-discovery-upgrades]] — Tier-1/2/3 hub page: how Q1–Q5, M1–M3, A1–A3 fit together

## Sources
- [[sources/openclaw-stream-clipper-summary]] — Full system architecture doc (project summary)
- [[sources/development-summary]] — Feature list, 10 bugs, file inventory, current state (2026-04-04)
- [[sources/fix-txt]] — User questions: score normalization, variable clip length, model switcher
- [[sources/implementation-plan]] — Phase 0–5 roadmap synthesizing `ClippingResearch.md` against the codebase
