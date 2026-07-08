#!/usr/bin/env python3
"""Pass-B driver — serial + two-phase, dependency-injected (Speed #5, I5.2).

The Stage-4 Pass-B loop runs each chunk's 2 LLM calls (moments + arc-card) SEQUENTIALLY
against an LM Studio server with 4 idle slots. This module extracts that control flow so it
can be (a) parallelised and (b) PROVEN equivalent to the serial path in pure logic — no LM
Studio — because every model-touching operation is INJECTED as a callable.

The equivalence theorem this module exists to prove (see scripts/research/passb_equiv.py):
  chunk cards depend ONLY on their own chunk's text, and a chunk's Pass-B prompt depends on
  (its own text) + (the summaries of the <=2 preceding chunks). So precomputing ALL cards
  first (two-phase Phase A) yields the SAME summaries as computing them inline, hence the
  SAME prompt for every chunk, hence the same moments — regardless of execution order.

Injected callables (all pure w.r.t. their inputs from the driver's perspective):
  build_prompt(chunk, prior_summaries) -> str        # Stage-4's exact prompt assembly
  call_llm(prompt) -> str|None                        # returns None on failure
  parse_moments(resp, chunk) -> list                  # + any per-chunk scoring/grounding
  build_card(chunk) -> card|None                      # the arc-card LLM call
  summarize(card, chunk) -> str                       # card -> one-liner (+ fallback)
  is_outage() -> bool                                 # BUG-31 breaker probe

A chunk is a dict; the driver only reads what the injected fns need + `ci` (chunk index)
for ordering. Returns a `PassBResult` with moments assembled in ascending `ci`, the summary
map, the failed-chunk list, and whether the breaker tripped — the exact observables the
Stage-4 loop produces, so a wired cut-over is a drop-in.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Callable, Optional


@dataclass
class PassBResult:
    moments: list = field(default_factory=list)     # assembled in ascending ci
    summaries: dict = field(default_factory=dict)   # ci -> summary text
    failed: list = field(default_factory=list)      # chunks whose moment call returned None
    breaker_tripped: bool = False
    prompts: dict = field(default_factory=dict)     # ci -> prompt (for the equivalence proof)


def _prior_summaries(summaries: dict, ci: int, n: int = 2) -> list:
    """The <=n most-recent PRECEDING chunk summaries, in chunk order — mirrors Stage-4's
    `chunk_summaries[-2:]` (which, because summaries are appended in ci order, is exactly
    the two chunks immediately before ci)."""
    return [summaries[k] for k in sorted(summaries) if k < ci][-n:]


def run_serial(chunks: list, *, build_prompt: Callable, call_llm: Callable,
               parse_moments: Callable, build_card: Callable, summarize: Callable,
               is_outage: Callable, prior_n: int = 2) -> PassBResult:
    """Reproduce the Stage-4 Pass-B order EXACTLY: per chunk, prompt (from prior summaries)
    -> moments call -> parse; then card -> summary (appended before the next chunk builds
    its prompt); breaker check aborts the remaining chunks."""
    r = PassBResult()
    for ch in chunks:
        ci = ch["ci"]
        prompt = build_prompt(ch, _prior_summaries(r.summaries, ci, prior_n))
        r.prompts[ci] = prompt
        resp = call_llm(prompt)
        if resp is not None:
            r.moments.extend(parse_moments(resp, ch))
            r.summaries[ci] = summarize(build_card(ch), ch)
        else:
            r.failed.append(ch)
        if is_outage():
            r.breaker_tripped = True
            break
    return r


def run_two_phase(chunks: list, *, build_prompt: Callable, call_llm: Callable,
                  parse_moments: Callable, build_card: Callable, summarize: Callable,
                  is_outage: Callable, workers: int = 3, prior_n: int = 2) -> PassBResult:
    """Two-phase: Phase A builds ALL cards/summaries in parallel (chunk-local → order-free);
    Phase B builds each prompt from the precomputed prior summaries and runs the moment
    calls in parallel, assembling results in ascending ci.

    Exactness under transient moment-call failures (found by passb_equiv, not a live run):
    Stage-4 serial creates a chunk's summary ONLY inside the `if moment_response:` block, so
    a chunk whose MOMENT call fails contributes NO summary and is absent from later chunks'
    prior-context. Building all summaries in Phase A would wrongly include a failed chunk's
    summary downstream. Since call_llm success is prompt-INDEPENDENT (it fails on
    connectivity, not content), the final success set is known after Phase B and is stable —
    so a single RECONCILIATION pass rebuilds the prompt of any succeeded chunk whose
    prior-window contained a failed chunk, from the succeeded-only summaries, and re-runs
    just those (bounded by ~prior_n × failures; ZERO on the happy path → identical to the
    fast path). Result: byte-exact to serial even under transient failures.

    Breaker: once an outage trips, stop SUBMITTING new moment calls (in-flight finish) and
    SKIP reconciliation — under a true outage we accept the bounded, documented degraded-path
    coverage delta vs serial (same class as Stage-4's BUG-31 abort)."""
    r = PassBResult()

    # --- Phase A: all cards in parallel (each depends only on its own chunk) ---
    def _card(ch):
        return ch["ci"], summarize(build_card(ch), ch)
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for ci, summ in ex.map(_card, chunks):
            r.summaries[ci] = summ
    full_summaries = dict(r.summaries)

    # --- Phase B: prompts (from ALL summaries) + moment calls in parallel ---
    prompts = {ch["ci"]: build_prompt(ch, _prior_summaries(full_summaries, ch["ci"], prior_n))
               for ch in chunks}
    lock = Lock()
    stop = {"tripped": False}
    results: dict = {}      # ci -> (resp, skipped_by_breaker)

    def _moments(ch):
        ci = ch["ci"]
        with lock:
            if stop["tripped"]:
                return ci, None, True
        resp = call_llm(prompts[ci])
        if resp is None and is_outage():
            with lock:
                stop["tripped"] = True
        return ci, resp, False

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for ci, resp, skipped in ex.map(_moments, chunks):
            results[ci] = (resp, skipped)
    r.breaker_tripped = stop["tripped"]

    # --- Reconciliation: enforce serial's "failed chunk → no summary" downstream ---
    if not r.breaker_tripped:
        succeeded = {ci for ci, (resp, _) in results.items() if resp is not None}
        valid = {ci: full_summaries[ci] for ci in succeeded}
        for ch in chunks:
            ci = ch["ci"]
            if ci not in succeeded:
                continue
            correct = build_prompt(ch, _prior_summaries(valid, ci, prior_n))
            if correct != prompts[ci]:     # a preceding chunk failed → serial saw a shorter prior
                prompts[ci] = correct
                results[ci] = (call_llm(correct), False)

    # --- Assemble in ascending ci — identical ordering to the serial extend() ---
    r.prompts = dict(prompts)
    for ch in chunks:
        ci = ch["ci"]
        resp, skipped = results[ci]
        if resp is not None:
            r.moments.extend(parse_moments(resp, ch))
        elif not skipped:
            r.failed.append(ch)
    return r


def retry_failed(failed: list, *, call_llm: Callable, parse_moments: Callable,
                 is_outage: Callable) -> list:
    """End-of-pass single retry of failed chunks (Stage-4 Gap #1). Order-independent of the
    two drivers; shared by both. Returns recovered moments; stops on a persistent outage."""
    recovered: list = []
    if is_outage():
        return recovered
    for ch in failed:
        resp = call_llm(ch["prompt"]) if "prompt" in ch else None
        if resp is None:
            if is_outage():
                break
            continue
        recovered.extend(parse_moments(resp, ch))
    return recovered
