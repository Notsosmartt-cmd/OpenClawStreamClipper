#!/usr/bin/env python3
"""L1.2 — turn feedback on a run's clips into ranker labels. NO manual labeling
required: the owner's natural chat feedback is filed by the agent via `set`; the
`template`/edit flow stays as an optional convenience.

A run's produced clips ARE the trace's `selected=True` candidates (10 selected → 10
rendered), each carrying its moment timestamp — so labels join to the training trace
by (run, timestamp) with no extra bookkeeping. Clip titles are enriched from the
per-run effects manifest when present (nicer to read); the trace preview is the
fallback.

Commands:
  template --run <stem>              seed clips/.diagnostics/ratings_<stem>.jsonl
                                     (one row per produced clip, label=null)
  set --run <stem> --match <text> --label 1|0 [--all]
                                     file feedback: set the label on the clip whose
                                     title/preview matches <text> (agent uses this for
                                     the owner's "the Rap Battle was good")
  collect [--out labels_owner.jsonl] merge every rated row -> {run,timestamp,label}
  show --run <stem>                  print the current ratings for a run

`<stem>` is the last_run file stem, e.g. last_run_20260705_010127 (matches how
fit_ranker tags trace rows)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
REPO = HERE.parents[2]
DIAG = REPO / "clips" / ".diagnostics"


def _trace(stem: str) -> dict | None:
    p = DIAG / f"{stem}.json"
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    return d.get("pass_c_candidates") if isinstance(d.get("pass_c_candidates"), dict) else d


def _run_stamp(stem: str) -> str:
    # last_run_20260705_010127 -> 20260705_010127 (the effects_log run stamp)
    return stem[len("last_run_"):] if stem.startswith("last_run_") else stem


def _titles_by_time(stem: str) -> list[tuple[float, float, str]]:
    """(clip_start, clip_end, title) from the run's effects manifest, best-effort."""
    fp = DIAG / "effects_log.jsonl"
    if not fp.exists():
        return []
    want = _run_stamp(stem)
    out = []
    for line in fp.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("run") != want:
            continue
        d = r.get("data") or {}
        cs, dur = d.get("clip_start"), d.get("clip_duration")
        if cs is not None and dur is not None:
            out.append((float(cs), float(cs) + float(dur), str(r.get("clip") or "")))
    return out


def _rows_for(stem: str) -> list[dict]:
    tr = _trace(stem)
    if not tr:
        return []
    sel = [c for c in tr.get("candidates", []) if c.get("selected")]
    titles = _titles_by_time(stem)
    rows = []
    for c in sel:
        t = float(c.get("timestamp", 0))
        title = next((ti for cs, ce, ti in titles if cs <= t <= ce and ti), "")
        rows.append({"run": stem, "timestamp": round(t, 1),
                     "clip": title or (c.get("why") or "")[:70],
                     "final_score": c.get("final_score"), "label": None})
    rows.sort(key=lambda r: -(r.get("final_score") or 0))
    return rows


def _ratings_path(stem: str) -> Path:
    return DIAG / f"ratings_{stem}.jsonl"


def _load_ratings(stem: str) -> list[dict]:
    p = _ratings_path(stem)
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def _save_ratings(stem: str, rows: list[dict]) -> None:
    _ratings_path(stem).write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def cmd_template(a) -> int:
    rows = _rows_for(a.run)
    if not rows:
        print(f"[rate_run] no trace/selected candidates for {a.run} "
              f"(looked for {DIAG / (a.run + '.json')})")
        return 1
    _save_ratings(a.run, rows)
    print(f"[rate_run] seeded {_ratings_path(a.run).name} with {len(rows)} clips (label=null):")
    for r in rows:
        print(f"   T={r['timestamp']:<8} {r['clip'][:60]}")
    print("Set labels via `set --match <text> --label 1|0`, or edit the file, then `collect`.")
    return 0


