---
title: "Plan — Reference-Clip Deconstruction: Cards → Diff → Apply (learning loop B rebuild)"
type: concept
tags: [plan, learning, reference-clips, forensics, attribute-cards, diff, few-shot, stage6]
status: in-progress
updated: 2026-07-13
---

> [!note] R0 + R1 IN PROGRESS (2026-07-11)
> **R0 COMPLETE — 59/59 timelines, all parse-valid.** Ran as a bounded CPU batch
> (`clip_forensics --ocr --trim-end 4 --no-llm`; `--no-llm` keeps LM Studio free for R1 and drops
> the superseded text-only style_profile). Survived a mid-run PC crash — resumed cleanly (the
> missing-set is recomputed each run; clip_forensics writes a timeline only on full completion, so
> no partial/corrupt files). **R1 tool SHIPPED + validated**:
> `scripts/research/attribute_cards.py` — one multimodal call/clip, Python-authoritative numerics
> merged with LLM editorial fields, output `reference_clips/.cache/<stem>.card.json` (schema v1,
> gitignored — derived artifact). **6 cards built for the owner spot-read gate.** Decisive
> validation: on `GeorgeBushFailJokeClip` the VLM read the on-screen hook as a clean
> `"you ever heard of george"` — the *exact* line EasyOCR mangled into "yoU ever heard of george
> TkTok KS" that poisoned caption-voice v1. The VLM also correctly *distrusted* noisy CLAP labels
> (set `sfx_grammar.kinds=[]` on a clip whose CLAP was 28× "bruh").
>
> **R1 GATE CLEARED (owner spot-read, 2026-07-11):** StreamerUpdate cards = "great outputs"
> (they directly inspired the [[concepts/plan-news-compilation-2026-07]] mode); George-Bush card
> = "alright" — the model can't know the external joke context ("absurd to expect"), but it
> "picks up on the auxiliary and main aspects… great for generalization" (exactly the designed
> behavior: cards capture STRUCTURE, the meme-format library carries external context);
> ReemKnocks "isn't that bad either". **Category question resolved by the owner's own reaction:**
> one "irl" bucket hid a format distinct enough to spawn a new pipeline mode → taxonomy refined
> to `street_interview|news_compilation|irl_moment|reaction|rap_freestyle|gaming|story|skill|
> controversy|other` and the FULL 59-card batch launched (rebuilds the 6 for consistency; 2h
> hard cap, failure-soft per clip).
>
> **R1 COMPLETE (2026-07-11): 59/59 cards, 0 skipped, mean confidence 0.94.** The refined
> taxonomy genuinely separated the corpus: `irl_moment` 38, `reaction` 7, **`news_compilation` 5**
> (the StreamerUpdate set = the [[concepts/plan-news-compilation-2026-07]] format spec),
> `gaming` 5, `story` 2, `street_interview` 1 (George Bush), `controversy` 1.
>
> **R2 tooling SHIPPED, first run in flight** — `scripts/research/our_clip_cards.py`: the same
> deconstruction over OUR produced clips into a RUN-SCOPED cache
> (`clips/.diagnostics/cards/<run>/`; `clip_forensics.decompose` + `attribute_cards.build_card`
> gained `--cache-dir` so our artifacts never mix into the reference cache). Primaries only by
> default ((B)/(Short) would double-count moments); each card merges `_ground_truth` from
> `effects_log.jsonl` (normalized-title match — log titles are RAW, filenames Stage-7-sanitized;
> gotcha: the effects run stamp is the pipeline START time, so the A/B run is `20260710_143929`
> in effects_log vs `last_run_20260710_202308` in diagnostics). No OCR/no text-LLM on our clips
> (we know our burned text; cards supersede the old style_profile).
> **R3 tool SHIPPED** — `scripts/research/corpus_diff.py`: per-category deterministic aggregates
> (OUR sfx counts prefer ground truth over CLAP), 25%-relative gap threshold, every gap item
> `lever:`-tagged (sfx_cues / style_profiles / hook_templates / caption bank / jump-cuts, or
> `feature-card`), category-coverage misses surfaced (news_compilation → the new mode), optional
> LLM narrative; outputs `corpus_diff_<date>.md` + machine-readable `.json` (the future R6
> approve queue).
>
> **R2 COMPLETE + R3 FIRST REPORT GENERATED (2026-07-11)** — 9/9 our-clip cards (all with
> `_ground_truth`; needed a PREFIX title match — filenames are the title `[:50]`-truncated) →
> `clips/.diagnostics/corpus_diff_20260711.md` (13 gap items). **Headline finding: the loop
> independently reproduced the owner's own recurring critique** — our clips are SFX-sparse vs
> the corpus (median 0.88 injected cues/30s vs the reference's dense sound furniture; the owner
> said "I wish there was sound effects" unprompted on 2026-07-09). Also: caption casing `mixed`
> vs reference `sentence case` (voice-bank/P1.5 lever), and coverage misses (news_compilation →
> the new mode; gaming = corpus-vs-run mismatch, our test VODs are IRL).
>
> **Measurement asymmetries filed (fix before trusting magnitudes, not directions):**
> (a) reference SFX counts come from raw CLAP events which over-fire on speech exclamations
> (28× "bruh" on one clip) — reference `31/30s` is inflated; ours is exact ground truth. Fix:
> dedupe consecutive CLAP windows + drop speech-like labels on the reference side.
> (b) our `caption_wps 3.08` is the SPEECH-rate fallback (our clips skip OCR) vs the
> reference's burned-caption OCR rate — not the same metric. Fix: derive our caption wps from
> the clip SRT (we own the exact timing). (c) our cut counts may include burned zoom-punches
> (PySceneDetect reads them as cuts). Directions are trustworthy; magnitudes need these fixes.
>
> **Candidate metric promotions (2026-07-13, owner Q&A on visual-comedy convergence):** the
> cards already capture `comedy.verbal_vs_visual` and clip duration, and `_agg` aggregates
> `verbal_vs_visual` — but neither is a gap-item RULE yet (`_cmp` skips them; they reach only
> the narrative LLM). Promote when wanted: (d) `verbal_vs_visual_top` as a categorical rule
> (like casing) → "reference: both, ours: verbal" would become an approvable item — the diff's
> lever for physical/visual comedy the transcript can't see; (e) `duration_med` per category →
> would catch "our storytime clips run short vs reference storytimes". Each is a 3-line change
> (`_agg` key → `_cmp` tuple → `_LEVERS` entry) per the closed-rules doctrine in
> [[concepts/reference-lab]].

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

