---
title: "Activation Wave — builds + flag activations converging on one validation loop"
type: concept
tags: [plan, activation, anomaly-lane, p-tight, adaptive-count, near-miss, labels, judge]
sources: 0
status: in-progress
updated: 2026-07-08
---

# Activation Wave (2026-07) — turn the built machinery ON

> [!note] Phase 0 builds SHIPPED 2026-07-08 (default-off) — the run (Phase 1) is next
> **0.1 near-miss review** (`scripts/research/near_miss.py`, `--self-test` PASS) — live-verified
> on frozen run `010127`: it surfaced the proven [[concepts/case-rap-battle-missed]] Mockingbird
> miss (t=5305) in the rank-11–25 window, exactly as designed. **0.2 anomaly filename tag** —
> Stage 7 prefixes `ANOMALY_` (owner req); required a Stage 6 fix (`_process_moment` rebuilds the
> entry from scratch → `src` was being dropped; now preserved). **0.3 judge-timeline** — built
> behind `CLIP_JUDGE_TIMELINE` (default off, activates in Run 2), failure-soft, render verified.
> All flag-off / byte-identical. `py_compile` clean. Left: the Phase-1 flagged render + owner review.

> [!note] Phase 1 RUN + Phase 2 OWNER REVIEW done 2026-07-08 — promotions decided
> Run 1 (2xRaKai, all flags): exit 0, 10 clips, 82m37s. **Owner verdict: 7/10 good with no
> correction** (incl. the previously-critiqued Shower Bluff — P-TIGHT's target case validated),
> 3 need tweaks (Disney: rap-flow head over-cut; Coke Machine: escalation build-up cut + title
> orphaned; Wimpy Kid: same class, left unrated). Review exposed **[[concepts/bugs-and-fixes#BUG 66]]**:
> Stage-6's rebuild dropped `primary_pattern` → the rap/freestyle exemption NEVER fired live
> (T=9567 `rap_battle_freestyle` was trimmed). Fixed same-session + head defaults per review
> (`head_min_lead_s` 2→4, `head_min_sentences=2`, segment-type exemption) — synthetic 4-case PASS.
> **Promotions:** P-TIGHT = KEEP (re-validate the 3 tweak clips next run). Adaptive count =
> **HOLD IN SHADOW** — the floor's one real-run trim (t=9926 Disney) was owner-GOOD, and the
> re-run τ sweep now shows every trimming τ cuts a GOOD clip → promotion blocked by the label
> constraint, exactly as designed. Anomaly lane = INCONCLUSIVE (4 proposed, all merged into
> transcript candidates within the 25 s dedup — the lane only adds distinct clips for ISOLATED
> cross-modal moments; 2xRaKai had none). Labels: 9 positives filed + frozen (4 runs / 20 labels
> now); ranking gate re-ran → still REJECT (0.63 < 0.963 — confirmatory positives can't beat the
> baseline that picked them; the differentiator is near-miss keepers, still unflagged).
> Judge-timeline Run 2 still pending.

Owner directive (2026-07-08): evaluate five ready items and compile them into one
implementation plan. Verdict: **they belong in ONE plan** because they converge on a
single validation run + a single owner review session — planned separately they'd each
demand their own ~50–75 min render; planned together, one run answers four questions at
once and every answer becomes ranker labels. This page is that plan.

**The five items** (all previously built/validated except the two Phase-0 builds):
near-miss review (build), judge-timeline / fusion option 3 (build, deferred activation),
anomaly lane (activate), P-TIGHT (activate), Plan A count (activate in shadow).
Plus a new owner requirement: **anomaly clips must be identifiable from the filename**.

## Why one plan (the evaluation)

- **Shared validation loop:** anomaly taste, P-TIGHT feel, the count would-trim list, and
  the near-miss keeper list are all judged from the SAME run's output. The review session
  is the expensive resource (owner time); batching maximizes it.
- **Shared payoff:** every review answer files labels (Path B + near-miss) → merge →
  freeze → the ranking gate + count gate re-run. The wave is simultaneously the next
  learning-loop iteration ([[concepts/plan-learning-activation-2026-07]] L4 cadence).
- **Attribution stays clean:** the activations touch different stages (proposal lane /
  boundary trim / count logging) and each leaves a distinct log signature (`src=ANOMALY`,
  `[P-TIGHT]`, `[COUNT]`). One deliberate exception — see judge-timeline sequencing below.

## Phase 0 — builds BEFORE the run

### 0.1 Near-miss review tool (`scripts/research/near_miss.py`) — build first
The only practical miss-class label source ([[concepts/label-paths-and-store-2026-07]]
§Near-miss review). Mockingbird sat at rank 24/257 — inside the band this surfaces.
- **Reads:** the run's trace (all candidates, rank/score/preview/why already banked).
- **Shows:** rejected candidates rank ~11–30 (`selected=false`, ordered by
  `pass_c_rank`), each with timestamp, preview text, category, score.
- **Snippets:** optional `--cut` flag extracts a short preview .mp4 per candidate via
  ffmpeg from the VOD on disk (cap ~20, bounded; delete after review — labels point at
  the trace, not the files).
- **Files labels:** owner reacts ("keep 3 and 7"); agent files `label=1` on keepers
  (and optionally `label=0` on viewed-and-boring) via the existing `rate_run`/
  `labels_owner.jsonl` path → `merge_labels` (auto-freezes).
- **Risk:** zero — research-side, no pipeline import.

### 0.2 Anomaly filename tagging (owner requirement) — small pipeline change
So anomaly-lane clips are classifiable at first glance in the clips folder.
- `src` already survives to Stage 4 output (`stage4_moments.py` output whitelist) and
  Stage 7 names clips `{title}.mp4` (`stage7.py:213`).
- **Change:** when the moment's `src == "ANOMALY"`, prefix the title → filename
  `ANOMALY_<title>.mp4` (and mirror the marker into `effects_log.jsonl`). No flag needed:
  the field only exists when the lane is on; absent → byte-identical.

### 0.3 Judge-timeline (fusion option 3) — build now, activate LATER (optional this wave)
Format `event_timeline` lines (words + live librosa events) into the Stage 5.5 judge's
per-clip text block behind a new flag `CLIP_JUDGE_TIMELINE` (default off), failure-soft.
- **Why build now:** cheap (~half day), all components exist.
- **Why NOT flag it on in Run 1:** it changes the judge's re-ranking — the same output
  surface the anomaly lane changes. Two selection-affecting variables in one run muddies
  the review ("did the lane propose badly or the judge rank badly?"). It gets its own
  activation in Run 2.
- **Honest ceiling:** re-ranks the already-proposed shortlist; can never recover a miss.
  Full value arrives only with CLAP-live (see hold gate below).

## Phase 1 — Validation Run 1 (one bounded render via phase_runner)

Flags: `CLIP_ANOMALY_LANE=1  CLIP_TIGHT_PUNCHLINE=1  CLIP_COUNT_ADAPTIVE=1
CLIP_COUNT_SHADOW=1` (judge-timeline OFF; Pass D gemma-4 optional — costs run time via
LM Studio swap at ~29 GB > 28 GB pool; owner's call, default skip).
- Anomaly lane: boost-only proposals; risk is taste (slot competition), not stability.
- P-TIGHT: content-adaptive head/tail trim, punchline categories only.
- Count shadow: logs the would-trim list on the TRUE `pre_bucket_score`; trims nothing.
- Run 1 also **re-freezes a trace with `pre_bucket_score`** so `count_sweep` stops using
  the final_score proxy.

## Phase 2 — Owner review (the single session that pays for everything)

Four questions from one batch: (1) are the `ANOMALY_*` clips keepers? (2) do P-TIGHT
boundaries feel right (no clipped words / lost setups)? (3) does the `[COUNT]` would-trim
list match taste? (4) any near-miss keepers (via 0.1)? → agent files ALL of it as labels
→ `merge_labels` → gates re-run (`fit_ranker --frozen --gate --count-gate`).

## Phase 3 — Independent promotions (each on its own evidence)

| Item | Promote when | Promotion |
|---|---|---|
| Anomaly lane | its clips are keepers (or trims obvious) | keep flag in standard run config; consider default-on |
| P-TIGHT | boundaries sound right | keep flag on; tune bounds only if a specific clip shows why |
| Plan A count | would-trim list matches taste | `CLIP_COUNT_SHADOW=0` next run (real trimming) |
| Judge-timeline | Run 2 (own activation) shows better ordering | keep flag on |
| Ranker / count gate | gates flip ENABLE (needs the labels this wave produces) | automatic via config file |

## Hold gate — CLAP in the live pipeline (deliberately NOT this wave)

CLAP already runs offline on reference clips (`audio_sense` via forensics/corpus tools);
the live lane uses the librosa `crowd_response` dial by design (no CLAP-over-VOD cost —
a VOD is 1–7 h vs a 15–60 s reference clip, and the CUDA lane is Whisper-contended).
**Trigger to build:** anomaly lane proves *signal-limited* — it surfaces the right
regions but can't distinguish laughter from music/noise with one dial. If its precision
is fine with librosa, this stays unbuilt and the wall-clock is saved forever.

## Expected improvements (honest)

- **Near-miss labels** — the corrective (not confirmatory) signal: fixes the
  selection-feedback bias (rejected candidates train as implicit negatives unless
  someone looks); accelerates both gates toward ENABLE. Biggest lever in the wave.
- **Anomaly lane** — only mechanism recovering the cross-modal miss class (~0% recall
  today); anything real it finds is pure gain, bounded by slot competition.
- **P-TIGHT** — directly the owner's length critiques (19.5 s setups, 5 s tails) on
  punchline clips; long-form lanes untouched.
- **Plan A count** — kills quota-padding on thin VODs, un-caps dense ones; shadow-first
  so zero risk until the owner approves the trim list.
- **Judge-timeline** — bounded: better ordering of the shortlist, no new recall.

## File map

| File | Phase | Change |
|---|---|---|
| `scripts/research/near_miss.py` | 0.1 | NEW — near-miss lister + snippet cutter + label filer |
| `scripts/pipeline/stages/stage7.py` | 0.2 | `ANOMALY_` filename prefix when `src=="ANOMALY"` |
| `scripts/lib/effects_log.py` (hook) | 0.2 | mirror anomaly marker in effects log |
| `scripts/lib/vlm_judge.py` + `stage5_5_judge.py` | 0.3 | timeline block in judge prompt, `CLIP_JUDGE_TIMELINE` |
| (no other pipeline changes) | 1–3 | activations are env flags on `phase_runner` runs |

Related: [[concepts/plan-learning-activation-2026-07]] · [[concepts/plan-adaptive-clip-count-2026-07]] ·
[[concepts/multimodal-fusion-2026-07]] (§implementation audit) · [[concepts/label-paths-and-store-2026-07]] ·
[[concepts/case-incongruity-comedy]] · [[concepts/case-rap-battle-missed]]
