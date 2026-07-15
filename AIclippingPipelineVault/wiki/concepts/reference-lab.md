---
title: "Reference Lab — the owner's guide (how it works, end to end)"
type: concept
tags: [reference-lab, dashboard, guide, reverse-engineering, cards, gap-report, learning]
updated: 2026-07-13
---

# Reference Lab — how it works

The Reference Lab is the **reverse of the normal pipeline**. The Clipper takes one VOD and
produces clips; the Reference Lab takes a pile of *other creators'* finished clips and produces
a **description of what makes them work**, then measures how OUR clips differ and hands the
owner a fix list. The point: the owner stops having to articulate "I want more sound effects
like that guy does" — the machine reads the reference clips and articulates it itself.
(Engineering plan + build history: [[concepts/plan-reference-deconstruction-2026-07]].)

## What you see

- **Reference Clips** — a VOD-Library-style table of everything in `reference_clips/`:
  checkbox per row, select-all header, row-click toggles. Status per clip:
  `✓ analyzed · <category>` or `not analyzed`; a **card** button opens the clip's style card.
- **Reference Controls** — a clip-run dropdown, an **Analysis model** dropdown, and three
  buttons: **Analyze Selected (N)** · **Analyze New** · **Compare → Gap Report** (+ Stop).
- **Gap report** — plain-language findings with **✓ Fix it / ✗ Not a problem** per row, and a
  **Copy judged report** button in the header.
- **Card detail** — the selected clip's style card (hook mechanic, arc, comedy device, pacing
  numbers, caption voice, "what to copy" paragraph, raw JSON).

## The two actions (each = one bounded background job)

**Analyze** (`reference_analyze.py`) — per clip, two chained steps:
1. *Decompose* (`clip_forensics`, **CPU-only, no LLM**): CLAP hears the sound effects, Whisper
   transcribes, PySceneDetect finds cuts, cv2 finds motion punches, EasyOCR samples burned-in
   text → `reference_clips/.cache/<stem>.timeline.json`. Skipped when already done.
2. *Attribute card* (`attribute_cards`, **one vision-LLM call**): 8 time-ordered frames + the
   transcript + the timeline facts → an editorial card. The model **reads on-screen text from
   the frames itself** (EasyOCR garble poisoned an earlier voice-learning attempt); the hard
   numbers (cuts/30s, sfx/30s, caption wps) are computed in Python and merged in — the LLM only
   writes the editorial fields. → `reference_clips/.cache/<stem>.card.json`.

Selection semantics: **Analyze Selected** rebuilds cards for exactly the checked clips
(decompose only if missing); **Analyze New** touches only clips with no card yet — the
"I dropped new competitor clips into the folder" button.

**Compare → Gap Report** (`reference_compare.py`) — pick one of OUR past clip runs, press it:
1. Cards our clips the same way (missing-only; run-scoped cache
   `clips/.diagnostics/cards/<run>/`) — with a twist: our cards merge **`effects_log` ground
   truth** (we logged exactly which SFX/effects we injected, so our side is exact, not
   inferred).
2. Diffs the two card sets per category (`corpus_diff`): every metric where ours is >25% off
   the reference median becomes a finding, pre-tagged with the **config lever** it maps to
   (`sfx_cues.json`, style profiles, hook templates, caption bank, …) or `feature-card` when no
   lever exists. Output: `corpus_diff_<date>.md` + `.json`.

## Which LLM it uses (and the model dropdown)

Yes — the Lab uses an LLM exactly like the pipeline: **one vision call per card** + **one text
call for the report narrative** (decompose is CPU-only). Both resolve through
`clip_forensics._llm_config()`: `CLIP_TEXT_MODEL` env → `config/models.json::text_model` → so
**by default the Lab uses the same model the Clipper uses**. The **Analysis model** dropdown
(2026-07-13, owner req) lists the models LM Studio has loaded (same source as the Models
panel) and overrides the model **for that job only** via a job-scoped `CLIP_TEXT_MODEL`;
"pipeline default" = no override. Live-verified: a card rebuilt under `qwen/qwen3.5-9b`
recorded `_model: qwen3.5-9b` — and notably re-categorized the clip (`story` vs the 35B's
`irl_moment`), a concrete demonstration that model choice changes card judgment. Each card
stamps its `_model` for provenance.

## Under the hood: why decompose is CPU-only

Both Lab entry points hardcode `device="cpu"` (`reference_analyze.py:86`,
`decompose_corpus.py:70`) — deliberate, three reasons:
1. **The GPU is already spoken for.** The same Analyze job's very next step is the card call,
   which needs the 35B resident in LM Studio — and LM Studio's Vulkan pool spans BOTH cards,
   including the 16 GB NVIDIA card that is the *only* device torch/CUDA can see. CUDA
   CLAP/whisper/EasyOCR would fight the served model on that same card (OOM or unload/reload
   thrash inside one job). CPU decompose = LM Studio never has to unload.