## Phase R6 — "Reference Lab" dashboard tab (owner req 2026-07-10: see + execute the reverse pipeline on demand)

The CLI tools (R0–R3) are the substrate; this tab is their permanent face, so running the
reverse pipeline never requires an agent session. Follows the dashboard's existing
architecture (Flask blueprint + ES module + panel section — [[entities/dashboard]]).

**Backend** — new `dashboard/routes/reference_routes.py` (registered like the other 8
blueprints):
- `GET /api/reference/corpus` — the corpus table: every video in `reference_clips/` with
  per-clip status badges — decomposed? (timeline.json), card? (card.json), notes state
  (none / `_draft` / corrected), duration/size, mtimes.
- `POST /api/reference/decompose` `{clips: [...] | "missing"}` — bounded background job
  running `clip_forensics --ocr --trim-end 4` per clip (R0).
- `POST /api/reference/cards` `{clips: [...] | "missing"}` — `attribute_cards.py` job (R1);
  refuses when LM Studio is down (reuse `check_lm_studio`).
- `POST /api/reference/diff` `{runs: [...]}` — `corpus_diff.py` job (R2+R3) against chosen
  run(s); result listed under reports.
- `GET /api/reference/card/<stem>` · `GET /api/reference/report/<id|latest>` — viewers' data.
- `GET /api/reference/job` — status + log tail (poll; same pattern as the pipeline log).
- `POST /api/reference/approve` `{report_id, item_id, verdict}` — records approve/reject per
  diff line into `clips/.diagnostics/diff_approvals.json`. **Approving does NOT auto-apply**
  (doctrine): approvals are the queue the agent works through in R4, each becoming a small
  reviewed config/prompt commit.

