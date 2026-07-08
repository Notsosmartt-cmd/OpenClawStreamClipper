---
title: "Plan — Learning Activation (render batch + label loop + first ranker fit)"
type: concept
tags: [plan, calibration, ranker, labels, twitch, render, learning, roadmap]
sources: 0
status: in-progress
updated: 2026-07-05
---

# Plan — Learning Activation

The unified, agent-iterable plan that takes the shipped-but-dormant learning machinery
([[concepts/calibration-ranker-2026-07]]) to a LIVE fitted ranker, wrapped around the
pending validation render batch. An executing agent works phases L0→L4 in order; every
phase has commands, files, a Definition-of-Done, and a fallback. House rules apply
throughout: flag-gated/default-off, failure-soft, wiki+commit per change, every run
bounded + verified not-stuck ([[concepts/plan-pipeline-upgrade-2026-07]] §rules).

## Label paths — tested verdicts (2026-07-05)

**Path A (Twitch clip API) is DEAD — wrong universe, not just broken.** Owner
correction: in this niche community highlights are NOT Twitch clips — **viewers
screen-record moments and post them on social media themselves** (that's exactly how the
reference corpus was collected). The API fallback is also mechanically broken
(`PersistedQueryNotFound` — rotated persisted-query hash, swallowed as "0 clips"), but
the deeper problem is it measures the wrong signal. Dropped from this plan; Helix repair
notes kept in [[entities/bootstrap-twitch-clips]] only for completeness.

**Path C (viewer-posted clip alignment) REPLACES it — method PROVEN.**
`scripts/research/align_ref_clips.py` aligns each reference clip back to its source VOD
by transcript shingle matching (both sides already have whisper transcripts). First real
pass: **2/36 clips aligned, unambiguously** (48 and 108 shingle hits; threshold 6) →
`clips/.diagnostics/labels_social.jsonl`. These are the BEST labels available:
platform-validated (a real viewer recorded + posted the moment). The 34 misses are clips
whose source VOD isn't on disk — **yield scales with collection habit, not code**: when
saving viewer clips for streams whose VODs are kept, every pair = a free label.

**Path B (owner feedback) — NO manual labeling required (owner preference 2026-07-05).**
The owner's NATURAL chat feedback on batches ("the Rap Battle was actually a good clip")
is filed as labels BY THE AGENT — no forms, no rating files, no new behavior. The
explicit `rate_run.py` flow stays available as an optional convenience, never a
requirement. B remains the taste ground truth; C covers the rejected candidates B can't
see. Merge: C + B, conflicts owner-wins. Later ladder step: posting outcomes (which
clips get posted + how they perform) = fully hands-free labels.

### §Case — the Mockingbird miss (external validation of the whole calibration thesis)

The very first Path-C label exposed a real selection error: *Teacher Explains To Kill a
Mockingbird* (viewer-posted, social) aligns to the rakai VOD @ **T≈5252**. In the p4cal
trace, T=5305 is that moment: Pass B **0.9375, cross-validated, final 1.3365 — near-top
of 257 candidates — and `selected=False`** (lost bucket competition). A second
[[concepts/case-rap-battle-missed]], but this time proven by ground truth from the
target platform, not internal reasoning. This is precisely the class the fitted ranker
(L3) exists to rescue, and it is now a *labelled training example*.

## The loop being activated

```
render batch (L0) ──► owner listens + rates (B labels, ~2 min)
      ▲                          │
      │                          ▼
 fitted ranker ◄── GATE ◄── fit_ranker (L3) ◄── labels merge (L1) ◄── social labels (L2, align_ref_clips)
      │
      └── every future run banks its trace automatically (already live)
```

Training data: every completed run already embeds its full B1-enriched Pass-C trace in
`clips/.diagnostics/last_run_*.json` (763 rows across the last 3 runs; `fit_ranker`
reads them directly — verified 2026-07-05).

---

