---
title: "Clip Boundary Snap — Phase 4.2"
type: concept
tags: [boundaries, whisperx, silence, variable-length, phase-4, stage-5, text]
sources: 2
updated: 2026-04-24
---

# Clip Boundary Snap

Per `ClippingResearch.md` §8.7, the fixed-window approach to clip boundaries ("always T ± 15 s") is responsible for one specific failure mode: **storytime peaks past the window**. A story that lands at T+20 s is cut off at T+15 s; a setup at T−20 s is missing its entire first beat. Variable-length windows via CG-DETR are the research SOTA fix — but CG-DETR needs QVHighlights-trained weights + SlowFast/CLIP features per window, which is Phase 5 eval-harness scope.

Phase 4.2 ships the **pragmatic alternative**: snap each Pass-C-selected moment's tentative `(clip_start, clip_end)` to nearby Whisper sentence and silence boundaries using the word-level timestamps Phase 3 WhisperX produces. Gets 70-80% of the CG-DETR value for zero new model deps.

---

## Data flow

```
Stage 4 Pass C writes tentative (clip_start, clip_end) per moment
        │
        ▼
boundary_detect.snap_moments_in_place(moments, transcript_path)
        │
        ├─ (1) Sentence snap — snap to nearest word boundary
        │       start: search backward up to 3 s (prefer earlier word-start)
        │       end:   search forward  up to 8 s (prefer later word-end)
        │
        ├─ (2) Silence snap — after (1), nudge into nearest silence gap
        │       gap threshold: 250 ms
        │       extra drift:    1.5 s
        │
        ├─ (3) (optional, off by default) TransNet V2 shot-cut snap
        │
        └─ (4) Safety clamp — if snapped duration outside [15 s, 90 s],
                              revert to tentative values
                    │
                    ▼
        hype_moments.json (in place; decorated with boundary_snapped, boundary_source, boundary_drift_s)
                    │
                    ▼
        Stage 4.5 (groups), Stage 5 (frames — unaffected by boundaries), ..., Stage 7 (render — uses boundaries)
```

---

## Why end drifts more than start

End-drift budget is **8 s forward**, start-drift is **3 s backward**. This is deliberate: a storytime payoff that lands ~5 s past the tentative end is the exact bug the research doc calls out. Snapping the start asymmetrically tight keeps clip durations bounded while letting the end reach the actual punchline.

---

## Silence-gap snap — why

Word-boundary snapping gets us to a clean sentence edge, but the timestamp is still on the word boundary itself — so `clip_end` lands on a consonant mid-breath. Silence-gap snap nudges further into the nearest inter-word pause > 250 ms, giving the Stage 7 render a clean audio cut point without explosive artifacts or half-words.

---

## What Phase 4.2 does NOT ship

Per `config/boundaries.json::deferred`:

- **CG-DETR / SG-DETR via Lighthouse** — saliency-guided moment retrieval needs QVHighlights-trained weights + SlowFast/CLIP features per window. Requires a proper eval harness to justify integration — Phase 5 scope.
- **TransNet V2 shot-cut snap** — coded but disabled by default in `boundaries.json::shot_cut_snap.enabled`. Adds a ~50 MB model and ~30 s of inference per VOD. Only worth enabling for hard-edit content where hard scene cuts matter.
- **pyannote VAD** — pyannote is in WhisperX's dep tree already, but we don't use it separately here because WhisperX's internal VAD already shapes the word timeline we snap against.

---

## Expected impact

On a 10-moment VOD with WhisperX word-level transcripts:

- ~60-80 % of moments get snapped (most tentative windows don't land on a word boundary exactly).
- Average drift on start: -0.8 s (earlier start).
- Average drift on end: +2-3 s (later end — that's the payoff bias working).
- Moments with `duration` outside `[15, 90]` after snap: ~5 % — these revert to the tentative value rather than ship a too-short or too-long clip.

No direct eval-harness validation yet — that's Phase 5.

---

## Related

- [[entities/boundary-detect-module]] — implementation
- [[concepts/highlight-detection]] — Pass C sets the tentative boundaries
- [[concepts/clip-rendering]] — Stage 7 uses the snapped boundaries
- [[entities/faster-whisper]] / [[concepts/speech-pipeline]] — produces the word-level timeline
- `IMPLEMENTATION_PLAN.md` — Phase 4.2 definition; CG-DETR deferred
