---
title: "Plan — Calibration Loop (fit the multipliers)"
type: concept
tags: [plan, calibration, eval, scoring, pass-c, fitter, learning]
sources: 0
status: shipped
updated: 2026-07-04
---

# Plan — Calibration Loop

Filed from the 2026-06-12 deep evaluation. Executes [[concepts/clipping-intelligence]] **Opportunity A** (close the calibration loop) and sets up **Opportunity B** (log-space scoring).

**Problem:** the final clip score is a product of ~50 hand-tuned constants (×1.20 cross-val, ×1.15 speaker, style weights, length/tightness, position, 70/30 bucket blend, axis ceilings + [0.80, 1.35] clamp, 0.6/0.4 rubric blend…) that have never been fitted against ground truth. Concrete failure: the Delaware rap battle, post-fixes, was **detected** (Pass B 0.878, cross-validated) yet **dropped by Pass C** — lost its bucket to a Pass B 0.433 moment that drew a 1.55 axis multiplier vs its 1.05 ([[concepts/case-rap-battle-missed]]). A hand-tuned axis stack overruled a strong, rare, cross-validated detection.

---

## Current state: ~30% built (better-positioned than the wiki's own evaluation suggested)

Already exists:

- **`scripts/research/bootstrap_twitch_clips.py`** — `fetch-clips` (Helix + GraphQL fallback; view counts, VOD offsets) → `pair` (3 spaced negatives per positive, min_gap 300 s, deterministic seed) → `triples.jsonl`. Label sourcing: done. See [[entities/bootstrap-twitch-clips]].
- **Every multiplier already stamped per moment** in `hype_moments.json` (arc/reaction/baseline/engagement multipliers + signals, `cross_validated`, length/tightness, position weight, `base_rank`/`pass_c_rank`/`vision_rank`), and `pass_c_candidates.json` persists the full scoring chain for **all** candidates, not just winners. A fitter's input format: done.
- **Run diagnostics**: `axis_report.json` (per-axis active%, ceil/floor hits, clamp bound-count), `judge_tournament.json` (pairwise rationale + win counts), `stage_timings.json`, `logtool axes` / `logtool selection`. See [[concepts/observability]].
- **`scripts/lib/eval_tier4.py`** — reference-vs-selected comparator already exists. **Keep it — load-bearing here** (a 2026-06-12 module-liveness audit confirmed it's CLI-only but is the natural eval harness for this loop).

Cached per run already: `transcript.json`, `audio_events.json`, chat JSONL.

---

## Missing glue — BUILT 2026-07-04 (see [[concepts/calibration-ranker-2026-07]])

> [!done] Items 1-3 shipped default-off: pass_c_candidates.json enriched to a full feature row (1), `scripts/lib/ranker.py` replays the score in log-space (2), `scripts/research/fit_ranker.py` fits it (3). Only real labelled data remains.

## Missing glue (~1–2 days total)

1. **Cache Pass B raw output** (~30 min) — serialize all candidate moments *before* Pass C in the work dir. The one missing artifact for replay.
2. **Offline re-scorer** (~2 h) — CLI that loads cached moments + a candidate `selection_axes.json`/`rubric.json` and replays Pass C/D math. Seconds per iteration; no LLM, no Whisper.
3. **Fitter** (~3 h) — grid-search (later Bayesian) over multiplier ranges → re-scorer → recall@N + rank correlation against `triples.jsonl` via `eval_tier4.py` → emit `selection_axes_fitted.json`.
4. **VOD↔video_id sidecar** (small) — map local filenames to Twitch video ids so labels match runs.

---

## The learning ladder (answer to "the intelligence ceiling")

1. **Fit the existing constants** (above) — converts ~50 hand-set numbers from vibes to measured, no architecture change.
2. **Additive log-space scoring** (Opportunity B) — the multiplier chain becomes a tiny logistic model over features *already persisted per moment*. Same behavior class, but fittable, interpretable, immune to the saturation that forced the BUG-37 soft-cap. First genuinely *learned* component; trains on CPU in seconds.
3. **Outcome labels** — [[concepts/plan-unoriginality-audio-layer]] P5's `posted.log` (clip → treatments → flagged?/views) joins Twitch-clip labels; the system learns from actual platform reward. Also evaluates the `src=ANOMALY` proposer lane from [[concepts/case-incongruity-comedy]].
4. **DPO/LoRA on Pass B** with the bootstrap dataset (its docs already envision DPO triples); long-term, a dedicated highlight model — only after 1–3 prove value.

> [!note] "Logistic ranker" vs "DPO/LoRA" are very different scales — don't conflate them
> Steps 2 and 4 are *not* the same kind of thing, and the bullet above understates the gap:
> - **Steps 1–3 need no GPU and no new model.** The "log-space / logistic ranker" (step 2) is **not an LLM** — it's a ~50-weight logistic regression (one weight per feature already stamped on each moment). "Log-space" just means summing the logs of the factors instead of multiplying them, which makes the Pass C chain a plain weighted sum. It trains in **under a second on CPU** (`sklearn.linear_model.LogisticRegression().fit(...)`), no download, no VRAM. It is essentially the fitter's output in a fittable form. This is the high-leverage step and it is trivial to run on the RTX 5060 Ti 16 GB rig.
> - **Step 4 (DPO/LoRA) IS real LLM fine-tuning** and is the heavy, optional, last-resort tail. It does **not** mean downloading a new smaller model — **LoRA** trains small adapter matrices (a few MB) *on top of the Pass B model you already run*; **DPO** is the training objective (feed `(prompt, chosen, rejected)` pairs — the `bootstrap_twitch_clips` triples are exactly that shape). With 4-bit **QLoRA** a 7–9B model is fine-tunable on 16 GB but it's hours-per-run, tight, and only worth it after steps 1–3 prove value.
>
> | Step | What it is | Local? | Cost |
> |---|---|---|---|
> | 1 Fit constants | grid-search the ~50 numbers | ✅ | CPU, minutes |
> | 2 Logistic/log-space ranker | ~50-weight regression over stamped features | ✅ | **CPU, <1 s** |
> | 3 Outcome-label feedback | add posted→views/flag labels to the fit | ✅ | CPU |
> | 4 DPO/LoRA on Pass B | fine-tune the actual LLM (QLoRA adapter) | ✅ but heavy | GPU, hours |
>
> Bottom line: the core value of this plan stops at **step 2** — a CPU-trivial fit, not a model you train or download.

## Related

- [[concepts/clipping-intelligence]] — Opportunities A/B this plan executes
- [[concepts/plan-decorrelate-judges]] — companion fix for weakness #2; its agreement down-weight becomes a fittable constant here
- [[entities/bootstrap-twitch-clips]], [[concepts/observability]], [[concepts/case-rap-battle-missed]]
