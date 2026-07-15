---
title: "Fine-Tuning Round Plan (post-Wave-3, 2026-07)"
type: concept
tags: [plan, quality, reference-lab, sfx, music, gates]
sources: 0
status: planned
updated: 2026-07-15
---

# Fine-Tuning Round Plan (2026-07)

The owner's post-Wave-3 phase ("I will see the final outputted clips and go back for
fine-tuning and modifications"). Compiles every proposed fix / open item as of 2026-07-15,
after the BUG-75 measurement corrections and the music-bed work. Four tracks, ordered.

**Standing owner preferences constraining this round:** keep the current frequent-SFX
density (2026-07-15); channels are non-monetized (license caution moot); no model
training ever; clips reviewed by the owner at the end of changes, not mid-wave.

---

## Track A — Measurement fidelity completion (agent, no LLM needed)

- [ ] **A1. Full reference re-decompose (86 clips)** — bounded ~2–4 h CPU background job.
  Re-runs R0 with everything shipped this week at the SOURCE instead of post-hoc:
  `trim_end="auto"` (outro detection — recovers the 4 real seconds the blanket cut from
  non-outro clips), bruh 0.50 / sad_trombone 0.40 CLAP thresholds (clean `audio_events`
  instead of count-side neutralization), fresh `music_bed` + OCR. Then
  `--refresh-facts` + regenerate the gap report. Cards' VLM editorial (the owner's 35B
  fields) untouched. **Recommended before pulling levers off report numbers.**
- [ ] **A2. Cuts-metric native-vs-added audit (quick, do BEFORE verdicting cuts items)** —
  ours reads 4.07 cuts/30s vs reference 1.98, and ours:gaming reads **10.0**. Same
  ambiguity the music metric had: scenedetect counts IN-GAME camera/scene changes as
  "cuts". Verify against raw-VOD windows (the music-bed A/B method); if confirmed, either
  document the metric as "visual cut rate the video carries" or subtract source-native
  cuts using the raw-VOD baseline. Until then, don't treat the cuts gap as editing advice.
- [ ] **A3. Our-side music ground truth in effects_log** — one field in the render_plan
  log entry (music decision: folder/track/None) so future compares distinguish added vs
  native without raw-VOD A/B. Piggyback on the next render-code change.

## Track B — Gap-report verdicts (owner, ~10 min in the Lab)

The corrected report (`corpus_diff_20260715`, sfx-policy v2 + music column) holds
**25 open items**. Recommendations given the corrections:

| Items | Recommendation |
|---|---|
| `sfx_per_30s` (ALL + per-cat, ours ABOVE the ref floor) | ✗ Not a problem — owner keeps frequent SFX; ref 1.14 is a floor (CLAP under-detects); owner's ear stays arbiter |
| `cuts_per_30s` (ALL, gaming 10.0) | **Hold verdict until A2** — likely source-native game cuts |
| `duration_med` (irl 32 vs 24.6) | Park → resolved by the jump-cuts v2 gate (C3): compression tightens over-running clips |
| `caption_casing` (mixed vs sentence case) | Approve → lands with the P1.5 voice-bank curation (C5, already on the backlog) |
| `chat_overlay_pct` (ref 3–11%, ours 0) | Owner decision: want editor-added chat overlays? Needs a chat dump + render feature (feature-card) |
| `music_bed_pct` per category | Gaming: nothing to do (ours is stream-native). **Story: owner call** — ref runs beds 72.5% full vs our quiet 11%; adding = set `music_bed` to `assets/music/` + per-category pools |
| `coverage:news_compilation` | Already built (news mode) — blocked on the kokoro ear-check gate (C4), not on code |

## Track C — Owner gates (the standing backlog, ordered)

- [ ] **C1. NVIDIA driver update** (595.71 → current) — crash suspect (2× bugcheck 0x50);
  do BEFORE the next multi-hour run. 10 min + reboot.
- [ ] **C2. Review the 15-clip set** (`clips/`, 4 FirstFullAudio + 11 Raud + (B) variants) —
  tag each complaint by stage via [[concepts/quality-leverage-ranking-2026-07]]'s routing
  table; this generates the concrete fine-tuning work-list.
- [ ] **C3. Jump-cuts v2 live gate** — one run with `CLIP_JUMP_CUTS=gaps` + eyeball
  (cut_inference is phase-pinned safe now). Decides the category-gated default flip AND
  the duration gap item.
