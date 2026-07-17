#!/usr/bin/env python3
"""Retroactive clip scorer — judge FINISHED clips without reprocessing VODs.

For clips whose pipeline scores were lost (e.g. the 2026-07-16 batches, whose
run traces were never written), this derives a fresh 0-10 judge score from the
rendered file itself: transcribe the clip's own audio (small Whisper, CPU —
never competes with a resident LLM for VRAM) and have the big text judge score
postability in batches of 8 (the S4.5 pattern, adapted from the Reference
Lab's "judge the artifact, not the plan" approach). Results append to
``clips/.diagnostics/clip_scores.jsonl`` — the same index Stage 7 now writes
at render time — so the poster's ★ Top-rated filter picks them up with no
further wiring.

Usage:
    python scripts/research/score_clips.py            # score every unscored clip
    python scripts/research/score_clips.py --limit 10 # first 10 (smoke)
    python scripts/research/score_clips.py --rescore  # ignore existing judge rows

Bounded by design: sequential, hard per-call timeouts, one line of progress
per clip, exits when the folder is covered.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent          # scripts/research
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))

import re  # noqa: E402

import requests  # noqa: E402

import lmstudio  # noqa: E402  (loads_lenient handles OBJECT replies; arrays here)


def _parse_array(content: str) -> list | None:
    """Parse a top-level JSON ARRAY from an LLM reply (loads_lenient is
    object-only — its {…} blob extraction mangles arrays)."""
    t = (content or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*|\s*```\s*$", "", t, flags=re.S).strip()
    try:
        v = json.loads(t)
        if isinstance(v, list):
            return v
    except Exception:
        pass
    a, b = t.find("["), t.rfind("]")
    if a != -1 and b > a:
        try:
            v = json.loads(re.sub(r",(\s*[}\]])", r"\1", t[a:b + 1]))
            if isinstance(v, list):
                return v
        except Exception:
            pass
    obj = lmstudio.loads_lenient(t)   # {"clips": [...]} style wrap
    if isinstance(obj, dict):
        for vv in obj.values():
            if isinstance(vv, list):
                return vv
    return None

CLIPS_DIR = Path(str(REPO / "clips"))
DIAG = CLIPS_DIR / ".diagnostics"
SCORES = DIAG / "clip_scores.jsonl"
VIDEO_EXTS = (".mp4", ".mkv", ".webm", ".mov")
GROUP = 8
JUDGE_TIMEOUT = 240

_PROMPT = """You are a harsh short-form clip judge for TikTok/Reels. Below are {n} finished clips (transcript of the clip's own audio + duration). Score each for POSTABILITY: does it have a hook, a clear payoff, and energy worth a stranger's 30 seconds? Typical folder average should land near 5.

Return ONLY a JSON array, one object per clip, same order:
[{{"id": 1, "score": 7.5, "rationale": "<= 12 words"}}, ...]

Scores are 0-10 (use decimals; reserve 8+ for genuinely strong clips).

{packets}"""


def _load_existing() -> set[str]:
    done: set[str] = set()
    if SCORES.exists():
        for ln in SCORES.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(ln)
            except Exception:
                continue
            if row.get("clip") and row.get("judge") is not None:
                done.add(str(row["clip"]).casefold())
    return done


def _clips_to_score(rescore: bool) -> list[Path]:
    done = set() if rescore else _load_existing()
    out = []
    for d in (CLIPS_DIR, CLIPS_DIR / "posted_clips"):
        if not d.exists():
            continue
        for f in sorted(d.iterdir()):
            if f.is_file() and f.suffix.lower() in VIDEO_EXTS \
                    and f.stem.casefold() not in done:
                out.append(f)
    return out


def _transcribe_all(clips: list[Path], model_size: str) -> dict[str, dict]:
    """{stem: {text, duration_s}} via faster-whisper on CPU (VRAM-neutral —
    the 35B judge can stay resident)."""
    from faster_whisper import WhisperModel
    print(f"[whisper] loading {model_size} (cpu/int8)...", flush=True)
    wm = WhisperModel(model_size, device="cpu", compute_type="int8")
    out: dict[str, dict] = {}
    t0 = time.time()
    for i, f in enumerate(clips, 1):
        try:
            segs, info = wm.transcribe(str(f), beam_size=1, vad_filter=True)
            text = " ".join(s.text.strip() for s in segs).strip()
            out[f.stem] = {"text": text[:2000],
                           "duration_s": round(info.duration, 1)}
        except Exception as e:  # noqa: BLE001
            print(f"[whisper] {i}/{len(clips)} FAILED {f.name}: {e}", flush=True)
            continue
        print(f"[whisper] {i}/{len(clips)} {f.stem[:46]} "
              f"({out[f.stem]['duration_s']}s, {len(out[f.stem]['text'])} ch)",
              flush=True)
    print(f"[whisper] done in {time.time() - t0:.0f}s", flush=True)
    return out


def _judge_group(url: str, model: str, group: list[tuple[Path, dict]]) -> dict:
    packets = []
    for i, (f, tx) in enumerate(group, 1):
        text = tx["text"] or "(no speech detected)"
        packets.append(f"--- clip {i} ({tx['duration_s']}s) ---\n{text}")
    prompt = _PROMPT.format(n=len(group), packets="\n\n".join(packets))
    r = requests.post(
        f"{url}/v1/chat/completions",
        json={"model": model, "temperature": 0,
              "max_tokens": 220 * len(group),
              "messages": [{"role": "user", "content": prompt}]},
        timeout=JUDGE_TIMEOUT,
    )
    r.raise_for_status()
    msg = r.json()["choices"][0]["message"]
    content = msg.get("content") or msg.get("reasoning_content") or ""
    arr = _parse_array(content)
    verdicts: dict = {}
    if isinstance(arr, list):
        for v in arr:
            try:
                idx = int(v.get("id"))
                verdicts[idx] = {"score": float(v.get("score")),
                                 "rationale": str(v.get("rationale", ""))[:120]}
            except (TypeError, ValueError, AttributeError):
                continue
    if not verdicts:
        print(f"[judge] parse produced nothing — raw head: {content[:300]!r}",
              flush=True)
    return verdicts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--rescore", action="store_true")
    ap.add_argument("--whisper", default="base")
    ap.add_argument("--model", default="")
    ap.add_argument("--url", default="http://localhost:1234")
    args = ap.parse_args()

    model = args.model
    if not model:
        try:
            cfg = json.loads((REPO / "config" / "models.json").read_text(encoding="utf-8"))
            model = cfg.get("vision_model") or "qwen/qwen3.6-35b-a3b"
        except Exception:
            model = "qwen/qwen3.6-35b-a3b"

    clips = _clips_to_score(args.rescore)
    if args.limit:
        clips = clips[:args.limit]
    if not clips:
        print("nothing to score — every clip already has a judge row")
        return 0
    print(f"scoring {len(clips)} clips with {model} (whisper={args.whisper})",
          flush=True)

    tx = _transcribe_all(clips, args.whisper)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    scored = failed = 0
    DIAG.mkdir(parents=True, exist_ok=True)
    todo = [(f, tx[f.stem]) for f in clips if f.stem in tx]
    for gi in range(0, len(todo), GROUP):
        group = todo[gi:gi + GROUP]
        try:
            verdicts = _judge_group(args.url, model, group)
        except Exception as e:  # noqa: BLE001
            print(f"[judge] group {gi // GROUP + 1} FAILED: {e}", flush=True)
            failed += len(group)
            continue
        with open(SCORES, "a", encoding="utf-8") as fh:
            for i, (f, _t) in enumerate(group, 1):
                v = verdicts.get(i)
                if not v:
                    failed += 1
                    continue
                fh.write(json.dumps({
                    "clip": f.stem, "score": None, "judge": v["score"],
                    "category": None, "run": f"retro_{stamp}",
                    "ts": int(time.time()), "src": "retro_judge",
                    "rationale": v["rationale"],
                }) + "\n")
                scored += 1
        print(f"[judge] {min(gi + GROUP, len(todo))}/{len(todo)} judged", flush=True)
    print(f"DONE: {scored} scored, {failed} failed, "
          f"{len(clips) - len(todo)} untranscribable", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
