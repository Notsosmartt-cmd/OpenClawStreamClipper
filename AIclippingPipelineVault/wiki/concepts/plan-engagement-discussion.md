---
title: "Selection Sub-Plan E — Engagement / Discussion-Worthiness (the 'yap' / take clip)"
type: concept
tags: [plan, selection, clip-worthiness, engagement, discussion, comments, niche, chat-features, pattern-catalog, media-pause, vision-judge, style, future-session]
sources: 3
updated: 2026-06-04
---

# Selection Sub-Plan E — Engagement / Discussion-Worthiness

> [!note] Status — research/implementation brief for a FUTURE session
> Fifth per-axis selection sub-plan under [[concepts/clipping-quality-overhaul]]. Onboard via that page +
> [[concepts/clipping-intelligence]]. Added 2026-06-04 from the user's "yap / take clip" insight. Global
> constraint: **virality weight = light platform-awareness**.

> [!note] Implementation plan — approved 2026-06-04 (not yet built)
> Backed by a concrete plan (full copy in the session plan file). Consolidates heavily with existing
> style/pattern/chat machinery. Building is a separate go-ahead.

## Implementation plan (E-MVP)

**Genuine new value** (vs the existing `spicy`/`informational` styles + patterns): the **observed
sustained post-moment chat discussion** signal (currently-unused `chat_features` `z_score`/`unique_chatters`
on `[T, T+60]`) and the **`media_pause_commentary`** vision archetype. Everything else reuses existing machinery.

- **E1 — `scripts/lib/engagement_signals.py`** (new, mirrors `arc_completeness.py`):
  `evaluate(moment, segments, *, chat_features, shape_module, markers, cfg)` → predicted stance
  (`claim_stake`/`info_ramble_marker` without an immediate `concession` + a held monologue, kept modest) +
  observed sustained chat discussion (`window(T, T+post_window)` `z_score`/`unique_chatters`). **Boost-only**
  `1 + gain·score` ∈ `[1.0, ~1.15]`. Failure-soft (no chat → predicted-only). `--selftest`.
- **E2 — Pass C** (`scripts/lib/stages/stage4_moments.py`): apply right after the arc-completeness block;
  stamp `engagement_*`. Surfaces takes **in auto mode**, not only the style.
- **E3 — an "engagement" style** (config-only): add to `config/style_pattern_weights.json` (+ aliases) —
  the existing `apply_style_weights()` auto-applies it; add matching `style_prompts`/`weight_map` entries
  in `stage4_moments.py`.
- **E4 — multimodal + judge**: add `media-pause-commentary` to the Stage 6 `interaction_shape` enum
  (`stage6_vision.py`) + a "clear take worth debating in the comments (even if calm)" criterion in
  `vlm_judge._INSTRUCTION` + an `[engagement: 0.NN]` card hint.
- **E5 — config**: an `engagement` block in `config/selection_axes.json`.

**Decisions:** both an always-on axis (E1) AND a selectable style (E3); **no new text pattern** (reuse
`hot_take_pushback`/`social_callout` + the pre-signal; `media_pause_commentary` is vision-only); niche
detection deferred; boost-only; B (`[T,T+12]` spike) vs E (`[T,T+60]` sustained) keeps them distinct.

**Verify:** `engagement_signals.py --selftest` (sustained-chat + clear-stake moment > flat; no-chat →
predicted-only) + `py_compile`; `stage5_5_judge.py --selftest` still passes; `--style engagement`
round-trips; live run shows `engagement=` in the Pass C log + `clips/.diagnostics/`.

## The metric
A good clip can be **low-impact but high-engagement** — it makes the **audience talk to each other in the
comments**: agree, disagree, relate, share their own take. The streamer doesn't need a big reaction; they
need a **clear stance / relatable take on a topic the audience cares about.**

> [!note] Engagement ≠ impact ≠ retention
> - **Impact** (axes A-D): is the moment a strong, complete, novel, distinct highlight?
> - **Retention** (Phases 2-4: hooks, boundaries, captions): will viewers watch the clip through?
> - **Engagement** (THIS axis): will viewers *comment / debate / relate*?
> Platforms reward retention most, but **substantive comments are the heaviest-weighted engagement
> signal**. This axis is complementary — it surfaces the "quieter genuinely-interesting" moments the
> viral-biased selection misses, exactly the user's complaint.

## Does it already exist?
**Partly, and mostly as latent material — there is no engagement *score* yet.**
- `scripts/lib/chat_features.py` measures real chat reaction, but timing-based chat scoring was *removed*
  from Pass A 2026-05-01 ("chat is latent vs. the moment"). For engagement we *want* that latency: the
  signal is the **sustained discussion AFTER the moment**, not an instantaneous emote spike.
- The Pattern Catalog (`config/patterns.json`) already has cousins: `informational_ramble`,
  `hot_take_pushback`, `reading_chat_reaction`. The specific **pause-and-opine** archetype is missing.
