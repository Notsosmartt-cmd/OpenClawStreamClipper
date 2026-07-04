#!/usr/bin/env python3
"""Phase 4 (B2+B4) — the fittable, log-space selection ranker.

The Pass C final score is a PRODUCT of hand-tuned factors:

    final_score = normalized_score           # Pass B backbone
                × style_multiplier            # category style weight (weight_map)
                × cross_val_factor            # 1.20 if cross_validated else 1.0
                × speaker_factor              # 1.15 if multi-speaker else 1.0
                × pattern_bonus               # rare-pattern rarity bonus
                × axis_multiplier             # clamp(arc×reaction×baseline×engagement)
                × length_penalty              # tightness

Taking logs turns that product into a SUM, i.e. a linear model whose weights are the
logs of the hand-tuned constants. This module makes those weights FITTABLE without
changing the pipeline's behaviour by default:

    ranker_score = Σ wᵢ · log(factorᵢ)  +  Σ wⱼ · interactionⱼ  +  bias

At the built-in default weights (identity factors weight 1.0, interactions weight 0.0,
bias 0.0) `score(moment)` == log(final_score) EXACTLY — so consulting the ranker with
no fitted file re-ranks IDENTICALLY to today (verified in __main__). A fitted
`config/selection_ranker.json` (produced offline by scripts/research/fit_ranker.py from
labelled runs) swaps in learned weights; the hand-tuned product stays the fallback.

Pure-Python, no numpy/sklearn at inference (the pipeline imports this). The trainer is
separate. Failure-soft everywhere: a missing/broken config or a missing feature never
raises — it degrades to the multiplicative baseline."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path

# Identity factors: their logs SUM to log(final_score). Default weight 1.0 each ->
# consulting the ranker reproduces the hand-tuned ranking exactly.
IDENTITY_FACTORS = (
    "normalized_score", "style_multiplier", "cross_val_factor", "speaker_factor",
    "pattern_bonus", "axis_multiplier", "length_penalty",
)
# Extra features the fitter may weight (default weight 0.0 -> no effect). Decomposed
# axis parts let a fit re-weight the axes independently of the clamped product;
# interactions capture the cross-modal cases the multiplicative chain misses (the
# anomaly-lane thesis: strong reaction + banal words; motion spike + banal words).
EXTRA_FEATURES = (
    "log_arc_multiplier", "log_reaction_multiplier", "log_baseline_multiplier",
    "log_engagement_multiplier",
    "ix_reaction_low_keyword",   # reaction_score × (1 − keyword_score)
    "ix_motion_low_keyword",     # motion_score  × (1 − keyword_score)
    "is_anomaly",                # src == ANOMALY (proposer lane)
    "is_cross_validated",        # raw 0/1 (lets a fit add/subtract beyond the 1.20)
)

# Canonical, stable order so the offline trainer and this inference module agree on
# what each weight means (the fitted config also stores its own order as a guard).
FEATURE_ORDER = tuple("log_" + n for n in IDENTITY_FACTORS) + EXTRA_FEATURES

_EPS = 1e-6
_CACHE: dict | None = None
_CACHE_MTIME: float | None = None


def _config_path() -> Path:
    env = os.environ.get("CLIP_SELECTION_RANKER")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "config" / "selection_ranker.json"


def _log(x) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return 0.0
    return math.log(max(_EPS, v))


def _num(x, default=0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def features(moment: dict) -> dict:
    """Extract the full feature dict for a moment (identity + extra). Missing fields
    degrade to the neutral value (factor 1.0 -> log 0; interaction 0) so a moment that
    lacks a signal simply doesn't get that term."""
    f: dict[str, float] = {}
    for name in IDENTITY_FACTORS:
        # normalized_score defaults to 1.0-neutral only if truly absent; a real 0
        # score stays ~log(eps). Other factors default to 1.0 (no effect).
        default = 1.0
        f["log_" + name] = _log(moment.get(name, default))
    for axis in ("arc_multiplier", "reaction_multiplier", "baseline_multiplier",
                 "engagement_multiplier"):
        f["log_" + axis] = _log(moment.get(axis, 1.0))
    kw = _num(moment.get("keyword_score"), 0.0)
    rx = _num(moment.get("reaction_score"), 0.0)
    mo = _num(moment.get("motion_score"), 0.0)
    f["ix_reaction_low_keyword"] = rx * (1.0 - min(1.0, kw))
    f["ix_motion_low_keyword"] = mo * (1.0 - min(1.0, kw))
    f["is_anomaly"] = 1.0 if str(moment.get("source") or moment.get("src") or "").upper() == "ANOMALY" else 0.0
    f["is_cross_validated"] = 1.0 if moment.get("cross_validated") else 0.0
    return f


def _default_weights() -> dict:
    """Identity weights: reproduce log(final_score) exactly; everything else 0."""
    w = {"log_" + n: 1.0 for n in IDENTITY_FACTORS}
    for name in EXTRA_FEATURES:
        w[name] = 0.0
    return w


def feature_vector(moment: dict, order=FEATURE_ORDER) -> list:
    """Ordered feature list for the offline trainer (fit_ranker.py). Same extraction
    as features(), flattened to FEATURE_ORDER."""
    f = features(moment)
    return [f.get(k, 0.0) for k in order]


