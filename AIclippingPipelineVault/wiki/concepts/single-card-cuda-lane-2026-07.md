---
title: "Single-card CUDA lane — where a smaller text model fits the pipeline (measured 2026-07-14)"
type: concept
tags: [performance, speed, cuda, vulkan, lm-studio, runtime, stage-3, stage-4, pass-b, serving, gpu, reference]
sources: 0
status: shipped
updated: 2026-07-15
---

# Single-card CUDA lane — where qwen3.5-9b-class models fit for speed

**Owner doctrine (2026-07-14)**: the dual-GPU Vulkan pool (~28 GB) STAYS — it is what
makes big models like `qwen/qwen3.6-35b-a3b` (22 GB) possible at all. The question this
page answers: where can **NVIDIA-only single-card CUDA** serving of a *similarly capable
but smaller* model (`qwen/qwen3.5-9b`, 6.55 GB) be used inside the pipeline for speed,
without giving up the 35B where it matters.

All numbers measured live 2026-07-14 on the owner's box (LM Studio 0.4.19, RTX 5060 Ti
16 GB + RX 6700 XT 12 GB, same prompts, run-unique 5k-token filler → **raw prefill, no
KV-prefix-reuse assist**). Mechanics of the runtime switch itself:
[[concepts/plan-serving-stack-2026-07]] §11.

---

## 1. The measured matrix (the whole story in one table)

| configuration | decode | prefill (5.1–5.4k prompt) | Pass-B-shaped call (5k in / short out) | vs baseline |
|---|---|---|---|---|
| **35B-A3B · Vulkan dual-GPU** (today's pipeline) | 27.7–28.4 tok/s | ~190 tok/s (TTFT 26.5–28.7 s) | **~30 s** | 1× |
| **9B · Vulkan dual-GPU** (config-only swap) | 53.3–53.7 tok/s | ~1,005 tok/s (TTFT 5.4 s) | **8.25 s** | **3.6×** |
| **9B · CUDA NVIDIA-only** (runtime switch) | 60–62 tok/s | ~2,800 tok/s (TTFT 1.86 s) | **4.6 s** | **6.4×** |

Decomposition: **~3.6× comes from the smaller model alone** (same Vulkan runtime, zero
new code — see §4) and **~1.8× more from the CUDA runtime** (needs the select wrapper).
Load/unload costs: 9B warm load 4.7 s (CUDA) / 6.1 s (Vulkan), 35B warm load 18.3 s,
unloads ~1 s → a full text↔vision phase swap costs **~25 s per run**. One-time gotcha:
the FIRST-ever CUDA load of a model took **6 m 27 s** (cold disk + CUDA kernel JIT for
the Blackwell card, cached after — budget one slow load per model × runtime pairing).

Why the 35B is so slow on exactly our workload: the Vulkan cross-vendor split's **raw
prefill floor is ~190 tok/s**, and Pass B is "feed a ~5k-token transcript chunk, get a
short JSON back" — prefill-dominated. Context length is NOT a factor (measured identical
at ctx 16384 and 32768). The older "50 tok/s / prefill ~42%" figures in
[[concepts/pipeline-speed-findings-2026-07]] §9b were replayed real prompts whose big
static prefix hit the KV prefix cache; production sits between the two, but the
per-chunk VARIABLE tokens (~2k transcript + prior-context cards) always pay the raw
floor (~11 s per chunk call on Vulkan-35B vs ~0.7 s on CUDA-9B).

> [!note] 9B is not a wild downgrade
> `qwen/qwen3.5-9b` is the dashboard's own SUGGESTED text model ("best reasoning + JSON
> output for moment detection"); the 35B was a quality upgrade on top. And the 9B is
> multimodal (vision-capable) — every role below is *technically* servable by it. The
> question per call site is quality, not capability.

## 2. Pipeline call-site map — where the lane applies

Volumes are for a fresh 3 h talk VOD (~10 clips), from the measured batch
([[concepts/pipeline-speed-findings-2026-07]] §10). "Key" = the models.json knob that
already routes that site.

