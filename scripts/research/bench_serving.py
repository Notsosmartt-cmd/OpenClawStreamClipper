#!/usr/bin/env python3
"""Speed Wave 2 — serving-stack benchmark (plan-serving-stack-2026-07 §P0.1).

Serial-only. Measures the LM Studio server under whatever model/config is CURRENTLY
loaded (load/unload is done OUT of band via `lms`). Never batches concurrent requests
(the §3 non-determinism landmine only triggers on co-batched requests — this stays serial).

Modes:
  decode   Replay Pass-B-shaped prompts serially; report per-call wall, TTFT,
           decode tok/s, prefill share, and any speculative/draft acceptance stats.
           This is the S1 (speculative decoding) gate: run once no-draft, once with
           the draft loaded, compare decode tok/s.
  ttft     C2(a) prefix-reuse probe. Synthetic (static block S) + (variable tail):
             1. S+V1 (cold)  2. S+V2 (reuse probe)  3. X+V3 (control: S w/ token-1 changed)
           Reuse fires iff TTFT2 << TTFT3 ~= TTFT1. Then an ALTERNATION probe
           (S, CARD, S) to see if a foreign prompt in between evicts the shared prefix.
  prefill  One large prompt, max_tokens=1: TTFT ~= pure prefill time. For the
           evalBatchSize A/B (Phase 3 / Proposal P).

Endpoints: prefers LM Studio native  POST /api/v0/chat/completions  (returns a `stats`
block: tokens_per_second, time_to_first_token, generation_time, + draft counters when
speculative decoding is active). Falls back to /v1/chat/completions with streaming and
client-side TTFT timing.

Prompts for `decode`:
  --prompts <jsonl>   real dumped prompts (run once with CLIP_PASSB_DUMP_PROMPTS=1)
  (default)           SYNTHETIC-realistic: config/patterns.json catalog (static) +
                      real cached-transcript word-windows (variable), in production order
                      (variable first, catalog last). Clearly labeled synthetic.

All bounded: per-call timeout default 300 s, no retry loops, capped prompt count.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "lib"))

DEFAULT_MODEL = "qwen/qwen3.6-35b-a3b"
BASE = "http://localhost:1234"


# --------------------------------------------------------------------------- I/O
def _post(path: str, payload: dict, timeout: float) -> tuple[dict | None, float]:
    """Non-streaming POST. Returns (json, wall_seconds)."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = json.loads(r.read().decode("utf-8"))
        return body, time.perf_counter() - t0
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        print(f"  ! request error: {e}", file=sys.stderr)
        return None, time.perf_counter() - t0


def _post_stream(path: str, payload: dict, timeout: float) -> tuple[float, int, float]:
    """Streaming POST. Returns (ttft_s, completion_tokens, wall_s). Client-side TTFT."""
    payload = {**payload, "stream": True}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    ttft = -1.0
    toks = 0
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            for raw in r:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                chunk = line[5:].strip()
                if chunk == "[DONE]":
                    break
                if ttft < 0:
                    ttft = time.perf_counter() - t0
                try:
                    obj = json.loads(chunk)
                    delta = obj["choices"][0].get("delta", {}).get("content")
                    if delta:
                        toks += 1  # rough: 1 SSE content chunk ~ 1 token
                except Exception:
                    pass
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"  ! stream error: {e}", file=sys.stderr)
    return ttft, toks, time.perf_counter() - t0


def call(model: str, prompt: str, max_tokens: int, timeout: float) -> dict:
    """One serial completion. Prefers /api/v0 stats; falls back to streamed /v1."""
    payload = {"model": model, "temperature": 0.0, "max_tokens": max_tokens,
               "messages": [{"role": "user", "content": prompt}]}
    body, wall = _post("/api/v0/chat/completions", payload, timeout)
    if body and isinstance(body.get("stats"), dict):
        s = body["stats"]
        # Surface any speculative/draft acceptance counters LM Studio exposes.
        draft = {k: v for k, v in s.items()
                 if any(t in k.lower() for t in ("draft", "accept", "spec"))}
        ttft = s.get("time_to_first_token")
        gen = s.get("generation_time")
        tps = s.get("tokens_per_second")
        ntok = None
        usage = body.get("usage") or {}
        ntok = usage.get("completion_tokens")
        return {"ok": True, "wall": wall, "ttft": ttft, "gen_time": gen,
                "tok_s": tps, "ntok": ntok, "draft": draft, "src": "v0"}
    # Fallback: streamed /v1 with client TTFT.
    ttft, toks, wall = _post_stream("/v1/chat/completions", payload, timeout)
    tok_s = (toks / (wall - ttft)) if (ttft > 0 and wall > ttft) else None
    return {"ok": ttft > 0, "wall": wall, "ttft": ttft, "gen_time": (wall - ttft),
            "tok_s": tok_s, "ntok": toks, "draft": {}, "src": "v1-stream"}


