#!/usr/bin/env python3
"""cut_inference.py — text-anchored "smart" jump cuts (jump-cuts-v2 phase J2).

The shipped smart-cut path asked the Stage-6 vision mega-prompt for drop spans in
absolute SECONDS — but LLMs are poor at word→second arithmetic, so the edges only
survived via a ±1 s snap. This module fixes that: ONE text-only LLM call asks the
model to QUOTE the verbatim substrings to delete; we map each quote to timestamps
DETERMINISTICALLY against the transcript segments (segment start/end + intra-segment
char interpolation). The model never does math, and a quote that doesn't match the
transcript is discarded — self-verifying.

Also houses:
  * coherence_ok()  — J3 gate: did the clip's payoff words survive the cut?
  * filler_cuts()   — J5 deterministic pause-adjacent filler-word micro-lane

Everything is failure-soft: any problem (no LM Studio, bad JSON, no match) returns
[] / True so the caller degrades to silence-only cuts, never crashes.
See concepts/plan-jump-cuts-v2-2026-07.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
# Deterministic quote → timestamp mapping (the crux — no LLM arithmetic)
# ─────────────────────────────────────────────────────────────────────────────

def _char_timeline(segments: list[dict]) -> tuple[str, list[tuple[int, int, float, float]]]:
    """Concatenate segment texts (single-spaced) and return (fulltext, spans) where
    each span = (char_start, char_end, t_start, t_end). A char offset into fulltext
    maps to an interpolated absolute time within its segment."""
    parts: list[str] = []
    spans: list[tuple[int, int, float, float]] = []
    pos = 0
    for s in segments or []:
        txt = " ".join(str(s.get("text", "")).split()).strip()
        if not txt:
            continue
        try:
            st = float(s.get("start", 0.0))
            en = float(s.get("end", st))
        except (TypeError, ValueError):
            continue
        if en < st:
            en = st
        if parts:
            parts.append(" ")
            pos += 1
        spans.append((pos, pos + len(txt), st, en))
        parts.append(txt)
        pos += len(txt)
    return "".join(parts), spans


def _char_to_time(off: int, spans) -> float | None:
    """Interpolated absolute time for a char offset into the concatenated text."""
    if not spans:
        return None
    off = max(0, off)
    for cs, ce, ts, te in spans:
        if cs <= off <= ce:
            frac = (off - cs) / max(1, (ce - cs))
            return ts + frac * (te - ts)
    if off < spans[0][0]:
        return spans[0][2]
    return spans[-1][3]


def _map_quote(quote: str, fulltext_lc: str, spans, *, min_chars: int = 4) -> dict | None:
    """Map a verbatim quote to a {drop_start, drop_end} absolute span + its char range,
    or None if it doesn't occur in the transcript (self-verifying — a hallucinated
    quote is dropped)."""
    q = " ".join(str(quote or "").split()).strip().lower()
    if len(q) < min_chars:
        return None
    idx = fulltext_lc.find(q)
    if idx < 0:
        return None
    a = _char_to_time(idx, spans)
    b = _char_to_time(idx + len(q), spans)
    if a is None or b is None or b <= a:
        return None
    return {"drop_start": round(a, 3), "drop_end": round(b, 3),
            "_c0": idx, "_c1": idx + len(q)}


def _remove_ranges(text: str, ranges: list[tuple[int, int]]) -> str:
    """Return `text` with the given [c0,c1) char ranges deleted (the surviving text
    after the proposed cuts) — used by the J3 coherence gate."""
    if not ranges:
        return text
    keep: list[str] = []
    cur = 0
    for c0, c1 in sorted(ranges):
        if c0 > cur:
            keep.append(text[cur:c0])
        cur = max(cur, c1)
    keep.append(text[cur:])
    return " ".join("".join(keep).split())


# ─────────────────────────────────────────────────────────────────────────────
# The LLM micro-call
# ─────────────────────────────────────────────────────────────────────────────

_PROMPT = """/no_think
You are a short-form video editor tightening ONE clip. Below is its transcript. Find spans of DEAD WEIGHT to delete so it reaches the payoff faster: filler ("um", "like", "you know"), false starts, repeated words, or a rambling tangent that doesn't serve the moment.

RULES:
- QUOTE the exact words to delete, copied VERBATIM from the transcript (character for character). Never paraphrase, never write timestamps.
- KEEP the setup and the payoff. Never quote the last sentence.
- Delete at most ~40% of the words. If the clip is already tight, return an empty list.

