---
title: "Originality + Calibration Evaluation — Consolidated Status (2026-06)"
type: concept
tags: [evaluation, status, hub, originality, calibration, roadmap, tracker]
sources: 0
status: in-progress
updated: 2026-06-13
---

# Originality + Calibration Evaluation — Consolidated Status

One-file compilation of the 2026-06-12 deep evaluation (the 9-section "originality + calibration" response), its incongruity-comedy addendum, and the three deep-research deep-dives — **plus a verified done / not-done audit** as of 2026-06-13. This is the single tracker; each row links to the page that holds the detail.

> [!note] Where the original responses live (they ARE saved)
> The evaluation was filed as focused pages, not one dump:
> - **§1 unoriginality** → [[concepts/plan-unoriginality-audio-layer]]
> - **§2 reference clips + §7 anomaly lane** → [[concepts/case-incongruity-comedy]] (and the generalization note on [[concepts/case-rap-battle-missed]])
> - **§3 calibration** → [[concepts/plan-calibration-loop]]
> - **§4 decorrelation** → [[concepts/plan-decorrelate-judges]]
> - **§5 storytelling/YouTube** → [[concepts/plan-youtube-informative]]
> - **§6 prompt-engineering backlog** → [[concepts/clipping-intelligence]] §Opportunities + the shipped items below
> - **§8 removal** → [[concepts/bugs-and-fixes]] (REMOVAL 2026-06-12) + tombstoned [[entities/self-consistency-module]]
> - **§9 research prompts** → [[concepts/tiktok-originality-mechanics-2026-06]], [[concepts/sfx-cue-taxonomy-2026-06]], [[concepts/hook-engineering-2026-06]]

Legend: ✅ shipped & code-verified · 🟡 partial / needs a real-VOD validation run · ⬜ not started.

---

## Headline verdict (read this first)

**The work that shipped is real and well-tested, but the user's actual goal — stop the TikTok "unoriginal" flag — is only PARTIALLY addressed, and arguably not yet by its strongest lever.**

- What shipped is mostly the **detection/quality + Tier-B engagement** layer (prompt-engineering backlog, the SFX cue taxonomy, hook-card templates, cold-open teaser, the orphan removal).
- The **TikTok-mechanics research itself concluded the strongest originality levers are voiceover/commentary + music** (additive audio that defeats the fingerprint), and **those are still OFF** (`tts_vo: false`, `music_bed: ""`, `eq_tilt` unwired). SFX cues are explicitly Tier-B (engagement), not the fingerprint fix.
- The **structural fix for the missed competitor clips** (the anomaly-proposer lane in [[concepts/case-incongruity-comedy]]) is **not built** — those clips would still be missed.
- The two highest-leverage *intelligence* upgrades — **calibration loop** and **judge decorrelation** — are **not started**.

So: good progress on quality + engagement scaffolding; the originality root-cause (audio) and the learning ceiling (calibration) remain open.

---

## §1 — Unoriginality / audio layer  ([[concepts/plan-unoriginality-audio-layer]], page status: planned → really PARTIAL)

