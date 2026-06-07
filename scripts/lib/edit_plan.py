#!/usr/bin/env python3
"""Edit-plan JSON schema + validator.

Stage 6 vision returns an `edit_plan` field per moment. Stage 7's
profile-mode renderer reads it to drive zoom punches, freeze frames,
slow-mo windows, meme cutaways, B-roll inserts, SFX cues, and caption
preset choice.

Vision is encouraged but never required to populate every field — missing
fields fall back to per-category defaults from style_profiles.

Schema (all fields optional except `profile`):

    {
      "profile":          "comedy" | "hype" | ... | null,
      "zoom_punches":     [{"t": <s>, "scale": <f>, "hold": <s>}, ...]
                          OR [<float>, ...]  (treated as t with default scale/hold),
      "freeze_at":        {"t": <s>, "duration": <s>} | <float>(=t) | null,
      "slow_mo":          {"start": <s>, "end": <s>, "rate": <0.4..0.9>} | null,
      "meme_cutaway":     {"t": <s>, "tag": "<str>", "duration": <s>} | null,
      "broll_inserts":    [{"t": <s>, "noun": "<str>", "duration": <s>}, ...],
      "sfx_cues":         [{"t": <s>, "kind": "whoosh|impact|scratch|ding|riser"}, ...],
      "caption_emphasis": [<word_idx>, ...],
      "caption_preset":   "neon" | "bouncy" | "clean" | "news" | "soft" | null,
      "chat_overlay":     true | false,
      "flashes":          [{"t": <s>, "dur": <0.05..0.30>, "style": "soft|hard"}, ...]
                          OR [<float>, ...]   (white pop at t; engagement beat),
      "cuts":             [{"drop_start": <s>, "drop_end": <s>}, ...]
                          (ABSOLUTE VOD seconds to DROP — jump-cut to compress to the payoff)
    }

The validator coerces sloppy inputs (lone numbers → t-only objects, strings
→ tag/preset). Anything unrecognized is dropped silently.
"""
from __future__ import annotations

from typing import Any


VALID_PRESETS = {"neon", "bouncy", "clean", "news", "soft"}
VALID_SFX_KINDS = {"whoosh", "impact", "scratch", "ding", "riser"}


def _to_float(x: Any, default: float | None = None) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _to_int(x: Any, default: int | None = None) -> int | None:
    try:
        return int(x)
    except (TypeError, ValueError):
        return default


def _norm_zoom_punches(raw: Any) -> list[dict]:
    out: list[dict] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if isinstance(item, (int, float)):
            t = _to_float(item)
            if t is not None and t >= 0:
                out.append({"t": t, "scale": 1.15, "hold": 0.30})
        elif isinstance(item, dict):
            t = _to_float(item.get("t"))
            if t is None or t < 0:
                continue
            scale = _to_float(item.get("scale"), 1.15) or 1.15
            hold = _to_float(item.get("hold"), 0.30) or 0.30
            scale = max(1.02, min(scale, 1.40))
            hold = max(0.10, min(hold, 1.50))
            out.append({"t": t, "scale": round(scale, 3), "hold": round(hold, 3)})
    return out


def _norm_freeze(raw: Any) -> dict | None:
    if raw is None or raw is False:
        return None
    if isinstance(raw, (int, float)):
        t = _to_float(raw)
        if t is None or t < 0:
            return None
        return {"t": t, "duration": 0.5}
    if isinstance(raw, dict):
        t = _to_float(raw.get("t"))
        if t is None or t < 0:
            return None
        dur = _to_float(raw.get("duration"), 0.5) or 0.5
        dur = max(0.15, min(dur, 1.5))
        return {"t": t, "duration": round(dur, 3)}
    return None


def _norm_slow_mo(raw: Any) -> dict | None:
    if not isinstance(raw, dict):
        return None
    start = _to_float(raw.get("start"))
    end = _to_float(raw.get("end"))
    rate = _to_float(raw.get("rate"), 0.5) or 0.5
    if start is None or end is None or end <= start:
        return None
    rate = max(0.30, min(rate, 0.95))
    return {
        "start": round(start, 3),
        "end":   round(end, 3),
        "rate":  round(rate, 3),
    }


def _norm_meme(raw: Any) -> dict | None:
    if not isinstance(raw, dict):
        return None
    t = _to_float(raw.get("t"))
    tag = str(raw.get("tag") or "").strip().lower()
    if t is None or t < 0 or not tag:
        return None
    dur = _to_float(raw.get("duration"), 1.0) or 1.0
    dur = max(0.4, min(dur, 2.5))
    return {"t": round(t, 3), "tag": tag, "duration": round(dur, 3)}


