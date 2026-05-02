---
title: "Tier-4 — Conversational Shape Detection + Rubric Judge"
type: concept
tags: [moment-discovery, upgrade-plan, tier-4, shipped, pass-d, pattern-catalog, conversation-shape, rubric, llm-judge, multimodal, hub]
sources: 1
updated: 2026-05-01
---

> [!note] Tier-4 SHIPPED 2026-05-01 (all 8 phases)
> Phases 4.1–4.8 landed in a single session. Verification: `bash -n` clean on `clip-pipeline.sh` + `stages/stage4_moments.sh`; AST parse clean on the 6 new/modified Python modules; all 5 new/modified JSON configs parse; smoke test confirms `conversation_shape.analyze_chunk` correctly tags the Lacy-penthouse signature (claim_stake → off_screen_intrusion → concession in the same chunk) and `eval_tier4.evaluate` produces correct precision/recall.
>
> Per-phase wire-in (file paths after the 2026-05-01 modularization):
> - **4.1 Model profiles** — `config/models.json` `profiles` block; `dashboard/routes/models_routes.py` `/api/models/profile` PUT; `dashboard/static/modules/models-panel.js` profile bar; `dashboard/static/app.js` window-export.
> - **4.2 Conversation analytics** — `scripts/lib/conversation_shape.py` (new); `config/discourse_markers.json` (new); wired into `scripts/lib/stages/stage4_moments.py` Pass A boost-only signals + per-chunk `convo_shape_block` injected into Pass B prompt.
> - **4.3 Pattern Catalog** — `config/patterns.json` (new); Pass B prompt rewritten to evaluate against named catalog patterns; `primary_pattern` + `secondary_patterns` fields propagated through Pass C.
> - **4.4 Pass D rubric judge** — `scripts/lib/stages/stage4_rubric.py` (new); `config/rubric.json` (new); wired into `scripts/stages/stage4_moments.sh` after Pass C, before Phase 4.2 boundary snap.
> - **4.5 Vision-as-shape-detector** — `scripts/lib/stages/stage6_vision.py` prompt extension + `interaction_shape` / `pattern_match` / `pattern_match_strength` / `gaze_direction` fields; cross-validation against Pass B + Pass D patterns produces `cross_validated_full` + score `+0.1`.
> - **4.6 MMR diversity** — `scripts/lib/stages/stage4_diversity.py` (new); reuses `sentence-transformers/all-MiniLM-L6-v2`; lambda configurable in `config/rubric.json::mmr_lambda`.
> - **4.7 Style presets** — `config/style_pattern_weights.json` (new); 5 new styles (`conversational`, `informational`, `freestyle`, `chatlive`, `spicy`); Discord agent `workspace/AGENTS.md` updated with aliases.
> - **4.8 Eval runner** — `scripts/lib/eval_tier4.py` (new); precision/recall vs. user reference labels with per-pattern breakdown.
>
> Stray bug fix bundled in: `stages/stage4_moments.sh` had a broken `\n` line continuation in the python3 invocation (would have made bash exec a non-existent `n` command). Replaced with a single-line invocation while wiring in Pass D + MMR.

# Tier-4 — Conversational Shape Detection + Rubric Judge

A continuation of the moment-discovery work tracked in [[concepts/moment-discovery-upgrades]] (Tier 1/2/3 — Q1–Q5, M1–M3, A1–A3). Tier-4 shifts detection from **lexical** (Pass A keywords + Pass B reading transcript text) to **structural+semantic** (turn graphs, discourse moves, named interaction patterns), and adds an **LLM-as-judge scoring phase** that uses the same multimodal model as Pass B and Stage 6.

Targets seven nuanced clip classes the current pipeline catches inconsistently: streamer reading-and-reacting-to-chat, controversial moments, storytelling arcs, informational rambles (financial / news / backstory / motivational / social-dynamics analysis), Lacy-penthouse-class self-claim contradictions, rap battles / freestyles, and challenge-and-fold conversation patterns (the Neon/6ix9ine archetype).

> [!note] Architectural framing
> One model, multiple phases. The same Qwen 3.5 9B / 35B-A3B / Gemma 4 26B-A4B handles Stage 3 classification, Pass B chunk analysis, **Pass D rubric judging (new)**, and Stage 6 vision enrichment. Loaded once into [[entities/lm-studio]], never unloaded between phases (only between Whisper transitions). The model is fully swappable via `config/models.json` — no hard-coded default.

Locks in the modular layout from 2026-05-01 — stage bodies live in `scripts/stages/stage{1..8}.sh`, embedded Python in `scripts/lib/stages/*.py`. All file references below target those locations, not the (now thin) `clip-pipeline.sh` orchestrator.

---

## Findings

### Conversational analytics (literature inputs)

Four cheap-to-extract structural signals that complement what an LLM reads from raw transcripts:

