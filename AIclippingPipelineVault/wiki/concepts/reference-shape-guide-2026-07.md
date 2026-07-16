---
title: "Reference Shape Guide (2026-07) — DRAFT"
type: concept
tags: [reference, shapes, subtype, detection, draft]
sources: 0
updated: 2026-07-15
---

# Reference Shape Guide — DRAFT

> [!success] APPLIED 2026-07-16 under the mechanical gates (owner dropped the sit-down:
> n≥8 floor = the approval gate, soft priors only, sub-floor auto-skip). Line fates:
> **1, 2, 4 applied** (species window-placement guidance → Pass-B prompt via
> `config/shape_priors.json` + `_species_priors_block`); **5 applied as HINTS only**
> (typical-length lines in the prompt; the 90/150 s hard caps untouched — hard caps from
> n=8-16 would risk recall); **6 = already satisfied** (hooks are mandatory by pipeline
> design; the wholesome exception auto-skipped at n=5); **3 auto-skipped** (n=4);
> **7 applied** — `solo_monologue` promoted into the card prompt, Pass-B vocabulary, and
> the judge's verdict enum, with a selective re-card of the irl_other clips. The same
> config feeds the S4.5 judge's packet norms (J7).

Data basis: 100 v3 cards (owner's 35B, anti-lazy subtype prompt), decomposed with the
BUG-75-clean counting, per-clip outro trims, and the gap-tonality music-bed scan.
Distillation floor: **n≥8** — groups below it are listed as *insufficient sample*.

## Measured profiles

| Group | n | Dominant arcs | Payoff lands | Med dur | Music bed | SFX/30s | Text hook |
|---|---|---|---|---|---|---|---|
| irl_moment (all) | 86 | instant 36 / setup_payoff 19 / escalation 15 | ~79% | 30s | 57% | 1.2 | 90% |
| **irl/banter_roast** | 45 | instant 20 / setup_payoff 16 | **~84%** | 30s | 68% | 1.3 | 93% |
| **irl/solo_monologue** (promoted from irl_other 2026-07-16; post-re-card n=17, irl_other emptied to 0) | 17 | **story 8** / instant 7 | — | **54s** | **28%** | 1.9 | 100% |
| **irl/freakout_overreaction** | 10 | **escalation 6** / instant 3 | **~34% (front-loaded)** | 31s | 52% | 0.9 | 80% |
| gaming | 8 | instant 3 / story 2 / list 2 | ~77% | **56s** | **85%** (game audio) | 0.6 | 100% |
| news_compilation | 5 *(< floor)* | list 5/5 | — | 61s | 0% | 0 | 100% |
| irl/prank_public | 6 *(< floor)* | escalation/instant | ~85% | 39s | 79% | 0.2 | 83% |
| irl/wholesome | 5 *(< floor)* | story 4 | ~68% | 46s | 77% | 0 | 40% |
| irl/performance_rap | 4 *(< floor)* | instant 3 | ~100% | 22s | 29% | 1.4 | 100% |

## Candidate guidance lines (each = one approve/edit/strike decision at the sit-down)

**Detection / window placement (Stage 4):**
1. Banter/roast moments typically resolve LATE — the payoff lands in the final fifth of the
   clip; prefer windows that keep ~25 s of build before the beat and little after.
2. Freakout/overreaction moments are FRONT-loaded — the eruption arrives in the first third
   and the value is the sustained reaction; prefer windows that start at (or just before)
   the trigger and ride the aftermath.
3. Performance (rap/song) moments are instant-shape and SHORT (~20-25 s): clip the bar/hook
   itself, payoff at the very end, no aftermath needed.
4. Solo monologue/story moments run LONG (~45-60 s) with a story arc — don't tighten them
   to banter length; the arc needs its full spine.

**Duration constants (per-species, replacing one-size caps):**
5. banter_roast ≈ 30 s · freakout ≈ 30 s · performance ≈ 22 s · solo monologue/story ≈ 55 s
   · gaming ≈ 55 s (current pipeline caps: 90 s general / 150 s storytime).

**Stage-6 hook guidance:**
6. 90-100% of reference clips in every strong group carry an on-screen TEXT HOOK —
   wholesome is the lone exception (40%). Keep hook text mandatory except for wholesome.

**Vocabulary (Lab + pipeline):**
7. **Promote `solo_monologue` to a named subtype** — 12-13 of the 16 irl_other
   justifications describe exactly this species (solo advice/story/commentary/interview);
   its profile (story arc, 54 s, low music) is nothing like banter. Requires: one enum word
   in the two subtype prompts + a re-card to reclassify the 16.

## Caveats (read before approving)

- **Corpus niche**: one scene (rap-streamer TikToks); these priors describe THIS corpus,
  not short-form in general. Growing corpus diversity outranks tuning to it.
- **Label noise**: category/subtype are 35B judgments at temp 0.3 — v2→v3 re-cards moved
  clips between categories (story/controversy collapsed into irl_moment; the story SHAPE
  now shows via arc + subtype instead). Aggregates are meaningful; individual cards wobble.
- **Music-bed numbers** measure what the video CARRIES (reference beds may be added or
  source-native — same as ours).
- Sub-floor groups (prank, wholesome, performance, news) are shown for orientation only —
  do not distill rules from n<8.

## Related
- [[concepts/plan-fine-tuning-round-2026-07]] — the plan this draft feeds (Phase 2 markup)
- [[concepts/reference-lab]] — measurement policy behind every number here
