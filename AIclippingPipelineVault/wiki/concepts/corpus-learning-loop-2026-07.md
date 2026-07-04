---
title: "Corpus Learning Loop (Phase 7)"
type: concept
tags: [reference-clips, learning, calibration, captions, forensics, phase-7]
sources: 1
updated: 2026-07-04
status: shipped
---

# Corpus Learning Loop (Phase 7)

Closes the gap between "analyse competitor clips" and "the pipeline **learns** from
them." Before Phase 7 the reference corpus was analysis-only: [[concepts/plan-clip-forensics]]
decomposed a clip → timeline + LLM style profile → a human read it and hand-edited
config. Phase 7 makes three of those loops mechanical + measurable. Built + validated
2026-07-04 (corpus was 35 clips, 4 decomposed, 1 template annotation).

Answers the owner's three questions directly:
1. *How do reference clips improve the pipeline?* → 7.1 + 7.3 turn them into calibration data; 7.2 turns them into caption voice.
2. *Can caption-language styling be learned + applied?* → 7.2 (yes for language; not visual styling).
3. *Can it reliably detect effects/cues + judge transcript value?* → 7.1 measures detection reliability; 7.3 judges transcript value.

## 7.1 — Draft-annotation loop → measured detector reliability

Reliability can't be asserted, only measured, and measurement needs labels (the corpus
had **1** annotation). The loop makes labelling cheap:

```
# 1. decompose (once) — writes .cache/<stem>.timeline.json
python scripts/research/clip_forensics.py --clip <name> --ocr --draft-notes
#    ...or batch-draft everything already decomposed (no re-decompose):
python scripts/research/corpus_eval.py --draft-from-cache
# 2. OWNER corrects each <clip>.notes.json: delete wrong lines, ADD missed cues
#    (cold_open_teaser, punchline sfx, music the tool didn't hear), drop the "_draft" key
# 3. aggregate corpus-wide precision + recall per detector family
python scripts/research/corpus_eval.py
```

- `clip_forensics._draft_notes(timeline)` pre-fills a `.notes.json` from the tool's OWN
  detections (music spans → music_in/out, censor → censor, top deduped audio events →
  sfx). Marked `_draft:true`; the owner corrects instead of authoring from blank.
