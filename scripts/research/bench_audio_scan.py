#!/usr/bin/env python3
"""bench_audio_scan.py — BUG 71c: why is the threaded audio-events scan ~1 core?

Times a fixed window-slice of a REAL WAV through the actual scan paths
(serial loop / _scan_threads / _scan_parallel) so the parallelism question is
answered by measurement, not theory. Each invocation runs ONE config in a fresh
interpreter (so env like KMP_DUPLICATE_LIB_OK is controlled by the caller).

Usage:
  python scripts/research/bench_audio_scan.py --audio X.wav --mode serial  --windows 24
  python scripts/research/bench_audio_scan.py --audio X.wav --mode threads --n 4 --windows 60
  python scripts/research/bench_audio_scan.py --audio X.wav --mode procs   --n 8 --windows 60
Prints one JSON line: mode, n, windows, wall_s, win_per_s, cpu_cores_avg, kmp.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "scripts" / "lib"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", required=True)
    ap.add_argument("--mode", choices=["serial", "threads", "procs"], required=True)
    ap.add_argument("--n", type=int, default=4, help="thread/process count")
    ap.add_argument("--windows", type=int, default=60)
    ap.add_argument("--start", type=int, default=400, help="first window index (skip intro)")
    args = ap.parse_args()

    import audio_events as ae
    librosa, np = ae._try_import_librosa()
    if librosa is None:
        print(json.dumps({"error": "librosa missing"}))
        return 1

    y, sr = ae._load_audio_fast(args.audio, ae.SAMPLE_RATE, librosa, np)
    dur = len(y) / sr
    tasks = ae._build_window_tasks(dur, ae.WINDOW_SIZE_DEFAULT, ae.STEP_DEFAULT, sr, len(y))
    sl = tasks[args.start:args.start + args.windows]
    if len(sl) < args.windows:
        print(json.dumps({"error": f"only {len(sl)} tasks available"}))
        return 1

    t0 = time.time()
    c0 = time.process_time()
    if args.mode == "serial":
        out = [ae._run_detectors(y[s:e], sr, librosa, np) for s, e, _, _ in sl]
        n_done = len(out)
    elif args.mode == "threads":
        windows, _fires = ae._scan_threads(
            y_full=y, sr=sr, tasks=sl, n_threads=args.n, librosa=librosa, np=np,
            n_total_estimate=len(sl), progress_every=0, t_scan=t0)
        n_done = len(windows)
    else:  # procs
        windows, _fires, ok = ae._scan_parallel(
            y_full=y, sr=sr, tasks=sl, n_workers=args.n,
            n_total_estimate=len(sl), progress_every=0, t_scan=t0)
        if not ok:
            print(json.dumps({"error": "parallel path bailed"}))
            return 1
        n_done = len(windows)
    wall = time.time() - t0
    cpu = time.process_time() - c0     # this process only (children not counted)

    print(json.dumps({
        "mode": args.mode, "n": args.n, "windows": n_done, "sr": sr,
        "wall_s": round(wall, 1), "win_per_s": round(n_done / wall, 2),
        "cpu_cores_avg_this_proc": round(cpu / wall, 2),
        "kmp": os.environ.get("KMP_DUPLICATE_LIB_OK", ""),
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
