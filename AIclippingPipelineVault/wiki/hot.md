---
title: "Hot — current state & recent activity"
type: overview
tags: [hot, hub, status]
updated: 2026-06-21
---

# Hot

Bounded current-state digest (cap ~100 lines — when you add, prune).
Rewritten freely; [[log]] is the append-only record, this is the cache.
Update this whenever you prepend a [[log]] entry: add the one-liner, drop
anything stale (>~2 weeks), and refresh the state table if defaults/flags/models changed.

## State snapshot
| Fact | Value | Since | Detail |
|---|---|---|---|
| Default architecture | bare-metal Windows (Python orchestrator) | 2026-06-04 | [[concepts/bare-metal-windows]] |
| Orchestrator entry | `scripts/run_pipeline.py` (`--vods` / `--all` / `--force`) | 2026-06-04 | [[concepts/clipping-pipeline]] |
| text_model | qwen/qwen3.6-35b-a3b (unified MoE, ~3B active) | 2026-06 | [[entities/qwen35]] |
| vision_model | qwen/qwen3.6-35b-a3b (same unified model) | 2026-06 | [[entities/qwen3-vl]] |
| Context length | 32768 | 2026-06 | [[concepts/vram-budget]] |
| Transcription | faster-whisper large-v3-turbo, word-level SRT | 2026-06 | [[entities/faster-whisper]] |
| Captions | CapCut word-box, bundled Montserrat Black | 2026-06-06 | [[concepts/captions]] |
| Stage 7 encode | h264_nvenc by default (libx264 fallback) | 2026-06-06 | [[concepts/clip-rendering]] |
| Latest bug | BUG 64 (white-flash painted clips white, fixed) | 2026-06-07 | [[concepts/bugs-and-fixes]] |
| Docker | legacy, superseded by bare-metal | 2026-06-04 | [[concepts/bare-metal-windows]] |

## In flight / awaiting validation
- **Clip-forensics SHIPPED — Phase 1-3 + 4b + watchdog (2026-06-21)** — offline decomposer is functionally complete: `audio_sense.py` (CLAP default; PANNs opt-in) + `visual_sense.py` (cv2 motion + EasyOCR captions) + `clip_forensics.py` (cuts/censor/music-bed/motion/captions + **LLM style_profile**). **Verified on `ReemKnocksClip.MP4`:** CLAP 14 events, whisper 18 words, 6 cuts, 9 motion spikes, OCR 13.08 wps (real text), LLM essence coherent (122 s). Install records + env caveats: **[[entities/audio-sense-module]]**, **[[entities/visual-sense-module]]**. **Hang-proof watchdog:** every stage under `_with_deadline` (cap → abandon → partial result; `_stages` status); total runtime ≤ sum of caps. Defaults: PANNs opt-in (torch≥2.9 stall), CPU default, EasyOCR `--ocr` opt-in, LLM `--no-llm` opt-out. Phase 4a exact-SFX deferred (needs seeded SFX lib). Plan [[concepts/plan-clip-forensics]] (shipped). *Open: CLAP cosines run low (~0.26–0.32) → `clap_threshold` 0.30, calibrate per-corpus vs `reference_clips/*.notes.json`.*
- **One-file status tracker: [[concepts/evaluation-status-2026-06]]** (2026-06-13) — verified done/not-done for the whole originality+calibration evaluation. Honest gap: the audio plan's *strongest* unoriginality levers (VO `tts_vo`, `music_bed`, `eq_tilt`) are still OFF, so what shipped (SFX/hooks/cold-open) is Tier-B engagement, not the fingerprint fix. Not started: calibration loop, decorrelation, YouTube/informative, anomaly-proposer.
- **2026-06-13 SFX + hook impl shipped, needs a real-VOD render check**: acoustic SFX cues are ON by default *inside profile-mode* (`CLIP_SFX_ANCHOR`, punchline boom rides hot); cold-open teaser (`CLIP_COLD_OPEN`) + hook templates are off/fallback. Watch: boom asset alias plays, SFX don't drown speech, cold-open seam is clean. Only boom has assets among the new kinds — scratch/sad_trombone/applause/crickets/bruh still need CC0 seeding — [[concepts/sfx-cue-taxonomy-2026-06]]
- **2026-06-12 detection fixes shipped, need a real-VOD validation run**: word-boundary keywords (default ON — watch Pass A recall), rare-pattern bonus (re-run rakai VOD: does the Delaware battle win its bucket now?), `CLIP_SEGMENT_VOTES=3` opt-in A/B — [[concepts/clipping-intelligence]] §Opportunity D
- **Audio-layer plan reframed (2026-06-12)** by [[concepts/tiktok-originality-mechanics-2026-06]]: the win is *genuine transformation* (voiceover/commentary Tier A) not fingerprint perturbation (Tier C, refuted); turn on `tts_vo` with real commentary first; account-level escalation makes half-measures risky — [[concepts/plan-unoriginality-audio-layer]]
- **2026-06-12 evaluation filed 5 plan pages** — unoriginality root cause = un-perturbed audio channel ([[concepts/plan-unoriginality-audio-layer]]); calibration loop glue ([[concepts/plan-calibration-loop]]); judge decorrelation ([[concepts/plan-decorrelate-judges]]); incongruity anomaly-proposer + micro-clips ([[concepts/case-incongruity-comedy]]); YouTube/informative ingest ([[concepts/plan-youtube-informative]])
- Transition animations (white-flash + jump-cut compression) shipped flag-gated; BUG 64 fix lands the flash — needs a clean validation run (`CLIP_JUMP_CUTS=gaps` safest first) — [[concepts/transition-animations]]
- Existing white-flashed clips are unrecoverable (transition pass `os.replace`d the good render) — re-run to regenerate — [[concepts/bugs-and-fixes]]
- Stitch-short still never forms in production (only 1 eligible per category, not a bug) — verify with `moment_groups.py --explain` — [[concepts/originality-stack]]
- Arc-stitch FORMED groups in production but stays inert at default ratio on rich VODs — lower `CLIP_ARC_GUARANTEE_MIN_RATIO` (~0.45) to give arcs a slot — [[concepts/arc-aware-extraction]]
- 4 detection fixes shipped flag-gated/failure-soft; Fix 4 (length-neutral) strongly validated, Fixes 2/3 not yet exercised on a known-arc VOD — [[concepts/detection-improvements-plan]]
- Fix 2 finding: short category prototypes only mildly discriminative (cosine ~0.15–0.27); follow-up is richer `config/patterns.json` signatures — [[concepts/detection-improvements-plan]]

