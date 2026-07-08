#!/usr/bin/env python3
"""Safe cleanup of the clips/.diagnostics/last_run_*.json trace pile.

Owner wants to keep the log pile from growing without bound. A trace is safe to
delete when nothing depends on it. The ONLY dependency is a label pointing into it —
and even that is removed once the run is FROZEN into the durable learning/frozen_runs
store (which copies the full candidate set + labels). So the rule:

  DELETABLE  = no label points at the run  OR  the run is already frozen
  PROTECTED  = a label points at the run AND it is NOT yet frozen (delete would orphan)

Also keeps the most-recent --keep-recent traces regardless (observability / a future
label). Dry-run by DEFAULT — prints what it would delete; pass --apply to delete.
Never touches the frozen store, the label files, or anything outside the trace pile."""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DIAG = REPO / "clips" / ".diagnostics"
sys.path.insert(0, str(Path(__file__).resolve().parent))


def _labeled_runs() -> set[str]:
    runs: set[str] = set()
    for f in ("labels_all.jsonl", "labels_owner.jsonl"):
        p = DIAG / f
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                try:
                    r = json.loads(line).get("run")
                    if r:
                        runs.add(r)
                except Exception:
                    pass
    return runs


def main() -> int:
    args = sys.argv[1:]
    apply = "--apply" in args
    keep_recent = int(args[args.index("--keep-recent") + 1]) if "--keep-recent" in args else 8

    try:
        import label_store
        frozen = label_store.frozen_runs()
    except Exception:
        frozen = set()
    labeled = _labeled_runs()

    traces = sorted(DIAG.glob("last_run_*.json"))
    recent = {p.stem for p in sorted(traces, key=lambda p: p.name)[-keep_recent:]}

    protected, deletable = [], []
    for p in traces:
        run = p.stem
        if run in recent:
            protected.append((p, "recent"))
        elif run in labeled and run not in frozen:
            protected.append((p, "labeled-but-UNFROZEN (freeze first!)"))
        else:
            deletable.append(p)

    freed = sum(p.stat().st_size for p in deletable)
    print(f"[prune] {len(traces)} traces | protect {len(protected)} "
          f"({len(recent)} recent, {sum(1 for _,r in protected if 'UNFROZEN' in r)} unfrozen-labeled) "
          f"| deletable {len(deletable)} (~{freed/1e6:.1f} MB)")
    unfrozen = [p.name for p, r in protected if "UNFROZEN" in r]
    if unfrozen:
        print(f"[prune] WON'T delete {len(unfrozen)} labeled-but-unfrozen trace(s) — run "
              f"`merge_labels` (auto-freezes) first: {unfrozen[:3]}")
    if not apply:
        print(f"[prune] DRY RUN — would delete {len(deletable)} trace(s). Re-run with --apply to delete.")
        for p in deletable[:5]:
            print(f"          would delete {p.name}")
        if len(deletable) > 5:
            print(f"          ... and {len(deletable)-5} more")
        return 0
    for p in deletable:
        try:
            p.unlink()
        except OSError as e:
            print(f"[prune] could not delete {p.name}: {e}")
    print(f"[prune] deleted {len(deletable)} trace(s), freed ~{freed/1e6:.1f} MB. "
          f"Frozen store + labeled traces intact.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
