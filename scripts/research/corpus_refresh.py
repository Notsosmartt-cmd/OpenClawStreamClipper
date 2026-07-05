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

Speed (2026-07-04, owner: "future runs faster, use more cores, don't compromise
quality"): two changes, neither affects output quality —
  * BATCH mode (default): clips are decomposed IN-PROCESS in one loop, so CLAP /
    whisper / EasyOCR load ONCE per run instead of once per clip (the old per-clip
    subprocess paid ~1.5-2 min of model reload every clip). The per-STAGE hang
    watchdogs (clip_forensics._with_deadline) still bound every stage; a clip that
    fails in-process is retried once in an isolated subprocess (the old path).
    `--no-batch` restores full per-clip isolation.
  * THREADS: OMP/MKL threads default to min(16, cpu_count) instead of whatever the
    caller happened to export (an earlier run inherited OMP_NUM_THREADS=2 on a
    24-core i9). Thread count changes SPEED only — same models, same parameters,
    same detections. Override with CLIP_REFRESH_THREADS.

Usage:
  python scripts/research/corpus_refresh.py              # full incremental pass
  python scripts/research/corpus_refresh.py --max 6      # bound this pass to 6 clips
  python scripts/research/corpus_refresh.py --trim-end 0 # clips WITHOUT the TikTok outro
  python scripts/research/corpus_refresh.py --llm        # also per-clip LLM style profiles
  python scripts/research/corpus_refresh.py --no-batch   # old per-clip-subprocess mode
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def _configure_threads() -> int:
    """Set math-library thread counts BEFORE numpy/torch are imported (they read
    these at import time). min(16, cores): CPU torch inference scales well to the
    P-core count and flattens beyond it. Forced (not setdefault) so an accidental
    OMP_NUM_THREADS=2 in the calling shell can't silently throttle the batch."""
    try:
        n = int(os.environ.get("CLIP_REFRESH_THREADS", "") or 0)
    except ValueError:
        n = 0
    if n <= 0:
        n = max(4, min(16, os.cpu_count() or 8))
    for var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
        os.environ[var] = str(n)
    os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
    return n

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


def _subprocess_decompose(m: Path, args) -> bool:
    """Old fully-isolated path: one subprocess per clip (pays a model reload)."""
    cmd = [PY, str(HERE.parent / "clip_forensics.py"), "--clip", m.name, "--ocr",
           "--trim-end", str(args.trim_end),
           "--out", str(CACHE / f"{m.stem}.timeline.json")]
    if not args.llm:
        cmd.append("--no-llm")
    return _run(cmd, args.clip_timeout, m.stem[:24])


def _batch_decompose(pending: list[Path], args) -> int:
    """Fast path: import clip_forensics ONCE and loop clips in-process, so CLAP /
    whisper / EasyOCR weights stay resident across the whole batch (~1.5-2 min
    saved per clip vs the subprocess path). Same functions, same parameters, same
    outputs — only the redundant reloads are gone. Hang safety is unchanged: every
    stage inside decompose() already runs under _with_deadline (cap -> abandon ->
    partial result). A clip that raises in-process gets ONE retry in an isolated
    subprocess before being skipped."""
    sys.path.insert(0, str(HERE.parent))
    import clip_forensics as cf  # heavy deps load lazily inside decompose()
    done = 0
    for i, m in enumerate(pending, 1):
        print(f"[refresh] {i}/{len(pending)} decomposing {m.name} (batch; models stay resident) ...",
              flush=True)
        t0 = time.time()
        try:
            tl = cf.decompose(m, device="cpu", ocr=True, llm=args.llm,
                              trim_start=0.0, trim_end=args.trim_end)
            (CACHE / f"{m.stem}.timeline.json").write_text(
                json.dumps(tl, indent=2), encoding="utf-8")
            bad = {k: v for k, v in (tl.get("_stages") or {}).items()
                   if v in ("timeout", "error")}
            print(f"  [{m.stem[:24]}] ok ({time.time()-t0:.0f}s) "
                  f"events={len(tl.get('audio_events') or [])} words={tl.get('n_words')} "
                  f"cuts={len(tl.get('cuts') or [])}"
                  + (f"  WARN partial stages: {bad}" if bad else ""), flush=True)
            done += 1
        except Exception as e:
            print(f"  [{m.stem[:24]}] in-process failed ({type(e).__name__}: {e}) — "
                  f"retrying isolated ...", flush=True)
            if _subprocess_decompose(m, args):
                done += 1
    return done


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
    ap.add_argument("--no-batch", action="store_true",
                    help="per-clip subprocess isolation (slow; reloads models each clip)")
    args = ap.parse_args()

    threads = _configure_threads()
    pending = _pending()
    total_media = len(_media())
    print(f"[refresh] corpus: {total_media} media, {total_media - len(pending)} cached, "
          f"{len(pending)} NEW to decompose | threads={threads} "
          f"mode={'subprocess' if args.no_batch else 'batch'}"
          + (f" (bounding to {args.max})" if args.max and len(pending) > args.max else ""),
          flush=True)
    if args.max:
        pending = pending[: args.max]

    if args.no_batch:
        done = sum(1 for m in pending if _subprocess_decompose(m, args))
    else:
        done = _batch_decompose(pending, args)
    print(f"[refresh] decomposed {done}/{len(pending)}", flush=True)

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
