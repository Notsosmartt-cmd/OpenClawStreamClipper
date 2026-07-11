---
title: "Plan — 'Streamer News Today' compilation mode (third pipeline output)"
type: concept
tags: [plan, news-compilation, multi-vod, stitch, stage7, evaluation]
status: in-progress
updated: 2026-07-11
---

> [!note] v1 SHIPPED 2026-07-11 — first compilation rendered; owner ear-check gate OPEN
> **`scripts/news_compile.py`** implements the finished-clips architecture: per selected VOD it
> joins the newest diagnostics trace (selected candidates: T + final_score) to the newest
> `effects_log` run (clip titles + windows) to the mp4s on disk (normalized-prefix title match,
> same rules as R2), round-robins top stories under the budget (per-VOD guarantee), then renders
> **intro** (2×2 `xstack` grid of story payoff thumbnails + "STREAMERS UPDATE <date>" band + boom
> + piper VO) → **stories** (payoff-centered sub-cuts of the FINISHED clips — captions/SFX/blur
> inherited — + lower-third `STREAMER — title` banner + whoosh + **piper anchor VO** reading the
> title) → filter-concat, uniform encode. Failure-soft VO (missing piper → text-only), bounded
> ffmpeg timeouts, sidecar `clips/post_kits/<name>.news.json`.
> **Piper installed** (`pip install piper-tts`) + voice `en_US-ryan-high` at `assets/piper/`
> (gitignored, 120 MB — re-fetch from HuggingFace rhasspy/piper-voices if missing); synth
> verified 3.2 s natural read. **First live compilation:** 2xRaKai single-VOD →
> `clips/STREAMERS UPDATE 07-11-2026.mp4` (58.9 s, intro + 4 stories, VO throughout); frames
> verified (grid + date card; story banner "2XRAKAI — he really said she killed that shit").
> **Dashboard:** `News Compile (N)` button on the multi-select → `POST /api/news-compile`
> (409 while pipeline runs, bare-metal only, spawn via the pipeline handle so Stop works;
> status-polled — the compiler writes no pipeline.log). Speed-1.0 assumption on payoff offsets
> documented in code. **Gate:** owner ear-checks the piper voice + story pacing on compilation
> #1; v2 items (same-story merge, grid polish) wait on that review.