| Signal | Source | What it captures |
|---|---|---|
| **Turn-taking graph** | Sacks/Schegloff/Jefferson 1974; modern dialog-systems work | who-spoke-when, interruptions, floor management. Off-screen voice intrusion is the structural fingerprint of the Lacy-penthouse pattern. |
| **Dialog acts** | DAMSL / SwDA tagging tradition (Stolcke et al.) | per-utterance category — statement-opinion, question, agreement, disagreement, backchannel, command, deflection. Adjacency pairs like `claim → counter-claim → concede` are *exactly* challenge-and-fold. |
| **Topic segmentation** | TextTiling (Hearst 1997) + modern BERT variants | finds boundaries where topic shifts. Coherent informational ramble = long stretch with low boundary density; unexpected topic shift = high-boundary spike. |
| **Discourse markers** | Schiffrin 1987 | "look", "the thing is", "let me tell you", "wait wait wait", "actually", "so check this out" — structural flags for setup-payoff that don't depend on emotional content. |

All four are stdlib-extractable using light regex + the existing [[entities/diarization]] M1 output. None require new models.

### Highlight-detection literature (research inputs)

Three patterns from the academic work that map directly onto pipeline upgrades:

1. **Query-conditioned moment retrieval** — QVHighlights / Moment-DETR / CG-DETR / SG-DETR. The most successful highlight detectors take a natural-language query and return ranked time intervals. Today the `--style` flag is a soft hint that gets *interpreted* by the keyword scorer; making it the literal retrieval prompt for the LLM lets the model do the matching directly.
2. **Saliency curve smoothing + diversity-aware ranking** — TVSum (Song et al. 2015), YouTube-Highlights. Top-K selection that's purely score-greedy clusters around 1-2 strong moments. Literature standard is **DPP** or simpler **MMR (Maximal Marginal Relevance)** — pick next clip maximizing `score − λ × similarity_to_already_picked`. Today's Pass C bucket distribution is a coarse approximation; a proper similarity-aware ranker over Pass B `why` embeddings catches near-duplicates more reliably.
3. **Sub-score aggregation > single-shot scoring** — LLM-as-judge research (G-Eval, MT-Bench, Prometheus). Asking an LLM "rate this 1-10" is noisier than asking "rate it 1-10 on each of [setup, payoff, originality, broad_appeal, replay_value]" and aggregating. The aggregated score is also auditable — when a clip ranks high you can name *why*.

### Multimodal model capabilities (Gemma 4 + Qwen 3.5)

Conservatively stated, what matters for this plan:

- **Multi-image input**: both handle the 6-frame (or 8 with [[concepts/two-stage-passb]] A2 setup) Stage 6 payload without quality dropoff. Documented limits are higher (Gemma 4 ≥32 images, Qwen 3.5 ≥16) but the payoff window doesn't need them.
- **Structured JSON output**: both reliably honor `response_format={"type": "json_object"}` in LM Studio; both return nested objects (rubric sub-scores) without breaking. Gemma 4 occasionally over-generates with thinking-mode reasoning — the existing `reasoning_content` fallback in `call_llm()` already handles that.
- **Long context**: 32K minimum on Qwen 3.5 9B, 128K on the others. The Pass B chunk + Pattern Catalog + speaker block + few-shots fits comfortably under 8K; context isn't the bottleneck.
- **Function-calling / tool use**: supported on both, *unused* here. Tool-calling adds latency and failure modes; nothing in Tier-4 needs it.
- **Same model across phases is free**: LM Studio keeps the model resident. Phase transitions are just new HTTP requests. Adding "another phase" costs one chat completion (~5-15s on 9B), not a VRAM swap.

---

## Phase overview