def cmd_set(a) -> int:
    rows = _load_ratings(a.run) or _rows_for(a.run)
    if not rows:
        print(f"[rate_run] no ratings/trace for {a.run}; run `template` first.")
        return 1
    q = a.match.lower()
    hit = [r for r in rows if q in str(r.get("clip", "")).lower()]
    if not hit:
        print(f"[rate_run] no clip matches {a.match!r}. Clips: "
              + "; ".join(r['clip'][:30] for r in rows))
        return 1
    if len(hit) > 1 and not a.all:
        print(f"[rate_run] {a.match!r} matches {len(hit)} clips — refine, or --all:")
        for r in hit:
            print(f"   T={r['timestamp']} {r['clip'][:60]}")
        return 1
    # --label is optional now: a `set` may record ONLY a caption verdict or a
    # variant winner (P1.6 / P2.4) without touching the clip-quality label.
    for r in hit:
        parts = []
        if a.label is not None:
            r["label"] = int(a.label)
            parts.append(f"label={a.label}")
        # P1.6 — caption verdict, stored SEPARATELY from the clip label. Clip-good
        # != caption-good (Little J's was exactly this split); this builds the
        # negative-exemplar pool + a caption regression metric.
        if a.caption is not None:
            r["caption_label"] = int(a.caption)
            parts.append(f"caption={a.caption}")
        # P2.4 — which A/B variant won on the platform (owner-recorded post-hoc).
        if a.variant_winner is not None:
            r["variant_winner"] = a.variant_winner
            parts.append(f"variant_winner={a.variant_winner}")
        if not parts:
            print("[rate_run] nothing to set — pass --label, --caption, and/or --variant-winner.")
            return 1
        print(f"[rate_run] {' '.join(parts)} -> T={r['timestamp']} {r['clip'][:55]}")
    _save_ratings(a.run, rows)
    return 0


def cmd_show(a) -> int:
    rows = _load_ratings(a.run)
    if not rows:
        print(f"[rate_run] no ratings for {a.run} (run `template`).")
        return 0
    for r in rows:
        lab = r.get("label")
        extra = ""
        if r.get("caption_label") is not None:
            extra += f" cap={r['caption_label']}"
        if r.get("variant_winner"):
            extra += f" win={r['variant_winner']}"
        print(f"   [{'?' if lab is None else lab}] T={r['timestamp']:<8} {r['clip'][:52]}{extra}")
    done = sum(1 for r in rows if r.get("label") is not None)
    capdone = sum(1 for r in rows if r.get("caption_label") is not None)
    print(f"[rate_run] {done}/{len(rows)} rated"
          + (f", {capdone} caption-rated" if capdone else ""))
    return 0


def cmd_collect(a) -> int:
    # Default = clip-quality labels -> labels_owner.jsonl (the ranker's input,
    # unchanged). --captions = the P1.6 caption verdicts -> labels_caption.jsonl
    # (a separate metric / negative-exemplar pool; never touches the ranker).
    field = "caption_label" if getattr(a, "captions", False) else "label"
    default_name = "labels_caption.jsonl" if field == "caption_label" else "labels_owner.jsonl"
    out = Path(a.out) if a.out else (DIAG / default_name)
    merged, n_files = [], 0
    for rp in sorted(DIAG.glob("ratings_*.jsonl")):
        n_files += 1
        for r in _load_ratings(rp.name[len("ratings_"):-len(".jsonl")]):
            if r.get(field) is not None:
                merged.append({"run": r["run"], "timestamp": r["timestamp"],
                               field: int(r[field]), "source": "owner"})
    out.write_text("\n".join(json.dumps(m) for m in merged) + ("\n" if merged else ""),
                   encoding="utf-8")
    pos = sum(1 for m in merged if m[field] == 1)
    print(f"[rate_run] collected {len(merged)} owner {field}(s) ({pos} pos / {len(merged)-pos} neg) "
          f"from {n_files} run(s) -> {out}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Rate a run's clips -> ranker labels")
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("template"); t.add_argument("--run", required=True); t.set_defaults(fn=cmd_template)
    s = sub.add_parser("set"); s.add_argument("--run", required=True); s.add_argument("--match", required=True)
    s.add_argument("--label", type=int, choices=[0, 1])          # clip quality (optional now)
    s.add_argument("--caption", type=int, choices=[0, 1])        # P1.6 caption verdict
    s.add_argument("--variant-winner", choices=["A", "B"])       # P2.4 A/B outcome
    s.add_argument("--all", action="store_true")
    s.set_defaults(fn=cmd_set)
    sh = sub.add_parser("show"); sh.add_argument("--run", required=True); sh.set_defaults(fn=cmd_show)
    c = sub.add_parser("collect"); c.add_argument("--out")
    c.add_argument("--captions", action="store_true")           # collect caption_label instead of label
    c.set_defaults(fn=cmd_collect)
    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
