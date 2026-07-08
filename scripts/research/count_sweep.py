#!/usr/bin/env python3
"""Offline tau sweep for Plan A adaptive clip count.

Validates the CLIP_COUNT_TAU tail-floor (concepts/plan-adaptive-clip-count-2026-07)
against the durable frozen runs BEFORE it is ever enabled on a real render.

For each run in learning/frozen_runs/, simulate the tail-floor trim across a tau grid
on that run's SELECTED clips, judging on `pre_bucket_score` (falls back to final_score
for runs frozen before that stamp existed). Two things are reported per tau:
  * how many selected clips would be trimmed (and how many were owner-BAD -> a WIN),
  * the LABEL CONSTRAINT: trimming a GOOD-labeled (label==1) selected clip is UNSAFE.

The recommendation is the LARGEST tau that trims some tail on at least one run while
NEVER trimming a GOOD clip on any run (mirrors the in-pipeline rule: min-keep 3,
arc-category exempt). Read-only; touches nothing.

Usage:
  python scripts/research/count_sweep.py
  python scripts/research/count_sweep.py --tau-min 0.88 --tau-max 0.99 --step 0.01
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FROZEN = REPO / "learning" / "frozen_runs"
MIN_KEEP = 3


def _key(c: dict) -> float:
    v = c.get("pre_bucket_score")
    if v is None:
        v = c.get("final_score")
    return float(v or 0.0)


def _median(vals: list[float]) -> float:
    s = sorted(vals)
    n = len(s)
    if not n:
        return 0.0
    return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])


def _label_map(run: dict) -> dict[int, int]:
    """round(timestamp) -> label for this run's labels."""
    out: dict[int, int] = {}
    for lb in run.get("labels", []) or []:
        try:
            out[round(float(lb["timestamp"]))] = int(lb["label"])
        except (KeyError, TypeError, ValueError):
            pass
    return out


def _label_for(c: dict, lmap: dict[int, int], tol: int = 10) -> int | None:
    ts = c.get("timestamp")
    if ts is None:
        return None
    ts = round(float(ts))
    for d in range(tol + 1):
        for cand in (ts - d, ts + d):
            if cand in lmap:
                return lmap[cand]
    return None


def simulate(selected: list[dict], tau: float) -> tuple[list[dict], list[dict], float]:
    """Return (kept, trimmed, floor) applying the in-pipeline tail-floor rule."""
    ranked = sorted(selected, key=_key, reverse=True)
    floor = tau * _median([_key(c) for c in ranked])
    keep, trimmed = [], []
    for c in ranked:
        if len(keep) < MIN_KEEP or _key(c) >= floor or c.get("primary_category") == "arc":
            keep.append(c)
        else:
            trimmed.append(c)
    return keep, trimmed, floor


