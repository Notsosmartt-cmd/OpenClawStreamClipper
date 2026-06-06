---
title: "Clip duration & chunk windowing (how clip length is decided)"
type: concept
tags: [clip-duration, chunking, pass-b, stage-4, stage-7, boundary-snap, length-penalty, cross-chunk, segment-aware, text]
sources: 1
updated: 2026-06-06
---

# Clip duration & chunk windowing

How each clip's length is actually decided, why outputs cluster near ~30 s, the per-chunk window the model sees, and what cross-chunk inference can and can't do. Mapped 2026-06-06 from `scripts/lib/stages/stage4_moments.py`, `scripts/lib/callbacks.py`, `scripts/lib/boundary_detect.py`, `scripts/pipeline/stages/stage7.py`.

> [!note] One-line answer
> **There is no hard 30 s clamp.** Clip length is content-driven — the Pass B LLM picks `start_time`/`end_time` (trusted within **[15 s, 90 s]**, or **[15 s, 150 s]** for `storytime`/`emotional`). The ~30 s skew is *emergent* from two things: (a) a per-category **default fallback** for moments that arrive without LLM boundaries, and (b) a score-side **`length_penalty`** that makes short clips win Pass C selection.

## How each clip's duration is set (by source)

| Source | Where boundaries come from | Net duration |
|---|---|---|
| **Pass B (LLM)** | Model returns absolute `start_time`/`end_time`, **trusted as-is**, clamped to [15, 90] (or [15, 150] for storytime/emotional). `<15 s` → discarded (falls to default); `>max` → truncated to max, re-centered on peak. (`parse_llm_moments`, `stage4_moments.py:963-977`) | content-driven 15–90 s+ |
| **Pass A (keyword)** | **No window at creation** (`:614-626`) → per-category `DEFAULT_DURATIONS` in Pass C (`:2379-2396`): reactive/dancing 25, funny/hype 30, hot_take/controversial 35, emotional 40, storytime 45 | the category default |
| **Arc (A1)** | Fixed **35 s** around payoff (−12/+23) (`:2154-2169`) | ~35 s |
| **Callback (M3)** | Judge LLM picks, else **~45 s** (−10/+35), clamped [15,150] (`callbacks.py:323-332`) | ~45 s or judge-driven |

When a keyword moment merges with an LLM moment (within 25 s during dedup), **the LLM's content-driven boundaries win** (`:2309-2313`). Then **boundary-snap** (Phase 4.2, `boundary_detect.py`, on by default) nudges start→nearest word/silence (≤3 s) and end→nearest word/silence (≤8 s forward); if the snapped span leaves [15, 90] it **reverts** rather than truncates (`boundary_detect.py:304-314`). Stage 6 copies the window through unchanged; **Stage 7 renders exactly the stored duration — no re-clamp, no re-derivation** (`stage7.py:102-103,138-139`).

## Why the output skews ~30 s (two emergent causes, not a clamp)

1. **Per-category default fallback (~30 s).** Any moment reaching Pass C *without* explicit LLM boundaries — every keyword-only moment, plus LLM moments where the model emitted no usable start/end — gets the category default (`funny`/`hype` = 30). Fast streams fire lots of keyword moments → most inherit this.
2. **`length_penalty` biases *selection* toward short clips** (`stage4_moments.py:2366-2377`, applied to the score at `:2557`): `≤30 s ×1.0, ≤45 s ×0.95, ≤60 s ×0.85, ≤75 s ×0.75, >75 s ×0.65`. It shortens nothing — it makes long clips **less likely to win** a limited per-bucket slot (a 70 s monologue must out-score a 28 s clip by ~33 % just to tie). So the *surviving set* skews short.

Plus the clip-count cap (`MAX_CLIPS` ≈ 2–4/hour) + duration-aware spacing favor *more short* clips over *fewer long* ones.

## Chunk windowing (what the model sees per Pass B call)

Per **segment type** (`CHUNK_DURATION_BY_SEGMENT`, `stage4_moments.py:1097-1112`):

| Segment type | Chunk | Overlap (each side) |
|---|---|---|
| `just_chatting`, `irl` | **480 s (8 min)** | 60 s |
| `debate` | 360 s (6 min) | 45 s |
| `reaction`, `gaming` | 300 s (5 min) | 30 s |
| (fallback) | 300 s | 30 s |

