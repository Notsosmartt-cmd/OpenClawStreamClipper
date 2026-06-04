#!/usr/bin/env python3
"""Chat-signal feature extraction — Phase 2.3 of the 2026 upgrade.

Takes a chat JSONL file (one message per line, each with at least a
``t`` second offset and a ``text``) and exposes a ``ChatFeatures`` object
that answers per-window questions:

- ``msgs_per_sec``, z-score vs rolling baseline
- ``emote_density[category]`` — count of emote tokens in each category
- ``unique_chatters`` — distinct ``user`` count in the window
- ``phrase_hits[category]`` — recurring-phrase regex bursts
- ``sub_count``, ``bit_count``, ``donation_count``, ``raid_count`` —
  HARD ground truth from event messages (``type`` != "chat")

The module is stdlib-only and graceful: a missing chat file, a missing
config, or a corrupted line all collapse to "no features available" and
return neutral defaults so callers can short-circuit.

Expected JSONL shape (produced by ``chat_fetch.py``):

    {"t": 12.4, "user": "xqc", "text": "KEKW that was insane",
     "emotes": ["KEKW"], "type": "chat"}
    {"t": 45.0, "user": "x", "text": "", "emotes": [],
     "type": "sub", "tier": "1000"}

Per Song et al. 2021 (EPJ Data Science 10:43) — chat emote signatures hit
~0.75 F1 on epic-moment detection, within 0.05 of vision alone — so this
module's output is a first-class moment-detection signal, not just a
prompt-decoration helper.
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

DEFAULT_EMOTES_PATH = Path(
    os.environ.get("CLIP_EMOTES_PATH", "/root/.openclaw/emotes.json")
)
DEFAULT_CHAT_CONFIG_PATH = Path(
    os.environ.get("CLIP_CHAT_CONFIG", "/root/.openclaw/chat.json")
)


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


# ---------------------------------------------------------------------------
# Emote dictionary
# ---------------------------------------------------------------------------


def load_emote_dict(path: Optional[str] = None) -> Tuple[Dict[str, str], Dict[str, List[re.Pattern]]]:
    """Return (emote → category, category → [compiled phrase regex]).

    Missing / unparseable config → ({}, {}) — the feature module collapses
    to zeros for emote categories, which is a reasonable neutral value.
    """
    data = _load_json(Path(path) if path else DEFAULT_EMOTES_PATH)
    emote_to_cat: Dict[str, str] = {}
    for cat, spec in (data.get("categories") or {}).items():
        emotes = spec.get("emotes", []) if isinstance(spec, dict) else []
        for emote in emotes:
            emote_to_cat[emote] = cat

    phrase_patterns: Dict[str, List[re.Pattern]] = {}
    phrase_spec = (data.get("phrase_patterns") or {}).get("patterns") or {}
    for cat, patterns in phrase_spec.items():
        compiled = []
        for p in patterns:
            try:
                compiled.append(re.compile(p, re.IGNORECASE))
            except re.error:
                continue
        if compiled:
            phrase_patterns[cat] = compiled
    return emote_to_cat, phrase_patterns


# ---------------------------------------------------------------------------
# Core features object
# ---------------------------------------------------------------------------


_EVENT_TYPE_MAP = {
    "sub": "sub_count",
    "subscription": "sub_count",
    "resub": "sub_count",
    "subgift": "sub_count",
    "subscribe": "sub_count",
    "community_sub": "sub_count",
    "bit": "bit_count",
    "bits": "bit_count",
    "cheer": "bit_count",
    "raid": "raid_count",
    "donation": "donation_count",
    "donate": "donation_count",
    "tip": "donation_count",
}


class ChatFeatures:
    """Lightweight window-query API over a loaded chat JSONL.

    Core state: ``_per_sec[t]`` is a list of normalized message dicts for
    second ``t``. Heavy-compute queries (``window``) iterate this dict for
    the requested range — fine for VOD-scale chats (≤ 1 M messages for a
    12 h stream) and keeps memory bounded.
    """

    def __init__(
        self,
        messages: List[dict],
        emote_to_cat: Dict[str, str],
        phrase_patterns: Dict[str, List[re.Pattern]],
    ) -> None:
        self._per_sec: Dict[int, List[dict]] = {}
        for m in messages:
            t = m.get("t")
            if not isinstance(t, (int, float)):
                continue
            sec = int(t)
            self._per_sec.setdefault(sec, []).append(m)
        self._emote_to_cat = emote_to_cat
        self._phrase_patterns = phrase_patterns
        self._max_sec = max(self._per_sec.keys(), default=0)
        self._total_msgs = sum(len(v) for v in self._per_sec.values())

    # --- Basic queries ---

    @property
    def message_count(self) -> int:
        return self._total_msgs

    @property
    def duration_sec(self) -> int:
        return self._max_sec

    def is_empty(self) -> bool:
        return self._total_msgs == 0

    # --- Window computation ---

    def window(self, start: float, end: float, baseline_window_sec: int = 300) -> Dict:
        """Compute features for the window ``[start, end]`` (seconds).

        ``baseline_window_sec`` sets the rolling comparison used for
        ``z_score`` — the baseline mean/sd is computed over a 2× wider
        symmetric window around the requested one, excluding the requested
        window itself so spikes don't mask themselves.
        """
        s = max(0, int(math.floor(start)))
        e = max(s, int(math.ceil(end)))

        msgs: List[dict] = []
        for sec in range(s, e + 1):
            msgs.extend(self._per_sec.get(sec, ()))

        duration = max(1, e - s)
        msg_count = len(msgs)
        msgs_per_sec = msg_count / duration

        # Rolling baseline for z-score — use a window of ±`baseline_window_sec`
        # on either side, exclude the target window.
        base_start = max(0, s - baseline_window_sec)
        base_end = min(self._max_sec, e + baseline_window_sec)
        base_counts = []
        for sec in range(base_start, base_end + 1):
            if s <= sec <= e:
                continue
            base_counts.append(len(self._per_sec.get(sec, ())))
        z_score = 0.0
        baseline_per_sec = 0.0
        if base_counts:
            mean = sum(base_counts) / len(base_counts)
            baseline_per_sec = mean
            var = sum((c - mean) ** 2 for c in base_counts) / max(1, len(base_counts))
            sd = math.sqrt(var)
            if sd > 0:
                z_score = (msgs_per_sec - mean) / sd

        # Emote density by category + raw count.
        emote_counts: Dict[str, int] = {}
        emote_top: Dict[str, int] = {}
        for m in msgs:
            for emote in m.get("emotes") or ():
                emote_top[emote] = emote_top.get(emote, 0) + 1
                cat = self._emote_to_cat.get(emote)
                if cat:
                    emote_counts[cat] = emote_counts.get(cat, 0) + 1

        # Phrase pattern hits.
        phrase_hits: Dict[str, int] = {}
        for m in msgs:
            text = m.get("text") or ""
            for cat, patterns in self._phrase_patterns.items():
                for rx in patterns:
                    if rx.search(text):
                        phrase_hits[cat] = phrase_hits.get(cat, 0) + 1
                        break

        # Unique chatters.
        users = {(m.get("user") or "") for m in msgs if m.get("user")}
        unique_chatters = len(users)

        # Hard ground-truth events (sub / bit / cheer / raid / donation).
        event_counts: Dict[str, int] = {
            "sub_count": 0,
            "bit_count": 0,
            "raid_count": 0,
            "donation_count": 0,
        }
        for m in msgs:
            etype = (m.get("type") or "").lower()
            if etype and etype != "chat":
                bucket = _EVENT_TYPE_MAP.get(etype)
                if bucket:
                    event_counts[bucket] += int(m.get("count") or 1)

        # Burst factor relative to baseline (safe for small baselines).
        if baseline_per_sec > 0:
            burst_factor = msgs_per_sec / baseline_per_sec
        else:
            burst_factor = float("inf") if msgs_per_sec > 0 else 0.0

        # Top 5 emotes by count.
        top_emotes = sorted(emote_top.items(), key=lambda kv: kv[1], reverse=True)[:5]

        return {
            "start": s,
            "end": e,
            "msgs": msg_count,
            "msgs_per_sec": round(msgs_per_sec, 3),
            "baseline_per_sec": round(baseline_per_sec, 3),
            "burst_factor": round(burst_factor, 2) if burst_factor != float("inf") else None,
            "z_score": round(z_score, 2),
            "unique_chatters": unique_chatters,
            "emote_density": emote_counts,
            "top_emotes": [(e, c) for e, c in top_emotes],
            "phrase_hits": phrase_hits,
            "sub_count": event_counts["sub_count"],
            "bit_count": event_counts["bit_count"],
            "raid_count": event_counts["raid_count"],
            "donation_count": event_counts["donation_count"],
        }


# ---------------------------------------------------------------------------
# File I/O helpers
# ---------------------------------------------------------------------------


def load_chat_jsonl(path: str) -> List[dict]:
    """Read a JSONL file and return the list of message dicts.

    Tolerant to partial files: lines that fail JSON parsing are dropped
    with a single stderr note. Empty / missing file → empty list.
    """
    p = Path(path)
    if not p.exists():
        return []
    messages: List[dict] = []
    bad_lines = 0
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            messages.append(json.loads(line))
        except json.JSONDecodeError:
            bad_lines += 1
    if bad_lines:
        print(
            f"[CHAT] {p.name}: {bad_lines} malformed lines dropped (kept {len(messages)})",
            file=sys.stderr,
        )
    return messages


def load(path: str, emotes_path: Optional[str] = None) -> ChatFeatures:
    """Top-level convenience: load chat file + emote dict in one call."""
    msgs = load_chat_jsonl(path)
    emote_to_cat, phrase_patterns = load_emote_dict(emotes_path)
    return ChatFeatures(msgs, emote_to_cat, phrase_patterns)


# ---------------------------------------------------------------------------
# Grounding-cascade bridge
# ---------------------------------------------------------------------------


def denylist_event_map(chat_config: Optional[dict] = None) -> Dict[str, Dict[str, str]]:
    """Return the category → {keyword: event_count_key} map from chat.json.

    Used by ``cascade_check(hard_events=...)`` to decide which denylist
    hits should be promoted to a hard-fail when the corresponding event
    count is zero. Empty / missing config → empty dict. The top-level
    ``description`` key inside the ``ground_truth`` block is a human note,
    not a category, so we filter to dict-valued entries only.
    """
    cfg = chat_config if chat_config is not None else _load_json(DEFAULT_CHAT_CONFIG_PATH)
    raw = cfg.get("ground_truth", {}) or {}
    return {k: v for k, v in raw.items() if isinstance(v, dict)}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="Chat features (Phase 2.3)")
    ap.add_argument("--chat", required=True, help="path to chat JSONL")
    ap.add_argument("--start", type=float, required=True)
    ap.add_argument("--end", type=float, required=True)
    ap.add_argument("--emotes", default=None, help="path to emotes.json")
    ap.add_argument(
        "--baseline-window", type=int, default=300, help="seconds for z-score baseline"
    )
    args = ap.parse_args()

    features = load(args.chat, args.emotes)
    if features.is_empty():
        print("(no chat data)")
        sys.exit(0)
    result = features.window(args.start, args.end, args.baseline_window)
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    _cli()
