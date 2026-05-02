---
title: "boundary_detect.py — clip boundary snap"
type: entity
tags: [boundaries, silence, variable-length, phase-4, module, stage-5, text]
sources: 1
updated: 2026-04-24
---

# `scripts/lib/boundary_detect.py`

Phase 4.2 clip boundary snap. Introduced 2026-04-24. Stdlib only (reads `transcript.json` directly; uses the word-level timestamps Phase 3 WhisperX produces).

See [[concepts/boundary-snap]] for the full architectural picture.

---

## API

```python
import boundary_detect as bd

cfg = bd.load_boundaries_config()
timeline = bd.load_word_timeline("/tmp/clipper/transcript.json")

# Single moment
result = bd.snap_boundaries(
    tentative_start=120.0, tentative_end=165.0,
    timeline=timeline, config=cfg,
)
# result = {
#   "clip_start": 120.3, "clip_end": 172.5, "clip_duration": 52.2,
#   "snapped": True, "source": "sentence+silence",
#   "drift_start_sec": +0.3, "drift_end_sec": +7.5,
# }

# Batch — decorates moments in place and rewrites their clip_start / clip_end
moved = bd.snap_moments_in_place(moments, "/tmp/clipper/transcript.json", cfg)
```

Helpers:
- `load_word_timeline(transcript_path)` — returns `[(word_start, word_end), ...]` from the transcript. Uses word-level data when present, falls back to segment-level.
- `snap_to_word_boundary(t, timeline, direction, max_drift_sec)` — directional snap with asymmetric budgets.
- `detect_silence_gaps(timeline, threshold_sec)` — inter-word gaps above threshold.
- `nudge_to_silence(t, gaps, max_extra_drift_sec)` — secondary snap after sentence-snap.

CLI: `python3 scripts/lib/boundary_detect.py --transcript t.json --start 120 --end 165`.

---

## Wire point

`scripts/clip-pipeline.sh` runs a single PYSNAP heredoc right after Stage 4 Pass C writes `hype_moments.json`. It calls `snap_moments_in_place` and rewrites the file. Every subsequent stage (4.5 groups, 5 frames, 6 vision, 7 render) reads the snapped boundaries without any further changes.

---

## Decorations added to each moment

After `snap_moments_in_place`:

| Field | Meaning |
|---|---|
| `boundary_snapped` | `True`/`False` — whether either boundary moved |
| `boundary_source` | `"sentence+silence"` / `"sentence"` / `"silence"` / `"none"` / `"disabled"` / `"no_timeline"` |
| `boundary_drift_s` | `(drift_start, drift_end)` — signed seconds relative to tentative |
| `clip_start` / `clip_end` / `clip_duration` | overwritten with snapped values |

---

## Fallback ladder

1. `config/boundaries.json::enabled = false` → all moments decorated with `source="disabled"`, no rewrites.
2. `transcript.json` missing or empty word timeline → `source="no_timeline"`, no rewrites.
3. Snap would produce duration outside `duration_bounds` → revert to tentative values.
4. Snap would produce `end <= start` → revert to tentative values.

---

## Related

- [[concepts/boundary-snap]] — architectural overview
- [[concepts/highlight-detection]] — Pass C produces the tentative boundaries
- [[concepts/clip-rendering]] — Stage 7 uses the snapped boundaries
- [[concepts/speech-pipeline]] — produces the word-level timeline
- `config/boundaries.json` — runtime config