| Item | Status | Evidence / note |
|---|---|---|
| P1.1 Punchline-anchored SFX | ✅ | `scripts/lib/sfx_cues.py` + `config/sfx_cues.json`; spliced in `profile_render.py` (gated `CLIP_SFX_ANCHOR`, default ON; active because runtime `style_profiles: true`). Per-kind `gain_db` mix; boom rides hot. |
| — boom plays today | ✅ | `assets/sfx/boom/library.json` aliases the impact library |
| — other new kinds (scratch/sad_trombone/sad_violin/crickets/applause/boing/pop/bruh) have assets | 🟡 | In `VALID_SFX_KINDS` + beat_defaults, but **no CC0 assets seeded** → they fall through to a seeded kind (e.g. fail→whoosh) until added |
| P1.2 **Music bed default-ON** | ⬜ | `music_bed: ""` (off). Machinery (`music_pick.py`) exists; not enabled, asset seeding unverified |
| P1.3 **Piper TTS voiceover (the research's #1 lever)** | ⬜ | `tts_vo: false`. Hook *text* templates shipped (≠ spoken VO). This is the strongest unoriginality lever and it's off |
| P1.4 Wire `eq_tilt_db` (firequalizer) + bolder audio micro-treatment | ⬜ | `style_profiles.py:331` computes it; **never applied** to a filter graph |
| P2 jump_cuts → `gaps` by default | ⬜ | `jump_cuts: "off"`; pending the clean validation run |
| P2 **Cold-open teaser** | ✅ | `scripts/lib/cold_open.py` + Stage 7 `_maybe_cold_open` (gated `CLIP_COLD_OPEN`, default OFF, dashboard toggle), integrity-probe before `os.replace` |
| P3 zoom-punch on punchline / meme-broll / camera-pan | 🟡 | All exist in profile mode; zoom punch **not yet anchored** to the acoustic cue; meme/broll asset-gated; camera-pan off |
| P4 account hygiene (caption/hashtag variety, no burst-post, appeals) | ⬜ | Outside the pipeline — operator workflow |
| P5 `posted.log` outcome measurement | ⬜ | Not built; would feed the calibration loop |
| (bonus) hook-text templates | ✅ | `config/hook_templates.json` + `stage6_vision.py` fallback |

---

## §2 + §7 — Reference clips + anomaly-proposer lane  ([[concepts/case-incongruity-comedy]], status: planned)

The structural fix for the cross-channel-incongruity blind spot (ReemKnocks bus/George-Bush, Delaware rap battle). **None built.**

| Item | Status |
|---|---|
| Repeated-phrase Pass A signal ("performing a bit") | ⬜ |
| Prosody-anomaly proposer + targeted Pass B verify (`src=ANOMALY`) | ⬜ (verified: no anomaly proposer in code) |
| Motion-spike lane (frame-diff vs baseline) | ⬜ |
| Micro-clip render path (solo 8–15 s beat) | ⬜ |

---

## §3 — Calibration loop  ([[concepts/plan-calibration-loop]], status: planned)

Still ~30% = only the pre-existing pieces. **Not started.**

| Item | Status |
|---|---|
| `bootstrap_twitch_clips.py` label tool | ✅ pre-existing |
| Per-moment multipliers stamped + `pass_c_candidates.json` | ✅ pre-existing |
| Cache Pass B raw output (the missing artifact) | ⬜ |
| Offline Pass C/D re-scorer | ⬜ (no fitter/re-scorer in `scripts/`) |
| Grid-search fitter → `selection_axes_fitted.json` | ⬜ |
| VOD↔video_id mapping | ⬜ |
| Log-space/logistic ranker; DPO/LoRA | ⬜ |

---

## §4 — Judge decorrelation  ([[concepts/plan-decorrelate-judges]], status: planned)

**Not started** (verified: `config/models.json` has only `text_model_passb`/`vision_model_stage6`, no passd/judge keys).

| Item | Status |
|---|---|
| `text_model_passb` / `vision_model_stage6` overrides | ✅ pre-existing |
| Add `text_model_passd` + `vision_model_judge` keys | ⬜ |
| Thread through `stage4_rubric.py` / `stage5_5_judge.py` | ⬜ |
| Run Pass D on Gemma 4 12B | ⬜ |

---

## §5 — Storytelling + YouTube  ([[concepts/plan-youtube-informative]], status: planned)

**Not started** (verified: no yt-dlp ingestion, no `informative` category).

| Item | Status |
|---|---|
| Length-neutral storytime scoring | ✅ pre-existing (`CLIP_LENGTH_NEUTRAL`) |
| No-boundary 45 s truncation fix | ⬜ |
| Narrative-group 90 s cap fix | ⬜ |
| Vision setup-frames for >90 s stories | ⬜ |
| `informative` category + keywords/weights | ⬜ |
| `--source youtube` profile + yt-dlp ingestion | ⬜ |

---

## §6 — Prompt-engineering backlog  ([[concepts/clipping-intelligence]] §D)

The fully-shipped section. ✅✅✅✅✅

| Item | Status |
|---|---|
| Word-boundary keyword matching (`CLIP_KEYWORD_BOUNDARY`) | ✅ |
| Per-channel keyword packs (`config/channel_keywords.json`) | ✅ |
| Unified prompt config (`config/prompts.json`) | ✅ |
| Segment-classification confidence vote + smoothing (`CLIP_SEGMENT_VOTES`) | ✅ (opt-in) |
| Rare-pattern Pass C bonus (`pass_c_bonus`) | ✅ |
| Arc-guarantee ratio tuning (0.45) | 🟡 needs real-VOD run |
| Arc Phase 3 precision validation | ⬜ needs real-VOD run |

---

## §8 — Removal  ([[concepts/bugs-and-fixes]] REMOVAL 2026-06-12)

| Item | Status |
|---|---|
| Delete `self_consistency.py` (the one true orphan) | ✅ (removed; verified gone) |
| Keep `eval_tier4.py`, speed control, Stage 5.5 judge, + the 6 "live" modules | ✅ honored |

---

## §9 — Research prompts  (all filed; 2 of 3 also implemented)

| Prompt | Filed | Implemented |
|---|---|---|
| #1 TikTok originality mechanics | ✅ [[concepts/tiktok-originality-mechanics-2026-06]] | n/a (informs the plan) |
| #2 SFX cue taxonomy | ✅ [[concepts/sfx-cue-taxonomy-2026-06]] | ✅ shipped |
| #3 Hook engineering | ✅ [[concepts/hook-engineering-2026-06]] | ✅ shipped |

---

## Scorecard

- **Shipped & verified:** 5 prompt-eng items · self_consistency removal · SFX cue taxonomy + per-kind mix · hook-text templates · cold-open teaser · all 3 research papers filed (2 implemented). **~14 items.**
- **Partial / needs validation:** audio-layer plan overall (SFX/hooks/cold-open in, VO/music/eq_tilt/jump-cuts out) · arc-guarantee + arc Phase 3 (need a real-VOD run) · new SFX kinds need CC0 assets.
- **Not started:** calibration loop · judge decorrelation · YouTube/informative + storytime fixes · the incongruity anomaly-proposer lane · `posted.log`.

## Recommended next actions (by leverage, for the stated goal)

1. **Turn on the audio originality levers** — flip `tts_vo` on with real commentary (research's #1 lever) and seed + enable a `music_bed`; this is the part of §1 that actually targets the unoriginality flag, and it's the cheapest high-impact gap. ([[concepts/tiktok-originality-mechanics-2026-06]], [[concepts/plan-unoriginality-audio-layer]])
2. **Decorrelation** (~2 h) — `text_model_passd` + `vision_model_judge`, Pass D → Gemma 4 12B. ([[concepts/plan-decorrelate-judges]])
3. **Calibration loop** (~1–2 d) — cache Pass B raw → offline re-scorer → fitter; the systemic fix the rap battle's Pass-C drop proved is needed. ([[concepts/plan-calibration-loop]])
4. **A real-VOD validation run** — confirm SFX/boom + cold-open render correctly and SFX don't drown speech; exercise arc-guarantee 0.45 + arc Phase 3.
5. **Anomaly-proposer lane** — the structural fix for the missed competitor clips (start with the trivial repeated-phrase signal). ([[concepts/case-incongruity-comedy]])
6. **Storytime fixes + `informative`/YouTube** ([[concepts/plan-youtube-informative]]).

## Related
- [[hot]] — current-state digest · [[concepts/clipping-intelligence]] — the intelligence-layer hub these plans extend
