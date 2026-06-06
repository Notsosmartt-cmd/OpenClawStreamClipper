---
title: "Detection improvements — detailed implementation plans"
type: concept
tags: [plan, implementation, stage-3, stage-4, embeddings, stitch, length-penalty, boundary-inference, segment-detection, research]
sources: 1
updated: 2026-06-06
---

# Detection improvements — detailed implementation plans

Concrete, file:line-anchored plans for the four fixes designed in [[concepts/detection-improvements]]. **Plans only — not yet implemented.** Grounded by code-mapping passes 2026-06-06. Suggested order: **Fix 4 → Fix 3 → Fix 2 → Fix 1**.

---

## Fix 4 — Length-neutral duration (remove the ~30 s skew without an anti-30 s bias)

**Goal:** clip length follows content (punchy ~15–25 s, monologues ~45–90 s) by removing the two artificial pulls — the per-category default and the duration penalty — and replacing them with *content-aware* equivalents. Both changes are in one file (`stage4_moments.py`) and reuse existing signals; no new infra.

### Change (a) — content-aware boundary inference (replaces `DEFAULT_DURATIONS`)
- **Site:** `stage4_moments.py:2379-2396` (the `else` branch that today sets a per-category fixed duration + symmetric `±dur//2` boundaries for boundary-less moments — every keyword moment + any LLM moment whose span was discarded).
- **New behavior:** for a boundary-less moment at `timestamp`, infer real boundaries from the transcript:
  1. Seed a window around the peak (e.g. `[ts-8, ts+22]`).
  2. Snap start→nearest word-start and end→nearest word-end via `boundary_detect.snap_to_word_boundary()` (`boundary_detect.py:119-171`).
  3. Trim trailing/leading dead air using `boundary_detect.detect_silence_gaps()` (`boundary_detect.py:179-191`) — pull `clip_end` back to the last word before a ≥0.4 s gap, so the clip ends on the payoff not on silence.
  4. Bound to [15, max] (90, or 150 for storytime/emotional — match the parser at `:968`).
- **Net:** boundary-less moments get a *content-shaped* window instead of a flat 30 s, so the 30 s pile-up at the source disappears.

### Change (b) — tightness/dead-air signal (replaces `length_penalty`)
- **Site:** `length_penalty()` `stage4_moments.py:2363-2377`, applied at `:2557` (`lp = length_penalty(m["clip_duration"])` → `final_score = styled_score * lp`).
- **New behavior:** compute a **tightness** multiplier from *content density*, not seconds:
  - `wps = _wps_in(segments, m["clip_start"], m["clip_end"])` (reuse `baseline_contrast.py:88-101` — overlap-weighted words/sec over any window; lift the 13-line helper to `stage4_moments.py` or import it).
  - `dead_air_frac` = Σ(silence gaps ≥0.4 s inside the window) / duration, from `detect_silence_gaps()` on the clip's word timeline.
  - `tightness = clamp(map(wps into the 1.0–3.0 wps "engaged" band) × (1 − dead_air_frac), 0.65, 1.0)`. A tight 70 s monologue → ~1.0; a padded 30 s clip with dead air → ~0.7. **Duration is not an input.**
  - Keep a small **floor exemption** for `emotional`/`storytime` (legitimately slower, lower wps) so a heartfelt pause isn't over-penalized.
- **Net:** length stops being penalized per se; only *padding* is. Long-but-dense clips now survive Pass C.

### Config / safety / verify
- `CLIP_LENGTH_NEUTRAL` env (default **on**); when off, fall back to the current `length_penalty` + `DEFAULT_DURATIONS` (keep both functions). Tightness band + dead-air threshold as `selection_axes.json` knobs.
- **Risk:** changes selection → validate. The slow-emotional case is the main risk (mitigated by the category floor). The boundary-snap revert at `boundary_detect.py:304-314` caps at 90 s — align its `max_sec` with the 150 s storytime allowance or it'll revert long storytimes.
- **Verify:** run a VOD; compare the duration histogram + which clips win, before/after; confirm a known monologue now survives at its natural length and a padded clip drops. Watch `logtool selection`.
- **Effort:** ~40–60 LOC, one file (+ a helper lift). Medium risk (selection).

