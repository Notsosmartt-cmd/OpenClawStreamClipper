#!/usr/bin/env python3
"""ONE command to make the reference corpus 'learn' from newly added clips.

The owner drops competitor clips into reference_clips/ and runs this (or asks the
agent to). It is INCREMENTAL and RESUMABLE — already-decomposed clips are skipped,
so re-running after adding 5 clips only processes those 5. There is no schedule:
run it whenever a meaningful batch of new clips lands.

What one pass does, in order:
  1. DECOMPOSE every media file with no cached timeline (clip_forensics: CLAP audio
     events, whisper words, cuts, motion, caption OCR). Default `--trim-end 3.5`
     cuts the TikTok download OUTRO (logo + sound banner auto-appended to
     downloaded TikToks — owner flagged 2026-07-04) so it never pollutes the
     analysis. Per-clip subprocess with a hard timeout: a wedged clip is skipped,
     never hangs the batch.
  2. SEED a draft .notes.json for each new clip (corpus_eval --draft-from-cache)
     for the owner to correct — corrected notes become detection precision/recall.
  3. RE-DISTILL the corpus-level learnings:
       caption_style.py    -> config/caption_style.json   (competitor caption VOICE)
       transcript_value.py -> transcript- vs reaction-carried labels (anomaly eval set)
       corpus_eval.py      -> detection reliability report (needs corrected notes)

Honest scope note: steps 1-3 are batch DISTILLATION, not continuous ML training.
The actually-trained component (the Phase-4 selection ranker) trains via
scripts/research/fit_ranker.py once labelled runs exist — separate pipeline.

Usage:
  python scripts/research/corpus_refresh.py              # full incremental pass
  python scripts/research/corpus_refresh.py --max 6      # bound this pass to 6 clips
  python scripts/research/corpus_refresh.py --trim-end 0 # clips WITHOUT the TikTok outro
  python scripts/research/corpus_refresh.py --llm        # also per-clip LLM style profiles
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
REPO = HERE.parents[2]
REF = REPO / "reference_clips"
CACHE = REF / ".cache"
PY = sys.executable
MEDIA_EXT = {".mp4", ".mov", ".mkv", ".webm"}


def _media() -> list[Path]:
    return sorted(p for p in REF.iterdir()
                  if p.is_file() and p.suffix.lower() in MEDIA_EXT)


def _pending() -> list[Path]:
    return [m for m in _media() if not (CACHE / f"{m.stem}.timeline.json").exists()]


def _run(cmd: list[str], timeout: int, label: str) -> bool:
    t0 = time.time()
    try:
        r = subprocess.run(cmd, timeout=timeout, capture_output=True, text=True)
        tail = (r.stderr or r.stdout or "").strip().splitlines()
        if tail:
            print(f"    {tail[-1][:140]}")
        print(f"  [{label}] {'ok' if r.returncode == 0 else f'exit {r.returncode}'} "
              f"({time.time()-t0:.0f}s)")
        return r.returncode == 0
    except subprocess.TimeoutExpired:
        print(f"  [{label}] TIMEOUT after {timeout}s — skipped (batch continues)")
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description="Incremental corpus learn-refresh")
    ap.add_argument("--max", type=int, default=0, help="max clips to decompose this pass (0 = all)")
    ap.add_argument("--trim-end", type=float, default=3.5,
                    help="seconds cut from clip end (TikTok download outro; default 3.5)")
    ap.add_argument("--llm", action="store_true",
                    help="also synthesize per-clip LLM style profiles (slower)")
    ap.add_argument("--clip-timeout", type=int, default=900)
    ap.add_argument("--skip-distill", action="store_true",
                    help="only decompose; skip the corpus-level re-distillation")
    args = ap.parse_args()

    pending = _pending()
    total_media = len(_media())
    print(f"[refresh] corpus: {total_media} media, {total_media - len(pending)} cached, "
          f"{len(pending)} NEW to decompose"
          + (f" (bounding to {args.max})" if args.max and len(pending) > args.max else ""))
    if args.max:
        pending = pending[: args.max]

    done = 0
    for i, m in enumerate(pending, 1):
        print(f"[refresh] {i}/{len(pending)} decomposing {m.name} ...")
        cmd = [PY, str(HERE.parent / "clip_forensics.py"), "--clip", m.name, "--ocr",
               "--trim-end", str(args.trim_end),
               "--out", str(CACHE / f"{m.stem}.timeline.json")]
        if not args.llm:
            cmd.append("--no-llm")
        if _run(cmd, args.clip_timeout, m.stem[:24]):
            done += 1
    print(f"[refresh] decomposed {done}/{len(pending)}")

    if args.skip_distill:
        return 0

    print("[refresh] seeding draft annotations for new clips ...")
    _run([PY, str(HERE.parent / "corpus_eval.py"), "--draft-from-cache"], 120, "draft-notes")

    print("[refresh] re-distilling caption voice (config/caption_style.json) ...")
    _run([PY, str(HERE.parent / "caption_style.py")], 300, "caption-style")

    print("[refresh] re-classifying transcript value (anomaly eval set) ...")
    _run([PY, str(HERE.parent / "transcript_value.py")], 900, "transcript-value")

    print("[refresh] detection reliability (needs owner-corrected notes) ...")
    _run([PY, str(HERE.parent / "corpus_eval.py")], 120, "corpus-eval")

    remaining = len(_pending())
    print(f"[refresh] DONE. {remaining} clip(s) still pending"
          + (" — re-run to continue." if remaining else " — corpus fully processed."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
