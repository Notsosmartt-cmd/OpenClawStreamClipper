#!/usr/bin/env python3
"""Phase 4 (B3) — fit the selection ranker from labelled runs.

Turns the ~50 hand-tuned Pass C constants from vibes into measured weights. Consumes:
  * cached `pass_c_candidates.json` traces (one per run; each candidate is a feature
    row already carrying the full scoring chain — see stage4_moments B1 enrichment), and
  * a labels file (JSONL: {"run": <run-id>, "timestamp": <s>, "label": 0|1}) marking
    which candidates were real highlights (from bootstrap_twitch_clips triples +
    community-highlight↔VOD alignment — that data-gathering is the remaining manual step).

Fits a logistic model over ranker.FEATURE_ORDER (log-space factors + interactions) →
`config/selection_ranker.json` {weights, bias, meta}. The pipeline's ranker.py loads
that file failure-soft; absent file = today's hand-tuned behaviour.

Self-contained: a small pure-Python standardize + logistic gradient descent (no
sklearn/numpy needed — ~50 weights over a few thousand rows converges in well under a
second). `--self-test` fits synthetic data with a planted signal and asserts recovery +
a clean round-trip through ranker.py, so the machinery is verifiable before any real
labels exist."""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
REPO = HERE.parents[2]
OUT = REPO / "config" / "selection_ranker.json"
sys.path.insert(0, str(REPO / "scripts" / "lib"))
sys.path.insert(0, str(HERE.parent))
import ranker  # noqa: E402  (FEATURE_ORDER, feature_vector)


# ----------------------------------------------------------------------------- data
def load_traces(paths: list[Path]) -> list[dict]:
    """Flatten pass_c_candidates.json files into candidate rows tagged with their run."""
    rows = []
    for p in paths:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[fit_ranker] skip {p} ({type(e).__name__})")
            continue
        run = p.parent.name or p.stem
        for c in data.get("candidates", []):
            c = dict(c)
            c["_run"] = run
            rows.append(c)
    return rows


def attach_labels(rows: list[dict], labels: list[dict], tol: float = 2.0) -> list[dict]:
    """Match each label to the nearest candidate in the same run within tol seconds.
    Unlabelled candidates are treated as negatives (highlight reels are sparse)."""
    for r in rows:
        r.setdefault("_label", 0)
    by_run: dict[str, list[dict]] = {}
    for r in rows:
        by_run.setdefault(r.get("_run", ""), []).append(r)
    matched = 0
    for lab in labels:
        run, t, y = lab.get("run", ""), float(lab.get("timestamp", -1)), int(lab.get("label", 0))
        cands = by_run.get(run) or [r for rs in by_run.values() for r in rs]
        best, bd = None, tol
        for r in cands:
            d = abs(float(r.get("timestamp", 0)) - t)
            if d <= bd:
                best, bd = r, d
        if best is not None:
            best["_label"] = y
            matched += 1
    print(f"[fit_ranker] matched {matched}/{len(labels)} labels to candidates "
          f"({sum(r['_label'] for r in rows)} positives / {len(rows)} rows)")
    return rows


# ------------------------------------------------------------------- logistic (pure)
def _standardize(X: list[list[float]]):
    n, d = len(X), len(X[0])
    mean = [sum(row[j] for row in X) / n for j in range(d)]
    var = [sum((row[j] - mean[j]) ** 2 for row in X) / max(1, n) for j in range(d)]
    std = [math.sqrt(v) or 1.0 for v in var]
    Z = [[(row[j] - mean[j]) / std[j] for j in range(d)] for row in X]
    return Z, mean, std


def _fit_logistic(X, y, l2=1.0, lr=0.3, epochs=400):
    """Pure-Python L2-regularised logistic regression via gradient descent on
    standardized features. Returns (weights_std, bias) in standardized space."""
    Z, mean, std = _standardize(X)
    n, d = len(Z), len(Z[0])
    w = [0.0] * d
    b = 0.0
    for _ in range(epochs):
        gw = [0.0] * d
        gb = 0.0
        for i in range(n):
            z = b + sum(w[j] * Z[i][j] for j in range(d))
            p = 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))
            err = p - y[i]
            for j in range(d):
                gw[j] += err * Z[i][j]
            gb += err
        for j in range(d):
            w[j] = w[j] - lr * (gw[j] / n + l2 * w[j] / n)
        b -= lr * gb / n
    return w, b, mean, std


def _destandardize(w_std, b_std, mean, std):
    """Map standardized weights back to raw-feature space: the raw-space linear score
    equals the standardized one, so ranker.score (which uses raw features) matches."""
    w_raw = [w_std[j] / std[j] for j in range(len(w_std))]
    b_raw = b_std - sum(w_std[j] * mean[j] / std[j] for j in range(len(w_std)))
    return w_raw, b_raw


