#!/usr/bin/env python3
"""Near-miss review — the practical MISS-CLASS label source (Activation Wave Phase 0.1).

The pipeline renders ~the top 10 of ~250 scored candidates. Path B (rate_run) only
reaches those rendered clips; Path C (viewer-clip alignment) is mostly inert for this
owner. So the *dropped-but-good* moments — the miss class the ranker most needs to learn
from — are never labelled. This tool surfaces the REJECTED candidates just below the cut
(rank ~11-30) from a run's own trace, lets the owner flag the keepers, and files them as
positive labels through the SAME path rate_run uses (a ratings_*.jsonl that
`rate_run collect` + `merge_labels` pick up automatically). Zero pipeline coupling.

The one proven miss we have — the [[case-rap-battle-missed]] Mockingbird moment, which a
real viewer clipped and the pipeline scored high but did not select — sat at rank 24/257,
squarely in this window. This surfaces that band on EVERY run, no luck required.

Commands:
  list  --run <stem> [--lo 11 --hi 30] [--cut] [--vod PATH]
        show rejected candidates ranked lo..hi with a stable index; --cut also extracts
        a short preview .mp4 per candidate (bounded) from the VOD for eyeballing.
  keep  --run <stem> --idx 1,4,5      file label=1 (a wrongly-dropped keeper) on those
  drop  --run <stem> --idx 2,3        file label=0 (viewed and correctly rejected)

<stem> is a trace stem (last_run_20260705_010127) resolved from clips/.diagnostics/ OR
learning/frozen_runs/. keep/drop write clips/.diagnostics/ratings_<stem>_nearmiss.jsonl
in the rate_run schema, so `python scripts/research/rate_run.py collect` then
`python scripts/research/merge_labels.py` fold them into the durable store like any label.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
REPO = HERE.parents[2]
DIAG = REPO / "clips" / ".diagnostics"
FROZEN = REPO / "learning" / "frozen_runs"


def _load_trace(stem: str) -> dict | None:
    """Resolve a run's candidate set from the diagnostics trace OR the frozen store."""
    p = DIAG / f"{stem}.json"
    if p.exists():
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            return d.get("pass_c_candidates") if isinstance(d.get("pass_c_candidates"), dict) else d
        except Exception:
            pass
    fp = FROZEN / f"{stem}.json"
    if fp.exists():
        try:
            return json.loads(fp.read_text(encoding="utf-8"))  # {run, vod, candidates, labels}
        except Exception:
            pass
    return None


def _rank_key(c: dict) -> float:
    # Prefer the deployment-faithful pre_bucket_score (post-position, pre-bucket-norm)
    # when present; fall back to final_score for older traces.
    v = c.get("pre_bucket_score")
    if v is None:
        v = c.get("final_score")
    return float(v or 0.0)


def _rejected_window(trace: dict, lo: int, hi: int) -> list[dict]:
    """Candidates at OVERALL rank [lo, hi] (all candidates ranked by score desc), kept
    only if REJECTED. Overall rank matches how misses are cited (Mockingbird = 24/257):
    selected clips occupy the top ranks and are handled by rate_run, so filtering them
    out of this band leaves exactly the just-missed rejects."""
    cands = trace.get("candidates") or []
    ranked = sorted(cands, key=_rank_key, reverse=True)
    lo = max(1, lo)
    return [c for c in ranked[lo - 1:hi] if not c.get("selected")]


def _preview(c: dict) -> str:
    return (c.get("why") or c.get("preview") or "").strip().replace("\n", " ")[:70]


def _ratings_path(stem: str) -> Path:
    return DIAG / f"ratings_{stem}_nearmiss.jsonl"


def cmd_list(a) -> int:
    trace = _load_trace(a.run)
    if not trace:
        print(f"[near_miss] no trace for {a.run} in {DIAG} or {FROZEN}")
        return 1
    win = _rejected_window(trace, a.lo, a.hi)
    if not win:
        print(f"[near_miss] no rejected candidates in rank {a.lo}-{a.hi} for {a.run}")
        return 0
    has_pbs = any("pre_bucket_score" in c for c in (trace.get("candidates") or []))
    print(f"[near_miss] {a.run}: rejected candidates rank {a.lo}-{a.hi} "
          f"(key={'pre_bucket_score' if has_pbs else 'final_score'}). "
          f"idx = flag with `keep --idx` / `drop --idx`:")
    for i, c in enumerate(win, 1):
        print(f"  [{i:>2}] t={c.get('timestamp')!s:>7}  score={_rank_key(c):.3f}  "
              f"[{c.get('primary_category','?')}]  {_preview(c)}")
    if a.cut:
        _cut_previews(a, win, trace)
    return 0


