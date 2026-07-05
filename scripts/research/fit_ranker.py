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
    """Flatten Pass-C candidate traces into feature rows tagged with their run.

    Accepts BOTH shapes:
      * a raw `pass_c_candidates.json` (work-dir file, if preserved), and
      * a `clips/.diagnostics/last_run_*.json` run snapshot — the pipeline's
        cleanup embeds the full trace under its "pass_c_candidates" key, so
        EVERY completed run automatically banks a training-ready trace with no
        extra plumbing. Point --traces at clips/.diagnostics to use them all."""
    rows = []
    for p in paths:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[fit_ranker] skip {p} ({type(e).__name__})")
            continue
        if "candidates" not in data and isinstance(data.get("pass_c_candidates"), dict):
            data = data["pass_c_candidates"]      # last_run_*.json snapshot shape
        cands = data.get("candidates") or []
        if not cands:
            continue
        # Skip pre-B1 traces: without the stamped factors (style_multiplier etc.) the
        # ranker would default them to 1.0 -> a systematically different feature-space
        # region that contaminates the fit. Only B1-enriched runs are trainable.
        if "style_multiplier" not in cands[0]:
            print(f"[fit_ranker] skip {p.name}: pre-B1 trace (no stamped factors)")
            continue
        run = p.stem  # e.g. last_run_20260705_010127 — matches labels' "run" key
        for c in cands:
            c = dict(c)
            c["_run"] = run
            rows.append(c)
        print(f"[fit_ranker] {p.name}: {len(cands)} candidates")
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
        in_win = [r for r in cands if abs(float(r.get("timestamp", 0)) - t) <= tol]
        if not in_win:
            continue
        # POSITIVE labels snap to the highest-scoring candidate in the window: a
        # viewer-clipped region's clip-worthy peak is what the label means, not
        # whichever adjacent line happens to be nearest in time (Path-C alignment
        # localizes to the ~50 s moment, not the exact payoff frame). This also
        # correctly targets the "scored high, dropped anyway" miss class. Negatives
        # snap to nearest (a specific rejected moment). Ties broken by nearness.
        if y == 1:
            best = max(in_win, key=lambda r: (_num(r.get("final_score")),
                                              -abs(float(r.get("timestamp", 0)) - t)))
        else:
            best = min(in_win, key=lambda r: abs(float(r.get("timestamp", 0)) - t))
        best["_label"] = y
        best["_explicit"] = True
        matched += 1
    # SELECTED-but-unrated candidates are UNKNOWN, not negatives: the owner reviews
    # only some produced clips ("didn't watch all"), and selected clips skew good.
    # Marking them 0 would teach the fit that its own selections are bad. Excluded
    # from training (label None). REJECTED unlabeled candidates stay implicit 0 —
    # highlight sparsity makes that a sound prior.
    excluded = 0
    for r in rows:
        if r.get("selected") and not r.get("_explicit"):
            r["_label"] = None
            excluded += 1
    print(f"[fit_ranker] matched {matched}/{len(labels)} labels to candidates "
          f"({sum(1 for r in rows if r.get('_label') == 1)} positives / {len(rows)} rows; "
          f"{excluded} selected-but-unrated excluded)")
    return rows


# ------------------------------------------------------------------- logistic (pure)
def _standardize(X: list[list[float]]):
    n, d = len(X), len(X[0])
    mean = [sum(row[j] for row in X) / n for j in range(d)]
    var = [sum((row[j] - mean[j]) ** 2 for row in X) / max(1, n) for j in range(d)]
    std = [math.sqrt(v) or 1.0 for v in var]
    Z = [[(row[j] - mean[j]) / std[j] for j in range(d)] for row in X]
    return Z, mean, std


