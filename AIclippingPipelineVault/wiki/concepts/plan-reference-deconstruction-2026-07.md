---
title: "Plan — Reference-Clip Deconstruction: Cards → Diff → Apply (learning loop B rebuild)"
type: concept
tags: [plan, learning, reference-clips, forensics, attribute-cards, diff, few-shot, stage6]
status: planned
updated: 2026-07-10
---

# Plan: reference-clip deconstruction — editorial cards → contrastive diff → curated apply

Owner-proposed architecture (2026-07-10), evaluated and endorsed with amendments. Replaces the
tedious workflow where the owner must *articulate* attributes he sees in other creators' clips
to an AI agent and slowly iterate implementations. The machine does the articulation; the owner
curates a generated diff.

**Owner's proposal (verbatim intent):** invert the pipeline — instead of VOD → clips, intake a
bunch of (reference) clips and use the current implementations (transcription, vision, audio,
CLAP, …) to construct *linguistic, detailed attribute deconstructions*, consumable by an AI
agent or the owner, to see what can be taken from good reference clips and programmatically
applied to the forward pipeline. No sub-model training.

**Evaluation verdict:**
1. The proposal is correct — and ~70% already exists. `clip_forensics.py` + `audio_sense.py`
   (CLAP) + `visual_sense.py` (motion/OCR) + whisper IS the inverted pipeline (shipped
   2026-06-21, [[concepts/plan-clip-forensics]]); 35/59 reference clips already have
   `.cache/*.timeline.json` decompositions.
2. What's missing is not extraction machinery but three layers: **(a)** the output speaks in
   signals (timestamps/events), not editorial language; **(b)** there is no comparison against
   OUR produced clips; **(c)** there is no bridge from findings to the pipeline's config/prompt
   levers. Those three layers are this plan.
3. Loop-A de-emphasis (decided): the owner-label→ranker loop keeps banking labels passively
   (pool 35: 33/2) but is NOT the priority lever — it only re-ranks candidates we already
   generate, and its fit gate stays REJECT. Reference-clip work (this plan) is the priority.

> [!warning] Doctrine constraint (owner, 2026-07-10)
> **No model training or fine-tuning anywhere.** All learning stays at the bottom of the
> data-appetite ladder: LLM *extraction* into structured cards, statistics distilled into
> config numbers, and few-shot/retrieval injection into prompts. Embeddings use a FROZEN
> pre-trained embedder only. If a step seems to want gradient descent, the step is wrong.

---

## Current state (what exists, verified 2026-07-10)

- `scripts/research/clip_forensics.py` — per-clip decomposer: cuts, censor beeps, music bed,
  motion punches, OCR captions (`--ocr`), LLM essence/style_profile; hang-proof watchdog.
- `scripts/lib/audio_sense.py` — CLAP semantic audio events (booms/laughter/risers); PANNs
  opt-in (torch≥2.9 deadlock). Caveat: CLAP cosines run low (~0.26–0.32), threshold 0.30.
- `scripts/lib/visual_sense.py` — cv2 motion + EasyOCR. **EasyOCR is the weakest sensor** —
  its garble poisoned caption-voice v1 ([[concepts/plan-captions-and-ab-variants-2026-07]]).
- `scripts/research/corpus_refresh.py` — batch driver; `caption_style.py` — voice distiller
  (v2 curation flow built, profile still `enabled=false`); `corpus_eval.py` — precision/recall
  vs `.notes.json` (35/36 notes are uncorrected drafts); `transcript_value.py`.
- Coverage: **59 reference videos, 35 decomposed** (24 missing). TikTok downloads carry a ~3s
  outro — decompose with `--trim-end 4`.
- Consumption today: caption voice (disabled), manual distillation only (SFX taxonomy, hook
  templates, meme formats were hand-derived by agents from the corpus).

---

## Phase R0 — corpus coverage + hygiene (mechanical)

Decompose the 24 missing clips: `corpus_refresh` / `clip_forensics --ocr --trim-end 4` (TikTok
outro caveat), bounded background batch. Sanity-check CLAP events against 2–3 known clips.
**Output:** 59/59 `.cache/*.timeline.json`. **Effort:** one bounded batch (EasyOCR is the slow
step). No gates — additive cache files.

## Phase R1 — editorial attribute cards (the "linguistic deconstruction")

New `scripts/research/attribute_cards.py`: per reference clip, ONE 35B multimodal call —
inputs = sampled frames (first-2s hook frame + payoff-window frames), full transcript,
timeline.json facts (cuts/CLAP/motion/music), with the VLM **reading on-screen text from the
frames directly** (do NOT trust EasyOCR for language). Output
`reference_clips/.cache/<stem>.card.json` (schema versioned):

