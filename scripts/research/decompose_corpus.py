#!/usr/bin/env python3
"""decompose_corpus.py — R0 batch: decompose reference clips that lack a timeline.

Standalone (mirrors our_clip_cards / corpus_diff) so the Reference Lab dashboard
tab — and the CLI — can run R0 over the whole corpus without a shell loop.
Each clip's timeline is written to reference_clips/.cache/<stem>.timeline.json.
CPU + no-LLM by default (the R1 attribute cards supersede clip_forensics' old
text-only style_profile); --ocr on by default for caption-density facts.

Usage:
  python scripts/research/decompose_corpus.py --missing        # only un-decomposed
  python scripts/research/decompose_corpus.py --all            # (re)do everything
  python scripts/research/decompose_corpus.py --clip NAME
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE.parent))

import clip_forensics as cf  # noqa: E402

CACHE = cf.REF_DIR / ".cache"
_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}


def _targets(args) -> list[Path]:
    if args.clip:
        c = cf._resolve_clip(args.clip)
        return [c] if c else []
    vids = [f for f in sorted(cf.REF_DIR.iterdir())
            if f.is_file() and f.suffix.lower() in _EXT]
    if args.missing:
        vids = [f for f in vids if not (CACHE / f"{f.stem}.timeline.json").exists()]
    return vids


def main() -> int:
    ap = argparse.ArgumentParser(description="R0 batch decomposer for reference clips")
    ap.add_argument("--missing", action="store_true", help="only clips lacking a timeline")
    ap.add_argument("--all", action="store_true", help="every clip (re-decompose)")
    ap.add_argument("--clip", help="one clip name/path")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--ocr", dest="ocr", action="store_true", default=True)
    ap.add_argument("--no-ocr", dest="ocr", action="store_false")
    ap.add_argument("--trim-end", type=str, default="auto",
                    help="drop the last N s (TikTok download outro), or 'auto' "
                         "(default): detect the outro per clip — trims exactly the "
                         "clips that have one, unsure falls back to 4s")
    ap.add_argument("--device", default="auto", choices=("auto", "cpu", "cuda"),
                    help="inference device for CLAP/whisper/EasyOCR. Default auto "
                         "(owner workflow: the Lab never runs beside the pipeline, "
                         "so the card is normally free): cuda when >=3GB VRAM free, "
                         "else cpu. Force with cpu/cuda.")
    args = ap.parse_args()
    if not (args.missing or args.all or args.clip):
        args.missing = True  # safest default

    CACHE.mkdir(parents=True, exist_ok=True)
    targets = _targets(args)
    if not targets:
        print("[decompose_corpus] nothing to do (all decomposed?)")
        return 0
    device = cf.resolve_lab_device(args.device)
    print(f"[decompose_corpus] {len(targets)} clip(s) to decompose "
          f"(ocr={args.ocr}, no-llm, device={device}, trim_end={args.trim_end})", flush=True)

    done = 0
    for i, clip in enumerate(targets, 1):
        stem = clip.stem
        print(f"[{i}/{len(targets)}] {clip.name} ...", flush=True)
        try:
            tl = cf.decompose(clip, device=device, ocr=args.ocr, llm=False,
                              trim_end=args.trim_end)
            (CACHE / f"{stem}.timeline.json").write_text(
                json.dumps(tl, indent=2), encoding="utf-8")
            done += 1
            print(f"    events={len(tl.get('audio_events', []))} "
                  f"cuts={len(tl.get('cuts', []))} words={tl.get('n_words')} "
                  f"dur={tl.get('duration_s')}s", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"    FAILED: {type(e).__name__}: {e}", flush=True)
        if args.limit and done >= args.limit:
            break
    print(f"[decompose_corpus] done: {done}/{len(targets)} decomposed", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