def _norm_broll(raw: Any) -> list[dict]:
    out: list[dict] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        t = _to_float(item.get("t"))
        noun = str(item.get("noun") or item.get("tag") or "").strip().lower()
        if t is None or t < 0 or not noun:
            continue
        dur = _to_float(item.get("duration"), 1.5) or 1.5
        dur = max(0.8, min(dur, 3.0))
        out.append({"t": round(t, 3), "noun": noun, "duration": round(dur, 3)})
    return out


def _norm_sfx(raw: Any) -> list[dict]:
    out: list[dict] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        t = _to_float(item.get("t"))
        kind = str(item.get("kind") or "").strip().lower()
        if t is None or t < 0 or kind not in VALID_SFX_KINDS:
            continue
        out.append({"t": round(t, 3), "kind": kind})
    return out


def _norm_flashes(raw: Any) -> list[dict]:
    """White-flash transition beats. Lone numbers → t with defaults."""
    out: list[dict] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if isinstance(item, (int, float)):
            t = _to_float(item)
            if t is not None and t >= 0:
                out.append({"t": round(t, 3), "dur": 0.12, "style": "soft"})
        elif isinstance(item, dict):
            t = _to_float(item.get("t"))
            if t is None or t < 0:
                continue
            dur = _to_float(item.get("dur"), None)
            if dur is None:
                dur = _to_float(item.get("duration"), 0.12)
            dur = max(0.05, min(dur or 0.12, 0.30))
            style = str(item.get("style") or "soft").strip().lower()
            if style not in ("soft", "hard"):
                style = "soft"
            out.append({"t": round(t, 3), "dur": round(dur, 3), "style": style})
    return out[:6]  # hard cap — sporadic, not strobing


def _norm_cuts(raw: Any) -> list[dict]:
    """Drop-spans (ABSOLUTE VOD seconds) for jump-cut compression. Merges
    overlaps; the keep-span computation (clip_cuts.py) enforces the budget."""
    out: list[dict] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        a = _to_float(item.get("drop_start", item.get("start")))
        b = _to_float(item.get("drop_end", item.get("end")))
        if a is None or b is None or b <= a:
            continue
        out.append({"drop_start": round(a, 3), "drop_end": round(b, 3)})
    out.sort(key=lambda d: d["drop_start"])
    merged: list[dict] = []
    for c in out:
        if merged and c["drop_start"] <= merged[-1]["drop_end"] + 0.05:
            merged[-1]["drop_end"] = max(merged[-1]["drop_end"], c["drop_end"])
        else:
            merged.append(c)
    return merged[:12]


def _norm_emphasis(raw: Any) -> list[int]:
    if not isinstance(raw, list):
        return []
    out: list[int] = []
    for x in raw:
        i = _to_int(x)
        if i is not None and i >= 0:
            out.append(i)
    return out


def normalize(plan: Any) -> dict[str, Any]:
    """Coerce whatever vision returned into a clean edit_plan dict."""
    if not isinstance(plan, dict):
        plan = {}

    profile = plan.get("profile")
    if profile is not None:
        profile = str(profile).strip().lower() or None

    preset = plan.get("caption_preset")
    if preset is not None:
        preset = str(preset).strip().lower()
        if preset not in VALID_PRESETS:
            preset = None

    return {
        "profile":          profile,
        "zoom_punches":     _norm_zoom_punches(plan.get("zoom_punches")),
        "freeze_at":        _norm_freeze(plan.get("freeze_at")),
        "slow_mo":          _norm_slow_mo(plan.get("slow_mo")),
        "meme_cutaway":     _norm_meme(plan.get("meme_cutaway")),
        "broll_inserts":    _norm_broll(plan.get("broll_inserts")),
        "sfx_cues":         _norm_sfx(plan.get("sfx_cues")),
        "caption_emphasis": _norm_emphasis(plan.get("caption_emphasis")),
        "caption_preset":   preset,
        "chat_overlay":     bool(plan.get("chat_overlay")) if "chat_overlay" in plan else None,
        "flashes":          _norm_flashes(plan.get("flashes")),
        "cuts":             _norm_cuts(plan.get("cuts")),
    }


# Default plan when vision didn't emit one — empty arrays + null fields.
EMPTY_PLAN: dict[str, Any] = {
    "profile":          None,
    "zoom_punches":     [],
    "freeze_at":        None,
    "slow_mo":          None,
    "meme_cutaway":     None,
    "broll_inserts":    [],
    "sfx_cues":         [],
    "caption_emphasis": [],
    "caption_preset":   None,
    "chat_overlay":     None,
    "flashes":          [],
    "cuts":             [],
}


def _cli() -> int:
    import argparse, json, sys
    ap = argparse.ArgumentParser()
    ap.add_argument("--validate", help="path to JSON file with an edit_plan to validate")
    args = ap.parse_args()
    if args.validate:
        with open(args.validate, "r", encoding="utf-8") as f:
            raw = json.load(f)
        print(json.dumps(normalize(raw), indent=2))
    else:
        print(json.dumps(EMPTY_PLAN, indent=2))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_cli())
