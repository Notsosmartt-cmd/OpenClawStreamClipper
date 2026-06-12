---
title: "Pipeline parallelization & optimization sweep (2026-06-04)"
type: concept
tags: [performance, parallelization, multiprocessing, threadpool, ffmpeg, librosa, optimization, hub]
sources: 1
updated: 2026-06-12
---

# Pipeline optimization sweep — 2026-06-04

Implementation pass on a prioritized list of pipeline performance wins.
Targets a 135-min real-world run breakdown (`logtool axes` on the
plaqueboymax 2026-04-19 VOD):

| Stage | Wall-clock | % of total |
|---|---|---|
| Stage 4 Pass B (moment detection) | 67 min | 49 % |
| Stage 2 transcription | 29 min | 22 % |
| Stage 6 vision enrichment | 13 min | 10 % |
| Stage 7 editing/export | 11 min | 8 % |
| Stage 5.5 vision judge | 10 min | 7 % |
| Tier-2 M2 audio events | ~25 min | (concurrent, but observably slow) |

Every stage except [[entities/audio-events]] (already parallelized 2026-06-04 round 1) was running **single-threaded sequential**.

---

## Implemented this round

### 1. Audio events RMS gate ([[entities/audio-events]])
**File**: `scripts/lib/audio_events.py`
**Mechanism**: Cheap energy check (one numpy reduction, ~50 µs) before invoking the expensive HPSS detector. Silent windows (RMS < 0.01) early-exit with a zero result, skipping the ~700 ms HPSS cost entirely.
**Knobs**: `AUDIO_EVENTS_RMS_GATE` env var; default 0.01; set to 0 to disable.
**Expected lift**: 1.5-3× on top of multiprocessing, varying with the share of silence in the VOD.
**Risk**: Low — the gate is conservative (normal speech ≈ 0.05-0.15). Tunable if false negatives appear.

### 2. Stage 5 frame extraction parallel dispatch
**File**: `scripts/pipeline/stages/stage5.py`
**Mechanism**: Refactored from nested `for moment / for offset` loops invoking ffmpeg sequentially into pre-collected task lists dispatched via `ThreadPoolExecutor`. Each ffmpeg call is short (~200 ms total seek + JPEG encode); subprocess work releases the GIL so threads parallelise fine.
**Knobs**: `STAGE5_WORKERS` env var; default 8; set to 1 to force serial.
**Math**: Previous ~480-720 ffmpeg invocations on a typical VOD → 60-90 s wall-clock. With 8 workers → ~10-15 s.
**Risk**: Low — same ffmpeg command per frame, same output paths, same content. No shared state between extractions.