def _fit_logistic(X, y, l2=1.0, lr=0.3, epochs=400, prior_raw=None):
    """Pure-Python L2-regularised logistic regression via gradient descent on
    standardized features. Returns (weights_std, bias) in standardized space.

    `prior_raw` (optional, raw-feature space): the regulariser shrinks each weight
    toward its prior instead of toward zero. This is the GENERALIZATION anchor
    (owner directive 2026-07-05): with the hand-tuned composite feature given a
    prior of 1.0, weak/noisy/niche-narrow labels leave the model ≈ the generalized
    hand-tuned ranking; only consistent evidence moves it away."""
    Z, mean, std = _standardize(X)
    n, d = len(Z), len(Z[0])
    # raw-space prior -> standardized space (w_raw = w_std / std  =>  w_std = w_raw * std)
    prior = [(prior_raw[j] if prior_raw else 0.0) * std[j] for j in range(d)]
    w = list(prior)          # start AT the prior — no evidence => stay there
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
            # data step, then PROXIMAL shrink toward the prior — standard convention
            # (λ NOT divided by n, so l2 means the same thing at any dataset size)
            # and unconditionally stable for any l2 (no lr*l2 divergence).
            w[j] = w[j] - lr * gw[j] / n
            w[j] = prior[j] + (w[j] - prior[j]) / (1.0 + lr * l2)
        b -= lr * gb / n
    return w, b, mean, std


def _destandardize(w_std, b_std, mean, std):
    """Map standardized weights back to raw-feature space: the raw-space linear score
    equals the standardized one, so ranker.score (which uses raw features) matches."""
    w_raw = [w_std[j] / std[j] for j in range(len(w_std))]
    b_raw = b_std - sum(w_std[j] * mean[j] / std[j] for j in range(len(w_std)))
    return w_raw, b_raw