- `config/style_pattern_weights.json` already has `informational` / `conversational` / `spicy` styles —
  the hooks to build an **"engagement" style** on.
Commercial clippers proxy retention-weighted "virality"; none separately model "will this drive comments."

## The archetype to capture (user examples)
- A streamer **pauses a video/media and turns to camera to give a take** (DDG; Joe Bartolozzi pausing for
  a 45 s+ speech).
- A brief **opinionated side-note on a named topic/brand/event** — e.g. DDG on the AP × Swatch collab
  ("I don't like it, my watches become useless").
New Pattern Catalog entries to add:
- **`media_pause_commentary`** — media playing → pause/freeze → streamer faces camera and opines. *This
  transition is multimodal: the transcript can't see the pause-and-pivot, but the VLM can* (a prime
  Vision-Judge signal and a direct answer to "harness the multimodal model").
- **`topical_take_aside`** — a short, self-contained stance on a named topic/brand/event that invites
  agreement/disagreement. (Joe Bart 45 s speech ≈ `informational_ramble` + `media_pause_commentary`.)

## The metric, two complementary signals
1. **Predicted engagement (always available, prompt-only):** the judge/LLM rates "discussion-worthiness"
   — is there a clear stance/opinion on an identifiable topic that invites agreement, disagreement, or
   "this happened to me too"? Structural corroboration: opinion/stance language, a named entity (brand,
   person, event, game), monologue length, the `media_pause_commentary` pattern.
2. **Observed engagement (when VOD chat exists):** measure **post-moment discussion density** in the
   window AFTER the beat — sustained back-and-forth / reply-like chains / sentiment divergence (debate),
   not emote spam — via `chat_features.py`. Real signal, and a free weak label for Plans 2-3.

## Niche / niche detection — needed?
Engagement *is* audience-dependent (a watch take engages a watch audience). But **don't block on full
niche detection.** Ladder it:
- **v1 (niche-agnostic):** reward a clear opinionated take on *any* identifiable topic + the structural
  patterns. Works everywhere.
- **v2 (niche-aware prompt):** feed cheap context to the judge — the Twitch category/game + the VOD's
  topic clusters ("this is a {niche} streamer; their audience discusses {topics}").
- **v3 (channel-learned):** learn per-channel which topics actually drove discussion from chat history —
  the personalization that pairs with [[concepts/plan-baseline-contrast]] + Plan 3 (trained reward model).

## As an axis AND a style
"Not every streamer/clip needs to be high-impact." So expose engagement two ways:
- An **always-on axis** in the judge (light default weight), and
- A selectable **"engagement" style** (extend `config/style_pattern_weights.json`) that biases toward
  `media_pause_commentary`/`topical_take_aside`/`informational_ramble`/`hot_take_pushback` — choose it for
  yappy/commentary streamers via the existing Discord/dashboard style routing.

## Where it plugs in
- New patterns in `config/patterns.json` + their structural signals in `conversation_shape.py`.
- Predicted-engagement criterion in the Stage 5.5 judge (`vlm_judge.py`); `media_pause_commentary` as a
  vision cue from the Stage-5 frames.
- Observed-engagement: a post-moment discussion scorer in `chat_features.py` → Pass A additive + judge card.
- New `engagement` style in `config/style_pattern_weights.json` (reuse the existing style machinery).

## Composition with the other axes
Mostly **orthogonal to impact (A-D)** — its job is to rescue good *low-impact* moments. Overlaps
`hot_take_pushback`/`informational_ramble`; consolidate, don't duplicate. Pairs with **Arc-completeness**
(a take should still be self-contained) and benefits from the **retention** work (even a yap clip needs a
hook + captions to be watched).

## Open research questions
- Reliable **post-moment discussion** measurement from chat (debate vs hype vs spam); chat availability.
- Detecting `media_pause_commentary` from frames robustly (paused-media UI + gaze-to-camera).
- Default weighting: how much engagement vs impact by default, and per-streamer override.
- Avoiding a flood of low-energy talking clips when engagement is over-weighted.

## Verification
On a "yappy"/commentary VOD (or DDG/Joe-Bart-style content): does the pipeline now surface the
pause-and-opine takes it previously skipped? Where chat exists, do selected clips correlate with
above-baseline post-moment discussion? Blind "would this start a comment thread?" rating vs current picks.

## Related
- [[concepts/clipping-quality-overhaul]] · [[concepts/clipping-intelligence]] · [[entities/chat-features]] · [[concepts/highlight-detection]] (Pattern Catalog) · [[concepts/style-profiles]]
- Sibling axes: [[concepts/plan-arc-completeness]], [[concepts/plan-reaction-worthy]], [[concepts/plan-baseline-contrast]], [[concepts/plan-batch-diversity]]
