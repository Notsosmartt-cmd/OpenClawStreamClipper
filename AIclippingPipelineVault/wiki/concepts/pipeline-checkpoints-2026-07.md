---
title: "Per-VOD pipeline checkpoints — resume a crashed batch mid-VOD"
type: concept
status: shipped
tags: [pipeline, checkpoints, resume, reliability]
sources: 0
updated: 2026-07-17
---

# Per-VOD pipeline checkpoints (2026-07-17)

Owner req: *"if VODs 1-3 finish and VOD 4 stops mid-pipeline, I want to
continue halfway through 4 — per VOD, not per batch. Process = continue if
saved state exists; force reprocess = start from 0."* Before this, the only
durable per-VOD state was the Stage-2 transcription/audio-events cache — a
crash during Stage 6 threw away S3 segments, S4 moments (timestamps!), the
S4.5 judge verdicts and all vision enrichment.

## How it works

`scripts/pipeline/checkpoint.py`, wired into `run_pipeline._execute_stages`:

- **Save**: after each expensive stage completes (**3** segments, **4**
  moments, **5** judge+frames, **6** enrichment), the work dir's `*.json` /
  `*.srt` artifacts (plus `frames/` from stage 5 on) snapshot to
  `vods/.pipeline_state/<vod stem>/work/` with a `checkpoint.json` manifest
  (stage, VOD size, style, type hint, run stamp, carried ctx fields).
  Near-atomic (tmp dir + rename); failure-soft (a save problem never breaks
  the run).
- **Resume**: Stage 1 (cheap discovery) always runs; then a valid checkpoint
  restores the snapshot into the work dir and **stages ≤ its stage are
  skipped** (logged + stage-marker'd). The resumed VOD **keeps its original
  session `run_stamp`**, so its clips stay grouped with their batch in
  effects_log / the Reference Lab.
- **Clear**: a cleanly completed VOD deletes its state (no stale resumes).

## The two buttons (exact owner semantics — no dashboard changes needed)

| Action | Flag | Behavior |
|---|---|---|
| Process (force unchecked) | no `--force` | resume from checkpoint if valid |
| **Force reprocess** (`chk-force`) | `--force` | **checkpoint wiped, start from 0** |

`--all`'s internal `batch_force=True` (processed.log semantics) deliberately
does NOT wipe checkpoints — only the user's actual force choice does
(`ctx.fresh`, carried separately).

## Checkpoint invalidation (never trust stale state)

Discarded with a logged reason when: the VOD file size changed · style
changed · type hint changed · older than 14 days · stage out of range ·
manifest without snapshot. Any checkpoint error → fresh run (fail-open in
the safe direction).

## What is deliberately NOT saved

- `audio.wav` (gigabytes; **only Stage 2 reads it** — verified) — a resume
  never needs it because Stage 2 is only skipped when its outputs are in the
  snapshot.
- Rendered clips (already durable in `clips/`); a resume that re-runs Stage 7
  re-renders its manifest, overwriting any partial file the crash left.
- Stage 7 has no internal checkpoint (renders are the cheap, idempotent part).

## Validation

6/6 offline orchestration selftests PASS (fake stages, real
`_execute_stages` + checkpoint module): crash@6 → state at 5 · resume runs
1,6,7,8 with restored artifacts + clear · force runs 1-8 from 0 · style
change discards · VOD-size change discards · resumed VOD keeps its original
run stamp.

> [!todo] First REAL resume still unproven: kill a run mid-Stage-6 on a small
> VOD and confirm Stage 6 resumes with restored moments (the snapshot carries
> every work-dir JSON, so stage inputs should be complete — but only a live
> resume proves the inventory). Cheap test: FirstFullAudio4-20.mp4.

Related: [[concepts/clipping-pipeline]] · [[concepts/plan-speed-wave3-2026-07]]
(the timing stakes) · [[entities/pipeline-orchestrator]]
