---
title: "self_consistency.py — N-candidate ranker"
type: entity
tags: [self-consistency, usc, phase-5, module, stage-6, vision]
sources: 1
updated: 2026-05-01
---

# `scripts/lib/self_consistency.py`

Phase 5.2 implementation of Universal Self-Consistency + SelfCheckGPT-style ranking. Introduced 2026-04-24. Stdlib-only — uses token-Jaccard via `grounding.content_overlap_ratio` with a local fallback when the grounding module is missing. The legacy `method="minicheck"` path was retired 2026-05-01 alongside the MiniCheck tier in the grounding cascade — see [[concepts/bugs-and-fixes#REMOVAL 2026-05-01b]].

See [[concepts/self-consistency]] for the architectural picture.

---

## API

```python
import self_consistency as sc

cfg = sc.load_config()               # /root/.openclaw/self_consistency.json
```

### `rank_candidates(candidates, reference, config)`

Rank N string candidates against a reference. Returns a list of dicts sorted best-first:

```python
ranked = sc.rank_candidates(
    candidates=["a grounded title", "hallucinated title", "another grounded"],
    reference="what the streamer actually said",
    config=cfg,
)
# [
#   {"text": ..., "rank": 0, "score": 0.72, "ref_score": 0.80, "agreement": 0.60, "original_index": 0},
#   {"text": ..., "rank": 1, "score": 0.55, ...},
#   {"text": ..., "rank": 2, "score": 0.12, ...},
# ]
```

### `rank_field_dict(candidate_dicts, field, reference, config)`

Convenience wrapper for parsed VLM outputs. Pass a list of parsed JSON dicts; rank by one field; return the winning dict plus the full ranking.

```python
n_outputs = [first_attempt, retry_1, retry_2]
winner = sc.rank_field_dict(n_outputs, field="title", reference=refs_text)
# winner["winner"] is the full parsed dict
# winner["winner_field_text"] is the winning title string
# winner["ranking"] is the full ranked list
# winner["n_candidates"] is how many non-empty candidates were found
```

### `load_config(path=None)`

Load `config/self_consistency.json` with safe defaults. Missing file → defaults (enabled=False, N=3, method=content_overlap).

---

## Methods

`cfg["method"]` is currently a vestigial knob — only `content_overlap` is supported:

- **`content_overlap`** (default and only): token-level Jaccard via `grounding.content_overlap_ratio` when available, with a local stdlib fallback. Zero deps.
- **`pairwise`** (reserved): placeholder for future sentence-transformers cosine similarity.
- **`minicheck`** (retired 2026-05-01): reused the Tier-2 MiniCheck NLI from the grounding cascade. Removed when MiniCheck itself was retired — see [[concepts/bugs-and-fixes#REMOVAL 2026-05-01b]].

The `agreement_weight` parameter (default 0.4) controls how much to weight USC-style candidate-vs-candidate agreement vs candidate-vs-reference grounding in the final score. 0.0 = pure grounding; 1.0 = pure USC.

---

## Fallback ladder

- `candidates` empty → empty list out.
- `candidates` all empty / whitespace → empty list.
- Single candidate → returned with `rank=0`, `agreement=1.0`.
- `grounding` module missing → local stdlib Jaccard fallback.

---

## CLI

```
python3 scripts/lib/self_consistency.py \\
    --candidate "first candidate" \\
    --candidate "second candidate" \\
    --candidate "third candidate" \\
    --reference "reference text"
```

Prints JSON `{"method": ..., "ranking": [...]}` to stdout.

---

## Related

- [[concepts/self-consistency]] — architectural overview
- [[entities/grounding]] — supplies `content_overlap_ratio`
- [[concepts/vision-enrichment]] — potential Stage 6 integration point
- `config/self_consistency.json` — runtime config
