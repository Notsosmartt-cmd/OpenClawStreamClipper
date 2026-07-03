---
title: "Master Proposal — Clipping Pipeline Automation Roadmap (2026-07)"
type: concept
tags: [plan, roadmap, proposal, hub, fusion, reference-humor, calibration, originality, research-handoff]
sources: 0
status: planned
updated: 2026-07-02
---

# Master Proposal — Clipping Pipeline Automation (2026-07)

The single organizing document for **everything proposed, in-progress, and shipped** across the 2026-06→07 evaluation arc. Commissioned by the owner 2026-07-02: *"organize my thoughts … deeper research and work into these … compile a big proposal report including the previous and currently in-progress proposals."* This page **indexes and sequences**; the detail stays on the linked pages. Done/not-done ground truth: [[concepts/evaluation-status-2026-06]] (the June tracker) + this page for everything since.

---

## 0. The goal stack (what all of this serves)

1. **Stop the TikTok "unoriginal" flag** — the owner's #1 stated goal ([[concepts/plan-unoriginality-audio-layer]], [[concepts/tiktok-originality-mechanics-2026-06]]).
2. **Catch the missed clip classes** — cross-modal incongruity + externally-referenced humor (bus clip, George Bush) ([[concepts/case-incongruity-comedy]], [[concepts/multimodal-fusion-2026-07]], [[concepts/reference-humor-2026-07]]).
3. **Make scoring measured, not vibes** — fit the ~50 hand-tuned constants; decorrelate the judges ([[concepts/plan-calibration-loop]], [[concepts/plan-decorrelate-judges]]).
4. **Keep what works** — storytime/conversation and blatant-banger lanes stay untouched; every new lane is additive, flag-gated, boost-only, failure-soft (repo convention).

## 1. Current state (verified, as of 2026-07-02)

| Layer | State |
|---|---|
| Live pipeline (8 stages) | Working; transcript-first detection; joint frames+transcript prompts only at Stage 5.5/6, *behind* the transcript-only proposal gate |
| Sensors (offline lane) | ✅ **Built + verified**: CLAP events, faster-whisper words, censor, music-bed w/ `added`, motion spikes, caption OCR, LLM style profile; hang-proof watchdog; TikTok-outro trim ([[entities/audio-sense-module]], [[entities/visual-sense-module]], [[concepts/plan-clip-forensics]] — shipped) |
| Forensics UX | ✅ Dashboard **Clip Forensics tab** ([[entities/dashboard]]) |
| Engagement scaffolding | ✅ code-shipped, 🟡 **no real-VOD validation yet**: SFX cues, hook templates, cold-open |
| Originality levers | ⬜ **OFF**: `tts_vo`, `music_bed`, `eq_tilt` (computed, never applied) |
| Learning layer | ⬜ nothing built (no Pass-B cache, re-scorer, fitter, ranker) |
| In-progress plan pages | [[concepts/clipping-quality-overhaul]] family (arc-completeness/baseline-contrast/engagement/reaction-worthy = shipped-awaiting-validation; batch-diversity/tier-4 planned) |

## 2. Evaluation of the fusion/reference analysis (the owner's pasted excerpt, claim-by-claim)

The excerpt is this month's [[concepts/multimodal-fusion-2026-07]] + [[concepts/reference-humor-2026-07]] analysis. Verdicts, and **which claims need live research before build**:

| Claim | Verdict | Action needed |
|---|---|---|
| "LM Studio has no audio/video content parts; llama.cpp gained audio 2025" | Was true at analysis time — **tooling moves fast** | **RQ1 (verify live)**: LM Studio/llama.cpp `mtmd` audio-in status *now*, per-version |
| "Audio ≈ 25 tok/s; 45 s window ≈ 10–20 k tokens" | Ballpark from Qwen-class encoders; conclusion (omni = judge, not scanner) is **robust even if numbers shift 2×** | RQ1 confirms exact rates per candidate model |
| "7B omni is a markedly weaker reasoner than the 35B MoE" | General scaling truth; **unproven for THIS task** | **RQ2 benchmark**: A/B on the owner's `reference_clips/` + `.notes.json` ground truth — 35B-reading-symbolic-timeline vs 7B-omni-watching |
| "Every sensor for timeline fusion is built and verified" | ✅ **TRUE** (verified 2026-06-21 on ReemKnocks: `bruh` cluster, 9 motion spikes, suspense bed 6–14 s `added:true`) | none — build-ready |
| "Chat is already ingested; nothing mines it" | Mechanism TRUE (grounding + Stage 6 chat block) — **but a data dependency hides here** | **Audit**: do the owner's VOD sources actually come with chat sidecars? Local recordings/TikTok downloads may have none → chat mining's value depends on capture |
| "The model already knows most memes — nobody asks" | Plausible, **untested** | Cheap probe experiment: prompt the 35B with 10–20 known formats described symbolically; measure recognition before building anything |
| "One proxy lane catches both incongruity + reference humor" | Sound — shared signature "audience reacts, words don't explain" | **Precision risk**: laughter-without-joke also fires on gameplay/inside jokes → the verifier stage (Stage 5.5-style) is load-bearing, not optional |
| "Deadpan + no reaction = accept the miss" | Agreed — correct scope guard | none |

