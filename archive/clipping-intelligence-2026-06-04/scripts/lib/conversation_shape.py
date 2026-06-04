"""conversation_shape.py — Tier-4 Phase 4.2.

Stdlib feature extractor that turns a Whisper transcript (with optional
M1 diarization speaker labels) into a per-Pass-B-chunk structural summary
the LLM consumes alongside the raw transcript.

Outputs ``/tmp/clipper/conversation_shape.json`` keyed by chunk index. Pass B,
Pass D, and Stage 6 all read this file.

Six sub-components:

1. Turn graph builder — speaker share + run + change-count summary.
2. Off-screen-intrusion detector — flags new speakers appearing in a
   previously single-speaker chunk. The Lacy-penthouse signal.
3. Discourse-marker scanner — regex over transcript text mapped to classes
   (story_opener, claim_stake, pushback, topic_pivot, info_ramble_marker,
   agreement, concession). Lexicon ships in config/discourse_markers.json.
4. Topic-boundary detector — TextTiling-style cosine drop between adjacent
   60s bag-of-words windows.
5. Monologue-run extractor — contiguous runs where one speaker holds
   >=80% of the floor for >=20s. The "informational ramble" signal.
6. Interruption detector — speaker-A end > speaker-B start (overlap)
   using WhisperX word-level timestamps.

CLI:
    python3 scripts/lib/conversation_shape.py \
        --transcript /tmp/clipper/transcript.json \
        --start 600 --end 900
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Discourse marker lexicon (loaded from config/discourse_markers.json with
# fallback to the built-in defaults below). Lexicon is hot-editable.
# ---------------------------------------------------------------------------

DEFAULT_DISCOURSE_MARKERS: Dict[str, List[str]] = {
    "story_opener": [
        r"\blet me tell you\b",
        r"\bso (check|let me tell|listen to) this\b",
        r"\bhere'?s the thing\b",
        r"\byou ever (had|seen|heard|been)\b",
        r"\bi('m| am) gonna tell you\b",
        r"\bback when i\b",
        r"\bstory time\b",
    ],
    "claim_stake": [
        r"\bi('m| am) (telling|gonna tell|saying) you\b",
        r"\btrust me\b",
        r"\blisten\b",
        r"\bi (have|own|made|built|got)\b",
        r"\bthis is (my|mine)\b",
        r"\bi swear\b",
        r"\b(no|i'?m) (cap|capping|kidding)\b",
    ],
    "pushback": [
        r"\bnah\b",
        r"\bno way\b",
        r"\bwait wait( wait)?\b",
        r"\bhold on\b",
        r"\bare you serious\b",
        r"\bwhat\?",
        r"\bhuh\?",
        r"\byou'?re kidding\b",
        r"\bbro what\b",
        r"\bare you (kidding|joking)\b",
    ],
    "concession": [
        r"\bok(ay)? (you('re|'re) right|fine|fair)\b",
        r"\byou'?re right\b",
        r"\b(yeah|yea|alright) (you|that)('s| is)\b",
        r"\bmy bad\b",
        r"\bi'?ll admit\b",
        r"\bfair (point|enough)\b",
        r"\bok(ay)? i (was|am) wrong\b",
    ],
    "topic_pivot": [
        r"\banyway(s)?\b",
        r"\bactually\b",
        r"\bon a different note\b",
        r"\bside note\b",
        r"\bunrelated\b",
        r"\bspeaking of\b",
        r"\bbut yeah\b",
    ],
    "info_ramble_marker": [
        r"\bthe thing about\b",
        r"\bwhat people don'?t (realize|know)\b",
        r"\bthe reality is\b",
        r"\blet me explain\b",
        r"\bthe (problem|issue) (is|with)\b",
        r"\bif you (think|look) about it\b",
        r"\bhere'?s why\b",
    ],
    "agreement": [
        r"\bexactly\b",
        r"\bfacts\b",
        r"\bfor real\b",
        r"\bsame\b",
        r"\b(100|hundred)( percent| %)\b",
    ],
    "question": [
        r"\?$",
    ],
}


def load_discourse_markers(path: Optional[str] = None) -> Dict[str, List[re.Pattern]]:
    """Compile the discourse-marker lexicon. Falls back to defaults when the
    config file is missing or malformed (so the pipeline never breaks because
    a user edited the JSON wrong)."""
    raw = DEFAULT_DISCOURSE_MARKERS
    cfg_path = Path(path or os.environ.get(
        "CLIP_DISCOURSE_MARKERS",
        "/root/.openclaw/discourse_markers.json",
    ))
    if cfg_path.exists():
        try:
            user_raw = json.loads(cfg_path.read_text(encoding="utf-8"))
            if isinstance(user_raw, dict):
                raw = {**raw, **user_raw}
        except (json.JSONDecodeError, OSError):
            pass
    compiled: Dict[str, List[re.Pattern]] = {}
    for cls, patterns in raw.items():
        regs: List[re.Pattern] = []
        for p in patterns:
            try:
                regs.append(re.compile(p, re.IGNORECASE))
            except re.error:
                continue
        if regs:
            compiled[cls] = regs
    return compiled


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def analyze_chunk(
    segments: Sequence[Dict[str, Any]],
    chunk_start: float,
    chunk_end: float,
    *,
    markers: Optional[Dict[str, List[re.Pattern]]] = None,
    monologue_min_share: float = 0.80,
    monologue_min_run_s: float = 20.0,
    off_screen_quiet_s: float = 30.0,
) -> Dict[str, Any]:
    """Compute the conversation-shape summary for a single Pass B chunk.

    ``segments`` is the slice of ``transcript.json`` segments overlapping the
    chunk window. Each segment is expected to have ``start``, ``end``, ``text``
    and (optionally) ``speaker`` and ``words``.
    """
    markers = markers if markers is not None else load_discourse_markers()
    window = [s for s in segments if s.get("end", 0) > chunk_start and s.get("start", 0) < chunk_end]

    speakers_summary = _speaker_summary(window)
    turn_changes, turn_change_times = _turn_changes(window)
    interruptions = _detect_interruptions(window)
    off_screen = _detect_off_screen_intrusions(
        segments=segments,
        window=window,
        chunk_start=chunk_start,
        quiet_s=off_screen_quiet_s,
    )
    monologue_runs = _detect_monologue_runs(
        window,
        min_share=monologue_min_share,
        min_run_s=monologue_min_run_s,
    )
    discourse_markers = _scan_discourse_markers(window, markers)
    topic_boundaries = _detect_topic_boundaries(window, chunk_start, chunk_end)

    return {
        "chunk_start": float(chunk_start),
        "chunk_end": float(chunk_end),
        "speakers": speakers_summary,
        "turn_changes": turn_changes,
        "turn_change_times": turn_change_times,
        "interruptions": interruptions,
        "off_screen_intrusions": off_screen,
        "topic_boundaries": topic_boundaries,
        "discourse_markers": discourse_markers,
        "monologue_runs": monologue_runs,
    }


def serialize_for_prompt(shape: Dict[str, Any], *, max_chars: int = 900) -> str:
    """Render a chunk's shape dict into a compact prompt block. Used by Pass B,
    Pass D, and Stage 6. Truncated at ``max_chars`` to keep prompt budget tight."""
    if not shape:
        return ""
    lines: List[str] = []
    speakers = shape.get("speakers") or []
    if speakers:
        sp_strs = ", ".join(
            f"{s['id']} {int(round(s.get('share', 0) * 100))}% (longest run {int(s.get('longest_run_s', 0))}s)"
            for s in speakers
        )
        lines.append(f"Speakers: {sp_strs}")
    lines.append(f"Turn changes: {shape.get('turn_changes', 0)}, interruptions: {shape.get('interruptions', 0)}")
    off = shape.get("off_screen_intrusions") or []
    if off:
        lines.append(
            "Off-screen voice intrusions: "
            + ", ".join(f"{int(o['t'])}s ({o['from_speaker']}→{o['to_speaker']})" for o in off[:3])
        )
    runs = shape.get("monologue_runs") or []
    if runs:
        r0 = runs[0]
        lines.append(
            f"Longest monologue: {r0.get('speaker', '?')} from {int(r0.get('start', 0))}s "
            f"to {int(r0.get('end', 0))}s ({int(r0.get('duration_s', 0))}s, {r0.get('word_count', 0)} words)"
        )
    markers = shape.get("discourse_markers") or []
    if markers:
        by_class: Dict[str, int] = Counter(m["class"] for m in markers)
        cls_str = ", ".join(f"{c}×{n}" for c, n in by_class.most_common(6))
        lines.append(f"Discourse markers: {cls_str}")
    boundaries = shape.get("topic_boundaries") or []
    if boundaries:
        lines.append(f"Topic boundaries: {len(boundaries)} (max delta {max(b['delta'] for b in boundaries):.2f})")
    out = "\n".join(lines)
    if len(out) > max_chars:
        out = out[: max_chars - 3] + "..."
    return out


def run_for_chunks(
    transcript_path: str,
    chunks: Sequence[Tuple[float, float]],
    out_path: str,
    *,
    markers_path: Optional[str] = None,
) -> Dict[int, Dict[str, Any]]:
    """Compute shape for every chunk and write a JSON keyed by chunk index.

    ``chunks`` is a list of (chunk_start, chunk_end) tuples — the same boundaries
    Pass B uses. Stage 4 builds them and passes them in.
    """
    with open(transcript_path, "r", encoding="utf-8") as f:
        transcript = json.load(f)
    segments = transcript.get("segments") if isinstance(transcript, dict) else transcript
    if segments is None:
        segments = []

    markers = load_discourse_markers(markers_path)
    out: Dict[int, Dict[str, Any]] = {}
    for idx, (cs, ce) in enumerate(chunks):
        out[idx] = analyze_chunk(segments, float(cs), float(ce), markers=markers)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({str(k): v for k, v in out.items()}, f)
    return out


def load_for_chunk(path: str, idx: int) -> Optional[Dict[str, Any]]:
    """Read the precomputed shape for a single chunk index. Returns None when
    the file is missing or the index is out of range — callers degrade gracefully."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data.get(str(idx))


