#!/usr/bin/env python3
"""L1.3 — unify label sources into one training file for fit_ranker.

Sources:
  * Path C  clips/.diagnostics/labels_social.jsonl  (viewer-posted, VOD-keyed, positives)
  * Path B  clips/.diagnostics/labels_owner.jsonl   (owner feedback, run-keyed, +/-)

Path C rows are keyed by VOD; fit_ranker joins by RUN. This resolves VOD -> run using
the trace's own `vod` stamp (L1.1) with a `trace_vods.json` sidecar fallback for older
traces that predate the stamp. Output rows are {run, timestamp, label, source}.

Merge policy (owner directive): C and B both count; on a (run, ~timestamp) collision
the OWNER wins (B overrides C) — B is the taste ground truth, C is proxy platform
signal. Output -> clips/.diagnostics/labels_all.jsonl (or --out)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
REPO = HERE.parents[2]
DIAG = REPO / "clips" / ".diagnostics"


def _load_jsonl(p: Path) -> list[dict]:
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def _run_vod_index() -> dict[str, str]:
    """run-stem -> vod basename, from each trace's `vod` stamp, then the sidecar map."""
    idx: dict[str, str] = {}
    for tp in sorted(DIAG.glob("last_run_*.json")):
        try:
            d = json.loads(tp.read_text(encoding="utf-8"))
            pc = d.get("pass_c_candidates") or {}
            vod = pc.get("vod") or d.get("vod")
            if vod:
                idx[tp.stem] = str(vod)
        except Exception:
            pass
    side = DIAG / "trace_vods.json"
    if side.exists():
        try:
            for run, vod in json.loads(side.read_text(encoding="utf-8")).items():
                idx.setdefault(run, vod)   # trace stamp wins; sidecar fills gaps
        except Exception:
            pass
    return idx


def _vod_key(v: str) -> str:
    return Path(str(v)).stem.lower()


def main() -> int:
    ap = argparse.ArgumentParser(description="Merge Path C + Path B labels for fit_ranker")
    ap.add_argument("--out")
    ap.add_argument("--tol", type=float, default=10.0,
                    help="seconds within which a B row overrides a C row (default 10)")
    a = ap.parse_args()

    owner = _load_jsonl(DIAG / "labels_owner.jsonl")
    social = _load_jsonl(DIAG / "labels_social.jsonl")
    idx = _run_vod_index()
    vod_to_runs: dict[str, list[str]] = {}
    for run, vod in idx.items():
        vod_to_runs.setdefault(_vod_key(vod), []).append(run)

    out_rows: list[dict] = []
    # B first (authoritative)
    for r in owner:
        if r.get("run") and r.get("label") is not None:
            out_rows.append({"run": r["run"], "timestamp": float(r["timestamp"]),
                             "label": int(r["label"]), "source": "owner"})
    # C, resolved VOD->run, skipped where an owner label already covers it
    unresolved = 0
    for r in social:
        runs = vod_to_runs.get(_vod_key(r.get("vod", "")), [])
        if not runs:
            unresolved += 1
            continue
        for run in runs:
            if any(o["run"] == run and abs(o["timestamp"] - float(r["timestamp"])) <= a.tol
                   for o in out_rows if o["source"] == "owner"):
                continue  # owner overrides
            out_rows.append({"run": run, "timestamp": float(r["timestamp"]),
                             "label": int(r.get("label", 1)), "source": "social"})

    out = Path(a.out) if a.out else (DIAG / "labels_all.jsonl")
    out.write_text("\n".join(json.dumps(x) for x in out_rows) + ("\n" if out_rows else ""),
                   encoding="utf-8")
    pos = sum(1 for x in out_rows if x["label"] == 1)
    b = sum(1 for x in out_rows if x["source"] == "owner")
    print(f"[merge_labels] {len(out_rows)} labels ({pos} pos / {len(out_rows)-pos} neg; "
          f"{b} owner + {len(out_rows)-b} social) -> {out}")
    if unresolved:
        print(f"[merge_labels] {unresolved} social label(s) had no matching run trace yet "
              f"(their VOD hasn't been run through the pipeline since B1 tracing / no sidecar entry). "
              f"They activate once that VOD is run.")
    known = ", ".join(f"{r}->{_vod_key(v)}" for r, v in list(idx.items())[:4])
    print(f"[merge_labels] run->vod index: {len(idx)} traces ({known}{'...' if len(idx)>4 else ''})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