## Recent changes (last ~10, one line each, newest first)
- [2026-07-03] **Phase 1 modules built**: `event_timeline.py` (fused symbolic stream) + `anomaly_propose.py` (8s windows, reaction×unexplained, few-shot verifier) — logic unit-verified (George-Bush proposes, precision controls hold); live Stage-4 wiring pending — [[log]]
- [2026-07-03] **EXECUTION STARTED — Phase 0.0 shipped**: `scripts/research/phase_runner.py` (launch/wait/evaluate/state) built + unit-verified; launches run_pipeline.py detached via the **.venv python** (cu128), evaluates via diagnostics+axis+forensics-on-output+baseline-moment-diff. Recon: 5 VODs present, LM Studio up, transcriptions cached → real runs feasible — [[concepts/plan-pipeline-upgrade-2026-07]] — [[log]]
- [2026-07-03] **Phase-runner → DELIVERABLE (Phase 0.0)** per owner directive: the executing agent BUILDS `phase_runner.py` (launch/wait/evaluate/state) first, then all gated VOD runs execute through it; deterministic-artifact baselines (not rendered bytes); LM Studio uptime committed by owner — [[concepts/plan-pipeline-upgrade-2026-07]] — [[log]]
- [2026-07-03] **Phase-runner protocol** added to the upgrade plan: real-VOD gating table + batching trick (one instrumented run serves P0+P1+P4) + autonomous loop design (detached launch, marker-file waits, auto-eval via forensics-on-own-output, phase_state.json, session-limit resumability) — [[concepts/plan-pipeline-upgrade-2026-07]] — [[log]]
- [2026-07-03] **Engineering plan filed** ([[concepts/plan-pipeline-upgrade-2026-07]]): 6 build phases — P0 validation run + omni smoke test clears the 🟡s, P1 anomaly lane (8s windows, few-shot verifier), P2 chat mining (auto-ROI, 7s lag), P3 judge/probe, P4 calibration+decorrelation, P5 meme lib, P6 conditional omni — with flags, params, per-phase DoD — [[log]]
- [2026-07-03] **RQ1-4 research filed** ([[concepts/master-research-2026-07]], 114 claims, UNVERIFIED — panel died again on session limits, resume `wf_edb4d979-c18`): llama-server dissolves the omni catch-22 (Qwen3-Omni GGUF on the 28GB pool, audio-in); symbolic timeline beats video-LLMs in literature (SMILE); chat lag seed = 7s; meme matching = classical+thresholds, not naked LLM — [[log]]
- [2026-07-03] **Chat-ROI auto-detect = default** (owner: "don't know where chat will be"); doubles as the has-chat test. Master proposal gained **§7 Decision log** (all owner corrections compiled, dated, with pointers) — [[concepts/master-proposal-2026-07]] — [[log]]
- [2026-07-03] **VO deprioritized (owner) + chat-mining design**: tts_vo stays optional/unused — clipping QUALITY is the priority (A1 anomaly lane = headline); A2 overlay-OCR mechanics filed (chat-velocity ROI diff → burst-anchored EasyOCR → `chat_events.json`; ~5–12s viewer lag modeled, auto-calibrated vs CLAP laughter) — [[concepts/reference-humor-2026-07]] — [[log]]
- [2026-07-02] **Roadmap Qs answered + SFX seed library**: chat = burned-in overlay → A2 pivots to OCR-region mining (reuses caption_ocr); tts_vo default = LLM+Piper; **14 meme SFX downloaded+validated** to `reference_clips/sfx_reference/` (analysis-only lane) → Phase 4a no longer asset-blocked — [[concepts/master-proposal-2026-07]] — [[log]]
- [2026-07-02] **Master proposal roadmap filed** ([[concepts/master-proposal-2026-07]]): all workstreams A-E sequenced (Phase 0 audits → 0.5 originality levers → 1 anomaly lane → …), claim-by-claim evaluation of the fusion/reference analysis (chat-data dependency + live LM-Studio-audio-in check flagged), 4 deep-research prompts ready. Wiki-only — [[log]]
- [2026-07-02] **Reference-humor evaluation filed** ([[concepts/reference-humor-2026-07]]): externally-referenced jokes (George Bush meme) — detect via reaction proxies (no reference needed), name via chat mining + a `known_format` probe, cover post-cutoff formats with a meme library. Additive lane; wiki-only — [[log]]
- [2026-07-02] **Multimodal-fusion evaluation filed + expanded** ([[concepts/multimodal-fusion-2026-07]]): joint prompts exist at 5.5/6 but sit behind a transcript-only proposal gate; 5 fusion options with per-option rig fit + the **dual-GPU catch-22** (28GB LM Studio pool can't hear; 16GB CUDA lane can't fit the big omni). Timeline fusion recommended. Wiki-only — [[log]]
- [2026-06-21] **Dashboard Clip Forensics tab**: the offline decomposer is now usable from the GUI — tab switcher (Clipper | Clip Forensics), `forensics_routes.py` (`/api/forensics/clips|run|result`) + `forensics-panel.js` render the timeline + LLM style profile; clip dropdown + trim-end/OCR/LLM/GPU toggles. Verified end-to-end via test client — [[entities/dashboard]] — [[log]]
- [2026-06-21] **Clip-forensics robustness (from ground truth)**: `--trim-end`/`CLIP_FORENSICS_TRIM_END` drops the ~3s TikTok download outro (logo+@handle) that was mis-logged as edits; music-bed false-negative fixed via per-label CLAP thresholds (music 0.18, suspense 0.20 — a bed under speech peaks ~0.27, under the 0.30 floor) + sustained-run gate + `added`=under-speech&(abrupt|mid-clip). Verified: ReemKnocks bed 6–14s added:true — [[entities/audio-sense-module]] — [[log]]
- [2026-06-21] **Clip-forensics Phase 3+4b + watchdog**: `visual_sense.py` (cv2 motion + EasyOCR captions, `--ocr`) + LLM `style_profile` synthesis (`--no-llm`); every stage under a hard wall-clock watchdog (cap→abandon→partial; the durable fix for runaway tasks). Verified: motion 9, OCR 13.08 wps, LLM essence coherent. [[entities/visual-sense-module]] — [[log]]
- [2026-06-21] **Clip-forensics Phase 2 shipped + models verified**: CLAP (1.2GB) + faster-whisper base (142MB) + PANNs (327MB, opt-in) installed & producing real output; censor + music-bed unit-verified; PANNs gated opt-in (torch≥2.9 stall), CPU default, librosa-onset→numpy. Install doc [[entities/audio-sense-module]] — [[log]]