### 3. Stage 7 render + audio-extract parallel dispatch
**File**: `scripts/pipeline/stages/stage7.py`
**Mechanism**: The 7b clip-audio-extraction loop and the 7d render loop both refactored to `ThreadPoolExecutor`. Each ffmpeg render is CPU-bound (blur-fill filter + libx264 + subtitle burn). The i9-13900K's 24 cores easily absorb 4 concurrent ffmpegs each using ~6 internal threads.
**Knobs**: `STAGE7_WORKERS` env var; default 4 (conservative re: ffmpeg thread oversubscription); set to 1 to force serial.
**Math**: 11 min serial → ~3 min parallel = ~3.5× lift.
**Risk**: Medium — concurrent ffmpegs share the encoder cache and could thrash. The 4-worker default is chosen so total threads ≈ core count. Watch for memory pressure on very long clips.
**Caveat**: stitch group rendering still runs serially after the parallel main loop (it's its own subprocess via `stitch_render.py`).

### 4. Pass B dead-chunk gate (rounds 2 + 4 — multi-signal with sampling + audit log)
**File**: `scripts/lib/stages/stage4_moments.py`
**Mechanism**: Configurable gate that decides whether to skip Pass B's LLM call on chunks with insufficient signal. Four modes via `CLIP_PASSB_DEAD_GATE`:

| Mode | Behaviour | Speed vs Selection |
|---|---|---|
| **`off`** (default — round 4) | No filtering, every chunk LLM'd | 0% lift, **zero false negatives** |
| `strict` | 2-signal gate (keywords + audio_events) — original round-2 behaviour | 25-30% lift, ~5-10% false-negative rate |
| `sample` | `multi` + every Nth dead chunk LLM'd anyway (`CLIP_PASSB_DEAD_SAMPLE_RATE`, default 3) — bounds consecutive-skip streak | 5-15% lift, very low false-negative risk |
| `multi` | 6-signal gate: keywords + audio_events + chat hard-events + diarization speakers ≥2 + word density ≥1.5/sec + segment type ∈ {reaction, hot_take, just_chatting} | 20-25% lift, low false-negative risk |

> [!warning] Label correction (2026-06-06) — `multi` and `sample` were swapped
> The original estimates (and the dashboard dropdown) had `multi` at 5-15%/very-low and `sample` at 20-25%/low. That is **backwards**: `sample` uses the *same* 6-signal `_alive` check as `multi` and then *additionally* force-runs the LLM on every Nth dead chunk (`stage4_moments.py` ~`:1413-1436`). So `sample` skips a strict **subset** of what `multi` skips → it runs **more** LLM calls (slower, less lift) and is **strictly safer** (every chunk it skips, `multi` also skips, plus more). Corrected ordering by both speed and FN risk: **off < sample < multi < strict**. Dashboard labels fixed to match (`dashboard/templates/index.html`). Percentages remain rough estimates — only the *ordering* is rigorous.

**Round 4 changes from round 2** (2026-06-04 evening):
- **Default flipped to `off`** after the rakai Delaware case study showed the strict 2-signal gate has a meaningful false-negative rate. A missed clip displaces a worse one in its time-bucket slot (Pass C is competitive), so false negatives cost ~5-10× a false positive (which downstream stages filter for free).
- **6-signal gate** added (multi/sample modes). Five of six signals were already loaded at chunk-loop scope (only `chat_features.window()` call is new); no new module-level state.
- **Sampling pass-through** (sample mode): guarantees no more than N-1 consecutive dead skips.
- **Audit log** written to `{TEMP_DIR}/pass_b_skipped_chunks.json` even on zero skips (positive signal). View with `logtool dead`.
- Legacy `CLIP_PASSB_KEEP_DEAD_CHUNKS=1` still honoured as alias for `off` (backwards compat).

**Knobs**:
- `CLIP_PASSB_DEAD_GATE` = `off` (default) | `strict` | `multi` | `sample`
- `CLIP_PASSB_DEAD_SAMPLE_RATE` = N (default 3) — sample mode only
- `CLIP_PASSB_KEEP_DEAD_CHUNKS=1` (legacy) — alias for `off`

**Observability**: `python scripts/logtool.py dead` shows the skipped-chunk table with per-row signal breakdown (`kw aud cht spk wd seg`), tip on tuning, and `--json` for raw output.

**Reference**: see `concepts/case-rap-battle-missed.md` for the worked example of why the strict 2-signal gate isn't safe by default.

---

## Implemented this round (continued — round 3, 2026-06-04)

### 5. Stage 6 vision parallel HTTP (round 3)
**File**: `scripts/lib/stages/stage6_vision.py`
**Mechanism**: Refactored the 600-line `for moment in moments:` loop body into a `_process_moment(moment) -> entry` function defined at module scope. Dispatch via `ThreadPoolExecutor(max_workers=STAGE6_WORKERS)` with `pool.map(_process_moment, moments)` so input order is preserved. Each moment's VLM call (~30-90 s) runs concurrently; the nested `_vision_call` closure now updates `_VISION_NET_FAIL_STREAK` under a `threading.Lock` to avoid read-modify-write races on the circuit-breaker counter.
**Knobs**: `STAGE6_WORKERS` env var; default 2 (conservative for VLM); set to 1 to force serial.
**Expected lift**: 2-3× on the per-moment phase (13 min → ~5 min). Real lift depends on whether [[entities/lm-studio|LM Studio]] internally serializes VLM calls — measure on a real VOD.
**Risk**: Low — selection-neutral by construction (each moment's enrichment is fully independent; no cross-moment state beyond the failure counter). The "3 consecutive failures" semantic loosens to "3 since last success" under concurrency, which is the right behaviour when LM Studio is down (skip remaining moments faster, not block on retries).
**Verification**: AST parse OK (901 lines, +82 from 819); `_process_moment` at module scope; 1 nested closure (`_vision_call`); 6 return statements; dispatch at line 870 (after function def, after `moments` + `enriched` initialization at lines 90/94).

---

## Pass C selection observability (2026-06-05)

A separate concern surfaced during the rakai verification run (see [[concepts/case-rap-battle-missed]] §2026-06-05): the structural FN fixes detected the Delaware rap battle perfectly (Pass A keyword hits → Pass B `rap_battle_freestyle` 0.878 with cross-val to 1.000), but **Pass C selection dropped it**. T=1828 with Pass B 0.433 won the bucket; T=654 with Pass B 0.878 didn't. Without per-candidate scoring traces, the cause was undiagnosable from the existing diagnostics (only the 10 winners get full `moment_<T>` records).

### Phase 1 — Pass C candidate trace (shipped 2026-06-05)

**File**: `scripts/lib/stages/stage4_moments.py` writes `{TEMP_DIR}/pass_c_candidates.json` after Pass C selection finalizes. One record per deduped moment with the FULL scoring chain: timestamp, source, primary_pattern/category, segment_type, Pass B score, normalized_score after ceiling, cross_validated flag, length_penalty, position_weight, all four axis multipliers (arc/reaction/baseline/engagement), combined axis_multiplier (post-clamp), final_score, base_rank, pass_c_rank, bucket_idx, bucket_rank, selected flag, and a 140-char `why` preview.

**Viewer**: new `logtool selection [-n N] [--pattern <id>] [--bucket N] [--json]` subcommand renders the trace per bucket with row colouring (selected rows green) and per-row signal breakdown. Looks like:

```
## Bucket 1/6 (00:00-32:17) — 5 candidates, 1 selected
  sel        T   rank  src   pattern                       cat        seg            cv     pB   norm   pos   len   axis   final
  ---  -------  -----  ----  ----------------------------  ---------  -------------  --  -----  -----  ----  ----  -----  ------
    ✓    30:28      1  llm   setup_external_contradiction  funny      irl             Y  0.433  0.433  0.95  1.00   1.55   0.952
         10:54      3  llm   rap_battle_freestyle          hype       just_chatting   Y  0.878  1.000  0.88  1.00   1.05   0.815
```

The rakai diagnosis becomes a single glance: T=1828 selected with Pass B 0.433 + axis 1.55 → 0.952; T=654 rejected with Pass B 0.878 + cross-val to 1.000 but axis only 1.05 → 0.815. The axis-multiplier gap (1.55 vs 1.05) is the smoking gun.

**Knobs**: none — this is pure observability. Write is unconditional (failure-soft via try/except).

**Verification**: AST OK; smoke-tested cmd_selection with synthetic data modelling the rakai bucket 0 situation; pattern and bucket filters confirmed working; selected-row colour rendering confirmed.

### Phase 2 — Rare-pattern bonus (deferred to next session)

The Phase 1 trace confirms the hypothesis: rare patterns like `rap_battle_freestyle` lose to common patterns whose axis multipliers compound. The next fix is a per-pattern multiplier applied to `final_score` when a Pass B moment with a rare pattern is also cross-validated. Defer until at least one more run with `logtool selection` confirms the cross-pattern axis-multiplier gap is reproducible, not specific to the Delaware case.

```python
RARE_PATTERN_BONUS = {
    "rap_battle_freestyle": 1.15,
    "interview_revelation": 1.10,
    "social_callout": 1.05,
}
```

---

## Deferred (high impact, higher risk — needs careful follow-up)

These are tracked here so the next implementation session can pick them up
with the full context.

### A. Pass B parallel HTTP (biggest single-stage win — 67 → ~17 min)
**File**: `scripts/lib/stages/stage4_moments.py` (2397 lines)
**Why deferred**: The chunk loop carries complex order-dependent state — `chunk_summaries` (used by Tier-1 Q1 prior-context block + Tier-3 A1 skeleton at line 1472), `CONVO_SHAPE_INDEX`, `llm_net_outage()` failure-streak tracking, and the regenerate-once grounding cascade. Parallelization requires:
1. Splitting the chunk-processing body into a `_process_one_chunk(...)` function that takes prior-context as an arg
2. Two-phase execution: first pass builds chunk_summaries in order (without prior context dep), then parallel LLM passes happen
3. Thread-safe net-outage tracking (`_LLM_NET_FAIL_STREAK` counter needs locking or atomic-int wrapping)
4. Order-preserving result collection so `llm_moments` and `chunk_summaries` stay deterministic
**Pattern**: `ThreadPoolExecutor(max_workers=3)` over chunks. LM Studio handles concurrent HTTP — 3 concurrent should give ~2-3× without saturating the LLM queue.
**Test plan**: A/B on a real VOD with `logtool axes`; compare `axis_report` rank churn — should be near-identical if order is preserved.

### ~~B. Stage 6 vision parallel HTTP~~ — **IMPLEMENTED** (round 3, see §5 above)

### C. Stage 5.5 Vision Judge concurrent pairs (10 → ~4 min)
**File**: `scripts/lib/vlm_judge.py` — modify `swiss_tournament` to accept an `executor` arg, run pairs within a single Swiss round concurrently.
**Why deferred**: Touches shared judge logic with its own test surface (`stage5_5_judge.py --selftest`). Round-by-round structure must be preserved (within-round concurrency only). Need to verify `compare_pair` is thread-safe (HTTP only — should be, but verify).

### D. Tier-2 modules parallel with Stage 3-4
**Pattern**: Run `audio_events`, `diarization`, `callback_module` in background threads/processes during Stage 3 + early Stage 4, await before Pass A consumes them.
**Why deferred**: Requires `scripts/run_pipeline.py` orchestrator refactor — currently sequential stage dispatch. Moderate-effort restructure with clear test path (the inputs and outputs are well-defined).

### E. LM Studio speculative decoding (zero code change)
**Mechanism**: Load `qwen3.5-9b` as draft model alongside `qwen3.6-35b-a3b` main; LM Studio handles the rest. **~2× per-token throughput** on most workloads.
**Action**: Configure via LM Studio's Speculative Decoding panel. Not a code change — user-side config.

### F. LLM JSON response caching
**Pattern**: Content-addressed cache: `hash(model + prompt + temp + max_tokens)` → response. Saves re-LLMing same chunks on `--force` re-runs or development prompt iterations.
**Why deferred**: Cache invalidation on prompt-template changes is the trickiest part. Worth doing for dev velocity; minimal benefit on production single-run pipelines.

---

## Expected combined wall-clock impact (post-implementation, pre-deferred)

| Stage | Before | After (this round) | Lever |
|---|---|---|---|
| Audio events | ~25 min | ~3-4 min × 1.5-3× silence gate = **~2 min** | round 1 mp + this round RMS gate |
| Stage 5 frame extraction | ~1 min | **~10-15 s** | parallel dispatch |
| Stage 7 render + audio extract | 11 min | **~3-4 min** | parallel ffmpegs |
| Stage 6 vision enrichment | 13 min | **~5 min** | parallel VLM HTTP (round 3) |
| Pass B (Stage 4) | 67 min | **~50-55 min** | dead-chunk pre-filter only (parallel HTTP deferred) |
| Other stages unchanged | ~14 min | ~14 min | — |
| **TOTAL** | **135 min** | **~75-80 min (~1.7× speedup)** | combined |

Pass B parallel HTTP (deferred item A) is the path to a further ~30-min reduction → ~45-50 min total run.

---

## Verification

| File | AST parse | Module import | Helpers present |
|---|---|---|---|
| `scripts/lib/audio_events.py` | ✓ | ✓ | `_rms_below_gate`, `_resolve_rms_gate`, `_RMS_GATE_DEFAULT=0.01` |
| `scripts/pipeline/stages/stage5.py` | ✓ | (mocked import OK) | `_resolve_workers`, `_collect_payoff_tasks`, `_collect_setup_tasks`, `_dispatch` |
| `scripts/pipeline/stages/stage7.py` | ✓ | (mocked import OK) | `_resolve_render_workers`, `_DEFAULT_RENDER_WORKERS=4` |
| `scripts/lib/stages/stage4_moments.py` | ✓ | n/a (script-level) | dead-chunk gate added inline |

Real-world wall-clock verification requires a full VOD run.

---

## Observations from the 2026-06-05 17:06 rakai run (post-shipment review)

Production review of the rakai re-run that ran with everything from rounds 1-4 + BLAS-pin + audio fast-load (commits up to `2e4aca8`). Five findings worth tracking for the next session — none blocking, but each represents a discrete quality or tuning opportunity.

### 1. Title pollution from Pass B reasoning text → [[concepts/bugs-and-fixes]] §BUG 60

Two of ten final clips had their `title` or `description` filled with the LLM's raw `Pattern <id>: ...` Pass B output. Visible as filenames like `Pattern_socialcallout_Friend_roasts_streamer_for_l.mp4`. The grounding cascade misses this because the pattern-reasoning text technically overlaps the transcript. Fix: regex strip of `^Pattern[\s_]+\w+\s*[:\-—]\s*` in `stage6_vision.py` post-process step + prompt-side prohibition. Selection-neutral quality fix, ~10 lines. See [[concepts/bugs-and-fixes]] §BUG 60 for full details.

### 2. Global axis-multiplier clamp is binding 100% of candidates

`axis_report` from this run: `global_clamp: {floor: 0.8, ceil: 1.35, bound_count: 253}` over `candidates: 253`. Every single Pass C candidate had its accumulated axis-multiplier product clamped to the [0.8, 1.35] window. The four axes' individual ceilings each top out at ~1.10-1.18; their product theoretically reaches ~1.9, so clamping is doing its safety job. But 100% bind rate suggests the per-axis ceilings are too aggressive for the product to remain naturally bounded.

Implication for the Delaware case (see [[concepts/case-rap-battle-missed]] §2026-06-05): rare patterns that DON'T trigger the axes get crushed by candidates that hit the ceiling. Either lower per-axis ceilings so the natural product fits the clamp, raise the global ceiling, or add pattern-aware compensation (Phase 2 rare-pattern bonus). The `logtool selection` tool shipped 2026-06-05 will show the per-axis breakdown for the next run so this can be tuned empirically.

### 3. Chat features disabled — selection signal loss

[[entities/chat-features|`dependencies.chat_features`]]`: False` because `vods/.chat/<vod>.jsonl` doesn't exist and chat auto-fetch is disabled. Effects:
- Pass A loses chat hard-events (sub/raid/donation) for boost
- Pass B prompts run without chat context
- Stage 6 can't verify sub/raid/donation claims against actual chat (grounding hard-event check no-ops)
- Pass C `engagement` axis is degraded (relies on sustained chat discussion as a signal)

Not a bug — auto-fetch is explicit user config. But enabling [[entities/chat-fetch]] auto-fetch (anonymous Twitch GraphQL + TwitchDownloader importer) would improve every VOD where chat is fetchable. **Next session idea**: surface an auto-fetch checkbox in the dashboard clip controls so the operator can toggle per-clip without editing config.

### 4. Stage 7 render parallelization under-scaled (analog of the audio_events BLAS issue)

Observed: 412.9 s for 10 clips with `STAGE7_WORKERS=4`. Implied per-clip wall-clock: ~41 s. Serial baseline was ~67 s/clip × 10 = ~670 s. So **1.6× speedup** instead of the predicted 3-4×.

Different mechanism from audio_events — Stage 7 isn't BLAS-bound; it's ffmpeg subprocesses. Likely contributors:
- Each ffmpeg uses ~6 internal threads → 4 workers × 6 = 24 threads on 24 cores → kernel scheduling overhead
- ffmpeg has serial-ish phases (init, seek, finalize) that don't parallelize
- The `originality.py` subprocess per clip may add a sequential portion not parallelized by `STAGE7_WORKERS`

**Tuning candidates for the next session** (no code change, just env-var experiments):
- `STAGE7_WORKERS=6` (oversubscribed but each ffmpeg gets less thread share — measure)
- `STAGE7_WORKERS=3` and bound each ffmpeg explicitly with `-threads 8` (sums to 24 = full CPU)
- Profile a single clip render with `time` to see how much is ffmpeg vs. ancillary subprocesses

### 5. Pass B chunk timing is steady at ~37s/chunk — Pass B parallel HTTP would help less now

Per-chunk timings across all 25 chunks:

```
Chunks 1-10:   36.7 / 38.7 / 39.6 / 36.8 / 37.3 / 35.4 / 38.1 / 36.0 / 36.3 / 36.3
Chunks 11-20:  35.5 / 37.5 / 36.5 / 36.7 / 36.7 / 34.7 / 37.3 / 41.1 / 37.7 / 38.6
```

Standard deviation ~1.5 s, mean ~37 s. **The qwen3.6-35b-a3b MoE w/ thinking-off on Vulkan pool is delivering consistent throughput** with no slow-chunk outliers (which used to happen on hybrid-thinking builds due to BUG 20 token exhaustion).

Math: ~37 s × 25 chunks = 925 s of LLM time + ~165 s of axis/shape/summary work = ~1090 s total (matches observed Stage 4 timing).

**Implication for the deferred Pass B parallel HTTP item**: with each chunk at 37 s instead of the old ~90+ s, the relative impact of parallelization is smaller. 4-way parallel HTTP would cut Pass B from ~18 min → ~6 min (12 min savings), still worth pursuing but no longer the dominant share of the run.

### 6. Grounding regen fired on 4 of 10 final clips (40%)

Tier-3 grounding cascade caught ungrounded fields on T=5798 (description), T=2895 (description), T=8326 (hook), T=8023 (description) — retried each with a stricter prompt. Working as designed.

If this 40% rate is consistent across runs, the first-pass Stage 6 prompt is letting through more ungrounded text than it should. Tightening the first-pass prompt with the explicit `Pattern <id>:` prohibition from BUG 60 might also reduce regens — many regens are likely fixing the same pattern-text leak.

### Recommended priority order for the next session

1. **Strip `Pattern <id>:` prefixes** (BUG 60) — 10 lines, selection-neutral, quality positive
2. **Verify BLAS-pin + audio fast-load actually delivered** on the next run (look for `method=soundfile+polyphase` and `parallel x8 6-8 win/s` in the log)
3. **Verify `logtool selection` populates** on the next run
4. **Phase 2 rare-pattern bonus** — only after Phase 1 trace confirms axis-gap reproduces
5. **Stage 7 worker-count A/B** — env-var-only experiment
6. **Chat auto-fetch toggle** in dashboard — moderate effort, ongoing benefit

---

## Dashboard UI (added round 4, 2026-06-04)

The Pass B dead-chunk gate mode is exposed as a dropdown in the [[entities/dashboard|dashboard]]'s clip control panel — `Pass B gate` next to the Speed dropdown. Options match the env var values: `Off` (default — no skips, safest), `Multi` (6-signal), `Sample` (multi + 1-in-3 pass-through), `Strict` (legacy 2-signal). The selected mode is forwarded to the pipeline as `CLIP_PASSB_DEAD_GATE` in both Docker (`docker exec -e`) and bare-metal (`Popen env=`) paths. The other knobs in the env-var table below remain env-only — they're tuner knobs more than user-facing modes.

## Prior performance work (pre-round-2 context)

Round 2 was *not* the first performance pass on this pipeline. Earlier rounds existed but weren't catalogued as numbered rounds:

| Date | Commit | Lever | Impact |
|---|---|---|---|
| 2026-04-20 | `e285a72` "speed and caption update" | speed-control dropdown (1×–1.5× setpts + rubberband), bash pipeline trims | ~10-15 % render-side |
| 2026-04-28 | (in-file, pre-modularization) audio_events per-window-load → load-once-and-slice | `scripts/lib/audio_events.py` | Hung "scanning audio events..." for minutes → minutes total (still serial; round 1 made it parallel) |
| 2026-06-03 | `51dfedb` "Whisper large-v3-turbo as default" | `config/models.json::whisper_model` ([[entities/faster-whisper]]) | ~2.5× transcription speedup for <1 % WER loss |

So the round numbering started with round 1 = audio_events multiprocessing because that was the first sweep where the team explicitly thought of performance as a tracked workstream. The prior wins were one-shot improvements driven by specific symptoms (slow renders, scan hangs, slow transcription) rather than a sweep.

## Env-var summary (operator quick-reference)

| Variable | Default | Effect |
|---|---|---|
| `AUDIO_EVENTS_WORKERS` | auto (`min(8, cpu-2)`) | Audio events parallel worker count |
| `AUDIO_EVENTS_RMS_GATE` | `0.01` | Silent-window RMS threshold; set to `0` to disable |
| `STAGE5_WORKERS` | `8` | Stage 5 ffmpeg frame-extract concurrency |
| `STAGE7_WORKERS` | `4` | Stage 7 ffmpeg render + audio-extract concurrency |
| `STAGE6_WORKERS` | `2` | Stage 6 VLM HTTP concurrency (conservative for vision-encoder serialization) |
| `CLIP_PASSB_DEAD_GATE` | `off` | Pass B dead-chunk gate mode (`off` = no filter, `strict` = 2-signal, `multi` = 6-signal, `sample` = multi + 1-in-N sampling) |
| `CLIP_PASSB_DEAD_SAMPLE_RATE` | `3` | Sample mode only — every Nth dead chunk LLM'd anyway |
| `CLIP_PASSB_KEEP_DEAD_CHUNKS` | unset | (legacy) `=1` aliases `CLIP_PASSB_DEAD_GATE=off` |

---

## Related

- [[entities/audio-events]] — multiprocessing details + RMS gate
- [[concepts/clipping-pipeline]] — full pipeline ordering
- [[concepts/observability]] — `logtool axes` for measuring lift
- [[concepts/case-rap-battle-missed]] — example of a stage where pre-filter doesn't help (rap battle had Pass A zero, BUT also audio_events skipped on cached transcript)
- [[entities/lm-studio]] — the LLM server whose concurrency ceiling bounds the Stage 4/6 parallel-HTTP wins
- [[entities/faster-whisper]] — large-v3-turbo default (2026-06-03 transcription speedup)
- [[concepts/clip-rendering]] — the Stage 7 render loop parallelized in item 3
- [[concepts/bugs-and-fixes]] — BUG 60 (Pass-B reasoning-text title pollution) surfaced in the post-shipment review
