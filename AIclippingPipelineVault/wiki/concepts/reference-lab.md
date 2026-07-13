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
- After ANY dashboard code change, restart the dashboard ([[concepts/bugs-and-fixes#BUG 70]]).

## The full loop (already exercised end-to-end)

**Analyze references → Compare → ✓ Fix it → agent applies the lever → next clip run →
Compare again = the gap measurably closes.** First full cycle: the SFX-density finding →
`sfx_cues.json` changes → re-run → re-measured **0.88 → 5.36 sound effects/30s**
([[concepts/handoff-2026-07-12]]).
