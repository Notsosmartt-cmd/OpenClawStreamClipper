# OpenClaw 2026 Upgrade — Implementation Plan

**Source:** `ClippingResearch.md` — 2026 technical roadmap synthesizing SOTA 2024–2026 literature against the current pipeline.
**Created:** 2026-04-23
**Owner:** maintained with the codebase; update status notes as phases ship.

---

## Executive summary

The research doc diagnoses a specific architecture bug and provides a prioritized roadmap. The **single highest-ROI change** is a ~30-line fix to frame sampling in Stage 5/6. The **next-highest** layer is a 3-tier grounding gate between Pass B and Stage 6 to stop hallucinations like "gifted subs." Below, every research recommendation is mapped to a concrete code/config change in this repo, ordered by ROI and risk.

---

## Phase 0 — Ship this week (zero risk, zero new deps)

### 0.1 Fix the frame-sampling window — **highest single ROI in the entire plan**

**Research basis:** "Additional topic 2" + 🥇 roadmap item. Payoff is at T+0 → T+3; current code describes the setup.

**Files:**
- `scripts/clip-pipeline.sh:1762-1785` (Stage 5 — frame extraction)
- `scripts/clip-pipeline.sh:1882` (Stage 6 — `for frame_idx in ["03", "04"]`)

**Change:** replace uniform 6-frame sweep + middle-2-frame selection with targeted offsets relative to the moment peak `T`:
- Extract at **T−2, T+0, T+1, T+2, T+3, T+5** (6 precise `-ss` invocations OR one `select` filter)
- Feed **all 6** frames to the VLM in one call, not just two
- Name files `frames_${T}_tminus2.jpg`, `frames_${T}_t0.jpg`, etc.
- Extend the prompt to state frames are time-ordered and ask the model to reason about the payoff in T+0..T+5.

**Acceptance:** regenerate 5 known-problem clips (incl. the "Ranked 3.0 / gifted subs" case); titles reference payoff content, not overlay chrome. **Risk:** none.

### 0.2 Audit `enable_thinking=false` on classification calls

**Research basis:** Additional topic 1. Pass B is classification, not reasoning.

**Files:** `scripts/clip-pipeline.sh:588, 1037, 1954` (all three call sites already set `chat_template_kwargs={enable_thinking: False}`).

**Change:** add a `/no_think` system-prompt sentinel (belt-and-suspenders for LM Studio versions that drop `chat_template_kwargs` for 35B). Log `reasoning_tokens` at every site (Stage 6 already does; Stage 3 + Pass B should follow).

**Acceptance:** on 9B, reasoning_tokens≈0 for Pass B; on 35B, document remaining token waste as a known limitation.

### 0.3 Regex denylist + transcript presence check

**Research basis:** §8.5 — cheapest possible grounding check.

**New files:** `config/denylist.json`, `scripts/lib/grounding.py`.

**Wire points:**
- Pass B post-parse (`parse_llm_moments` ~line 1140): if `why` contains a denylist term absent from the chunk text, null it.
- Stage 6 post-parse (~line 2023 block): run the same check on `title`/`hook`/`description` against `(transcript window ∪ why)`. On fail, regenerate once with a tighter prompt; on second fail, emit a generic template.

**Acceptance:** the 4 hallucination modes ("gifted subs", "sub train", "clutch", "triple kill") never propagate when the transcript lacks those tokens.

---

## Phase 1 — Grounding cascade + structured outputs (1–2 weeks)

### 1.1 Three-tier grounding gate between Pass B and Stage 6
- **Tier 1** BM25 + spaCy content-word overlap (<5 ms, `rank_bm25`)
- **Tier 2** MiniCheck NLI — `lytang/MiniCheck-Flan-T5-Large` CPU or `bespokelabs/Bespoke-MiniCheck-7B` GPU (50–200 ms)
- **Tier 3** Lynx-8B (`PatronusAI/Llama-3-Patronus-Lynx-8B-Instruct`) via LM Studio on borderline only (~5–10 %)
- Wire between Pass B and Stage 6 (null failing Pass B fields) and after Stage 6 (regenerate-once then generic template)
- Config: `config/grounding.json` with tier thresholds + enable flags

