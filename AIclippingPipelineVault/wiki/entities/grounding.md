---
title: "grounding.py — 2-tier grounding cascade (Tier 1 denylist + LLM judge)"
type: entity
tags: [grounding, hallucination, denylist, cascade, pass-b, stage-6, llm-judge, module, stage-4, text, hub]
sources: 3
updated: 2026-05-01
---

# `scripts/lib/grounding.py`

Two-tier anti-hallucination cascade. Verifies that LLM-generated text — Pass B `why` and Stage 6 `title`/`hook`/`description` — is supported by the transcript window. If a tier rejects, the offending field is nulled (Pass B) or one regeneration is attempted with a stricter prompt (Stage 6).

> [!note] 2026-05-01 — retired MiniCheck and Lynx
> The 3-tier cascade (regex → MiniCheck NLI → Lynx-8B) was collapsed to **two tiers**: Tier 1 (regex denylist + content overlap, unchanged) and Tier 2 (LLM-as-judge using the pipeline's main text model). Reasons: MiniCheck's literal-entailment training was structurally mismatched to inferential summaries (BUG 35 lowered the threshold to 0.3 to compensate), and Lynx's "independence" disappeared in practice because LM Studio routes its requests to whatever single model the operator has loaded (BUG 33/44). Both retired tiers also added recurring tuning surface and ~6.5 GB of model weights to disk. The companion A/B harness `grounding_ab.py` was removed too — there's nothing to compare against. See [[concepts/bugs-and-fixes#REMOVAL 2026-05-01b]].

---

## API

### Tier 1 only (back-compat)

```python
import grounding

denylist = grounding.load_denylist()            # loads /root/.openclaw/denylist.json
result = grounding.check_claim(
    claim="Streamer Gets Gifted Subs After Ranked 3.0 Clutch",
    references=[transcript_window, pass_b_why],
    denylist=denylist,
    min_overlap=0.15,
)
# → {"passed": False, "reason": "denylist_unsupported", ...}
```

### Full cascade

```python
denylist = grounding.load_denylist()
config = grounding.load_grounding_config()       # loads /root/.openclaw/grounding.json

result = grounding.cascade_check(
    claim="Streamer rages after losing match",   # wrong meaning, right words
    references=[transcript_window, pass_b_why],
    denylist=denylist,
    config=config,
    min_overlap=0.15,
)
# → {
#     "passed": False,
#     "reason": "judge_low_weighted",
#     "tier": "judge",
#     "judge_weighted": 3.4,
#     "judge_dims": {"grounding": 2, "setup_payoff": 4, "speaker": 5, "conceptual": 3, "callback": 0},
#     "judge_rationale": "claim asserts the streamer is angry, transcript shows laughter",
#     "escalations": [
#       {"tier": 1, "passed": True, "overlap": 0.38},
#       {"tier": "judge", "passed": False, "weighted": 3.4, "dims": {...}, "rationale": "..."},
#     ],
#   }
```

### Helpers

- `load_denylist(path=None)` — compile JSON denylist to `{category: [re.Pattern]}`; missing file ⇒ `{}` (cascade collapses to overlap-only Tier 1).
- `load_grounding_config(path=None)` — load `/root/.openclaw/grounding.json`; missing file ⇒ `{}` (cascade collapses to Tier 1 + module defaults).
- `denylist_hits(text, compiled)` — list every regex match in `text`.
- `content_overlap_ratio(claim, reference)` — token-overlap ratio after stop-word stripping.
- `check_claim(...)` — Tier 1 only (preserved for callers that want the cheapest check).
- `cascade_check(...)` — Tier 1 → judge. Returns the same shape as `check_claim` plus a `tier` field (`1` or `"judge"`) and an `escalations` list with every intermediate result.
- `llm_judge(claim, transcript_window, optional_setup, optional_speaker_info, ...)` — call the judge directly. Returns `{grounding, setup_payoff, speaker, conceptual, callback, rationale}` (each 0-10) or `None`.
- `_resolve_judge_model(cfg_model)` — pick the judge model. Order: explicit cfg → `CLIP_GROUNDING_JUDGE_MODEL` env → `CLIP_TEXT_MODEL` env → fallback `qwen/qwen3.5-9b`.
- `_judge_weighted_score(dimensions, weights=None)` — reduce 5-dim judge result to a single 0-10 score via weighted mean. Default weights: grounding 0.55, setup_payoff 0.15, speaker 0.05, conceptual 0.15, callback 0.10.

CLI mode:
- `python3 scripts/lib/grounding.py --claim "..." --ref "..."` → runs the full cascade.
- Add `--tier-1-only` for a stdlib-only check (useful for offline tests).
- Exits 0 on pass, 1 on fail.

---

## Tier 1 — regex denylist + overlap (stdlib)

Stdlib only, <5 ms. Catches:

- **Exact-word hallucinations** — claim says "gifted subs" but the transcript has no "gifted" or "subs" tokens. Phase 0.3 logic.
- **Phase 2.4d hard-event check** — denylist hit on a Twitch-event keyword AND the chat window has zero events of that type → instant `event_contradicts_ground_truth` reject. This is the **single sharpest anti-hallucination signal in the system** because chat events are factual, not subjective. It runs regardless of judge availability and **no LLM rubber-stamping can defeat it**.

Categories live in `config/denylist.json` (bind-mounted as `/root/.openclaw/denylist.json`):

| Category | Examples |
|---|---|
| `platform_meta_tics` | `subscribe`, `don't forget to`, `like and subscribe`, `hit the bell` |
| `twitch_jargon_overclaims` | `gifted subs`, `sub train`, `hype train`, `raid`, `bits rain`, `tier 3`, `re-sub`, `first-time sub`, `sub-bombing` |
| `generic_creator_templates` | `in this video`, `today we`, `let's dive in`, `welcome back` |
| `sports_highlight_tropes` | `clutch play`, `game-winning`, `triple-kill`, `quad-kill`, `penta-kill`, `ace` |

All patterns are Python regex, compiled case-insensitive, anchored on `\b` where appropriate. Bad patterns in the file are silently skipped — config typos can't crash the pipeline.

---

## Tier 2 — LLM-as-judge

A single call to whatever multimodal model `CLIP_TEXT_MODEL` resolves to (Gemma 4-26B in the default `gemma4-26b` profile, Qwen 3.5-9B / 35B-A3B in the others). The prompt asks for a 5-dimensional 0-10 score in one JSON object:

| Dimension | What it measures | Default weight |
|---|---|---|
| `grounding` | Whether literal facts in the claim are supported by the transcript window | **0.55** |
| `setup_payoff` | Presence of narrative arc structure (build then beat) | 0.15 |
| `speaker` | Multi-speaker / off-screen voice / interruption value | 0.05 |
| `conceptual` | Ironic / contradictory / surprising vs just verbally funny / loud | 0.15 |
| `callback` | If `optional_setup` is supplied, strength of the connection | 0.10 |

Weighted mean ≥ `pass_threshold` (default 5.0) → pass.

### Self-judging caveat

> [!warning] The same model that wrote the claim is now judging it
> Self-judging tends to inflate faithfulness ratings 5-15 percentage points compared to an independent judge. This is a deliberate trade — the structural safety net is **Tier 1's hard-event check**, which is independent of any LLM and catches the most dangerous-class hallucinations ("gifted subs" with `sub_count == 0` in chat). The judge handles softer semantic mismatches where some leniency is acceptable. If self-judging proves too lenient in a given workflow, raise `pass_threshold` (e.g. 6.0–6.5) — that has the same effect as adding an external arbiter without spinning one up.

### Model resolution

`_resolve_judge_model()` picks the model in this order:

1. Explicit `judge.model` in `config/grounding.json`
2. `CLIP_GROUNDING_JUDGE_MODEL` env var (operator override per run)
3. `CLIP_TEXT_MODEL` env var (the pipeline's currently-loaded text model — set by `clip-pipeline.sh` from `config/models.json`)
4. Fallback `qwen/qwen3.5-9b`

Switching models requires no code change — set `CLIP_TEXT_MODEL` (or load a different model in LM Studio and update `config/models.json::active_profile`).

### Config — `config/grounding.json`

```json
{
  "tier_1": {
    "enabled": true,
    "pass_b_min_overlap": 0.15,
    "stage_6_min_overlap": 0.15,
    "clear_pass_overlap": 0.55
  },
  "judge": {
    "enabled": true,
    "model": null,
    "timeout_s": 30,
    "pass_threshold": 5.0,
    "weights": {
      "grounding": 0.55, "setup_payoff": 0.15,
      "speaker": 0.05, "conceptual": 0.15, "callback": 0.10
    }
  },
  "regeneration": {
    "enabled": true,
    "stage_6_retry_count": 1
  }
}
```

`model: null` defers to env-based resolution above. Set `judge.enabled: false` to disable the judge entirely; the cascade then collapses to Tier 1 only.

### Failure modes

- LM Studio unreachable → `llm_judge` returns `None` → cascade returns Tier 1 verdict
- Malformed JSON response → returns `None` → returns Tier 1 verdict
- Missing `lmstudio` helper → `_import_lmstudio` returns `None` → returns Tier 1 verdict
- Tier 1's denylist + hard-event check always runs first, so the most dangerous hallucinations are caught even when the judge fails

---

## Cascade logic

```
cascade_check(claim, references, ...):
  t1 = check_claim(...)                         # always runs
  if t1 is "clear pass" or "hard fail"        → return Tier 1 verdict
  if judge disabled                            → return Tier 1 verdict

  gj = llm_judge(...)
  if gj is None (judge unavailable)           → return Tier 1 verdict (safety net)

  weighted = _judge_weighted_score(gj)
  return judge verdict (passed = weighted >= pass_threshold)
```

"Clear pass" = Tier 1 passes AND `overlap >= clear_pass_overlap` (default 0.55). "Hard fail" = Tier 1 fails with `reason=denylist_unsupported` OR `event_contradicts_ground_truth` (no point asking the judge if the streamer flatly didn't say it, or if chat ground truth contradicts the claim).

---

## Wire points

**Pass B** ([[concepts/highlight-detection]] — `stage4_moments.py`): after `parse_llm_moments` returns, each moment's `why` is checked against a tight ±90 s transcript window (BUG 34) plus the full chunk. Failing `why` is set to `""` and `grounding_fail=<reason>` is recorded on the moment dict. The moment itself stays — Pass C still scores and selects it — so the gate never drops a clip, only prevents a potentially-hallucinated summary from seeding the Stage 6 prompt.

**Stage 6** ([[concepts/vision-enrichment]] — `stage6_vision.py`): after the vision JSON is parsed, `title` / `hook` / `description` are checked against `[±8 s transcript window, transcript_why]`. Failing fields trigger the regenerate-once policy with a stricter prompt that names the violation. Fields that still fail the retry are nulled and `grounding_fails` is appended to the parsed dict. The existing `entry` assembly seeds transcript-only defaults so a nulled title / description falls back gracefully.

---

## Latency budget

- Tier 1: <5 ms per claim (stdlib regex + set ops)
- Tier 2: 0.5–10 s per claim (depends on the active profile — Qwen 3.5-9B fast, Gemma 4-26B with thinking slow)

Tier 2 only runs on borderline-Tier-1 claims (the cascade short-circuits clear passes and hard fails), so the typical VOD pays the judge cost on ~30-100 claims, not every parsed moment.

VRAM: zero overhead — the judge reuses the main text model that's already loaded.

---

## Related

- [[concepts/vision-enrichment]] — Stage 6 consumer
- [[concepts/highlight-detection]] — Pass B consumer
- [[concepts/bugs-and-fixes]] BUG 26 — the hallucination mode this fixes
- [[concepts/bugs-and-fixes#REMOVAL 2026-05-01b]] — the MiniCheck + Lynx retirement
- [[entities/lmstudio]] — HTTP client used by the judge call
