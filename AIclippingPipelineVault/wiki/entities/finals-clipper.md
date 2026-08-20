---
title: THE FINALS Clipper
type: entity
tags: [app, finals, ocr, montage, sibling-app]
sources: [finals/, scripts/finals/, start-finals.cmd]
updated: 2026-08-20
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

> [!note] Multi-VOD batching (2026-08-20, owner-requested parity with the main dashboard)
> Owner: "I can't select multiple vods in the finals pipeline as I can in the main pipeline." The single VOD dropdown became a **checkbox multi-select table** — same interaction pattern as [[entities/dashboard]]'s `vods-panel.js` (row-click/checkbox toggle, header select-all, button label shows the count) — plus an "add a path" chip list for files outside `vods/`. Engine side: `run_finals.py` now takes `--vod` (repeatable) or `--vods` (comma-separated, same convention as `run_pipeline.py`) and loops the queue **sequentially in one subprocess**, writing `events.json` per VOD into its own `finals_clips/<stem>/` as before. One VOD failing logs and moves on rather than aborting the batch (checkpoint-style resilience, not true resume — each item starts fresh). The pid marker is rewritten per queue item (`index=`/`total=`/`queue=` fields) so `/api/state` can report `"VOD 2/4"` position; `--max-minutes` re-arms per VOD, not per batch. `start`/`end` scan-window fields apply to every queued VOD (documented via tooltip — a shared window across differently-sized VODs is a known sharp edge, not yet solved). Verified live: 2-VOD batch queued+ran sequentially end-to-end over HTTP, per-item progress reporting confirmed, legacy single `{"vod": ...}` body still accepted, 409 double-run guard intact, clean process-tree stop mid-queue (no orphaned ffmpeg).

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

## Montage (2026-08-20, owner ask: "automatically combined into a montage… sped up 1.5x-2x")

`finals_cut.build_montage()` concatenates a run's clips **chronologically** (a match's revives, then its end screens, then the next match — natural session narrative) into one sped-up montage per VOD: `finals_clips/<stem>/montage_<speed>x.mp4`. Individual clips are always kept.

- **Speed**: default **1.75×** (middle of the owner's 1.5–2× range), UI/CLI-adjustable 1.0–3.0. Video via `setpts=PTS/S` + explicit `fps=` (probed from the first clip — the owner's OBS recordings are **30 fps**, not 60; never assume); audio via pitch-preserving `atempo` (chained above 2.0× for old-ffmpeg safety). Duration verified exact: 38.0 s of clips → 21.75 s @ 1.75× / 19.04 s @ 2×.
- **Assembly**: ffmpeg concat *demuxer* over the just-cut clips (uniform by construction — same `_cut()` settings, same source; `-fflags +genpts`), NVENC→x264 fallback shared with `_cut()`. Montage-type entries are excluded from the source list, so a re-montage never eats a previous montage.
- **Default ON** (`--no-montage` to skip; "Build montage" checkbox + speed input in the dashboard). Params recorded in `events.json`; the montage appears in the `clips` list as `type: "montage"` and renders **first** in the Results table.
- **Existing runs**: `run_finals.py --remontage <stem> --montage-speed S` (dashboard: **"Montage this run"** button + `POST /api/montage`) rebuilds a montage from an existing `events.json`'s already-cut clips — **no re-scan** — for runs made before this feature or to change speed. Old montage files are left on disk (only the `events.json` entry is swapped); runs through the same runner/markers, so the 409 guard and Stop apply.

> [!warning] Stale-instance lesson (caught live, 2026-08-20)
> The first montage API test silently ran at the default 1.75× instead of the requested 1.5×: a **stale Finals app instance** (started via `start-finals.cmd` before the montage code existed) was still squatting :5200, so the fresh instance rolled to :5201 and the test hit the old one — which dropped the unknown JSON fields and spawned a fresh-code `run_finals.py` with engine defaults. Symptom to remember: *new fields ignored + engine defaults applied* ⇒ check for a port-roll (`netstat -ano | findstr :5200`) before debugging the code. Route/param changes require restarting the Finals app, same as the main dashboard.

## Verification (2026-08-19)

- `--selftest`: 12/12 PASS (synthetic banner/marker/hold/end-screens through real easyocr + grouping/merge units).
- Real-footage plumbing: 3-min slice of the 1080p60 Jynxzi VOD — NVDEC + GPU OCR ~3× realtime *worst case* (Siege nameplates sit in the band → gate passed ~100 % of frames; **0 false revive hits** across 360 frames). Finals footage has a much emptier band.
- Cutter: fabricated overlapping events → correct `x2` merge with both names; 12 s span capped to 6 s; NVENC path confirmed.
- App: all endpoints exercised over HTTP (run / 409 double-run guard / state+progress / stop with clean process tree — verified no orphaned ffmpeg — / results / probe with annotated image).

> [!note] Real-footage status (2026-08-20): detector confirmed working at scale
> Band geometry and keywords were calibrated from screenshots only, but the owner has since run FULL scans of 4 of their 5 Finals VODs on their own instance — e.g. the 2026-08-18 VOD (39 min): **14 revive events + 12 end-screen spans → 24 clips**; 47/20/20 clips on the others. So the gates + OCR demonstrably fire on real OBS recordings (30 fps 1080p). Remaining known noise: teammate-name extraction collects OCR variants of the same name across frames (`G1GGA`/`GIGGA`…) — cosmetic, names are labels only. Owner eyeball-verdict on clip QUALITY (buffer feel, false-positive rate) still pending.

## Knobs (dashboard + CLI)

pre/post buffers, end-screen cap, sample fps (0.5–6), OCR auto/gpu/cpu, revives/endscreens toggles, scan window (`--start/--end`), `--max-minutes`, `--scan-only`, `--no-hwaccel`.

Related: [[entities/dashboard]], [[entities/buffer-poster]], [[concepts/bugs-and-fixes]] (BUG 67/72 patterns reused).