### 1.2 Structured outputs via XGrammar (LM Studio) or NuExtract two-stage split
- Prefer LM Studio's `response_format: {type: json_schema, strict: true}`.
- Fallback: `numind/NuExtract-2.0-4B` (Qwen2.5-VL base) as a dedicated formatter step.
- Centralize LM Studio calls into `scripts/lib/lmstudio.py`; remove the inlined bash heredocs.

---

## Phase 2 — Chat-signal ingestion (Pass A') (3–5 days)

**Research basis:** 🥈 #2 + Additional topic 4. "Biggest win per line of code after the frame-sampling fix."

### 2.1 Live path — EventSub
`scripts/lib/chat_ingest.py` — persistent WebSocket listener on `channel.chat.message`, `channel.subscribe`, `channel.cheer`, `channel.raid`. Writes `vods/.chat/{channel}_{ts}.jsonl`.

### 2.2 VOD path — TwitchDownloader / GraphQL
`scripts/lib/chat_fetch.py` — on Stage 1, download timestamped chat for Twitch VODs. Cache parallel to `.transcriptions/`.

### 2.3 Features
`scripts/lib/chat_features.py` computes per-second:
- `msgs_per_sec` + z-score vs channel baseline
- `emote_density[category]` (laugh/hype/tense/sad/W/L/tilt) using `config/emotes.json`
- `unique_chatters_per_sec`, `recurring_phrase_burst`
- `sub_count`, `bit_count`, `donation_count` — **hard ground truth**

### 2.4 Wire in
- Pass A: chat burst/emote as two more universal signals (+≤2)
- Pass B: inject a structured `chat_context` block into the prompt
- Stage 6: same block + explicit rule "If sub/bit/donation events = 0, you may NOT mention gifted subs, sub trains, bit rain, or donations."
- Grounding gate: EventSub events = hard ground truth; `sub_count=0` + "gifted subs" claim = hard reject.

**Config:** `config/chat.json` (Twitch creds, channel→platform map, EventSub webhook URL).

---

## Phase 3 — Speech pipeline upgrade (1–2 weeks)

**Research basis:** 🥈 #3 + §8.3. 50–70 % fewer hallucinated segments, 20–35 % relative WER on streamer speech.

### 3.1 Swap faster-whisper for WhisperX (~1 afternoon for 80 % of gains)
Replace Stage 2 call; WhisperX bundles VAD + batched faster-whisper + forced alignment + pyannote diarization.

### 3.2 Upgrade ASR model
`config/models.json` → `whisper_model: "large-v3-turbo"` (free 2.5× speedup). Evaluate **NVIDIA Parakeet-TDT-0.6B-v3** (CC-BY-4.0) if throughput-bound.

### 3.3 Vocal separation
`scripts/lib/vocal_sep.py` — Mel-Band RoFormer or Demucs v4 `htdemucs_ft` before Whisper; gate behind `config/speech.json` flag.

### 3.4 Active-speaker detection
`scripts/lib/asd.py` — TalkNet-ASD on webcam crop; AND with audio VAD.

### 3.5 Streamer-slang biasing
`config/streamer_prompts.json` — per-channel `initial_prompt` (game titles, character names, emotes, jargon; ≤224 tokens).

---

## Phase 4 — UI masking + variable-length windows (2–3 weeks)