Good examples: "um, so, like,", "wait wait let me start over,", "and yeah, anyway,".

TRANSCRIPT:
\"\"\"{transcript}\"\"\"

Respond with ONLY JSON: {{"cuts": [{{"quote": "<verbatim words to delete>", "reason": "filler|false_start|tangent|repetition"}}]}}"""


def _resolve_llm(model, url):
    if model and url:
        return model, url
    # BUG-74 audit (2026-07-15): CLIP_CUT_MODEL is the phase pin — renders run
    # during Stage 6 (D6) with the VISION model loaded, and the bare
    # CLIP_TEXT_MODEL fallback would JIT-summon the text model beside it
    # (the exact ghost class fixed in stages 4/6). stage6.py sets the pin.
    return (model
            or os.environ.get("CLIP_CUT_MODEL")
            or os.environ.get("CLIP_TEXT_MODEL") or "",
            url or os.environ.get("CLIP_LM_URL") or "http://localhost:1234/v1")


def _parse_quotes(txt: str) -> list[dict]:
    if not txt:
        return []
    s, e = txt.find("{"), txt.rfind("}")
    if s < 0 or e <= s:
        return []
    try:
        obj = json.loads(txt[s:e + 1])
    except Exception:
        return []
    cuts = obj.get("cuts") if isinstance(obj, dict) else None
    return [c for c in cuts if isinstance(c, dict)] if isinstance(cuts, list) else []


def infer_cuts(segments: list[dict], *, model: str | None = None, url: str | None = None,
               max_frac: float = 0.40, timeout: int = 30, payoff_text: str = "", log=None) -> list[dict]:
    """One text-only LLM call → drop-spans (absolute VOD seconds) for filler/false-
    starts/tangents/repetition. Quotes are mapped to time deterministically and
    budget-capped at `max_frac` of the clip.

    J3 coherence gate: reconstruct the surviving transcript and require the payoff's
    content words to survive (`payoff_text`); if they don't, the cuts are rejected
    (return []) so Stage 7 falls back to silence-only. Optional LLM fidelity judge
    when CLIP_CUT_JUDGE=1. [] on any failure."""
    fulltext, spans = _char_timeline(segments)
    if not fulltext or not spans:
        return []
    try:
        import lmstudio
    except Exception:
        return []
    mdl, u = _resolve_llm(model, url)
    try:
        txt = lmstudio.chat(_PROMPT.format(transcript=fulltext[:4000]), model=mdl, url=u,
                            timeout=timeout, response_json=True, max_tokens=300)
    except Exception:
        return []
    quotes = _parse_quotes(txt)
    if not quotes:
        return []
    fulltext_lc = fulltext.lower()
    total = spans[-1][3] - spans[0][2]
    budget = max(0.0, float(max_frac) * total)
    cuts: list[dict] = []
    ranges: list[tuple[int, int]] = []
    dropped = 0.0
    for q in quotes:
        m = _map_quote(q.get("quote", ""), fulltext_lc, spans)
        if not m:
            continue                              # hallucinated / non-verbatim → drop
        d = m["drop_end"] - m["drop_start"]
        if dropped + d > budget + 1e-6:
            continue
        ranges.append((m.pop("_c0"), m.pop("_c1")))
        m["reason"] = str(q.get("reason", "")).strip()[:20]
        cuts.append(m)
        dropped += d
    if not cuts:
        return []

    # J3: would the payoff survive? (deterministic, then optional LLM judge)
    kept_text = _remove_ranges(fulltext, ranges)
    if payoff_text and not coherence_ok(kept_text, payoff_text):
        if log:
            log(f"  [cut_infer] REJECTED — payoff words dropped by the cuts (coherence gate)")
        return []
    if os.environ.get("CLIP_CUT_JUDGE", "0").strip().lower() in ("1", "true", "on", "yes"):
        if not _judge_fidelity(fulltext, kept_text, model=mdl, url=u, timeout=timeout, log=log):
            if log:
                log(f"  [cut_infer] REJECTED — fidelity judge failed")
            return []
    if log:
        log(f"  [cut_infer] {len(cuts)}/{len(quotes)} quotes mapped "
            f"({dropped:.1f}s / {budget:.1f}s budget){' +judged' if os.environ.get('CLIP_CUT_JUDGE') else ''}")
    return cuts


_JUDGE_PROMPT = """/no_think
An editor compressed a short clip by deleting filler/rambling. Does the SHORTENED transcript still read as a coherent setup→payoff, preserving the point of the clip? Answer strictly.