## 3. Workstreams (all proposals, organized)

### A. Perception & fusion (goal 2 — the new work; detail: [[concepts/multimodal-fusion-2026-07]], [[concepts/reference-humor-2026-07]])
- **A1. Timeline-builder + anomaly-proposer lane** — merge transcript + CLAP events + motion into one time-ordered stream; propose `src=ANOMALY` where reaction/motion exceeds the words; verify via a joint prompt. *Sensors ready; the keystone build.* (~1–2 days)
- **A2. Chat reference mining** — burst n-grams/emote spikes around candidates → score boost + title material. **Pivoted per owner (see §6.1): primary path = OCR the burned-in chat overlay region** (reuses `visual_sense.caption_ocr`); structured chat files are the secondary path when they exist. (~1–1.5 days)
- **A3. Reference-recognition probe** — `known_format:{name,confidence}` + spoken-word↔seen-object wordplay check in Stage 5.5/6 prompts. (hours; depends on A1/A5 so both pun halves are present)
- **A4. Meme-format library** — `config/meme_formats.json`, embedding-matched (sentence-transformers already in repo); grown from `.notes.json` + forensics decompositions; later yt-dlp/deep-research refresh. (~1 day + ongoing curation)
- **A5. Judge timeline upgrade** — feed the A1 event stream into the existing Stage 5.5 joint prompt. (hours)
- **A6. Embedding-incongruity axis** — CLAP audio↔text distance as a "senses disagree" number. *Experimental; gated on CLAP calibration.*
- **A7. Omni models** — deferred; the dual-GPU catch-22 (28 GB pool can't hear; 16 GB CUDA lane fits only 7B-class). Re-check tooling via RQ1; endgame = A1 proposes, omni verifies top-N.

### B. Learning & calibration (goal 3 — June proposals, untouched; detail: [[concepts/plan-calibration-loop]], [[concepts/plan-decorrelate-judges]])
- **B1.** Cache Pass B raw (~30 min) → **B2.** offline re-scorer (~2 h) → **B3.** grid-search fitter → `selection_axes_fitted.json` (~3 h) → **B4.** logistic/log-space ranker (<1 s CPU train) **+ interaction features** (`motion_high × words_banal` — where workstream A's features enter the learned layer).
- **B5.** Judge decorrelation — `text_model_passd` on Gemma 4 (~2 h, independent).
- **B6.** Outcome labels — `posted.log` (clip → treatments → flagged?/views); joins Twitch-clip labels. (Feeds goal 1 measurement too.)
- **B7.** DPO/QLoRA on Pass B — last resort, only after B1–B4 prove value.

### C. Originality levers (goal 1 — DEPRIORITIZED by owner decision 2026-07-03)
> [!note] Owner decision (2026-07-03): voiceover stays available but unused
> Most TikTok/IG clips get engagement without VO; the owner wants to **maximize clipping quality (detection/selection) instead**. `tts_vo` (Piper) remains a dashboard option, default OFF, likely unused. Workstream **A (perception/fusion) is now the headline**; C items stay documented as options.
- **C1.** Wire `eq_tilt_db` into the filter graph (~30 min — value already computed at `style_profiles.py:331`).
- **C2.** Seed + enable `music_bed` (optional); **C3.** `tts_vo` — **available, off, deprioritized per above**; **C4.** CC0 assets for the new SFX kinds; **C5.** account hygiene (operator workflow).

### D. Coverage ([[concepts/plan-youtube-informative]], untouched)
- **D1.** Storytime length fixes (45 s truncation, 90 s cap); **D2.** `informative` category; **D3.** yt-dlp ingest; **D4.** micro-clip render path (8–15 s solo beats — pairs with A1's proposals).

### E. Forensics extensions ([[concepts/plan-clip-forensics]] shipped; refinements)
- **E1.** CLAP threshold calibration vs `.notes.json`; **E2.** Phase 4a exact-SFX — **seed library DONE 2026-07-02** (`reference_clips/sfx_reference/`, 14 sounds, analysis-only); remaining = audfprint/cross-correlation wiring; **E3.** style-profile → `sfx_cues.json`/`style_profiles.py` auto-feed (the render half of "wire it live").

## 4. Dependencies & sequence

```
Phase 0  AUDITS (hours)         LM Studio audio-in check (RQ1) · real-VOD validation
                                run (SFX/cold-open/arc — clears the 🟡s)
Phase 1  A1 anomaly lane        THE HEADLINE (owner 2026-07-03: maximize clipping
                                quality); unblocks A3/A5, feeds D4
Phase 1.5 A2 chat mining        overlay-OCR design on [[concepts/reference-humor-2026-07]]
                                §A2 mechanics (velocity ROI + burst OCR + lag model)
(optional) C1–C2 originality    music/eq remain available; VO deprioritized per owner
Phase 2  A5 judge upgrade       feed the fused timeline into the Stage 5.5 prompt
Phase 3  B1→B4 calibration      + B5 decorrelation alongside; A-features → B4 interactions
Phase 4  A3 probe + A4 library  understanding layer on top of the detection layer
Deferred A6 · A7 (tooling) · B7 · D3 · E2 (library seeded 2026-07-02; build when wanted)
```

## 5. Deep-research handoff prompts (ready to run)

- **RQ1 — Local omni serving, live status:** *"As of mid-2026: does LM Studio expose audio/video content parts via its OpenAI-compatible API? What is llama.cpp `mtmd`'s audio/video input support matrix (models, formats)? For Qwen2.5-Omni-7B and Qwen3-Omni-30B-A3B: exact 4-bit VRAM incl. encoders, audio/video token rates, and Windows-viable serving recipes (vLLM/WSL/transformers) on a 16 GB RTX 5060 Ti + 12 GB RX 6700 XT rig. Deliverable: go/no-go + recipe."*
- **RQ2 — Anomaly-proposer design + benchmark:** *"Best-known methods for laughter/reaction-anchored comedy-moment proposal from fused symbolic timelines (FunnyNet-W lineage, audio-visual humor detection since 2024): windowing, precision controls, verifier prompt design. Design an eval on ~25 annotated reference clips (notes.json ground truth): symbolic-timeline-35B vs omni-7B on incongruity/reference clips."*
- **RQ3 — Chat mining:** *"Burst/novelty detection in Twitch/TikTok chat for joke-naming (n-gram spikes, emote lexicons); available chat-capture formats for local VODs; mapping bursts → titles/hooks. Include: what to do when no chat sidecar exists."*
- **RQ4 — Meme-format library:** *"Schema + seed sources for a machine-matchable meme/skit-format library (KYM-style taxonomies); embedding-matching thresholds with sentence-transformers; growth loops from clip decompositions."*

## 6. Open questions — ANSWERED by the owner (2026-07-02)

1. **Chat data**: some VODs have chat **overlaid on the stream video** (burned into pixels — streamer-dependent), *not* as a sidecar file; owner will also drop **downloaded YouTube MP4s** into `vods/` (no chat sidecar either). **Consequence — A2 pivots from data mining to VISION mining**: the chat overlay is burned-in text → mine it with the **caption-OCR machinery already built** (`visual_sense.caption_ocr` restricted to the chat-overlay region). Three-tier A2: (a) burned-in overlay → **OCR-region mining** (reuses EasyOCR, no new deps); (b) when a platform export exists (TwitchDownloader chat JSON, yt-dlp live-chat replay) → structured mining as originally designed; (c) neither → skip, rely on the A1 audio/motion proxies (failure-soft). The pipeline's existing chat ingestion (grounding, Stage 6 chat block) expects structured chat — for the owner's actual sources it will usually be absent, so tier (a) is the primary path. Also raises workstream **D**'s priority (YouTube MP4s are coming regardless of the `informative` work).
2. **`tts_vo` source**: clarified for the owner — Stage 6 already returns a `voiceover` line and Piper (local TTS) speaks it, so **LLM-written + Piper-voiced is the zero-effort default**; owner-recorded lines remain the stronger-originality upgrade later. Start with LLM+Piper (C3).
3. **Soundboard library: DONE 2026-07-02** — 14 canonical meme SFX downloaded + ffprobe-validated into `reference_clips/sfx_reference/` (vine boom, bruh, quack, airhorn, record scratch, sad trombone, crickets, applause, boing, whoosh, censor beep, metal pipe, anime wow, oof). **Analysis-only license lane** (myinstants provenance — matching reference, never render assets; see the folder README). **E2 is no longer asset-blocked** — remaining work is the audfprint/cross-correlation wiring itself.

## Related
- [[concepts/evaluation-status-2026-06]] (June done/not-done tracker) · [[concepts/multimodal-fusion-2026-07]] · [[concepts/reference-humor-2026-07]] · [[concepts/plan-calibration-loop]] · [[concepts/plan-decorrelate-judges]] · [[concepts/plan-unoriginality-audio-layer]] · [[concepts/plan-youtube-informative]] · [[concepts/plan-clip-forensics]] · [[concepts/case-incongruity-comedy]] · [[concepts/clipping-quality-overhaul]]