### 4.1 UI chrome detection and masking (§Additional topic 3)
`scripts/lib/chrome_mask.py`
1. Per-streamer calibration: Florence-2 `<REGION_PROPOSAL>` → cache `config/streamers/{channel}_chrome.json`.
2. Per-clip preprocessing: OpenCV MOG2 at 2 fps over the window → detect transient overlays.
3. Union → UI mask applied to Stage 5 JPEGs before the VLM call.
4. PaddleOCR PP-OCRv5 runs on the unmasked frame; overlay text goes into the prompt as ground truth.
5. If OBS scene JSON is available, use it instead of Florence-2 — exact bboxes for free.

### 4.2 Variable-length windows via CG-DETR (§8.7)
`scripts/lib/boundary_detect.py`
1. Per Pass C candidate, CG-DETR via Lighthouse on ±90 s window with SlowFast+CLIP (or InternVideo2).
2. Snap boundaries to TransNet V2 shot cuts (±5 s), Whisper sentence boundaries (±3 s), pyannote silence gaps (>200 ms). Bias end snap later (+0..+8 s).
3. LLM storytime-payoff prompt for `{setup_start, payoff_start, payoff_end, confidence}`.

Gate behind `config/pass_c.json` `variable_length: false` default. Acceptance: QVHighlights R1@0.5 ≥ 0.65 on a held-out subset.

---

## Phase 5 — R&D / backlog

### 5.1 Split Pass B from Stage 6 into 6a + 6b
- `config/models.json` gains `text_model_passb` and `vision_model_stage6`.
- Pass B → Qwen3-32B or Qwen3-30B-A3B (text-only, non-thinking).
- Stage 6a (text classifier) emits `{what_happened, category, confidence}` from transcript + chat + OCR.
- Stage 6b (vision describer) treats `what_happened` as a hard constraint, produces title/hook/description.
- Hot-swap to Qwen3-VL-32B AWQ INT4 for top 5 % of candidates.

### 5.2 Self-consistency N=3 on titles/hooks (§8.2)
`scripts/lib/self_consistency.py` — sample N=3 at T=0.8, SBERT cosine + NLI; USC for descriptions. Only runs on gate-passing clips.

### 5.3 Bootstrap a Twitch/Kick clip-worthiness dataset (§8.6)
`scripts/research/bootstrap_twitch_clips.py` — Helix `GET /helix/clips` + TwitchDownloaderCLI for VODs. ~50 k labeled triples.

### 5.4 HITL + DPO retraining loop (§8.10)
Argilla or Label Studio; 3 candidates + transcript + 4 frames; weekly DPO fine-tune on accumulated preferences. **Never show AI confidence above content** (Bias-in-the-Loop arXiv:2509.08514).

### 5.5 Temporal Contrastive Decoding (EventHallusion)
Evaluate TCD at Stage 6 as a language-prior suppressor.

### 5.6 Revisit end-to-end architectures (Q4 2026)
All current long-video-to-clips LLMs underperform supervised DETRs by 15–30 mAP. Hold.

---

## Configuration inventory (new / changed)

| File | New/Changed | Phase | Purpose |
|---|---|---|---|
| `config/denylist.json` | new | 0 | hallucination-pattern regexes |
| `config/grounding.json` | new | 1 | NLI tier thresholds, enable flags |
| `config/chat.json` | new | 2 | Twitch API keys, channel→platform |
| `config/emotes.json` | new | 2 | emote category dictionary |
| `config/streamer_prompts.json` | new | 3 | per-channel Whisper `initial_prompt` |
| `config/streamers/{channel}_chrome.json` | new | 4.1 | cached UI bboxes |
| `config/pass_c.json` | new | 4.2 | variable-length window flags |
| `config/models.json` | changed | 5.1 | split text/vision model IDs |
| `config/speech.json` | new | 3 | vocal separation / VAD flags |

## Dependency additions