| # | site | key (today's value) | volume | time today | CUDA-lane fit | quality risk / gate |
|---|---|---|---|---|---|---|
| 1 | S2 whisper + S7 captions | `whisper_model` | — | ~12 min | **already NVIDIA-CUDA** (faster-whisper) | n/a |
| 2 | S7 NVENC encode | — | — | — | **already NVIDIA** (ASIC) | n/a |
| 3 | S3 segment classification | `text_model` (35B) | ~6–8 votes/chunk-group | ~3 min | ✅ good — bounded classification | MED: labels steer prompts + gate signals |
| 4 | S4 chunk cards | `text_model_passb` (→35B) | ~24 calls | ~12 min | ✅ good — structured extraction | LOW-MED |
| 5 | S4 Pass B moments | `text_model_passb` (→35B) | ~24 calls | ~30 min (with #6/#7) | ✅ **the big one** | **HIGH: this is the finder — owner clip-set A/B required** |
| 6 | S4 grounding judges | same instance as #5 | ~10–30 calls | (inside #5) | ✅ | MED: nulls `why` fields only, not recall |
| 7 | S4 rubric / Pass D | `text_model_passd` (→passb) | per-moment | (inside #5) | ✅ **designed for it** — models.json's own note recommends a smaller/different model for decorrelation | LOW |
| 8 | S5.5 Vision Judge | `vision_model` (35B) | ~30 comparisons | ~6.5 min | ⚠ possible (9B is multimodal) but | HIGH: ranking quality = clip selection |
| 9 | S6 Vision Enrichment + caption gates | `vision_model_stage6` (35B) | ~10–20 calls | ~13 min | ⚠ same | HIGH: titles/hooks voice quality |
| 10 | S7 cut_inference (jump cuts, default-off) | text model | ~1–2 calls/clip | 0 today | ✅ when enabled | MED: coherence gate already guards |
| 11 | Reference Lab cards + narrative | **per-job model picker (exists!)** | 86-clip batch ≈ 65 min on 35B | offline | ✅ **available TODAY, zero code** | LOW: cards are owner-reviewed anyway; Lab already proved 9B differs on a category — that's the point of the picker |
| 12 | News compile / corpus_diff narrative | job model | few calls | offline | ✅ same as #11 | LOW |

**Bottom line of the map**: the natural split is a **text phase (S3+S4 → 9B)** and a
**vision phase (S5.5+S6 → 35B)**. Sites #3–#7 all live in the text phase and swap
together; #8/#9 stay on the 35B (that's where the dual-GPU pool earns its keep); #11/#12
can use the 9B *today* via the Lab's existing model picker.

## 3. Why phase-level granularity (the VRAM math)

Co-residence is impossible at current sizes: the Vulkan 35B holds **~14 GB of the 16 GB
NVIDIA card** (plus ~10 GB of the AMD card); a CUDA 9B needs ~7–9 GB of the same NVIDIA
card → 23 GB > 16 GB. So you cannot cherry-pick "rubric on 9B, Pass B on 35B" within
Stage 4 — whichever model is loaded during the stage serves ALL its calls. The unit of
choice is the **phase**, and the phases already exist in code (§4). AMD sits idle during
the text phase — that is the deliberate trade: ~25 s of swap for ~6× on the calls.

## 4. The machinery already exists — rollout ladder

Discovered during this research: the pipeline ALREADY swaps models at exactly the right
boundaries — `stage4.py:19` ("Phase 5.1: swapping text model → text_model_passb") and
`stage6.py:20` (swap passb → `vision_model_stage6`, skipped when equal). Model routing
is pure config; only the RUNTIME choice needs new code.

- **L0 — Lab on 9B (today, zero code)**: pick `qwen/qwen3.5-9b` in the Reference Lab's
  model dropdown for card batches → ~3.6× on Vulkan as-is (86-card batch ≈ 65 → ~18 min).
- **L1 — `text_model_passb: "qwen/qwen3.5-9b"` (config-only)**: Stage 4 (cards + moments
  + judges + rubric) runs on the 9B **on Vulkan** → measured **3.6× per call**, S4
  ~47 → ~17–22 min, fresh 3 h VOD ~90 → ~62–68 min. Existing swap logs make it
  auditable. **Gate: owner clip-set A/B vs a unified run on the same VOD** — this is
  the finder; quality is the whole question ([[concepts/pass-b-false-negatives]]).
- **L2 — CUDA runtime wrapper (+~1.8× on top)**: wrap the stage-4 load in
  `lms runtime select cuda → load → select vulkan back` (a `runtime=` param on
  `common.load_model`, selection restored on EVERY error path). S4 → ~13–18 min, fresh
  3 h VOD ~55–60 min. Combined with the Tier-1/2 diets from the speed plan → **~40–45
  min, hitting the owner's target**.
- **L3 — S3 on the 9B too**: set `text_model` = 9B (keep `vision_model` = 35B explicit).
  Saves ~2 min more and removes the S3(35B)→S4(9B) swap; MED risk (segment labels).
- **L4 — vision phase on 9B (probably never)**: possible (9B is multimodal), high risk
  to titles/hooks/judge ranking; only worth testing if L1/L2 quality holds AND the
  vision stages become the bottleneck.

## 5. Landmines (operational)

- **Runtime selection is process-global**: while GGUF=CUDA, ANY concurrent load (TTL
  re-load, Lab job, second client) binds CUDA — a 22 GB model on the 16 GB card
  spills/fails. Keep the flipped window tight; restore on every exit path; never leave
  it flipped between stages.
- **First-load JIT**: one ~6 min load per model × CUDA pairing (then 4.7 s warm). Do it
  once manually before relying on it in a run.
- **Version drift**: CUDA pack is 2.23.1 vs Vulkan 2.24.0 — `lms runtime update` exists;
  re-verify after LM Studio upgrades (backend dir names are versioned).
- **Skip-if-loaded footgun**: `common.load_model` skips when the id is already loaded —
  a model resident on the WRONG runtime (or wrong ctx) carries into the run. The wrapper
  must check more than the id, or unload first.
- **Quality doctrine**: default-off until the owner promotes (RED rubric). L1's A/B is
  the decision point for everything above it.

## 6. SHIPPED — production results + the two operational laws (2026-07-15)

The ladder shipped through L3 (L0 available, L1+L2 = Waves B1/B2, L3 = B3). Production
measurements (Raud 3.47 h + FirstFullAudio 1.19 h, [[concepts/pipeline-speed-findings-2026-07]] §11):

- **S4 on the lane: 3.9–5.0 min/VOD-h** (was ~15 on the unified 35B — ~3.5×), zero call
  failures once the two laws below were honored. **S3 on the 9B: 89.7 s vs 274 s (3.1×)**.
- **Law 1 — the context POOL ([[concepts/bugs-and-fixes#BUG 73]])**: llama.cpp shares the
  loaded ctx across ALL in-flight requests. Lane loads at **32768** (KV ≈ 6.1 GiB total)
  and runs **2 workers**; `workers ≤ ctx / (max prompt + worst-case gen)`. Slots are not
  free — 4 workers × big prompts = every call failing with "Context size has been exceeded".
- **Law 2 — phase-pin every judge model id ([[concepts/bugs-and-fixes#BUG 74]])**: any env
  fallback (`CLIP_TEXT_MODEL`) that names the OTHER phase's model JIT-summons it — a 22 GB
  ghost beside the 9B (VRAM spill) or a CPU-placed 9B beside the 35B (offload 0). Stage 4
  pins judges to `text_model_passb`, Stage 6 to `vision_model_stage6`. The `ctx 16384 +`
  unexpected-placement combo in `lms ps` is the JIT-ghost fingerprint — both instances were
  owner-spotted in LM Studio's UI.
- Ops notes: one ~6.5 min first-ever CUDA load per model (Blackwell JIT, then 4.7 s warm);
  runtime selection restored in a `finally` on every path; hardware profiles keep the lane
  auto-inert off dual-vendor rigs ([[concepts/plan-speed-wave3-2026-07]] §2b).

## Related

[[concepts/plan-serving-stack-2026-07]] §11 (runtime-switch mechanics + PoC detail) ·
[[concepts/pipeline-speed-findings-2026-07]] §10 (fresh-VOD baseline this optimizes) ·
[[concepts/plan-pipeline-speed-2026-07]] · [[concepts/pass-b-false-negatives]] (why the
Pass-B gate is quality) · [[concepts/vram-budget]] · [[entities/qwen35]] ·
[[entities/lm-studio]] · [[concepts/reference-lab]] (the L0 model picker)