```json
{
  "hook":   {"mechanic": "...", "first_2s": "...", "text_hook_style": "..."},
  "arc":    {"shape": "setup->payoff|escalation|instant", "setup_s": 0.0, "payoff_s": 0.0},
  "comedy": {"device": "...", "verbal_vs_visual": "verbal|visual|both"},
  "edit_grammar": {"cuts_per_30s": 0.0, "cut_alignment": "on-beat|on-punchline|loose",
                    "zooms": 0, "freezes": 0},
  "sfx_grammar":  {"count_per_30s": 0.0, "kinds": [], "offset_from_payoff_ms": 0,
                    "loudness_vs_speech": "over|under|ducked"},
  "captions": {"casing": "...", "voice": "...", "density_wps": 0.0},
  "engagement": {"chat_overlay": false, "emoji": false, "freeze_bait": false},
  "essence_commentary": "one editor's paragraph in plain language",
  "confidence": 0.0
}
```

Numeric fields come from timeline.json math where possible (deterministic), LLM fills the
editorial/linguistic fields. Failure-soft per clip. **Cost:** ~30–60 s/clip × 59 ≈ one bounded
~45-min batch. **Gate:** owner spot-reads 5 cards against the actual clips — do they describe
them truthfully? (This gate matters: everything downstream trusts the cards.)

## Phase R2 — same deconstruction on OUR clips

Run forensics + cards over the produced clips of recent runs (e.g. `20260710_202308`). For our
own clips, **merge known ground truth from `effects_log.jsonl`** (we logged exactly which SFX/
effects we injected — no need to infer) with forensics for the emergent properties (pacing,
hook timing). Output `clips/.diagnostics/cards/<run>/<title>.card.json`. Re-runnable per run.

## Phase R3 — contrastive diff report (the tedium killer)

New `scripts/research/corpus_diff.py`: group cards by category; compute numeric gaps
(SFX/30s, payoff offsets, cut cadence, %-with-text-hook, caption casing rates) reference-vs-ours,
then one LLM pass writes the **gap narrative** per category, citing specific clips. Every gap
item carries a `lever:` field naming the existing knob it maps to — `config/sfx_cues.json`,
`style_profiles.py` params, `config/hook_templates.json`, `config/caption_style.json`,
`config/patterns.json`, detection weights — or `lever: none → feature-card` (e.g. the
visual-subtext blind spot / School-Layout class). Output:
`clips/.diagnostics/corpus_diff_<date>.md`, written for the owner to read in minutes.
**Gate:** owner reads report #1 and approves/rejects items. His role shifts from *articulating
attributes* to *approving diff lines*.

## Phase R4 — curated apply bridge

Each approved item = one small reviewed config/prompt commit against its named lever (wiki +
commit per change, as always). Items without levers become explicit feature cards in the wiki
backlog. **Nothing auto-applies.** This keeps the loop: deconstruct → diff → owner approves →
config edit → next run → (optionally) next diff measures whether the gap closed — iteration
without re-articulation.

## Phase R5 — retrieval few-shot at generation time (inference-side twin)

Embed the cards with a FROZEN pre-trained embedder (sentence-transformers already a dependency
via the callback module). At Stage 6 (title/hook) — and optionally Pass B pattern hints —
retrieve the top-3 same-category reference cards and inject a compact "proven exemplars" block
into the prompt. Per-clip mimicry that compounds automatically as reference clips are added.
Flag `CLIP_REF_FEWSHOT` (ship default-off → owner spot-check run → default-on per the rubric).
Channel/niche scoping reuses the caption-style `applies_to` pattern (generalization guard).

---

## Sequencing, effort, gates

```
R0 coverage (batch, no gate)
  → R1 cards (batch ~45 min) → GATE: owner spot-reads 5 cards
  → R2 our-clips cards (per run, fast)
  → R3 first diff report      → GATE: owner approves items
  → R4 apply commits (each small, reviewed)   → next run closes the loop
  → R5 retrieval few-shot (flag) → GATE: spot-check run → default-on
```

Estimated build: R0–R3 one session; R4 is ongoing practice; R5 a second session.

Related: [[concepts/plan-clip-forensics]] (the decomposer this builds on),
[[concepts/corpus-learning-loop-2026-07]] (Phase 7 — superseded in part by this),
[[concepts/plan-captions-and-ab-variants-2026-07]] (voice bank consumer),
[[concepts/case-incongruity-comedy]] (the blind-spot class R3 will surface),
[[concepts/plan-learning-activation-2026-07]] (loop A — de-emphasized, keeps banking labels).
