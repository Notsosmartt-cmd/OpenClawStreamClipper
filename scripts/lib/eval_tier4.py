"""Tier-4 Phase 4.8 — Eval and validation harness.

Compares pipeline-selected moments against a user-curated reference set and
reports precision / recall, broken down by Pattern Catalog id.

Reference format (one VOD's labels, JSON file):
    {
      "vod": "lacy_2024-10-15.mp4",
      "wanted": [
        {"timestamp": 480, "tolerance_s": 20, "pattern": "setup_external_contradiction", "note": "penthouse"},
        {"timestamp": 1240, "tolerance_s": 15, "pattern": "challenge_and_fold", "note": "..."}
      ]
    }

A pipeline-selected moment counts as a TRUE-POSITIVE for a wanted entry when:
    abs(moment.timestamp - wanted.timestamp) <= wanted.tolerance_s
A wanted entry with at least one matching pipeline moment is RECALLED.
A pipeline moment with no matching wanted entry is a FALSE-POSITIVE.

Output:
    Precision = TP / (TP + FP)
    Recall    = TP / (TP + FN)         where FN = wanted entries not matched
    Per-pattern recall when wanted entries name a pattern.

CLI:
    python3 scripts/lib/eval_tier4.py \\
        --reference labels/lacy.json \\
        --selected /tmp/clipper/hype_moments.json \\
        --report-out clips/.diagnostics/lacy_eval.json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from typing import Any, Dict, List, Optional, Sequence, Set


def _load_json(path: str) -> Optional[Any]:
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"[EVAL] couldn't load {path}: {e}", file=sys.stderr)
        return None


def evaluate(
    reference: Dict[str, Any],
    selected: Sequence[Dict[str, Any]],
    *,
    default_tolerance_s: float = 20.0,
) -> Dict[str, Any]:
    """Return a structured precision/recall report keyed by pattern."""
    wanted: List[Dict[str, Any]] = list(reference.get("wanted") or [])
    selected_list = list(selected or [])

    if not wanted and not selected_list:
        return {"precision": 0.0, "recall": 0.0, "tp": 0, "fp": 0, "fn": 0, "by_pattern": {}}

    matched_wanted: Set[int] = set()
    matched_selected: Set[int] = set()
    pair_log: List[Dict[str, Any]] = []

    for w_idx, w in enumerate(wanted):
        try:
            w_ts = float(w.get("timestamp"))
        except (ValueError, TypeError):
            continue
        tol = float(w.get("tolerance_s", default_tolerance_s) or default_tolerance_s)
        for s_idx, s in enumerate(selected_list):
            try:
                s_ts = float(s.get("timestamp"))
            except (ValueError, TypeError):
                continue
            if abs(s_ts - w_ts) <= tol:
                matched_wanted.add(w_idx)
                matched_selected.add(s_idx)
                pair_log.append({
                    "wanted_t": w_ts,
                    "selected_t": s_ts,
                    "delta_s": round(s_ts - w_ts, 2),
                    "wanted_pattern": w.get("pattern"),
                    "selected_pattern": s.get("primary_pattern") or s.get("pattern_confirmed"),
                    "note": w.get("note", ""),
                })
                break

    tp = len(matched_wanted)
    fn = len(wanted) - tp
    fp = max(0, len(selected_list) - len(matched_selected))
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0

    by_pattern_total: Dict[str, int] = defaultdict(int)
    by_pattern_hit: Dict[str, int] = defaultdict(int)
    for w_idx, w in enumerate(wanted):
        pid = w.get("pattern") or "(unspecified)"
        by_pattern_total[pid] += 1
        if w_idx in matched_wanted:
            by_pattern_hit[pid] += 1

    by_pattern = {
        pid: {
            "wanted": by_pattern_total[pid],
            "matched": by_pattern_hit[pid],
            "recall": round(by_pattern_hit[pid] / by_pattern_total[pid], 3) if by_pattern_total[pid] else 0.0,
        }
        for pid in sorted(by_pattern_total)
    }

    return {
        "vod": reference.get("vod"),
        "wanted_count": len(wanted),
        "selected_count": len(selected_list),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "by_pattern": by_pattern,
        "matches": pair_log,
    }


def _print_report(report: Dict[str, Any]) -> None:
    print(f"\n=== Tier-4 Eval Report — {report.get('vod', '?')} ===")
    print(f"wanted={report['wanted_count']}  selected={report['selected_count']}")
    print(f"TP={report['tp']}  FP={report['fp']}  FN={report['fn']}")
    print(f"precision={report['precision']:.3f}  recall={report['recall']:.3f}")
    if report["by_pattern"]:
        print("Per-pattern recall:")
        for pid, stats in report["by_pattern"].items():
            print(f"  {pid}: {stats['matched']}/{stats['wanted']} = {stats['recall']:.3f}")
    if report["matches"]:
        print(f"Matches ({len(report['matches'])}):")
        for m in report["matches"][:10]:
            print(
                f"  wanted t={m['wanted_t']:.1f}s pat={m['wanted_pattern']!r} "
                f"  selected t={m['selected_t']:.1f}s pat={m['selected_pattern']!r}  "
                f"  delta={m['delta_s']:+.2f}s  {m['note']}"
            )


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(description="Tier-4 eval — precision/recall vs. user labels")
    parser.add_argument("--reference", required=True, help="JSON file with {vod, wanted: [...]}")
    parser.add_argument("--selected", default="/tmp/clipper/hype_moments.json", help="Pipeline output JSON")
    parser.add_argument("--report-out", default=None, help="Optional path to write the JSON report")
    parser.add_argument("--default-tolerance", type=float, default=20.0, help="Tolerance (seconds) when wanted entries omit it")
    args = parser.parse_args(argv)

    reference = _load_json(args.reference)
    if not isinstance(reference, dict):
        print(f"[EVAL] reference at {args.reference} is not a dict", file=sys.stderr)
        return 2

    selected = _load_json(args.selected)
    if selected is None:
        return 2
    if not isinstance(selected, list):
        print(f"[EVAL] selected at {args.selected} is not a list", file=sys.stderr)
        return 2

    report = evaluate(reference, selected, default_tolerance_s=args.default_tolerance)
    _print_report(report)

    if args.report_out:
        try:
            with open(args.report_out, "w") as f:
                json.dump(report, f, indent=2)
            print(f"[EVAL] wrote report to {args.report_out}", file=sys.stderr)
        except OSError as e:
            print(f"[EVAL] couldn't write report to {args.report_out}: {e}", file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
