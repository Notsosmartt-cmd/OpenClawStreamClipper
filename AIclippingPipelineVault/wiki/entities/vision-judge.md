---
title: "Vision Judge (Stage 5.5)"
type: entity
tags: [vision-judge, stage-5-5, tournament, swiss, pairwise, multimodal, selection, reranking, plan-1a, module]
sources: 1
updated: 2026-06-04
---

# Vision Judge (Stage 5.5)

The stage that finally lets the **multimodal model decide *which* moments win**, not just title them. Built 2026-06-04 as **Phase 1.a** of [[concepts/clipping-quality-overhaul]] — the shared substrate the five selection axes (A-E) plug into.

Runs **between Stage 5 (frame extraction) and Stage 6 (vision enrichment)**, after the vision model is already loaded, so it adds **no extra model load**. Wired in `scripts/pipeline/stages/stage6.py` via `common.run_module("stages/stage5_5_judge.py", check=False)` (failure-soft).

> [!note] Why a *tournament*, not an absolute score
> Research (BLITZRANK, Vote-in-Context) and the [[concepts/clipping-intelligence]] evaluation agree: VLMs are weak at absolute 0-10 scoring but strong at **relative "which of these two is better"**. So the judge ranks by **pairwise comparison**, deliberately *not* reproducing the opaque-absolute "virality score" of commercial clippers ([[concepts/clipping-quality-overhaul]] differentiation stance).

---

## How it works

1. **Shortlist** the top-N Pass C moments by `raw_score` (`shortlist_max`, default 12; needs ≥ `shortlist_min`=3).
2. Per clip build a **card**: 4 reused Stage-5 frames (`t0/tplus1/tplus3/tplus5`) + the ±clip-window verbatim transcript + category/why.
3. **Seeded Swiss tournament** (`vlm_judge.swiss_tournament`): `ceil(log2 N)+rounds_extra` rounds, each pair asked *"which clip is more engaging to a stranger scrolling sound-OFF — a self-contained moment with a clear payoff?"* → `{"winner":"A"|"B","confidence","reason"}`. Bounded by `max_comparisons` (default 30 ≈ one round-robin's worth).
4. **Aggregate win-count** → `vision_rank`, `vision_win_count`, `judge_rationale`.
5. **Bounded reweight**: `raw_score *= 1 + reweight_span·(1 − 2·(rank−1)/(N−1))` — rank 1 ×(1+span), last ×(1−span), default span 0.25. Updates `raw_score` **and** the clamped `score`, and stamps `pass_c_raw_score`. Because Stage 6 sorts `scored_moments.json` by `raw_score`, this is what makes the judge's verdict the **final selection order**.
6. Re-sorts `hype_moments.json` by the new `raw_score` and writes it back.

**Never deletes a clip** — the reweight is bounded and multiplicative, so the judge re-orders/re-weights but a moment can't be zeroed or dropped. (Optional future knob: over-select in Stage 4 so the judge can *trim* — Phase 2.b.)

---

## Modules & config

- `scripts/lib/vlm_judge.py` — shared, network-decoupled primitives: `vision_call()` (multimodal LM Studio POST, `reasoning_content` fallback), `compare_pair()` (builds the A/B content array + parses the verdict), `swiss_tournament()` (pure ranking logic — unit-testable with a mock comparator), `load_frame_parts()`. Standalone (does **not** import `stage6_vision`, to keep blast radius small).
- `scripts/lib/stages/stage5_5_judge.py` — orchestrator (modeled on `stage4_rubric.py`): `run_judge(moments, cfg, work_dir, transcript, compare_fn)` returns the re-ranked list; `main()` wires the files; `--selftest` runs a mock tournament.
- `config/judge.json` — `enabled, shortlist_min/max, frames_per_clip, rounds_extra, max_comparisons, reweight_span, per_pair_timeout_seconds, max_tokens, stage_timeout_seconds, fail_streak_limit`. Exported as `CLIP_JUDGE_CONFIG` from `paths.child_env()`.

---

## Failure-soft behavior (mirrors BUG 32 / Pass D)

| Condition | Behavior |
|---|---|
| `enabled=false` / `< shortlist_min` moments | skip; Pass C order untouched |
| LM Studio outage (`fail_streak_limit` consecutive network failures) | abort tournament early, keep Pass C order |
| Stage timeout (`stage_timeout_seconds`) | finalize from comparisons completed so far |
| `< 2` completed comparisons | no reweight; Pass C order untouched |
| A pair returns bad/empty JSON | counted as a tie; tournament continues |
| Frames missing for a clip | text-only comparison (still ranks) |

So the worst case is "the judge did nothing" — it can never break or empty the render set.

---

## Diagnostics

Each judged moment gains `vision_rank`, `vision_win_count`, `judge_rationale`, `pass_c_raw_score` (all captured in the `clips/.diagnostics/last_run_*.json` dump). The stage logs a summary line: `[JUDGE] re-ranked N clips in G comparisons, Ts — T=..(#1) > T=..(#2) ...` plus a per-clip `raw a->b` line.

> [!note] Verification status (2026-06-04)
> Logic verified offline via `python scripts/lib/stages/stage5_5_judge.py --selftest` (a mock comparator drives the known-best clip to rank #1 and the worst to last; reweight stays within ±span; nothing zeroed; comparison cap respected) + `py_compile`. The **live** multimodal call path (`vision_call`/`compare_pair`) verifies on the next real VOD run — it is failure-soft until then.

---

## How the selection axes plug in (A-E)

This is the substrate for [[concepts/clipping-quality-overhaul]]'s five axes: **A** (arc completeness — already a Pass-C pre-signal via [[concepts/plan-arc-completeness]]) and **B** (reaction-worthy) become **judge comparison criteria** in the `compare_pair` prompt; **C** (baseline contrast) feeds a pre-signal into the card; **D** (batch diversity) is a post-tournament re-rank; **E** (engagement) is a judge criterion + a selectable style. Today's prompt encodes a single "engaging sound-off, self-contained payoff" criterion — the axes refine it.

---

## Related
- [[concepts/clipping-quality-overhaul]] — the plan this implements (Phase 1.a)
- [[concepts/vision-enrichment]] — Stage 6, which runs *after* the judge and stays non-gatekeeping
- [[concepts/highlight-detection]] — Pass C, whose `raw_score` order the judge re-ranks
- [[concepts/clipping-intelligence]] — the evaluation that motivated promoting vision to *selector*
- [[entities/lm-studio]] — the multimodal model the judge calls
