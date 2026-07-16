#!/usr/bin/env python3
"""evidence_packets.py — J1 of plan-s45-text-judge-2026-07.

Deterministic per-candidate EVIDENCE PACKETS for the S4.5 batched text judge:
verbatim transcript window (with speaker turns) + audio-event marks + the
proposer's claim. Built entirely from artifacts the pipeline already has —
zero LLM cost. The judge must re-derive from raw evidence, never trust the
proposer's summary alone, so the transcript text is VERBATIM.

Hard size cap (~900 tokens ≈ 3,600 chars) enforced by middle-truncation of
the transcript block — head and tail survive (setups and payoffs live at the
edges of a window; the middle is the safest cut).

Failure-soft: any missing artifact degrades the packet (section omitted),
never raises out of build_packets().
"""
from __future__ import annotations

import json
from pathlib import Path

MAX_PACKET_CHARS = 3600          # ≈900 tokens at ~4 chars/token
_WINDOW_PAD_S = 5.0              # context beyond the claimed clip bounds
_DEFAULT_HALF_WINDOW_S = 30.0    # when the moment has no clip bounds
_MAX_AUDIO_MARKS = 8

_PRIORS_CACHE: dict | None = None


def _shape_priors() -> dict:
    """J7 (2026-07-16): per-species SHAPE PRIORS from config/shape_priors.json
    (shared source with the Pass-B prompt block). Failure-soft -> {}."""
    global _PRIORS_CACHE
    if _PRIORS_CACHE is None:
        try:
            cfg = json.loads((Path(__file__).resolve().parents[2] / "config" /
                              "shape_priors.json").read_text(encoding="utf-8"))
            _PRIORS_CACHE = cfg.get("subtypes") or {}
        except Exception:
            _PRIORS_CACHE = {}
    return _PRIORS_CACHE


def _norms_line(subtype: str) -> str:
    p = _shape_priors().get(str(subtype or "").strip().lower())
    if not p:
        return ""
    bits = []
    if p.get("payoff_pct_typical") is not None:
        bits.append(f"payoff ~{p['payoff_pct_typical']}% into the clip")
    if p.get("duration_s_typical"):
        bits.append(f"~{p['duration_s_typical']}s typical")
    if p.get("arc_typical"):
        bits.append(f"arc: {p['arc_typical']}")
    if p.get("note"):
        bits.append(str(p["note"]))
    return "SPECIES NORMS (typical, not required): " + "; ".join(bits)


def load_words(transcript_json_path) -> list[dict]:
    """Tolerant word loader: accepts the stage-2 transcript JSON as either
    {"segments":[{..., "words":[{word,start,end[,speaker]}]}]} or a flat
    word list. Returns [{word, start, end, speaker}] sorted by start."""
    try:
        data = json.loads(Path(transcript_json_path).read_text(encoding="utf-8"))
    except Exception:
        return []
    words: list[dict] = []

    def _push(w, seg_speaker=None):
        try:
            words.append({
                "word": str(w.get("word") or w.get("text") or "").strip(),
                "start": float(w.get("start", 0.0)),
                "end": float(w.get("end", w.get("start", 0.0))),
                "speaker": w.get("speaker") or seg_speaker,
            })
        except (TypeError, ValueError):
            pass

    if isinstance(data, dict) and isinstance(data.get("segments"), list):
        for seg in data["segments"]:
            seg_speaker = seg.get("speaker")
            for w in (seg.get("words") or []):
                _push(w, seg_speaker)
    elif isinstance(data, list):
        for w in data:
            if isinstance(w, dict):
                _push(w)
    words.sort(key=lambda w: w["start"])
    return [w for w in words if w["word"]]


def _window_bounds(moment: dict) -> tuple[float, float]:
    ts = float(moment.get("timestamp", 0) or 0)
    start = moment.get("clip_start")
    end = moment.get("clip_end")
    if start is not None and end is not None:
        return max(0.0, float(start) - _WINDOW_PAD_S), float(end) + _WINDOW_PAD_S
    return max(0.0, ts - _DEFAULT_HALF_WINDOW_S), ts + _DEFAULT_HALF_WINDOW_S


def _speaker_turns(words: list[dict], lo: float, hi: float) -> str:
    """Verbatim transcript of [lo, hi] as speaker-turn lines."""
    turns: list[tuple[str, list[str]]] = []
    for w in words:
        if w["end"] < lo or w["start"] > hi:
            continue
        spk = str(w.get("speaker") or "SPEAKER")
        if turns and turns[-1][0] == spk:
            turns[-1][1].append(w["word"])
        else:
            turns.append((spk, [w["word"]]))
    return "\n".join(f"{spk}: {' '.join(toks)}" for spk, toks in turns)


