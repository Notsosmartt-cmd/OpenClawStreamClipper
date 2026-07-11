#!/usr/bin/env python3
"""our_clip_cards.py — Phase R2 of concepts/plan-reference-deconstruction-2026-07.

Run the SAME deconstruction (clip_forensics timeline → attribute card) over the
clips OUR pipeline produced, so R3 can diff them against the reference-corpus
cards. Two deliberate differences from the reference path:

  1. **Separate, run-scoped cache dir** — `clips/.diagnostics/cards/<run>/` —
     so our artifacts never mix into `reference_clips/.cache`.
  2. **Ground truth merged, not inferred** — we LOGGED what we injected
     (`clips/.diagnostics/effects_log.jsonl` render_plan rows: sfx cues, zoom
     punches, cold-open). Each card gains a `_ground_truth` section from the
     log, so the diff can use exact numbers where we have them and the card's
     inferred view where we don't.

Primary clips only by default: " (B).mp4" variants differ from A only in the
seed draw + hook, and " (Short).mp4" companions are sub-cuts — carding them
would double-count the same detected moments in the diff.

Usage:
  python scripts/research/our_clip_cards.py --run 20260710_202308
  python scripts/research/our_clip_cards.py --run <stamp> --include-variants
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(REPO / "scripts" / "lib"))

import clip_forensics as cf      # noqa: E402
import attribute_cards as ac     # noqa: E402

CLIPS = REPO / "clips"
DIAG = CLIPS / ".diagnostics"
EFFECTS_LOG = DIAG / "effects_log.jsonl"


def _log(msg: str) -> None:
    print(f"[our_clip_cards] {msg}", file=sys.stderr, flush=True)


def _norm(s: str) -> str:
    """Match effects_log titles (raw, may carry apostrophes) to on-disk filenames
    (Stage-7-sanitized: alnum + ' -' only) by normalizing BOTH sides the same way."""
    return "".join(c for c in str(s) if c.isalnum() or c in " -").strip().lower()


def _effects_for_run(run_stamp: str) -> dict[str, dict]:
    """{clip_title: render_plan data} for the run — the injected ground truth."""
    out: dict[str, dict] = {}
    if not EFFECTS_LOG.exists():
        return out
    for line in EFFECTS_LOG.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("run") != run_stamp:
            continue
        kind = r.get("type")   # effects_log rows carry `type` (render_plan/cold_open)
        clipname = str(r.get("clip") or "")
        if not clipname:
            continue
        rec = out.setdefault(clipname, {})
        if kind == "render_plan":
            d = r.get("data") or {}
            rec["sfx_cues"] = d.get("sfx_cues") or []
            rec["zoom_punches"] = d.get("zoom_punches") or []
            rec["freeze_at"] = d.get("freeze_at")
            rec["slow_mo"] = d.get("slow_mo")
            rec["category"] = d.get("category")
            rec["clip_duration"] = d.get("clip_duration")
        elif kind == "cold_open":
            rec["cold_open"] = r.get("data") or True
    return out


def _run_clips(run_stamp: str, include_variants: bool) -> list[Path]:
    """The run's produced mp4s, matched via effects_log titles (authoritative for
    the run) with a mtime fallback when the log has nothing for that run."""
    titles = {_norm(t) for t in _effects_for_run(run_stamp).keys()}
    vids: list[Path] = []
    for f in sorted(CLIPS.glob("*.mp4")):
        name = f.stem
        if not include_variants and (name.endswith(" (B)") or name.endswith(" (Short)")):
            continue
        base = _norm(name.replace(" (B)", "").replace(" (Short)", ""))
        if titles and base not in titles:
            continue
        vids.append(f)
    if not vids and not titles:
        _log(f"no effects_log rows for run {run_stamp}; falling back to ALL clips/*.mp4")
        vids = [f for f in sorted(CLIPS.glob("*.mp4"))
                if include_variants or not (f.stem.endswith(" (B)") or f.stem.endswith(" (Short)"))]
    return vids


def main() -> int:
    ap = argparse.ArgumentParser(description="Deconstruct OUR produced clips into attribute cards (R2)")
    ap.add_argument("--run", required=True, help="run stamp, e.g. 20260710_202308")
    ap.add_argument("--include-variants", action="store_true",
                    help="also card ' (B)' and ' (Short)' outputs (default: primaries only)")
    ap.add_argument("--frames", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    cache_dir = DIAG / "cards" / args.run
    cache_dir.mkdir(parents=True, exist_ok=True)
    ground = _effects_for_run(args.run)
    clips = _run_clips(args.run, args.include_variants)
    if not clips:
        _log(f"no clips found for run {args.run}")
        return 1
    _log(f"run {args.run}: {len(clips)} clip(s) -> {cache_dir}")

    built = 0
    for clip in clips:
        stem = clip.stem
        # 1) forensics timeline (skip if cached). Our clips: no TikTok outro to
        #    trim, no OCR (we KNOW our burned text; the card's VLM reads frames
        #    anyway), no text-only LLM style profile (cards supersede it).
        tl_path = cache_dir / f"{stem}.timeline.json"
        if not tl_path.exists():
            _log(f"decomposing {stem} ...")
            tl = cf.decompose(clip, device="cpu", ocr=False, llm=False,
                              cache_dir=cache_dir)
            tl_path.write_text(json.dumps(tl, indent=2), encoding="utf-8")
        # 2) attribute card (VLM)
        card = ac.build_card(clip, n_frames=args.frames, cache_dir=cache_dir)
        if card is None:
            continue
        # 3) merge injected ground truth from effects_log (normalized-title match:
        #    log titles are raw, filenames are Stage-7-sanitized)
        _gnorm = {_norm(k): v for k, v in ground.items()}
        gt = _gnorm.get(_norm(stem)) or {}
        if gt:
            card["_ground_truth"] = gt
            (cache_dir / f"{stem}.card.json").write_text(
                json.dumps(card, indent=2, ensure_ascii=False), encoding="utf-8")
        built += 1
        if args.limit and built >= args.limit:
            break
    _log(f"done: {built}/{len(clips)} card(s) in {cache_dir}")
    return 0 if built else 1


if __name__ == "__main__":
    sys.exit(main())
