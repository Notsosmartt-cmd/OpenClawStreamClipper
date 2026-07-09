---
title: "Speed Wave 2 — Unified Serving-Stack + Pipeline Plan (2026-07)"
type: concept
status: planned
tags: [performance, speed, lm-studio, serving, speculative-decoding, prefill, kv-cache, batch-overlap, plan, stage-4, stage-2, stage-3]
sources: 0
updated: 2026-07-09
---

# Speed Wave 2 — Unified Implementation Plan

Unifies the serving-stack proposals (speculative decoding, prefill tuning) with the
second-sweep candidates (C1–C6) into ONE execution-grade plan. Written for agent execution:
every phase names the exact files, flags, commands, and gates. Supersedes the earlier
serving-stack-only draft of this page (git history has it).

**Scope rules (owner-set, standing):**
- No quality-degrading levers. Owner-excluded: low-quant CUDA-only fit, KV-cache quantization,
  dropping rationale/why output fields, judge-image diet, faster NVENC presets, model swaps.
- **Owner rubric: default-off = RED.** A lever only counts GREEN when it is the default for
  every production run. Build flag-gated/failure-soft, validate, then promote or archive.
- All runs bounded (watchdog `--timeout ≥5400 --stall ≥900`); LM Studio app stays up
  (loads/unloads via `lms` CLI / pipeline only); never fabricate labels.
- Every code change: wiki update + commit (house rules).

**Why this exists**: Stage 4 = ~48 sequential LLM calls at ~16% GPU util (S4 median 1156 s;
run median 0.26× realtime). Pipeline-code levers are exhausted
([[concepts/pipeline-speed-findings-2026-07]] §7) but the serving layer + a handful of
orchestration levers were never touched.

---

## 1. Grounding facts (verified live 2026-07-09 — do not re-derive)

**Serving controllability** (details in [[concepts/pipeline-speed-findings-2026-07]] §9):
- Speculative decoding = pure `lms load` flags (`--speculative-draft-simple
  --speculative-draft-model <m>`, `--speculative-draft-max-tokens/-min-tokens/
  -min-continue-probability`). MTP is **dead** on the current GGUF (no bundled head;
  live-tested, fails fast at 0%).
- Draft vocab: qwen3.5/3.6 family shares speculation vocab **248320**; qwen3-8b/gemma/
  nemotron/gpt-oss incompatible. Qwen3.5 ships 0.8B/2B/4B smalls (none on disk).
- Per-model GUI toggles persist to
  `C:\Users\user\.cache\lm-studio\.internal\user-concrete-model-default-config\qwen\qwen3.6-35b-a3b.json`
  (flashAttention **already true**, contextLength 16384, enableThinking false).
- GPU split NOT tunable on Vulkan dual-GPU (UI "split evenly" only) — no-go.
- Server config: `justInTimeModelLoading: true` (`.internal\http-server-config.json`);
  `lms ps --json` exists for scripted state checks.
- Hardware path already optimal: G:=NVMe 990 EVO Plus, ReBAR ON (BAR1 16 GiB), 5060 Ti x8 =
  card-native width. (One-off TODO: `nvidia-smi -q` during Stage-4 load to confirm PCIe ramps
  Gen1→Gen5 under load.)

**Pipeline load/unload architecture** (drives Phases 1/2/5 — read these lines first):
- `scripts/pipeline/stages/stage2.py:34-37` — Stage 2 **unconditionally unloads ALL LM Studio
  models** at entry ("Free VRAM before Whisper"), even when the transcript+events caches will
  be hit and whisper never runs.
- `scripts/pipeline/stages/stage3.py:14` — Stage 3 reloads via
  `common.load_model(log, ctx.llm_url, ctx.text_model, ctx.context_length)`. **Stage 3 is
  LLM-based** (segment voting, `CLIP_SEGMENT_VOTES`) — it cannot overlap other LLM work.
- `scripts/pipeline/common.py:267` — `load_model()` already shells `lms load <model> -c <ctx>
  -y --ttl <CLIP_MODEL_TTL default 3600>`, skips when already loaded (`_lms_loaded_ids()`),
  heartbeats, failure-soft. **This function is the single integration point for draft flags.**
- Net: the 35B is evicted + reloaded **once per VOD, every VOD, including cached re-runs**.
- Stage-4 moment prompt (`scripts/lib/stages/stage4_moments.py:1904`): `seg_type` interpolated
  into sentence 1, prior-context above the ~1–2k-token static PATTERN CATALOG → near-zero
  cross-call prefix share today. Server logs carry no reuse stats — must measure via TTFT.

