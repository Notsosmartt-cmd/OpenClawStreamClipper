# Clipping Intelligence — frozen snapshot (2026-06-04)

A point-in-time copy of every source/config file that holds the **prompt engineering
and heuristics** for the stream-clipping decision system. Frozen so future prompt/scoring
iterations can be diffed against this baseline.

- **Evaluation & full write-up:** `AIclippingPipelineVault/wiki/concepts/clipping-intelligence.md`
- **Captured at commit:** see `git log` around 2026-06-04 ("Evaluate clipping prompt engineering…").
- **Layout:** mirrors the repo (`scripts/lib/...`, `config/...`) so files drop back into place.

> These are COPIES. The live files remain in the repo and may have changed since.
> Nothing here is imported or executed — it is reference only.

## The decision system (pipeline order)

| Layer | File(s) here | Role |
|---|---|---|
| 1. Segment classification (router) | `scripts/lib/stages/stage3_segments.py` | 1-word LLM tag per 10-min window → routes all downstream weights/prompts |
| 2. Pass A — keyword scan (heuristic) | `scripts/lib/stages/stage4_moments.py` | substring categories + segment weights + universal/optional signals |
| 3. Pass B — per-chunk LLM | `scripts/lib/stages/stage4_moments.py`, `config/patterns.json`, `config/discourse_markers.json` | Pattern-Catalog prompt, prior-context + conversation-shape blocks, arcs (A1), summaries |
| 3b. Pass B+ — callbacks | `scripts/lib/callbacks.py` | embedding + FAISS retrieval gated by an LLM judge |
| 3c. conversation shape / audio / chat signals | `scripts/lib/conversation_shape.py`, `audio_events.py`, `chat_features.py` | optional boost-only structural signals |
| 4. Pass C — selection (heuristic) | `scripts/lib/stages/stage4_moments.py`, `scripts/lib/stages/stage4_5_snap.py` | multiplier chain + time-bucket distribution + boundary snap |
| 5. Pass D — rubric judge | `scripts/lib/stages/stage4_rubric.py`, `config/rubric.json`, `scripts/lib/stages/stage4_diversity.py` | 7-dim 0-10 rubric, 0.6/0.4 blend, MMR diversity |
| 6. Vision enrichment | `scripts/lib/stages/stage6_vision.py` | multimodal grounding/titles/hooks, non-gatekeeping boost |
| ×. Grounding cascade (cross-cutting) | `scripts/lib/grounding.py`, `config/grounding.json`, `config/denylist.json`, `scripts/lib/lmstudio.py` | regex + LLM faithfulness judge on every generated `why`/title |
| Style routing | `config/style_pattern_weights.json`, `config/streamer_prompts.json`, `config/boundaries.json`, `config/self_consistency.json` | style→pattern weights, Whisper slang biasing, boundary/SC config |

## Files (22)

### Source — stages (`scripts/lib/stages/`)
- `stage3_segments.py` — Layer 1 segment classification
- `stage4_moments.py` — Pass A + Pass B + Pass B-global (A1) + Pass C (the core; ~99 KB)
- `stage4_rubric.py` — Pass D rubric judge
- `stage4_diversity.py` — Pass D MMR diversity re-rank
- `stage4_5_snap.py` — clip boundary snap
- `stage6_vision.py` — Stage 6 vision prompt + grounding/regenerate-once

### Source — lib (`scripts/lib/`)
- `grounding.py` — 2-tier faithfulness cascade + LLM judge prompt
- `conversation_shape.py` — turn graph / discourse markers / off-screen intrusions
- `callbacks.py` — long-range setup→payoff detection
- `audio_events.py` — rhythmic / crowd / music boost signals
- `chat_features.py` — hard-event ground truth (subs/bits/raids/donations)
- `lmstudio.py` — minimal HTTP client used by the grounding judge
- `self_consistency.py` — N-candidate ranking helper

### Config (`config/`)
- `streamer_prompts.json` — per-channel Whisper `initial_prompt` slang biasing
- `patterns.json` — **Pattern Catalog** (10 named interaction shapes)
- `rubric.json` — Pass D dimension weights + Pass C/rubric blend
- `style_pattern_weights.json` — style → Pattern Catalog boosts/demotes
- `grounding.json` — cascade tiers, judge model, thresholds
- `denylist.json` — grounding regex denylist
- `discourse_markers.json` — conversation-shape marker patterns
- `boundaries.json` — clip boundary-snap config
- `self_consistency.json` — self-consistency config