---

## Fix 3 — Stitched setup→payoff clips (make A1/M3 detection pay off in the render)

**Goal:** render arc/callback moments as a **2-segment clip** — a short setup snippet jump-cut to the payoff — so the irony/contradiction lands visually. Reuses the existing stitch machinery.

### Changes
1. **New builder** in `scripts/lib/moment_groups.py` — `build_arc_stitch_groups(moments)`: for each moment with `m.get("setup_time")` and `primary_category in {"arc","callback"}`, emit a `kind == "stitch"` group with **two members** (mirror the schema at `moment_groups.py:149-168`):
   - **setup member:** `{"timestamp": m["timestamp"], "start": setup_time - 2, "duration": 6-10, "role": "setup", "hook": "Earlier: " + (setup_text or why-derived)}`.
   - **payoff member:** `{"timestamp": m["timestamp"], "start": m["clip_start"], "duration": clip_duration, "role": "payoff", "hook": m["hook"]}`.
   - Both members reuse the payoff `timestamp` as the `moment_by_ts` key (the renderer prefers `member["start"]` over `moment.clip_start`, `stitch_render.py:116-118`, so overriding `start` per member works without a setup pseudo-moment).
   - Merge into `groups` at `moment_groups.py:217`.
2. **Renderer:** no change needed — `stitch_render.py` already renders ≥2 members with xfade + hook (`render_group` requires ≥2, `:273-275`). Use a hard cut or quick `fade` between setup and payoff. *(Gap: `apply_overlays` burns only the hook, not subtitles — add an SRT burn there if captions are wanted on arc stitches.)*
3. **Gate + wiring:** `CLIP_ARC_STITCH = _bool_env("CLIP_ARC_STITCH", False)` in `run_pipeline.py:78-91`; thread `--arc-stitch` into `moment_groups.py` from `stage4.py:46-55`; the Stage 7e dispatch (`stage7.py:496-509`) already fires when groups exist. Solo double-render is auto-avoided (moment_groups stamps `group_kind="stitch"` → `stage7.py:151` skips it).

### Phasing / data / verify
- **Phase 1 — callbacks first:** M3 callbacks carry **both** `setup_time` AND `setup_text` (`callbacks.py:349-351`) → the setup snippet + caption are well-defined. Lowest risk.
- **Phase 2 — arcs:** A1 arcs carry `setup_time` but **not** `setup_text` (`stage4_moments.py:2160-2175`) — derive the setup caption from `why`, or add `setup_text` to the arc dict (cheap: A1 already has the register quote).
- **Risk:** disjointed clips if the setup snippet is poorly bounded (snap it to word/silence edges like Fix 4); setup must be self-contained; extra render cost (2 ffmpeg cuts + concat per arc). All gated behind the flag.
- **Verify:** enable on a VOD with a known arc (the rakai irony arc T=11224); eyeball that the setup→payoff cut reads correctly and the caption matches.
- **Effort:** ~60–90 LOC (one new builder + flag wiring). Medium; the renderer is reused.

---

## Fix 2 — Embedding-similarity category signal for Pass A (modernize the crude keyword term)

**Goal:** make Pass A semantic without losing its deterministic recall-net role — augment the literal keyword count with embedding similarity to per-category prototypes. Keep keywords + shape/audio/speaker signals.