> [!done] CROSS-VOD GATE 2026-07-05 (Tylil run tylil0, PASS, 9 clips, VOD-stamped trace).
> With labels now spanning 2 VODs (7 owner rakai + 3 social incl. the activated Tylil
> label), the gate does a REAL leave-one-VOD-out. Fixed a gate correctness bug first: the
> fitted key now mirrors deployment exactly — `sigmoid(ranker.score) × position_weight`
> (the pipeline applies position AFTER maybe_rescore; the old key omitted it, unfairly
> penalizing the fit ~6 ranks). Result: on the seen rakai runs the fit is a wash (010127
> rank 12→11.2, 074956 12.2 vs 11.5); on the **held-out Tylil VOD the fit makes the
> positive WORSE (67→77)** — it does NOT transfer to the unseen channel. **Verdict REJECT
> → ranker stays OFF** (no selection_ranker.json). The generalization guard working end to
> end: a fit that doesn't generalize across VODs is refused. Path to ENABLE = more labels
> across more VODs on the miss class (Tylil positive rank 77/244, Mockingbird 24/257) —
> accrues hands-free via Path C + owner feedback. Also: P-TIGHT built (default off);
> Tylil = a different streamer, pipeline generalized cleanly (voice scoped/off, ranker off).
>
> EXECUTION 2026-07-05 (agent, "execute the plan"). **L0 PASS** (learn0, rakai,
> 10 clips, exit 0): adaptive SFX gain fired per-clip (0.0→+4.9 dB by loudness),
> **cold-open teasers ATTACH** (5, zero WinError 17 — BUG 65 fix validated live), SFX
> beat-anchored + varied (riser→boom one-twos, ding), manifest 8/10 under one
> `CLIP_RUN_STAMP`. **L1 built** (rate_run / merge_labels / fit_ranker --gate/--tol +
> pre-B1 filter + best-in-window label snap + rank diagnostic). **L1.1** VOD stamp added
> to the trace. **L3 GATE RAN on 2 B1 rakai runs → verdict HOLD → ranker stays OFF**
> (config/selection_ranker.json absent). Honest read: the Mockingbird positive sits at
> hand-tuned rank ~24/257 (just outside the top-10 cut — the confirmed miss); a fit on 1
> positive / 1 VOD moved it to ~34 (worse), so the gate correctly refused to enable. This
> is the generalization guard working: **insufficient/single-VOD labels never turn the
> ranker on.** Next: labels spanning ≥2 VODs (owner feedback on L0 clips + Path-C growth).

## Phase L0 — Validation render batch (~75 min run + owner listen) — FIRST

Exercises, in ONE run: adaptive SFX gain, onset-snap timing, cold-open attach (post
BUG 65), full effects-manifest grouping (CLIP_RUN_STAMP), anomaly lane. Doubles as
**labeled-run #1** once the owner rates it.

1. Launch + monitor (never leave unwatched — stall-aware wait):
   ```
   python scripts/research/phase_runner.py launch --vod 20260424_2xRaKai_2756365448.mp4 \
       --force --profile validation --label learn0 --phase L0 --env CLIP_ANOMALY_LANE=1
   python scripts/research/phase_runner.py wait --run learn0 --stall 600
   python scripts/research/phase_runner.py evaluate --run learn0
   ```
   Same VOD as p4cal on purpose → direct A/B against the previous batch.
   `CLIP_CAPTION_STYLE` stays wherever the owner set `caption_style.json.enabled`.
2. Verify in `work/pipeline.log` + `clips/.diagnostics/effects_log.jsonl`:
   - ≥1 `sfx-adapt: +X.XdB` line on a loud clip; 0-boost on quiet clips
   - `[cold-open] prepended teaser to T=...` lines (BUG 65 fix landing) — expect ≥3
   - manifest: ALL clips under ONE `run` stamp, each with `sfx_adapt_db` + cues
   - boom `t` values differ from p4cal's for the same moments (onset-snap moved them)
3. Present the owner: clip list + per-clip effects manifest (kind/t/gain/boost).

