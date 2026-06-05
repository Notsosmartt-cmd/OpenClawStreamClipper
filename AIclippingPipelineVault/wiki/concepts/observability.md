---
title: "Pipeline Observability — Diagnostics & Axis Tuning"
type: concept
tags: [observability, diagnostics, logging, tuning, selection-axes, vision-judge, logtool, stage-timing, rank-churn, hub]
sources: 1
updated: 2026-06-04
---

# Pipeline Observability — Diagnostics & Axis Tuning

How to see *what the pipeline decided and why*, and tune it between runs. Added 2026-06-04 alongside the
A/B/C/E selection axes ([[concepts/clipping-quality-overhaul]]) so their effect is measurable, not guessed.

> [!note] One-liner
> After a run: `logtool axes` shows — for the latest run — each axis's coverage + multiplier spread +
> dependency readiness, the **base→passC→vision rank churn** per delivered clip, per-stage timing, and the
> Vision-Judge pairwise bracket. Everything is failure-soft and read-only; none of it affects clip output.

## The artifacts (written to the work dir, captured by the diagnostics snapshot)

At cleanup, [[entities/pipeline-orchestrator|the orchestrator]] snapshots every work-dir JSON into
`clips/.diagnostics/last_run_<UTC>.json` (`common.py::cleanup`). The work dir is wiped right after, so this
snapshot is the **only durable record**. The 2026-06-04 update added:

- **`axis_report.json`** — written by Pass C (`stage4_moments.py::_emit_axis_report`) over **all
  candidates** (not just delivered). Per axis (arc/reaction/baseline/engagement): `active` count + `pct`,
  multiplier `min/median/mean/max`, `at_ceil`/`at_floor`. Plus a **`dependencies`** block (did
  `chat_features` / `audio_events` / `conversation_shape` load?) and **`global_clamp.bound_count`** (how
  often the `[0.80, 1.35]` product clamp actually bit). A dead axis (all 1.0 because a dependency is
  missing) is now distinguishable from a neutral one.
- **`stage_timings.json`** — per-stage durations from the `set_stage` marks (`common.py::_STAGE_MARKS`),
  incl. **Stage 5.5 (Vision Judge)** separately. Printed inline at cleanup too.
- **`judge_tournament.json`** — the Vision Judge's **pairwise bracket**: every comparison's
  `a`/`b`/`winner` (timestamps), `confidence`, and `reason`. Written even on a partial/aborted tournament.
- **Rank-churn fields** stamped on every moment: `base_rank` (by pre-multiplier `normalized_score`),
  `pass_c_rank` (by post-axis `final_score`), and `vision_rank` (the judge). `base → passC → vision` shows
  exactly which stage moved a clip.
- The snapshot now keeps the **full** `hype_moments`/`scored_moments` lists (was capped at 30) so the
  rank-churn view is complete.

## The log lines (in `pipeline.log` / the persistent run log)

- **Per-line timestamp** — *every* log line is prefixed `[HH:MM:SS +<elapsed>s]` by `common.Logger`
  (`_stamp_lines`, applied in `write()` so it covers streamed child-stage output too — e.g. each Pass B
  chunk). Wall-clock lets you correlate with system events; the elapsed-since-start lets you time any two
  outputs by subtraction, and the **last line's elapsed == the VOD session time**. The stamp is added only
  to the log copies — `run_module` collects the *raw* child output first, so captured `$(...)` output is
  untouched, and logtool's error regexes/SSE are mid-line so nothing breaks.
- **`VOD session time [<vod>]: Xm Ys`** — logged per VOD by `run_pipeline._execute_stages` (works in
  `--all` too, where cleanup's single total wouldn't separate VODs).
- **`[PASS C]`** per-moment line — carries `arc= rx= bc= eng= ax=` (the per-axis scores + the applied
  clamped axis multiplier) alongside `score/raw/dur/lp/pw`.
- **`[AXES]`** block — the axis report, printed at the end of Pass C (dependency readiness + per-axis
  coverage + clamp activity).
- **`Per-stage timing:`** block — at cleanup (also persisted to `stage_timings.json`).
- **`[JUDGE]`** lines — the re-rank order + per-clip rationale; plus a `tournament bracket (...) ->
  judge_tournament.json` line.

> [!note] First measured run (`last_run_20260605_005657`, plaqueboymax, 9 clips, **135.8 min**)
> The per-stage timing immediately localized the cost: **Stage 4 Moment Detection = 67.1 min (49%)**,
> Stage 2 Transcription = 29.3 min, Stage 6 Vision = 13.0, Stage 7 Edit = 11.0, Stage 5.5 Judge = 10.0.
> Stage 4 is LLM-bound (Pass A→D) and exploded because the run used the **35B *thinking* model**
> (`qwen3.6-35b-a3b`) for the per-chunk Pass B + Pass D calls. Levers, biggest first: a **Pass-B model
> split** (`text_model_passb` → a smaller non-thinking text model — see [[concepts/model-split]]) or
> disabling thinking for Pass B; transcription is already addressed by the [[entities/faster-whisper]]
> `large-v3-turbo` default (~2.5x). Vision stages (6 + 5.5 = 23 min) similarly benefit from a vision split.

## `logtool axes [RUN]`

`python scripts/logtool.py axes [RUN] [--judge-limit N]` (`logtool.py::cmd_axes`). `RUN` = index from
newest, a name substring, or a path; default = latest. Reads a `last_run_*.json` snapshot and renders all
four sections (axis report, rank churn, stage timing, judge bracket). This is the **tune→run→diff loop**:
edit `config/selection_axes.json`, force-reprocess a VOD, `logtool axes`, compare. Sits alongside the
existing health commands (`doctor` / `list` / `errors` / `show` / `tail`).

## How to use it for tuning

- **Is an axis doing anything?** `axis_report.axes.<axis>.active` / `pct_active`. 0% + a missing dependency
  ⇒ the signal is dead (e.g. engagement's observed term needs VOD chat; reaction's audio term needs
  `audio_events`).
- **Is the clamp over-biting?** `global_clamp.bound_count` high ⇒ many moments hit the `1.35` ceiling;
  consider lowering individual axis ceilings rather than the clamp.
- **Are the axes/judge helping?** Rank churn — if delivered clips' `base_rank` ≈ `vision_rank` for every
  clip, the new machinery isn't changing outcomes; large promotions (like base#9 → vision#1) are the axes
  earning their keep. Sanity-check those against the actual clips.
- **What did the judge cost?** `stage_timings` — the Stage 5.5 line. (First live signal for the judge,
  which had never run live before this.)

## Related
- [[concepts/clipping-quality-overhaul]] · [[concepts/clipping-intelligence]] (Pass C) · [[entities/vision-judge]] (the bracket) · [[concepts/bugs-and-fixes]]
- The axes the reports measure: [[concepts/plan-arc-completeness]], [[concepts/plan-reaction-worthy]], [[concepts/plan-baseline-contrast]], [[concepts/plan-engagement-discussion]]