def fit(rows: list[dict], l2: float = 1.0, anchor: bool = True) -> dict:
    """Fit the ranker. anchor=True (default) appends a COMPOSITE feature = the
    hand-tuned log-score (sum of the identity log-factors) with a prior weight of
    1.0, and shrinks every other weight toward 0. Generalization guarantee: with
    weak/noisy/niche-narrow labels the fit stays ≈ sigmoid(hand-tuned score) — the
    generalized baseline ranking — and deviates only on consistent evidence. The
    composite folds back into the per-feature weights afterwards (composite = sum
    of identity features, so adding its weight to each is exactly equivalent),
    keeping the output schema identical for ranker.py."""
    rows = [r for r in rows if r.get("_label") is not None]   # unknowns excluded
    order = list(ranker.FEATURE_ORDER)
    X = [ranker.feature_vector(r, order) for r in rows]
    y = [int(r.get("_label", 0)) for r in rows]
    if sum(y) == 0 or sum(y) == len(y):
        raise SystemExit("[fit_ranker] need both positive and negative labels to fit.")
    n_id = len(ranker.IDENTITY_FACTORS)
    prior = None
    if anchor:
        for xi in X:
            xi.append(sum(xi[:n_id]))            # composite hand-tuned log-score
        prior = [0.0] * len(order) + [1.0]       # anchor: composite prior = 1.0
    w_std, b_std, mean, std = _fit_logistic(X, y, l2=l2, prior_raw=prior)
    w_raw, b_raw = _destandardize(w_std, b_std, mean, std)
    comp_w = None
    if anchor:
        comp_w = w_raw.pop()                     # fold composite back into identities
        for j in range(n_id):
            w_raw[j] += comp_w
    weights = {order[j]: round(w_raw[j], 5) for j in range(len(order))}
    return {"weights": weights, "bias": round(b_raw, 5),
            "meta": {"feature_order": order, "n_rows": len(rows),
                     "n_positive": sum(y), "l2": l2, "anchored": bool(anchor),
                     "anchor_composite_weight": round(comp_w, 5) if comp_w is not None else None,
                     "note": "Fitted by scripts/research/fit_ranker.py (identity-anchored: weak "
                             "evidence keeps the generalized hand-tuned ranking). Loaded "
                             "failure-soft by scripts/lib/ranker.py; delete this file to revert."}}


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

    # GENERALIZATION anchor check — the MECHANISM: as regularization grows, an
    # anchored fit on uninformative labels must converge to the hand-tuned ranking.
    # (In-sample, a flexible fit can always mine spurious correlations from finite
    # noise, so the invariant is monotone convergence with l2, and ≈baseline at
    # strong l2 — the level the L3 plan prescribes for small label sets.)
    import itertools
    noise_rows = []
    for i in range(300):
        m = {"normalized_score": rnd(i + 11, 0.2, 0.9),
             "style_multiplier": rnd(i, 1.0, 1.3),
             "cross_val_factor": 1.2 if i % 3 == 0 else 1.0,
             "speaker_factor": 1.0, "pattern_bonus": 1.0,
             "axis_multiplier": rnd(i + 5, 0.85, 1.3),
             "length_penalty": rnd(i + 9, 0.8, 1.05),
             "reaction_score": rnd(i + 2, 0, 1), "keyword_score": rnd(i + 4, 0, 1),
             # labels from a DIFFERENT hash family than the features
             "_label": 1 if (math.sin(i * 77.777) * 43758.5453) % 1.0 > 0.5 else 0}
        m["final_score"] = round(m["normalized_score"] * m["style_multiplier"] *
                                 m["cross_val_factor"] * m["axis_multiplier"] *
                                 m["length_penalty"], 4)
        noise_rows.append(m)
    hand = [r["final_score"] for r in noise_rows]
    pairs = list(itertools.combinations(range(0, 300, 7), 2))

    def _conc(model):
        f = [ranker.score(r, model["weights"], model["bias"]) for r in noise_rows]
        return sum(1 for a, b in pairs
                   if (f[a] - f[b]) * (hand[a] - hand[b]) > 0) / len(pairs)
    c_weak, c_strong = _conc(fit(noise_rows, l2=0.5)), _conc(fit(noise_rows, l2=25.0))
    print(f"[self-test] anchor convergence: concordance l2=0.5 -> {c_weak:.3f}, "
          f"l2=25 -> {c_strong:.3f} (expect strong > weak and strong > 0.9)")
    conc_ok = c_strong > 0.9 and c_strong >= c_weak
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
    ok = w_ix > 0.5 and hi > lo and conc_ok
    print(f"[self-test] reaction-carried rescored above word-carried: {hi:.3f} > {lo:.3f} = {hi > lo}")
    print("[self-test]", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def _recall_at_n(held: list[dict], key, n: int) -> float | None:
    """Fraction of the held run's positives that land in the top-n by `key`."""
    pos = [r for r in held if r.get("_label") == 1]
    if not pos:
        return None
    top = set(id(r) for r in sorted(held, key=key, reverse=True)[:n])
    return sum(1 for r in pos if id(r) in top) / len(pos)


def _mean_pos_rank(held: list[dict], key) -> float | None:
    """Mean rank (1 = highest) of the held run's positives under `key`."""
    pos = [r for r in held if r.get("_label") == 1]
    if not pos:
        return None
    order = sorted(held, key=key, reverse=True)
    rank = {id(r): i + 1 for i, r in enumerate(order)}
    return round(sum(rank[id(r)] for r in pos) / len(pos), 1)


def run_gate(rows: list[dict], l2: float, n: int) -> dict:
    """L3 GATE — leave-one-run-out holdout. For each run that has >=1 positive: fit on
    the OTHER runs, then on the held run compare recall@n of the FITTED ranking vs the
    hand-tuned baseline. Verdict ENABLE only if fitted >= baseline on average AND
    strictly beats it somewhere (never worse). This is the generalization check the
    identity anchor is built to survive: a niche/thin fit converges to baseline -> a
    tie -> not enabled (correctly, nothing to gain)."""
    by_run: dict[str, list[dict]] = {}
    for r in rows:
        by_run.setdefault(r.get("_run", ""), []).append(r)
    holdable = [run for run, rs in by_run.items() if any(x.get("_label") == 1 for x in rs)]
    per = []
    if len(by_run) < 2 or not holdable:
        return {"verdict": "UNDECIDED", "reason": "need >=2 runs and a positive outside the held run",
                "n_runs": len(by_run), "holdable": len(holdable), "per_run": per}
    for held in holdable:
        train = [r for run, rs in by_run.items() if run != held for r in rs]
        if not (any(r.get("_label") == 1 for r in train) and any(r.get("_label") == 0 for r in train)):
            continue  # can't train without both classes
        m = fit(train, l2=l2)
        heldrows = by_run[held]
        rf = _recall_at_n(heldrows, lambda r: ranker.score(r, m["weights"], m["bias"]), n)
        rb = _recall_at_n(heldrows, lambda r: _num(r.get("final_score")), n)
        # DIRECTION diagnostic: mean rank of the held positives (1 = top), fitted vs
        # baseline. recall@n can tie at 0 when the positive is a known miss (rank > n),
        # yet the fit may still move it up — this shows that.
        pf = _mean_pos_rank(heldrows, lambda r: ranker.score(r, m["weights"], m["bias"]))
        pb = _mean_pos_rank(heldrows, lambda r: _num(r.get("final_score")))
        per.append({"held": held, "recall_fitted": round(rf, 3) if rf is not None else None,
                    "recall_baseline": round(rb, 3) if rb is not None else None,
                    "pos_rank_fitted": pf, "pos_rank_baseline": pb, "n_cands": len(heldrows)})
    scored = [p for p in per if p["recall_fitted"] is not None and p["recall_baseline"] is not None]
    if not scored:
        return {"verdict": "UNDECIDED", "reason": "no run had both a held positive and a trainable fit",
                "per_run": per}
    mf = sum(p["recall_fitted"] for p in scored) / len(scored)
    mb = sum(p["recall_baseline"] for p in scored) / len(scored)
    better = any(p["recall_fitted"] > p["recall_baseline"] for p in scored)
    worse = any(p["recall_fitted"] < p["recall_baseline"] for p in scored)
    verdict = "ENABLE" if (mf >= mb and better and not worse) else \
              "HOLD" if mf >= mb else "REJECT"
    return {"verdict": verdict, "recall_at_n": n, "mean_fitted": round(mf, 3),
            "mean_baseline": round(mb, 3), "per_run": scored}


def _num(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def main() -> int:
    args = sys.argv[1:]
    if "--self-test" in args:
        return self_test()
    if "--traces" not in args or "--labels" not in args:
        print("usage: fit_ranker.py --traces <glob-or-dir> --labels <labels.jsonl> "
              "[--l2 1.0] [--tol 10] [--gate [--gate-n 10]]\n"
              "       fit_ranker.py --self-test")
        return 2
    tp = args[args.index("--traces") + 1]
    trace_paths = ([Path(p) for p in Path().glob(tp)] if any(c in tp for c in "*?[")
                   else (list(Path(tp).rglob("pass_c_candidates.json"))
                         + sorted(Path(tp).glob("last_run_*.json"))) if Path(tp).is_dir()
                   else [Path(tp)])
    labels = [json.loads(l) for l in Path(args[args.index("--labels") + 1]).read_text(
        encoding="utf-8").splitlines() if l.strip()]
    l2 = float(args[args.index("--l2") + 1]) if "--l2" in args else 1.0
    tol = float(args[args.index("--tol") + 1]) if "--tol" in args else 2.0
    rows = attach_labels(load_traces(trace_paths), labels, tol=tol)
    if not rows:
        print("[fit_ranker] no candidate rows found."); return 1

    if "--gate" in args:
        n = int(args[args.index("--gate-n") + 1]) if "--gate-n" in args else 10
        g = run_gate(rows, l2=l2, n=n)
        print(f"\n[GATE] verdict={g['verdict']}")
        for k in ("reason", "mean_fitted", "mean_baseline", "recall_at_n"):
            if k in g:
                print(f"       {k}: {g[k]}")
        for p in g.get("per_run", []):
            print(f"       held {p['held']}: recall@{g.get('recall_at_n','?')} "
                  f"fitted={p['recall_fitted']} baseline={p['recall_baseline']} | "
                  f"pos mean-rank fitted={p.get('pos_rank_fitted')} baseline={p.get('pos_rank_baseline')} "
                  f"of {p.get('n_cands')}")
        print("[GATE] ENABLE => safe to write config/selection_ranker.json; "
              "HOLD/REJECT/UNDECIDED => stay on hand-tuned scores.")
        if g["verdict"] != "ENABLE":
            return 0
        print("[GATE] passed — fitting final model on ALL runs for deployment...")

    _write(fit(rows, l2=l2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
