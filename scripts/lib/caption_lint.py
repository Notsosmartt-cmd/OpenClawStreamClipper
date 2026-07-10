#!/usr/bin/env python3
"""Deterministic 'AI-tell' linter for clip titles and hooks (Part 1, P1.4).

Owner critique across two review rounds: generated titles/hooks "look too much
like an AI wrote it" — Title Case Headlines, quotation marks around invented
nouns, "The 'X' Y" constructions, listicle adjectives. A learned voice profile
(caption_style.py) + a rewritten prompt (stage6_vision P1.3) reduce this, but a
model drifts; this catches the drift with zero LLM cost so Stage 6 can regenerate
once (see stage6_vision._ground_field).

Stdlib only. Two entry points:
    lint(text, kind="title"|"hook") -> list[dict]   # every flag, with severity
    is_ai_voice(text, kind) -> bool                  # any "voice"-severity flag

Severity:
    "voice"  — the AI-tell tells the owner cares about; TRIGGERS regenerate-once
    "format" — length / trailing-period; ADVISORY (logged, never gates)

Run `python caption_lint.py --self-test` — fixtures are the six owner-critiqued
titles (must all flag) plus human-voiced titles/hooks (must all pass).
"""
from __future__ import annotations

import re
import sys

# Listicle / clickbait lexicon the owner flagged as "AI language". Deliberately
# SMALL and high-precision — a big list would false-positive on real speech.
# Word-boundary matched, case-insensitive.
_BANNED_LEXICON = (
    "ensues", "epic", "hilarious", "insane", "unbelievable", "jaw-dropping",
    "jaw dropping", "ultimate", "iconic", "legendary", "wholesome", "priceless",
    "you won't believe", "you wont believe", "gone wrong", "must-see", "must see",
    "utterly", "sheer chaos", "pure chaos",
)

# A quoted 1-4 word "invented noun" — 'Samurai Slicer', "Right on the Dot".
# Straight or curly quotes; short inner span so we don't match a whole quoted
# sentence (which can be a legit verbatim punchline the owner LIKES).
_QUOTED_NOUN = re.compile(r"""['"‘’“”]([A-Za-z][^'"‘’“”]{0,28})['"‘’“”]""")

# Headline shape: starts with "The " then a Capitalized word — "The Samurai…",
# "The 'Yo!' Freestyle…". Sentence-case titles that merely START with "The"
# and a lowercase word are fine.
_HEADLINE_THE = re.compile(r"^\s*The\s+['\"‘’“”]?[A-Z]")

# "<Title Case Noun> of <Title Case Noun>" listicle shape.
_THE_X_OF_Y = re.compile(r"\bThe\s+[A-Z][a-z]+.*\bof\s+[A-Z][a-z]+")

_CAPWORD = re.compile(r"^[A-Z][a-z]+$")
_ALPHA = re.compile(r"[A-Za-z]")
_EM_DASH = "—"

# Render caps: the hook card wraps at 18 chars × 3 lines (stage7 _wrap_hook);
# a title over ~70 chars gets sanitized to [:50] for the filename and reads long.
_TITLE_MAX = 70
_HOOK_MAX = 54


def _title_case_ratio(text: str) -> float:
    """Fraction of 'real' words (len>=3, alphabetic) that are Capitalized like a
    Headline. Sentence case (one leading cap) stays low; Title Case runs high."""
    words = [w.strip(".,!?:;'\"") for w in text.split()]
    real = [w for w in words if len(w) >= 3 and _ALPHA.search(w)]
    if not real:
        return 0.0
    capped = sum(1 for w in real if _CAPWORD.match(w))
    return capped / len(real)