- `clip_forensics._score_against_notes` upgraded from recall-only-all-lumped to
  **per-family precision + recall** (`sfx / music / censor / cut / cold_open`). Precision
  is `None` for a family with no ground-truth annotations (e.g. cuts — the owner doesn't
  annotate them, so a detected cut can't be "wrong"). `cold_open` has no detector → recall
  0.0 by design (honest: the tool can't find cold opens).
- `corpus_eval.py` sums per-clip scores into corpus precision/recall per family →
  `.cache/corpus_eval.json`. **Low precision on a family ⇒ that CLAP label's threshold is
  too loose** — this is the data that calibrates E1 (threshold tuning) instead of the
  current single-clip guesses.

> [!warning] A raw draft scores ~1.0 trivially
> The draft is generated FROM the detections, so scoring it un-corrected is circular
> (recall ≈ 1.0). The number only means something AFTER the owner corrects it. `is_draft`
> is surfaced in every eval + the console warns "[DRAFT — correct it for a real score]".

Validated 2026-07-04: generated a draft, simulated an owner correction (deleted half the
sfx as noise, added a `cold_open_teaser` + a far punchline sfx) → `is_draft` flipped
False, `cold_open` recall 0.0, sfx recall dropped 1.0→0.833, sfx precision dropped as the
annotation set shrank. Math behaves. 3 real drafts seeded for the owner to correct.

## 7.2 — Caption-LANGUAGE style learning

`scripts/research/caption_style.py` distils the burned-in caption **text** (EasyOCR
`captions.samples` across all cached timelines) into a reusable voice profile:

```
python scripts/research/caption_style.py     # writes config/caption_style.json (enabled=false)
```

- Fuzzy-dedups the per-frame OCR repeats, strips watermark/handle noise (`@handles`,
  platform tokens, sub-6-char garble), computes local stats (casing ratio, word-length
  distribution, emoji presence, frequent tokens), then asks LM Studio ONCE to distil
  `voice_summary / casing_rule / slang_lexicon / hook_phrasings / per_category_tone`.
- Failure-soft: LM down → a stats-only profile is still written.
- **Consumed by Stage 6** ([[concepts/vision-enrichment]]): `_caption_style_fewshot()` injects
  `voice_summary` + casing + a few hook phrasings into the VLM hook/title prompt as a
  "match this VOICE (not the words)" block. **Gated + opt-in**: no injection unless
  `config/caption_style.json` has `enabled:true` AND `CLIP_CAPTION_STYLE` isn't force-off.
  Default `enabled:false` → prompt byte-identical to before (owner reviews the profile
  first). Verified: silent when off, injects when on, `CLIP_CAPTION_STYLE=0` kills it.

> [!note] Language yes, visual styling no
> OCR recovers the *language* voice reliably (the LLM sees through OCR garble). It does
> NOT recover font/colour/position, and it mixes editor captions with streamer/chat
> overlay text. Profile carries a `caveats` field saying so.

First run distilled: *"casual, conversational Gen-Z voice… repetitive meme-like hooks…
loose grammar and phonetic slang"*, casing "mostly lowercase", slang `[homeboy, gas,
period, rich, mean, anyways, add me]` from 38 lines / 2 clips.

## 7.3 — Transcript-value classifier

`scripts/research/transcript_value.py` scores each clip's transcript STANDALONE and labels
it **transcript-carried vs reaction-carried vs mixed**:

```
python scripts/research/transcript_value.py   # -> .cache/<stem>.value.json + transcript_value.json
```

- Signals: `wps` (dead air vs dense talk); `keyword_score` (density of the REAL Pass-A
  `KEYWORD_SETS`, AST-extracted from `stage4_moments.py` so the tool stays offline — no
  torch import; 250 keywords/8 categories); `reaction_score` (fraction of audio events
  that are `REACTION_LABELS` crowd/laughter — the "words don't explain it" tell); and an
  LM Studio verdict "would the TEXT ALONE justify a clip?". LLM leads; signals set confidence + break ties.
- The `reaction_carried` list **is the [[concepts/case-incongruity-comedy]] ground-truth
  eval set** — the clips whose value is reaction/visual, not words.

**Finding (4-clip sample, 2026-07-04): 0 transcript-carried, 2 reaction-carried, 2 mixed.**
ReemKnocksClip (kw=0.0, rx=0.42) and the Rakai clip (kw=0.4, rx=0.86) are reaction-carried;
even the two "mixed" clips had high reaction. This **empirically supports the anomaly-lane
thesis** — in this niche the clip value is frequently NOT in the transcript, which is
exactly the class keyword-only Pass A misses.

## Shared hardening — `lmstudio.loads_lenient`

All three tools (and 6+ existing call sites) parsed LLM JSON with the fragile
`text.find('{') / rfind('}') + json.loads` idiom. A live qwen reply dropped a closing
quote on one field → the whole caption-style synthesis silently fell to null. Added
`lmstudio.loads_lenient(text)`: strict-parse first (never mangles valid JSON), then repair
the observed failure modes (fences, trailing commas, unterminated single-line string
values) and retry. Wired into `caption_style` + `clip_forensics`. Verified against the
exact failing reply + valid-JSON passthrough + garbage→None.

## Status / what's owner-gated

- **Built + validated**: all three tools + the Stage 6 wiring + the lenient parser.
- **Owner action to activate**: (7.1) correct the seeded drafts → run `corpus_eval` for the
  reliability number; (7.2) review `config/caption_style.json`, set `enabled:true` to use
  the learned voice in generated clips. 7.3 needs nothing — it's an eval artifact.
- Scales as the corpus grows: decompose more clips → richer voice + a bigger eval set.

Related: [[concepts/plan-clip-forensics]] · [[concepts/case-incongruity-comedy]] ·
[[concepts/plan-pipeline-upgrade-2026-07]] · [[concepts/vision-enrichment]] ·
[[concepts/hook-engineering-2026-06]]
