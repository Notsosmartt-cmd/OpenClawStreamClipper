#!/usr/bin/env python3
"""meme_match.py — machine-matchable meme/skit-format recognition (plan Phase 5).

Matches a candidate moment (transcript text + optional visual/audio tags)
against config/meme_formats.json — the audio/skit-format dimension no
off-the-shelf library covers (concepts/reference-humor-2026-07). This NAMES the
format (for title/hook + the known_format probe); it does not decide clip-worth.

PRECISION-FIRST (concepts/master-research-2026-07 RQ4): a verbal_trigger match
(or a strong visual+audio co-occurrence) is required — embedding proximity alone
is NOT a match (naked embeddings/LLM underperformed classical trigger matching).
Per-format thresholds. v1 is pure-lexical (no heavy deps); an optional
sentence-transformers path (embed_fn) can be injected when available.

    match(text, visual_tags=?, audio_labels=?, formats=?) -> [{name, confidence, via, about}]
"""
from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable

_CACHE: dict | None = None


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", str(s).lower())


def load_formats(path: str | None = None) -> list[dict]:
    """Load config/meme_formats.json (cached). [] on failure (failure-soft)."""
    global _CACHE
    if path is None and _CACHE is not None:
        return _CACHE
    p = Path(path) if path else (Path(__file__).resolve().parents[2] / "config" / "meme_formats.json")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        formats = data.get("formats", []) if isinstance(data, dict) else []
    except Exception:
        formats = []
    if path is None:
        _CACHE = formats
    return formats


def _trigger_hit(text_n: str, trigger: Any) -> float:
    """Confidence a single trigger fires in normalized text. Phrase → substring
    (1.0) or fuzzy (best window ratio); {"re": ...} → regex (0.95)."""
    if isinstance(trigger, dict) and "re" in trigger:
        try:
            return 0.95 if re.search(trigger["re"], text_n) else 0.0
        except re.error:
            return 0.0
    tn = _norm(str(trigger)).strip()
    if not tn:
        return 0.0
    if tn in text_n:
        return 1.0
    # fuzzy: slide the trigger's word-length window across the text
    tw = tn.split()
    words = text_n.split()
    if not words or not tw:
        return 0.0
    best = 0.0
    for i in range(max(1, len(words) - len(tw) + 1)):
        window = " ".join(words[i:i + len(tw)])
        best = max(best, SequenceMatcher(None, tn, window).ratio())
    return best if best >= 0.82 else 0.0


def match(text: str, *, visual_tags: list[str] | None = None,
          audio_labels: list[str] | None = None, formats: list[dict] | None = None,
          embed_fn: Callable[[str], Any] | None = None) -> list[dict]:
    """Return matched formats above their per-format threshold, best first.

    Confidence = max verbal-trigger hit, + 0.2 if a visual_signature keyword is
    present, + 0.15 if an audio_cue label is present (capped 1.0). A format with
    NO verbal_trigger can still match on visual+audio co-occurrence (both
    required) — but pure single-modal coincidence never matches (precision)."""
    formats = formats if formats is not None else load_formats()
    text_n = _norm(text or "")
    vis = {_norm(v).strip() for v in (visual_tags or []) if str(v).strip()}
    aud = {_norm(a).strip() for a in (audio_labels or []) if str(a).strip()}
    out: list[dict] = []
    for fmt in formats:
        thr = float(fmt.get("threshold", 0.5))
        triggers = fmt.get("verbal_trigger") or []
        vsig = {_norm(v).strip() for v in (fmt.get("visual_signature") or [])}
        acue = {_norm(a).strip() for a in (fmt.get("audio_cue") or [])}

        trig = max((_trigger_hit(text_n, t) for t in triggers), default=0.0)
        vis_hit = bool(vis & vsig)
        # audio labels are substringy (e.g. "beep_censor" contains "beep")
        aud_hit = any(any(c in a for c in acue) for a in aud) if acue else False

        via: list[str] = []
        conf = 0.0
        if triggers:
            # A trigger-defining format ONLY matches on its verbal trigger
            # (visual/audio just corroborate) — precision: bush+laughter without
            # the "george" line is not the George-Bush bit.
            if trig > 0:
                conf = trig
                via.append("verbal")
                if vis_hit:
                    conf = min(1.0, conf + 0.2); via.append("visual")
                if aud_hit:
                    conf = min(1.0, conf + 0.15); via.append("audio")
        else:
            # No verbal trigger — match on whatever signal(s) the format DEFINES.
            defines_vis, defines_aud = bool(vsig), bool(acue)
            if defines_vis and defines_aud:
                if vis_hit and aud_hit:
                    conf = 0.65; via = ["visual", "audio"]
            elif defines_aud and aud_hit:
                conf = 0.65; via = ["audio"]        # audio-signature format (vine_boom, crickets)
            elif defines_vis and vis_hit:
                conf = 0.6; via = ["visual"]
        # embed_fn reserved as a future tie-breaker; precision-first keeps matching
        # gated on the classical signals above (RQ4: naked embeddings underperform).
        if conf >= thr:
            out.append({"name": fmt["name"], "confidence": round(conf, 3),
                        "via": "+".join(via), "about": fmt.get("about", "")})
    return sorted(out, key=lambda m: m["confidence"], reverse=True)


if __name__ == "__main__":
    import sys
    txt = " ".join(sys.argv[1:]) or "ever heard of george"
    print(json.dumps(match(txt, visual_tags=["bush"], audio_labels=["laughter"]), indent=2))