def lint(text: str, kind: str = "title") -> list[dict]:
    """Return every AI-tell flag in ``text``. Each flag: {code, severity, detail}.
    Empty list = clean. ``kind`` is "title" or "hook" (only affects length cap +
    the trailing-period check, which applies to hooks)."""
    text = (text or "").strip()
    flags: list[dict] = []
    if not text:
        return flags

    ratio = _title_case_ratio(text)
    if ratio > 0.6:
        flags.append({"code": "title_case", "severity": "voice",
                      "detail": f"{ratio:.0%} of words are Title-Cased — write it in sentence case or lowercase"})

    qn = _QUOTED_NOUN.search(text)
    if qn:
        flags.append({"code": "quoted_noun", "severity": "voice",
                      "detail": f"quotes around \"{qn.group(1)}\" — drop the scare-quotes around invented names"})

    if _HEADLINE_THE.search(text):
        flags.append({"code": "headline_the", "severity": "voice",
                      "detail": "starts with 'The <Capitalized>' — a headline shape, not how a viewer talks"})

    if _THE_X_OF_Y.search(text):
        flags.append({"code": "listicle_shape", "severity": "voice",
                      "detail": "'The X of Y' listicle construction"})

    low = text.lower()
    hit = [w for w in _BANNED_LEXICON if re.search(r"\b" + re.escape(w) + r"\b", low)]
    if hit:
        flags.append({"code": "banned_lexicon", "severity": "voice",
                      "detail": f"clickbait words: {', '.join(hit)}"})

    if _EM_DASH in text:
        flags.append({"code": "em_dash", "severity": "voice",
                      "detail": "em-dash — models love it, humans texting don't"})

    if "#" in text:
        flags.append({"code": "hashtag", "severity": "voice",
                      "detail": "hashtag in the caption text"})

    # --- format-severity (advisory) ---
    cap = _HOOK_MAX if kind == "hook" else _TITLE_MAX
    if len(text) > cap:
        flags.append({"code": "too_long", "severity": "format",
                      "detail": f"{len(text)} chars > {cap} cap for a {kind}"})

    if kind == "hook" and text.endswith(".") and not text.endswith("..."):
        flags.append({"code": "hook_period", "severity": "format",
                      "detail": "hook ends with a period — drop it"})

    return flags


def is_ai_voice(text: str, kind: str = "title") -> bool:
    """True iff ``text`` trips any VOICE-severity flag (the gate signal)."""
    return any(f["severity"] == "voice" for f in lint(text, kind))


def summarize(text: str, kind: str = "title") -> str:
    """One-line human summary of the voice flags (for the regen prompt)."""
    return "; ".join(f["detail"] for f in lint(text, kind) if f["severity"] == "voice")


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------
_MUST_FLAG = [
    "The 'Right on the Dot' Payoff",
    "The 'Samurai Slicer' Diss",
    "The 'Yo!' Freestyle Challenge",
    "The 'Fake Girls' Freestyle on the Bus",
    "Principal Addresses the 'Underprivileged' Student Body",
    "Streamer Caught Lying About His 'Sister'",
    "The Ultimate Freestyle Battle",
    "Chaos Ensues at the School Assembly",
]
_MUST_PASS = [
    "grab your balls twist them pop them",
    "he really said bring the chop after school",
    "bro got hit in the head and blamed the chat",
    "the way he folded the second she pushed back",
    "streamer finds out the vending machines are segregated",
    "i missed the whole gym class for this",
]
_MUST_PASS_HOOKS = [
    "wait for the vending machine reveal",
    "he did NOT think that through",
    "chat was not ready for this one",
]


def _self_test() -> int:
    fails = []
    for t in _MUST_FLAG:
        if not is_ai_voice(t, "title"):
            fails.append(f"FALSE NEGATIVE (should flag): {t!r} -> {lint(t, 'title')}")
    for t in _MUST_PASS:
        if is_ai_voice(t, "title"):
            fails.append(f"FALSE POSITIVE (should pass): {t!r} -> {lint(t, 'title')}")
    for h in _MUST_PASS_HOOKS:
        if is_ai_voice(h, "hook"):
            fails.append(f"FALSE POSITIVE hook (should pass): {h!r} -> {lint(h, 'hook')}")
    if fails:
        print("caption_lint self-test FAILED:")
        for f in fails:
            print("  " + f)
        return 1
    print(f"caption_lint self-test PASS "
          f"({len(_MUST_FLAG)} flagged, {len(_MUST_PASS)} titles + {len(_MUST_PASS_HOOKS)} hooks clean)")
    return 0


def _cli() -> int:
    if "--self-test" in sys.argv:
        return _self_test()
    if len(sys.argv) > 1:
        text = " ".join(a for a in sys.argv[1:] if not a.startswith("--"))
        kind = "hook" if "--hook" in sys.argv else "title"
        fl = lint(text, kind)
        print(f"{text!r} ({kind}): " + (", ".join(f"{f['code']}[{f['severity']}]" for f in fl) or "clean"))
        for f in fl:
            print(f"   - {f['detail']}")
        return 1 if is_ai_voice(text, kind) else 0
    print("usage: caption_lint.py --self-test | caption_lint.py '<caption>' [--hook]")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