| # | Title | Position | Risk | Hours |
|---|---|---|---|---|
| [4.1](#phase-41---multi-phase-model-swappable-architecture) | Multi-phase, model-swappable architecture | cross-cutting (config + dashboard) | trivial | 1 |
| [4.2](#phase-42---conversational-analytics-layer) | Conversational analytics layer | pre–Pass B input | low | 4 |
| [4.3](#phase-43---pattern-catalog--pass-b-prompt-rewrite) | Pattern Catalog + Pass B prompt rewrite | Pass B | medium | 6 |
| [4.4](#phase-44---pass-d-structured-rubric-judge-new-phase) | Pass D — structured rubric judge **(NEW phase)** | between Pass C and Phase 4.2 boundary snap | medium | 5 |
| [4.5](#phase-45---vision-as-shape-detector-stage-6-prompt-extension) | Vision-as-shape-detector | Stage 6 | low | 1 |
| [4.6](#phase-46---diversity-aware-ranking-mmr) | Diversity-aware ranking (MMR) | post Pass D, pre Stage 5 | medium | 3 |
| [4.7](#phase-47---style-preset-extension) | Style preset extension | cross-cutting (Discord agent + config) | low | 2 |
| [4.8](#phase-48---eval-and-validation) | Eval and validation | post-ship | medium | 4 |
| 4.9 | Wiki updates (mandatory per `CLAUDE.md`) | post each phase | trivial | 2 |

**Why this order:** 4.2 must land before 4.3 (Pattern Catalog references conversation-shape signals). 4.5 should land before 4.4 so cross-validation has the third channel. 4.6 needs 4.4's `final_score` to operate on. 4.8 is the gate for declaring Tier-4 done.

---

## Phase 4.1 — Multi-phase, model-swappable architecture

**Goal:** make the unified-model path the documented happy path while preserving full swappability for future models.

**Architecture:** add a `models_profile` block to `config/models.json` with named presets. No enforced default — the dashboard ships with `active_profile` set to whichever the user picks, and switching profiles flips both `text_model` and `vision_model` atomically.

```json
{
  "active_profile": "qwen35-9b",
  "profiles": {
    "qwen35-9b":   { "text": "qwen/qwen3.5-9b",        "vision": "qwen/qwen3.5-9b" },
    "qwen35-35b":  { "text": "qwen/qwen3.5-35b-a3b",   "vision": "qwen/qwen3.5-35b-a3b" },
    "gemma4-26b":  { "text": "google/gemma-4-26b-a4b", "vision": "google/gemma-4-26b-a4b" }
  }
}
```

**Files touched:**
- `config/models.json` — schema extension, backwards-compatible (existing free-text fields still work as overrides)
- `dashboard/routes/models.py` (or whichever dashboard route handles model state post-modularization) — read/write `active_profile`
- `dashboard/static/modules/models-panel.js` — profile dropdown alongside existing inputs
- `dashboard/templates/index.html` — dropdown HTML
- `entities/model-profile.md` (new) — documents the contract
- Updates to `entities/qwen35.md`, `entities/lm-studio.md` to point at it

**Steps:**
1. Add `models_profile` schema to `config/models.json`. Phase 5.1 split overrides (`text_model_passb` / `vision_model_stage6`) and the `CLIP_TEXT_MODEL` / `CLIP_VISION_MODEL` env vars stay — they win over the profile.
2. Dashboard Models panel: append profile dropdown; handler dispatches to existing `/api/models` PUT endpoint with both `text_model` and `vision_model` fields.
3. Pipeline reads `active_profile` only as a *hint* — the actual values come from `text_model` / `vision_model` (already populated by the dashboard from the chosen profile).
4. Document the contract in `entities/model-profile.md`; update related entity pages.

**Why no default model:** the user explicitly wants swap-ability for future models. The dashboard ships with `active_profile = qwen35-9b` because it's the most stable baseline (no thinking-token tax, broadest VRAM compatibility), but flipping presets is a one-click operation. Future models slot in via config — no code change.

**Risks:** none meaningful. Backwards-compatible.

**Verification:** dashboard switches profiles, Pipeline run uses the switched model, env-var override still wins when set.

---

## Phase 4.2 — Conversational analytics layer

**Goal:** extract structural conversation signals before Pass B reads the transcript, so the LLM sees the *shape* of the chunk before the words.

**New module:** `scripts/lib/conversation_shape.py` (stdlib + reuse of existing diarization output).

**What it computes per Pass B chunk:**

```python
{
  "speakers": [
    {"id": "SPEAKER_00", "share": 0.61, "longest_run_s": 22.4},
    {"id": "SPEAKER_01", "share": 0.32, "longest_run_s": 8.1},
    {"id": "SPEAKER_02", "share": 0.07, "longest_run_s": 1.8}
  ],
  "turn_changes": 17,
  "interruptions": 2,
  "off_screen_intrusions": [
    {"t": 14.3, "from_speaker": "SPEAKER_00", "to_speaker": "SPEAKER_02"}
  ],
  "topic_boundaries": [{"t": 41.2, "delta": 0.78}],
  "discourse_markers": [
    {"t": 12.1, "speaker": "SPEAKER_00", "marker": "let me tell you", "class": "story_opener"},
    {"t": 38.4, "speaker": "SPEAKER_01", "marker": "wait wait wait",  "class": "pushback"}
  ],
  "monologue_runs": [
    {"speaker": "SPEAKER_00", "start": 8.1, "end": 32.5, "duration_s": 24.4, "word_count": 84}
  ]
}
```

**Sub-components:**

| Component | What it does | Cost |
|---|---|---|
| **Turn graph builder** | reads `transcript.json` segments with `speaker` field ([[entities/diarization]] M1) → speaker share + run + change-count summary | ~80 lines stdlib |
| **Off-screen-intrusion detector** | flags when a *new* speaker first appears in a chunk previously single-speaker for ≥30s. The Lacy-penthouse signal. | ~30 lines stdlib |
| **Discourse-marker scanner** | regex over transcript text mapped to classes (`story_opener`, `claim_stake`, `pushback`, `topic_pivot`, `info_ramble_marker`, `agreement`, `concession`). Lexicon ships in `config/discourse_markers.json`. | ~50 lines stdlib |
| **Topic-boundary detector** | TextTiling-style cosine drop between adjacent 60s bag-of-words windows. No model. | ~50 lines stdlib |
| **Monologue-run extractor** | contiguous runs where one speaker holds ≥80% of the floor for ≥20s. The "informational ramble" signal. | ~40 lines stdlib |
| **Interruption detector** | speaker-A end-time > speaker-B start-time (overlap) using WhisperX word-level timestamps | ~30 lines stdlib |

**Wire-in:** `stages/stage4_moments.sh` calls `python3 -c "import conversation_shape; conversation_shape.run_for_chunks(...)"` once after Stage 3, writing `/tmp/clipper/conversation_shape.json` keyed by chunk index. Pass B reads it per chunk; Pass D and Stage 6 also have access.

**Pass A boost-only signals (additive, never gating):**
- `+0.5` per off-screen intrusion in window
- `+0.3` per `pushback` discourse marker in window
- `+0.5` per `story_opener` marker followed by ≥15s monologue
- `+0.3` per `claim_stake` marker if the chunk also has multi-speaker overlap

These are conservative caps — surface candidates to Pass B, don't score them.

**Why this works for the user's targets:**

| User target | Signal combination from this phase |
|---|---|
| Streamer reading chat | `monologue_run` + Stage 6 `gaze_direction = at-chat` (added in 4.5) |
| Controversial | `pushback` markers + multi-speaker overlap + topic_boundary |
| Storytelling | `story_opener` + sustained `monologue_run` ≥40s + payoff frame at end |
| Informational ramble | sustained `monologue_run` ≥60s with low topic_boundary density |
| Lacy penthouse | `claim_stake` + `off_screen_intrusion` within 30s |
| Rap battle | M2 audio `music_dominance` ≥0.6 + ≥2 speakers + rhyme density |
| Neon/6ix9ine | ≥3 turn changes + `pushback` + `concession` marker |

**Risks:**
- M1 diarization isn't always loaded (`HF_TOKEN` not set). Module handles gracefully — emits empty turn graph but still runs discourse-marker scan and topic boundaries.
- Discourse-marker regex is English-only initially. Future work: per-channel `discourse_markers.json` overlay.

**Verification:** `python3 scripts/lib/conversation_shape.py --chunk transcript.json --start 600 --end 900` produces a JSON dump matching the schema above. Run on a known Lacy-penthouse VOD; confirm the off-screen intrusion is flagged at the right timestamp.

---

## Phase 4.3 — Pattern Catalog + Pass B prompt rewrite

**Goal:** replace the current 6-pattern checklist + 3 few-shots with a closed taxonomy of named interaction shapes the LLM evaluates against.

**New file:** `config/patterns.json` (user-editable, hot-reloadable).

**Initial catalog (10 patterns):**

| Pattern ID | Signature | Category hint |
|---|---|---|
| `setup_external_contradiction` | First-person assertive claim → off-screen voice contradicts → claimant concedes | controversial |
| `challenge_and_fold` | Speaker A escalates → speaker B challenges → A backs down | controversial |
| `reading_chat_reaction` | Speaker pauses to read chat aloud → emotive response to content | reactive |
| `storytelling_arc` | "let me tell you about..." → buildup → punchline / twist reveal | storytime |
| `hot_take_pushback` | Controversial claim → pushback → speaker doubles down | hot_take |
| `informational_ramble` | Sustained monologue on coherent topic with concrete substance (financial / news / backstory / motivational / social-dynamics) | storytime |
| `interview_revelation` | Host probes → guest reveals something candid or self-implicating | storytime |
| `rap_battle_freestyle` | Rhythmic cadence + rhyme density over a beat; turn-taking between MCs | hype |
| `social_callout` | Speaker A claims → speaker B exposes inconsistency / roasts → social tension | controversial |
| `unexpected_topic_shift` | Abrupt pivot mid-flow that lands — audience reacts | funny |

**Schema** (one entry):

```json
{
  "id": "setup_external_contradiction",
  "label": "Setup → external contradiction",
  "signature": "First-person assertive claim followed by an off-screen voice or third party contradicting it; speaker concedes or adapts.",
  "structural_signals": ["claim_stake", "off_screen_intrusion", "concession"],
  "example": "Streamer claims a penthouse is theirs; off-screen voice says it isn't; streamer admits.",
  "category_hint": "controversial"
}
```

**Pass B prompt rewrite** (in `scripts/lib/stages/stage4_moments.py`): the existing prompt (numbered patterns + few-shots) is replaced with a structured pattern-matching prompt:

```
You are evaluating a stream chunk for clip-worthy moments.

PATTERN CATALOG (evaluate against these — not keywords):
[serialized config/patterns.json with id + label + signature + example]

CONVERSATION SHAPE (from speech analysis):
[serialized conversation_shape.json for THIS chunk]

PRIOR CHUNKS:
[Tier-1 Q1 prior context — unchanged]

TRANSCRIPT (timestamps MM:SS):
[chunk_text]

For each clip-worthy moment, return:
{
  "time": "MM:SS",
  "start_time": "MM:SS",
  "end_time": "MM:SS",
  "primary_pattern": "<pattern id from catalog>",
  "secondary_patterns": ["<id>", ...],
  "category": "<category_hint or override>",
  "why": "one sentence naming WHICH pattern signature is satisfied and HOW the transcript+shape evidence it"
}

Reject moments that don't satisfy any pattern's signature. Don't invent patterns.
```

**Files touched:**
- `config/patterns.json` (new)
- `scripts/lib/stages/stage4_moments.py` — prompt rewrite, JSON schema extension, `primary_pattern` + `secondary_patterns` fields propagated through Pass C
- `entities/pattern-catalog.md` (new wiki page documenting schema)

**Why this is better than today:**
- LLM grounds picks in named structures the user can audit
- Adding new patterns is a config edit, not a prompt rewrite
- `primary_pattern` becomes a first-class signal that Pass C and Pass D consume
- Removes the "Look beyond keywords" prose Pass B was already partially ignoring

**Risks:**
- Prompt-tuning churn: the catalog signatures may need 2-3 iterations after first eval. Keep them in `config/patterns.json` (hot-editable) so iteration doesn't require code changes.
- LLM may invent pattern IDs not in the catalog. Pass B parser validates against the catalog and drops invalid IDs (logs warning).
- Don't over-grow the catalog past ~12 patterns — the LLM needs to internalize them in one prompt; more patterns = vaguer matches.

**Verification:** AST parse of `stage4_moments.py` clean. Manual run on a Lacy-penthouse VOD; confirm Pass B tags the moment `setup_external_contradiction` and `why` cites the off-screen intrusion.

---

## Phase 4.4 — Pass D: structured rubric judge (NEW phase)

**Goal:** add a between-Pass-C-and-Stage-5 phase that re-scores every surviving candidate using a structured rubric on the same multimodal model. This is the LLM-as-judge layer.

**Why "Pass D" and not modifying Pass B:** Pass B is per-chunk batch detection — it has to scan widely. Pass D sees only the candidates that survived Pass C's bucket distribution and merge — typically 10-20 moments across the VOD. Dedicating a focused per-moment judgment call to each is cheap (10-20 chat completions) and lets the rubric be much richer than what Pass B's batch prompt can afford.

**Position in pipeline:**

```
Pass C selects N candidates → writes hype_moments.json
       ↓
Pass D rubric judge          (NEW)
       ↓
Phase 4.2 boundary snap → Stage 4.5 groups → Stage 5 frames
```

**Module:** `scripts/lib/stages/stage4_rubric.py`, called from `stages/stage4_moments.sh` after Pass C.

**Rubric returned per moment:**

```json
{
  "moment_id": "T=1234",
  "scores": {
    "setup_strength":    7,
    "payoff_strength":   8,
    "originality":       6,
    "broad_appeal":      7,
    "replay_value":      6,
    "audio_quality":     8,
    "self_contained":    9
  },
  "pattern_confirmed": "challenge_and_fold",
  "pattern_match_strength": 0.85,
  "rejection_flags": [],
  "audit_one_liner": "Host backs down after guest dares them to repeat the slur — clean fold pattern with clear payoff."
}
```

**Aggregation** into `rubric_score` between 0.0 and 1.0:
- `setup_strength × 0.15`
- `payoff_strength × 0.25`
- `originality × 0.20`
- `broad_appeal × 0.15`
- `replay_value × 0.10`
- `audio_quality × 0.05`
- `self_contained × 0.10`

Weights ship in `config/rubric.json`, user-editable.

**Score blending:** `final_score = 0.6 × pass_c_score + 0.4 × rubric_score`. Both clamped to `[0, 1]` for serialization but `raw_score` is preserved for ranking (post-BUG-37b/c convention; see [[concepts/bugs-and-fixes#BUG 37b]]).

**Rejection flags** (scoring 0 on `self_contained` or 0 on `audio_quality`) demote the moment to the bottom of the bucket — never *eliminate* (mirrors the non-gatekeeping principle from [[concepts/vision-enrichment]]). Pass C's selection cap still applies.

**Per-moment prompt** (sees transcript window + Pass B `why` + conversation shape + Pattern Catalog):

```
You are an editor scoring a clip candidate for replay value on a 0-10 rubric.

CANDIDATE:
- Time: 14:02 - 14:45
- Pattern claimed: setup_external_contradiction
- Why: <Pass B's reasoning>

TRANSCRIPT (verbatim, 90s window):
"""<text>"""

CONVERSATION SHAPE:
- Speakers in window: 2 (SPEAKER_00 80%, SPEAKER_01 20%)
- Off-screen intrusions: 1 at 14:28
- Discourse markers: claim_stake at 14:02, concession at 14:33

PATTERN SIGNATURE (for reference):
<signature text from catalog>

RATE 0-10 on each dimension. If any dimension scores 0, name a rejection_flag.
RETURN JSON: {scores: {...}, pattern_confirmed: "<id or null>", pattern_match_strength: 0.0-1.0, rejection_flags: [...], audit_one_liner: "<25 words max>"}
```

**Cost:** 10-20 LLM calls per VOD, each ~1-2k output tokens, ~5-15s wall time on Qwen 3.5 9B. Adds ~3 minutes to a 2-hour VOD's pipeline.

**Failure mode:** if Pass D times out or returns malformed JSON for a moment, that moment keeps its Pass C score unchanged. Failure-soft — it can never delete a candidate. Mirrors the BUG-32 fail-fast pattern: 3 consecutive network failures abort Pass D for the rest of the VOD.

**Files touched:**
- `scripts/lib/stages/stage4_rubric.py` (new)
- `stages/stage4_moments.sh` — add Pass D call after Pass C
- `config/rubric.json` (new, weights)
- `entities/rubric-judge-module.md` (new wiki page)

**Diagnostics:** `clips/.diagnostics/<vod>_diagnostics.json` gains a `rubric_scores` field per moment. Dashboard surfaces the `audit_one_liner` per clip.

**Risks:**
- Latency budget: 10-20 LLM calls add ~3 min to a 2-hour VOD. Acceptable for offline pipeline. Consider parallelizing 2-3 calls if it becomes a bottleneck.
- Rubric weight tuning is feel-based until [[#phase-48---eval-and-validation]] runs. Ship the weights documented above and revisit only after eval.

**Verification:** AST parse clean. Smoke test: Pass D on 5 known moments; verify rubric scores correlate with subjective quality.

---

## Phase 4.5 — Vision-as-shape-detector (Stage 6 prompt extension)

**Goal:** push the multimodal model from per-frame description to *interaction-shape recognition* in the visual channel — providing the third cross-validation channel.

**Existing Stage 6 output:** `{score, category, title, description, hook, mirror_safe, voiceover, callback_confirmed?, chrome_regions}`.

**New fields:**

| Field | Values |
|---|---|
| `interaction_shape` | `monologue` / `reading-chat` / `dialog-with-on-screen-guest` / `dialog-with-off-screen-voice` / `gameplay-with-commentary` / `silent-gameplay` / `multi-speaker-stage` |
| `pattern_match` | ID from `patterns.json` that the *frames* most strongly support |
| `pattern_match_strength` | 0.0-1.0 |
| `gaze_direction` | `at-camera` / `at-chat` / `at-screen` / `at-guest` / `off-screen` / `down` |

**Wire-in:** the cross-validation rule in Pass C/Pass D becomes:

```
moment is fully cross_validated when:
  Pass B primary_pattern == Pass D pattern_confirmed == Stage 6 pattern_match
  AND all three pattern_match_strengths >= 0.6
```

Today's `cross_validated` flag (Pass A keyword + Pass B LLM) stays as a separate weaker signal.

**Files touched:**
- `scripts/lib/stages/stage6_vision.py` — prompt extension + JSON schema
- `stages/stage4_moments.sh` — Pass D consumes Stage 6 output if available (it isn't yet at Pass D time — see ordering note below)
- `concepts/vision-enrichment.md` — document new fields

> [!note] Ordering subtlety
> Pass D runs BEFORE Stage 5/6 in the new pipeline order. So Pass D can't read Stage 6's `pattern_match` directly. The cross-validation runs in two places:
> 1. Pass D writes its own `pattern_confirmed` (text-only judgment).
> 2. Stage 6 writes its `pattern_match` (vision-only judgment).
> 3. Stage 7 manifest-build step compares all three and stamps `cross_validated_full` on moments where all three agree. This bumps the moment's score by `+0.1` (capped at 1.0) before Stage 7 manifest sort.

**Cost:** zero additional model calls — same Stage 6 prompt, four more fields in the JSON schema. ~50 more output tokens per moment.

**Risks:**
- VLM may guess `interaction_shape` confidently on ambiguous frames. Defaults to `monologue` if confidence is low (single-speaker visible, no chat panel visible).
- `gaze_direction` is the noisiest field; relies on the VLM correctly localizing where the streamer is looking. Treat as soft signal — only `at-chat` is consumed (for `reading_chat_reaction` confirmation).

**Verification:** run Stage 6 on a known reading-chat moment; verify `interaction_shape == "reading-chat"` and `gaze_direction == "at-chat"`.

---

## Phase 4.6 — Diversity-aware ranking (MMR)

**Goal:** when Pass C's bucket distribution still produces 3-4 near-duplicate moments (e.g., the streamer keeps making the same hot take across multiple buckets), demote the duplicates.

**Method:** Maximal Marginal Relevance over sentence-transformer embeddings of each moment's Pass B `why` field. Reuses `sentence-transformers/all-MiniLM-L6-v2`, already loaded for [[entities/callback-module]] (Tier-2 M3 callback detection) — no new dependency.

**Algorithm:**

```python
# After Pass D, before Phase 4.2 boundary snap
embeddings = model.encode([m["why"] for m in moments])
selected = []
remaining = list(range(len(moments)))
λ = 0.7  # weights toward score; 0.3 toward novelty

while len(selected) < TARGET_CLIP_COUNT and remaining:
    best_idx, best_value = None, -1
    for i in remaining:
        score = moments[i]["final_score"]
        max_sim = max(
            (cosine(embeddings[i], embeddings[j]) for j in selected),
            default=0.0
        )
        mmr = λ * score - (1 - λ) * max_sim
        if mmr > best_value:
            best_value, best_idx = mmr, i
    selected.append(best_idx)
    remaining.remove(best_idx)
```

**Position:** between Pass D and Phase 4.2 boundary snap. Gates the final candidate list, not the diagnostic record (full Pass D scores stay in diagnostics).

**Files touched:**
- `scripts/lib/stages/stage4_moments.py` (or a new `stage4_diversity.py`) — MMR step after Pass D
- `config/rubric.json` — add `mmr_lambda` parameter (default 0.7)

**Tradeoff:** MMR can demote a high-scoring duplicate that the user would have wanted both copies of. The `λ=0.7` skew toward score is conservative; if early VODs feel under-selected, raise to 0.8.

**Risks:**
- Embedding model load time on cold start (~5s); reuse the existing M3 callback module's loaded instance to amortize.
- All-zeroes similarity matrix fallback if embedding fails — degrade to score-greedy ranking, log warning.

**Verification:** run on a VOD with 3 known near-duplicate hot-take moments; verify only the highest-scoring of the three survives MMR.

---

## Phase 4.7 — Style preset extension

**Goal:** add interaction-coded styles alongside the existing emotion-coded ones. New style names map to weighted Pattern Catalog subsets.

**New styles:**

| Style | Boosted patterns | Demoted patterns |
|---|---|---|
| `conversational` | `challenge_and_fold`, `interview_revelation`, `social_callout` | `silent-gameplay` |
| `informational` | `informational_ramble`, `storytelling_arc` | `hype` |
| `freestyle` | `rap_battle_freestyle` | (none) |
| `chatlive` | `reading_chat_reaction`, `unexpected_topic_shift` | (none) |
| `spicy` | `hot_take_pushback`, `social_callout`, `setup_external_contradiction` | (none) |

Existing styles (`auto`, `funny`, `hype`, `emotional`, `storytime`, `hot_take`, `reactive`, `variety`) preserved.

**Files touched:**
- `config/style_pattern_weights.json` (new) — style → pattern weight mapping
- `workspace/AGENTS.md` — Discord agent natural-language → style mapping (e.g., "find informational clips" → `informational`, "interview moments" → `conversational`)
- `workspace/skills/stream-clipper/SKILL.md` — same alias updates
- `scripts/lib/stages/stage4_moments.py` — style → pattern weight applied to Pass B `primary_pattern` re-scoring
- `entities/openclaw.md`, `entities/discord-bot.md` — document new style aliases

**Risks:**
- Demotions can over-prune — `conversational` mode demoting `silent-gameplay` makes sense for the user's listed targets but breaks variety mode. Demotion only fires when style is explicitly chosen, never on `auto`.
- Discord agent ambiguity: "find conversational clips" vs "find good conversation". The Discord agent prompt should pick the closest style alias and surface the choice in its response.

**Verification:** run `--style informational` on a VOD with both rambling segments and gameplay; verify the rambling moments outrank gameplay.

---

## Phase 4.8 — Eval and validation

**Goal:** measure precision/recall delta vs the pre-Tier-4 pipeline. Without this, Tier-4 is shipping vibes.

**Method:**

1. Pick 5 VODs covering the user's targets (1× Just Chatting with controversial moments, 1× IRL with off-screen voices, 1× interview format, 1× heavy-storytelling, 1× variety).
2. For each: have user pre-label the moments they would *want* clipped (5-10 per VOD).
3. Run pre-Tier-4 pipeline → record:
   - Precision = `|selected ∩ wanted| / |selected|`
   - Recall = `|selected ∩ wanted| / |wanted|`
4. Run post-Tier-4 pipeline → record same.
5. Diff per pattern class.

**Targets:** `+20%` recall on `informational_ramble` and `setup_external_contradiction`; `+10%` precision overall. If we don't see those numbers, retune rubric weights or revisit the catalog before declaring done.

**Tooling:** [[entities/bootstrap-twitch-clips]] already exists as a research tool for building eval sets — extend it to ingest user pre-labeled moments and emit the precision/recall report.

**Files touched:**
- `scripts/lib/eval_tier4.py` (new) — eval runner
- `clips/.diagnostics/<vod>_eval.json` (new output) — per-VOD precision/recall by pattern
- `entities/bootstrap-twitch-clips.md` — document the new pre-label ingest path

---

## Cross-cutting concerns

### What NOT to do

- **Don't gate on patterns.** A moment that doesn't fit any catalog pattern can still be clip-worthy. The catalog is a *guide for the LLM*, not a rejection rule. The non-gatekeeping principle from [[concepts/vision-enrichment]] applies across all Tier-4 phases.
- **Don't over-tune the rubric weights.** Without an eval set, weight-tuning is just feel. Ship the documented weights; revisit only after Phase 4.8.
- **Don't replace M3 callbacks or A1 two-stage Pass B.** Tier-4 *complements* them — M3 finds cross-chunk arcs, A1 finds global arcs, Pass D scores intra-chunk shape. Different scales.
- **Don't skip Phase 4.8.** The eval phase is the gate for declaring Tier-4 done. Ship 4.1-4.7, then run 4.8 before integrating into production.
- **Don't mix the rubric weights into Pass C's bucket distribution.** Pass C's per-bucket diversity is structural (time spread); Pass D's rubric is qualitative (clip-worthiness); MMR (4.6) is similarity-based. Three orthogonal signals — keeping them separate is what lets each work cleanly.
- **Don't grow the Pattern Catalog past ~12 patterns.** The user-facing taxonomy needs to be small enough for the LLM to internalize in one prompt. More patterns = vaguer matches = worse precision.

### VRAM impact

Zero additional VRAM. Tier-4 reuses:
- The Pass B / Stage 6 multimodal model (already loaded)
- The M3 sentence-transformers embeddings model (already loaded for callback detection)
- Stdlib only for [[#phase-42---conversational-analytics-layer]]

See [[concepts/vram-budget]] — the budget table doesn't change.

### Wall-time impact

| Phase | Added wall time | Notes |
|---|---|---|
| 4.2 conversation_shape | ~5s per VOD | One stdlib pass |
| 4.3 Pass B prompt rewrite | ~0s | Same call count, larger prompt |
| 4.4 Pass D rubric judge | ~3 min on a 2-hr VOD | 10-20 LLM calls @ 5-15s each |
| 4.5 Stage 6 fields | ~0s | Same call, more output tokens |
| 4.6 MMR diversity | ~10s per VOD | Embedding compute |
| 4.7 style presets | ~0s | Config-driven |

Total: ~3.5 min added to a 2-hr VOD that currently takes ~45-90 min. Negligible.

---

## Wiki pages — created and updated

**New pages (created with each phase):**
- [[entities/conversation-shape-module]] — Phase 4.2
- [[entities/pattern-catalog]] — Phase 4.3
- [[entities/rubric-judge-module]] — Phase 4.4
- [[entities/model-profile]] — Phase 4.1

**Updated pages:**
- [[concepts/clipping-pipeline]] — Pass D inserted between Pass C and 4.2; Stage 6 field additions
- [[concepts/highlight-detection]] — Pass D documented; Pattern Catalog reference; MMR re-rank
- [[concepts/vision-enrichment]] — `interaction_shape` / `pattern_match` / `gaze_direction` fields
- [[concepts/moment-discovery-upgrades]] — Tier-4 added under the existing tier hub
- [[entities/qwen35]] + [[entities/lm-studio]] — point at model profiles
- [[entities/openclaw]] + [[entities/discord-bot]] — new style aliases
- [[index]] — new entries
- [[log]] — entry per shipped phase

---

## Decision points (locked in 2026-05-01)

1. **Pattern Catalog ownership:** `config/patterns.json` (user-editable, hot-reloadable). ✓
2. **Sub-score rubric:** ships as a dedicated phase (Pass D), same multimodal model as Pass B / Stage 6. ✓
3. **Single-model default:** none — `models_profile` block ships with `qwen35-9b`, `qwen35-35b`, `gemma4-26b` presets, swappable via dashboard. Future models slot in via config. ✓

---

## Related

- [[concepts/moment-discovery-upgrades]] — Tier-1/2/3 hub (Q1-Q5, M1-M3, A1-A3) that Tier-4 extends
- [[concepts/highlight-detection]] — Stage 4 detail; Pass D inserts here
- [[concepts/vision-enrichment]] — Stage 6 detail; Phase 4.5 extends prompt
- [[concepts/clipping-pipeline]] — full pipeline view post-Tier-4
- [[entities/grounding]] — cascade integration preserved; rubric scores feed into the `audit_one_liner`
- [[entities/lm-studio]] — model swap mechanics
- [[concepts/bugs-and-fixes]] — BUG 37b/c (raw_score convention Pass D inherits)
- `MOMENT_DISCOVERY_UPGRADE_PLAN.md` (project root, outside vault) — the parent upgrade plan that Tier-4 sits in
