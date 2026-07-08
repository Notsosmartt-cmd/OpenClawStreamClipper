#!/usr/bin/env python3
"""Durable, self-contained training store — makes the trace pile safe to delete.

Owner directive (2026-07-08): both label paths should keep their OWN copy of what
training needs, so the ephemeral `clips/.diagnostics/last_run_*.json` traces can be
cleaned up without orphaning anything. Any moment the owner labels, or any reference
clip aligned (Path C), is training-relevant forever.

A label alone isn't enough: the fitter needs the labeled moment's FEATURES *and* the
run's other candidates as negatives *and* the full candidate set for the gate's
recall@N. So we FREEZE the ENTIRE candidate set of any labeled run — a small
features-only snapshot (no audio/video) — into a committed store:

    learning/frozen_runs/<run>.json = {run, vod, frozen_at, candidates:[...], labels:[...]}

`fit_ranker --frozen learning/frozen_runs` then trains + gates entirely from this
store, with ZERO dependency on the trace pile. Freeze is idempotent + additive: new
labels for an already-frozen run are merged in (candidates kept). Only B1-complete
runs (with stamped features) are frozen — pre-B1 traces are untrainable anyway.

Committed to git (unlike the gitignored diagnostics), so the labels survive a
`clips/.diagnostics/` wipe or a fresh checkout."""
from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DIAG = REPO / "clips" / ".diagnostics"
FROZEN_DIR = REPO / "learning" / "frozen_runs"


def _trace_payload(run_stem: str) -> dict | None:
    """The pass_c trace dict for a run stem, from the diagnostics snapshot."""
    p = DIAG / f"{run_stem}.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    pc = d.get("pass_c_candidates") if isinstance(d.get("pass_c_candidates"), dict) else d
    return pc if isinstance(pc, dict) and pc.get("candidates") else None


def _is_b1(candidates: list) -> bool:
    return bool(candidates) and "style_multiplier" in candidates[0]


def freeze(labels: list[dict], *, verbose: bool = True) -> dict:
    """Freeze every RUN referenced by `labels` (each label = {run, timestamp, label,
    source, [vod]}) into learning/frozen_runs/<run>.json — full candidate set + the
    run's labels. Additive: merges labels into an existing frozen file (dedup by
    timestamp+source). Returns {frozen, updated, skipped_prebn, missing_trace}."""
    FROZEN_DIR.mkdir(parents=True, exist_ok=True)
    by_run: dict[str, list[dict]] = {}
    for lab in labels:
        run = lab.get("run")
        if run:
            by_run.setdefault(run, []).append(lab)

    stats = {"frozen": 0, "updated": 0, "skipped_prebn": [], "missing_trace": []}
    for run, run_labels in by_run.items():
        dst = FROZEN_DIR / f"{run}.json"
        norm = [{"timestamp": float(l["timestamp"]), "label": int(l["label"]),
                 "source": l.get("source", "owner")} for l in run_labels
                if l.get("timestamp") is not None and l.get("label") is not None]
        if dst.exists():
            # additive: keep the frozen candidates, union the labels
            cur = json.loads(dst.read_text(encoding="utf-8"))
            seen = {(round(x["timestamp"], 1), x["source"]) for x in cur.get("labels", [])}
            added = [x for x in norm if (round(x["timestamp"], 1), x["source"]) not in seen]
            if not added:
                continue
            cur["labels"] = cur.get("labels", []) + added
            dst.write_text(json.dumps(cur, indent=1), encoding="utf-8")
            stats["updated"] += 1
            if verbose:
                print(f"[label_store] +{len(added)} label(s) -> {dst.name}")
            continue
        pc = _trace_payload(run)
        if pc is None:
            stats["missing_trace"].append(run)
            continue
        cands = pc.get("candidates") or []
        if not _is_b1(cands):
            stats["skipped_prebn"].append(run)
            continue
        dst.write_text(json.dumps({
            "run": run, "vod": pc.get("vod", ""),
            "candidates": cands, "labels": norm,
            "_note": "Frozen self-contained training snapshot (features only, no media). "
                     "Safe to delete the source clips/.diagnostics trace once this exists.",
        }, indent=1), encoding="utf-8")
        stats["frozen"] += 1
        if verbose:
            print(f"[label_store] froze {dst.name}: {len(cands)} candidates, {len(norm)} label(s)")
    if verbose:
        if stats["skipped_prebn"]:
            print(f"[label_store] skipped {len(stats['skipped_prebn'])} pre-B1 run(s) (untrainable)")
        if stats["missing_trace"]:
            print(f"[label_store] {len(stats['missing_trace'])} run(s) have labels but no trace on "
                  f"disk to freeze (already pruned? re-run that VOD to regenerate): "
                  f"{stats['missing_trace'][:3]}")
    return stats


def load_frozen() -> list[dict]:
    """All frozen run snapshots (each {run, vod, candidates, labels})."""
    out = []
    for p in sorted(FROZEN_DIR.glob("*.json")):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            pass
    return out


def frozen_runs() -> set[str]:
    """Run stems that are safely frozen (their trace is now deletable)."""
    return {p.stem for p in FROZEN_DIR.glob("*.json")}


def main() -> int:
    import sys
    labels_path = DIAG / "labels_all.jsonl"
    if len(sys.argv) > 1:
        labels_path = Path(sys.argv[1])
    labels = [json.loads(l) for l in labels_path.read_text(encoding="utf-8").splitlines()
              if l.strip()] if labels_path.exists() else []
    if not labels:
        print(f"[label_store] no labels at {labels_path} — run merge_labels first.")
        return 0
    freeze(labels)
    print(f"[label_store] durable store: {FROZEN_DIR} ({len(list(FROZEN_DIR.glob('*.json')))} run(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