---

## 2. Phase 0 — Instrumentation (build first; Phases 1/3 consume it)

### P0.1 `scripts/research/bench_serving.py` (new)
Serial-only benchmark against LM Studio. **Prefer `POST http://localhost:1234/api/v0/chat/completions`**
(native REST returns a `stats` block: `tokens_per_second`, `time_to_first_token`,
`generation_time`, and draft/speculative counters when active); fall back to `/v1/...` with
client-side timing (`stream=True`, TTFT = first-chunk wall delta). Temp 0, `max_tokens` 512,
per-call timeout 300 s, total run bounded < 30 min, no retry loops.

Modes (`--mode`):
- **`decode`** — replay real Pass-B prompts (from P0.2 dump) serially, `--reps 2`; report
  per-call wall, TTFT, decode tok/s, and **prefill share** (TTFT ÷ wall — this number decides
  how much S1 vs P/C2 matter; capture it in the wiki).
- **`ttft`** — the C2(a) prefix-reuse protocol, synthetic prompts (~2k-token static block S +
  ~2k variable tails, counts approximated at 4 chars/token):
  1. `S+V1` (cold), 2. `S+V2` (reuse probe), 3. `X+V3` where X = S with token 1 changed
  (control). Reuse fires iff TTFT₂ ≪ TTFT₃ ≈ TTFT₁.
  Then the **alternation probe**: `S+V1`, `CARD+W1` (different-family prompt), `S+V2` — if
  TTFT₃ reverts to cold, single-slot reuse dies under moment/card alternation (expected).
  Optional: repeat after `lms load ... --parallel 2` — llama.cpp routes requests to the slot
  with the longest common prefix, which may survive alternation. **Check the loaded context
  per slot first** (`--parallel` may split n_ctx; verify a 12k-token prompt still completes)
  and confirm requests stay serial (no co-batching → §3 landmine not triggered).
- **`prefill`** — one ~8–12k-token prompt, `max_tokens=1`; TTFT ≈ pure prefill time. Used for
  the evalBatchSize A/B (Phase 3).

