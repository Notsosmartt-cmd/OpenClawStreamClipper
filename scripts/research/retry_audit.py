#!/usr/bin/env python3
"""C6 — JSON-retry audit (Speed Wave 2, plan-serving-stack-2026-07 §P0.3).

Read-only. Scans persistent pipeline logs for Stage-4/6 LLM retry + parse-failure
markers and reports the retry RATE per run. Decides whether grammar-constrained
decoding is worth pursuing:

  * rate ~= 0            -> close C6 permanently (constrained decoding alters the
                           token distribution and is only justified by material
                           retry waste; there is none).
  * rate > ~2% of calls -> file a follow-up candidate with the measured number
                           (still quality-gated).

The markers matched are the exact stderr lines emitted by stage4_moments.call_llm
and the Pass-B re-queue path (see stage4_moments.py):
  - "LLM call attempt N/M failed"           (network/exception retry)
  - "LLM returned empty content (attempt N)"(empty-content retry)
  - "queued for end-of-pass retry"          (per-chunk failure -> re-queue)
  - "Re-queueing N failed chunk(s)"         (Pass-B batch re-queue)
  - "A1 JSON parse failed"                  (global-arc JSON parse failure)
  - "Chunk N (...)"                         (denominator: chunks that reached the LLM)

Usage:
    python scripts/research/retry_audit.py            # last 10 runs
    python scripts/research/retry_audit.py --n 25
    python scripts/research/retry_audit.py --log <path>   # one file
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Resolve PATHS without importing the whole pipeline package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
try:
    from paths import PATHS  # type: ignore
    _LOG_DIR = PATHS.persistent_log_dir
except Exception:  # pragma: no cover - fallback for odd checkouts
    _LOG_DIR = Path(__file__).resolve().parents[2] / "clips" / ".pipeline_logs"

# (label, compiled pattern). Retry/failure markers are the NUMERATOR.
_RETRY_MARKERS = {
    "call_attempt_failed": re.compile(r"LLM call attempt \d+/\d+ failed"),
    "empty_content_retry": re.compile(r"LLM returned empty content \(attempt \d+\)"),
    "chunk_requeued": re.compile(r"queued for end-of-pass retry"),
    "passb_requeue": re.compile(r"Re-queueing \d+ failed chunk"),
    "a1_parse_failed": re.compile(r"A1 JSON parse failed"),
}
# Denominator: a chunk reaching the LLM prints "  Chunk N (start s-end s): type, ..."
# Logs carry a "[HH:MM:SS +Ns] " timestamp prefix, so match anywhere in the line.
_CHUNK_CALL = re.compile(r"Chunk \d+ \(\d+s-\d+s\):")


def audit_file(path: Path) -> dict:
    counts = {k: 0 for k in _RETRY_MARKERS}
    chunk_calls = 0
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return {"path": path, "error": str(e)}
    for line in text.splitlines():
        if _CHUNK_CALL.search(line):
            chunk_calls += 1
        for name, pat in _RETRY_MARKERS.items():
            if pat.search(line):
                counts[name] += 1
    total_retry = sum(counts.values())
    # Denominator: prefer observed chunk calls; guard against zero.
    denom = max(chunk_calls, 1)
    return {
        "path": path,
        "chunk_calls": chunk_calls,
        "retry_events": total_retry,
        "rate": total_retry / denom,
        "breakdown": counts,
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n", type=int, default=10, help="most-recent N runs (default 10)")
    ap.add_argument("--log", type=Path, help="audit a single log file")
    args = ap.parse_args(argv)

    if args.log:
        files = [args.log]
    else:
        if not _LOG_DIR.exists():
            print(f"(no log dir: {_LOG_DIR})")
            return 1
        files = sorted(_LOG_DIR.glob("*.log"), key=lambda p: p.stat().st_mtime,
                       reverse=True)[: args.n]
    if not files:
        print(f"(no logs in {_LOG_DIR})")
        return 1

    print(f"{'run':<52} {'chunks':>7} {'retries':>8} {'rate':>7}")
    print("-" * 78)
    agg_chunks = agg_retry = 0
    agg_break: dict[str, int] = {k: 0 for k in _RETRY_MARKERS}
    for f in files:
        r = audit_file(f)
        if "error" in r:
            print(f"{f.name:<52} ERROR {r['error']}")
            continue
        agg_chunks += r["chunk_calls"]
        agg_retry += r["retry_events"]
        for k, v in r["breakdown"].items():
            agg_break[k] += v
        print(f"{f.name:<52} {r['chunk_calls']:>7} {r['retry_events']:>8} "
              f"{r['rate']*100:>6.2f}%")

    denom = max(agg_chunks, 1)
    rate = agg_retry / denom
    print("-" * 78)
    print(f"{'TOTAL':<52} {agg_chunks:>7} {agg_retry:>8} {rate*100:>6.2f}%")
    print(f"\nbreakdown: {agg_break}")
    print("\nVERDICT:", end=" ")
    if rate <= 0.02:
        print(f"rate {rate*100:.2f}% <= 2% -> CLOSE C6 "
              "(grammar-constrained decoding not justified; alters token distribution "
              "for negligible retry savings).")
    else:
        print(f"rate {rate*100:.2f}% > 2% -> KEEP C6 as a candidate "
              "(measured retry waste is material; constrained decoding worth a quality-gated trial).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
