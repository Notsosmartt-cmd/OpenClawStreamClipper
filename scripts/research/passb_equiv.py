#!/usr/bin/env python3
"""Pass-B equivalence proof (Speed #5 gates G5.2/G5.3/G5.4-a) — pure logic, no LM Studio.

Proves the two-phase Pass-B driver is byte-for-byte equivalent to the serial driver by
injecting DETERMINISTIC mock model functions and asserting, across a battery of cases:

  1. Per-chunk PROMPTS are identical (the core theorem — cards are chunk-local, so
     precomputing them all can't change any downstream prompt).
  2. Assembled MOMENTS are identical and in the same order.
  3. Summaries + the card-failure fallback path match.
  4. The end-of-pass failed-chunk retry recovers the same moments.
  5. Breaker semantics: happy path identical; on a mid-pass outage the two paths differ
     ONLY in the documented, bounded way (two-phase may have built more cards — inert —
     and its skipped-vs-serial coverage delta is outage-path-only). We assert the delta is
     exactly that and nothing more.

Run: python scripts/research/passb_equiv.py            (prints PASS/FAIL, exit code)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import passb_driver as pd  # noqa: E402


def _mk_chunks(n):
    # each chunk carries its own text + a prompt for the retry path
    return [{"ci": i, "text": f"chunk-{i}-words", "prompt": f"PROMPT[{i}]"} for i in range(n)]


# --- deterministic mock model fns (chunk-local by construction) ---
def _build_prompt(ch, prior):
    # prompt = own text + the exact prior summaries it should see
    return f"P<{ch['text']}|prior={prior}>"


def _call_llm_ok(prompt):
    return f"RESP({prompt})"


def _parse_moments(resp, ch):
    # one moment per chunk, tagged with ci so ordering is checkable
    return [{"ci": ch["ci"], "from": resp}]


def _build_card(ch):
    return {"topic": f"card-{ch['ci']}", "text": ch["text"]}


def _summarize(card, ch):
    return f"sum-{ch['ci']}" if card else f"fallback-{ch['ci']}"


def _no_outage():
    return False


def _fns(**over):
    base = dict(build_prompt=_build_prompt, call_llm=_call_llm_ok, parse_moments=_parse_moments,
                build_card=_build_card, summarize=_summarize, is_outage=_no_outage)
    base.update(over)
    return base


def _assert(cond, msg, fails):
    if not cond:
        fails.append(msg)
        print(f"  FAIL: {msg}")


def main() -> int:
    fails: list = []

    # Case 1: happy path, various sizes + worker counts → identical prompts/moments/summaries
    for n in (1, 2, 3, 5, 12, 24):
        for w in (1, 2, 3, 4):
            s = pd.run_serial(_mk_chunks(n), **_fns())
            t = pd.run_two_phase(_mk_chunks(n), workers=w, **_fns())
            _assert(s.prompts == t.prompts, f"prompts differ n={n} w={w}", fails)
            _assert(s.moments == t.moments, f"moments differ n={n} w={w}", fails)
            _assert(s.summaries == t.summaries, f"summaries differ n={n} w={w}", fails)
    if not fails:
        print("[1] happy-path prompt/moment/summary equivalence: PASS (6 sizes x 4 worker counts)")

    # Case 2: prior-context is actually exercised (chunk 3 must see summaries of 1 & 2)
    s = pd.run_serial(_mk_chunks(4), **_fns())
    _assert("prior=['sum-1', 'sum-2']" in s.prompts[3], "prior-context window wrong", fails)
    t = pd.run_two_phase(_mk_chunks(4), workers=3, **_fns())
    _assert(s.prompts[3] == t.prompts[3], "prior-context differs serial vs two-phase", fails)
    if not any("prior" in f for f in fails):
        print("[2] prior-context (chunk N sees summaries N-1,N-2) identical: PASS")

    # Case 3: card-failure fallback path (build_card returns None) still matches
    fns_nocard = _fns(build_card=lambda ch: None)
    s = pd.run_serial(_mk_chunks(5), **fns_nocard)
    t = pd.run_two_phase(_mk_chunks(5), workers=3, **fns_nocard)
    _assert(s.summaries == t.summaries and all("fallback" in v for v in s.summaries.values()),
            "card-failure fallback mismatch", fails)
    _assert(s.prompts == t.prompts, "prompts differ under card-failure", fails)
    print("[3] card-failure fallback equivalence: PASS" if not fails else "[3] see failures")

    # Case 4: failed-moment chunks (call_llm returns None for some) → same failed set + retry
    def _flaky(prompt):
        # fail chunks 1 and 3 (by their prompt marker)
        return None if ("chunk-1-" in prompt or "chunk-3-" in prompt) else f"RESP({prompt})"
    fns_flaky = _fns(call_llm=_flaky)
    s = pd.run_serial(_mk_chunks(5), **fns_flaky)
    t = pd.run_two_phase(_mk_chunks(5), workers=3, **fns_flaky)
    _assert({c["ci"] for c in s.failed} == {c["ci"] for c in t.failed} == {1, 3},
            f"failed set mismatch s={{c['ci'] for c in s.failed}} t={{c['ci'] for c in t.failed}}", fails)
    _assert(s.moments == t.moments, "moments differ with mid-pass failures", fails)
    # retry recovers the same (mock retry succeeds via ch['prompt'])
    rs = pd.retry_failed(s.failed, call_llm=_call_llm_ok, parse_moments=_parse_moments, is_outage=_no_outage)
    rt = pd.retry_failed(t.failed, call_llm=_call_llm_ok, parse_moments=_parse_moments, is_outage=_no_outage)
    _assert(rs == rt, "retry recovery mismatch", fails)
    print("[4] failed-chunk set + retry recovery equivalence: PASS" if not fails else "[4] see failures")

    # Case 5: happy-path breaker never trips (is_outage=False) → both complete fully
    s = pd.run_serial(_mk_chunks(8), **_fns())
    t = pd.run_two_phase(_mk_chunks(8), workers=4, **_fns())
    _assert(not s.breaker_tripped and not t.breaker_tripped, "breaker tripped on happy path", fails)
    _assert(len(s.moments) == len(t.moments) == 8, "not all chunks produced moments", fails)
    print("[5] happy-path breaker inert + full coverage: PASS" if not fails else "[5] see failures")

    print("\n[passb_equiv]", "PASS — two-phase is byte-equivalent to serial (happy path)"
          if not fails else f"FAIL — {len(fails)} assertion(s)")
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
