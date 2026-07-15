---
title: "Fine-Tuning Round Plan (post-Wave-3, 2026-07)"
type: concept
tags: [plan, quality, reference-lab, sfx, music, gates, subtype]
sources: 0
status: in-progress
updated: 2026-07-15
---

# Fine-Tuning Round Plan (2026-07) — v2

**v2 (2026-07-15, owner directives):** (1) the irl SUBTYPE layer must be implemented in BOTH
the Reference Lab AND the main pipeline; (2) the "learn" stays SEPARATE — the Lab produces a
comparison, the owner adds their modifications, and hands off to an agent to apply; **never a
straight/automatic modification of the main pipeline**; (3) the fewest possible gates that
need the owner's eyes → everything consolidates into ONE review handoff; (4) **no driver
updates, no memory tests** (removed; the crash forensics stay in [[concepts/bugs-and-fixes#BUG 75]]-era
notes as reference only).

**Standing owner constraints:** keep the current frequent-SFX density; channels non-monetized;
no model training ever (learning = reviewable prompt/config artifacts, retrieval few-shot,
at most the gated linear ranker).

---

## The learn boundary (standing rule)

```
sensors → cards → comparison report + DRAFT proposals   (Lab, agent-side, auto)
                       ↓
        OWNER reads, edits, approves/rejects — "my modifications"   (the ONE gate)
                       ↓
        agent applies the owner-marked set to the pipeline           (agent-side)
                       ↓
        re-measure via the Lab loop; deltas reported                 (agent-side)
```

The Lab NEVER writes into pipeline prompts/config on its own. Every Lab→pipeline change
passes through the owner handoff — including Track E shape guidance.

---

## Phase 1 — Agent build (no owner eyes needed)

- [x] **1a. Reference re-decompose (A1)** — 101 clips, batched-CUDA, auto-outro, raised CLAP
  thresholds, fresh music_bed. Then `--refresh-facts` + report regen. *(running — chain
  auto-completes on the batch notification)*
- [ ] **1b. Cuts-metric native-vs-added audit (A2)** — ours:gaming reads 10 cuts/30s; verify
  against raw-VOD windows (the music-bed A/B method). Fix or re-document the metric as
  "visual cut rate the video carries". No verdicting cuts gap items until this lands.
- [ ] **1c. SUBTYPE layer — Reference Lab side**: `subtype` field in the card prompt
  (irl vocabulary: `banter_roast / prank_public / freakout_overreaction / performance_rap /
  wholesome / other`; non-irl categories may fill it or 'none'); `category` stays the stable
  join key. Re-card the reference corpus (~86 × ~40 s on the 35B) AND the current our-clips
  card dirs (comparability). Aggregates + diff gain within-category subtype breakdowns
  (shown only where n≥8).
- [ ] **1d. SUBTYPE layer — main pipeline side**: Stage-4 Pass-B output schema gains an
  optional `subtype` (SAME vocabulary as the Lab, so ours↔reference joins work); carried
  through scored_moments → Stage 6 → effects_log → our cards. **Label-only at first — zero
  scoring/behavior change** (default-neutral; per-subtype styling/config keys reserved but
  empty). Any behavioral use of subtype arrives only via the owner handoff.
- [ ] **1e. Music ground truth in effects_log (A3)** — render logs the music decision
  (none/folder/track) so future compares separate added vs stream-native without raw-VOD
  A/B. Piggybacks on the same plumbing commit as 1d.
- [ ] **1f. Shape-guide DRAFT** — per-category (and per-subtype where n≥8) profiles from the
  re-decomposed, subtyped cards: arc mix, payoff placement, duration percentiles, hook
  mechanics, music-bed norms. Soft-prior phrasing only. Lands as a wiki page marked
  **DRAFT — not applied**; nothing touches the pipeline yet.
- [ ] **1g. Review-bundle preparation** (so the owner's single sit-down covers everything):
  jump-cuts sample (one VOD re-render with `CLIP_JUMP_CUTS=gaps` — cut_inference is
  phase-pinned safe), news-compile sample (kokoro VO), and the **fresh-VOD speed benchmark**
  (agent-verifiable, no eyes needed — turns the 31–40 min fresh-3h projection into a
  measured number and exercises caption_judge_multi live).

## Phase 2 — THE owner review handoff (the only eyeball gate)

One sit-down, one bundle, owner returns "my modifications":
1. **Clip review**: the 15-clip set + the jump-cuts variants + the news sample — complaints
   tagged by stage via [[concepts/quality-leverage-ranking-2026-07]]'s routing table.
2. **Gap-report verdicts** (25 items, recommendations pre-attached): sfx = not-a-problem
   (owner keeps frequent SFX); cuts items pre-resolved by 1b; duration items informed by the
   jump-cuts sample just watched; casing → voice bank; chat overlay + story music bed =
   owner want/skip calls.
3. **Shape-guide markup**: approve / edit / strike lines of the 1f draft.

No other gate requires owner eyes. Curation (voice bank, labels) stays an optional trickle,
never a blocker.

## Phase 3 — Agent apply + re-measure (no owner eyes)

- [ ] Apply the owner-marked shape-guide lines + approved gap levers to Stage-4 guidance,
  rubric wording, duration constants, Stage-6 hook guidance, and any per-subtype config the
  owner approved.
- [ ] Re-run the Lab comparison on the next clip runs; report deltas against the Phase-1
  baselines (that's the whole point of the deterministic metric set).
- [ ] R5 retrieval few-shot stays gated until after one full Phase-3 cycle proves the
  static guidance out.

## Removed per owner (2026-07-15)

- ~~NVIDIA driver update~~ and ~~memtest~~ — owner explicitly declined; crash-forensics
  reference stays in the bug registry only.

## Parked (Track D, unchanged)

R5 retrieval few-shot (until a Phase-3 cycle completes); roast-cadence / disbelief-fail beat
wiring (pure config, pools stocked); fry_timer promotion; spec-decode (single-card only);
multi-clip parallel decompose workers (post-batching bottleneck is CPU stages; rare job class).

## Related
- [[concepts/reference-lab]] — measurement policy (sfx v2, caption dedup, music-bed, outro, device policy)
- [[concepts/bugs-and-fixes#BUG 75]] — why the report numbers changed this week
- [[concepts/quality-leverage-ranking-2026-07]] — complaint→stage routing for the review
- [[concepts/plan-jump-cuts-v2-2026-07]] / [[concepts/plan-news-compilation-2026-07]] — folded into the Phase-2 bundle