# ---------------------------------------------------------------- prompt sources
def _load_catalog() -> str:
    """Static block ~ the real Pass-B PATTERN CATALOG (config/patterns.json rendered)."""
    pj = REPO / "config" / "patterns.json"
    try:
        data = json.loads(pj.read_text(encoding="utf-8"))
        lines = ["PATTERN CATALOG (evaluate against these — pick the best fit):"]
        pats = data.get("patterns", data) if isinstance(data, dict) else data
        it = pats.items() if isinstance(pats, dict) else enumerate(pats)
        for k, v in it:
            pid = v.get("id", k) if isinstance(v, dict) else k
            sig = v.get("signature", v.get("description", "")) if isinstance(v, dict) else str(v)
            ex = v.get("examples", "") if isinstance(v, dict) else ""
            lines.append(f"- {pid}: {sig}")
            if ex:
                lines.append(f"    examples: {ex}")
        return "\n".join(lines)
    except Exception:
        # Bundled fallback ~1.5k tokens.
        return ("PATTERN CATALOG:\n" + "\n".join(
            f"- pattern_{i}: a named interaction shape with a setup, a payoff, and social "
            f"dynamics that make it clip-worthy for short-form audiences." for i in range(40)))


def _load_transcript_windows(n: int, words_per: int = 1400) -> list[str]:
    """Variable blocks: real cached-transcript word-windows (production chunk size ~1200-1700)."""
    tdir = REPO / "vods" / ".transcriptions"
    files = sorted(tdir.glob("*.transcript.json"), key=lambda p: p.stat().st_size, reverse=True)
    words: list[tuple[float, str]] = []
    for f in files:
        try:
            segs = json.loads(f.read_text(encoding="utf-8"))
            for s in segs:
                t = s.get("start", 0)
                for w in str(s.get("text", "")).split():
                    words.append((t, w))
            if len(words) > n * words_per:
                break
        except Exception:
            continue
    if not words:
        return [("the streamer says something funny and then chat reacts " * 200)
                for _ in range(n)]
    out = []
    for i in range(n):
        chunk = words[i * words_per:(i + 1) * words_per]
        if not chunk:
            break
        mm = int(chunk[0][0]) // 60
        out.append(f"Transcript (timestamps MM:SS from stream start), starting ~{mm:02d}:00:\n"
                   + " ".join(w for _, w in chunk))
    return out


_MOMENT_TMPL_STATIC_LAST = """/no_think
You are a stream clip scout. This is a JUST_CHATTING segment. Find 0-3 clip-worthy moments by matching against the PATTERN CATALOG below.

{variable}

{catalog}

Respond with ONLY a single JSON object: {{"moments": [ ... ]}}. Each element: {{"time": "MM:SS", "start_time": "MM:SS", "end_time": "MM:SS", "score": 1-10, "category": "hype|funny|emotional|hot_take|storytime|reactive|dancing|controversial", "primary_pattern": "<pattern_id>", "why": "one sentence"}}.
If nothing stands out, respond: {{"moments": []}}"""


def synth_decode_prompts(n: int) -> list[str]:
    cat = _load_catalog()
    vary = _load_transcript_windows(n)
    return [_MOMENT_TMPL_STATIC_LAST.format(variable=v, catalog=cat) for v in vary]


def load_dumped_prompts(path: Path, n: int) -> list[str]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            o = json.loads(line)
            if o.get("kind", "moment") == "moment":
                out.append(o["prompt"])
        except Exception:
            pass
    return out[:n]