### Changes
1. **Prototypes:** lift the 8 one-line category descriptions at `stage4_moments.py:1657-1665` to a module constant `CATEGORY_DESCRIPTIONS` (1:1 with the 8 `KEYWORD_SETS` keys). Embed them **once** at module load.
2. **Reuse the embed stack:** `callbacks.embed_segments()` (`callbacks.py:92-122`) — model `sentence-transformers/all-MiniLM-L6-v2`, batched, `normalize_embeddings=True`, `cache_dir`-aware; returns `(emb, np)`. (sentence-transformers now reliable in every stage subprocess post-BUG 62 / `sitecustomize.py`.)
3. **Pre-embed windows:** before the `keyword_scan` `while` loop (`stage4_moments.py:427`), batch-embed every window's `combined` text in one pass (don't call `encode` per window). Index by window.
4. **Inject** right after the keyword loop (`:464`, before the universal-signals block): for each category, `sim = cos(window_emb, proto_emb[cat])`; if `sim >= EMBED_THRESHOLD` (~0.35), add a capped, weight-respecting term: `categories_found[cat] += min(sim * EMBED_WEIGHT * weights.get(cat,1.0), CAP)` and `total_signals += …`. No downstream changes — the emit path (`:604-626`) is generic.

### Config / safety / verify
- `CLIP_PASSA_EMBED` env (default **on**), `EMBED_THRESHOLD`, `EMBED_WEIGHT`, `EMBED_CAP`. Failure-soft: if sentence-transformers is unavailable, skip (keywords still run) — exactly like M3.
- **Risk:** over-firing (everything looks a little "hype") → the threshold + per-category cap + keeping it *additive/boost-only* contain it. Determinism preserved (embeddings are deterministic). Cost: one model load (already loaded for M3) + one batched encode of N windows (cheap — M3 already embeds 744 windows/VOD).
- **Verify:** diff `keyword_moments.json` with/without the flag; confirm recall on known keyword-less moments (the Delaware freestyle archetype) rises without flooding low-value windows.
- **Effort:** ~30–50 LOC, one file (+ prototype constant). Low–medium.

---

## Fix 1 — Finer segment granularity (measure-first; likely low priority)

**Goal:** reduce the 10-min labeling coarseness so short off-type pockets (a 2-min debate in a gaming stream) get their own segment type for chunk-sizing + Pass A thresholds. **Caveat: moment detection is already type-agnostic** (Pass B/A1/M3 read transcript, not labels), so a pocket's *clips are not lost today* — this only sharpens chunk-size + threshold plumbing.

### Plan
- **Phase 0 — instrument & decide:** add a diagnostic that counts how often a selected moment's LLM category disagrees with its segment label, and how many moments fall in "minority" sub-windows. If it's rare/no clips lost, **stop here** (don't over-engineer).
- **Phase 1 (if warranted) — finer overlapping windows:** in `stage3_segments.py`, drop `SEGMENT_CHUNK` 600→**300 s** and add a small overlap to the classification windows (the loop at `:53-163`); keep the adjacent-merge (`:166-171`) so stable regions still collapse. Cost: ~2× classification LLM calls (each is one small/fast call).
- **Phase 2 (optional) — variance-triggered refinement:** only re-classify a 600 s window when its content looks mixed (e.g. high category disagreement across its sub-windows), leaving the coarse pass elsewhere — cheaper than blanket 300 s.
- **Risk:** low (more, smaller labels; merge keeps it stable). **Verify:** segment map matches eyeballed transitions on a known variety VOD. **Effort:** small (Phase 1 is a constant + overlap); but gated behind Phase 0 measurement.

---

## Cross-cutting notes
- **Shared dependencies:** Fix 2 + parts of Fix 1 use the sentence-transformers stack (reliable post-`sitecustomize.py`); Fix 4 + the setup-snipping in Fix 3 reuse `boundary_detect.py` (`snap_to_word_boundary` / `detect_silence_gaps`) + `_wps_in`; Fix 3 reuses `stitch_render.py`.
- **All four are flag-gated and failure-soft** — each can ship dark and be validated on one VOD before becoming default.
- **Validation harness:** `logtool selection` (Pass C trace), `logtool axes`, and the per-run diagnostics already capture what's needed to A/B each change.

## Related
- [[concepts/detection-improvements]] — the design rationale (the "why")
- [[concepts/detection-walkthrough]] — Stage 3/4 walkthrough
- [[concepts/clip-duration]] — Fix 4 background
- [[concepts/highlight-detection]] · [[concepts/segment-detection]] · [[concepts/callback-detection]] · [[concepts/two-stage-passb]]
