---
title: "Hot — current state & recent activity"
type: overview
tags: [hot, hub, status]
updated: 2026-06-12
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
- [2026-06-12] TikTok originality-mechanics research filed ([[concepts/tiktok-originality-mechanics-2026-06]], full verify 14✓/11✗) — refines the audio plan: **VO > SFX/music**, "music bed breaks fingerprint" REFUTED, perturbation is the wrong frame, **flag escalates to account-level**; ranked Tier A/B/C transform list — [[log]]
- [2026-06-12] SFX cue-taxonomy research filed ([[concepts/sfx-cue-taxonomy-2026-06]]) — beat→sound→offset→mix + CC0 sources + JSON drop-in for `sfx_cues`; feeds the audio-layer plan P1 (deep-research verify layer crashed on session limits → synthesized from sourced corpus) — [[log]]
- [2026-06-12] Evaluation fixes executed: word-boundary keywords (default ON), `config/channel_keywords.json` packs, `config/prompts.json` unification, `CLIP_SEGMENT_VOTES` confidence+smoothing (opt-in), rare-pattern Pass C bonus (rap_battle 1.15), `self_consistency.py` REMOVED — [[log]]
- [2026-06-12] Deep evaluation filed as 5 plan/case pages (unoriginality audio layer, calibration loop, decorrelation, incongruity case, YouTube ingest); `self_consistency.py` flagged as the one orphan module — [[log]]
- [2026-06-12] Wiki maintenance pass: added [[hot]] + `scripts/wiki_lint.py`, `status:` field on plan pages, fixed stale model/arch facts, completed the bugs quick-nav — [[log]]
- [2026-06-07] Added narrative `[GROUPS]` logging + `moment_groups.py --explain` dry-run to verify stitch/arc grouping — [[concepts/originality-stack]]
- [2026-06-07] Untracked + gitignored `config/originality.json` (dashboard runtime state tripping the wiki Stop-hook) — [[entities/dashboard]]
- [2026-06-07] `config/originality.json` persists Originality-panel toggles (arc_stitch/flash_cuts on, jump_cuts off) — [[entities/dashboard]]
- [2026-06-07] BUG 64 fixed: transient `drawbox` flash replaces chained `fade=...:color=white` that held white outside its window — [[concepts/transition-animations]]
- [2026-06-06] BUG 63 fixed: stitch budget invariant (cap 12→10, target 28→36) + decoupled eligibility + `[GROUPS]` logs — [[concepts/bugs-and-fixes]]
- [2026-06-06] Transition animations shipped (white flashes + jump-cut compression) as a flag-gated Stage 7d.5 post-pass — [[concepts/transition-animations]]
- [2026-06-06] Hook card restyled to Montserrat Black + contrast-aware outline; fixed hard-coded Linux DejaVu font path — [[concepts/captions]]
- [2026-06-06] Dashboard port now `DASHBOARD_PORT`/`PORT` env with auto-fallback (fixed WSAEACCES startup crash) — [[entities/dashboard]]
- [2026-06-06] Dashboard "Studio" theme imported (teal, merged not overwritten) — [[entities/dashboard]]
- [2026-06-06] CapCut word-box captions shipped (bundled Montserrat, word-level SRT, 4 caption bugs fixed) — [[concepts/captions]]

## Landmines (top gotchas for the next agent)
- FFmpeg `fade=...:color=white` holds the colour OUTSIDE its ramp window — use transient `drawbox enable='between(t,a,b)'` instead (BUG 64) — [[concepts/transition-animations]]
- Stitch-short needs ≥3 same-category eligibles ≤28s within budget; the invariant is `target+4 ≥ min×cap` (BUG 63) — [[concepts/bugs-and-fixes]]
- `config/originality.json` is untracked dashboard runtime state — edit `DEFAULT_ORIGINALITY` / `config/originality.example.json` for committed defaults — [[entities/dashboard]]
- A single clip can't straddle a chunk seam — `parse_llm_moments` clamps each to its nominal window; A1/M3 emit payoff-centered clips — [[concepts/clip-duration]]
- The Discord **agent** model (`qwen3.5-9b`, in `config/openclaw.json`) is NOT the **pipeline** model (`qwen3.6-35b-a3b`, in `config/models.json`) — two different models, two different files — [[entities/qwen35]]
- NVENC accelerates encode only; per-clip filtering (blur-fill/captions) stays on CPU, so wall-clock gain depends on the filter/encode split — [[concepts/clip-rendering]]