### P0.2 Prompt dump hook (tiny, default-off)
`stage4_moments.py` already sha1-hashes every Pass-B prompt (`_PASSB_PROMPT_HASHES` capture
site). Behind `CLIP_PASSB_DUMP_PROMPTS=1`, also append
`{"idx": chunk_idx, "kind": "moment"|"card", "sha1": ..., "prompt": ...}` to
`<work>/passb_prompts.jsonl` (failure-soft try/except). Exact replay of a full run's prompts
is impossible to reconstruct offline (chunk N's prompt embeds LLM outputs of N−1/N−2), so any
one instrumented run yields the replay set for `decode` mode.

### P0.3 C6 — JSON-retry audit (measure, probably close)
`scripts/research/retry_audit.py` (or a one-off): scan recent run logs/diagnostics for Stage
4/6 parse-failure + retry markers (grep the `call_llm` retry log lines; count per run).
- Rate ≈ 0 → **close C6 permanently** (grammar-constrained decoding stays excluded; it alters
  the token distribution and is only justified by material retry waste).
- Rate > ~2% of calls → file a follow-up candidate with the measured number; still
  quality-gated (constrained decoding changes outputs).

**Phase-0 DoD**: bench runs clean against the loaded 35B; prefill-share number recorded in
this page; TTFT reuse verdict recorded; retry rate recorded.

---

## 3. Phase 1 — S1 speculative decoding (headline lever; target weights untouched)

Draft+verify preserves the target distribution by construction (greedy: exact argmax match;
sampling: rejection scheme) — categorically different from the §3 co-batching landmine.
JSON-heavy Pass-B output → high expected acceptance. Published typical: 1.5–2.5× decode.

| Step | Action | Gate |
|---|---|---|
| I-S1.0 | `lms get qwen/qwen3.5-2b -y` (~1.5 GB) | on disk; vocab check: parse `.internal\gguf-metadata-cache.json` → `draftSpeculationVocab.tokenCount == 248320` |
| I-S1.1 | `lms load qwen/qwen3.6-35b-a3b -c 16384 --speculative-draft-simple --speculative-draft-model qwen/qwen3.5-2b -y` | loads; `lms ps --json` inspected — **record whether draft config is visible in ps output** (needed for I-S1.5 idempotence); `nvidia-smi --query-gpu=memory.used --format=csv` shows no spill |
| I-S1.2 | `bench_serving.py --mode decode` baseline (no draft) vs draft, same prompts | decode tok/s ratio + acceptance rate + prefill share recorded |
| I-S1.3 | If ratio < 1.5×: sweep `--speculative-draft-max-tokens {4,8}` × `--speculative-draft-min-continue-probability {default, 0.75}`; if drafting overhead dominates, retry with `qwen3.5-0.8b` | best config picked on measured tok/s |
| I-S1.4 | Full 2xRaKai run with draft loaded (bounded, phase_runner) vs latest no-draft run | wall ≥15% faster; clip-set overlap **≥5/10 ±20 s** (variance yardstick, findings §3-reframe); owner spot-check |
| I-S1.5 | **Integration** — in `common.load_model()` (common.py:267): when `CLIP_LLM_DRAFT=1` (+ `CLIP_LLM_DRAFT_MODEL`, default `qwen/qwen3.5-2b`), append the draft flags to the existing `lms load` cmd list. Idempotence: the "already loaded — skip" branch must detect a no-draft-loaded state when draft is requested (use the I-S1.1 `lms ps --json` finding; if undetectable, unload+reload when the env flag is set and a marker file says the last load was draftless). Failure-soft: if the draft model is missing, log + load plain. | flag-gated run passes; then per rubric: flip `CLIP_LLM_DRAFT` default to on (GREEN) or archive with numbers (RED) |

**Why load_model() is the right (and only) place**: Stage 2 evicts everything each VOD, and
Stage 3 reloads via this function — a pre-run CLI load would be evicted before Stage 4 ever
runs. Any JIT load (server-side) would also lack draft flags; after I-S1.5, verify in a batch
run that no JIT load path bypasses `load_model()` (grep the run log for the pre-load line
once per VOD).

### Lane S2 — MTP repack (fallback ONLY if S1 < 1.2× after I-S1.3)
`unsloth/Qwen3.6-35B-A3B-MTP-GGUF` **UD-Q4_K_M (22.7 GB)** via `lms get <full HF URL>`, then
`--speculative-draft-mtp`.
> [!warning] S2 is a weights change (UD quant ≠ current Q4_K_M) — full model-change gate:
> A/B variance yardstick + full-VOD owner review + explicit owner signoff before any default.

---

## 4. Phase 2 — C3 residency & reload hygiene (zero-risk, two concrete fixes)

1. **Skip the pointless eviction on cached re-runs.** `stage2.py:34-37` unloads all models
   before checking the transcript cache. Move the unload INSIDE the will-run-whisper branch:
   compute `_cache_ok` + `_reuse_transcript` (stage2.py:56-59) first; skip the unload when the
   cached path will be taken AND the audio-events cache is valid (whisper + scan both skipped
   → GPU never needed). Saves an unload + a ~30–60 s reload per VOD on every re-run batch.
   Byte-neutral (no compute changes — pure lifecycle). Flag: none needed (behavior-preserving
   when whisper runs); still verify with one cached re-run (log shows no unload, Stage 3
   "already loaded — skipping").
2. **TTL**: `load_model()` already honors `CLIP_MODEL_TTL` (default 3600 s). For batch runs
   3600 s is fine (never idle that long mid-run). Document: `CLIP_MODEL_TTL=0` = persistent.
   No code change; verify call-1-vs-call-2 timing in bench output shows no mid-run reload.

---

## 5. Phase 3 — P prefill batch + C2 KV/prefix-cache

### P — evalBatchSize (needs one 30-second owner action)
1. Owner sets "Evaluation batch size" in LM Studio GUI advanced load settings for the 35B →
   diff `...\user-concrete-model-default-config\qwen\qwen3.6-35b-a3b.json` → **record the
   exact key** (expected `llm.load.llama.evalBatchSize`).
2. Script the A/B: edit the JSON (value default → 1024 → 2048), `lms unload --all` +
   `lms load` between points, `bench_serving.py --mode prefill` each. Watch VRAM (larger
   batch = larger compute buffers; abort a config on spill).
3. Keep the best value in the per-model JSON (this IS the integration — every subsequent
   load, CLI or JIT, reads it). Byte-caveat: batch size can reorder FP reductions → same
   noise class as everything on this stack; fixed config is deterministic run-to-run.

### C2(a) — measure prefix reuse (free, byte-neutral, no code)
Run `bench_serving.py --mode ttft` (protocol in P0.1). Outcomes:
- Reuse ≈ 0 even on back-to-back shared-prefix calls → LM Studio isn't reusing across
  requests → C2(b) is dead serving-side; record and close.
- Reuse fires but alternation kills it → C2(b) has upside; also record the `--parallel 2`
  slot-routing result.

### C2(b) — static-first prompt reorder (ONLY if (a) shows ≥20% prefill savings available)
> [!warning] Output-changing. Reordering prompt sections changes the token sequence →
> different outputs (same info). Gate = variance yardstick + owner spot-check, exactly like
> card-parallel. NOT byte-neutral; "within existing noise" defensible.
- Target: `stage4_moments.py:1904` (catalog prompt) + the legacy fallback at :1946. New order:
  `/no_think` + generic role line (seg_type REMOVED from sentence 1) + PATTERN CATALOG +
  how-to-use + skip rules + JSON output spec (all static) → THEN `This is a {seg_type}
  segment` + `{seg_instructions}` + STYLE + prior_context + convo_shape + transcript.
- Flag: `CLIP_PROMPT_STATIC_FIRST=1`, default off; both prompt variants kept side by side.
- Validate: (1) bench TTFT re-measured with the reordered shape — confirm the projected
  saving is REALIZED before spending a full run; (2) full-VOD A/B overlap ≥5/10; (3) owner
  spot-check. Card prompts and the judge prompt can follow the same treatment as a second
  step if the first pays.

---

## 6. Phase 4 — C4 Stage-3 segment cache (re-run lever, ~165 s/VOD)

Mirror the Stage-2 audio-events cache pattern (`stage2.py`, shipped GREEN):
- Cache file: `p.transcriptions_dir / f"{stem}.segments.{key12}.json"` where `key12` = first
  12 hex of sha1(transcript.json bytes + canonical-JSON of every stage-3 config input).
- **Enumerating the key inputs is the critical step**: grep `stage3.py` + any
  `scripts/lib/stages/stage3*.py` for `os.environ` reads and ctx fields consumed (known:
  `CLIP_SEGMENT_VOTES`, `ctx.text_model`, context length; assume more — enumerate, don't
  guess). Any input not in the key = silent staleness bug.
- Bypass on `ctx.force`. Failure-soft (corrupt cache → regenerate). Cache only written after
  a successful stage.
> [!note] Honest classification — this caches a STOCHASTIC output
> Stage 3 is LLM-voted, so a fresh run re-rolls segments while a cache hit replays the prior
> draw. That's byte-identical to the cached run and usually *desirable* (A/B runs hold
> segments constant), but it changes re-run semantics from "new draw" to "frozen draw" →
> ship flag-gated `CLIP_SEGMENT_CACHE=1` default-off, promote after one clean re-run batch +
> owner OK on the semantics.
- Validate: run VOD twice with flag on — 2nd run stage-3 time < 5 s, segments byte-identical
  to 1st; then corrupt the cache file → regenerates cleanly.