| Package | Phase | Notes |
|---|---|---|
| `rank-bm25`, `spacy` + `en_core_web_sm` | 1 | Tier-1 grounding |
| `transformers` (MiniCheck T5) | 1 | Tier-2 grounding (CPU) |
| `websocket-client` | 2 | EventSub listener |
| `whisperx` | 3 | Stage 2 replacement |
| `demucs` or RoFormer weights | 3 | Vocal separation |
| `paddleocr` + `paddlepaddle` | 4.1 | Overlay OCR |
| `lighthouse-emnlp24` + TransNet V2 | 4.2 | Moment retrieval + shot detection |
| `argilla` | 5.4 | HITL UI |

---

## Sequencing rationale

The order isn't just ROI — it's **dependency**. Phase 0 surfaces problems; Phase 1 catches them with a gate; Phase 2 adds the hard-ground-truth signal the gate needs to be robust; Phase 3 cleans the transcript everything downstream reads; Phase 4 cleans the pixels; Phase 5 is compounding infrastructure (data, eval, HITL) that only pays off after the pipeline is already producing correct-enough clips to be worth rating.

**Minimum viable patch to ship in ~3 days:** Phase 0.1 + 0.2 + 0.3 + Phase 1.1 Tier 1 + Tier 2 on Pass B output. This alone is what the research doc calls "the single patch that would have prevented the gifted-subs propagation."

---

## Wiki-update discipline

Every phase produces wiki updates in the same PR as the code:
- New pages: `grounding-gate.md`, `chat-signal.md`, `chrome-masking.md`, `speech-pipeline.md`, `self-consistency.md` (concepts); `whisperx.md`, `chat-ingest.md`, `minicheck.md`, `lynx-8b.md`, `cg-detr.md`, `florence-2.md`, `paddleocr.md` (entities).
- Updated: `overview.md`, `clipping-pipeline.md`, `highlight-detection.md`, `vision-enrichment.md`, `vram-budget.md`, `bugs-and-fixes.md`, `index.md`, `log.md`.

---

## Status tracker