def _audio_marks(events, lo: float, hi: float, ts: float) -> str:
    """Compact audio-event lines relative to the moment timestamp."""
    if not events:
        return ""
    picked = []
    for e in events:
        try:
            t = float(e.get("t", e.get("start", -1)))
        except (TypeError, ValueError):
            continue
        if lo <= t <= hi:
            picked.append((t, str(e.get("label") or e.get("kind") or "event")))
    picked.sort()
    return "\n".join(f"[t{'+' if t >= ts else ''}{t - ts:.0f}s] {lab}"
                     for t, lab in picked[:_MAX_AUDIO_MARKS])


def _truncate_middle(text: str, budget: int) -> str:
    if len(text) <= budget:
        return text
    head = budget * 3 // 5
    tail = budget - head - 24
    return text[:head] + "\n[... middle trimmed ...]\n" + text[-max(0, tail):]


def build_packet(idx: int, moment: dict, words: list[dict],
                 audio_events=None, max_chars: int = MAX_PACKET_CHARS) -> str:
    """One candidate's evidence packet (plain text block, hard-capped)."""
    lo, hi = _window_bounds(moment)
    ts = float(moment.get("timestamp", 0) or 0)
    claim = (
        f"CANDIDATE {idx}\n"
        f"claim: category={moment.get('category') or moment.get('primary_category')}"
        f" subtype={moment.get('subtype') or '?'}"
        f" pattern={moment.get('primary_pattern') or '?'}"
        f" proposer_score={moment.get('score')}\n"
        f"bounds: t={ts:.0f}s window=[{lo:.0f}s..{hi:.0f}s]"
        f" segment={moment.get('segment_type') or '?'}\n"
        f"proposer_why: {str(moment.get('why') or '')[:200]}"
    )
    marks = _audio_marks(audio_events, lo, hi, ts)
    marks_block = f"\nAUDIO MARKS:\n{marks}" if marks else ""
    norms = _norms_line(moment.get("subtype"))
    norms_block = f"\n{norms}" if norms else ""
    transcript = _speaker_turns(words, lo, hi) or "(no transcript in window)"
    fixed = len(claim) + len(marks_block) + len(norms_block) + len("\nTRANSCRIPT (verbatim):\n")
    transcript = _truncate_middle(transcript, max(400, max_chars - fixed))
    return f"{claim}{norms_block}{marks_block}\nTRANSCRIPT (verbatim):\n{transcript}"


def build_packets(moments: list[dict], transcript_json_path,
                  audio_events_path=None) -> list[str]:
    """Packets for every moment, index-aligned (1-based idx in the text).
    Never raises; artifact failures degrade individual sections."""
    words = load_words(transcript_json_path)
    events = None
    if audio_events_path:
        try:
            raw = json.loads(Path(audio_events_path).read_text(encoding="utf-8"))
            events = raw.get("events") if isinstance(raw, dict) else raw
            if not isinstance(events, list):
                events = None
        except Exception:
            events = None
    out = []
    for i, m in enumerate(moments, 1):
        try:
            out.append(build_packet(i, m, words, events))
        except Exception:
            out.append(f"CANDIDATE {i}\nclaim: (packet build failed)\n"
                       f"proposer_why: {str(m.get('why') or '')[:200]}")
    return out


def _selftest() -> int:
    words_flat = [
        {"word": w, "start": 100 + i * 0.4, "end": 100.3 + i * 0.4,
         "speaker": "SPEAKER_00" if i < 30 else "SPEAKER_01"}
        for i, w in enumerate(["yo"] * 60)
    ]
    import tempfile, os
    with tempfile.TemporaryDirectory() as td:
        tp = Path(td) / "t.json"
        tp.write_text(json.dumps(words_flat), encoding="utf-8")
        m = {"timestamp": 110, "clip_start": 102, "clip_end": 118,
             "category": "funny", "subtype": "banter_roast", "score": 0.7,
             "why": "roast exchange"}
        pkts = build_packets([m], tp, None)
        assert len(pkts) == 1 and "CANDIDATE 1" in pkts[0]
        assert "SPEAKER_00:" in pkts[0] and "SPEAKER_01:" in pkts[0], "turns missing"
        assert "banter_roast" in pkts[0]
        # cap test: absurdly long transcript INSIDE the window must be trimmed
        big = [{"word": "blah" * 3, "start": 97 + i * 0.008, "end": 97 + i * 0.008 + 0.005}
               for i in range(3000)]
        tp.write_text(json.dumps(big), encoding="utf-8")
        p2 = build_packets([m], tp, None)[0]
        assert len(p2) <= MAX_PACKET_CHARS + 200, f"cap breached: {len(p2)}"
        assert "middle trimmed" in p2
        # segments-shape loader
        tp.write_text(json.dumps({"segments": [{"speaker": "S0", "words": [
            {"word": "hi", "start": 105, "end": 105.2}]}]}), encoding="utf-8")
        p3 = build_packets([m], tp, None)[0]
        assert "S0: hi" in p3
    print("evidence_packets selftest: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
