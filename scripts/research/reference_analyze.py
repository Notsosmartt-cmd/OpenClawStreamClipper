#!/usr/bin/env python3
"""reference_analyze.py — ONE-BUTTON analyze for the Reference Lab tab.

Chains the loop's ingest steps per clip so the owner never runs them separately:
    decompose (only if the timeline is missing)  ->  build attribute card

Selection semantics match the Clipper UX:
  --clips a,b,c   re-ANALYZE exactly these (cards rebuilt; decompose only if missing)
  --all-new       only clips that don't have a card yet

Usage:
  python scripts/research/reference_analyze.py --clips "NameA,NameB"
  python scripts/research/reference_analyze.py --all-new
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

import clip_forensics as cf     # noqa: E402
import attribute_cards as ac    # noqa: E402

CACHE = cf.REF_DIR / ".cache"
_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}


def _resolve(stem_or_name: str) -> Path | None:
    c = cf._resolve_clip(stem_or_name)
    if c:
        return c
    for ext in (".mp4", ".MP4", ".mkv", ".webm", ".mov"):
        c = cf._resolve_clip(stem_or_name + ext)
        if c:
            return c
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Analyze reference clips (decompose-if-needed + card)")
    ap.add_argument("--clips", default="", help="comma-separated names/stems to (re)analyze")
    ap.add_argument("--all-new", action="store_true", help="only clips without a card yet")
    ap.add_argument("--trim-end", type=str, default="auto",
                    help="seconds, or 'auto' (default): per-clip TikTok-outro detection; "
                         "unsure falls back to the 4s blanket")
    args = ap.parse_args()

    targets: list[Path] = []
    if args.clips:
        for s in args.clips.split(","):
            s = s.strip()
            if not s:
                continue
            c = _resolve(s)
            if c:
                targets.append(c)
            else:
                print(f"[analyze] clip not found, skipping: {s}", flush=True)
        force_card = True   # explicit selection = rebuild the card
    else:
        targets = [f for f in sorted(cf.REF_DIR.iterdir())
                   if f.is_file() and f.suffix.lower() in _EXT
                   and not (CACHE / f"{f.stem}.card.json").exists()]
        force_card = False

    if not targets:
        print("[analyze] nothing to analyze (everything already has a card)", flush=True)
        return 0
    CACHE.mkdir(parents=True, exist_ok=True)
    print(f"[analyze] {len(targets)} clip(s) — decompose-if-missing, then card "
          f"({'rebuild' if force_card else 'new only'})", flush=True)

    done = 0
    for i, clip in enumerate(targets, 1):
        stem = clip.stem
        print(f"[{i}/{len(targets)}] {clip.name}", flush=True)
        try:
            tl_path = CACHE / f"{stem}.timeline.json"
            if not tl_path.exists():
                print("    decomposing (audio events, cuts, motion, captions — CPU, ~1-3 min)...",
                      flush=True)
                tl = cf.decompose(clip, device="cpu", ocr=True, llm=False,
                                  trim_end=args.trim_end)
                tl_path.write_text(json.dumps(tl, indent=2), encoding="utf-8")
            card_path = CACHE / f"{stem}.card.json"
            if card_path.exists() and not force_card:
                print("    card exists — skipping", flush=True)
                done += 1
                continue
            print("    building attribute card (one vision call)...", flush=True)
            card = ac.build_card(clip)
            if card:
                print(f"    ✓ card: category={card.get('category')} "
                      f"confidence={card.get('confidence')}", flush=True)
                done += 1
            else:
                print("    card FAILED (see above)", flush=True)
        except Exception as e:  # noqa: BLE001 — one bad clip never stops the batch
            print(f"    FAILED: {type(e).__name__}: {e}", flush=True)
    print(f"[analyze] done: {done}/{len(targets)} analyzed", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