ORIGINAL:
\"\"\"{orig}\"\"\"

SHORTENED:
\"\"\"{kept}\"\"\"

Respond with ONLY JSON: {{"coherent": true|false, "fidelity": 0-10}}"""


def _judge_fidelity(orig: str, kept: str, *, model, url, timeout: int = 20, log=None) -> bool:
    """Optional LLM fidelity judge (caption_judge pattern). True (accept) on any
    failure — the deterministic gate already ran; this is a bonus check."""
    try:
        import lmstudio
        txt = lmstudio.chat(_JUDGE_PROMPT.format(orig=orig[:2500], kept=kept[:2500]),
                            model=model, url=url, timeout=timeout, response_json=True, max_tokens=60)
        s, e = txt.find("{"), txt.rfind("}")
        obj = json.loads(txt[s:e + 1])
        return bool(obj.get("coherent", True)) and float(obj.get("fidelity", 10)) >= 6
    except Exception:
        return True


# ─────────────────────────────────────────────────────────────────────────────
# J3 — coherence gate (did the payoff survive?)
# ─────────────────────────────────────────────────────────────────────────────

_STOP = {"the", "a", "an", "and", "or", "but", "so", "to", "of", "in", "on", "it",
         "is", "was", "i", "you", "he", "she", "they", "we", "that", "this", "for",
         "with", "at", "as", "be", "are", "im", "its", "my", "me", "not", "no"}


def _keywords(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9']+", (text or "").lower())
            if len(w) > 2 and w not in _STOP}


def coherence_ok(kept_text: str, payoff_text: str, *, min_ratio: float = 0.6) -> bool:
    """J3: does the compressed transcript still contain the payoff's content words?
    True (permissive) when we can't tell (no payoff text). Deterministic pre-filter
    before the optional LLM judge."""
    kw = _keywords(payoff_text)
    if not kw:
        return True
    survived = kw & _keywords(kept_text)
    return (len(survived) / len(kw)) >= min_ratio


# ─────────────────────────────────────────────────────────────────────────────
# J5 — deterministic filler micro-lane (no LLM)
# ─────────────────────────────────────────────────────────────────────────────

# Filler tokens only worth cutting when clustered next to a pause (an isolated
# "like" mid-sentence makes a machine-gun micro-cut that looks worse than the filler).
FILLER_WORDS = ("um", "uh", "erm", "hmm", "like", "you know", "i mean", "sort of",
                "kind of", "basically", "literally", "right")


def load_word_srt(srt_path: str, offset: float = 0.0) -> list[dict]:
    """Parse a word-per-block SRT (stage7_transcribe writes one block per WORD, with
    clip-relative timing) into [{word,start,end}], shifted by `offset` (the clip start)
    to absolute VOD seconds so it lines up with the other cut sources. [] on failure."""
    try:
        txt = Path(srt_path).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []

    def _ts(s: str) -> float:
        s = s.strip().replace(",", ".")
        h, m, rest = s.split(":")
        return int(h) * 3600 + int(m) * 60 + float(rest)

    out: list[dict] = []
    for blk in txt.replace("\r\n", "\n").strip().split("\n\n"):
        lines = blk.strip().splitlines()
        tl = next((ln for ln in lines if "-->" in ln), None)
        if not tl:
            continue
        a, _, b = tl.partition("-->")
        try:
            ra, rb = _ts(a), _ts(b)
        except Exception:
            continue
        word = " ".join(lines[lines.index(tl) + 1:]).strip()
        if word:
            out.append({"word": word, "start": offset + ra, "end": offset + rb})
    return out


def filler_cuts(word_items: list[dict], *, pause_min: float = 0.4, cluster_min: float = 0.5,
                cap: int = 4, protect_after: float | None = None) -> list[dict]:
    """Drop-spans for filler-word clusters ADJACENT to a ≥`pause_min` s pause, where
    the merged span is ≥`cluster_min` s. word_items: [{"word","start","end"}, …]
    (word-level SRT). Isolated fillers are left alone. Absolute VOD seconds. Capped."""
    words = []
    for w in word_items or []:
        try:
            words.append((str(w.get("word", "")).strip().lower().strip(".,!?"),
                          float(w.get("start")), float(w.get("end"))))
        except (TypeError, ValueError):
            continue
    if len(words) < 2:
        return []
    cuts: list[dict] = []
    n = len(words)
    for i, (tok, st, en) in enumerate(words):
        if tok not in FILLER_WORDS:
            continue
        prev_gap = st - words[i - 1][2] if i > 0 else 99.0
        next_gap = words[i + 1][1] - en if i < n - 1 else 99.0
        if max(prev_gap, next_gap) < pause_min:      # not adjacent to a pause
            continue
        a, b = st, en
        if prev_gap >= pause_min and i > 0:          # absorb the leading pause
            a = words[i - 1][2] + 0.1
        if next_gap >= pause_min and i < n - 1:
            b = words[i + 1][1] - 0.1
        if b - a < cluster_min:
            continue
        if protect_after is not None and b >= protect_after:
            continue                                 # never inside the payoff zone
        cuts.append({"drop_start": round(a, 3), "drop_end": round(b, 3), "reason": "filler"})
        if len(cuts) >= cap:
            break
    return cuts


# ─────────────────────────────────────────────────────────────────────────────
# Self-test (deterministic — no LM Studio)
# ─────────────────────────────────────────────────────────────────────────────

def _selftest() -> int:
    fails = 0

    def check(name, cond):
        nonlocal fails
        print(f"  {'OK ' if cond else 'FAIL'} {name}")
        if not cond:
            fails += 1

    segs = [{"start": 100.0, "end": 104.0, "text": "okay so um like anyway"},
            {"start": 104.0, "end": 108.0, "text": "the point is he actually won"},
            {"start": 108.0, "end": 111.0, "text": "which nobody expected at all"}]
    fulltext, spans = _char_timeline(segs)
    check("timeline concatenates", fulltext.startswith("okay so um like anyway the point"))

    # quote maps to a plausible in-segment time
    m = _map_quote("um like anyway", fulltext.lower(), spans)
    check("quote maps in-window", m is not None and 100.0 <= m["drop_start"] < m["drop_end"] <= 104.5)

    # hallucinated quote (not in transcript) is discarded
    check("bogus quote -> None", _map_quote("rocket surgery elephant", fulltext.lower(), spans) is None)

    # _remove_ranges deletes the quoted chars → surviving text
    _m = _map_quote("um like anyway", fulltext.lower(), spans)
    kept = _remove_ranges(fulltext, [(_m["_c0"], _m["_c1"])])
    check("_remove_ranges drops the quote", "um like anyway" not in kept and "the point is" in kept)

    # coherence: payoff words survive vs dropped
    check("coherence keeps payoff",
          coherence_ok("the point is he actually won which nobody expected", "he actually won") is True)
    check("coherence flags dropped payoff",
          coherence_ok("okay so anyway", "he actually won nobody expected") is False)
    check("coherence permissive w/o payoff", coherence_ok("anything", "") is True)

    # filler lane: "um" after a 0.6s pause, cluster ≥0.5s -> a cut; isolated "like" -> none
    words = [{"word": "so", "start": 100.0, "end": 100.3},
             {"word": "um", "start": 100.9, "end": 101.5},          # 0.6s pause before
             {"word": "yeah", "start": 102.2, "end": 102.6}]         # 0.7s pause after
    fc = filler_cuts(words)
    check("filler cluster -> 1 cut", len(fc) == 1 and fc[0]["drop_end"] > fc[0]["drop_start"])
    words2 = [{"word": "he", "start": 100.0, "end": 100.2},
              {"word": "like", "start": 100.25, "end": 100.45},      # no pause around
              {"word": "won", "start": 100.5, "end": 100.8}]
    check("isolated filler -> no cut", filler_cuts(words2) == [])
    # payoff protection
    check("filler in payoff zone skipped",
          filler_cuts(words, protect_after=100.5) == [])

    # load_word_srt parses word blocks + applies the clip-start offset
    import tempfile
    srt = ("1\n00:00:01,000 --> 00:00:01,300\nso\n\n"
           "2\n00:00:01,900 --> 00:00:02,500\num\n\n"
           "3\n00:00:03,200 --> 00:00:03,600\nyeah\n")
    with tempfile.NamedTemporaryFile("w", suffix=".srt", delete=False, encoding="utf-8") as _f:
        _f.write(srt)
        _srt_path = _f.name
    wi = load_word_srt(_srt_path, offset=100.0)
    os.unlink(_srt_path)
    check("load_word_srt parses + offsets",
          len(wi) == 3 and wi[1]["word"] == "um" and abs(wi[1]["start"] - 101.9) < 1e-6)

    print("SELFTEST", "PASS" if fails == 0 else f"FAIL ({fails})")
    return 1 if fails else 0


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    print("cut_inference.py — text-anchored smart cuts (use --selftest)")
