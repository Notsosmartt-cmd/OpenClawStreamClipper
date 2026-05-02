"""Phase 4.6 — Maximal Marginal Relevance (MMR) diversity rank.

Re-ranks Pass D survivors so near-duplicate moments don't dominate the final
selection. Operates on sentence-transformer embeddings of each moment's
``why`` field. Reuses the M3 callback module's loaded model when available.

The ranker is failure-soft: when sentence-transformers isn't installed or
embeddings fail, the file is left unchanged (the existing Pass C/D ordering
survives). The phase can never delete a candidate.

Algorithm:
    selected = []
    while remaining:
        for each remaining moment i:
            score_i = moment[i].score (or raw_score / final_score / fallback)
            max_sim = max(cosine(emb[i], emb[j]) for j in selected) or 0
            mmr_i = lambda * score_i - (1 - lambda) * max_sim
        pick the i with highest mmr_i
    write sorted-by-selection-order back to hype_moments.json
"""
from __future__ import annotations

import json
import math
import os
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

TEMP_DIR = "/tmp/clipper"
DEFAULT_LAMBDA = 0.7


def _load_lambda() -> float:
    for path in ("/root/.openclaw/rubric.json", "/root/scripts/lib/../../config/rubric.json"):
        try:
            with open(path) as f:
                cfg = json.load(f)
            return float(cfg.get("mmr_lambda", DEFAULT_LAMBDA))
        except (OSError, json.JSONDecodeError, ValueError, TypeError):
            continue
    return DEFAULT_LAMBDA


def _moment_score(m: Dict[str, Any]) -> float:
    for key in ("raw_score", "final_score", "score"):
        v = m.get(key)
        try:
            if v is None:
                continue
            return float(v)
        except (ValueError, TypeError):
            continue
    return 0.0


def _moment_text(m: Dict[str, Any]) -> str:
    """Pick the most-informative text for embedding. Prefer Pass D audit_one_liner
    (concise, model-curated) → Pass B `why` → preview → category."""
    for key in ("audit_one_liner", "why", "preview"):
        v = (m.get(key) or "").strip()
        if v:
            return v[:512]
    return str(m.get("primary_category") or m.get("category") or "")


def _try_embed(texts: Sequence[str]) -> Optional[List[List[float]]]:
    """Embed via sentence-transformers when available. Returns None on failure."""
    if not texts:
        return None
    try:
        sys.path.insert(0, "/root/scripts/lib")
        from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]
    except Exception as e:
        print(f"[MMR] sentence-transformers unavailable ({e}); skipping diversity rank", file=sys.stderr)
        return None
    try:
        model_id = os.environ.get("CLIP_MMR_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
        model = SentenceTransformer(model_id, device="cpu")
        embs = model.encode(list(texts), convert_to_numpy=False, normalize_embeddings=True)
        return [list(e) for e in embs]
    except Exception as e:
        print(f"[MMR] embedding failed ({e}); skipping diversity rank", file=sys.stderr)
        return None


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    num = sum(x * y for x, y in zip(a, b))
    da = math.sqrt(sum(x * x for x in a))
    db = math.sqrt(sum(y * y for y in b))
    if da == 0 or db == 0:
        return 0.0
    return num / (da * db)


def mmr_rank(
    moments: List[Dict[str, Any]],
    *,
    lam: float = DEFAULT_LAMBDA,
) -> List[Dict[str, Any]]:
    """Return the moments re-ordered by MMR. Each moment gains an mmr_rank field."""
    if len(moments) <= 1:
        for i, m in enumerate(moments):
            m["mmr_rank"] = i
        return list(moments)

    texts = [_moment_text(m) for m in moments]
    embs = _try_embed(texts)
    if embs is None:
        # Pure score-greedy fallback — no diversity penalty.
        ordered = sorted(moments, key=_moment_score, reverse=True)
        for i, m in enumerate(ordered):
            m["mmr_rank"] = i
        return ordered

    n = len(moments)
    scores = [_moment_score(m) for m in moments]
    selected: List[int] = []
    remaining = set(range(n))

    while remaining:
        best_i = -1
        best_v = float("-inf")
        for i in remaining:
            max_sim = 0.0
            for j in selected:
                sim = _cosine(embs[i], embs[j])
                if sim > max_sim:
                    max_sim = sim
            mmr = lam * scores[i] - (1.0 - lam) * max_sim
            if mmr > best_v:
                best_v = mmr
                best_i = i
        if best_i < 0:
            break
        selected.append(best_i)
        remaining.discard(best_i)

    ordered = [moments[i] for i in selected]
    for rank, m in enumerate(ordered):
        m["mmr_rank"] = rank
    print(f"[MMR] Re-ordered {len(ordered)} moments (lambda={lam})", file=sys.stderr)
    return ordered


def _load_style_pattern_weights() -> Dict[str, Any]:
    for path in ("/root/.openclaw/style_pattern_weights.json", "/root/scripts/lib/../../config/style_pattern_weights.json"):
        try:
            with open(path) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
    return {}


def apply_style_weights(moments: List[Dict[str, Any]], style: str) -> int:
    """Phase 4.7 — multiply each moment's score by the style preset's
    boost/demote for its primary_pattern (or pattern_confirmed). Returns
    count of moments adjusted. Safe no-op when style is unknown."""
    if not style or not moments:
        return 0
    cfg = _load_style_pattern_weights()
    styles = cfg.get("styles") or {}
    preset = styles.get(style)
    if not preset:
        return 0
    boost = preset.get("boost") or {}
    demote = preset.get("demote") or {}
    if not boost and not demote:
        return 0
    adjusted = 0
    for m in moments:
        pid = m.get("pattern_confirmed") or m.get("primary_pattern") or ""
        if not pid:
            continue
        mult = None
        if pid in boost:
            mult = float(boost[pid])
        elif pid in demote:
            mult = float(demote[pid])
        if mult is None or mult == 1.0:
            continue
        old_score = float(m.get("score", 0) or 0)
        new_score = max(0.0, min(old_score * mult, 1.0))
        m["score"] = round(new_score, 3)
        m["style_pattern_mult"] = round(mult, 3)
        # raw_score parallel — preserves over-1.0 magnitudes for downstream sort.
        old_raw = float(m.get("raw_score", old_score) or 0)
        m["raw_score"] = round(old_raw * mult, 4)
        adjusted += 1
    if adjusted:
        print(f"[STYLE] Applied '{style}' pattern weights to {adjusted} moments", file=sys.stderr)
    return adjusted


def main(argv: Sequence[str]) -> int:
    moments_path = argv[1] if len(argv) > 1 else f"{TEMP_DIR}/hype_moments.json"
    try:
        with open(moments_path) as f:
            moments = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"[MMR] couldn't load {moments_path}: {e}", file=sys.stderr)
        return 0

    if not isinstance(moments, list) or len(moments) <= 1:
        # Nothing to diversify.
        return 0

    # Phase 4.7 style → Pattern Catalog weighting fires before MMR so the
    # diversity ranker sees the post-style scores.
    apply_style_weights(moments, os.environ.get("CLIP_STYLE", "").strip())

    lam = _load_lambda()
    ordered = mmr_rank(moments, lam=lam)

    with open(moments_path, "w") as f:
        json.dump(ordered, f)
    print(f"[MMR] wrote {len(ordered)} moments back to {moments_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