---

## 7. Phase 5 — C1 cross-VOD overlap in `--all` batches (REVISED design)

**Design revision from grounding** (the original "overlap stages 1–3" idea is wrong): Stage 3
is LLM-based → can't overlap LLM stages; whisper requires the eviction Stage 2 already does →
can't run while the 35B serves Stage 4–6. The correct window:

> After VOD i's **last LLM call** (end of Stage 6), the 35B is idle-resident through Stage 7/8
> (render, NVENC + CPU filters, ~338 s median). **Early-evict it there and prefetch VOD i+1's
> ENTIRE Stage 2** (ffmpeg extract + whisper + scan, ~6–7 min fresh) into the render window.
> This is exactly today's eviction semantics, re-timed — whisper still runs with the GPU free.

Implementation sketch (`scripts/run_pipeline.py`, batch loop under `main()` at :227):
- `ThreadPoolExecutor(max_workers=1)` prefetcher. After VOD i's Stage 6 returns:
  `common.unload_model(...)` (the early evict — same call Stage 2 makes), then
  `future = pool.submit(run_stage2_only, ctx_for(vods[i+1]))` and continue into Stage 7/8.
- At VOD i+1's turn: `future.result()` (bounded `timeout=1800`); its Stage 2 then sees warm
  caches and no-ops (the unload-skip from Phase 2 fix 1 makes this free); Stage 3 reloads the
  35B as it already does.