def fit(rows: list[dict], l2: float = 1.0) -> dict:
    order = list(ranker.FEATURE_ORDER)
    X = [ranker.feature_vector(r, order) for r in rows]
    y = [int(r.get("_label", 0)) for r in rows]
    if sum(y) == 0 or sum(y) == len(y):
        raise SystemExit("[fit_ranker] need both positive and negative labels to fit.")
    w_std, b_std, mean, std = _fit_logistic(X, y, l2=l2)
    w_raw, b_raw = _destandardize(w_std, b_std, mean, std)
    weights = {order[j]: round(w_raw[j], 5) for j in range(len(order))}
    return {"weights": weights, "bias": round(b_raw, 5),
            "meta": {"feature_order": order, "n_rows": len(rows),
                     "n_positive": sum(y), "l2": l2,
                     "note": "Fitted by scripts/research/fit_ranker.py. Loaded failure-soft "
                             "by scripts/lib/ranker.py; delete this file to revert to hand-tuned scores."}}


def _write(model: dict) -> None:
    OUT.write_text(json.dumps(model, indent=2), encoding="utf-8")
    print(f"[fit_ranker] wrote {OUT}\n  bias={model['bias']}  top weights:")
    for k, v in sorted(model["weights"].items(), key=lambda kv: -abs(kv[1]))[:8]:
        print(f"    {k:<28} {v:+.4f}")


# ----------------------------------------------------------------------- self-test
def self_test() -> int:
    """Plant a signal: positives are reaction-carried (high reaction×low-keyword) even
    when their hand-tuned final_score is mediocre — exactly the anomaly-lane clip the
    multiplicative chain under-ranks. A correct fit must give ix_reaction_low_keyword a
    strongly positive weight, and the fitted file must round-trip through ranker.py."""
    import random  # noqa: local, only in self-test
    rows = []
    # deterministic pseudo-random without Date/random seed issues
    def rnd(i, a, b):
        x = math.sin(i * 12.9898) * 43758.5453
        return a + (x - math.floor(x)) * (b - a)
    for i in range(400):
        pos = i % 2 == 0
        rx = rnd(i, 0.55, 0.95) if pos else rnd(i, 0.0, 0.35)
        kw = rnd(i + 7, 0.0, 0.3) if pos else rnd(i + 7, 0.4, 0.9)
        ns = rnd(i + 3, 0.35, 0.6)   # mediocre Pass B score for BOTH -> signal is the interaction
        rows.append({"normalized_score": ns, "style_multiplier": 1.0, "cross_val_factor": 1.0,
                     "speaker_factor": 1.0, "pattern_bonus": 1.0, "axis_multiplier": 1.0,
                     "length_penalty": 1.0, "reaction_score": rx, "keyword_score": kw,
                     "final_score": ns, "_label": 1 if pos else 0})
    model = fit(rows, l2=0.5)
    w_ix = model["weights"]["ix_reaction_low_keyword"]
    print(f"[self-test] learned ix_reaction_low_keyword weight = {w_ix:+.3f} (expect strongly +)")
    # round-trip: the fitted model must load + rescore through ranker.py
    import os
    _tmp = REPO / "config" / "_selftest_ranker.json"
    _tmp.write_text(json.dumps(model), encoding="utf-8")
    os.environ["CLIP_SELECTION_RANKER"] = str(_tmp)
    ranker._CACHE = None; ranker._CACHE_MTIME = None
    hi = ranker.maybe_rescore({"normalized_score": 0.5, "reaction_score": 0.9, "keyword_score": 0.1,
                               "final_score": 0.5})
    lo = ranker.maybe_rescore({"normalized_score": 0.5, "reaction_score": 0.1, "keyword_score": 0.9,
                               "final_score": 0.5})
    _tmp.unlink(missing_ok=True)
    ok = w_ix > 0.5 and hi > lo
    print(f"[self-test] reaction-carried rescored above word-carried: {hi:.3f} > {lo:.3f} = {hi > lo}")
    print("[self-test]", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main() -> int:
    args = sys.argv[1:]
    if "--self-test" in args:
        return self_test()
    if "--traces" not in args or "--labels" not in args:
        print("usage: fit_ranker.py --traces <glob-or-dir> --labels <labels.jsonl> [--l2 1.0]\n"
              "       fit_ranker.py --self-test")
        return 2
    tp = args[args.index("--traces") + 1]
    trace_paths = ([Path(p) for p in Path().glob(tp)] if any(c in tp for c in "*?[")
                   else list(Path(tp).rglob("pass_c_candidates.json")) if Path(tp).is_dir()
                   else [Path(tp)])
    labels = [json.loads(l) for l in Path(args[args.index("--labels") + 1]).read_text(
        encoding="utf-8").splitlines() if l.strip()]
    l2 = float(args[args.index("--l2") + 1]) if "--l2" in args else 1.0
    rows = attach_labels(load_traces(trace_paths), labels)
    if not rows:
        print("[fit_ranker] no candidate rows found."); return 1
    _write(fit(rows, l2=l2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