## Landmines (top gotchas for the next agent)
- `panns_inference` **deadlocks (uncatchable stall) on torch≥2.9** in `SoundEventDetection.__init__`, CUDA *and* CPU — it's opt-in (`CLIP_AUDIO_SENSE_PANNS=1`); CLAP is the default audio backend. Also: panns shells out to `wget` (absent on Windows) → pre-place its ckpt+CSV in `~/panns_data/` — [[entities/audio-sense-module]]
- Downloaded TikToks carry a ~3s **outro** (logo + @handle) that clip-forensics mis-logs as real edits — pass `--trim-end 4` / `CLIP_FORENSICS_TRIM_END=4` when batch-analyzing TikTok downloads — [[entities/audio-sense-module]]
- FFmpeg `fade=...:color=white` holds the colour OUTSIDE its ramp window — use transient `drawbox enable='between(t,a,b)'` instead (BUG 64) — [[concepts/transition-animations]]
- Stitch-short needs ≥3 same-category eligibles ≤28s within budget; the invariant is `target+4 ≥ min×cap` (BUG 63) — [[concepts/bugs-and-fixes]]
- `config/originality.json` is untracked dashboard runtime state — edit `DEFAULT_ORIGINALITY` / `config/originality.example.json` for committed defaults — [[entities/dashboard]]
- A single clip can't straddle a chunk seam — `parse_llm_moments` clamps each to its nominal window; A1/M3 emit payoff-centered clips — [[concepts/clip-duration]]
- The Discord **agent** model (`qwen3.5-9b`, in `config/openclaw.json`) is NOT the **pipeline** model (`qwen3.6-35b-a3b`, in `config/models.json`) — two different models, two different files — [[entities/qwen35]]
- NVENC accelerates encode only; per-clip filtering (blur-fill/captions) stays on CPU, so wall-clock gain depends on the filter/encode split — [[concepts/clip-rendering]]