# ----------------------------------------------------------------------- modes
def approx_tokens(s: str) -> int:
    return max(1, len(s) // 4)


def mode_decode(model, prompts, reps, max_tokens, timeout, label):
    print(f"\n=== DECODE  [{label}]  prompts={len(prompts)} reps={reps} ===")
    rows = []
    for i, p in enumerate(prompts):
        for r in range(reps):
            res = call(model, p, max_tokens, timeout)
            if not res["ok"]:
                print(f"  prompt {i} rep {r}: FAILED"); continue
            share = (res["ttft"] / res["wall"]) if (res["ttft"] and res["wall"]) else None
            rows.append(res)
            ds = f" draft={res['draft']}" if res["draft"] else ""
            print(f"  p{i} r{r} src={res['src']:9} wall={res['wall']:6.2f}s "
                  f"ttft={ (res['ttft'] or 0):5.2f}s tok/s={ (res['tok_s'] or 0):6.1f} "
                  f"ntok={res['ntok']} prefill_share={ (share or 0):.0%}{ds}")
    if rows:
        tok = [r["tok_s"] for r in rows if r["tok_s"]]
        sh = [(r["ttft"] / r["wall"]) for r in rows if r["ttft"] and r["wall"]]
        wl = [r["wall"] for r in rows]
        print(f"  --- median decode tok/s = {_med(tok):.1f}   "
              f"median prefill_share = {_med(sh):.0%}   median wall = {_med(wl):.2f}s")
    return rows


def mode_ttft(model, timeout):
    print("\n=== TTFT / prefix-reuse (C2a) ===")
    cat = _load_catalog()
    S = "SYSTEM CONTEXT (static):\n" + cat + "\n\n" + ("filler context. " * 400)
    X = "z" + S[1:]  # control: change token 1 so the prefix differs
    CARD = ("ARC-CARD BUILDER (different family):\n" + ("summarize the following claims. " * 400))
    V = ["\n\nTASK 1: list 3 moments as JSON.",
         "\n\nTASK 2: list 3 different moments as JSON.",
         "\n\nTASK 3: list 3 other moments as JSON.",
         "\n\nTASK 4: list 3 more moments as JSON."]

    def one(tag, prompt):
        res = call(model, prompt, 1, timeout)  # max_tokens=1 -> TTFT ~= prefill
        t = res["ttft"] if res["ttft"] else res["wall"]
        print(f"  {tag:26} ttft={t:6.3f}s  (~{approx_tokens(prompt)} tok, src={res['src']})")
        return t

    print(" reuse probe (expect step2 << step3):")
    t1 = one("1. S+V1 (cold)", S + V[0])
    t2 = one("2. S+V2 (reuse probe)", S + V[1])
    t3 = one("3. X+V3 (control, diff prefix)", X + V[2])
    reuse = (t3 - t2) / t3 if t3 else 0
    print(f"   -> shared-prefix reuse saving ~ {reuse:.0%} "
          f"({'REUSE FIRES' if t2 < 0.8 * t3 else 'no meaningful reuse'})")
    print(" alternation probe (foreign prompt between shared-prefix calls):")
    one("4. S+V1 (warm)", S + V[0])
    one("5. CARD (foreign)", CARD + V[1])
    t6 = one("6. S+V2 (after foreign)", S + V[3])
    print(f"   -> after foreign prompt: ttft={t6:.3f}s "
          f"({'survived' if t6 < 0.8 * t3 else 'reverted to cold (single-slot reuse died)'})")
    print("\n   NOTE: if reuse fires but dies on alternation, retry after "
          "`lms load ... --parallel 2` (llama.cpp routes to the longest-common-prefix slot).")


def mode_prefill(model, timeout, ntok_prompt=10000):
    print("\n=== PREFILL (Proposal P / evalBatchSize A/B) ===")
    big = ("SYSTEM:\n" + _load_catalog() + "\n\n"
           + ("this is a long transcript chunk with lots of words. " * (ntok_prompt // 9)))
    for r in range(3):
        res = call(model, big, 1, timeout)
        t = res["ttft"] if res["ttft"] else res["wall"]
        print(f"  rep{r}: prefill_ttft={t:6.3f}s  (~{approx_tokens(big)} tok, src={res['src']})")


def _med(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return 0.0
    m = len(xs) // 2
    return xs[m] if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["decode", "ttft", "prefill"], default="decode")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--label", default="current", help="config label for the decode summary")
    ap.add_argument("--prompts", type=Path, help="dumped passb_prompts.jsonl (decode)")
    ap.add_argument("--count", type=int, default=4, help="# prompts (decode)")
    ap.add_argument("--reps", type=int, default=2, help="reps per prompt (decode)")
    ap.add_argument("--max-tokens", type=int, default=512)
    ap.add_argument("--timeout", type=float, default=300.0)
    args = ap.parse_args(argv)

    if args.mode == "decode":
        if args.prompts and args.prompts.exists():
            prompts = load_dumped_prompts(args.prompts, args.count)
            print(f"[real dumped prompts: {len(prompts)} from {args.prompts.name}]")
        else:
            prompts = synth_decode_prompts(args.count)
            print(f"[SYNTHETIC-realistic prompts: {len(prompts)} "
                  f"(catalog + real transcript windows); ~{approx_tokens(prompts[0])} tok each]")
        mode_decode(args.model, prompts, args.reps, args.max_tokens, args.timeout, args.label)
    elif args.mode == "ttft":
        mode_ttft(args.model, args.timeout)
    else:
        mode_prefill(args.model, args.timeout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