| Phase | Status | Notes |
|---|---|---|
| 0.1 Frame sampling | ✅ shipped | 2026-04-23. `scripts/clip-pipeline.sh` Stage 5 + Stage 6. |
| 0.2 Thinking audit | ✅ shipped | 2026-04-23. `/no_think` sentinel on Stage 3 + Pass B prompts. |
| 0.3 Denylist (Tier 1) | ✅ shipped | 2026-04-23. `scripts/lib/grounding.py`, `config/denylist.json`. |
| 1.1 Grounding cascade | ✅ shipped | 2026-04-23. Tier 2 (MiniCheck NLI) + Tier 3 (Lynx-8B) + regenerate-once on Stage 6. Tier 3 off by default until user loads Lynx in LM Studio. Requires `docker compose build` to pick up `transformers`. |
| 1.2 JSON mode | ✅ shipped | 2026-04-23. `response_format: {json_object}` on `call_llm` + Stage 6 `_vision_call`. Pass B prompt migrated to `{"moments": [...]}` shape; parser accepts both. Full call-site centralization into `scripts/lib/lmstudio.py` deferred as a follow-up — current wrapper is used only by grounding Tier 3. |
| 2.1 Live EventSub | ⬜ deferred | Separate product — requires Twitch app + webhook infra. |
| 2.2 VOD chat fetch | ✅ shipped | 2026-04-23. `scripts/lib/chat_fetch.py` — anonymous Twitch GraphQL + TwitchDownloader import. |
| 2.3 Chat features | ✅ shipped | 2026-04-23. `scripts/lib/chat_features.py` — stdlib, z-score + emote density + hard events. |
| 2.4a Stage 1b discovery | ✅ shipped | 2026-04-23. Auto-fetch opt-in via `config/chat.json::auto_fetch`. |
| 2.4b Pass A signal | ✅ shipped | 2026-04-23. Burst + emote-density bonuses, capped per `config/chat.json::scoring`. |
| 2.4c Prompt context | ✅ shipped | 2026-04-23. Structured chat_context block in Pass B + Stage 6, plus hard-rule directive in Stage 6. |
| 2.4d Event ground-truth | ✅ shipped | 2026-04-23. `cascade_check(hard_events=..., event_map=...)` — Tier 1 hard-fail on event contradiction. |
| 3.1 WhisperX swap | ✅ shipped | 2026-04-23. `scripts/lib/speech.py` — WhisperX primary with faster-whisper fallback. 20-minute chunking dropped; VAD handles it. |
| 3.2 `large-v3-turbo` default | ✅ shipped | 2026-04-23. `config/speech.json::model`. Override via `CLIP_WHISPER_MODEL` env var still supported. |
| 3.3 Vocal separation | ✅ shipped (opt-in) | 2026-04-23. `scripts/lib/vocal_sep.py` — Demucs v4 htdemucs_ft. Off by default; flip `speech.json::vocal_separation.enabled` to turn on. |
| 3.4 TalkNet-ASD | ⬜ deferred | Face tracking + webcam-crop localization + VAD AND-gating. Separate product scope. |
| 3.5 Streamer prompts | ✅ shipped | 2026-04-23. `config/streamer_prompts.json` — per-channel `initial_prompt` with filename-substring matching. |
| 4.1a MOG2 transient-overlay detection | 🗑 removed 2026-05-01 | BUG 50 — Stage 5's [-2,0,+1,+2,+3,+5]s frame layout is too sparse for background subtraction; misfired 100 % of windows, dead code in practice. |
| 4.1b OBS scene overrides | 🗑 removed 2026-05-01 | Removed alongside chrome stage. No active streamer overrides existed. |
| 4.1c PaddleOCR overlay text | 🗑 removed 2026-05-01 | BUG 49 — PaddleOCR wedged on a frame, truncated the pipeline before Stages 6/7/8 ran. Defense layers (SIGALRM, heartbeat, outer timeout 600) couldn't fully bound the C++-extension wedge. |
| 4.1 Florence-2 auto-calibration | 🗑 abandoned | Was deferred behind 4.1a/c — chrome stage removed entirely. |
| 4.2 Sentence + silence boundary snap | ✅ shipped | 2026-04-24. `scripts/lib/boundary_detect.py` — asymmetric drift budgets; uses Phase 3 word-level timestamps. |
| 4.2 CG-DETR / SG-DETR | ⬜ deferred | Needs QVHighlights eval harness. Revisit after Phase 5 bootstrap. |
| 4.2 TransNet V2 shot-cut snap | ⬜ opt-in | Coded but off by default in `config/boundaries.json::shot_cut_snap.enabled`. |
| 5.1 Per-stage model split (config + pipeline) | ✅ shipped | 2026-04-24. Optional `text_model_passb` / `vision_model_stage6` in `config/models.json`; backwards-compat (null → unified). |
| 5.1 Stage 6a text classifier | ⬜ deferred | Requires Stage 6 rewrite. |
| 5.1 Top-5 % vision escalation | ⬜ deferred | Needs cost/quality eval harness. |
| 5.2 Self-consistency ranker (module) | ✅ shipped | 2026-04-24. `scripts/lib/self_consistency.py` — USC + reference grounding. Standalone + CLI. |
| 5.2b Stage 6 self-consistency wire-in | ⬜ opt-in | Module ready; integration into `_vision_call()` closure is a targeted follow-up. |
| 5.3 Twitch clip dataset bootstrap | ✅ shipped | 2026-04-24. `scripts/research/bootstrap_twitch_clips.py` — Helix + GraphQL fetch, positive/negative pairing, summary stats. |
| 5.4 HITL + DPO loop | ⬜ deferred | Separate product scope (Argilla/Label Studio + weekly fine-tune). |
| 5.5 Temporal Contrastive Decoding | ⬜ deferred | Research evaluation against Phase 5.3 dataset. |
| 5.6 End-to-end architectures | ⬜ deferred | Held per plan; revisit Q4 2026. |
