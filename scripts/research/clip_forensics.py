#!/usr/bin/env python3
"""clip_forensics.py — offline clip decomposer (research lane).

Implements Phase 1 of concepts/clip-forensics-research-2026-06: read a curated
reference clip, run the shared semantic sensing layer (audio_sense) + scene-cut
detection, and emit a timeline/EDL JSON of the clip's editing "essence". When a
`<name>.notes.json` sidecar exists it scores recovered-vs-annotated events.

This is OFFLINE research tooling (not the live pipeline): heavier deps are fine,
and everything is failure-soft so a missing backend just leaves its section
empty rather than crashing. Phase 2-4 capabilities (censor, caption OCR,
optical-flow motion, exact-SFX fingerprint, LLM style-profile synthesis) are
stubbed with TODOs.

Usage:
    python scripts/research/clip_forensics.py --clip ReemKnocksClip.MP4 [--out t.json]
    python scripts/research/clip_forensics.py --clip /abs/path.mp4 --no-cuda
A bare name resolves against reference_clips/ at the repo root.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
REF_DIR = REPO / "reference_clips"
LIB_DIR = REPO / "scripts" / "lib"
sys.path.insert(0, str(LIB_DIR))  # so `import audio_sense` works as a script


def _log(msg: str) -> None:
    print(f"[clip_forensics] {msg}", file=sys.stderr)


def _resolve_clip(clip: str) -> Path | None:
    p = Path(clip)
    if p.is_file():
        return p
    cand = REF_DIR / clip
    if cand.is_file():
        return cand
    # case-insensitive match within reference_clips/
    if REF_DIR.is_dir():
        for f in REF_DIR.iterdir():
            if f.name.lower() == clip.lower():
                return f
    return None


def _ffprobe(clip: Path) -> tuple[float, float]:
    """(duration_s, fps). Best-effort; (0.0, 30.0) on failure."""
    dur, fps = 0.0, 30.0
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "format=duration:stream=r_frame_rate",
             "-of", "json", str(clip)],
            capture_output=True, text=True, timeout=30)
        data = json.loads(r.stdout or "{}")
        dur = float((data.get("format") or {}).get("duration") or 0.0)
        rate = ((data.get("streams") or [{}])[0]).get("r_frame_rate") or "30/1"
        num, _, den = rate.partition("/")
        fps = float(num) / float(den or 1) if den else float(num)
    except Exception as e:
        _log(f"ffprobe failed ({type(e).__name__}); defaults")
    return round(dur, 3), round(fps, 3)


def _detect_cuts(clip: Path) -> list[dict]:
    """Scene-cut timestamps via PySceneDetect ContentDetector (fast-cut friendly).
    [] if scenedetect is not installed."""
    try:
        from scenedetect import detect, ContentDetector
    except Exception as e:
        _log(f"PySceneDetect unavailable ({type(e).__name__}); cuts=[]")
        return []
    def _secs(tc) -> float:
        # version-robust: 0.7 deprecates get_seconds() in favor of .seconds
        v = getattr(tc, "seconds", None)
        return float(v) if v is not None else float(tc.get_seconds())
    try:
        scenes = detect(str(clip), ContentDetector())
        # Each scene boundary after the first is a cut point.
        cuts = [{"t": round(_secs(s), 3)} for s, _ in scenes[1:]]
        return cuts
    except Exception as e:
        _log(f"PySceneDetect failed ({type(e).__name__}: {e}); cuts=[]")
        return []


# Sounds editors use to censor a curse (CLAP/PANNs labels). beep_censor + quack
# are the unambiguous ones; the rest can also co-occur with an audible curse.
_CENSOR_SFX = ("beep_censor", "quack", "beep", "bleep", "airhorn", "boom", "scratch")


def _detect_censor(words: list[dict], events: list[dict], tol: float = 0.6) -> list[dict]:
    """Phase 2 — censor detection (concepts/clip-forensics-research-2026-06 RQ5):
    (1) a profane word (better-profanity) with a co-located censor SFX = high-conf
    'quack-over-the-curse'; (2) a beep/quack SFX in a word-gap (curse bleeped out
    of the transcript) = medium-conf. Masks the curse in the output. [] soft."""
    try:
        from better_profanity import profanity
        profanity.load_censor_words()
    except Exception as e:
        _log(f"better-profanity unavailable ({type(e).__name__}); censor=[]")
        return []
    censor_sfx = [e for e in events
                  if any(k in str(e.get("label", "")).lower() for k in _CENSOR_SFX)]
    out: list[dict] = []
    used: list[int] = []
    # Pass 1: profane word + nearby censor SFX (high confidence).
    for w in words:
        tok = str(w.get("word", "")).strip(" .,!?\"'-").lower()
        if not tok:
            continue
        try:
            if not profanity.contains_profanity(tok):
                continue
        except Exception:
            continue
        wt = (float(w["start"]) + float(w["end"])) / 2.0
        near = min((e for e in censor_sfx), key=lambda e: abs(e["t"] - wt), default=None)
        if near is not None and abs(near["t"] - wt) <= tol:
            used.append(id(near))
            out.append({"t": round(wt, 3), "word": "***", "sfx": near["label"],
                        "via": "word+sfx", "confidence": "high", "score": near.get("score")})
    # Pass 2: unambiguous censor SFX sitting in a word-gap (curse bleeped away).
    for e in censor_sfx:
        if id(e) in used or str(e.get("label", "")).lower() not in ("beep_censor", "quack", "beep", "bleep"):
            continue
        if not any(float(w["start"]) <= e["t"] <= float(w["end"]) for w in words):
            out.append({"t": e["t"], "word": "?", "sfx": e["label"],
                        "via": "sfx-gap", "confidence": "medium", "score": e.get("score")})
    return sorted(out, key=lambda c: c["t"])


def _music_bed(events: list[dict], words: list[dict], onsets: list[float]) -> list[dict]:
    """Phase 2 — music-bed spans + an `added` heuristic (no TF/Demucs): merge
    music-ish events (CLAP suspense_music + PANNs *music*) into spans, then flag
    `added` when a span starts on an abrupt onset AND overlaps speech — i.e. a
    bed an editor dropped under the talking, vs stream-native ambient music."""
    music_ev = sorted((e for e in events if "music" in str(e.get("label", "")).lower()),
                      key=lambda e: e["t"])
    spans: list[dict] = []
    for e in music_ev:
        suspense = e.get("label") == "suspense_music"
        if spans and e["t"] <= spans[-1]["end"] + 2.0:
            spans[-1]["end"] = max(spans[-1]["end"], e["end"])
            spans[-1]["_suspense"] = spans[-1].get("_suspense") or suspense
        else:
            spans.append({"start": e["t"], "end": e["end"], "kind": "music", "_suspense": suspense})
    out: list[dict] = []
    for sp in spans:
        abrupt = any(abs(o - sp["start"]) <= 0.4 for o in onsets)
        speech = any(float(w["start"]) < sp["end"] and float(w["end"]) > sp["start"] for w in words)
        rec = {"start": round(sp["start"], 3), "end": round(sp["end"], 3), "kind": "music",
               "abrupt_onset": abrupt, "added": bool(abrupt and speech)}
        if sp.get("_suspense"):
            rec["mood"], rec["mood_conf"] = "suspenseful?", "low"
        out.append(rec)
    return out


def _score_against_notes(timeline: dict, notes: dict, tol: float = 1.0) -> dict:
    """Rough recall: for each human-annotated event, is there a detected signal
    (audio_event / cut / music-start) within tol seconds? Sanity metric only."""
    detected_t: list[float] = []
    detected_t += [e["t"] for e in timeline.get("audio_events", [])]
    detected_t += [c["t"] for c in timeline.get("cuts", [])]
    detected_t += [m["start"] for m in timeline.get("music", [])]
    detected_t += [c["t"] for c in timeline.get("censor", [])]
    ann = [a for a in (notes.get("events") or []) if isinstance(a.get("t"), (int, float))]
    rows = []
    hit = 0
    for a in ann:
        t = float(a["t"])
        nearest = min((abs(t - d) for d in detected_t), default=None)
        matched = nearest is not None and nearest <= tol
        hit += 1 if matched else 0
        rows.append({"t": t, "kind": a.get("kind"), "matched": matched,
                     "nearest_detected_delta_s": round(nearest, 3) if nearest is not None else None})
    return {"annotated": len(ann), "matched": hit,
            "recall": round(hit / len(ann), 3) if ann else None, "rows": rows}


def decompose(clip: Path, *, device: str | None = None,
              window_s: float = 1.0, hop_s: float = 0.5) -> dict:
    import audio_sense  # lazy; from LIB_DIR

    cache_dir = REF_DIR / ".cache"
    cache_dir.mkdir(exist_ok=True)
    stem = clip.stem
    dur, fps = _ffprobe(clip)

    events = audio_sense.sense_events(
        str(clip), window_s=window_s, hop_s=hop_s, device=device,
        cache_path=str(cache_dir / f"{stem}.events.json"))
    words = audio_sense.transcribe_words(
        str(clip), device=device, cache_path=str(cache_dir / f"{stem}.words.json"))
    onsets = audio_sense.onset_times(str(clip))
    cuts = _detect_cuts(clip)

    timeline = {
        "clip": clip.name,
        "duration_s": dur,
        "fps": fps,
        "n_words": len(words),
        "audio_events": events,
        "music": _music_bed(events, words, onsets),          # Phase 2: spans + `added` heuristic
        "cuts": cuts,
        "censor": _detect_censor(words, events),             # Phase 2: profanity + censor-SFX
        # --- Phase 3-4 stubs (deferred per the research phasing) ---
        "captions": None,   # TODO Phase 3: EasyOCR caption density / wps
        "motion": [],       # TODO Phase 3: OpenCV optical-flow zoom/punch detection
        "sfx_matches": [],  # TODO Phase 4: audfprint vs a seeded SFX library
        "style_profile": None,  # TODO Phase 4: LLM synthesis -> edit_plan/sfx_cues shape
    }

    notes_path = clip.with_suffix(".notes.json")
    if not notes_path.exists():
        notes_path = clip.parent / f"{stem}.notes.json"
    if notes_path.exists():
        try:
            notes = json.loads(notes_path.read_text(encoding="utf-8"))
            timeline["notes_eval"] = _score_against_notes(timeline, notes)
        except Exception as e:
            _log(f"notes eval failed ({type(e).__name__})")
    return timeline


def _cli() -> int:
    ap = argparse.ArgumentParser(description="Decompose a reference clip into an editing-essence timeline")
    ap.add_argument("--clip", required=True, help="path or a name under reference_clips/")
    ap.add_argument("--out", help="write timeline JSON here (default: stdout)")
    ap.add_argument("--cuda", action="store_true",
                    help="use GPU. Default is CPU — safer for this offline tool; "
                         "Windows CUDA can hang the PANNs/whisper checkpoint load.")
    ap.add_argument("--no-cuda", action="store_true", help="(default) force CPU")
    ap.add_argument("--window", type=float, default=1.0)
    ap.add_argument("--hop", type=float, default=0.5)
    args = ap.parse_args()

    clip = _resolve_clip(args.clip)
    if clip is None:
        _log(f"clip not found: {args.clip!r} (looked in {REF_DIR})")
        return 1
    _log(f"decomposing {clip.name} ...")
    device = None if args.cuda else "cpu"  # default CPU (offline; avoids Windows CUDA hangs)
    timeline = decompose(clip, device=device, window_s=args.window, hop_s=args.hop)
    text = json.dumps(timeline, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        _log(f"wrote {args.out}")
    else:
        print(text)
    # Console summary
    _log(f"events={len(timeline['audio_events'])} cuts={len(timeline['cuts'])} "
         f"music={len(timeline['music'])} censor={len(timeline['censor'])} "
         f"words={timeline['n_words']} dur={timeline['duration_s']}s")
    if "notes_eval" in timeline and timeline["notes_eval"].get("recall") is not None:
        ne = timeline["notes_eval"]
        _log(f"notes recall={ne['recall']} ({ne['matched']}/{ne['annotated']} annotated events recovered)")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