**Job safety (owner's no-zombie directive + GPU contention):** the reverse pipeline shares
the GPUs with the forward one (whisper CUDA, CLAP, 35B calls) — so ONE job at a time, mutual
exclusion BOTH ways with the clip pipeline via the same on-disk pid-marker pattern as BUG 67
(`reference_job.pid`; forward play → 409 while a lab job runs, lab buttons → 409 while the
pipeline runs), hard per-job timeout, Stop button kills the process tree, done-marker +
log file for progress polling.

**Frontend** — `dashboard/static/modules/reference-panel.js` + a "Reference Lab" tab in
`index.html`: corpus table with status chips + select-all (mirrors the VOD Library pattern);
buttons "Decompose missing (N)" / "Build cards (N)" / "Run diff vs run…" (run picker from
`clips/.diagnostics/`); job progress area; a card viewer (structured fields + the essence
commentary paragraph); a report viewer rendering the diff markdown with per-line
Approve / Reject / Later controls. No external CDNs (dashboard is local-only).

**Effort:** ~one session once R0–R3 tools exist (the tab only shells to them). **Gate:** none
beyond the R1/R3 gates it surfaces — it's a control surface, not new behavior.

> [!note] R6 SHIPPED 2026-07-12 — replaced the old Clip Forensics tab
> The **Reference Lab** tab now drives the whole loop from the dashboard. Built:
> `dashboard/routes/reference_routes.py` (10 endpoints: corpus, decompose, cards, our-cards,
> diff, stop, job, card, report, approve) + `scripts/research/decompose_corpus.py` (R0 batch —
> the dashboard can't run a shell loop) + `dashboard/static/modules/reference-panel.js` + the
> tab markup (reuses the `.fx-*` styles). The old `forensics_routes.py`/`forensics-panel.js`
> were **deleted** (the single-clip decompose is subsumed by the corpus "Decompose" button).
> **Job model:** one background subprocess at a time, streamed to `reference_job.log` the UI
> polls; mutual-exclusion 409 BOTH ways with the clip pipeline via `is_reference_running()`
> (added to `pipeline_runner`) + `_state.reference_job`; bare-metal only; Stop button (taskkill
> tree). **Approve/reject** writes verdicts straight into the R4 queue `diff_approvals.json` —
> the report viewer joins each gap item to its current verdict; nothing auto-applies (an agent
> works the queue into config commits). **Verified live** (booted on :5099): corpus 60 clips /
> 59 carded, the newest gap report loads with the already-approved SFX items showing "approved",
> job idle, tab served. Owner UX: open Reference Lab → 1·Decompose → 2·Build cards → pick a clip
> run → 3·Card our clips → 4·Gap report → approve/reject each lever — no CLI.

> [!note] R6 v2 — Clipper-style UX simplification (2026-07-12, owner: "kind of confusing")
> The numbered 1→4 workflow confused the owner; the tab now mirrors the Clipper's UX language
> (check rows in a table → press one big button). **Reference Clips** panel = a `.vod-table`
> clone (select-all header, checkbox rows, row-click toggle, status "✓ analyzed · category" /
> "not analyzed", per-row "card" viewer). **Reference Controls** = a run dropdown + THREE
> buttons: **Analyze Selected (N)** (chains decompose-if-missing + card rebuild for the checked
> clips), **Analyze New** (only clips without a card), **Compare → Gap Report** (chains
> card-our-clips-missing-only + report). Backend: the 4 step endpoints were REPLACED by
> `/api/reference/analyze` (`reference_analyze.py`) + `/api/reference/compare`
> (`reference_compare.py`) — each button = one bounded background job. Gap-report items render
> in PLAIN LANGUAGE ("Sound effects per 30s — all clips: theirs 31.7 vs ours 5.4") with
> **✓ Fix it / ✗ Not a problem** buttons. Verified live: Analyze New found + carded the one
> new clip (60/60 now), Compare skipped 9 cached cards and regenerated the report, 409 guard
> held mid-job, test instance shut down clean, owner's dashboard restarted (BUG 70 rule).
> Note: a re-run of Compare mints a NEW report date, so verdicts start fresh (history persists
> under the old date in `diff_approvals.json`).

> [!note] R6 v2.1 — Analysis-model picker + judged-report export (2026-07-13, owner req)
> Two additions after the owner asked "does the Lab use an LLM like the pipeline?" (yes — one
> vision call per card + one text call for the report narrative; decompose is CPU-only) and
> "can I copy the judged diff_approvals report?" (didn't exist yet). (1) **Analysis model
> dropdown** — same selection experience as the Clipper's Models panel: populated from
> `/api/models/available`, default "pipeline default — `<text_model>`" (surfaced as
> `default_model` on `/corpus`); a non-default pick rides the analyze/compare POST as `model`
> and becomes a **job-scoped `CLIP_TEXT_MODEL`** (`_model_env` → `_start_job(env_extra)`),
> which `cf._llm_config()` already resolves first — no config file touched, override dies with
> the job. Proven end-to-end: rebuilding one card under `qwen/qwen3.5-9b` stamped
> `_model: qwen3.5-9b` AND changed its category (`story` vs the 35B's `irl_moment`) — model
> choice measurably changes card judgment; restored on the default. (2) **Copy judged report**
> (`GET /api/reference/approvals-export?date=`) — joins the report items with their
> `diff_approvals.json` verdicts into one humanized markdown doc grouped
> ✅ approved / ❌ rejected / ➖ no-action / ❓ unjudged, writes
> `clips/.diagnostics/corpus_diff_<date>_judged.md`, and the UI button copies it to the
> clipboard with a `✓ copied (5✓ 0✗ 2?)` flash. Verified on 20260711: counts
> {approved 5, no-action 4, unjudged 2}, file written; the `/report` glob excludes `_judged`
> stems so exports never shadow real reports. Owner guide for all of this: [[concepts/reference-lab]].

---

## Sequencing, effort, gates

```
R0 coverage (batch, no gate)
  → R1 cards (batch ~45 min) → GATE: owner spot-reads 5 cards
  → R2 our-clips cards (per run, fast)
  → R3 first diff report      → GATE: owner approves items
  → R6 Reference Lab dashboard tab (control surface over R0–R4: corpus table,
        execute buttons, card/report viewers, approve/reject per diff line)
  → R4 apply commits (each small, reviewed)   → next run closes the loop
  → R5 retrieval few-shot (flag) → GATE: spot-check run → default-on
```

Estimated build: R0–R3 one session; **R6 one session** (thin face over the CLI tools; first
diff report arrives via CLI before the tab exists); R4 is ongoing practice; R5 a second
session. Steady state: the owner drops new reference clips into `reference_clips/`, opens the
Reference Lab tab, clicks Decompose/Cards/Diff as needed, and approves lines — no agent
session required to *run* the loop, only to *apply* approved items (R4).

Related: [[concepts/plan-clip-forensics]] (the decomposer this builds on),
[[concepts/corpus-learning-loop-2026-07]] (Phase 7 — superseded in part by this),
[[concepts/plan-captions-and-ab-variants-2026-07]] (voice bank consumer),
[[concepts/case-incongruity-comedy]] (the blind-spot class R3 will surface),
[[concepts/plan-learning-activation-2026-07]] (loop A — de-emphasized, keeps banking labels).
