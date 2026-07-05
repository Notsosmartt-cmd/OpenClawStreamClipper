---
title: "Plan — Learning Activation (render batch + label loop + first ranker fit)"
type: concept
tags: [plan, calibration, ranker, labels, twitch, render, learning, roadmap]
sources: 0
status: planned
updated: 2026-07-05
---

# Plan — Learning Activation

The unified, agent-iterable plan that takes the shipped-but-dormant learning machinery
([[concepts/calibration-ranker-2026-07]]) to a LIVE fitted ranker, wrapped around the
pending validation render batch. An executing agent works phases L0→L4 in order; every
phase has commands, files, a Definition-of-Done, and a fallback. House rules apply
throughout: flag-gated/default-off, failure-soft, wiki+commit per change, every run
bounded + verified not-stuck ([[concepts/plan-pipeline-upgrade-2026-07]] §rules).

## Path A vs Path B — tested verdict (2026-07-05)

**Path A (Twitch community-clip labels) is BROKEN as shipped**: live test returned 0
clips for every channel incl. kaicenat; raw probe shows `PersistedQueryNotFound` — Twitch
rotated the persisted-query hash `bootstrap_twitch_clips.py` uses, and the tool swallows
the error. Repair options in L2. **Even repaired, A is a proxy** (live-Twitch-viewer
taste, hype-biased, some clips lack `vodOffset`) while **Path B (owner ratings) is ground
truth for the actual goal** (owner's taste, TikTok target) but only covers SELECTED
clips (10/run), never the ~247 rejected candidates. **Decision: B is required and leads;
A is opportunistic volume — merge when available, B overrides on conflict.**

## The loop being activated

```
render batch (L0) ──► owner listens + rates (B labels, ~2 min)
      ▲                          │
      │                          ▼
 fitted ranker ◄── GATE ◄── fit_ranker (L3) ◄── labels merge (L1) ◄── Twitch labels (L2, opportunistic)
      │
      └── every future run banks its trace automatically (already live)
```

Training data: every completed run already embeds its full B1-enriched Pass-C trace in
`clips/.diagnostics/last_run_*.json` (763 rows across the last 3 runs; `fit_ranker`
reads them directly — verified 2026-07-05).

---

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

## Phase L2 — Path A repair (opportunistic, time-boxed)

Two independent tracks; FIRST to land wins, other is dropped:
- **L2a (owner, ~5 min, official/stable):** register a free app at dev.twitch.tv →
  set `TWITCH_CLIENT_ID` + `TWITCH_OAUTH_TOKEN` (app token) env → the existing Helix
  path in `bootstrap_twitch_clips.py` activates unchanged.
- **L2b (agent, time-boxed 2 h, unofficial):** replace the dead persisted-query with a
  raw GraphQL query text (TwitchDownloader-style) under the public client-id.
  Acceptance: ≥20 clips for kaicenat with non-null `vod_id` + `vod_offset_s`.
  If the box expires without acceptance → mark A dead-for-now in this page, continue B-only.
- Conversion once either lands: `fetch-clips` for the 4 broadcasters → filter records to
  `vod_id ∈ {2742682361, 2752095628, 2752399598, 2756365448}` (from the VOD filenames)
  → `labels_twitch.jsonl` (`run` resolved via L1.1/`trace_vods.json`, `timestamp` =
  `vod_offset_s`, label 1; tol 8-10 s at fit time).

**DoD:** labels_twitch.jsonl with ≥10 offset-bearing positives across the 4 VODs — or a
documented dead-for-now verdict. **Never blocks L3.**

## Phase L3 — First fit + GATE (agent, <1 h once ≥3 rated runs exist)

1. Inputs: all `last_run_*.json` traces + `labels_owner.jsonl` (+ `labels_twitch.jsonl`
   if L2 landed).
2. **Holdout validation (the GATE):** leave-one-run-out — fit on N−1 runs, on the held
   run compare `recall@10` (fitted ranking vs hand-tuned final_score ranking) against
   its labels. **Enable only if fitted ≥ baseline on the held-out run(s).** Print both
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

## Phase L4 — Cadence (steady state)

- Every VOD run: trace banks automatically (zero effort).
- Owner: rate the produced clips (~2 min/run) — chat feedback counts; the agent files it
  via `rate_run.py`.
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
| GQL re-breaks after L2b | A is opportunistic by design; loop runs B-only |
| Owner fatigue on ratings | 10 clips × 1/0 ≈ 2 min; chat feedback accepted verbatim |

Related: [[concepts/calibration-ranker-2026-07]] · [[concepts/plan-calibration-loop]] ·
[[entities/bootstrap-twitch-clips]] · [[concepts/case-rap-battle-missed]] ·
[[concepts/corpus-learning-loop-2026-07]] · [[concepts/plan-pipeline-upgrade-2026-07]]
