---
title: "Detection improvements (design answers — segment granularity, keywords, setup→payoff, duration skew)"
type: concept
tags: [design, plan, stage-3, stage-4, segment-detection, keywords, embeddings, setup-payoff, stitch, length-penalty, clip-duration, research]
sources: 1
updated: 2026-06-06
---

# Detection improvements (design answers)

Four design questions raised 2026-06-06 about the limitations in [[concepts/detection-walkthrough]]. **Analysis + options only — nothing implemented yet.**

## 1. The 10-min segment-granularity limitation (2-min debate absorbed into "gaming")

> [!note] Reframe first: it's mostly a *labeling* coarseness, not a detection loss
> Moment detection is **type-agnostic**. Pass B reads the actual transcript of every chunk and assigns each moment its **own** category (the debate beats become hot_take/controversial regardless of the segment label); A1/M3/prior-context operate on transcript + moments, **not** on the segment label. So a 2-min debate inside a gaming stream **still gets its clips found and categorized correctly**. The "gaming" label only affects (a) Pass B **chunk size** (5 vs 6 min) and (b) Pass A's **segment-specific threshold + score boost** — second-order effects, not whether the moment surfaces.

So *"can the models infer across chunks so the small pocket isn't an issue?"* — **they already do**, at the transcript/moment level (the cross-chunk machinery doesn't depend on segment granularity).

**If the residual labeling coarseness still costs clips** (measure with `logtool` first):
- **Finer + overlapping segment windows** — drop `SEGMENT_CHUNK` 600 s → ~300 s with overlap so short pockets get their own label. Cost: ~2× classification calls (cheap; each is one small LLM call). Lowest-effort fix.
- **Variance-triggered refinement** — only re-classify windows whose content looks mixed (high category variance), keeping the coarse pass elsewhere.
- **Local-window type for thresholds** — Pass A/B already peek at the local type (`_chunk_window_for` at start+150 s); push that further so thresholds use the *local* window type, not the merged segment.

**Recommendation:** measure impact first; if real, finer overlapping windows. Headline: detection is already type-agnostic, so this is minor plumbing, not a recall gap.

## 2. Is the keyword classification too crude — replace or phase out?

**Don't phase out Pass A** — it's the *deterministic recall net* under the LLM (Pass B can time out / self-limit / 400). Only the **literal-keyword term** is crude; Pass A's conversation-shape, audio-event (M2), and speaker (M1) signals are structural/multimodal and fine.

**Upgrade, don't replace:** add an **embedding-similarity category signal** as a co-equal to the keyword count — embed each 30 s window, cosine-compare to per-category **prototype / few-shot embeddings**, and feed the result into `total_signals`. This catches semantically-relevant windows that lack the literal word ("that's actually crazy" with no keyword), is deterministic, and reuses the **sentence-transformers stack already loaded for M3**. Keep keywords as the complementary fast signal.

- *Alternatives:* a small zero-shot NLI classifier (heavier, less deterministic); auto-mining the streamer's past VODs for high-signal n-grams (incremental, keeps the literal approach).
- *Tradeoffs:* needs the embedding model loaded (we have it), category prototypes defined, small added time per window. **Recommendation: embedding-similarity augmentation** — turns the crude literal match into semantic recall without losing the deterministic net.

## 3. Contiguous setup→payoff spans (vs payoff-centered clips)

Today A1/M3 emit a **payoff-centered ~35–45 s clip** with the setup only in the title/caption — so the irony/contradiction doesn't *land visually* (the viewer never sees "this is my penthouse" before "I never said it was mine").

**Options:**
1. **Single contiguous clip** `[setup_start, payoff_end]` — only viable for **short-range** arcs (≤90/150 s cap); a setup minutes before the payoff makes one clip far too long.
2. **Stitched 2-segment clip (jump-cut)** — a short setup snippet (~6–10 s, captioned "Earlier:") + the payoff (~25 s), concatenated. **Works for long-range arcs too** (the cut bridges the time gap). **Reuses the existing `stitch_render.py`** machinery (it already concatenates sub-segments with xfade transitions, per-member randomization, hook overlay, subtitles). A1/M3 already carry `setup_time` / `setup_text`. *Recommended.*
3. **Setup as a text card** before the payoff — lighter (no setup video), but less engaging than seeing it happen.

**How it improves the pipeline:** arc/callback clips become **self-contained** — the cross-chunk detection (A1/M3), which is currently semi-wasted (the clip is just the payoff), finally pays off in the final product. The Lacy-penthouse archetype renders correctly, and stitched setup→payoff is a strong differentiator vs single-window commercial clippers.

**Tradeoffs / risks:** editing complexity; disjointed clips if the setup snippet is poorly bounded; the setup quote must be self-contained; extra render cost. Gate behind a config flag; bound to A1/M3 moments that carry a verified `setup_time`.

## 4. Stop the ~30 s skew WITHOUT deliberately biasing against 30 s

The skew comes from **two artificial pulls**, not from content. The fix is to make duration **length-neutral** (judge content, not seconds) so length *follows content* — NOT to invert the penalty (that would just create a 60 s skew, which is the same mistake in the other direction).

1. **Per-category default fallback (~30 s)** for moments without LLM boundaries (`DEFAULT_DURATIONS`). → Replace with **content-aware boundary inference** for boundary-less moments: expand from the peak to the nearest sentence/silence edges (reuse the boundary-snap signals), or a tiny LLM boundary call. No artificial 30 s default.
2. **`length_penalty`** docks long clips at selection (`≤30 s ×1.0 … >75 s ×0.65`). This is an explicit anti-long bias that *creates* the 30 s pile-up. → Replace the **duration-based** penalty with a **tightness/density** signal: penalize **trailing dead air / low speech-density / padding**, not raw seconds. A tight 70 s monologue scores full; a padded 30 s clip with dead air gets docked. Duration becomes **emergent** from content. (Dead-air/density is already computable — word-density per window + silence gaps from boundary-snap.)

**Result:** punchy moments settle ~15–25 s, monologues run ~45–90 s, *naturally* — the distribution reflects content with no forced skew either way. This satisfies "don't deliberately prevent 30 s skewing" because nothing pushes *toward* long; the 30 s-*causing* mechanisms are simply removed and scoring is made length-neutral.

## Sequencing / dependencies
- #2 (embeddings) and parts of #1 reuse the **sentence-transformers** stack (now reliable post-BUG 62).
- #4's tightness signal reuses the **silence/word-density** signals already computed for boundary-snap.
- #3 reuses **`stitch_render.py`**.
- Suggested order: **#4** (highest user-visible value, removes the skew) → **#3** (makes arc detection pay off) → **#2** (recall quality) → **#1** (measure-then-maybe).

## Related
- [[concepts/detection-walkthrough]] — the limitations these address
- [[concepts/clip-duration]] — #4 detail (length_penalty + default fallback)
- [[concepts/highlight-detection]] — Pass A (#2), Pass C (#4)
- [[concepts/segment-detection]] — #1
- [[concepts/two-stage-passb]] / [[concepts/callback-detection]] — #3 (A1/M3 setup metadata)
- [[entities/callback-module]] — sentence-transformers stack reused by #1/#2