def _cut_previews(a, win: list[dict], trace: dict) -> None:
    vod = a.vod or trace.get("vod") or ""
    vpath = Path(vod)
    if not vod or not vpath.exists():
        print(f"[near_miss] --cut: VOD not found (vod='{vod}'); pass --vod PATH. Skipping snippets.")
        return
    outdir = DIAG / f"nearmiss_{a.run}"
    outdir.mkdir(parents=True, exist_ok=True)
    n = min(len(win), 20)  # bounded — never spawn an unbounded batch of ffmpeg jobs
    print(f"[near_miss] cutting {n} preview snippet(s) -> {outdir} (~12s each, bounded)")
    for i, c in enumerate(win[:n], 1):
        t = float(c.get("timestamp", 0) or 0)
        ss = max(0.0, t - 4.0)
        out = outdir / f"nm_{i:02d}_t{int(t)}.mp4"
        cmd = ["ffmpeg", "-y", "-ss", f"{ss:.2f}", "-t", "12", "-i", str(vpath),
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
               "-c:a", "aac", "-ac", "1", str(out)]
        try:
            r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90)
            print(f"  [{i:>2}] {'ok ' if r.returncode == 0 else 'ERR'} {out.name}")
        except (subprocess.TimeoutExpired, OSError) as e:
            print(f"  [{i:>2}] snippet failed ({type(e).__name__})")
    print(f"[near_miss] review the snippets, then: keep --run {a.run} --idx <n,n> "
          f"(delete {outdir} after — labels point at the trace, not the files).")


def _parse_idx(s: str) -> list[int]:
    out = []
    for part in s.split(","):
        part = part.strip()
        if part.isdigit():
            out.append(int(part))
    return out


def _file_labels(a, label: int) -> int:
    trace = _load_trace(a.run)
    if not trace:
        print(f"[near_miss] no trace for {a.run}")
        return 1
    win = _rejected_window(trace, a.lo, a.hi)
    idxs = _parse_idx(a.idx)
    if not idxs:
        print(f"[near_miss] no valid --idx given (got {a.idx!r})")
        return 1
    # merge with any existing near-miss rows for this run (idempotent on timestamp)
    rp = _ratings_path(a.run)
    existing = []
    if rp.exists():
        existing = [json.loads(l) for l in rp.read_text(encoding="utf-8").splitlines() if l.strip()]
    by_ts = {round(float(r["timestamp"]), 1): r for r in existing}
    filed = 0
    for i in idxs:
        if not (1 <= i <= len(win)):
            print(f"[near_miss] idx {i} out of range 1-{len(win)}; skipped")
            continue
        c = win[i - 1]
        ts = round(float(c.get("timestamp", 0) or 0), 1)
        by_ts[ts] = {"run": a.run, "timestamp": ts, "label": int(label),
                     "clip": f"[near-miss] {_preview(c)}", "source": "owner"}
        filed += 1
        print(f"[near_miss] label={label} -> t={ts} {_preview(c)}")
    rows = list(by_ts.values())
    rp.write_text("\n".join(json.dumps(r) for r in rows) + ("\n" if rows else ""), encoding="utf-8")
    print(f"[near_miss] wrote {filed} label(s) -> {rp.name} ({len(rows)} total). "
          f"Next: `python scripts/research/rate_run.py collect` then `merge_labels.py`.")
    return 0


def cmd_keep(a) -> int:
    return _file_labels(a, 1)


def cmd_drop(a) -> int:
    return _file_labels(a, 0)


def _self_test() -> int:
    """Synthetic trace: assert the rank window selects rejected candidates in [lo,hi] and
    that keep/drop map idx -> the right candidate's timestamp."""
    cands = []
    for i in range(40):
        cands.append({"timestamp": i * 30, "selected": i < 10,
                      "pre_bucket_score": 1.0 - i * 0.01,
                      "primary_category": "funny", "why": f"cand {i}"})
    trace = {"candidates": cands}
    win = _rejected_window(trace, 11, 30)
    # OVERALL rank: score desc == i asc; ranks 1-10 (i=0..9) are selected, so overall
    # rank 11-30 == i=10..29, all rejected. Head t=300 (i=10), tail t=870 (i=29), 20 wide.
    assert win and win[0]["timestamp"] == 300, f"window head wrong: {win[0] if win else None}"
    assert len(win) == 20, f"window size {len(win)} != 20"
    assert win[-1]["timestamp"] == 29 * 30, "window tail wrong"
    # selected clips must never appear in the window
    assert all(not c.get("selected") for c in win), "selected leaked into window"
    # idx mapping: keep --idx 1 targets the head candidate's timestamp
    assert win[0]["timestamp"] == 300
    print("[near_miss] self-test PASS (overall-rank window, selected excluded, idx maps)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Near-miss review: surface + label rank ~11-30 rejects")
    ap.add_argument("--self-test", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    for name, fn in (("list", cmd_list), ("keep", cmd_keep), ("drop", cmd_drop)):
        sp = sub.add_parser(name)
        sp.add_argument("--run", required=True)
        sp.add_argument("--lo", type=int, default=11)
        sp.add_argument("--hi", type=int, default=30)
        if name == "list":
            sp.add_argument("--cut", action="store_true", help="extract preview snippets from the VOD")
            sp.add_argument("--vod", default="", help="VOD path (else the trace's vod stamp)")
        else:
            sp.add_argument("--idx", required=True, help="comma-separated indices from `list`")
        sp.set_defaults(fn=fn)
    a = ap.parse_args()
    if a.self_test:
        return _self_test()
    if not getattr(a, "cmd", None):
        ap.print_help()
        return 2
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
