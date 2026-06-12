---
title: "Self-Consistency Ranking — Phase 5.2"
type: concept
tags: [self-consistency, usc, hallucination, phase-5, stage-6, vision]
sources: 2
updated: 2026-06-12
---

# Self-Consistency Ranking

> [!warning] REMOVED 2026-06-12 — never wired in, module deleted
> The "available but not wired in by default" state below never changed: the 2026-06-12 audit confirmed no stage ever imported [[entities/self-consistency-module]], and it was deleted along with its config and env plumbing — see [[concepts/bugs-and-fixes#REMOVAL 2026-06-12]]. Page kept as the architectural record for any future Phase 5.2 revival (code recoverable from `archive/clipping-intelligence-2026-06-04/` or git history).

Per `ClippingResearch.md` §8.2: sample N candidates at high temperature, pick the one that agrees with the others AND is most grounded in the reference. The core insight is from **Universal Self-Consistency** (Chen et al., arXiv:2311.17311) and **SelfCheckGPT** (Manakul et al., EMNLP 2023, arXiv:2303.08896): hallucinated claims diverge across samples; grounded claims stay consistent.

Phase 5.2 ships [[entities/self-consistency-module]] — a standalone ranking library that can be invoked from any call site that produces N candidates. Stage 6 integration is **available but not wired in by default** — enabling it is a follow-up decision once the user has `config/self_consistency.json::enabled = true` and is ready for the ~3× vision-call cost.

---

## Scoring

For each candidate `c_i`:

1. **Reference score** — content-token overlap between `c_i` and the concatenated reference (`transcript + chat_context + pass_b_why` in Stage 6's case). The legacy `minicheck` method was retired 2026-05-01 alongside the MiniCheck tier in the grounding cascade.
2. **Agreement score** (USC) — mean pairwise score of `c_i` against the other N−1 candidates.
3. **Combined** = `(1 − w) × ref_score + w × agreement`, where `w` is `config/self_consistency.json::agreement_weight` (default 0.4; lean toward reference grounding).

The candidate with the highest combined score is the winner.

---

## Methods

`config/self_consistency.json::method` controls the scorer:

| Method | Deps | Cost | What it catches |
|---|---|---|---|
| `content_overlap` (only) | stdlib | <1 ms | lexical divergence — "gifted subs" in one, "community raid" in another |
| `pairwise` (future) | sentence-transformers | ~5 ms/pair | embedding-cosine USC (not yet wired) |

`content_overlap` is the only supported method since 2026-05-01 — the previous `minicheck` method depended on the Tier-2 MiniCheck NLI weights that were retired alongside the grounding cascade simplification.

---

## When it's worth enabling

Self-consistency is **opt-in** because sampling N candidates means N vision calls per clip. On a 10-moment VOD at N=3, that's 30 vision calls vs. the current 10–20 (10 first-attempts + 0–10 retries on grounding fail).

Enable when:
- The cascade's regenerate-once policy still lets too many hallucinated titles through.
- You have VRAM / wall-time budget to burn.
- You're running a domain where hallucinations are especially costly (client delivery, marketing-sensitive channels).

Leave disabled when:
- Phase 2.4d hard-event ground truth + Phase 4.1 overlay-text grounding already cover your failure modes.
- Wall time matters more than the marginal quality gain.

---

## Non-integration surface

The module is usable standalone:

```python
import self_consistency as sc

cfg = sc.load_config()
ranked = sc.rank_candidates(
    candidates=["clutch ranked 3.0 push", "sub train celebration", "epic rank-up"],
    reference="okay that was insane rank 3.0",
    config=cfg,
)
# ranked[0] is the best-grounded candidate.
```

Or with parsed vision output dicts:

```python
parsed_n = [first_call_result, retry_1, retry_2]   # 3 sampled vision responses
winner = sc.rank_field_dict(parsed_n, field="title", reference=reference_text)
# winner["winner"] is the full dict; winner["ranking"] has per-candidate details.
```

The CLI `python3 scripts/lib/self_consistency.py --candidate c1 --candidate c2 --reference r` prints the JSON ranking to stdout.

---

## Integration surface (available, not wired)

When Stage 6's existing "regenerate once on cascade fail" policy isn't enough, the full Phase 5.2 integration would:

1. Sample `n_candidates` vision responses UP FRONT at `temperature=0.8` (not the current T=0.3).
2. For each output field (`title` / `hook` / `description`), call `sc.rank_field_dict(n_outputs, field, reference)` to rank.
3. Try the top-ranked candidate against the grounding cascade; on fail, try rank 1, rank 2, etc.
4. Fall back to null-and-default only when ALL ranked candidates fail the cascade.

This replaces the current regenerate-once flow entirely; it's a drop-in swap when the user enables `self_consistency.enabled = true`. The Stage 6 PYEOF block is a good target for this wire-in — it already has the `_vision_call()` closure, so wrapping it in a sampling loop is straightforward. Tracked as a Phase 5.2b follow-up in `IMPLEMENTATION_PLAN.md`.

---

## Related

- [[entities/self-consistency-module]] — implementation
- [[entities/grounding]] — the cascade the ranker runs against
- [[concepts/vision-enrichment]] — Stage 6 (currently uses regenerate-once; self-consistency is a drop-in replacement)
- `config/self_consistency.json` — runtime config