def load_weights() -> dict | None:
    """Load fitted weights from config/selection_ranker.json, or None if absent/broken.
    None -> the caller keeps the hand-tuned final_score (current behaviour). Cached on
    mtime so the pipeline re-reads a freshly-fitted file without a restart."""
    global _CACHE, _CACHE_MTIME
    p = _config_path()
    try:
        mtime = p.stat().st_mtime
    except OSError:
        _CACHE, _CACHE_MTIME = None, None
        return None
    if _CACHE is not None and _CACHE_MTIME == mtime:
        return _CACHE
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        weights = data.get("weights") if isinstance(data, dict) else None
        if not isinstance(weights, dict) or not weights:
            raise ValueError("no weights")
        _CACHE = {"weights": weights, "bias": float(data.get("bias", 0.0)),
                  "meta": data.get("meta", {})}
        _CACHE_MTIME = mtime
        return _CACHE
    except (OSError, ValueError, TypeError) as e:
        print(f"[ranker] ignoring unreadable selection_ranker.json ({type(e).__name__}: {e}); "
              "using hand-tuned scores", flush=True)
        _CACHE, _CACHE_MTIME = None, None
        return None


def score(moment: dict, weights: dict | None = None, bias: float = 0.0) -> float:
    """Log-space ranker score. With weights=None uses the identity defaults, so the
    return equals log(final_score) and ranking is unchanged. A fitted `weights` dict
    (partial is fine — unspecified features fall back to their identity/zero default)
    swaps in learned coefficients."""
    w = _default_weights()
    if weights:
        w.update(weights)
    f = features(moment)
    return sum(w.get(k, 0.0) * v for k, v in f.items()) + bias


def rank_key(moment: dict) -> float:
    """The ranking key Pass C should sort on. If a fitted ranker is loaded, use its
    score; otherwise fall back to the hand-tuned final_score (returned as-is so the
    sort is byte-identical to legacy). Boost-free, re-rank only — never gates."""
    cfg = load_weights()
    if cfg is None:
        return _num(moment.get("final_score"), 0.0)
    return score(moment, cfg["weights"], cfg["bias"])


def _sigmoid(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))


def maybe_rescore(moment: dict) -> float | None:
    """Pipeline hook. Returns a BOUNDED (0,1) replacement for `final_score` when a
    fitted config/selection_ranker.json is present, else None (caller keeps the
    hand-tuned final_score → zero behaviour change by default).

    The return is sigmoid(log-space score) = the model's P(highlight). Sigmoid is
    monotonic, so it preserves the fitted RANKING while guaranteeing the value stays
    in (0,1) — the same band the downstream position-weight + within-bucket
    normalization assume, so a pathological fit with huge weights can't produce a
    runaway final_score. (Raw exp() would; a separable fit can push logits large.)
    At the identity defaults the ORDER is unchanged (sigmoid∘log is monotonic in
    final_score), which is the property that matters — the no-op guarantee that
    actually protects production is the `cfg is None` short-circuit above. This fits
    only the per-moment product, not the two bucket-level transforms (v1 boundary).
    Never raises: a bad config already degraded load_weights() to None."""
    cfg = load_weights()
    if cfg is None:
        return None
    try:
        return _sigmoid(score(moment, cfg["weights"], cfg["bias"]))
    except (ValueError, OverflowError):
        return None


if __name__ == "__main__":
    # Self-test: identity defaults must reproduce log(final_score) exactly, and
    # rank_key with no config must equal final_score (byte-identical fallback).
    import random  # noqa
    def _mk(ns, style, xval, spk, pat, arc, rx, bc, eng, lp):
        axis = round(max(0.80, min(1.35, arc * rx * bc * eng)), 3)
        final = ns * style * (1.20 if xval else 1.0) * (1.15 if spk else 1.0) * pat * axis * lp
        return {"normalized_score": ns, "style_multiplier": style,
                "cross_val_factor": 1.20 if xval else 1.0, "cross_validated": xval,
                "speaker_factor": 1.15 if spk else 1.0, "pattern_bonus": pat,
                "arc_multiplier": arc, "reaction_multiplier": rx,
                "baseline_multiplier": bc, "engagement_multiplier": eng,
                "axis_multiplier": axis, "length_penalty": lp,
                "final_score": round(final, 6)}
    cases = [
        _mk(0.80, 1.3, True, False, 1.0, 1.05, 1.10, 1.0, 1.0, 0.95),
        _mk(0.43, 1.0, False, True, 1.25, 0.90, 1.20, 1.15, 1.10, 1.0),
        _mk(0.878, 1.3, True, True, 1.4, 1.05, 1.0, 1.0, 1.0, 1.0),
    ]
    ok = True
    for m in cases:
        s = score(m)                       # identity weights
        recon = math.exp(s)
        exact = abs(recon - m["final_score"]) < 1e-3
        # rank_key with no fitted file must return final_score unchanged
        rk = rank_key(m)
        fallback_ok = abs(rk - m["final_score"]) < 1e-9  # (assumes no config present)
        ok = ok and exact
        print(f"final={m['final_score']:.4f} exp(score)={recon:.4f} exact={exact} "
              f"rank_key={rk:.4f} fallback_ok={fallback_ok}")
    # a fitted weight must actually change the ranking
    biased = score(cases[1], {"ix_reaction_low_keyword": 2.0})
    print(f"reaction×low-keyword up-weight moves score: {biased:.4f} vs {score(cases[1]):.4f}")
    print("SELF-TEST", "PASS" if ok else "FAIL")
