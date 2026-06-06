---
title: "Clip-Quality & Perf Remediation Plan (2026-06 session review)"
type: concept
tags: [plan, remediation, vision, grounding, stage-5.5, vision-judge, pass-c, scoring, performance, stage-6, session-review, text, vision]
sources: 1
updated: 2026-06-06
---

# Clip-Quality & Perf Remediation Plan (2026-06 session review)

Derived from the 2026-06-06 review of the `20260606_071210_20260424_2xRaKai` session (rakai 193-min VOD, `qwen3.6-35b-a3b` split across the NVIDIA+AMD pool, 58 min, 10 clips, exit 0). The run **completed cleanly** — these are quality/perf refinements, not crash fixes. Each fix below is grounded in code (file:line) from a three-pronged investigation. **Plan only — nothing here is implemented yet** except the already-shipped [[concepts/bugs-and-fixes#BUG 60]] backtick fix.

> [!note] Active pipeline path (correction worth recording)
> The investigation confirmed the **live** entrypoint is `scripts/run_pipeline.py` → `scripts/pipeline/stages/stage{1..8}.py`, each shelling out to the heavy `scripts/lib/stages/*.py` modules. The `clip-pipeline.sh` bash orchestrator referenced in `CLAUDE.md` / [[concepts/modularization-plan]] is **legacy** on bare-metal Windows. Update those references when convenient.

---

## Findings summary

| # | Issue | Severity | Evidence (this run) | Status |
|---|---|---|---|---|
| 1 | **Vision REGEN → ungrounded fallback titles** — clips ship raw pattern-name titles like `"Pattern setupexternalcontradiction Streamer claims"` | **P1 quality (user-visible)** | many `REGEN still fails for title/hook (judge_low_weighted)`; final manifest title byte-identical to the garbage string | ✅ **SHIPPED 2026-06-06** (Fix 1A-D) |
| 2 | **Stage 5.5 vision-judge tournament cost** — 620 s (~18% of wall-clock), serial | **P2 perf** | `Stage 5.5/8 — Vision Judge: 620.0s`; 24 comparisons × ~25.8 s, sequential | ✅ **2B parallelized SHIPPED 2026-06-06** (~2×); 2A/2C documented |
| 3 | **Pass C score display saturates at 1.000** — all 10 finals show `score=1.000` | **P3 clarity (cosmetic)** | raw 1.33–1.54 → displayed 1.000; selection itself is correct | ✅ **3A SHIPPED 2026-06-06** (→ 0.83-0.96 spread); 3B deferred |
| 4 | **torchcodec not installed** — diarization on fallback decoder | **P4 robustness** | `torchcodec is not installed correctly…`; still got 4621/4706 segments | Plan below |
| 5 | **A1 arcs don't win selection** — 5 arcs detected, 0 in final 10 | **P5 tuning (deferred)** | see [[concepts/arc-aware-extraction]] §Verified-in-production | Deferred to arc Phase 3 |

Sequencing recommendation: **1 → 2 → 3 → 4**, with 5 folded into the arc plan's Phase 3. Rationale: #1 is the only one a viewer sees; #2 is the biggest single time sink and partially a "should it even run?" question; #3/#4 are low-risk hygiene.

---

## Fix 1 — Vision REGEN → ungrounded fallback titles (P1, quality)

> [!success] SHIPPED 2026-06-06 (`stage6_vision.py`)
> All four sub-fixes are in: **1A** `_ground_field()` dispatch — `title`/`hook` now run Tier-1 denylist + hard-event check only (`min_overlap=0.0`, no judge), `description` keeps the full cascade; used in both the initial loop and the REGEN recheck. **1B** `_derive_baseline_title()` strips the `^Pattern <id>:` prefix (regex). **1C** when a vision title is nulled but the description passed, the title is synthesized from the description's first clause instead of the baseline. **1D** stale `f"Clip_T{T}"` comments corrected here + in [[concepts/vision-enrichment]]. Compile clean; unit-tested that the canonical garbage title `"Pattern setupexternalcontradiction Streamer claims"` no longer occurs. Next live run should show ~zero `REGEN still fails for title/hook`.

### Root cause (confirmed, file:line)
The grounding cascade is applied **uniformly** to `title`, `hook`, and `description` in Stage 6 (`stage6_vision.py:584-594`, `cascade_check(..., min_overlap=0.15)`). The cascade's Tier-2 **LLM judge** (`grounding.py:312-395`) scores five dims and weights **`grounding` at 0.55** (`grounding.py:376-382` / `config/grounding.json`), with a pass threshold of **5.0** (`grounding.py:383, 492-495`). The judge prompt explicitly rewards literal paraphrase ("paraphrases what the streamer actually said scores 8-10", `grounding.py:290`).

But `title`/`hook` are **designed to be non-literal** — the Stage 6 prompt itself asks for a "short **viral** title" and a hook "in the voice of a content creator" (`stage6_vision.py:445-457`). A catchy title has low literal `grounding` and the `speaker`(0.05)/`callback`(0.10) dims default to ~0 for ordinary solo moments, so the weighted mean lands **below 5.0 even when the title is clean** → `reason=judge_low_weighted`. This is a **false-positive grounding failure** on creative copy.

When the single REGEN retry (`stage_6_retry_count=1`, `stage6_vision.py:605-668`) also fails, the field is **nulled to `""`** (`stage6_vision.py:678-696`, `parsed[_field] = ""`). The non-empty guard at `stage6_vision.py:714-716` (`if v_title and v_title != ""`) then fails, so the entry **keeps its baseline title** from `_derive_baseline_title` (`stage6_vision.py:244, 153-182`). Baseline preference #1 is the **first sentence of Pass B's `why`** — which the Pattern-Catalog prompt formats with a literal `"Pattern <id>:"` prefix (`stage4_moments.py:1547`). Stage 7's sanitizer (`stage7.py:98-101`) strips the underscores/colon → **`"Pattern setupexternalcontradiction Streamer claims"`** (reproduced byte-for-byte by the investigation).

> [!note] The fenced-JSON bug ([[concepts/bugs-and-fixes#BUG 60]]) was a *historical* contributor (parse fail → `None` → baseline title, no REGEN lines). It's fixed now; the **current** failures with explicit `REGEN … judge_low_weighted` lines are the judge, not the parser.

> [!warning] Stale comments mask this bug
> `stage6_vision.py:158-162` and `concepts/vision-enrichment.md:149` still claim the fallback is `f"Clip_T{T}"`. That changed when `_derive_baseline_title` landed; whoever last reasoned about it assumed a harmless `ClipT7613`, not a sanitized pattern label. Fix the docs as part of this.

### Plan (4 changes, smallest blast radius first)
- **1A — Exempt `title`/`hook` from the Tier-2 judge; keep the denylist + hard-event guard** *(primary fix)*. In `stage6_vision.py:584-594` (and the mirrored retry recheck at `:644-668`), run **Tier-1 only** (`grounding.check_claim` — keeps the regex denylist + Phase 2.4d hard-event check that stops "gifted subs in a title with no sub event") for `title`/`hook`, and the **full `cascade_check`** for `description` (which *should* be literally grounded, per `stage6_vision.py:451`). This keeps the dangerous-hallucination guard while letting creative-but-clean titles through — directly kills most spurious REGENs.
- **1B — Stop the pattern label leaking into the title stream at the source.** The Pass B `why` template prefixes `"Pattern <id>:"` (`stage4_moments.py:1547,1558`); the pattern is *already* stored separately as `primary_pattern` (`stage4_moments.py:960-961`). Either (i) instruct the Pass B prompt to write `why` as a plain sentence **without** the prefix, or (ii) strip a leading `^\s*Pattern\s+[a-z_]+:\s*` in `_derive_baseline_title` (`stage6_vision.py:170-176`) before using `why`. Do (ii) regardless — it's a one-line guarantee that no fallback ever ships a raw pattern label.
- **1C — Better fallback: synthesize the title from the *grounded description*, not the pattern `why`.** When `title` is nulled but `description` passed grounding, derive the title from the first clause of that description (modify the null-handling at `stage6_vision.py:678-696` or the enrichment guard at `:714-716`). A grounded description is a far better title seed than the Pass B debug string.
- **1D — Doc/comment fixes.** Correct `stage6_vision.py:158-162` and [[concepts/vision-enrichment]] to describe `_derive_baseline_title`'s real behavior.

**Alternatives considered:** globally lowering `judge.pass_threshold` 5.0→3.0 (rejected — also weakens `description` + Pass B `why` grounding); re-weighting the judge to renormalize over applicable dims (`grounding.py:386-395`) is a reasonable *additional* step but higher blast radius than 1A.

**Risk:** Low. 1A narrows what the judge gates but preserves the denylist/hard-event guard (the real anti-hallucination net — `concepts/highlight-detection` Tier-1 is the safety net, the judge is the soft tier). **Effort:** ~30-40 lines across two files. **Verify:** re-run the rakai VOD; expect near-zero `REGEN still fails for title/hook`, zero `"Pattern …"` titles in the manifest, descriptions still judged.

---

## Fix 2 — Stage 5.5 vision-judge tournament cost (P2, perf)

> [!success] SHIPPED 2026-06-06 (`vlm_judge.py`, `stage5_5_judge.py`, `config/judge.json`)
> **2B (parallelize) — the main win.** `swiss_tournament()` gained a `workers` param: each Swiss round now collects its (independent) pairings and dispatches the `compare()` calls through a `ThreadPoolExecutor`, folding results sequentially; rounds still re-rank between themselves. Verified by unit test that parallel and serial produce **identical rankings** (pairings are fixed from the round-start order, each item plays once per round, so it's race-free). `stage5_5_judge.py` resolves `JUDGE_WORKERS` env → `judge.json:workers` (default **2**, matching Stage 6's cap for the shared LM Studio vision model on the split pool) and locks the `outage_streak` circuit-breaker. Expected ~1.8-2× → **620 s ≈ 320-340 s** at 2 workers.
> **2A (gate).** The investigation confirmed Stage 5.5 only **re-orders/re-weights a set that renders in full** (`stage6.py:40`), so the existing `judge.json:enabled=false` is already the correct hard off-switch — documented in the config `_workers_note`. Not defaulted off (it still orders the clips); the user can disable after measuring whether the re-rank earns its (now-halved) cost.
> **2C (dials).** `max_comparisons` / `frames_per_clip` / `shortlist_max` were already config-tunable; `workers` added alongside. No default changes (avoids quality regression) — the parallelization is the win.

### Root cause (confirmed, file:line)
Stage 5.5 is a **seeded Swiss tournament** (`vlm_judge.py:199-273`) re-ranking the top `min(shortlist_max=12, n)` Pass-C moments. It issues up to `max_comparisons=30` pairwise vision calls (`config/judge.json:9`; empirically **24** this run), each sending **2 clips × 4 frames = 8 inlined JPEGs** + transcript blocks to the 35b (`vlm_judge.py:148-196`). At **~25.8 s/call, strictly serial**, that's the 620 s.

It is **sequential** — the comparison loop calls `compare(a, b)` one at a time at `vlm_judge.py:256`; the module imports no `threading`/`concurrent.futures`. **Stage 6 enrichment, by contrast, was parallelized** in the prior optimization sweep (`stage6_vision.py:882-891`, `ThreadPoolExecutor` + `_VISION_NET_FAIL_LOCK` + `STAGE6_WORKERS`). Stage 5.5 never got that treatment, and it runs as a **blocking subprocess before Stage 6** (`stage6.py:31-33`), so the two vision stages are fully serial: 620 s + 428 s ≈ 1048 s of vision time.

> [!note] Decision point: does 5.5 earn its cost at all?
> Stage 5.5 only changes **order/weight**, never the **set** — and in the current config **every detected moment renders anyway** (`stage6.py:39-40`). So a 620 s re-rank of a set you keep in full only matters if (a) the reweight changes which clips win per-time-bucket selection, or (b) over-selection (more candidates than clips) is enabled. Worth measuring its actual selection impact before optimizing it.

### Plan (gate first, then parallelize, then dial)
- **2A — Gate it** *(do this first)*. Skip Stage 5.5 when `len(shortlist) <= target_clip_count` (nothing to discriminate) or when over-selection is off. Anchor: the dispatch in `stage6.py:31-33` or the `too_few` guard in `stage5_5_judge.py:99-100`. **When applicable: −620 s outright.** First measure whether the reweight ever changes the final 10 vs not running it — if it rarely does, gating is the whole fix.
- **2B — Parallelize within each Swiss round** *(mirror Stage 6)*. Rounds are dependent (re-rank between them) but **all pairings inside a round are independent**. Refactor the inner loop (`vlm_judge.py:229-273`) to collect a round's pairings, dispatch via `ThreadPoolExecutor`, then fold results under a lock on `wins`/`games`/`played`/`outage_streak`. Add a `JUDGE_WORKERS` env knob like `STAGE6_WORKERS`. **Expected ~2-3× → 620 s to ~210-310 s.** (Sub-linear: LM Studio's vision encoder may serialize internally and the Vulkan split is already saturated — Stage 6 caps at 2 workers for this reason, `stage6_vision.py:129-132`.)
- **2C — Config dials (zero-risk, stackable).** `max_comparisons` 30→15 (`config/judge.json:9`) ≈ −50%; `frames_per_clip` 4→2 (`config/judge.json:7`) ≈ −20-35% per-call prefill; `shortlist_max` 12→8 (`config/judge.json:6`) fewer rounds+pairings. Pure config, trades ranking resolution for speed.
- **2D — Early-termination.** `swiss_tournament` already accepts `should_stop` (`vlm_judge.py:206,270-271`, wired only to outage/deadline). Add a convergence check (stop when the top-K is stable across a round). Variable −20-40%.

**Alternatives considered:** a smaller/faster judge model (`vlm_judge.py:62-77` supports a `model` override) — **rejected for now**: it reintroduces a VRAM swap, negating the "5.5 reuses Stage 6's already-loaded model" benefit, and a second model is costly on the 16 GB+12 GB split. Frame-cache (encode each clip's JPEGs once vs per-game) — minor CPU win, not the bottleneck.

**Risk:** 2A low (pure skip; measure impact first). 2B medium (concurrency on shared tournament state — needs the same lock discipline Stage 6 already proved). 2C none. **Verify:** compare `judge_tournament.json` rank order + final-10 set with/without each change; confirm Stage 5.5 timing in `stage_timings`.

---

## Fix 3 — Pass C score display saturates at 1.000 (P3, cosmetic)

> [!success] SHIPPED 2026-06-06 (3A; `stage4_moments.py`)
> Replaced the hard `min(raw, 1.0)` display clamp at the Pass C output (`:2797`) with a soft-squash `min(raw / _DISPLAY_SCALE, 1.0)` (`_DISPLAY_SCALE=1.6`, `CLIP_DISPLAY_SCORE_SCALE` env). Verified on the 6/6 run's 10 selected `raw_score`s: the display went from **10 tied 1.000s → 10 distinct, monotonic values 0.830-0.965** (rank preserved). **Confirmed display-only**: selection ranks on the unclamped `final_score`, and Stage 6 drives all its score math off `raw_score` (the `score` field there is only a clamped display + one log line). **3B (reactivate inert reaction/engagement axes) NOT done** — deferred: it changes selection and the axes are likely inert due to missing chat/audio inputs on this VOD, not bad thresholds. **Note:** Stage 6's *final* manifest clamp (`stage6_vision.py` `min(new_raw,1.0)`) is unchanged by design (BUG 37: `raw_score` carries true magnitude); extending the squash to the user-facing manifest/Discord score is a separate UX call.

### Root cause (confirmed, file:line)
The displayed `score` is a **hard `min(raw, 1.0)` clamp** at `stage4_moments.py:2797` (`display_score = round(min(max(raw,0.0),1.0),3)`). With raw `final_score` 1.33–1.54, all top clips flatten to 1.000. **Selection is unaffected** — ranking runs entirely on the unclamped `final_score` (`stage4_moments.py:2505,2695,2718`), and the unclamped value is already preserved as `raw_score` (`:2805`) and logged (`:2873`). So this is purely a **display** issue (intentional per the BUG 37 comment, but it over-flattens).

The deeper reason the raw range is itself narrow (1.33–1.54): the four selection-axis multipliers are compressed near 1.0 and their **product is clamped to [0.8, 1.35]** (`stage4_moments.py:2426`), with **254/256 candidates pinned at the 1.35 ceiling**. Of the axes, only **baseline** does real work (median 1.05, reaches 1.18); **reaction** (median 1.001) and **engagement** (median 1.0) are **near-inert** — almost certainly because this VOD had **no chat** (`chat=False` in the axis deps) and audio-only reaction signal. So even fixing the display won't widen a genuinely thin underlying spread.

### Plan (display first, axes optional)
- **3A — Replace the hard clamp with a soft squash** *(one-liner, low risk)*. At `stage4_moments.py:2797`, map the known raw range through a monotone saturating curve, e.g. `display = round(min(raw/1.6, 1.0), 3)` (divide by an empirical max instead of clamping at 1.0). Keeps absolute meaning + cross-VOD comparability, removes the plateau. **Alternative:** min-max normalize across the selected set (full 0–1 spread, but batch-relative and exaggerates flat fields), or rank-percentile display (ordinal-faithful, discards magnitude). Soft-squash is the safest default.
- **3B — Reactivate the inert axes** *(deeper, optional)*. reaction/engagement fire for almost no candidate. Raising their `gain` (`config/selection_axes.json:12,38`) or loosening gating thresholds would inject real spread — **but** investigate *why* they're inert first (missing chat/audio inputs vs over-tight thresholds), and beware reintroducing the energy-bias the global clamp was added to suppress. Pair with widening axis bounds (`selection_axes.json` ceils + global `:7`) only alongside 3A.

**Risk:** 3A trivial (display-only; selection untouched — but verify no downstream consumer of `hype_moments.json`'s `score` assumes the old clamp). 3B medium (tuning, could shift selection). **Verify:** finals show a spread (not ten 1.000s); `raw_score` unchanged; final-10 set identical after 3A.

---

## Fix 4 — torchcodec / diarization robustness (P4)

`pyannote` warns `torchcodec is not installed correctly so built-in audio decoding will fail` and falls back (it still labeled 4621/4706 segments, so M1 works, just fragile + noisy). **Plan:** install a torchcodec build matching the venv's torch/CUDA in `.venv`, or pin a known-good version in the requirements; confirm the warning disappears and diarization coverage holds. Low effort, low risk, pure robustness. See [[entities/diarization]].

---

## Fix 5 — A1 arcs don't win selection (P5, deferred)

5 cross-chunk arcs were detected but none reached the final 10 (the 1.4× arc boost lost to keyword/LLM moments under the 1-clip-per-time-bucket distribution). This is **Phase 3 of [[concepts/arc-aware-extraction]]** — a precision/tuning question (boost weight vs bucket cap), explicitly gated behind the "quality > quantity" decision rule. Don't raise the arc boost blindly; measure via `judge_tournament` whether arc clips *win pairwise* first. Tracked there, not here.

---

## Effort & sequencing summary

| Fix | Effort | Risk | Payoff | Order |
|---|---|---|---|---|
| 1 Vision titles | ~30-40 LOC, 2 files | Low | High (user-visible) | **1st** |
| 2A gate 5.5 | small + measurement | Low | −620 s when it applies | **2nd** |
| 2B parallelize 5.5 | medium (concurrency) | Medium | ~2-3× on 5.5 | 3rd |
| 2C/2D config dials | config / small | None–Low | −20-50% on 5.5 | with 2B |
| 3A score display | 1 line | Trivial | Clarity | 4th |
| 4 torchcodec | install/pin | Low | Robustness | 5th |
| 3B / 5 axis+arc tuning | tuning | Medium | Deferred | later |

---

## Related
- [[concepts/vision-enrichment]] — Stage 6 (Fix 1 lives here; doc fix needed)
- [[entities/grounding]] / [[entities/grounding-ab]] — the judge cascade Fix 1 adjusts
- [[entities/vision-judge]] — Stage 5.5 (Fix 2)
- [[concepts/highlight-detection]] — Pass C scoring (Fix 3) + the Tier-1 safety net Fix 1 preserves
- [[concepts/observability]] — `judge_tournament` / `axis_report` used to verify Fixes 2 & 3
- [[concepts/arc-aware-extraction]] — Fix 5 / arc Phase 3
- [[concepts/bugs-and-fixes#BUG 60]] — the already-shipped backtick fix (a Fix-1 contributor)
- [[concepts/pipeline-optimizations-2026-06]] — the prior sweep that parallelized Stage 6 (Fix 2B mirrors it)
