---
title: "Moment Discovery Upgrades — Tier 1/2/3/4"
type: concept
tags: [moment-discovery, upgrade-plan, tier-1, tier-2, tier-3, tier-4, pass-a, pass-b, pass-c, pass-d, hub, lacy-penthouse]
sources: 1
updated: 2026-05-01
---

# Moment Discovery Upgrades

Hub page for the moment-discovery upgrade plan. Tiers 1/2/3 documented in `MOMENT_DISCOVERY_UPGRADE_PLAN.md` at the project root (outside the vault); **Tier-4** detailed in [[concepts/tier-4-conversation-shape]] (shipped 2026-05-01). All implementation pages backlink here.

> [!note] Tier-4 (2026-05-01) shipped
> Adds conversation-shape detection (turn graphs, discourse markers, off-screen voice intrusions, monologue runs, topic boundaries), a Pattern Catalog ([[concepts/tier-4-conversation-shape#phase-43---pattern-catalog--pass-b-prompt-rewrite]]), Pass D rubric judge (NEW phase between Pass C and Phase 4.2 boundary snap), Stage 6 shape detection, MMR diversity ranking, 5 new style presets (`conversational` / `informational` / `freestyle` / `chatlive` / `spicy`), and an eval harness. See [[concepts/tier-4-conversation-shape]].

The plan targets three classes of clip-worthy moments the original pipeline systematically missed:

1. **Long-range setup–payoff arcs** — claim made minutes ago becomes ironic / contradicted later (canonical Lacy penthouse archetype).
2. **Multi-modal events** — speaker changes, music/rhythm, crowd response, off-screen voice — invisible to a transcript-only view.
3. **Narrative continuity** — long storytimes that exceed a single Pass B chunk or get cut mid-arc by the 90 s clip cap.

Sequenced for ROI per engineering hour. Tier 1 ships first; Tier 2 builds on Tier 1; Tier 3 makes architectural changes that need Tier 2 signals to be valuable.

---

## The four signals every clip carries

| # | Signal | What it is | Owner after the plan |
|---|---|---|---|
| 1 | Local energy | Word density, exclamations, laughter, chat burst | [[concepts/highlight-detection]] Pass A (unchanged) |
| 2 | Local narrative | Setup–payoff inside one ~5 min window | [[concepts/highlight-detection]] Pass B (Tier-1 prompt upgrade) |
| 3 | Long-range narrative | Setup–payoff spanning 5–60 min | Tier-1 Q1 (cheap), Tier-2 M3 (full), Tier-3 A1 (best) |
| 4 | Multi-modal events | Speaker change, music/rhythm, crowd response, off-screen voice | Tier-2 M1 + M2 |

The Lacy penthouse moment requires #3 and #4 together — it is the canonical end-to-end test case for whether the upgrade succeeded.

---

## Tier 1 — Quick prompt + config wins (Pass B / Pass C)

Five low-risk changes to `scripts/clip-pipeline.sh` and `config/`. Shipped 2026-04-27. See [[concepts/highlight-detection]] for the integrated picture.

| Item | Mechanism | What it unlocks |
|---|---|---|
| **Q1** Prior-chunk summaries | One-line summary per chunk; last 2 injected into next chunk's Pass B prompt as `Earlier in this stream:` | Cross-chunk callbacks visible to the LLM (signal #3) |
| **Q2** Few-shot examples | Three explicit transcript→JSON examples (off-screen voice, long-form storytime, hot take with pushback) before the live transcript | Smaller / thinking-leaky models stop drifting from the schema |
| **Q3** Per-category keyword ceilings | Per-category cap in Pass C (`storytime` 0.90; `hot_take`/`emotional`/`controversial` 0.85; `hype`/`reactive` 0.75; `funny`/`dancing` 0.70) | Rare-but-specific keyword phrases compete fairly with cross-validated moments |
| **Q4** Per-segment chunk durations | `CHUNK_DURATION_BY_SEGMENT`: 480 s (`just_chatting`/`irl`), 360 s (`debate`), 300 s (`reaction`/`gaming`) | Storytimes / arguments fit in a single chunk instead of getting cut |
| **Q5** Variable clip duration cap | Pass B prompt + parser allow 150 s for `storytime`/`emotional` (was hard 90 s) | Multi-minute storytimes survive instead of being trimmed mid-arc |

---

## Tier 2 — Signal-adding modules

Three new feature streams that close the same Lacy gap from a different angle than Tier 1. M4 (self-consistency) was intentionally skipped — costly and subsumed by Tier 3 A1.

| Item | Page | Mechanism | Boost shape |
|---|---|---|---|
| **M1** Speaker diarization | [[entities/diarization]] | WhisperX + pyannote 3.1 attaches `speaker` to each transcript segment | Pass A +1 to `funny`/`controversial` when ≥2 speakers; Pass C ×1.15 multiplicative |
| **M2** Audio events | [[entities/audio-events]] | librosa CPU detectors per 30 s window: `rhythmic_speech`, `crowd_response`, `music_dominance` | Pass A +1 to matching keyword categories (boost-only) |
| **M3** Long-range callback detector | [[entities/callback-module]] / [[concepts/callback-detection]] | sentence-transformers + FAISS cosine search ≥ 5 min back, gated by Pass B' LLM judge | New `callback`-category moments; `cross_validated=true`; ×1.5 score boost |

All Tier-2 modules are graceful-degradation: missing dependencies → empty output, never fatal.

---

## Tier 3 — Architectural changes

| Item | Page | What changes |
|---|---|---|
| **A1** Two-stage Pass B | [[concepts/two-stage-passb]] | Single global LLM call over the chunk-summary skeleton catches arcs that span the whole stream (irony / contradiction / fulfillment / theme_return / exposure / prediction) |
| **A2** Visual setup–payoff verification | [[concepts/vision-enrichment]] | Stage 5 extracts setup±1 frames; Stage 6 prompt scores `callback_confirmed` 0–10 → multiplicative `[0.85, 1.20]` adjustment (only Stage-6 path that can *penalize*) |
| **A3** LLM judge for grounding | [[entities/grounding]] | Model-agnostic 5-dimensional judge runs as the cascade's Tier 2. **MiniCheck and Lynx fully retired 2026-05-01** — see [[concepts/bugs-and-fixes#REMOVAL 2026-05-01b]]. The judge resolves to whatever LM Studio model the operator has loaded (Qwen, Gemma, Llama). |

A3 originally shipped as additive (MiniCheck + Lynx kept as fallbacks per CLAUDE.md §6.2 graceful degradation) but was promoted to the sole Tier-2 mechanism on 2026-05-01 once the additive path proved structurally redundant — Tier 1's hard-event check is the real safety net, MiniCheck's literal-entailment training was mismatched to inferential summaries, and Lynx-via-LM-Studio routed to the main model anyway.

---

## Status snapshot (2026-04-28)

- Tier 1 (Q1–Q5): shipped 2026-04-27. **Q1 chunk_summary token budget bumped 200 → 4000 on 2026-04-28** ([[concepts/bugs-and-fixes#BUG 38]]) — was silently degraded on Gemma 4 because the budget assumed Qwen-class thinking.
- Tier 2 (M1, M2, M3): shipped 2026-04-27. M2 hang on long VODs fixed 2026-04-28 (see [[entities/audio-events]] BUG callout).
- Tier 3 (A1, A2, A3): shipped 2026-04-27. **A1 Pass B-global token budget bumped 2000 → 6000 on 2026-04-28** ([[concepts/bugs-and-fixes#BUG 38]]). A3 follow-up (judge rename + A/B harness) shipped 2026-04-27.
- Empirical validation gate (Tier-1 effect measurement before Tier-2 start) was skipped — see [[concepts/open-questions]].

> [!warning] Gemma 4 permanent-thinking budget gotcha
> Any `/no_think`-prefixed call with a tight `max_tokens` budget will silently fail on Gemma 4-26B-A4B (and any other LM Studio model whose thinking can't be disabled via `chat_template_kwargs`). The model burns 3000–6000 reasoning tokens regardless and then has no headroom for the actual answer. New call sites must budget for the full reasoning + answer or be explicitly Qwen-only.

---

## Related

- [[overview]] — system context
- [[concepts/clipping-pipeline]] — full pipeline placement of every Tier item
- [[concepts/highlight-detection]] — Pass A/B/C consumer of every signal
- [[concepts/callback-detection]] — M3 architectural concept
- [[concepts/two-stage-passb]] — A1 architectural concept
- [[entities/audio-events]] — M2 module
- [[entities/diarization]] — M1 module
- [[entities/callback-module]] — M3 module
- [[entities/grounding]] — A3 host module
- [[entities/grounding-ab]] — A3 validation harness
- [[concepts/open-questions]] — what's still un-validated after the upgrade
- [[concepts/bugs-and-fixes]] — running bug ledger including the M2 hang fix