**DoD:** evaluate PASS + all four checks above + owner has the clips.
**Fallback:** any check fails → diagnose before L3 (the fixes are this run's payload);
cold-open attach failing again = reopen [[concepts/bugs-and-fixes#BUG 65]].

## Phase L1 — Label plumbing (~half day, agent-only)

1. **L1.1 Trace self-identification** (gap found 2026-07-05: traces don't name their
   VOD). In `stage4_moments.py` add to `_trace_payload`: `"vod": os.environ.get("VOD_BASENAME", "")`
   (+ mirror into the L0 verification). For the 3 existing rakai traces, a sidecar map
   `clips/.diagnostics/trace_vods.json` written by hand once.
2. **L1.2 Ratings tool** `scripts/research/rate_run.py`:
   - `template --run <last_run stem>` → writes `clips/.diagnostics/ratings_<run>.jsonl`
     pre-filled from that run's `clips_made` (one line per clip: title, T, `"label": null`).
   - Owner (or agent taking owner's chat feedback) sets `label: 1|0` per clip.
   - `collect` → merges every completed ratings file into `labels_owner.jsonl`
     (`{"run","timestamp","label"}` — timestamps from the clip's T).
3. **L1.3 Fitter tolerance flag**: `fit_ranker --tol` (default 2.0; community-clip
   offsets need ~8-10 s; owner ratings stay tight at 2 s). Merge helper
   `merge_labels.py`: B overrides A within tol; B rows duplicated ×3 (poor-man's
   sample weighting; note in meta).

**DoD:** `rate_run.py template` + `collect` round-trip on the L0 run; a trace row
matches its rating by (run, timestamp) with the L1.1 vod stamp present in the L0 trace.

## Phase L2 — Path C harvesting (SHIPPED 2026-07-05; ongoing habit)

The aligner is built and proven (`scripts/research/align_ref_clips.py` → 2/36 matched,
labels in `clips/.diagnostics/labels_social.jsonl`, gitignored runtime data —
regenerable by re-running the script). What remains is HABIT + coverage, not code:
- **Owner habit:** when downloading viewer-posted clips, prefer streams whose VODs are
  (or will be) on disk; drop them in `reference_clips/` as usual. `corpus_refresh.py`
  transcribes them; `align_ref_clips.py` then labels them automatically.
- **Agent cadence:** run `align_ref_clips.py` after every corpus refresh; new matches
  append (deduped by clip).
- **Tylil note:** the 108-hit match (T=1009, Tylil VOD) has NO banked trace yet — the
  Tylil VOD predates B1 tracing. A future pipeline run on that VOD banks the trace and
  activates the label.

**DoD (already met):** ≥1 confident alignment + the labels file. **Never blocks L3.**

## Generalization doctrine (owner directive 2026-07-05 — high-variety content incoming)

Corpus-driven learning must NOT overfit the current niche. Three layers, each with an
enforced mechanism:
1. **Global/structural (learn everywhere):** detectors, arc/reaction/keyword features,
   SFX beat taxonomy — content-agnostic by construction; safe to learn globally.
2. **Niche/channel-scoped (learn per source):** the caption VOICE. Enforced:
   `caption_style.json.applies_to` (channel substrings vs VOD basename) — non-matching
   or unknown channels get the NEUTRAL prompt; the distiller preserves the scoping
   across regenerations. Current profile scoped to rakai/tylil/plaqueboymax/lacy.
   A second niche later = a second profile keyed to its channels.
3. **Per-item runtime adaptation (never bake corpus constants):** adaptive SFX gain
   (measures each clip), onset-snap (each clip's audio), chat-ROI auto-detect —
   the house pattern; anything that CAN adapt at render time should.

**The ranker is identity-ANCHORED (enforced in `fit_ranker`):** the fit carries the
hand-tuned composite score with prior weight 1.0 (proximal L2 toward the prior, standard
λ convention). Verified mechanism: uninformative labels → concordance with the
hand-tuned ranking 0.903 @ l2=0.5 → 0.998 @ l2=25; a planted real signal still recovers
(+2.1). Weak or niche-narrow evidence leaves the GENERALIZED baseline ranking intact;
only consistent evidence moves weights. Composite folds back into per-feature weights so
`ranker.py`'s schema is unchanged.

## Phase L3 — First fit + GATE (agent, <1 h once ≥3 rated runs exist)

1. Inputs: all `last_run_*.json` traces + `labels_owner.jsonl` + `labels_social.jsonl`
   (Path C; regenerate via `align_ref_clips.py`).
2. **Holdout validation (the GATE):** leave-one-run-out — fit on N−1 runs, on the held
   run compare `recall@10` (fitted ranking vs hand-tuned final_score ranking) against
   its labels. **Enable only if fitted ≥ baseline on the held-out run(s).** Start with
   strong regularization (l2 5–25) while labels are few — the identity anchor makes
   over-regularizing safe (worst case = baseline behavior). **Once labels span ≥2
   channels, the gate upgrades to leave-one-CHANNEL-out** — the generalization gate:
   a fit must transfer to an unseen channel, not just an unseen run. Print both
   numbers into the fit meta + wiki.
3. Enable = write `config/selection_ranker.json` (the pipeline auto-detects; logs
   `[RANKER] fitted selection_ranker.json loaded`). Commit the fitted file.
4. One live run with the ranker active (harness, same VOD family) → evaluate PASS +
   owner review of the NEW selection. Watch for: anomaly-lane moments surviving Pass C
   (the [[concepts/case-rap-battle-missed]] class), no degenerate selections (all clips
   from one bucket, etc. — the bucket layer still applies, but check).
5. **Rollback:** delete `config/selection_ranker.json` → hand-tuned behavior, instantly.

**DoD:** gate numbers recorded in [[concepts/calibration-ranker-2026-07]]; fitted file
committed (or a documented "gate failed, staying off" verdict — also a valid outcome).

## P-TIGHT — punchline boundary tightening (BUILT 2026-07-05, default OFF)

Owner review of L0: "clipping is grabbing a little too much — for shorter punchline
jokes I want short clips, but keep the ability to pick up long talking segments."
Cases: *Shower Bluff* — 19.5 s of setup before the punchline (start should be ~19 s in)
+ 2 s of tail; *Mental Breakdown* — last 5 s filler. Rap-battle clips = good length
(don't touch). SHIPPED as `scripts/lib/clip_tighten.py`, wired into `stage7._render_clip` before SFX/cold-open. Flag `CLIP_TIGHT_PUNCHLINE`, default OFF, failure-soft. Design:
- Applies ONLY to payoff-type categories (funny / reactive / hot_take / social_callout /
  controversial). **storytime / rap / emotional lanes EXEMPT** — long segments preserved
  by construction.
- Head trim (REBUILT 2026-07-05 — content-adaptive, owner: "no fixed length"): snaps
  `clip_start` to the NATURAL start of the utterance leading into the payoff — the most
  recent silence gap in the audio (speech level referenced from the payoff so it holds in
  mostly-silent windows), refined to the nearest transcript sentence boundary. Head length
  is DERIVED from content (a 1-sentence setup → ~3 s, a built-up bit → more, a monologue
  capped at head_max_lead_s). Bounds head_min_lead_s(2)/head_max_lead_s(12) are guardrails,
  not targets. Verified: same payoff + 3 different setups → heads 3.0/11.0/5.0 s.
- Tail trim: end at the last speech/reaction burst within payoff + ~8 s; drop trailing
  low-RMS/no-keyword filler (the Breakdown −5 s case).
- Acceptance: Shower-Bluff-class clips land ≈ payoff−8s → payoff+reaction; storytime
  durations unchanged byte-identically with the flag off AND on.

## Phase L4 — Cadence (steady state)

- Every VOD run: trace banks automatically (zero effort).
- Owner: nothing mandatory — natural chat feedback on batches is filed as labels by the
  agent (`rate_run.py` remains optional for thorough passes).
- Agent: re-fit every +2-3 rated runs; gate each time; append gate metrics to
  [[concepts/calibration-ranker-2026-07]] (a small table: date, n_labels, recall@10
  fitted vs baseline). Corpus refresh (`corpus_refresh.py`) whenever reference clips are
  added — unrelated cadence, same session is fine.
- Later (ladder step 3, separate plan): posted-clip outcome labels (views/flags) join
  the fit — [[concepts/plan-unoriginality-audio-layer]] P5 `posted.log`.

## Projection (recorded honestly)

Selection is the weakest measured link (the Delaware class: Pass B 0.878 cross-validated
dropped for a 0.433). Rescuing 1-2 clips per 10 from that class = **10-20% usable-clip
lift concentrated on the best moments** — plausible target, NOT a promise; the L3 gate
produces the real number and it's recorded either way. Caption voice (packaging/CTR) and
detection eval (sense calibration) compound on top but aren't in this plan's gate.

## Risk register

| Risk | Mitigation |
|---|---|
| Too few labels → overfit | L2 regularization (already), gate on held-out run, B×3 weighting only after A exists |
| Twitch offsets drift vs local VOD files | fit-time tol 8-10 s for A labels; B labels use exact clip T |
| Fitted ranker degenerate on live run | sigmoid-bounded rescore + bucket layer intact + rollback = delete one file |
| Path-C yield stays low (few VOD-matched clips) | collection-habit note (L2); loop runs B-only meanwhile |
| Label volume grows slowly (no manual labeling) | anchor keeps the fit safe at any volume; Path-C habit (keep VODs of clipped streams) is the zero-effort accelerator |

Related: [[concepts/calibration-ranker-2026-07]] · [[concepts/plan-calibration-loop]] ·
[[entities/bootstrap-twitch-clips]] · [[concepts/case-rap-battle-missed]] ·
[[concepts/corpus-learning-loop-2026-07]] · [[concepts/plan-pipeline-upgrade-2026-07]]
