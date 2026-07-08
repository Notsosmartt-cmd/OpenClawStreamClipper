---
title: "Label Paths & Durable Store (ranker training data)"
type: concept
tags: [learning, calibration, ranker, labels, path-b, path-c, data-flow, cleanup]
sources: 0
updated: 2026-07-08
---

# Label Paths & Durable Store

How the selection ranker ([[concepts/calibration-ranker-2026-07]]) gets its training
data: what a "label" is, the three ways to source one, and the committed store that
makes the trace pile safe to delete. This is the reference; the roll-out sequence lives
in [[concepts/plan-learning-activation-2026-07]].

## The core idea — labels are pointers, features live once

The ranker learns from **candidate moments** the pipeline already scored. Every run
auto-saves a **trace** (`clips/.diagnostics/last_run_<stamp>.json`) holding *all* ~250
candidates with their full feature vectors (Pass-B score, cross-val, style/pattern
factors, axis multipliers, reaction/keyword/motion signals, position weight, final
score, selected flag, preview) — but **no audio/video**.

A **label** is a tiny verdict stapled onto one of those candidates:

```
{ run: <which run>,  timestamp: <moment seconds>,  label: 1 | 0,  source: owner|social }
```

- **Features are never copied** — they exist once, in the trace. Labels just *point*.
- **At fit time**, `fit_ranker` **joins**: for each label it fetches that candidate's
  feature vector out of the trace and trains on it. Fetch-and-join, not copy.
- Consequence: deleting a trace a label points at **orphans the label** — which is why
  the durable store (below) exists.

`{run, timestamp}` matches a candidate within a tolerance; a POSITIVE label snaps to the
highest-scoring candidate in-window (a viewer/owner validated the *clip-worthy peak*, not
whichever line is nearest), a negative snaps to nearest. Selected-but-unrated clips are
excluded (unknown, not negative — the owner reviews only some).

## The three paths

### Path A — Twitch clip API  ❌ DEAD
`bootstrap_twitch_clips.py`. Rejected 2026-07-05: wrong universe (this niche's community
signal is viewer social recordings, not Twitch clips) *and* mechanically broken
(`PersistedQueryNotFound` — rotated persisted-query hash). Not used.