- **Isolation requirements** (verify each, don't assume): Stage 2 writes only per-VOD paths
  (`transcriptions/{stem}.*`, `p.work(...)` per-VOD work dir — confirm work dirs are
  per-VOD, not shared); the prefetch ctx must NOT write the shared stage marker/log
  (`common.set_stage`) — pass `prefetch=True` to route its log lines to a per-VOD prep log
  and suppress `set_stage`.
- **Contention accounting**: scan uses ≤4 BLAS-pinned threads, render uses ≤4 ffmpeg procs —
  on a 24-core i9-13900K that coexists; whisper (CUDA) overlaps only NVENC (dedicated
  encoder block + small VRAM) after the early evict. If render VRAM + whisper ever conflict,
  serialize just the whisper span with a `threading.Lock`.
- Flags: `CLIP_BATCH_PREFETCH=1` default-off; failure-soft — any prefetch exception logs and
  falls back to the normal inline path (`future = None`).
- **Value (revised honest estimate)**: ~4–6 min × (N−1) on fresh batches (render window ~5.6
  min covers most of a ~7 min Stage 2; remainder spills serially). ~0 on re-run batches
  (Stage 2 is already cache-hits). Weigh effort accordingly — build AFTER Phases 0–4.
- Validate: 2-VOD fresh batch, prefetch on vs off — per-VOD transcript/events/segment hashes
  identical; wall saving measured; whisper stage-2 time within ±20% of solo; run_metrics rows
  sane; no interleaved garbage in the main log.

---

## 8. Phase 6 — C5 GPU HPSS (lowest priority, likely archive)

torch/CUDA reimplementation of `librosa.decompose.hpss(margin=1.0)` semantics (STFT + median
filtering) for `_detect_music_dominance` in `scripts/lib/audio_events.py`. Only worth building
if fresh-VOD scan time still matters after C1 (it runs inside the prefetch window anyway —
which is why this is last). Validation harness already exists: `vector_equiv.py` zero-flip
gate on ≥2 real VODs (FP differs from librosa → flips possible → gate is mandatory) + bench
must beat the shipped threaded scan end-to-end. Expect RED; keep the numbers.

---

## 9. Execution order & gates summary

```
P0 instrumentation ──► S1 spec decode (I-S1.0..5, C3.2 verified by its bench)
                 │             │
                 │             └─ S2 MTP repack (only if S1 <1.2×; weights-change gate)
                 ├─► C3.1 unload-skip (independent, tiny — do alongside S1)
                 ├─► P evalBatchSize + C2(a) TTFT ──► C2(b) reorder (only if ≥20% available)
                 ├─► C4 segment cache (independent)
                 └─► C1 batch prefetch (last big build) ──► C5 GPU HPSS (optional)
```

| Item | Risk class | Gate before default-on |
|---|---|---|
| S1 draft | distribution-preserving | bench ≥1.5× decode; full-run ≥15%; overlap ≥5/10; owner OK |
| S2 MTP repack | **weights change** | all S1 gates + explicit owner signoff |
| P evalBatchSize | FP-noise class | prefill bench win; no VRAM spill |
| C2(a) measure | none (read-only) | n/a — record verdict |
| C2(b) reorder | **output-changing** | realized TTFT win + overlap ≥5/10 + owner OK |
| C3 unload-skip / TTL | none (lifecycle only) | one clean cached re-run |
| C4 segment cache | freezes a stochastic draw | byte-identical 2nd run + owner OK on semantics |
| C1 prefetch | none per-VOD (no LLM concurrency) | hash-identical outputs + measured saving |
| C5 GPU HPSS | FP → fire-flips possible | zero-flip on 2 VODs + beats threaded scan |
| C6 retry audit | none (read-only) | n/a — record rate, close if ≈0 |

**Projected best case** (all GREEN): fresh batch VOD ~40 min → **~25–30 min** (S1 cuts the
~19–33 min LLM span by the accepted-token factor; C1 hides Stage 2; C3/C4 trim fixed costs).
Re-run: ~40–45 → **~30–35 min** (S1 + C3 + C4). Numbers to be replaced by measurements as
phases land.

## 10. No-go / closed (decided — don't re-litigate)

Split-mode tuning (not controllable on Vulkan), row split (not exposed), flash-attn (already
on), qwen3.5-9b as draft (compute-backwards + VRAM), MTP on current GGUF (no head), disk/ReBAR
(already optimal), llama.cpp-direct second stack (out of stack), and the owner-excluded
quality-touching list at the top.

Related: [[concepts/pipeline-speed-findings-2026-07]] (§7 correction, §9 facts) ·
[[concepts/plan-pipeline-speed-2026-07]] (Wave 1, shipped) · [[concepts/vram-budget]] ·
[[entities/qwen35]]
