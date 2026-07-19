---
title: "Plan: Edit-Quality Revisions (2026-07) — framing, duration discipline, caption presence"
type: concept
tags: [plan, editing, rendering, captions, framing, duration, stage-7, reference, quality]
sources: 0
status: planned
updated: 2026-07-18
---

# Plan: Edit-Quality Revisions (2026-07)

Consolidates every open finding from the 2026-07-18 review arc into buildable
workstreams. Three evidence tiers, kept distinct on purpose:

- **[M] measured** — deterministic aggregates over 270 attribute cards (corpus_diff + direct card mining)
- **[E] eyeballed** — the agent read actual frames (contact sheets, 6 refs + 4 of run `20260717_180215`); no local-LLM mediation
- **[J] judged** — 35B verdicts (weakest tier; see [[concepts/bugs-and-fixes#BUG 76]] for how it can lie)

> [!warning] Two corrections this plan encodes
> 1. **`chrome_regions` does NOT exist in current code.** It appears only in an April
>    trace (`last_run_20260423_035710.json`) and in stale wiki prose. The vision stage
>    emits title/hook/angle — no regions. Any "crop to the action" design that assumed a
>    live VLM region feed was wrong; W1 below is therefore **deterministic CV**, not VLM.
> 2. **SFX density is owner-vetoed** ("the high sfx density is funny") — no workstream here
>    touches SFX counts. Visual-transition trims were already applied 2026-07-17.

---

## W0 — Verification run (BLOCKS EVERYTHING ELSE)

**Why first:** the 2026-07-17 levers (hook sentence-casing, comedy/hot_take/storytime
transition trims, shape-priors v2) have **never rendered a clip** — the sampled clips in
the eyeball audit all predate them. Building on unverified changes compounds risk.

- **Action:** one normal pipeline run on a VOD; eyeball 3–5 output clips.
- **Checks:** hook now sentence-cased and matching the captions; fewer freeze/cutaway/b-roll
  events; banter durations moving toward the 24 s prior; nothing regressed.
- **Effort:** zero code. **Gate:** owner eyeball.

---

## W1 — Full-bleed framing (crop-to-action) — highest visual impact

**Problem [E]:** our clips letterbox a 16:9 source between blurred bands — real content
occupies **~35 % of frame height**; one sampled clip is a campus wide-shot where the
subject is tiny. **Every** sampled reference is full-bleed native vertical with the subject
at 80–100 % of frame height. This was the single most obvious difference on sight, and it
dwarfs the effect-density deltas the compare had been chasing.

**Bonus it fixes [M]:** source chat panel visible in **83 % of ours vs 30 % of refs**. The
stream's chat/overlay chrome sits at the frame edge — a side-cropping fill removes it for
free, closing that gap without a separate feature.

**Current code:** `stage7.py:291-294` (and the mirror in `profile_render.py:118`):
```
split[bg][fg];
[bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=..[blurred];
[fg]scale=1080:-2:force_original_aspect_ratio=decrease[sharp];   <- letterbox source
```

**Design — new render mode, default OFF:**
- `CLIP_FRAME_MODE=blur|fill` (default `blur` = today's behavior, byte-identical).
- `fill`: `scale=-2:1920,crop=1080:1920:x=<off>:y=0` — fill the height, crop the sides.
- **x-offset from deterministic CV, no VLM:** `visual_sense.motion_events()` already runs the
  cv2 loop (grayscale, `absdiff`, 160-px downsample). Extend it to also return the
  **column centroid of the frame-diff** per sample → aggregate to one stable x per clip
  (median, clamped to the legal crop range, hysteresis so it can't wobble).
- Fallbacks: no cv2 / no motion → center crop; centroid variance too high (subject moves
  across frame) → center crop. Never a moving crop in v1 — a static, well-chosen window.
- Per-category gating possible later (e.g. `fill` for irl/reaction, `blur` for gaming where
  the HUD matters).

**Risk:** cropping out a second speaker or on-screen action in wide shots. Mitigated by the
variance fallback + the owner gate. **Effort:** M. **Gate:** owner eyeball, same clips
rendered both ways.

---

## W2 — Duration discipline (the 85 s problem)

**Problem [E]:** three of four sampled clips run **85 s** — at judge scores 7–9 — against
reference banter/freakout of **14–24 s**. They sit near the 90 s hard cap on species whose
reference norm is ~25 s. **The judge does not penalize length**, so quality scoring cannot
catch this. **[M]** reference banter ends **3.0 s** after the payoff; ours lingers **6.1 s**.

Three independent levers, cheapest first:

**W2a — post-payoff tail trim (NEW, small, deterministic).**
Clip end = `payoff + tail_s` for payoff-shaped species (banter_roast/comedy, freakout),
`tail_s` from `config/shape_priors.json` (~3 s, per-species). Skip when payoff is unknown or
the trim would violate the 15 s minimum. Attacks the measured 6.1 → 3.0 gap directly, and
half the banter length excess is this tail. **Effort:** S.

**W2b — species-aware duration caps.** `stage4_moments.py:1215` is a blunt
`max_dur = 150 if storytime/emotional else 90`. Reference norms are per-species (banter ~25,
freakout ~27, monologue ~39, gaming ~49). Priors v2 already carries these as *soft prompt
hints* — they demonstrably don't hold (banter still rendered 41 s median). Proposal: a
**soft cap** (species norm × ~1.6) applied at selection, hard 90/150 retained as backstop.
**Risk:** hard caps from n=8–16 samples could hurt recall — hence *soft*, and gated.
**Effort:** S.

**W2c — jump cuts v2 [BUILT, default-off, owner gate outstanding].** The only lever that
condenses *within* a clip (references remove dead air; we render one contiguous window).
`CLIP_JUMP_CUTS=gaps` + eyeball → decide the category-gated default flip. See
[[concepts/plan-jump-cuts-v2-2026-07]]. **Effort:** zero code — just run and watch it.

---

## W3 — Caption presence A/B (the finding the compare structurally could not see)

**Problem [E]:** **4 of 5 sampled references carry NO word-by-word captions at all** — one
persistent hook line does the whole job. The one produced format (news) uses sparse
ALL-CAPS keyword captions with an accent word ("RAISING THE **SUBATHON**"). corpus_diff
measures caption *casing* and *speed* but never caption **presence**, so our
captions-on-everything default was never visible as a divergence.

**Design:** add a caption-mode dimension to the existing A/B variant lane
(`stage7._render_ab_variant`, already gated by `CLIP_AB_MIN_JUDGE_SCORE`):
- variant A = today (word-box captions), variant B = **hook-only** (no word captions).
- Optionally a third mode later: news-style keyword captions.
- Restrict to banter/freakout species first — that is where references are barest.

This is a **taste question only the owner can settle**, so the deliverable is a side-by-side
pair per clip, not a default flip. **Effort:** M. **Gate:** owner picks A or B.

---

## W4 — Caption grouping: stop straddling sentence boundaries

**Problem [E]:** groups read "advice. **You** were", "room? Yes. **So**", "I'm muted. **And**".
`kinetic_captions._group_words` (line 180) is a naive `range(0, len, 3)` slice with zero
punctuation awareness, so a new sentence starts mid-box.

**Design:** break a chunk when the previous word ends in `.!?` (and optionally on a long
inter-word gap, which marks a natural beat). Keep 3 as the *max* group size; allow shorter
final groups. Purely local to `_group_words`; both render paths inherit it.

**Effort:** S. **Risk:** low. **Verify:** unit selftest over crafted word lists + one
rendered clip eyeballed.

---

## W5 — Emoji in hooks (biggest cheap style gap — with a real technical risk)

**Problem [M/E]:** **68 % of reference clips use emoji** (hook lines like
"boy has to be stopped😹✌🏾🤌", "TBVNKS breaks down the Bruce drama 😭") vs **0.6 % of ours**.

**Lever:** the Stage-6 hook prompt + `config/hook_templates.json` (+ the P1.5 voice bank).
`kinetic_captions.normalize_overlay_casing` already passes emoji through untouched.

> [!warning] Spike this BEFORE promising it
> The hook card renders through **ffmpeg `drawtext` with Montserrat Black**. `drawtext`
> generally cannot render **color** emoji (no CBDT/COLR support in most builds) — expect
> tofu boxes or monochrome glyphs. **Step 1 is a rendering spike**, not a prompt change:
> render one hook containing 😭 and look at it. If drawtext fails, options are (a) a
> libass/ASS overlay for the hook (libass has better emoji handling), (b) compositing an
> emoji PNG, or (c) drop the idea. **Effort:** S if fonts cooperate, M if not.

---

## W6 — Gaming cut-rate diagnostic (blocks any gaming profile change)

**Problem [M]:** our gaming clips read **10.0 cuts/30 s** vs reference 3.31 — but the
effects log shows only ~1–2 injected effects per clip, so injected effects cannot explain
it. Candidates: source-native game-cam switching, the scene detector over-firing on fast
gameplay motion, or jump-cut/transition artifacts.

**This is a diagnostic, not a fix:** eyeball 2–3 gaming clips, compare raw vs rendered
detector output on the same file. **No gaming profile knob should be touched until this is
explained** (the 07-17 recalibration deliberately left gaming alone for this reason).
**Effort:** S.

---

## W7 — Split-screen hook (deferred behind W1)

**[M/E]** 12/115 references use split-screen — calm/chaos before-after for storytime, a grid
for news. It is a genuine format we cannot produce. Deferred: it composes the frame, so it
must land *after* W1 settles what a frame looks like. **Effort:** L.

---

## W8 — Close the measurement gaps (so these stop being one-off discoveries)

Every W3/W5/W1 finding was invisible to `corpus_diff` because it aggregates only what it was
told to. Add to the card schema + diff:
- `captions_present` (bool) → **caption-presence %** per side
- `emoji` already exists on cards but is **not aggregated** → add an emoji % row
- `framing` (`full_bleed | letterboxed | pillarboxed`) → framing % per side

Also: **subtype-aware joins** (corpus_diff still groups by `category` only; the blended
irl_moment aggregate produced one artifact already) and backfill subtypes on the 78 pre-v3
our-cards. See the 2026-07-17 audit log entry. **Effort:** M.

---

## Sequencing

| Wave | Contents | Rationale |
|---|---|---|
| **0** | W0 verification run | Unblocks everything; zero code |
| **1** | W2a tail trim · W4 caption grouping · W5 emoji **spike** · W6 gaming diagnostic | Small, local, low-risk; two are diagnostics |
| **2** | W1 full-bleed framing · W2c jump-cuts gate | The two big visual levers; each needs its own eyeball |
| **3** | W3 caption-presence A/B · W2b soft caps | Taste + tuning, after the frame is settled |
| **4** | W8 measurement · W7 split-screen | Infrastructure + new format |

**Every render-affecting item ships default-OFF behind a flag and reverts by unsetting it.**

## Explicitly NOT doing (and why)

- **SFX density reduction** — owner veto; intentional style.
- **On-beat cut alignment** — references are predominantly "loose" too [M]; we already match.
- **Music beds** — ours are stream-native, the pipeline adds none; no render lever exists.
- **Freeze-bait** — ~3 % on both sides; already matched.
- **`chat_overlay_pct` gap item** — 1 clip in 115; threshold noise.
- **`caption_wps` gap item** — ours is measured as raw speech rate (our decompose runs
  `ocr=False`), and the named lever cannot move words-per-*second* anyway.

## Related
- [[concepts/plan-jump-cuts-v2-2026-07]] — W2c, already built
- [[concepts/reference-shape-guide-2026-07]] — species norms feeding W2a/W2b
- [[concepts/style-profiles]] — the 07-17 transition trims W0 verifies
- [[concepts/captions]] — W3/W4/W5 surface
- [[concepts/clip-rendering]] — W1 surface
- [[concepts/reference-lab]] — W8 measurement home