### Path B — owner feedback  ✅ the reliable fuel
- **Tool:** `scripts/research/rate_run.py` (`template` / `set` / `collect`).
- **How it works:** the owner reacts to clips the pipeline **produced** ("the Rap Battle
  was good", "Gym Class was lackluster"); the AGENT files each as a label via
  `rate_run set --match <title> --label 1|0` (fuzzy title match). NO manual labeling —
  the owner just talks. `collect` merges into `clips/.diagnostics/labels_owner.jsonl`.
- **Reads:** the run's trace + `effects_log.jsonl` (to map a clip title → its moment).
- **Reach:** only the **selected** clips (~10/run) — what the owner watched. Both
  positive AND negative.
- **Judge / truth type:** the owner's taste = the actual target (TikTok performance).
- **Blind spot:** can't see the ~240 rejected candidates, so it rarely surfaces a *miss*.
- **Requirement:** the owner reviews + an agent files it. It is NOT wired into the
  pipeline (manual/agent-run); the clip `.mp4` must survive the review window, then is
  disposable (the label points at the trace, not the file).

### Path C — viewer-posted clip alignment  ⚠️ built, mostly inert for this owner
- **Tool:** `scripts/research/align_ref_clips.py` → `clips/.diagnostics/labels_social.jsonl`.
- **How it works:** takes a **viewer's edited clip** from `reference_clips/` and finds
  *where in the source stream it happened* by transcript **shingle matching** (5-token
  sequences of the clip's speech, histogrammed against the VOD transcript; ≥6 hits in one
  spot = a confident match). That yields a POSITIVE label at that VOD timestamp: "a real
  viewer validated this moment."
- **Reads:** `reference_clips/.cache/<stem>.words.json` (viewer-clip transcripts, from
  `corpus_refresh`) + `vods/.transcriptions/*.transcript.json` (VOD transcripts, Stage 2).
- **Reach:** **any** moment a viewer clipped — INCLUDING ones the pipeline **rejected**
  (the miss class). Positive only (no "viewer said bad" signal).
- **Judge / truth type:** community/platform validation.
- **Requirement — the catch:** it needs the clip's **source VOD on disk** to align
  against. The owner collects viewer *edited clips*, not the source streams, so for ~34
  of 36 reference clips there is nothing to align to — Path C fires only on the rare
  coincidence that a viewer clip came from a stream the owner independently ran
  (2/36 so far: the [[concepts/case-rap-battle-missed]] Mockingbird → rakai, + a Tylil clip).

### Near-miss review — the practical miss-class source (planned)
Because Path C (the miss-class tool) is mostly inert for this owner and Path B can't see
rejects, the fitting need — labelling the *dropped-but-good* moments — is served by
showing the owner the pipeline's **near-miss candidates (rank ~11–30)** from their OWN
runs to flag keepers. Same label format, same trace target, no viewer VOD required.

### B vs C at a glance

| | Path B (owner) | Path C (viewer clip) |
|---|---|---|
| Judge | owner's taste | community/platform |
| Reach | selected clips only | any moment, incl. rejected (miss class) |
| Polarity | positive + negative | positive only |
| Needs | owner review + agent files it | the clip's **source VOD** on disk |
| Output | `labels_owner.jsonl` | `labels_social.jsonl` |
| Status | reliable | built, mostly inert for this owner |

Both are **offline research tools**, NOT pipeline stages. Both emit the same
`{run, timestamp, label}` schema; `merge_labels.py` unifies them (owner overrides social
on collision, VOD→run resolved via the trace's `vod` stamp + `trace_vods.json` sidecar) →
`labels_all.jsonl`.

## Durable frozen store — the trace pile is safe to clean up

`scripts/research/label_store.py` + committed `learning/frozen_runs/<run>.json`
(added 2026-07-08, owner directive). Since a fit needs the labeled moment's features PLUS
the run's ~240 negatives PLUS the full set for the gate's recall@N, freezing just the
labelled moment isn't enough — so **the entire candidate set of any labeled run** is
frozen (features only, ~230 KB/run) alongside its labels.

- **Freeze:** `merge_labels` auto-freezes after every merge (additive, idempotent, B1-only).
- **Train trace-independently:** `fit_ranker --frozen learning/frozen_runs [--gate]` reads
  ONLY the committed store — verified byte-identical gate verdict vs the trace path. It
  survives a `clips/.diagnostics/` wipe or a fresh checkout (store is git-tracked; traces
  are gitignored).
- **Safe prune:** `scripts/research/prune_traces.py` deletes traces that are unlabeled OR
  frozen (keeps newest `--keep-recent`=8; REFUSES to orphan a labeled-but-unfrozen trace;
  dry-run default, `--apply` to delete).

**Cleanup rule:** a trace is safe to delete once it is *unlabeled* or *frozen*. Reference
`.cache` is regenerable from the kept `reference_clips/` sources, and Path-C labels freeze
durably — so cleaning it is safe too.

## File map / data flow

| File | Written by | Role | Durable? |
|---|---|---|---|
| `clips/.diagnostics/last_run_*.json` | pipeline (auto) | trace — all candidates + features | gitignored (regenerates per run) |
| `clips/.diagnostics/effects_log.jsonl` | pipeline (render) | per-clip effect log; B reads for titles | gitignored, append-only |
| `clips/.diagnostics/labels_owner.jsonl` | Path B | owner labels | gitignored |
| `clips/.diagnostics/labels_social.jsonl` | Path C | viewer-clip labels | gitignored |
| `clips/.diagnostics/labels_all.jsonl` | merge_labels | unified labels | gitignored |
| `clips/.diagnostics/trace_vods.json` | (hand/merge) | VOD→run sidecar | gitignored |
| **`learning/frozen_runs/<run>.json`** | label_store | **durable training snapshot (features + labels)** | **committed to git** |
| `vods/.transcriptions/*.json` | pipeline (Stage 2) | VOD transcripts; C reads | regenerable |
| `reference_clips/.cache/*.words.json` | corpus_refresh | viewer-clip transcripts; C reads | regenerable |

Related: [[concepts/calibration-ranker-2026-07]] · [[concepts/plan-learning-activation-2026-07]] ·
[[concepts/corpus-learning-loop-2026-07]] · [[concepts/case-rap-battle-missed]]