# ---------------------------------------------------------------------------
# Sub-components
# ---------------------------------------------------------------------------

def _speaker_summary(window: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    durations: Dict[str, float] = defaultdict(float)
    longest_run: Dict[str, float] = defaultdict(float)
    cur_speaker: Optional[str] = None
    cur_run_start: Optional[float] = None
    total = 0.0
    for s in window:
        sp = s.get("speaker") or "UNKNOWN"
        dur = max(0.0, float(s.get("end", 0)) - float(s.get("start", 0)))
        durations[sp] += dur
        total += dur
        if sp != cur_speaker:
            if cur_speaker is not None and cur_run_start is not None:
                run_len = float(s.get("start", 0)) - cur_run_start
                longest_run[cur_speaker] = max(longest_run[cur_speaker], run_len)
            cur_speaker = sp
            cur_run_start = float(s.get("start", 0))
    if cur_speaker is not None and cur_run_start is not None and window:
        run_len = float(window[-1].get("end", 0)) - cur_run_start
        longest_run[cur_speaker] = max(longest_run[cur_speaker], run_len)

    if total <= 0.0:
        return []
    summary = [
        {"id": sp, "share": dur / total, "longest_run_s": float(longest_run.get(sp, 0.0))}
        for sp, dur in durations.items()
    ]
    summary.sort(key=lambda x: x["share"], reverse=True)
    return summary


def _turn_changes(window: Sequence[Dict[str, Any]]) -> Tuple[int, List[float]]:
    changes = 0
    times: List[float] = []
    last: Optional[str] = None
    for s in window:
        sp = s.get("speaker") or "UNKNOWN"
        if last is not None and sp != last:
            changes += 1
            times.append(float(s.get("start", 0)))
        last = sp
    return changes, times


def _detect_interruptions(window: Sequence[Dict[str, Any]]) -> int:
    """Two segments overlap by >=0.3s with different speakers."""
    n = 0
    for i in range(len(window) - 1):
        a = window[i]
        b = window[i + 1]
        sp_a = a.get("speaker") or "UNKNOWN"
        sp_b = b.get("speaker") or "UNKNOWN"
        if sp_a == sp_b:
            continue
        overlap = float(a.get("end", 0)) - float(b.get("start", 0))
        if overlap >= 0.3:
            n += 1
    return n


def _detect_off_screen_intrusions(
    *,
    segments: Sequence[Dict[str, Any]],
    window: Sequence[Dict[str, Any]],
    chunk_start: float,
    quiet_s: float,
) -> List[Dict[str, Any]]:
    """A speaker that appeared in the chunk but did NOT appear in the
    ``quiet_s`` seconds preceding the chunk is flagged as an intrusion."""
    pre_window = [s for s in segments if s.get("end", 0) <= chunk_start and s.get("end", 0) > chunk_start - quiet_s]
    pre_speakers = {s.get("speaker") for s in pre_window if s.get("speaker")}
    intrusions: List[Dict[str, Any]] = []
    seen_in_chunk: set = set()
    prev_speaker: Optional[str] = None
    for s in window:
        sp = s.get("speaker") or "UNKNOWN"
        if sp not in pre_speakers and sp not in seen_in_chunk and prev_speaker is not None and sp != prev_speaker:
            intrusions.append({
                "t": float(s.get("start", 0)),
                "from_speaker": prev_speaker,
                "to_speaker": sp,
            })
        seen_in_chunk.add(sp)
        prev_speaker = sp
    return intrusions


def _detect_monologue_runs(
    window: Sequence[Dict[str, Any]],
    *,
    min_share: float,
    min_run_s: float,
) -> List[Dict[str, Any]]:
    if not window:
        return []
    runs: List[Dict[str, Any]] = []
    cur: Optional[Dict[str, Any]] = None
    for s in window:
        sp = s.get("speaker") or "UNKNOWN"
        start = float(s.get("start", 0))
        end = float(s.get("end", 0))
        text = s.get("text", "") or ""
        if cur is None or sp != cur["speaker"]:
            if cur and (cur["end"] - cur["start"]) >= min_run_s:
                runs.append(cur)
            cur = {"speaker": sp, "start": start, "end": end, "word_count": len(text.split())}
        else:
            cur["end"] = end
            cur["word_count"] += len(text.split())
    if cur and (cur["end"] - cur["start"]) >= min_run_s:
        runs.append(cur)
    runs.sort(key=lambda r: r["end"] - r["start"], reverse=True)
    for r in runs:
        r["duration_s"] = round(r["end"] - r["start"], 2)
    # Note: min_share isn't enforced per-run here because we already merge
    # consecutive same-speaker segments — the run IS the speaker by construction.
    # min_share is left in the API for future use (e.g. accepting interleaved
    # backchannels under a threshold).
    _ = min_share
    return runs[:5]


def _scan_discourse_markers(
    window: Sequence[Dict[str, Any]],
    markers: Dict[str, List[re.Pattern]],
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for s in window:
        text = s.get("text", "") or ""
        if not text.strip():
            continue
        for cls, patterns in markers.items():
            for rx in patterns:
                m = rx.search(text)
                if m:
                    out.append({
                        "t": float(s.get("start", 0)),
                        "speaker": s.get("speaker") or "UNKNOWN",
                        "marker": m.group(0).lower(),
                        "class": cls,
                    })
                    break
    return out


def _detect_topic_boundaries(
    window: Sequence[Dict[str, Any]],
    chunk_start: float,
    chunk_end: float,
    *,
    bin_s: float = 60.0,
    min_delta: float = 0.5,
) -> List[Dict[str, Any]]:
    if not window:
        return []
    bins: Dict[int, Counter] = defaultdict(Counter)
    for s in window:
        mid = (float(s.get("start", 0)) + float(s.get("end", 0))) / 2.0
        bin_idx = int((mid - chunk_start) // bin_s)
        if bin_idx < 0:
            continue
        text = (s.get("text", "") or "").lower()
        for tok in re.findall(r"[a-z]{3,}", text):
            bins[bin_idx][tok] += 1
    if len(bins) < 2:
        return []
    keys = sorted(bins)
    boundaries: List[Dict[str, Any]] = []
    for i in range(len(keys) - 1):
        a = bins[keys[i]]
        b = bins[keys[i + 1]]
        sim = _cosine_counter(a, b)
        delta = 1.0 - sim
        if delta >= min_delta:
            boundaries.append({
                "t": chunk_start + (keys[i + 1]) * bin_s,
                "delta": round(delta, 3),
            })
    return boundaries


def _cosine_counter(a: Counter, b: Counter) -> float:
    if not a or not b:
        return 0.0
    common = set(a) & set(b)
    num = sum(a[k] * b[k] for k in common)
    den_a = math.sqrt(sum(v * v for v in a.values()))
    den_b = math.sqrt(sum(v * v for v in b.values()))
    if den_a == 0 or den_b == 0:
        return 0.0
    return num / (den_a * den_b)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(description="Tier-4 conversation_shape extractor")
    parser.add_argument("--transcript", required=True, help="path to transcript.json")
    parser.add_argument("--start", type=float, required=True, help="chunk start (seconds)")
    parser.add_argument("--end", type=float, required=True, help="chunk end (seconds)")
    parser.add_argument("--markers", default=None, help="path to discourse_markers.json")
    args = parser.parse_args(argv)

    with open(args.transcript, "r", encoding="utf-8") as f:
        tr = json.load(f)
    segments = tr.get("segments") if isinstance(tr, dict) else tr
    markers = load_discourse_markers(args.markers)
    shape = analyze_chunk(segments or [], args.start, args.end, markers=markers)
    json.dump(shape, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
