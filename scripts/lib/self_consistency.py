#!/usr/bin/env python3
"""Self-consistency candidate ranking — Phase 5.2 of the 2026 upgrade.

Implements the "sample N, pick the most consistent" pattern from
ClippingResearch.md §8.2:

- **Universal Self-Consistency** (Chen et al., arXiv:2311.17311): feed N
  sampled candidates back to a scorer and pick the "most consistent" one
  — candidates that agree with each other are more likely correct.
- **SelfCheckGPT-style divergence** (Manakul et al., EMNLP 2023): spread
  across samples is a hallucination signal; low spread → grounded, high
  spread → hallucinated.

Opt-in via ``config/self_consistency.json::enabled = true``. When
enabled, Stage 6 samples N candidates at ``temperature`` per call
instead of regenerating once on cascade fail. Ranks the candidates by
combined (reference grounding) × (pairwise agreement), then returns
them in best-first order so the caller can cascade-through the list.

Stdlib only — uses token-Jaccard via ``grounding.content_overlap_ratio``
(falls back to a local implementation if the grounding module is missing).
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

DEFAULT_CONFIG_PATH = Path(
    os.environ.get("CLIP_SELF_CONSISTENCY_CONFIG", "/root/.openclaw/self_consistency.json")
)

# Reuse Phase 1's grounding module — it already has token-overlap.
_grounding = None


def _get_grounding():
    global _grounding
    if _grounding is not None:
        return _grounding
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in sys.path:
            sys.path.insert(0, here)
        import grounding as _g  # type: ignore
        _grounding = _g
    except ImportError:
        _grounding = None
    return _grounding


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def load_config(path: Optional[str] = None) -> dict:
    """Load config/self_consistency.json with safe defaults."""
    cfg = _read_json(Path(path) if path else DEFAULT_CONFIG_PATH)
    cfg.setdefault("enabled", False)
    cfg.setdefault("n_candidates", 3)
    cfg.setdefault("temperature", 0.8)
    cfg.setdefault("method", "content_overlap")
    cfg.setdefault("fields_to_sample", ["title", "hook", "description"])
    cfg.setdefault("agreement_weight", 0.4)
    cfg.setdefault("min_score_diff", 0.05)
    return cfg


# ---------------------------------------------------------------------------
# Scoring methods
# ---------------------------------------------------------------------------


def _content_overlap_score(candidate: str, reference: str) -> float:
    """Jaccard overlap of content tokens between candidate and reference.
    Delegates to grounding.content_overlap_ratio when available; falls
    back to a local implementation when grounding is missing."""
    g = _get_grounding()
    if g is not None:
        return g.content_overlap_ratio(candidate, reference)
    # Local fallback — very rough.
    a = set(re.findall(r"[A-Za-z']+", (candidate or "").lower()))
    b = set(re.findall(r"[A-Za-z']+", (reference or "").lower()))
    a = {t for t in a if len(t) > 2}
    b = {t for t in b if len(t) > 2}
    if not a:
        return 1.0
    if not b:
        return 0.0
    return len(a & b) / len(a)


def _pairwise_agreement(candidates: List[str], scorer) -> List[float]:
    """Mean pairwise score of each candidate against the others (USC)."""
    n = len(candidates)
    if n <= 1:
        return [1.0] * n
    means = []
    for i, a in enumerate(candidates):
        total = 0.0
        count = 0
        for j, b in enumerate(candidates):
            if i == j:
                continue
            s = scorer(a, b)
            if s is None:
                continue
            total += s
            count += 1
        means.append(total / count if count else 0.0)
    return means


# ---------------------------------------------------------------------------
# Top-level ranking
# ---------------------------------------------------------------------------


def rank_candidates(
    candidates: List[str],
    reference: str,
    config: Optional[dict] = None,
) -> List[Dict]:
    """Rank ``candidates`` by combined grounding + pairwise-agreement
    score. Returns a list sorted best-first:

        [{"text": ..., "rank": 0, "score": 0.82, "ref_score": 0.77, "agreement": 0.91}, ...]

    Empty input → empty output. Single input → that candidate with
    score=ref_score, agreement=1.0 (degenerate case).
    """
    cfg = config if config is not None else load_config()
    cands = [c for c in candidates if (c or "").strip()]
    if not cands:
        return []

    agreement_weight = float(cfg.get("agreement_weight", 0.4))
    scorer = _content_overlap_score

    # Reference scores.
    ref_scores: List[float] = []
    for c in cands:
        s = scorer(c, reference)
        ref_scores.append(float(s) if s is not None else 0.0)

    # Pairwise agreement (USC-style).
    agreements = _pairwise_agreement(cands, scorer) if len(cands) > 1 else [1.0]

    # Combine.
    combined: List[Tuple[float, float, float, str, int]] = []
    for i, c in enumerate(cands):
        ref = ref_scores[i]
        agr = agreements[i]
        score = (1.0 - agreement_weight) * ref + agreement_weight * agr
        combined.append((score, ref, agr, c, i))

    combined.sort(key=lambda t: t[0], reverse=True)

    out: List[Dict] = []
    for rank, (score, ref, agr, text, orig_idx) in enumerate(combined):
        out.append(
            {
                "text": text,
                "rank": rank,
                "score": round(score, 4),
                "ref_score": round(ref, 4),
                "agreement": round(agr, 4),
                "original_index": orig_idx,
            }
        )
    return out


def rank_field_dict(
    candidate_dicts: List[Dict],
    field: str,
    reference: str,
    config: Optional[dict] = None,
) -> List[Dict]:
    """Convenience: rank the ``field`` value across a list of parsed
    response dicts. Returns the full dict of the winner + metadata for
    the full ranking. Missing / empty field values are dropped.

    Output shape:

        {
          "winner": { ...full parsed dict... },
          "winner_field_text": "...",
          "ranking": [ {text, rank, score, ...}, ... ],
          "n_candidates": N,
        }
    """
    texts = [(d.get(field) or "").strip() for d in candidate_dicts]
    valid = [(i, t) for i, t in enumerate(texts) if t]
    if not valid:
        return {"winner": None, "winner_field_text": "", "ranking": [], "n_candidates": 0}

    ordered_texts = [t for _i, t in valid]
    ranking = rank_candidates(ordered_texts, reference, config)

    if not ranking:
        return {"winner": None, "winner_field_text": "", "ranking": [], "n_candidates": len(valid)}

    best = ranking[0]
    # Map back to the original dict (via the "original_index" in the ranking,
    # which is an index into `ordered_texts`; `valid[that].0` is the source index).
    src_index_in_dicts = valid[best["original_index"]][0]
    winner_dict = candidate_dicts[src_index_in_dicts]
    return {
        "winner": winner_dict,
        "winner_field_text": best["text"],
        "ranking": ranking,
        "n_candidates": len(valid),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="Self-consistency ranking (Phase 5.2)")
    ap.add_argument("--candidate", "-c", action="append", required=True, help="candidate text (repeatable)")
    ap.add_argument("--reference", "-r", required=True)
    ap.add_argument("--agreement-weight", type=float, default=None)
    args = ap.parse_args()

    cfg = load_config()
    if args.agreement_weight is not None:
        cfg["agreement_weight"] = args.agreement_weight

    ranked = rank_candidates(args.candidate, args.reference, cfg)
    json.dump({"method": "content_overlap", "ranking": ranked}, sys.stdout, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    _cli()
