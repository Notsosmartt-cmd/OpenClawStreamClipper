#!/usr/bin/env python3
"""Vision-slot bench (Speed #3, plan-pipeline-speed-2026-07).

Stage 6 and the Stage-5.5 judge both cap their ThreadPool at 2 workers, while LM Studio
runs PARALLEL=4 slots (and Stage 7 render already uses 4). Whether raising STAGE6_WORKERS /
JUDGE_WORKERS to 3-4 actually helps depends on whether the mtmd vision encoder serializes
server-side — an EMPIRICAL question. This fires the SAME judge-shaped multi-image request
at concurrency 1/2/3/4 and reports wall-time + throughput per level, so the worker bump is
data-driven, not a guess.

Also prints a KV-headroom note: raising concurrency shares the 32768-ctx KV cache across
more slots — the one quality-relevant failure mode (context truncation). Compare the
reported prompt token counts × slots against the server's KV budget before enabling 4.

Requires: LM Studio up with a vision model loaded (or it JIT-loads on first call — slow).
Read-only; makes only inference calls. Usage:
  python scripts/research/bench_vision_slots.py [--clip path.mp4] [--frames 4] [--reps 4]
"""
from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
URL = "http://localhost:1234/v1/chat/completions"


def _model() -> str:
    try:
        cfg = json.loads((REPO / "config" / "models.json").read_text(encoding="utf-8"))
        return cfg.get("vision_model") or cfg.get("text_model") or "qwen/qwen3.6-35b-a3b"
    except Exception:
        return "qwen/qwen3.6-35b-a3b"


def _extract_frames(clip: Path, n: int, outdir: Path) -> list[Path]:
    outdir.mkdir(parents=True, exist_ok=True)
    # n evenly-spaced 960x540 frames (matches Stage 5's scale)
    out = outdir / "benchframe_%02d.jpg"
    cmd = ["ffmpeg", "-y", "-i", str(clip), "-vf",
           f"fps=1,scale=960:540", "-frames:v", str(n), "-q:v", "2", str(out)]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return sorted(outdir.glob("benchframe_*.jpg"))[:n]


def _payload(model: str, frames: list[Path]) -> dict:
    content = [{"type": "text", "text": "Describe what is happening across these time-ordered "
                                        "frames in one sentence, then rate 1-10 how engaging it is."}]
    for fp in frames:
        b64 = base64.b64encode(fp.read_bytes()).decode()
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
    return {"model": model, "messages": [{"role": "user", "content": content}],
            "max_tokens": 200, "temperature": 0}


def _one_call(body: bytes) -> tuple[float, int]:
    t0 = time.time()
    req = urllib.request.Request(URL, data=body, headers={"Content-Type": "application/json"},
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            d = json.loads(r.read().decode())
        toks = (d.get("usage") or {}).get("total_tokens", 0)
        return time.time() - t0, toks
    except Exception as e:
        print(f"    call failed: {type(e).__name__}: {e}", file=sys.stderr)
        return time.time() - t0, -1


def main() -> int:
    ap = argparse.ArgumentParser(description="Bench vision concurrency slots (Speed #3)")
    ap.add_argument("--clip", default="")
    ap.add_argument("--frames", type=int, default=4)
    ap.add_argument("--reps", type=int, default=4, help="calls per concurrency level")
    ap.add_argument("--levels", default="1,2,3,4")
    a = ap.parse_args()

    clip = Path(a.clip) if a.clip else next(iter(sorted(REPO.glob("clips/**/*.mp4"))), None)
    if not clip or not clip.exists():
        print("[bench] no clip found — pass --clip PATH"); return 1
    model = _model()
    frames = _extract_frames(clip, a.frames, REPO / "clips" / ".diagnostics" / "_benchframes")
    if not frames:
        print("[bench] frame extraction failed"); return 1
    body = json.dumps(_payload(model, frames)).encode()
    prompt_imgs = len(frames)
    print(f"[bench] model={model} frames/call={prompt_imgs} reps={a.reps} clip={clip.name}")
    print(f"[bench] warming up (JIT-load if needed)...")
    _one_call(body)  # warm

    levels = [int(x) for x in a.levels.split(",") if x.strip().isdigit()]
    print(f"[bench] {'conc':>4} {'wall_s':>8} {'calls/s':>8} {'x vs conc1':>11}")
    base_rate = None
    for c in levels:
        n = max(c, a.reps)
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=c) as ex:
            list(ex.map(lambda _: _one_call(body), range(n)))
        wall = time.time() - t0
        rate = n / wall if wall > 0 else 0.0
        if c == 1:
            base_rate = rate
        speedup = (rate / base_rate) if base_rate else 1.0
        print(f"[bench] {c:>4} {wall:>8.1f} {rate:>8.2f} {speedup:>10.2f}x")
    print("[bench] If calls/s plateaus at conc=2, the encoder serializes → keep workers=2.")
    print("[bench] If it scales to 3-4, set STAGE6_WORKERS/JUDGE_WORKERS accordingly")
    print("[bench] (after confirming KV headroom: prompt_tokens × slots ≤ server KV budget).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
