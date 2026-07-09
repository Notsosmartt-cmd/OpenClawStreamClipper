#!/usr/bin/env python3
"""Speed #5 CUT-OVER 2 gate — serial vs moment-parallel A/B (plan-serving-stack-2026-07).

Runs the full pipeline twice on one VOD (transcript+audio-events caches reused):
  arm S: serial (CLIP_PASSB_MOMENT_WORKERS unset)          — also validates the extraction
         refactor live (serial path now routes through _process_moment_response).
  arm P: CLIP_PASSB_MOMENT_WORKERS=<N> (2 default)          — moment calls in flight.

GATES (from the plan; owner-approved):
  speed:   Stage-4 wall(P) <= wall(S) / 1.4            (>=1.4x on the dominant stage)
  quality: selected-clip overlap(S, P) >= 5/10 (+-20s)  (the variance yardstick — two plain
           serial re-runs already only overlap 5/10 at production temp, findings §3-reframe)

Outputs a verdict JSON to clips/.diagnostics/moment_ab_<stamp>.json and prints PROMOTE /
REJECT. Bounded: each arm capped at --arm-timeout (default 7200 s).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PY = REPO / ".venv" / "Scripts" / "python.exe"
DIAG = REPO / "clips" / ".diagnostics"


def newest_diag(after_ts: float) -> Path | None:
    cands = [p for p in DIAG.glob("last_run_*.json") if p.stat().st_mtime >= after_ts]
    return max(cands, key=lambda p: p.stat().st_mtime) if cands else None


def run_arm(vod: str, label: str, extra_env: dict, timeout_s: int) -> dict:
    env = dict(os.environ)
    env["CLIP_REUSE_TRANSCRIPT"] = "1"     # deterministic caches; skip ~35 min whisper
    env.update(extra_env)
    t0 = time.time()
    print(f"[AB] arm {label}: starting run_pipeline --vod {vod} --force "
          f"(env: {extra_env or 'serial'})", flush=True)
    r = subprocess.run(
        [str(PY), str(REPO / "scripts" / "run_pipeline.py"),
         "--style", "auto", "--vod", vod, "--force"],
        cwd=str(REPO), env=env, timeout=timeout_s,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    wall = time.time() - t0
    diag = newest_diag(t0)
    out = {"label": label, "exit": r.returncode, "wall_s": round(wall, 1),
           "diag": str(diag) if diag else None, "stage4_s": None, "clips": []}
    if diag:
        d = json.loads(diag.read_text(encoding="utf-8"))
        for st in d.get("stage_timings", {}).get("stages", []):
            if "Moment Detection" in st.get("stage", "") and "Groups" not in st["stage"]:
                out["stage4_s"] = st["seconds"]
        data = (d.get("scored_moments") or {}).get("data") or []
        out["clips"] = sorted(round(float(m.get("timestamp", 0))) for m in data)
    print(f"[AB] arm {label}: exit={r.returncode} wall={wall:.0f}s "
          f"stage4={out['stage4_s']}s clips={out['clips']}", flush=True)
    return out


def overlap(a: list, b: list, tol: float = 20.0) -> int:
    return sum(1 for x in a if any(abs(x - y) <= tol for y in b))


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--vod", default="20260424_2xRaKai_2756365448.mp4")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--arm-timeout", type=int, default=7200)
    ap.add_argument("--speedup-gate", type=float, default=1.1)  # owner-lowered 1.4->1.1 (2026-07-09)
    ap.add_argument("--overlap-gate", type=int, default=5)      # advisory only (spot-check gate)
    args = ap.parse_args(argv)

    S = run_arm(args.vod, "serial", {}, args.arm_timeout)
    P = run_arm(args.vod, f"parallel-{args.workers}",
                {"CLIP_PASSB_MOMENT_WORKERS": str(args.workers)}, args.arm_timeout)

    verdict = {"vod": args.vod, "serial": S, "parallel": P}
    ok = True
    if S["exit"] != 0 or P["exit"] != 0:
        verdict["verdict"] = "INVALID (non-zero exit)"
        ok = False
    else:
        sp = (S["stage4_s"] / P["stage4_s"]) if (S["stage4_s"] and P["stage4_s"]) else 0
        ov = overlap(S["clips"], P["clips"])
        n = max(len(S["clips"]), 1)
        verdict.update({
            "stage4_speedup": round(sp, 2),
            "clip_overlap": f"{ov}/{n}",
            "speed_gate": f">={args.speedup_gate}x -> {'PASS' if sp >= args.speedup_gate else 'FAIL'}",
            "overlap_gate": f">={args.overlap_gate}/{n} (+-20s) -> "
                            f"{'PASS' if ov >= args.overlap_gate else 'FAIL'}",
        })
        # Speed is the hard gate. Overlap is ADVISORY (2026-07-09 owner decision): clip
        # selection is non-deterministic at temp 0.3 and a different-but-good draw is fine —
        # so overlap can't auto-reject; instead the owner SPOT-CHECKS the parallel clips.
        speed_ok = sp >= args.speedup_gate
        ok = speed_ok
        if speed_ok:
            verdict["verdict"] = (
                f"PROMOTE-PENDING-SPOTCHECK (speed {sp:.2f}x >= {args.speedup_gate}x; "
                f"overlap {ov}/{n} is ADVISORY — owner reviews the parallel clips before "
                f"default-on)")
        else:
            verdict["verdict"] = f"REJECT (speed {sp:.2f}x < {args.speedup_gate}x)"

    stamp = time.strftime("%Y%m%d_%H%M%S")
    out = DIAG / f"moment_ab_{stamp}.json"
    out.write_text(json.dumps(verdict, indent=2), encoding="utf-8")
    print(f"[AB] verdict: {verdict['verdict']}")
    print(f"[AB] full result: {out}")
    print(json.dumps(verdict, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
