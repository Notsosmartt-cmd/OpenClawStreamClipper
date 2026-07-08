#!/usr/bin/env python3
"""Run-metrics reader (Speed #7, plan-pipeline-speed-2026-07).

Durable pipeline speed history. `common.cleanup` appends one row per run to
`clips/.diagnostics/run_metrics.jsonl` (survives prune_traces). This tool:

  backfill   scan existing last_run_*.json diagnostics and rebuild run_metrics.jsonl
             (recovers the ~21 historical runs BEFORE any prune deletes them; idempotent —
             keyed by the run stamp, never double-counts).
  report     per-stage medians/means + realtime-ratio distribution + regression flags
             (a stage >1.5x its median on the latest run is flagged).

Read-only except `backfill`, which only writes the metrics file. Usage:
  python scripts/research/run_metrics.py backfill
  python scripts/research/run_metrics.py report
"""
from __future__ import annotations

import glob
import json
import statistics as st
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DIAG = REPO / "clips" / ".diagnostics"
METRICS = DIAG / "run_metrics.jsonl"


def _load() -> list[dict]:
    if not METRICS.exists():
        return []
    return [json.loads(ln) for ln in METRICS.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _row_from_diag(path: Path) -> dict | None:
    """Reconstruct a metrics row from a last_run_*.json diagnostic (has stage_timings)."""
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    stt = d.get("stage_timings")
    if not stt:
        return None
    pc = d.get("pass_c_candidates") or {}
    stem = path.stem  # last_run_YYYYmmdd_HHMMSS
    ts = stem[len("last_run_"):] if stem.startswith("last_run_") else stem
    clips = d.get("clips_made")
    return {
        "ts": ts,
        "vod": pc.get("vod") or "",
        "vod_seconds": pc.get("max_time_s"),
        "clips": len(clips) if isinstance(clips, list) else (pc.get("selected_count") or 0),
        "total_seconds": stt.get("total_seconds"),
        "exit_code": 0,
        "stages": {s["stage"]: s["seconds"] for s in stt.get("stages", [])},
        "_backfilled": True,
    }


def cmd_backfill() -> int:
    existing = {r.get("ts") for r in _load()}
    added = 0
    rows = _load()
    for f in sorted(glob.glob(str(DIAG / "last_run_*.json"))):
        row = _row_from_diag(Path(f))
        if row and row["ts"] not in existing:
            rows.append(row)
            existing.add(row["ts"])
            added += 1
    rows.sort(key=lambda r: r.get("ts", ""))
    METRICS.parent.mkdir(parents=True, exist_ok=True)
    METRICS.write_text("\n".join(json.dumps(r) for r in rows) + ("\n" if rows else ""),
                       encoding="utf-8")
    print(f"[run_metrics] backfilled {added} row(s) from diagnostics -> {METRICS} "
          f"({len(rows)} total).")
    return 0


def _norm(label: str) -> str:
    return label.split("—")[-1].strip() if "—" in label else label.strip()


def cmd_report() -> int:
    rows = _load()
    if not rows:
        print("[run_metrics] no rows — run `backfill` first (or run the pipeline once).")
        return 0
    per: dict[str, list[float]] = {}
    ratios = []
    for r in rows:
        for lab, sec in (r.get("stages") or {}).items():
            per.setdefault(_norm(lab), []).append(sec)
        tot, vs = r.get("total_seconds"), r.get("vod_seconds")
        if tot and vs:
            ratios.append(tot / vs)
    print(f"[run_metrics] {len(rows)} run(s). Per-stage seconds (n, median, mean, max):")
    for lab, v in sorted(per.items(), key=lambda kv: -st.median(kv[1])):
        print(f"  {lab[:44]:<46} n={len(v):>2} med={st.median(v):>7.1f} "
              f"mean={st.mean(v):>7.1f} max={max(v):>7.1f}")
    if ratios:
        print(f"  realtime ratio (proc/vod): n={len(ratios)} median={st.median(ratios):.3f} "
              f"mean={st.mean(ratios):.3f} range {min(ratios):.3f}-{max(ratios):.3f}")
    # regression flag: latest run vs per-stage median
    latest = rows[-1]
    flags = []
    for lab, sec in (latest.get("stages") or {}).items():
        med = st.median(per[_norm(lab)])
        if med > 0 and sec > 1.5 * med:
            flags.append(f"{_norm(lab)} {sec:.0f}s vs median {med:.0f}s ({sec/med:.1f}x)")
    if flags:
        print(f"[run_metrics] REGRESSION FLAGS on latest run ({latest.get('ts')}):")
        for f in flags:
            print(f"    ⚠ {f}")
    else:
        print(f"[run_metrics] latest run ({latest.get('ts')}) within 1.5x median on all stages.")
    return 0


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    if cmd == "backfill":
        return cmd_backfill()
    if cmd == "report":
        return cmd_report()
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