≈ 600–1,600 words (~800–2,200 tokens) of transcript per chunk — a small fraction of context. `chunk_start` advances by the **full** duration (`:1843`) but each chunk's transcript slice is widened by ±overlap (`:1374-1375`), so **consecutive chunks share their boundary content**. The window is chosen by peeking at the segment type 150 s in (`_chunk_window_for`, `:1114-1125`). Sizes were set expressly so **storytimes/arguments (4–8 min) fit whole in one chunk** (Tier-1 Q4) — a longer chunk improves the model's boundary inference for long moments.

## Cross-chunk inference — what works, what doesn't

**The model DOES understand cross-chunk relevance**, three ways: (a) the 30–60 s **overlap** means a boundary moment is *seen* in both chunks; (b) each prompt is fed the **last 2 chunks' summaries** ("Earlier in this stream… look for setup→payoff", `:1548-1569`); (c) the **A1 global pass** links setup→payoff anywhere via an arc-bait register of all chunk cards, and **M3 callbacks** do embedding search ≥5 min back.

**But a single clip cannot physically straddle a chunk boundary.** `parse_llm_moments` clamps every moment + clip to its **nominal** `[chunk_start, chunk_end]` (`:912-913, 965-966`) — the parser is even called with the *nominal* bounds, not the overlap-extended ones (`:1672`). So a moment on the seam (e.g. "7 s from chunk N + 25 s from chunk N+1") gets **truncated to one side** in each chunk's output: chunk N+1 starts at the boundary (loses the 7 s), chunk N ends at the boundary (loses the 25 s). The overlap guarantees the moment isn't *missed*, not that one clip spans the seam.

And the cross-chunk mechanisms that link far-apart setup→payoff (**A1, M3**) emit a **payoff-centered ~35–45 s clip** with the setup kept only as title/description metadata — never one long contiguous setup→payoff span.

## Does chunk length affect clip length / inference?

- It does **not** cap clip length: the 90/150 s clip cap binds first (chunks are ≥300 s).
- A **longer chunk improves boundary/duration inference** for long moments (the model sees the whole arc instead of a bisected half — the Q4 rationale for 8-min `just_chatting` chunks).
- The only hard chunk-imposed limit is that a *local* clip can't cross its nominal boundary (rarely the binding constraint).

## Levers to increase duration variety (not yet done)

1. **Soften `length_penalty`** (esp. `storytime`/`emotional`/`hot_take`) so strong 45–90 s monologues survive selection. Biggest lever.
2. **Push the prompt to always emit explicit start/end** (+ bias talky categories longer) so fewer moments fall to the flat 30 s default.
3. **Let clips extend into the overlap band** — pass the overlap-extended bounds to `parse_llm_moments` + rely on dedup — so a boundary-straddling moment is emitted whole instead of truncated.

## Key code references
- Pass B prompt duration guidance: `stage4_moments.py:1609-1616`
- Pass B parser clamp [15, 90/150]: `:963-977`
- Keyword moment (no window): `:614-626`
- Per-category `DEFAULT_DURATIONS` fallback: `:2379-2396`
- Dedup (LLM boundaries win): `:2309-2313`
- `length_penalty`: `:2366-2377`, applied `:2557`
- Chunk durations + overlap: `:1097-1125`; loop `:1367-1379,1843`
- Prior-context block: `:1548-1569`; A1 global pass `:1972-2200`; arc window `:2154-2169`
- M3 callback window: `callbacks.py:323-332`
- Boundary-snap revert [15,90]: `boundary_detect.py:304-314`
- Stage 7 renders as-is: `stage7.py:102-103,138-139`

## Related
- [[concepts/highlight-detection]] — Pass A/B/C (where duration is set)
- [[concepts/two-stage-passb]] / [[concepts/arc-aware-extraction]] — A1 cross-chunk pass
- [[concepts/callback-detection]] — M3 cross-chunk callbacks
- [[concepts/boundary-snap]] — Phase 4.2 sentence/silence snap
- [[concepts/clip-rendering]] — Stage 7 (consumes the duration)
- [[concepts/segment-detection]] — segment types drive chunk sizing
- [[concepts/open-questions]] — "variable clip length" open item
