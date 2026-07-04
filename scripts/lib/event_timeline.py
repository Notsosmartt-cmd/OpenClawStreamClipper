#!/usr/bin/env python3
"""event_timeline.py — fused symbolic multimodal timeline (upgrade plan Phase 1a).

Merges the pipeline's separate modality streams — transcript words, semantic
audio events (CLAP via audio_sense), motion punches (visual_sense), scene cuts,
and optionally chat bursts — into ONE time-ordered symbolic stream, and renders
a window of it as compact text for an LLM to reason over the CONJUNCTION of
senses (the multimodal-fusion / anomaly-proposer work: concepts/
multimodal-fusion-2026-07, concepts/case-incongruity-comedy).

Pure logic, stdlib only: callers inject already-computed event lists (so this
unit-tests without running any model, and the live pipeline decides when/whether
to compute CLAP over a whole VOD). Failure-soft: bad/missing streams are skipped.

Event dict shape (normalized):  {"t": float, "kind": str, ...payload}
  kinds: TEXT (word/phrase) · AUDIO (label,score) · MOTION (energy,rel) ·
         CUT · CHAT (text and/or velocity)
"""
from __future__ import annotations

from typing import Any, Callable, Iterable


def _f(x, default=0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def build_timeline(words: Iterable[dict] | None = None,
                   audio_events: Iterable[dict] | None = None,
                   motion_events: Iterable[dict] | None = None,
                   cuts: Iterable[dict] | None = None,
                   chat_events: Iterable[dict] | None = None) -> list[dict]:
    """Merge modality streams into one time-sorted list of normalized events."""
    tl: list[dict] = []
    for w in (words or []):
        t = w.get("start", w.get("t"))
        txt = str(w.get("word", w.get("text", ""))).strip()
        if t is None or not txt:
            continue
        tl.append({"t": round(_f(t), 3), "kind": "TEXT", "text": txt,
                   "end": round(_f(w.get("end", t)), 3)})
    for e in (audio_events or []):
        if e.get("t") is None:
            continue
        tl.append({"t": round(_f(e["t"]), 3), "kind": "AUDIO",
                   "label": str(e.get("label", "")), "score": round(_f(e.get("score")), 3)})
    for m in (motion_events or []):
        if m.get("t") is None:
            continue
        tl.append({"t": round(_f(m["t"]), 3), "kind": "MOTION",
                   "energy": round(_f(m.get("energy")), 2), "rel": round(_f(m.get("rel")), 2)})
    for c in (cuts or []):
        if c.get("t") is None:
            continue
        tl.append({"t": round(_f(c["t"]), 3), "kind": "CUT"})
    for ch in (chat_events or []):
        if ch.get("t") is None:
            continue
        ev = {"t": round(_f(ch["t"]), 3), "kind": "CHAT"}
        if ch.get("text"):
            ev["text"] = str(ch["text"])[:200]
        if ch.get("velocity") is not None:
            ev["velocity"] = round(_f(ch.get("velocity")), 2)
        tl.append(ev)
    tl.sort(key=lambda e: (e["t"], e["kind"]))
    return tl


def _phrase_words(text_events: list[dict], gap_s: float = 0.8) -> list[dict]:
    """Collapse consecutive TEXT events into short phrases for readable prompts."""
    out: list[dict] = []
    cur: list[str] = []
    start = last = None
    for w in text_events:
        if start is None:
            start, cur, last = w["t"], [w["text"]], w.get("end", w["t"])
        elif w["t"] - last <= gap_s and len(cur) < 20:
            cur.append(w["text"]); last = w.get("end", w["t"])
        else:
            out.append({"t": start, "text": " ".join(cur)})
            start, cur, last = w["t"], [w["text"]], w.get("end", w["t"])
    if cur:
        out.append({"t": start, "text": " ".join(cur)})
    return out


def render_for_prompt(timeline: list[dict], t0: float | None = None,
                      t1: float | None = None, *, max_lines: int = 60) -> str:
    """Render a [t0,t1] window as compact text lines for an LLM. Groups TEXT into
    phrases; audio/motion/cut/chat pass through. Non-TEXT lines lead so the model
    sees the reaction cues alongside the words."""
    win = [e for e in timeline
           if (t0 is None or e["t"] >= t0) and (t1 is None or e["t"] <= t1)]
    texts = _phrase_words([e for e in win if e["kind"] == "TEXT"])
    lines: list[tuple[float, str]] = []
    for p in texts:
        lines.append((p["t"], f'[t={p["t"]:.1f}] TEXT "{p["text"]}"'))
    for e in win:
        if e["kind"] == "AUDIO":
            lines.append((e["t"], f'[t={e["t"]:.1f}] AUDIO {e["label"]}({e["score"]:.2f})'))
        elif e["kind"] == "MOTION":
            lines.append((e["t"], f'[t={e["t"]:.1f}] MOTION {e["rel"]:.1f}x'))
        elif e["kind"] == "CUT":
            lines.append((e["t"], f'[t={e["t"]:.1f}] CUT'))
        elif e["kind"] == "CHAT":
            bits = []
            if e.get("velocity") is not None:
                bits.append(f'burst {e["velocity"]:.1f}x')
            if e.get("text"):
                bits.append(f'"{e["text"]}"')
            lines.append((e["t"], f'[t={e["t"]:.1f}] CHAT {" ".join(bits)}'))
    lines.sort(key=lambda x: x[0])
    if len(lines) > max_lines:
        lines = lines[:max_lines]
    return "\n".join(text for _, text in lines)


def window_events(timeline: list[dict], t0: float, t1: float,
                  kinds: tuple[str, ...] | None = None) -> list[dict]:
    """Events within [t0, t1], optionally filtered to given kinds."""
    return [e for e in timeline if t0 <= e["t"] <= t1
            and (kinds is None or e["kind"] in kinds)]


def save(timeline: list[dict], path: str) -> None:
    import json
    from pathlib import Path
    try:
        Path(path).write_text(json.dumps(timeline), encoding="utf-8")
    except OSError:
        pass


if __name__ == "__main__":  # tiny smoke
    tl = build_timeline(
        words=[{"word": "ever", "start": 6.0, "end": 6.3},
               {"word": "heard", "start": 6.3, "end": 6.6},
               {"word": "of", "start": 6.6, "end": 6.7},
               {"word": "george", "start": 6.7, "end": 7.1}],
        audio_events=[{"t": 7.5, "label": "laughter", "score": 0.41}],
        motion_events=[{"t": 7.2, "energy": 52.0, "rel": 6.3}],
        cuts=[{"t": 7.9}])
    print(render_for_prompt(tl))
