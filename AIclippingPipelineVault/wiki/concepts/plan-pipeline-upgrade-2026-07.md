---
title: "Plan — Pipeline Upgrade 2026-07 (research → actionable engineering plan)"
type: concept
tags: [plan, engineering, anomaly-proposer, chat-mining, calibration, timeline-fusion, meme-library, roadmap]
sources: 0
status: planned
updated: 2026-07-03
---

# Plan — Pipeline Upgrade 2026-07

The **actionable engineering plan** converting [[concepts/master-research-2026-07]] (RQ1-4 findings) + [[concepts/master-proposal-2026-07]] (workstreams/decisions) into build phases — and closing out the 🟡 in-progress/awaiting-validation states. Written to the same standard as the clip-forensics engineering prompt (which shipped cleanly in 3 sessions).

**Conventions binding every phase** (repo law): flag-gated (default OFF), failure-soft (missing dep/model → prior behavior, never crash), boost-only (new lanes ADD candidates/signal, never gate or displace existing ones), storytime/blatant lanes untouched, wiki + commit per phase, every heavy run bounded (watchdog/timeout — no zombie tasks).

---

## Phase 0 — Close the in-progress states + de-risk (half day; no new features)

**0.1 Real-VOD validation run** — the single run that clears most 🟡s. On a real VOD (ideally rakai's, for the Delaware-battle check) with `style_profiles:true`, cold_open ON, arc_stitch ON, `CLIP_SEGMENT_VOTES=3`:
- SFX: boom fires at payoff, rides at speech level, other SFX duck under speech (no drowned dialogue) — [[concepts/sfx-cue-taxonomy-2026-06]]
- Cold-open: teaser seam clean (flash+whoosh, no white-hold regression à la BUG 64) — [[concepts/hook-engineering-2026-06]]
- Arc: `CLIP_ARC_GUARANTEE_MIN_RATIO=0.45` actually seats an arc; `[GROUPS]` logs confirm — [[concepts/arc-aware-extraction]]
- Detection fixes: word-boundary keywords recall OK; rare-pattern bonus → does the rap battle win its bucket? — [[concepts/detection-improvements-plan]]
- **Exit:** update each page's status (in-progress → shipped/validated, or file BUGs); refresh [[concepts/evaluation-status-2026-06]] rows.

**0.2 RQ1 smoke test (one command, ~30 min):** Vulkan llama.cpp build → `llama-server -hf ggml-org/Qwen3-Omni-30B-A3B-Instruct-GGUF` → POST a short audio clip to `/v1/chat/completions`. Expect possible AMD mmproj FPE (→ retry `--no-mmproj-offload`) and possible 400 on audio content parts (→ try the native `llama-mtmd-cli` shape). **Exit:** A7 verdict recorded (go → Phase 6 unlocks; no-go → stays deferred, zero loss).

**0.3 Verification debt:** resume `wf_edb4d979-c18` (post-reset) so the adversarial panel votes on the 114 claims; upgrade [[concepts/master-research-2026-07]] markings.

## Phase 1 — A1: Event timeline + anomaly-proposer lane (keystone; ~1.5–2 days)

**1a. `scripts/lib/event_timeline.py`** — `build_timeline(vod_or_clip, t0, t1)` merges into one time-ordered symbolic stream: transcript words (existing), `audio_sense.sense_events` (CLAP — laughter/cheering emphasized), `visual_sense.motion_events`, cuts, (Phase 2 adds CHAT). Serialize to `{work}/timeline.json`; render-to-prompt helper emits the `[t=6.2] AUDIO … | MOTION … | TEXT …` format. *Prosody stats (SMILE's pitch/jitter/shimmer) are a stretch goal — librosa is env-fragile here (onset hang precedent); only via a bounded pure-numpy pitch proxy, else skip.*

**1b. `scripts/lib/anomaly_propose.py`** — research-parameterized:
- **Windows: 8 s, stride 2 s** (FunnyNet ablation), scored per window: `reaction = CLAP laughter/cheer + motion-spike energy (+ chat velocity later)`; `explained = Pass-A keyword score`; **anomaly = high reaction × low explained**, deduped against existing Pass A/B moments (≥45 s gap rule respected).
- **Precision controls:** top-K cap per VOD (start K=6), min-reaction floor, and a **mandatory LLM verifier** — `lmstudio.chat` with a **few-shot prompt (3–5 exemplars incl. a bus-clip-style positive and 2 negatives)**; research shows few-shot is load-bearing (71.1 vs 14.5 F1). Verifier returns keep/kill + category + why.
- Survivors enter Pass C as `src=ANOMALY` candidates with a bounded score (boost-only; can win a bucket, can't evict guaranteed picks).
- **Flag:** `CLIP_ANOMALY_LANE` (default OFF). Stage 4 integration point: after Pass B+, before Pass C.
- **Verification:** (i) unit tests on synthetic windows; (ii) laughter-anchored auto-eval — FunnyNet's trick: windows preceding detected laughter = free positives; measure lane recall on them across one VOD; (iii) flag-off run byte-identical to baseline.

## Phase 2 — A2: Chat-overlay mining (~1–1.5 days)

**`scripts/lib/chat_mine.py`**, flag `CLIP_CHAT_MINE` (default OFF), output `{work}/chat_events.json`:
- **Auto-ROI (default, owner decision):** EasyOCR detection-only over ~15 sampled frames → persistent small-text cluster heatmap → chat box; **no cluster = no chat = clean skip** (the has-chat test). Optional per-channel override config.
- **Velocity track:** ROI frame-diff at 2–4 fps → `{t, velocity}` (feeds Phase 1's reaction score).
- **Burst OCR:** at bursts + candidate windows, OCR `[T−2, T+20]` @ ~1 fps; consecutive-sample diff keeps NEW lines; **Levenshtein dedup ~80/100, per-word confidence floor ~75** (videocr-proven parameters, ported to EasyOCR); **char-level n-gram burst extraction** (chat text breaks word tokenizers, +22.3 F evidence).
- **Lag:** seed **7 s forward** (EMNLP-2017 sweep); auto-calibrate per channel by cross-correlating CLAP laughter × velocity; attribute bursts backward (B → B−lag).
- **Consumers:** velocity → anomaly lane; burst n-grams → Stage 6 context + title material + CHAT track in the timeline.
- **Verification:** run on one chat-overlay VOD (owner has some) + one no-chat YouTube MP4 (must cleanly skip).

## Phase 3 — A5 + A3: Judge timeline + known-format probe (hours)

- **A5:** append the clip's timeline excerpt (named audio + motion + chat bursts) to the Stage 5.5 judge's existing frames+transcript prompt (`vlm_judge._clip_text_block`), flag `CLIP_JUDGE_TIMELINE` (default OFF).
- **A3:** add to judge/Stage-6 prompts: `known_format:{name,confidence}` + wordplay check (spoken word ↔ seen object — "George" + bush), few-shot. Nulls tolerated; output threads into title/hook when confident.
- **Verification:** judge tournament unchanged flag-off; spot-check on the two reference cases via the forensics tab.

## Phase 4 — B: Calibration loop + decorrelation (~2 days, CPU only)

Per [[concepts/plan-calibration-loop]], now with research additions:
- **B1** cache Pass B raw pre-Pass-C (~30 min) → **B2** offline re-scorer CLI (~2 h) → **B3** grid-search fitter → `selection_axes_fitted.json` (~3 h) → **B4** logistic/log-space ranker (<1 s train) **+ interaction features** (`motion_high×words_banal`, `reaction×low_keyword`, anomaly-lane features once Phase 1 lands).
- **Labels:** `bootstrap_twitch_clips` triples + the RQ3 bonus trick — align community highlight reels to VODs (color-grid template matching) for free positives.
- **B5 decorrelation** (independent, ~2 h): `text_model_passd` → `google/gemma-4-12b-qat` (already in LM Studio), thread through `stage4_rubric.py`/`stage5_5_judge.py`.
- Fitted weights load only when the file exists; hand-tuned constants remain the fallback.

## Phase 5 — A4: Meme-format library (~1 day + ongoing curation)

- `config/meme_formats.json` — CM50-derived schema: `{name, aliases, verbal_trigger, visual_signature, audio_cue, about, examples[]}`; seed ~20 owner-curated formats (George Bush first), grown from `.notes.json` + forensics decompositions.
- **Matching:** sentence-transformers embeddings (already in repo) with **per-format thresholds = median distance of the format's own examples** (global fallback), **precision-first**; classical/embedding match decides, the LLM probe only *names/explains*. Do NOT use joint CLIP-style embeddings or naked-VLM matching (both underperformed in the literature).
- KYM data: scrape-yourself if ever needed (no redistribution — commercial caution); not required for v1.
- **Verification:** George Bush + 2 negatives match correctly at threshold; false-positive rate on 10 random clips ≈ 0.

## Phase 6 (conditional on 0.2 GO) — A7: Omni verifier (~1 day)

`llama-server` (Vulkan pool) as a **swap-in judging phase**: after Pass C, send top-N 15–30 s windows (audio + a few frames — video-token costs cap window length) to Qwen3-Omni for a second opinion folded in as a bounded reweight (Stage 5.5-style). Never co-resident with the LM Studio model — explicit load/unload phase like Whisper. If 0.2 = no-go, skip with zero plan impact.

---

## Order, effort, and definition of done

| Phase | What | Effort | Unlocks |
|---|---|---|---|
| 0 | Validation run + smoke test + verify-resume | ~½ day | clears all 🟡s; A7 verdict |
| 1 | Timeline + anomaly lane | 1.5–2 d | the missed-clip classes; feeds 2/3/4 |
| 2 | Chat mining | 1–1.5 d | reference naming + reaction signal |
| 3 | Judge timeline + format probe | hours | better ranking/titles |
| 4 | Calibration + decorrelation | ~2 d | measured scoring (the ceiling-raiser) |
| 5 | Meme library | ~1 d | post-cutoff/niche formats |
| 6 | Omni verifier (conditional) | ~1 d | perception-level second opinion |

**Definition of done, every phase:** flag default OFF · flag-off run identical to baseline · failure-soft verified (kill a dep, confirm graceful) · bounded runtime (no unbounded background tasks) · real-media verification recorded · wiki pages + log/hot updated · committed.

## Real-VOD gating (which phases need a run) + the batching trick

| Phase | Real-VOD run needed? | For what |
|---|---|---|
| 0 | **YES — it IS the run** | clears the 🟡s |
| 1 | YES (2 runs: flag-off baseline + flag-on) | byte-identical check + lane recall via laughter-anchored auto-eval |
| 2 | YES (1 chat-overlay VOD + 1 no-chat MP4) | ROI auto-detect + clean-skip proof |
| 3 | light | can replay Stage 5.5 on **cached** work-dir artifacts; no full run required |
| 4 | ONE instrumented run to produce the Pass-B raw cache | everything after is offline (re-scorer iterates in seconds) |
| 5 | no | forensics-lane verification on reference clips only |
| 6 | YES (integration run) after the smoke test | bounded reweight sanity |

**Batching trick:** one instrumented run serves three phases at once — Phase 0.1's checks + Phase 4's Pass-B raw cache + Phase 1's flag-off baseline. Plan runs deliberately; each 2-h VOD costs ~45–90 min wall-clock.

## Autonomous execution protocol (phase-runner — designed 2026-07-03)

Owner asked whether the agent can iterate the phases end-to-end — launch its own VOD runs, wait, evaluate, continue — with no human interruption. **Yes, with this architecture** (each element counters a failure mode already observed this month):

1. **Detached pipeline launch — never run the pipeline inside the agent sandbox.** The sandbox killed >30-min in-sandbox processes repeatedly (the zombie-task saga). The repo already has the machinery: the dashboard's `pipeline_runner` spawns detached processes that survive independently (or `POST /api/clip`). The agent launches detached, keeps nothing heavy in-sandbox.
2. **Bounded waiting via marker files.** The orchestrator already writes `pipeline_stage.txt`, `pipeline.log`, `pipeline.done`, pid markers. The agent waits with a background `until`-loop watching **done-marker OR error signatures OR a hard timeout** (silence ≠ success — the filter must catch `[ERROR]`/stall, not just completion), and gets woken by the harness notification. Long waits use scheduled wake-ups rather than hot polling.
3. **Auto-evaluation harness (the key enabler).** A script grades each run machine-readably against the phase's acceptance criteria: diagnostics JSON + `axis_report` (counts, buckets, axis coverage) · **`clip_forensics.py` decomposing the pipeline's own OUTPUT clips** (dogfooding: did the boom fire at the payoff? music span where expected? cold-open flash present as a cut+whoosh at t≈0?) · ffmpeg loudness stats (SFX-vs-speech ratio — "boom drowns dialogue" is measurable) · flag-off byte-comparison for the baseline check. Verdict: PASS / FAIL-with-evidence.
4. **Phase-state on disk** (`{work}/phase_state.json`: current phase, awaiting-run id, criteria, verdicts). Advance on PASS; on FAIL, halt that phase, file the failure in the wiki, continue any independent phase (e.g., Phase 5 needs no runs). State-on-disk makes the loop **resumable across sessions** — a session limit interrupts, the next session reads state and continues; the wiki log is the audit trail.
5. **Known hard limits (honest):** Anthropic **session limits** are the real interrupter (killed the verify layer twice) — pacing via long wake intervals + overnight windows mitigates but can't eliminate; **LM Studio must be up** (agent can check `/v1/models` and `lms load`, but not launch the GUI); **permission prompts** break autonomy unless the run uses pre-approved allowlists; a few checks stay **perceptual** (seam *aesthetics*, humor quality) — the loop logs artifacts for async human spot-check instead of blocking.
6. **Pilot = Phase 0.** The validation run executes exactly this protocol once (detached launch → bounded wait → auto-eval → wiki+commit) before Phase 1 depends on it.

## Related
- [[concepts/master-proposal-2026-07]] (workstreams + decisions) · [[concepts/master-research-2026-07]] (parameter sources) · [[concepts/plan-calibration-loop]] · [[concepts/case-incongruity-comedy]] · [[concepts/reference-humor-2026-07]] · [[concepts/multimodal-fusion-2026-07]]
