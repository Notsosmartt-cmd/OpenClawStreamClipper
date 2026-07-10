---
title: "Plan — Caption-Language Overhaul, then A/B Variant Outputs"
type: concept
tags: [plan, captions, stage6, stage7, ab-testing, voice, platform]
status: in-progress
updated: 2026-07-10
---

# Plan: caption-language overhaul → A/B variant outputs

Two-part plan, **strictly sequenced by owner direction (2026-07-10)**: fix the caption/title
language first, THEN build A/B variant outputs — because variants multiply whatever caption
quality exists (3 variants of an AI-sounding caption = 3 AI-sounding captions).

**Motivation (owner):**
- Recurring critique class across two review rounds: captions/titles "look too much like an AI
  wrote it" (Little J's 2026-07-10), "caption/header language is bad" ('Yo!' Freestyle
  2026-07-09), "caption doesn't match the video" (Samurai Slicer 2026-07-09), caption/context
  mismatch (Right on the Dot 2026-07-10 — upstream dead-air cause fixed as
  [[concepts/bugs-and-fixes#BUG 68]], but the mismatch class is real).
- New goal from an ingested creator-advice transcript (`B:\AuxCoding\VideoToText-main\
  transcripts\A_BContent.txt`): trial-reel style A/B testing — repost the same clip with a
  changed hook/caption; tiny wording changes swing distribution. Owner posts to **multiple
  platforms** (TikTok, IG Reels, YT Shorts), so outputs must be platform-agnostic.
- Ties into [[concepts/plan-learning-activation-2026-07]] (labels) and the
  [[concepts/corpus-learning-loop-2026-07]] caption-voice distiller (Phase 7.2), which this
  plan repairs and supersedes in part.

---

## Implementation status (shipped 2026-07-10)

**All machinery for Part 1 + Part 2 is built, compiles, and is live-validated against the
real model.** Part 1 caption fixes are **default-ON** (with per-flag kill switches); Part 2 A/B
is **default-OFF** (env flag + dashboard checkbox), per the plan's gating.

| Item | File(s) | Flag (default) | State |
|---|---|---|---|
| P1.1 full-window transcript | `stage6_vision.py` (`[:500]`→`[:4000]`, + `_ts` variant) | — | **ON** |
| P1.2 caption fidelity judge | `grounding.py::caption_judge` + `config/grounding.json::caption_judge` | `CLIP_CAPTION_JUDGE=1` | **ON** |
| P1.3 voice-contract prompt | `stage6_vision.py::_caption_voice_contract` + JSON field rewrite | `CLIP_CAPTION_VOICE=1` | **ON** |
| P1.4 AI-tell linter | `scripts/lib/caption_lint.py` (self-test PASS) | `CLIP_CAPTION_LINT=1` | **ON** |
| P1.5 voice bank v2 | `caption_style.py` (handle/garble filter + `--review-sheet`/`--ingest-sheet`) | profile `enabled=false` | machinery ON; profile awaits owner curation |
| P1.6 caption labels | `rate_run.py set --caption`, `collect --captions` | — | tool ready |
| P2.1 variant-B generation | `stage6_vision.py::_generate_variant_b` | `CLIP_AB_VARIANTS>=2` (**0**) | OFF |
| P2.2 variant render (varied AV) | `stage7.py::_maybe_ab_variant`, `profile_render.py --seed-offset` | `CLIP_AB_VARIANTS` + `CLIP_AB_VARIANTS_TOP_N=5` + `CLIP_VARIANT_SEED_OFFSET=1` | OFF |
| P2.3 platform post kit | `stage6_vision.py::_generate_post_kit` + `stage7.py::_maybe_write_post_kit` | `CLIP_POST_KIT` (**0**) | OFF |
| P2.4 variant-winner label | `rate_run.py set --variant-winner` | — | tool ready |
| Dashboard toggles | `pipeline_runner.py`, `pipeline_routes.py`, `pipeline-ui.js`, `index.html` | `chk-ab-variants`, `chk-post-kit` | wired (env-chain verified: checked → `CLIP_AB_VARIANTS=2`) |

**The caption gate (P1.2/P1.4):** `_ground_field` for the creative fields (title/hook) now, AFTER
the existing Tier-1 pass, runs `_caption_gate` — the deterministic linter first (free), then the
fidelity judge (one call, pinned to the loaded Stage-6 `VISION_MODEL` so it never swaps models). A
fail sets `passed=False` + `caption_detail`, which drives the EXISTING regenerate-once path
(violation named in the retry prompt); a second fail nulls to the plain baseline. The creative
fields finally get the semantic check `description` always had, reusing all the regen/null wiring.

**The A/B seed mechanic (P2.2):** variant B is a full INDEPENDENT `profile_render` with the
alternate hook AND `--seed-offset 1`, so its SFX pick + profile/fingerprint draws differ from A
while beat PLACEMENT stays anchored on the real timestamp. `seed_offset=0` (variant A) is
byte-identical to before. Needs profile mode (that's where SFX/visual variety lives) — a hook-only
B is logged-skipped.

**Live validation (2026-07-10, qwen3.5-9b):**
- Linter: "The Ultimate Vending Machine Reveal" → flagged (title_case + headline_the + "ultimate"); human caption → clean.
- **caption_judge discriminates sharply**: on-topic caption fidelity **10** / human_voice 9; off-topic ("epic freestyle rap battle") fidelity **0** / human_voice 2, rationale "describes a rap battle that never happens in the clip." This is the caption↔video mismatch detector the owner asked for.
- Variant B: produced a distinct, on-topic, linter-clean alternate ("school vending machines are segregated" / "one for staff one for everyone", angle=quote).

**Gates still owned by the owner:**
1. **Part-1 default-on eyeball** — judge+lint add ~2 short calls/moment and can null a title→regen→baseline. Failure-soft + kill switches (`CLIP_CAPTION_JUDGE=0`, `CLIP_CAPTION_LINT=0`, `CLIP_CAPTION_VOICE=0`). The offline before/after A/B on run `20260710_005533`'s cached clips is the formal gate.
2. **P1.5 profile enable** — `python scripts/research/caption_style.py --review-sheet sheet.txt` → mark `[x]` the good lines → `--ingest-sheet sheet.txt --enable`. Until then the voice bank stays `enabled=false` (unchanged behavior).
3. **Part-2 variant run** — flip the dashboard "A/B variant clips" checkbox (needs Style profiles ON) on one VOD, review B vs A + render cost, then default-on for top-N.

---

## Current state (grounded inventory, 2026-07-10)

Where every piece of clip-facing TEXT comes from today:

| Field | Generated | Validated | Rendered |
|---|---|---|---|
| `title` | Stage 6 vision prompt ("short viral title") — `stage6_vision.py:661` | **Tier-1 only, `min_overlap=0.0`** (denylist + hard-events; NO semantic check) — `stage6_vision.py:342` | Filename (sanitized `[:50]`) — `stage7.py:124` |
| `hook` | Same prompt ("punchy 1-line, max 8 words") — `stage6_vision.py:663` | Same relaxed Tier-1 path (`_CREATIVE_FIELDS`) | Burned header via `_wrap_hook` (18 chars × 3 lines) — `stage7.py:287`, `profile_render.py:524` |
| `description` | Same prompt (literal one-liner) | Full 2-tier cascade (overlap 0.15 + LLM judge ≥5.0) | Clips log / delivery text |
| word captions | whisper verbatim (`kinetic_captions.py`) | n/a — verbatim | burned-in |
| Fallbacks | `_derive_baseline_title` (Pass-B why) + `_hook_from_template` (hook-engineering templates) | — | same paths |

**Root causes found (this is why the critiques recur):**

1. **The two fields the owner SEES are the two fields nothing semantically checks.**
   `_CREATIVE_FIELDS = ("title", "hook")` deliberately runs Tier-1 with `min_overlap=0.0` and
   never reaches the LLM judge (2026-06 "Fix 1" — relaxed so punchy phrasing wouldn't be nulled
   by literal-overlap false positives). Correct diagnosis, wrong cure: it removed ALL semantic
   verification instead of replacing overlap with a semantic check. `description` (which nobody
   sees) gets the full cascade. Mismatch class (Samurai Slicer) is structurally uncatchable today.
2. **Long clips generate titles from a fraction of their transcript.** The vision prompt's
   transcript window was widened to the full clip window in a prior fix but is truncated
   `[:500]` chars (`stage6_vision.py:509`) ≈ first ~20-30 s of speech. Every critiqued clip was
   45-60 s. The model literally cannot "encapsulate the context of the clip" it never saw. The
   same truncated string feeds the grounding refs.
3. **The voice-learning system exists but is disabled AND poisoned.** Phase 7.2
   (`scripts/research/caption_style.py` → `config/caption_style.json` → Stage 6 few-shot via
   `_caption_style_fewshot()`, channel-scoped `applies_to`) shipped 2026-07-04 but
   `enabled=false`, and the learned profile is OCR garbage: `frequent_tokens` are watermark
   HANDLES (`solereports`, `realstableronaldo`…), `examples` are garbled OCR
   ("yoU ever heard of george TkTok"), `slang_lexicon` includes `pyyyyyyy`. Enabling it as-is
   would make captions WORSE. So the prompt runs with ZERO voice exemplars — the model falls
   back to LLM headline-ese: Title Case, quoted invented nouns, "The 'X' Y" constructions
   (visible in the actual clip titles: "The 'Right on the Dot' Payoff", "The 'Samurai Slicer'
   Diss").

---

## Part 1 — Caption-language overhaul (implement FIRST)

Goal: titles/hooks that (a) read like a human clipper wrote them and (b) describe what actually
happens in the FULL clip. Ship default-on after the owner gate (default-off = RED rubric).

### P1.1 Full-window transcript (trivial, do first)
`stage6_vision.py:509` — raise `[:500]` → `[:4000]` (~1k tokens; fine in 32k ctx; the `_ts`
variant at `:513` similarly `[:1100]`→`[:4000]`). The SAME string must feed the grounding refs
so generation and validation see identical evidence. Cheapest fix with the highest expected
effect on "doesn't encapsulate the clip".

### P1.2 Caption-fidelity judge (closes the structural hole)
New `caption_judge()` in `scripts/lib/grounding.py`: ONE call, judges title+hook together
against {full clip-window transcript, vision description, category}:
`{"fidelity": 0-10 (does it describe what happens?), "human_voice": 0-10 (would a human
clipper write this?), "rationale": str}`. Wire into `stage6_vision._ground_field` for
`_CREATIVE_FIELDS` (keep `min_overlap=0.0` — the judge replaces overlap, not adds to it).
Fail (fidelity <6) → the existing regenerate-once path (`stage6_vision.py:~875`) with the
rationale injected into the retry prompt; second fail → baseline title (current behavior).
Config: `grounding.json::caption_judge` {enabled, thresholds, timeout}. Cost: +1 short call
per moment (~10-20/run — negligible vs Stage 4).

### P1.3 Voice contract in the prompt (rewrite the ask)
Rewrite the title/hook instructions (`stage6_vision.py:661-663`): write like a viewer texting
a friend, sentence case or lowercase, BAN: Title Case Headlines, quotation marks around
invented nouns, "The X: Y" / "The 'X' Y" constructions, em-dashes, hashtag-speak, listicle
words (epic/hilarious/insane/ensues/ultimate). Per-category tone hints (rap/freestyle vs
storytime vs irl). Titles ≤ 9 words; hooks stay ≤ 8 (render cap is 54 chars — `_wrap_hook`).

### P1.4 Deterministic AI-tell linter (cheap, testable, catches drift)
New `scripts/lib/caption_lint.py` (stdlib-only): flags Title-Case ratio >0.6, quoted-noun
pattern, banned lexicon, "The … of …" headline shapes, trailing period on hooks, char caps.
Runs after the judge; a flag → same regenerate-once with the specific violations named;
persistent flag → keep text but log `[caption-lint]` so we can measure drift over runs.
Self-test with the 4 critiqued titles as fixtures (all must flag) + owner-liked titles
(must pass). Zero LLM cost.

### P1.5 Voice exemplar bank v2 (repair Phase 7.2, don't fork it)
Regenerate `config/caption_style.json` (same file/loader/scoping — bump `version: 2`):
- Harder cleaning in `caption_style.py::collect`: drop tokens matching known handle patterns +
  lines failing a dictionary-word ratio (kills the OCR garble that poisoned v1).
- Merge a hand-curated exemplar set: mine candidate caption lines from the cleaned OCR corpus +
  competitor-transcript hooks on B: + owner-approved past titles; write ~30 candidates to a
  review sheet; owner thumbs up/down once (rate_run-style); approved lines land in
  `examples`/`hook_phrasings` per category.
- Then `enabled=true` (this was the blocker: the profile was never good enough to enable).
`_caption_style_fewshot()` already injects it — no Stage-6 code change needed beyond P1.3.

### P1.6 Caption labels (make the critique measurable)
Extend `scripts/research/rate_run.py` with an optional `--caption {0,1}` on `set` (stored as
`caption_label` beside `label`). The owner already gives caption verdicts verbally every
review; recording them separately (clip good ≠ caption good — Little J's was exactly this
split) builds the negative-exemplar pool ("do NOT write like: …" in the prompt) and a
regression metric for future runs.

### Part 1 validation gate (owner)
Offline re-generation A/B on run `20260710_005533`'s 10 clips (frames + transcripts cached —
no full pipeline run): old vs new title+hook side-by-side table. **Ship default-on when the
owner approves the language on ≥8/10 and fidelity on 10/10.** Kill switches:
`CLIP_CAPTION_JUDGE=0`, `CLIP_CAPTION_LINT=0`, `CLIP_CAPTION_STYLE=0` (exists).

Effort: one session. P1.1+P1.4 shippable same-day; P1.2+P1.3 same session; P1.5 needs one
owner review pass; P1.6 trivial.

---

## Part 2 — A/B variant outputs + platform post kit (implement AFTER Part 1)

Goal: per clip, emit an A + B variant render (2 versions) + paste-ready per-platform post
text, so the owner can trial-reel/A-B test across ANY platform. Part 1's judge+linter become
the quality gate every variant must pass.

### P2.1 Variant generation (classic A/B, 2 versions, angle-distinct)
Owner: **exactly 2 variants — classic A and B.** A = the primary caption already generated
(highest-fidelity, typically quote-the-punchline). B = ONE alternate-angle challenger chosen
by the model from {reaction-POV ("the way he …"), context-tease (setup withheld)} — whichever
contrasts most with A. So Part 2 adds just ONE extra generation (B); A is free. Distinct
angles give the A/B test real contrast (temperature resampling gives near-duplicates). B must
pass `caption_judge` + `caption_lint`; if it fails twice, no B is emitted for that clip (A
still ships). Stored as `hook_variants: [{label:"B", hook, title, angle}]` (A is the base entry).

### P2.2 Variant rendering (full independent render per variant — owner wants varied AV)
Owner: **variants vary SOUND and VISUAL effects too**, not just the header. Mechanic: the SFX
pick and the style-profile / fingerprint randomization are already deterministic seeded draws
(`pick_sfx` seed = `int(round(T))`; profile effects seeded per-clip). Variant B renders with a
**perturbed seed** (`seed + 1`, env `CLIP_VARIANT_SEED_OFFSET`) so it draws a different but
still-valid SFX + visual-effect set, AND with B's `--hook-text`. Each variant is therefore a
**full independent NVENC render** (~10-30 s/clip) — the earlier "hook-less master +
drawtext-only pass" optimization is INVALID here (a shared master can't carry per-variant AV),
so it's dropped. Files: A = `"<title>.mp4"` (primary), B = `"<title> (B).mp4"` (suffix after
the `[:50]` sanitize). Bounded by gating variants to top-N clips per run
(`CLIP_AB_VARIANTS_TOP_N`, default 5). Companion shorts
(`stage7.py:_maybe_companion_short`) apply to A only.
**Note the tradeoff (owner's explicit choice):** varying AV means the A/B test measures
caption + AV *together*, not the caption in isolation — a won variant tells you "this whole
package won", not specifically "this hook won". Accepted for stronger per-post differentiation.

### P2.3 Platform post kit (the "any platform" part — zero render cost)
The 9:16 file already fits TikTok/Reels/Shorts; what differs per platform is the POST TEXT.
New sidecar `"<title>.post.json"` per clip, one LLM call from {title, hook, description,
punchline quote, voice profile}: `tiktok` (short caption), `instagram` (hook line + context
sentence + `trial_reel: true` marker for variant sets), `youtube_shorts` (title ≤100 chars +
description). **No hashtags anywhere (owner: zero tags)** — pure post copy. Copy-paste-ready;
variants A/B get their own entries. Flag `CLIP_POST_KIT` (default on after gate — additive text).

### P2.4 Outcome loop (later, owner-driven)
Owner records which variant won (views) per platform — a one-line `rate_run` extension
(`--variant-winner B`). Winners feed the P1.5 exemplar bank; this is the calibration loop the
trial-reel transcript describes, and the first posting-outcome signal for
[[concepts/plan-learning-activation-2026-07]].

### Part 2 gates
Dashboard toggle `chk-ab-variants` → `CLIP_AB_VARIANTS` (0 off / 2 / 3), wired like the
companion-shorts toggle ([[entities/dashboard]] pattern). First variant-enabled run → owner
spot-checks variant quality + render-cost measurement → then default-on for top-N clips per
the rubric (default-off = RED, promote or delete).

Effort: one session after Part 1 lands.

---

## Sequencing + dependencies

```
P1.1 window fix ──┐
P1.3 prompt ──────┼─→ offline A/B on cached run → OWNER GATE → default-on
P1.2 judge ───────┤                                    │
P1.4 linter ──────┘                                    ▼
P1.5 voice bank (owner curation pass) ──────→ Part 2 build (P2.1→P2.3) → variant run
P1.6 caption labels (any time)                         → OWNER GATE → default-on
```

**Owner decisions locked (2026-07-10):** (1) **2 variants — classic A/B**, not K=3;
(2) **no hashtags** in the post kit; (3) **variants DO vary SFX + visual effects**
(seed-perturbed B), overriding the earlier "one variable per experiment" default — owner
prefers maximum per-post differentiation over isolating the caption as the sole A/B variable.
Consequence baked into P2.2: B is a full independent render (no shared master), and the A/B
outcome reflects the whole package (caption + AV), not the hook alone.
