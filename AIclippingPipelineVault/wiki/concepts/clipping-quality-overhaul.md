---
title: "Clipping-Quality Overhaul — Plan (Harness the Multimodal LLM)"
type: concept
tags: [plan, roadmap, clipping-quality, vision-judge, tournament-ranking, boundaries, duration, captions, multimodal, differentiation, virality-score]
sources: 3
updated: 2026-06-04
---

# Clipping-Quality Overhaul — Plan

The approved roadmap for fixing "clips are not good." Companion to the evaluation in
[[concepts/clipping-intelligence]]. Full working copy lives in the plan file
`~/.claude/plans/breezy-meandering-moon.md`; this page is the durable project copy.

> [!note] Status — approved 2026-06-04
> Plan 1 (prompt-only) first; trained ranker is the destination (Plans 2-4, optional).
> **Differentiation philosophy is being actively chosen** — we are deliberately **not** cloning
> commercial "virality score" clippers (see the [[#Differentiation stance]] section). The selection
> *brain* will be our own; we only borrow the *packaging* craft.

---

## Context

Clips are bad across all five axes: wrong moments, bad boundaries, wrong duration (too short / too
long), weak titles+captions, amateur look. Root cause (diagnosis + research):

- **Selection is transcript-only.** Stage 4 decides what gets clipped from the transcript alone; the
  multimodal model runs only in Stage 6 (post-selection, non-gatekeeping) writing titles + a tiny boost.
  The most powerful asset is wasted on captions.
- Commercial SOTA (Opus Clip *ClipAnything*) selects multimodally; academic SOTA for one 16 GB GPU
  favors **comparative / tournament VLM ranking** > absolute scores, then a small reward model on weak
  labels, then LoRA as a last resort.
- Quality-killers beyond selection: blanket `length_penalty`, boundary snapping that misses the
  hook/payoff, segment-level captions despite computed word timings, randomized rendering. Nothing is
  measured.

**User decisions:** full overhaul incl. a trained ranker is the destination, but optimize the
**prompt-only** path hard now; eval/training set optional + deferred; break into multiple plans.

## The reframe (one line)

Make the multimodal model the **judge of what gets clipped** (not just the title-writer), right-size each
clip to its content arc, start on the hook, keep the payoff, make it look intentional — prompt-only
first, measurable later.

---

## PLAN 1 — prompt-only clip-quality overhaul (DO NOW · no training)

Phased; every phase failure-soft (degrades to today's behavior); reuses the already-loaded Stage 6 model.

- **1.a — Stage 5.5 "Vision Judge"** *(headline)*: new `scripts/lib/vlm_judge.py` (shared VLM call +
  outage helpers extracted from `stage6_vision.py`) + `scripts/lib/stages/stage5_5_judge.py` (modeled on
  `stage4_rubric.py`). Shortlist top-N by Pass C `raw_score`; per clip build a card (4 reused Stage-5
  frames + ±6 s transcript + audio features); run a **seeded Swiss tournament** of pairwise "which clip
  is more engaging sound-off?" comparisons (cap ~30); win-count → reorder `hype_moments.json` + bounded
  ±25 % `raw_score` reweight (never deletes). Wire into `stage6.py` before vision enrichment (`check=False`).
  `config/judge.json` + `CLIP_JUDGE_CONFIG`. MVP = win-count Swiss only.
- **1.b — Arc-driven duration**: replace `length_penalty()` with `arc_fit_penalty(m)` — category bands
  (min/ideal/max) + shortness penalty so padded one-liners stop winning and long storytimes survive.
- **2.a — Hook/arc-aware boundaries**: Pass B prompt starts on the scroll-stopping line, ends after
  payoff+reaction; request `hook_time`; hook-aware start snap + reaction-protecting end guard in
  `boundary_detect.py`.
- **2.b — Judge polish**: Bradley-Terry tie-break, swapped-pair order-bias check, audio features; optional
  over-select so the judge can drop weak clips.
- **3 — Titles + kinetic captions**: viral-hook title prompt (keep grounding cascade); wire word-level
  captions (`stage7_transcribe.py` words SRT → `kinetic_captions.srt_to_ass()` → burn).
- **4 — Visual consistency**: constrain `originality.py` ranges; hook/caption collision floors;
  face-aware crop default via `face_pan.py` (else blur-fill).
- **Diagnostics**: land judge/boundary/duration fields in the work-dir JSON; `stage8_summary.py` emits a
  one-row-per-clip `clips/.diagnostics/last_run_clipcard.txt` for eyeball QA.

## ROADMAP — optional, gated on Plan 1

- **Plan 2 — measurement loop**: offline eval; labels optional (Twitch-clip harvester and/or small hand
  set); recall@N + rank-correlation. Where calibration + additive log-space scoring land.
- **Plan 3 — trained reward model**: multi-embedding MLP (CLIP/SigLIP + audio + text) on weak labels;
  shortlist re-ranker; de-correlates the same-model panel.
- **Plan 4 — optional LoRA fine-tune**: only if Plan 3 plateaus.

---

## Differentiation stance

> [!warning] We are NOT building an Opus-Clip clone
> The user dislikes commercial clip "taste." Research confirms the instinct: Opus Clip's **Virality
> Score** (0-99) is an *opaque, undisclosed-weighting* heuristic over four dimensions — **Hook, Flow,
> Value, Trend** — that even its makers don't validate, and creators report low-scored clips routinely
> beat high-scored ones (treat it as a noisy sort, not truth). Its bias toward high-energy / on-trend
> moments is exactly the "samey, energy-baity" output we want to avoid.

What we **adapt** vs **reject** vs make **ours**:

| Borrow (packaging craft — validated, platform-rewarded) | Reject (their selection taste) | Make ours (the differentiator) |
|---|---|---|
| First-3-seconds hook; cut on the strongest line | Opaque absolute "virality" score | Structure-first selection via the [[concepts/highlight-detection]] Pattern Catalog (setup→contradiction, challenge-and-fold, …) |
| Word-level kinetic captions (retention) | **Trend-chasing** (drives sameness) | **Comparative** (tournament) judging — "best clip from THIS stream", not "matches a viral template" |
| Sound-off readability; clean framing | Energy/keyword-density bias | A **novelty / anti-sameness** axis + (later) **channel-specific taste** instead of a global viral model |

### What "a good clip" means here — our own rubric (decided 2026-06-04)

Instead of an opaque 0-99 virality number, our selector optimizes a **transparent, tunable set of axes we
control**, judged **relative to the same stream** (tournament), with **virality weight = light
platform-awareness** (a small nudge for hook strength / sound-off readability — never the driver of *what*
gets clipped). All four axes are in scope (stream content varies too much to pick one), each decomposed
into its **own research/implementation sub-plan for a future session**:

- [[concepts/plan-arc-completeness]] — **A.** complete, self-contained setup→payoff
- [[concepts/plan-reaction-worthy]] — **B.** genuine, earned reaction (the multimodal/audio/chat signals selection ignores today)
- [[concepts/plan-baseline-contrast]] — **C.** deviates from the streamer's *own* norm (novelty — our most original mechanism)
- [[concepts/plan-batch-diversity]] — **D.** the delivered *set* is varied (no five near-identical clips)

These compose on the **Stage 5.5 Vision Judge** (Phase 1.a) as the shared substrate: A and B become judge
comparison criteria, C feeds a cheap pre-signal into Pass A + the judge card, D is a post-tournament
re-rank. Each sub-plan page is self-contained so a future agent can pick up exactly one.

## Related
- [[concepts/clipping-intelligence]] — evaluation of the current stack (what we're improving)
- [[concepts/highlight-detection]] — Stage 4; the Pattern Catalog we lean into as the differentiator
- [[concepts/vision-enrichment]] — Stage 6; where the judge plugs in