2. **Windows-CUDA stability.** CUDA checkpoint loads can hang whisper/PANNs (in-code warning
   at `clip_forensics.py:567`; the panns torch≥2.9 deadlock landmine) — an uncatchable stall
   is fatal to an unattended background job. CPU is the always-finishes path; R0 even
   survived a mid-run PC crash and resumed off cached timelines.
3. **The economics don't need a GPU.** Decompose is a once-per-clip *cached* cost (~1–3 min
   on the i9) over 20–60 s clips. Contrast the main pipeline, where whisper DOES run CUDA —
   but on multi-hour VODs, and before the LLM stages need the card.

Escape hatch: the `clip_forensics.py` CLI has `--cuda` for one-off runs; the Lab never passes it.

## How gap items are minted — and their limits

Gap items are **100% deterministic Python** (`corpus_diff.py`); the LLM writes only the
narrative prose, and its prompt is fenced with "Do not invent metrics not shown". Three rules:
1. **Numeric** — 7 hardcoded metrics (`sfx_per_30s`, `cuts_per_30s`, `zooms`,
   `pct_text_hook`, `caption_wps`, `chat_overlay_pct`, `sfx_offset_ms`): ours ≥25%
   relative off the reference median → item. Compared at ALL scope, plus per category
   where both sides have ≥2 cards.
2. **Categorical** — `caption_casing`, `cut_alignment`: item when the top-1 label differs.
3. **Coverage** — any reference category (≥3 cards) we produce zero clips in →
   "missing format" item, lever `feature-card`.

**No count cap** — every breach becomes an item — but the *kind* vocabulary is bounded by
rules × categories. What's open vs closed:
- **Open (no code change needed):** the label space. Categories and categorical values are
  LLM-authored card strings, so a new format appearing in the reference corpus automatically
  mints a new coverage item — that is exactly how `news_compilation` surfaced and became the
  news mode.
- **Closed (needs a small code change):** new *measurable* metrics. A new phenomenon must be
  captured by decompose/cards, aggregated in `_agg`, added to `_cmp`'s metric tuple, and
  mapped in `_LEVERS`. The narrative can *mention* an unmeasured pattern and the cards'
  free-text (`what_to_copy`) can carry it, but it gets no item id, no verdict buttons, and
  no run-to-run tracking until promoted into the metric set.

Closed-by-design: items must be reproducible for approve → apply → re-run → **re-measure**
to mean anything (0.88→5.36 works only because the metric is identical run to run), and per
the no-training doctrine the measurement vocabulary grows by curated reviewed commits, not
by the model rewriting its own rubric.

## Measurement policy (sfx v2 + caption dedup + outro auto — BUG 75, 2026-07-15)

Three counting rules every report since 2026-07-15 uses (the md footer discloses them):

