---
title: THE FINALS Clipper
type: entity
tags: [app, finals, ocr, montage, sibling-app]
sources: [finals/, scripts/finals/, start-finals.cmd]
updated: 2026-08-19
---

# THE FINALS Clipper

A **game-specific sibling app** (owner request 2026-08-19) that extracts montage source material from THE FINALS gameplay VODs: clips of every teammate **revive** and a quick clip of every **end-of-match screen** appearance. Served on its own port (**5200**, `FINALS_PORT`, same bind-test roll-forward as the dashboard/poster) so it can't be confused with the main dashboard.

> [!note] Why not a duplicate of the main pipeline
> The owner suggested duplicating the main pipeline, but revives and scoreboards are **visual HUD events** — the transcript-driven Stage-3/4 moment detection literally cannot see them. This is a purpose-built OCR detector instead: faster (no transcription, no LLM), deterministic, and zero LM Studio / VRAM contention (easyocr uses ~1 GB *only if* ≥2 GB is free; else CPU).

## Architecture

Two halves, decoupled the same way as dashboard ↔ pipeline:

- **Engine** — `scripts/finals/` (CLI-runnable, no Flask imports):
  - `finals_common.py` — geometry constants, fuzzy text matching, event grouping, window merging. All geometry is in *fractions of the frame* (resolution-independent).
  - `hud_ocr.py` — easyocr wrapper (GPU auto-pick), pixel gates, per-frame classification.
  - `finals_scan.py` — one ffmpeg rawvideo pipe (`fps=2, scale=960:-2`, NVDEC with CPU fallback) feeding the detectors; progress lines every 15 s.
  - `finals_cut.py` — buffer windows → merge overlaps → NVENC re-encode cuts (libx264 fallback). Clips are **raw material**: source resolution/audio, no captions, no 9:16.
  - `run_finals.py` — CLI: scan+cut, `--probe T` (one-frame classify + annotated jpg), `--selftest` (12 synthetic-frame checks), pid/done run markers, `--max-minutes` watchdog (default 240 — every run is bounded).
- **App** — `finals/` (Flask :5200, mirrors `poster/` layout): `app.py`, `_state.py`, `runner.py` (spawn/stop with `taskkill /T` + cross-process markers — BUG 67/72 lessons applied), `routes.py`, one HTML/CSS/JS page (red/gold theme, deliberately distinct). Start: `start-finals.cmd`.

Output: `finals_clips/<vod-stem>/*.mp4` + `events.json` manifest (events, params, stats, clips). Gitignored like `clips/`.

## Detection design (calibrated from owner screenshots, 2026-08-19)

**Revives** — bright-pixel gate on the center-lower band (x 0.20–0.80, y 0.55–0.83), then OCR:
- `REVIVE <NAME>` completion banner (y ≈ 0.67–0.71) → **strong** hit; teammate name captured.
- `REVIVE 200 + 200` score popup → **strong** (strict score-token regex `^\+?\d{2,4}$` so world-marker distances like `5M` can't fake it).
- Bare big `REVIVE` (≥ 0.024·H tall) → **weak**; a group needs ≥1 strong or ≥2 weak to emit.
- `HOLD TO REVIVE` prompt → recorded as corroboration, **never clipped alone** (proximity ≠ revive; the pre-buffer covers the approach anyway).
- World-space `REVIVE` markers over downed teammates: excluded by band top edge + line-height gate (< 0.016·H dropped).

Hits within 3 s group into one event; clip = `[t_first − pre, t_last + post]` (defaults **10 s / 5 s**, the owner's ask); overlapping windows merge (`revive_03_x2_…` names).

**End screens** — red-dominance gate at ~1 fps (the summary screens are a wall of red bokeh; personal screen dark red) + an unconditional 12 s OCR backstop. Top strip classifies the family; the body band (only OCR'd for summary screens) picks the subtype:
- `end_tournament` — RANK SCORE UPDATE / FINISHING POSITION / PERFORMANCE BONUS
- `end_teamperf` — COMBAT/OBJECTIVE/SUPPORT SCORE player cards
- `end_personal` — DEFEATED/VICTORY + PERSONAL PERFORMANCE tab (checked **first**: its tab row contains "MATCH SUMMARY", which would otherwise match the summary family)
- `end_summary` — summary family, unknown subtype (e.g. Standout Contestants)

Each contiguous same-type span → one quick clip, capped at `end_cap` (default 6 s, "a quick clip of each time it is shown").

## Verification (2026-08-19)

- `--selftest`: 12/12 PASS (synthetic banner/marker/hold/end-screens through real easyocr + grouping/merge units).
- Real-footage plumbing: 3-min slice of the 1080p60 Jynxzi VOD — NVDEC + GPU OCR ~3× realtime *worst case* (Siege nameplates sit in the band → gate passed ~100 % of frames; **0 false revive hits** across 360 frames). Finals footage has a much emptier band.
- Cutter: fabricated overlapping events → correct `x2` merge with both names; 12 s span capped to 6 s; NVENC path confirmed.
- App: all endpoints exercised over HTTP (run / 409 double-run guard / state+progress / stop with clean process tree — verified no orphaned ffmpeg — / results / probe with annotated image).

> [!warning] Not yet validated on real THE FINALS footage
> No Finals VOD existed on disk at build time — band geometry and keywords come from the owner's screenshots. First real run: use the **Probe** panel on a known revive timestamp and eyeball the annotated frame; adjust `REVIVE_BAND` / gates in `finals_common.py` if the recording layout differs (e.g. facecam overlays near the band).

## Knobs (dashboard + CLI)

pre/post buffers, end-screen cap, sample fps (0.5–6), OCR auto/gpu/cpu, revives/endscreens toggles, scan window (`--start/--end`), `--max-minutes`, `--scan-only`, `--no-hwaccel`.

Related: [[entities/dashboard]], [[entities/buffer-poster]], [[concepts/bugs-and-fixes]] (BUG 67/72 patterns reused).
