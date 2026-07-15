---
title: "Quality-Leverage Ranking of the Pipeline Stages (2026-07)"
type: concept
tags: [quality, pipeline, review, fine-tuning, reference]
sources: 0
updated: 2026-07-15
---

# Quality-Leverage Ranking of the Pipeline Stages

Owner question (2026-07-15): *"Which part of the pipeline affects the clipping quality the
most?"* Filed because the answer is the map for the post-Wave-3 fine-tuning round — it tells
the owner's clip review where each class of complaint should be routed.

**Organizing principle:** a failure's quality cost = **visibility × recoverability**. Two
stages produce *unrecoverable* failures (a moment never found, a word never transcribed);
every other stage degrades clips that still ship, which review can catch.

## The ranking

| # | Stage | Quality lever | Recoverable downstream? |
|---|---|---|---|
| 1 | **S4 Moment Detection** | the candidate set, payoff timestamps, clip windows, category/pattern labels | **No** — a missed moment is invisible forever |
| 2 | **S2 Transcription** | the text + word timing everything reads; captions (master-slice!), SFX anchors, diarization | **No** — ASR errors propagate everywhere |
| 3 | **S7 Editing/Render** | captions, SFX placement/gain, music, zooms, framing — what the viewer experiences | Yes, but degrades 100% of shipped clips |
| 4 | **S6 Vision Enrichment** | titles/hooks/descriptions (post kit), fidelity gates, arc verification | Mostly (copy is rewritable) |
| 5 | **S5.5 Vision Judge** | which candidates render (selection + ordering) | Partially — buried gems never render |
| 6 | **S3 Segment Detection** | stream-type multipliers, S4 prompt steering | Yes — indirect, small |
| 7 | **S5 Frame Extraction** | what vision stages can see (sampling, 640×360 — measured no quality loss) | Rarely fails |
| 8 | **S1 / S8** | none directly (S8 effects_log feeds the [[concepts/reference-lab]] loop = *future* quality) | n/a |

## Evidence anchors (this project's own history)

- **S4 = #1**: BUG 66 (primary_pattern never emitted → downstream styling defaulted),
  setup-vs-payoff timestamps (→ `payoff_rescue`/`boom_after_line`), BUG 68 dead-air window
  from the arc lane, open cases: moment-splitting, visual-subtext blind spot. It emits the
  three values everything downstream keys on (payoff T, window, category). Wave 3's one
  deliberate quality bet (Pass B on the 9B) lives here — the pending owner clip review
  adjudicates it.
- **S2 = #2**: since A2′ master-slice, burned-in caption text+timing come DIRECTLY from S2.
  The pre-Wave-0 silent-fallback bug (whisperx → plain faster-whisper for weeks, nobody
  noticed) is the canonical failure. Most reliable stage in practice — low variance, but an
  absolute ceiling.
- **S7 = #3**: the owner's listen rounds concentrate here — 07-04 (adaptive gain, onset
  snap), 07-05 (payoff rescue, boom masking the line), 07-09 ("too quiet", "more cues"),
  R4 density 0.88→5.36 cues/30s. Most tunable stage: fixes are config dials.
- **S5.5 = #5**: judged the dead-air end-screen 0.778 (BUG 68) — frames-only, so
  audio-carried humor is a structural blind spot in both directions.

## Complaint → stage routing (for clip review)

| Review note sounds like… | Route to |
|---|---|
| "why did it clip THIS" / "missed the real moment" / "starts late / ends before the reaction" | **S4** |
| "captions wrong word / out of sync" | **S2** |
| "SFX early/buried/too dense; music wrong; crop weird" | **S7** (config dials) |
| "title/hook cringe or overclaims" | **S6** |
| "good clip ranked low; junk rendered" | **S5.5** |

## Related
- [[concepts/bugs-and-fixes]] — the evidence register
- [[concepts/plan-speed-wave3-2026-07]] — the 9B Pass-B quality bet under review
- [[concepts/sfx-cue-taxonomy-2026-06]] — the S7 sound layer
- [[concepts/reference-lab]] — the S8-fed feedback loop
