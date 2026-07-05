---
title: "Calibration Ranker + Decorrelation (Phase 4)"
type: concept
tags: [calibration, scoring, pass-c, ranker, logistic, decorrelation, phase-4]
sources: 1
updated: 2026-07-05
status: shipped
---

# Calibration Ranker + Decorrelation (Phase 4)

Executes [[concepts/plan-calibration-loop]]: turn the ~50 hand-tuned Pass C scoring
constants from vibes into fittable, measured weights, and decorrelate the rubric's
judgement from Pass B. **Machinery built + validated 2026-07-04; ships DEFAULT-OFF**
(no fitted file / null model = byte-identical to the prior pipeline). The one remaining
step is data: labelled runs to actually fit (see "What's left").

## The keystone insight

The Pass C `final_score` is a PRODUCT of hand-tuned factors:

    final_score = normalized_score × style_multiplier × cross_val_factor(1.20)
                × speaker_factor(1.15) × pattern_bonus × axis_multiplier × length_penalty

Taking logs turns that product into a SUM — i.e. it's **already a linear model** whose
weights are the logs of the hand-tuned constants. So "fit the constants" = fit a
log-space linear ranker over features the pipeline **already stamps on every moment**.
No architecture change; the hand-tuned numbers are just the default weights.

## B1 — every factor stamped + traced (`stage4_moments.py`)

The per-moment scoring loop now stamps `style_multiplier`, `cross_val_factor`,
`speaker_factor`, and always-stamps `pattern_bonus` (was only when ≠1.0). The
`pass_c_candidates.json` trace record is enriched with those plus the raw interaction
signals (`reaction_score`, `keyword_score`, `motion_score`) — so each traced candidate
is a complete feature row for the fitter. `pass_c_candidates.json` already persisted the
rest of the chain for ALL candidates, so no new artifact was needed.

## B2+B4 — the fittable ranker (`scripts/lib/ranker.py`)

- `score(moment, weights, bias)` — log-space linear score. At the built-in identity
  defaults (identity factors weight 1.0, interactions 0.0), `exp(score) == final_score`
  **exactly** (self-test verified: 2.3154 == 2.3154).
- Features = `IDENTITY_FACTORS` (the log-factors above) + `EXTRA_FEATURES`: decomposed
  axis parts (arc/reaction/baseline/engagement) so a fit can re-weight the axes
  independently of the clamped product, and **interactions** `ix_reaction_low_keyword`
  (reaction × 1−keyword) and `ix_motion_low_keyword` — the cross-modal
  [[concepts/case-incongruity-comedy]] anomaly signature the multiplicative chain misses
  — plus `is_anomaly` / `is_cross_validated`.
- `maybe_rescore(moment)` — the pipeline hook. Returns **`sigmoid(score)` ∈ (0,1)** when
  a fitted `config/selection_ranker.json` exists, else **None** (caller keeps the
  hand-tuned score → zero behaviour change). Sigmoid keeps the value bounded so a
  pathological fit with huge weights can't produce a runaway `final_score` (raw `exp()`
  would — a separable fit pushed a logit to `exp≈2003`; sigmoid caps it at ~1.0 while
  preserving order).

**Wiring** (`stage4_moments.py`): after `final_score` is assigned, `maybe_rescore`
replaces it when (and only when) a fitted file is present. Applied before the
position-weight + within-bucket-norm transforms, so those still layer on top unchanged —
v1 fits the per-moment product, not those two bucket-level transforms. Failure-soft:
import error, bad config, or exception → hand-tuned score. Verified: **no config → None
(true no-op); identity config → order unchanged; huge-weight config → bounded (0,1),
order preserved.**

## B3 — the fitter (`scripts/research/fit_ranker.py`)

Loads cached `pass_c_candidates.json` traces + a labels JSONL
(`{"run","timestamp","label"}`), builds the feature matrix via `ranker.feature_vector`,
and fits an L2-regularised logistic model → `config/selection_ranker.json`
`{weights, bias, meta}`. **Self-contained**: pure-Python standardize + logistic gradient
descent (no sklearn/numpy — ~50 weights over a few-thousand rows in <1 s). `--self-test`
plants a reaction-carried signal and asserts recovery (learned
`ix_reaction_low_keyword = +15.7`) + a clean round-trip through `ranker.py`. Delete the
output file to revert to hand-tuned scoring.

## B5 — decorrelation (`text_model_passd` → gemma-4)

The Stage 4 text **rubric is "Pass D"**: an independent second opinion. New
`text_model_passd` (config/models.json + run_pipeline forwarding) lets the rubric run a
DIFFERENT model family than Pass B so their errors decorrelate instead of echoing. Set
it to `google/gemma-4-12b-qat` (confirmed loaded in LM Studio) while Pass B runs qwen.
**Default null → falls back to `text_model_passb` → `text_model` (no decorrelation, no
change).** Threaded through `stage4_rubric._call_llm`. Verified end-to-end: gemma-4 (a
thinking model, emits a reasoning preamble) returns rubric JSON that the rubric's
already-Gemma-aware `_parse_response` extracts cleanly (full 7-axis scores). VRAM: LM
Studio pools both GPUs (5060 Ti 16 GB + 6700 XT 12 GB ≈ **28 GB** Vulkan — see
[[concepts/vram-budget]]), so gemma-4-12b (~7 GB) + qwen-35b (~22 GB) ≈ 29 GB is just over
the pool → co-loading is marginal (LM Studio swaps/partially spills around the rubric
call). Pairing gemma-4 with a SMALLER Pass B model fits both comfortably — the sensible
decorrelation config. Opt-in either way. The vision judge (`stage5_5_judge`) already uses
a distinct model role (vision vs Pass B text) — extending an explicit `passd` override to
it is a follow-up.

## What's left — the data step (owner/harness)

The machinery is done and safe; producing REAL fitted weights needs labelled runs.
**Update 2026-07-05: traces bank AUTOMATICALLY** — `common.cleanup` embeds the full
`pass_c_candidates` trace in every `clips/.diagnostics/last_run_*.json`, and `fit_ranker`
reads those directly (verified: 763 rows loaded from the last 3 real runs). So step 1
costs nothing — every run contributes. Labels are the ONLY missing input:
1. (automatic) every real VOD run banks its B1-enriched trace in the diagnostics dir.
2. Build a labels JSONL from [[entities/bootstrap-twitch-clips]] triples (+ community
   highlight↔VOD alignment) marking which candidate timestamps were real highlights.
3. `python scripts/research/fit_ranker.py --traces <dir> --labels labels.jsonl` →
   `config/selection_ranker.json`. The pipeline picks it up automatically (logs
   `[RANKER] fitted selection_ranker.json loaded`).
4. To enable decorrelation: set `text_model_passd` in config/models.json.

Until then everything runs on the hand-tuned constants exactly as before. This is the
"[[concepts/case-rap-battle-missed]]" fix path: a fitted ranker can up-weight the
rare-but-cross-validated reaction-carried moment the hand-tuned axis stack currently
drops.

Related: [[concepts/plan-calibration-loop]] · [[concepts/plan-pipeline-upgrade-2026-07]] ·
[[concepts/case-rap-battle-missed]] · [[concepts/case-incongruity-comedy]] ·
[[concepts/corpus-learning-loop-2026-07]] (7.3 transcript-value = a source of anomaly labels) ·
[[entities/bootstrap-twitch-clips]]
