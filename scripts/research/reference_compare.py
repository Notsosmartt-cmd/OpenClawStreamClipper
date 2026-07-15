#!/usr/bin/env python3
"""reference_compare.py — ONE-BUTTON compare for the Reference Lab tab.

Chains the comparison half of the loop so the owner presses one button:
    card OUR clips for the run (skip clips already carded)  ->  gap report

Usage:
  python scripts/research/reference_compare.py --run 20260711_172834
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
import our_clip_cards as occ     # noqa: E402
import corpus_diff               # noqa: E402


def _card_run(run: str, device: str = "cpu") -> int:
    """Card OUR clips for one run (missing only). Returns the number carded."""
    cache_dir = occ.DIAG / "cards" / run
    cache_dir.mkdir(parents=True, exist_ok=True)
    ground = occ._effects_for_run(run)
    clips = occ._run_clips(run, include_variants=False)
    if not clips:
        print(f"[compare] no clips found for run {run} — clip a VOD first "
              f"(or its mp4s were moved/archived off-repo)", flush=True)
        return 0

    todo = [c for c in clips if not (cache_dir / f"{c.stem}.card.json").exists()]
    print(f"[compare] run {run}: {len(clips)} clip(s), {len(todo)} need carding "
          f"({len(clips) - len(todo)} cached)", flush=True)

    carded = 0
    for i, clip in enumerate(todo, 1):
        stem = clip.stem
        print(f"[{i}/{len(todo)}] carding OUR clip: {stem[:55]}", flush=True)
        try:
            tl_path = cache_dir / f"{stem}.timeline.json"
            if not tl_path.exists():
                print(f"    decomposing ({device})...", flush=True)
                tl = cf.decompose(clip, device=device, ocr=False, llm=False,
                                  cache_dir=cache_dir)
                tl_path.write_text(json.dumps(tl, indent=2), encoding="utf-8")
            card = ac.build_card(clip, cache_dir=cache_dir)
            if card:
                _sn = occ._norm(stem)
                gt = next((v for k, v in ground.items()
                           if occ._title_match(_sn, occ._norm(k))), {})
                if gt:
                    card["_ground_truth"] = gt
                    (cache_dir / f"{stem}.card.json").write_text(
                        json.dumps(card, indent=2, ensure_ascii=False), encoding="utf-8")
                    carded += 1
        except Exception as e:  # noqa: BLE001
            print(f"    FAILED: {type(e).__name__}: {e}", flush=True)
    return carded


def main() -> int:
    ap = argparse.ArgumentParser(description="Card our clips (missing only) + generate the gap report")
    ap.add_argument("--run", help="single clip-run stamp (effects_log)")
    ap.add_argument("--runs", help="comma-separated run stamps — aggregate several runs' clips "
                    "into ONE comparison against the reference corpus")
    # cpu default: interleaves decompose with 35B card calls (see reference_analyze)
    ap.add_argument("--device", default="cpu", choices=("auto", "cpu", "cuda"),
                    help="cpu (default — card calls need LM Studio on the CUDA card "
                         "mid-job); auto/cuda for decompose-heavy runs at your own risk")
    args = ap.parse_args()
    runs = [r.strip() for r in (args.runs or "").split(",") if r.strip()] or \
           ([args.run.strip()] if args.run else [])
    if not runs:
        print("[compare] need --run or --runs", flush=True)
        return 1

    device = cf.resolve_lab_device(args.device)
    for run in runs:
        _card_run(run, device=device)

    print(f"[compare] generating the gap report over {len(runs)} run(s)...", flush=True)
    sys.argv = ["corpus_diff", "--runs", ",".join(runs)]
    return corpus_diff.main()


if __name__ == "__main__":
    sys.exit(main())