> [!note] v1.5 (2026-07-11, owner directives after seeing v1)
> **Multi-VOD across streamers is the PRIMARY use** (single-VOD kept). Changes:
> **(1) News-weighted selection** — the standard ranking is comedy-tuned; news re-weights
> `final_score` by category (controversial ×1.35, storytime ×1.2, hype ×1.15, emotional ×1.1)
> so "story times, controversial, or impactful events" surface, plus **category-diversity
> swaps** during round-robin (a story duplicating an already-picked category yields to the
> VOD's next story when it brings a new category at ≥85% of the news score).
> **(2) "News compile after run" toggle** (`chk-news-after` → `CLIP_NEWS_AFTER`) —
> `run_pipeline` now ends single- AND multi-VOD runs with one compilation from the freshly
> clipped VODs (failure-soft, never changes the run's exit code; standalone button kept).
> **(3) A/B compilations** — follows `CLIP_AB_VARIANTS` (default on): version B swaps each
> story to its `(B)` clip render where one exists (different hook + SFX baked in) + rotates
> the whoosh/boom draw; skipped with a log when no story has a B (never an identical dupe).
> **(4) Reorg-proof lookup** — the owner moves finished clips into subfolders (2xBvnks/741/…)
> and off-repo after posting; clip lookup is now RECURSIVE root-first (post_kits/.diagnostics
> skipped; STREAMERS UPDATE outputs never story sources), and vanished sources log clearly
> ("re-clip the VOD to compile from fresh sources"). A fresh 2xRaKai clip run with
> `CLIP_NEWS_AFTER=1` exercises all of it end-to-end (also the R4 lever verification run).

# Evaluation + plan: the "news today" compilation mode

Owner request (2026-07-11), triggered directly by the R1 attribute cards: the three
StreamerUpdate reference cards ([[concepts/plan-reference-deconstruction-2026-07]]) decoded a
distinct FORMAT — multi-story, news-ticker-style compilations — and the owner wants it as a
**third pipeline output mode**: select multiple VODs → one compiled "today this happened"
video, alongside the existing per-VOD clip mode and multi-VOD batch mode.

**This is the first real consumer of the reference-deconstruction loop**: the format spec
below comes from cards, not from the owner having to articulate it — exactly the loop-B
workflow working as designed.

---

## The format spec (measured from the 3 StreamerUpdate cards)

| Attribute | Card evidence |
|---|---|
| Intro | 4-panel grid / split-screen of recognizable faces + bold **"STREAMERS UPDATE \<date\>"** title card — establishes format + urgency instantly |
| Arc | `list` — 3-6 unrelated high-interest stories, rapid-fire ("information density" is the value) |
| Pacing | 9.4–14.1 cuts/30s (avg shot ~2–3s), cut alignment `on-punchline`/`on-beat` |
| SFX | 31–44 events/30s (dense whoosh/boom furniture on transitions + punchlines) |
| Captions | mixed casing, "news anchor / hype-man" voice, informative + fast |
| Per-story edit | highlight-reel: only the peak of each story, headline text anchors each segment |
| Essence | "pack multiple stories into one video, maximize information density" |

## Reuse map — ~75% of this already exists

**Needs NO new work (per-VOD side):** multi-VOD selection (`--vods a,b,c` + dashboard
multi-select), Stages 1–6 per VOD (discovery → transcription → segments → moments → judge →
titles) already produce scored, vision-judged, titled moments per VOD — and for
already-processed VODs every stage is CACHED, so a news compile over yesterday's processed
streams costs almost nothing but the compile itself.

**Existing machinery the compile stage reuses:** `stitch_render.py` (multi-segment concat with
the full blur-fill/caption chain — currently intra-VOD, needs cross-VOD input paths); P-TIGHT +
companion-short payoff-centering logic (tighten a 45s moment to a 10–25s story segment);
transitions (`clip_cuts` whoosh/white-flash — the on-punchline cut furniture); SFX anchors +
`sfx_cues.json`; Stage-5 frames (the intro grid's face panels already exist on disk); Stage-6
title machinery + caption gate (headline text per story, with a news-anchor voice-contract
variant); `piper_vo.py` TTS (optional anchor narration, Wave D); post kit (compilation-level
post text).

**Genuinely new (the real build, ~25%):**
1. **Story selection across VODs** — top-1..2 moments per selected VOD by `final_score` under a
   total budget (target 60–120s), with a per-VOD guarantee (≥1 story per selected VOD) so the
   compilation actually covers "today". Small module over already-scored moments (knapsack-ish,
   no new detection).
2. **Per-story segment render** — payoff-centered 10–25s cut + a lower-third HEADLINE overlay
   ("<streamer> did X") + source audio; the companion-short sub-cut pattern applied per story.
3. **News wrapper** — intro card (ffmpeg `xstack` 4-panel grid from per-VOD Stage-5 frames +
   date title), whoosh/flash between stories, outro; concat via the stitch pattern.
4. **Mode entry** — `run_pipeline.py --news-compile` (uses the same `--vods` list) + a third
   dashboard button ("News Compile (N)") wired like Clip Selected; compile output lands in
   `clips/` as one video + its post kit.
   > [!note] Owner directive (2026-07-11): this is a **separate, explicit button acting on the
   > multi-select — NEVER part of the standard clipping flow**. Pressing Clip Selected/Clip All
   > must never produce a compilation; no default-on promotion path exists for this mode — it
   > is press-to-run by design.

## Design questions for the owner (defaults proposed)

1. **Story sourcing (v1 default: reuse detection as-is).** The detectors are comedy/highlight
   -tuned; true "news-worthiness" (announcements, drama, events) is a different scoring axis.
   v1 = "today's best moments" compilation (zero new detection risk); a later v2 can add a
   news-weighted scorer (controversy/announcement patterns) if the v1 story mix feels off.
2. **Target length**: 60–120s (3–6 stories × 10–25s)?
3. **Narration — ANSWERED by owner (2026-07-11): piper TTS anchor narration is IN, and the
   news mode is its flagship home.** The pipeline has carried piper (Wave D, `piper_vo.py`)
   dormant for weeks; a news-anchor format is the one output where a synthetic narrator VOICE
   is native to the genre rather than a tell. v1 builds anchor lines per story ("Streamer X
   got hit with Y today") read by piper over the story's intro beat, ducked under the source
   audio at the payoff. Text headline cards remain as the fallback layer (and stay burned in
   regardless — the VO complements, never replaces them). Gate: owner EAR-CHECK of the first
   compilation before the voice is kept — if piper's local voice reads too robotic for the
   anchor role, we fall back to text-only and revisit voices.
4. **Same-story merging** (v2+): two streamers reacting to the same event should become ONE
   story with both angles — needs cross-VOD topic matching (embedding similarity over story
   transcripts, frozen embedder). Deferred; v1 treats VODs independently.

## Risks (honest)

- **Comedy-tuned selection** may surface punchlines over "news" — accepted for v1 (see Q1);
  the owner's review of compilation #1 is the calibration signal.
- **Faces/branding polish**: the competitor grid is hand-designed; our v1 grid (xstack + title)
  will be plainer. Iterate via the R3 diff once compilation cards exist (dogfood: run
  attribute_cards on OUR compilation and diff against the StreamerUpdate cards — the loop
  measures its own gap).
- **Multi-VOD wall-clock** only matters for UNprocessed VODs (each needs its normal Stage 1–6
  pass first; C1 prefetch + caches already amortize this). Over processed VODs the compile is
  minutes.
- **Rights surface**: unchanged from current practice (same streamers, same source VODs).
- **Piper VO in v1** (owner directive): adds TTS synthesis + audio-duck mixing to the v1 build
  (was v2) — modest scope increase, machinery exists (`piper_vo.py` + the Stage-7 VO mix path);
  the ear-check gate keeps it reversible to text-only.

## Effort + sequencing

**v1 ≈ 1–2 sessions** (selection module + story segment render + wrapper + mode entry + dashboard
button). Best sequenced AFTER R2/R3 land so the news mode's pacing/caption numbers are pulled
from the reference cards rather than hand-set — but nothing hard-blocks building it first if
the owner wants to jump the queue. v2 items (VO narration, news-weighted scoring, same-story
merge, grid polish) gate on v1's owner review.

Related: [[concepts/plan-reference-deconstruction-2026-07]] (format spec source + the dogfood
diff), [[concepts/clip-rendering]] (stitch/companion-short patterns), 
[[concepts/hook-engineering-2026-06]] (cold-open/headline furniture),
[[concepts/sfx-cue-taxonomy-2026-06]] (transition SFX), [[entities/piper]] (v2 narration),
[[entities/dashboard]] (third button).
