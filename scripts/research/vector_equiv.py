#!/usr/bin/env python3
"""Audio-scan equivalence harness (Speed #6 gate — I6.0/I6.2), CPU-only, no LM Studio.

The vectorized scan (#6) can't be byte-identical to the per-window scan (STFT center-padding
differs at window edges), so the SHIP GATE is PER-WINDOW FIRE EQUALITY: no window may change
its gate membership at any consumed threshold (rhythmic ≥0.7, crowd ≥0.5, music ≥0.6, anomaly
lane ≥0.40). This tool scans an audio/VOD two ways and reports:
  * per-dial max/mean absolute delta,
  * a FIRE-FLIP report per threshold — every window whose gate membership differs (this is
    the hard gate: it must be EMPTY),
  * how many windows fall in the near-threshold hybrid band (#6 recomputes those exactly).

Modes:
  --old-vs-old         scan serial twice → proves the HARNESS (must be zero deltas/flips)
  --old-vs-threads     serial vs threaded (should already be byte-identical — Speed #2)
  --old-vs-vector      serial vs AUDIO_EVENTS_VECTOR=1 (the real #6 gate; needs I6.1 built)
Input: --wav PATH  or  --vod PATH (extracts 16k mono via ffmpeg).  Read-only.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import audio_events as ae  # noqa: E402

THRESHOLDS = {"rhythmic_speech": 0.7, "crowd_response": 0.5, "music_dominance": 0.6}
ANOMALY_THR = 0.40  # crowd_response gate used by the anomaly lane


def _ensure_wav(a) -> str:
    if a.wav:
        return a.wav
    if not a.vod:
        print("[vector_equiv] pass --wav or --vod"); sys.exit(2)
    out = str(Path(tempfile.gettempdir()) / (Path(a.vod).stem + ".ve.wav"))
    if not Path(out).exists():
        subprocess.run(["ffmpeg", "-y", "-i", a.vod, "-vn", "-acodec", "pcm_s16le",
                        "-ar", "16000", "-ac", "1", out],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return out


def _scan(wav: str, tag: str, *, threads=None, workers=None, vector=False) -> list:
    out = str(Path(tempfile.gettempdir()) / f"ve_{tag}.json")
    if vector:
        os.environ["AUDIO_EVENTS_VECTOR"] = "1"
    else:
        os.environ.pop("AUDIO_EVENTS_VECTOR", None)
    ae.scan_audio_events(wav, out, n_threads=threads, n_workers=workers)
    return json.loads(Path(out).read_text(encoding="utf-8")).get("windows", [])


def _fires(w: dict) -> dict:
    f = {k: (w.get(k, 0.0) >= thr) for k, thr in THRESHOLDS.items()}
    f["anomaly_crowd"] = w.get("crowd_response", 0.0) >= ANOMALY_THR
    return f


def main() -> int:
    ap = argparse.ArgumentParser(description="Audio-scan equivalence / fire-flip harness (#6)")
    ap.add_argument("--wav"); ap.add_argument("--vod")
    ap.add_argument("--mode", choices=["old-vs-old", "old-vs-threads", "old-vs-vector"],
                    default="old-vs-old")
    ap.add_argument("--band", type=float, default=0.05, help="near-threshold hybrid band width")
    a = ap.parse_args()
    wav = _ensure_wav(a)

    A = _scan(wav, "A", threads=1, workers=1)                       # reference: serial per-window
    if a.mode == "old-vs-old":
        B = _scan(wav, "B", threads=1, workers=1)
    elif a.mode == "old-vs-threads":
        B = _scan(wav, "B", threads=4)
    else:
        B = _scan(wav, "B", vector=True)

    if len(A) != len(B):
        print(f"[vector_equiv] WINDOW COUNT MISMATCH: {len(A)} vs {len(B)} — FAIL"); return 1

    max_d = {k: 0.0 for k in THRESHOLDS}
    sum_d = {k: 0.0 for k in THRESHOLDS}
    flips = []
    near_band = 0
    for i, (wa, wb) in enumerate(zip(A, B)):
        for k, thr in THRESHOLDS.items():
            d = abs(float(wa.get(k, 0.0)) - float(wb.get(k, 0.0)))
            max_d[k] = max(max_d[k], d); sum_d[k] += d
            if abs(float(wa.get(k, 0.0)) - thr) <= a.band:
                near_band += 1
        fa, fb = _fires(wa), _fires(wb)
        if fa != fb:
            diffs = [g for g in fa if fa[g] != fb[g]]
            flips.append((wa.get("start"), diffs))

    n = len(A)
    print(f"[vector_equiv] mode={a.mode} windows={n}")
    for k in THRESHOLDS:
        print(f"  {k:<18} max_delta={max_d[k]:.4f} mean_delta={sum_d[k]/max(1,n):.5f}")
    print(f"  near-threshold windows (±{a.band}, hybrid would recompute): {near_band}")
    if flips:
        print(f"  FIRE FLIPS: {len(flips)} window(s) changed a gate — FAIL (ship gate is ZERO):")
        for start, diffs in flips[:10]:
            print(f"    t={start}: {diffs}")
        return 1
    print("  FIRE FLIPS: 0 — PASS (no window changed any gate)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