- [ ] **C4. News gates** — kokoro news-voice ear-check (`STREAMERS UPDATE 07-11-2026.mp4`)
  + the multi-streamer news test (clip a 2nd streamer → select both → News Compile).
- [ ] **C5. Curation backlog (trickle)** — P1.5 voice bank (`caption_style.py
  --review-sheet` → `[x]` → `--ingest-sheet --enable`); labels (negatives + near-misses
  worth most); `.notes.json` drafts (35/36 pending).
- [ ] **C6. Optional: fresh-VOD benchmark** (agent runs on owner's go, ~30–45 min) —
  turns the fresh-3h ≈ 31–40 min projection into a measured number and gives
  caption_judge_multi its first live exercise. Best after C1.

## Track E — Shape-guide distillation (PROPOSED 2026-07-15, owner evaluation requested it; awaits owner go)

Owner question: can the cards' per-category SHAPES (arc, payoff placement, duration, hook
mechanics) improve pipeline detection/inference guides "without implementing a
non-deterministic smaller model that can lead to bias or over-fitting"?

**Yes — via deterministic, reviewable artifacts only** (the no-training doctrine's designed
path). Current corpus already profiles cleanly: irl = instant arcs, payoff ~79% in, ~25s;
story = story-arcs, payoff at the END, ~64s; news = list-shape 5/5; gaming refs run 46s vs
our 34s median. Steps when promoted:
0. **irl SUBTYPE layer first** (owner question 2026-07-15: "should irl be broken up?" → yes,
   as a subtype, NOT a top-level split): 57/86 cards = one bucket = mixture-average profiles
   (rap performances + banter + freakouts + pranks blended; the enum's `rap_freestyle` got 0
   cards — everything collapsed into irl_moment). Add `subtype` to the card prompt (irl:
   banter_roast / prank_public / freakout_overreaction / performance_rap / wholesome /
   other); `category` stays the stable join key for comparisons; distill by subtype only
   where n≥8. Needs a re-card pass (~86 × ~40s on the 35B) — do it right after the A1
   re-decompose since some clips' analysis windows changed (outro auto-trim) and editorial
   fields were grounded on old frames.
1. Aggregate per-category profiles from the (re-decomposed, subtyped) cards → a
   `reference-shape-guide` wiki page — **owner reviews/approves per line** (same flow as
   gap items).
2. Approved lines land as: Pass-B per-category prompt guidance blocks (S4), Pass-D rubric
   wording, per-category duration constants, Stage-6 hook guidance. All git-diffable text.
3. Later: R5 retrieval few-shot (frozen embedder, gated) replaces static exemplars.

**Safeguards (the owner's overfitting concern is valid — for prompts too, not just models):**
sample-size floor (distill only n≥8 categories — controversy n=2 / street_interview n=1 are
noise); SOFT priors only ("typically…"), never candidate-rejection rules (protects recall);
owner gate per line; Lab re-measure after apply; grow corpus diversity before trusting niche
categories. Known gap: reference side has NO diarization → conversational turn-taking shape
is unmeasured (addable: the pipeline's diarizer works on 30s clips).

## Track D — Parked / deferred (no action this round unless promoted)

R5 retrieval few-shot (`CLIP_REF_FEWSHOT`, gated); disbelief/fail beat wiring — pure
`category_beats` config, pools stocked (bruh 5-deep, meme_scream, fry_timer) — the
**roast-cadence** beat is the natural first wiring; fry_timer promotion (move up a beat
list); spec-decode (single-card only, measured 8× regression on dual-GPU); memtest only
if another crash after the driver update.

## Order of operations

1. **C1 driver update** (owner, first — machine safety)
2. **A1 re-decompose** (agent, background — CPU-only, can run during anything)
3. **C2 clip review + Track B verdicts** (owner, while A1 runs) — but hold cuts items for…
4. **A2 cuts audit** (agent, quick) → then finish B verdicts
5. **C3 jump-cuts gate** → resolves duration items
6. **C4 news gates**, **C5 curation** trickle
7. **C6 benchmark** whenever convenient after C1

## Related
- [[concepts/bugs-and-fixes#BUG 75]] — why the report numbers changed this week
- [[concepts/reference-lab]] — measurement policy (sfx v2, caption dedup, music-bed, outro)
- [[concepts/quality-leverage-ranking-2026-07]] — the complaint→stage routing for C2
- [[concepts/plan-jump-cuts-v2-2026-07]] — C3's gate
- [[concepts/plan-news-compilation-2026-07]] — C4's gate