- **Reference SFX = editor-SFX-like cues, not raw CLAP events.** Labels flagged
  `sfx_countable: false` in `config/audio_sense_labels.json` (bruh, music, suspense_music,
  laughter, cheering) never count, and same-label window-chains merge at ≤0.6 s gaps. v1
  counted every CLAP window — the VOCAL `bruh` prompt matched ordinary speech on every hop
  (73% of all events) and inflated reference density to ~30/30 s ([[concepts/bugs-and-fixes#BUG 75]]).
  Post-fix: reference median **1.81/30 s** (floor — CLAP under-detects real booms under
  speech), ours 3.26 from effects_log ground truth. **The owner's ear, not this metric,
  arbitrates density pushes.**
- **Reference caption wps = first-appearance dedup** (2-frame flicker memory) when OCR sample
  text exists; otherwise falls back to speech rate like ours. v1 recounted persisting captions
  every sampled frame (17–27 "wps"); post-fix reference 2.92 ≈ ours 2.8 — word-box captions
  run at speech rate on both sides.
- **TikTok outro**: `trim_end="auto"` (now the default in analyze/decompose batch paths)
  detects the download outro per clip — last cut in the final ~6 s + speechless tail +
  TikTok/@ OCR confirm; speech-to-the-end = certain no-outro (trim 0); unsure = the legacy
  4 s blanket. The corpus is "most but not all downloads" (owner), so the blanket was cutting
  4 real seconds from the non-outro clips. All 86 pre-existing timelines were verified
  already-trimmed (the outro never polluted current metrics).

To re-apply the numeric policy to cached cards after a counting change (no VLM re-run):
`python scripts/research/attribute_cards.py --refresh-facts [--cards-dir <dir>] [--scan-audio]`.

## Music-bed detection (owner req 2026-07-15: "know if there is music in the background")

CLAP's "background music playing" prompt under-detects ducked beds (34/86 timelines had ANY
music span; median 0%). The replacement — `audio_sense.music_bed_scan` — uses the signal a bed
can't hide from: **energy persisting through the gaps between words**, classified by
POWER-spectrum features calibrated 2026-07-15 (in-band power flatness: music 0.03–0.10 vs
white-ish noise 0.56; top-5%-bins concentration: music 0.80–0.94 vs single-tone hum 1.00;
adaptive rms floor = speech p95 − 32 dB). Pure numpy on the ffmpeg decoder — NO librosa
(deadlock hazard, BUG 71c).

Semantics that matter when reading the numbers:
- **coverage_pct = music ratio among OBSERVABLE gap moments** ("of the moments we can hear
  the background, X% carry music") — NOT summed span time; dense speech would under-measure
  a full bed to whatever its longest pause shows. Beds persist through speech; gaps are
  sampling points.
- **pattern** ∈ none / full / first_half / second_half / partial / intermittent — from
  per-third gap-music ratios (the owner's described corpus shapes). **confidence: low** when
  a third is unobservable (dense speech) — the pattern is then best-effort.
- The scan runs on the TRIMMED window (the TikTok outro is itself musical — it must not vote).
- Validated: bare clip 0%, −18 dB bed mix 83% full, pure music 91% full, white noise 0%.
  Corpus read matches the owner's description: **36 full / 27 none / 17 partial / 6 half**
  across the 86 reference clips.

Where it lands: timeline `music_bed` block → card `music_grammar` {bed_coverage_pct,
bed_pattern, added_by_editor} → report **music-bed column** + `music_bed_pct_med` gap metric
(measured the SAME way on both sides).

> [!warning] Read the metric as "music the video CARRIES", not "music the editor added"
> The scan cannot tell added beds from source-native music — and **our pipeline has NEVER
> added music** (owner-confirmed 2026-07-15 + verified: `music_bed` config is `""` in the
> live config, the default, and the example — `profile_render` mixes no music input without
> it; and raw-VOD A/B proved it: the top "music" clips read ~98% on the RENDERED clip and
> ~98–99% on the SAME window of the RAW VOD — it's the streamers' own in-stream rap/game
> music). So per-category reads mean: reference gaming edits carry LESS audible music
> (12.5%) than our raw gameplay slices (73% — stream-native); reference story runs beds
> (72.5%) where our story windows are quiet (11%). Whether to ADD beds (the `music_bed`
> config + `assets/music/` are ready) is an owner call, not a detector instruction.
> Owner preference on the SFX side (2026-07-15): keep the current frequent-SFX density.

**Music vs soundboard SFX separation** is two-sided: (1) `sfx_countable:false` labels never
count as SFX (BUG 75); (2) `music_confusable:true` labels (boing, quack, sad_trombone, riser
— melodic/toy prompts CLAP hears inside melodies) need score ≥ 0.35 to count while a bed is
detected (clip-wide at ≥40% coverage, else inside bed spans). Guard effect on the reference
corpus: sfx median 1.81 → **1.14/30s**.

## Verdicts, the queue, and the JUDGED report export

Pressing **✓ Fix it / ✗ Not a problem** writes the verdict into
`clips/.diagnostics/diff_approvals.json` (the R4 queue). **Nothing auto-applies** — an agent
works approved items into small reviewed config/prompt commits (that's how the SFX-density
lever shipped and later measured 0.88→5.36 cues/30s).

**Copy judged report** (2026-07-13, owner req): once you've judged the findings, the button
builds ONE copy-ready markdown document — every finding with its plain-language label, the
reference-vs-ours numbers, the config lever, the explanation, and your verdict (+ reason),
grouped ✅ approved / ❌ rejected / ➖ no-action / ❓ unjudged — copies it to the clipboard AND
saves it beside the raw report as `corpus_diff_<date>_judged.md`. That document is the
paste-anywhere handoff (to an agent session, a note, Discord).

## Rules & gotchas

- **One job at a time**, with a Stop button (kills the process tree). The Lab and the Clipper
  **mutually 409** — they share the GPU + LM Studio. Bare-metal only.
- Each Compare mints a **fresh report date** with blank verdicts; earlier verdicts persist in
  the queue under their old date (and in any `_judged.md` you exported).
- All outputs are derived artifacts (gitignored): cards in `reference_clips/.cache/`, our-clip
  cards in `clips/.diagnostics/cards/<run>/`, reports + queue in `clips/.diagnostics/`.
- **Folder layout (2026-07-13 reorg):** the reference clips sit at the top level of
  `reference_clips/` (video files only — uncluttered so you can add/rename/remove clips
  freely); owner annotations are grouped in **`reference_clips/notes/<stem>.notes.json`**
  (tracked in git, stem must match the clip). Resolution is centralized in
  `clip_forensics.notes_path()` / `iter_notes()` and still READS a legacy top-level sidecar
  if one is present, so nothing breaks if you drop a clip in with an old-style sidecar —
  but every tool now WRITES into `notes/`. `.cache/` (machine artifacts) and `sfx_reference/`
  are unchanged.
- After ANY dashboard code change, restart the dashboard ([[concepts/bugs-and-fixes#BUG 70]]).

## The full loop (already exercised end-to-end)

**Analyze references → Compare → ✓ Fix it → agent applies the lever → next clip run →
Compare again = the gap measurably closes.** First full cycle: the SFX-density finding →
`sfx_cues.json` changes → re-run → re-measured **0.88 → 5.36 sound effects/30s**
([[concepts/handoff-2026-07-12]]).
