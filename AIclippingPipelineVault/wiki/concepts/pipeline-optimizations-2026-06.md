---
title: "Pipeline parallelization & optimization sweep (2026-06-04)"
type: concept
tags: [performance, parallelization, multiprocessing, threadpool, ffmpeg, librosa, optimization, hub]
sources: 1
updated: 2026-06-04
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
| `multi` | 6-signal gate: keywords + audio_events + chat hard-events + diarization speakers ≥2 + word density ≥1.5/sec + segment type ∈ {reaction, hot_take, just_chatting} | 5-15% lift, very low false-negative risk |
| `sample` | `multi` + every Nth dead chunk LLM'd anyway (`CLIP_PASSB_DEAD_SAMPLE_RATE`, default 3) — bounds consecutive-skip streak | 20-25% lift, low false-negative risk |

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
**Expected lift**: 2-3× on the per-moment phase (13 min → ~5 min). Real lift depends on whether LM Studio internally serializes VLM calls — measure on a real VOD.
**Risk**: Low — selection-neutral by construction (each moment's enrichment is fully independent; no cross-moment state beyond the failure counter). The "3 consecutive failures" semantic loosens to "3 since last success" under concurrency, which is the right behaviour when LM Studio is down (skip remaining moments faster, not block on retries).
**Verification**: AST parse OK (901 lines, +82 from 819); `_process_moment` at module scope; 1 nested closure (`_vision_call`); 6 return statements; dispatch at line 870 (after function def, after `moments` + `enriched` initialization at lines 90/94).

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