def _self_test() -> int:
    """Assert the tail-floor invariants that mirror the in-pipeline rule."""
    def mk(scores, cats=None):
        cats = cats or ["hype"] * len(scores)
        return [{"timestamp": i * 60, "pre_bucket_score": s, "primary_category": c}
                for i, (s, c) in enumerate(zip(scores, cats))]

    # 1. Flat curve at tau=0.94 -> nothing trimmed (no separable tail).
    flat = mk([1.00, 0.99, 0.99, 0.98, 0.98, 0.97])
    _, trimmed, _ = simulate(flat, 0.94)
    assert not trimmed, f"flat curve should not trim, got {len(trimmed)}"

    # 2. Cliff -> the low tail is trimmed.
    cliff = mk([1.60, 1.59, 1.58, 1.57, 0.90, 0.85, 0.80])
    keep, trimmed, _ = simulate(cliff, 0.94)
    assert len(trimmed) == 3, f"cliff should trim the 3 low ones, got {len(trimmed)}"

    # 3. min-keep 3: even if almost everything is below the floor, keep >= 3.
    crash = mk([2.0, 0.1, 0.1, 0.1, 0.1])
    keep, trimmed, _ = simulate(crash, 0.94)
    assert len(keep) >= MIN_KEEP, f"must keep >= {MIN_KEEP}, kept {len(keep)}"

    # 4. arc category is exempt from trimming even below the floor.
    arc = mk([1.60, 1.59, 1.58, 1.57, 0.50], cats=["hype"] * 4 + ["arc"])
    keep, trimmed, _ = simulate(arc, 0.94)
    assert all(c["primary_category"] != "arc" for c in trimmed), "arc must be exempt"

    print("[count_sweep] self-test PASS (flat=no-trim, cliff=trim, min-keep-3, arc-exempt)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="tau sweep for adaptive clip count (Plan A)")
    ap.add_argument("--tau-min", type=float, default=0.88)
    ap.add_argument("--tau-max", type=float, default=0.99)
    ap.add_argument("--step", type=float, default=0.01)
    ap.add_argument("--self-test", action="store_true", help="assert the tail-floor invariants and exit")
    a = ap.parse_args()
    if a.self_test:
        return _self_test()

    files = sorted(FROZEN.glob("*.json"))
    if not files:
        print(f"[count_sweep] no frozen runs in {FROZEN} — freeze a labeled run first "
              f"(merge_labels auto-freezes).")
        return 0

    taus: list[float] = []
    t = a.tau_min
    while t <= a.tau_max + 1e-9:
        taus.append(round(t, 4))
        t += a.step

    # per-tau global safety: does ANY run trim a GOOD clip at this tau?
    unsafe_at: dict[float, list[str]] = {tau: [] for tau in taus}
    any_trim_at: dict[float, int] = {tau: 0 for tau in taus}

    for fp in files:
        run = json.loads(fp.read_text(encoding="utf-8"))
        cands = run.get("candidates") or []
        selected = [c for c in cands if c.get("selected")]
        has_pbs = any("pre_bucket_score" in c for c in cands)
        lmap = _label_map(run)
        n_good = sum(1 for v in lmap.values() if v == 1)
        n_bad = sum(1 for v in lmap.values() if v == 0)
        print(f"\n=== {fp.stem}  vod={run.get('vod','?') or '?'}")
        print(f"  selected={len(selected)}  labels={len(lmap)} ({n_good} good / {n_bad} bad)  "
              f"score_key={'pre_bucket_score' if has_pbs else 'final_score (pre-stamp fallback)'}")
        if len(selected) <= MIN_KEEP:
            print(f"  (<= {MIN_KEEP} selected — never trims; skipped)")
            continue
        for tau in taus:
            keep, trimmed, floor = simulate(selected, tau)
            if not trimmed:
                continue
            any_trim_at[tau] += len(trimmed)
            good_t = bad_t = unl_t = 0
            for c in trimmed:
                lb = _label_for(c, lmap)
                if lb == 1:
                    good_t += 1
                elif lb == 0:
                    bad_t += 1
                else:
                    unl_t += 1
            flag = ""
            if good_t:
                unsafe_at[tau].append(fp.stem)
                flag = f"  <<< UNSAFE: trims {good_t} GOOD"
            print(f"  tau={tau:.2f} floor={floor:.3f}: trim {len(trimmed)} "
                  f"(bad={bad_t} unlabeled={unl_t} good={good_t}){flag}")

    # recommendation: largest tau that trims something somewhere and is safe everywhere
    safe = [tau for tau in taus if not unsafe_at[tau] and any_trim_at[tau] > 0]
    print("\n" + "=" * 60)
    if safe:
        rec = max(safe)
        print(f"[count_sweep] RECOMMEND tau={rec:.2f} — largest tau that trims a tail "
              f"({any_trim_at[rec]} clip(s) across runs) without EVER trimming a GOOD clip.")
        unsafe_taus = sorted(tau for tau in taus if unsafe_at[tau])
        if unsafe_taus:
            print(f"[count_sweep] unsafe at tau >= {min(unsafe_taus):.2f} "
                  f"(would trim a GOOD-labeled clip on {sorted(set(sum((unsafe_at[t] for t in unsafe_taus), [])))}).")
    else:
        trims_anywhere = [tau for tau in taus if any_trim_at[tau] > 0]
        if not trims_anywhere:
            print("[count_sweep] no tau in range trims anything — score curves are flat on "
                  "these runs (expected; nothing to safely cut yet). Default tau=0.94 is a no-op here.")
        else:
            print("[count_sweep] every tau that trims also trims a GOOD clip — labels too "
                  "sparse / tail too close to keepers. Hold at a conservative tau and gather "
                  "more labels before enabling non-shadow.")
    print("[count_sweep] NOTE: runs frozen before the pre_bucket_score stamp use final_score "
          "(bucket-norm-lifted) as a proxy — re-freeze after a run on this build for the true key.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
