---
title: "Implementation Plan — 2026 upgrade phases"
type: source
tags: [source, roadmap, phases, implementation-plan, hub, originality, grounding, chat, speech, vision, phase-0, phase-1, phase-2, phase-3, phase-4, phase-5]
sources: 1
updated: 2026-04-28
---

# Implementation Plan (Phases 0–5)

Summary of `IMPLEMENTATION_PLAN.md` at the project root (outside the vault). Maps every research recommendation from `ClippingResearch.md` to a concrete code/config change, ordered by ROI and risk. Used as the hub for the seven wiki pages that reference Phase X.Y of this plan.

The plan is companion to [[concepts/moment-discovery-upgrades]], which was authored later and addresses a different gap class (long-range narrative + multi-modal events).

---

## Phase 0 — Ship-this-week, zero-risk wins

| Sub-phase | What it changes | Wiki page |
|---|---|---|
| 0.1 | Frame-sampling window: 6 targeted offsets relative to the moment peak `T` (`T−2, T+0, T+1, T+2, T+3, T+5`); all 6 frames go to the VLM | [[concepts/vision-enrichment]] |
| 0.2 | `enable_thinking=false` audit on classification calls; `/no_think` belt-and-suspenders sentinel | [[entities/lm-studio]] |
| 0.3 | Tier-1 grounding cascade — stdlib regex denylist + token-overlap | [[entities/grounding]] |

---

## Phase 1 — Grounding cascade + LM Studio client

| Sub-phase | What it changes | Wiki page |
|---|---|---|
| 1.1 | Tier-2 (MiniCheck NLI) + Tier-3 (Lynx-8B) grounding; `cascade_check()` API; `lmstudio.py` HTTP client | [[entities/grounding]], [[entities/lmstudio]] |
| 1.2 | Pass B JSON mode (`response_format: {type: json_object}`); top-level `{"moments": [...]}` envelope | [[concepts/highlight-detection]] |

---

## Phase 2 — Twitch chat signal

| Sub-phase | What it changes | Wiki page |
|---|---|---|
| 2.1 | Pass A' integration into Pass C ranking | [[concepts/chat-signal]] |
| 2.2 | VOD chat acquisition: anonymous Twitch GraphQL + TwitchDownloader importer | [[entities/chat-fetch]] |
| 2.3 | Stdlib feature extractor (burst, emote density, hard event counts) | [[entities/chat-features]] |

---

## Phase 3 — Speech pipeline rebuild

| Sub-phase | What it changes | Wiki page |
|---|---|---|
| 3.1 | WhisperX VAD + batched ASR + forced alignment | [[entities/speech-module]] |
| 3.2 | Streamer-prompt biasing (custom vocabulary) | [[entities/speech-module]] |
| 3.3 | Optional Demucs v4 vocal-stem separation | [[entities/vocal-sep-module]] |
| 3.5 | faster-whisper fallback + speech-module wrapper | [[entities/speech-module]] |

---

## Phase 4 — Pre-vision masking + boundary snapping

| Sub-phase | What it changes | Wiki page |
|---|---|---|
| 4.1 | UI overlay masking (MOG2) + overlay-text OCR (PaddleOCR) | [[concepts/chrome-masking]], [[entities/chrome-mask-module]] |
| 4.2 | Pragmatic variable-length windows via sentence + silence-gap snapping | [[concepts/boundary-snap]], [[entities/boundary-detect-module]] |

---

## Phase 5 — Model split + self-consistency

| Sub-phase | What it changes | Wiki page |
|---|---|---|
| 5.1 | Optional per-stage model overrides (Pass B text-only, Stage 6 vision-specialist) | [[concepts/model-split]] |
| 5.2 | N-candidate ranking for hallucination suppression | [[concepts/self-consistency]], [[entities/self-consistency-module]] |
| 5.3 | Bootstrap Twitch-clip eval dataset | [[entities/bootstrap-twitch-clips]] |

---

## Relationship to other plans

- **Source research**: `ClippingResearch.md` at project root — 2026 SOTA literature synthesis that motivates every phase.
- **Sibling plan**: [[concepts/moment-discovery-upgrades]] — Tier-1/2/3 work targeting long-range arcs + multi-modal events. Authored 2026-04-27, after Phases 0–3 had shipped.

---

## Related

- [[overview]] — system context
- [[concepts/clipping-pipeline]] — pipeline placement of every Phase
- [[concepts/moment-discovery-upgrades]] — Tier-1/2/3 sibling plan
- [[concepts/bugs-and-fixes]] — bug ledger including BUGs introduced/fixed during phase rollouts
- [[sources/openclaw-stream-clipper-summary]] — original architecture doc
