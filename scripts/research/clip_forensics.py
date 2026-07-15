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
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
REF_DIR = REPO / "reference_clips"
# Owner notes live in a dedicated subfolder so the clip files aren't buried among
# sidecars (2026-07-13 reorg). Reads fall back to the legacy top-level sidecar so a
# clip dropped in with an old-style <stem>.notes.json still resolves.
NOTES_DIR = REF_DIR / "notes"
LIB_DIR = REPO / "scripts" / "lib"
sys.path.insert(0, str(LIB_DIR))  # so `import audio_sense` works as a script


def _log(msg: str) -> None:
    print(f"[clip_forensics] {msg}", file=sys.stderr)


def notes_path(clip, *, for_write: bool = False) -> Path:
    """Resolve a clip's .notes.json. Canonical home: reference_clips/notes/<stem>.notes.json.
    `clip` may be a Path, a filename, or a bare stem. On READ, falls back to a legacy
    top-level sidecar (reference_clips/<stem>.notes.json) when the grouped file is absent.
    On WRITE (for_write=True) it always targets the grouped notes/ dir."""
    stem = Path(clip).stem
    grouped = NOTES_DIR / f"{stem}.notes.json"
    if for_write or grouped.exists():
        return grouped
    legacy = REF_DIR / f"{stem}.notes.json"
    return legacy if legacy.exists() else grouped


def iter_notes() -> list[Path]:
    """Every .notes.json across the corpus: the grouped notes/ dir first, then any
    legacy top-level sidecar not already shadowed by a grouped file."""
    out: list[Path] = []
    seen: set[str] = set()
    if NOTES_DIR.is_dir():
        for p in sorted(NOTES_DIR.glob("*.notes.json")):
            seen.add(p.name[: -len(".notes.json")])
            out.append(p)
    for p in sorted(REF_DIR.glob("*.notes.json")):
        if p.name[: -len(".notes.json")] not in seen:
            out.append(p)
    return out


# Hard per-stage wall-clock caps (seconds). Generous — a real run on a curated
# short clip finishes in well under these — but they guarantee a single hung
# stage can't wedge the process for hours. Total runtime <= sum of these caps.
# Scale with --deadline-scale or the CLIP_FORENSICS_DEADLINE_SCALE env var.
_STAGE_DEADLINES = {
    "audio_sense": 600.0,   # CLAP/PANNs window inference
    "transcribe": 300.0,    # faster-whisper
    "onset": 60.0,          # numpy onset (fast; was librosa, which hung)
    "scenedetect": 180.0,   # PySceneDetect
    "motion": 180.0,        # cv2 frame-diff (Phase 3, default on)
    "caption_ocr": 600.0,   # EasyOCR (Phase 3, opt-in; downloads + slower)
    "style_profile": 150.0,  # local-LLM synthesis (Phase 4b)
}


def _with_deadline(label: str, seconds: float, fn, default):
    """Run fn() under a hard wall-clock cap. A daemon worker does the work; if it
    overruns we log, ABANDON the thread (it dies when the process exits), and
    return `default` so the run still completes with a partial result.

    This is the only reliable way to bound C-extension hangs (PANNs init,
    librosa, a wedged CUDA load): they ignore Python signals/exceptions, so a
    try/except or signal.alarm can't rescue them — but the main thread can simply
    stop waiting. Returns (value, status) where status is ok|timeout|error.
    """
    box = {"v": default, "err": None}

    def _runner():
        try:
            box["v"] = fn()
        except BaseException as e:  # noqa: BLE001 — record, never propagate to main
            box["err"] = f"{type(e).__name__}: {e}"

    t = threading.Thread(target=_runner, name=f"cf:{label}", daemon=True)
    t.start()
    t.join(seconds)
    if t.is_alive():
        _log(f"[deadline] stage {label!r} exceeded {seconds:.0f}s — SKIPPED "
             f"(partial result; worker abandoned, dies with process)")
        return default, "timeout"
    if box["err"] is not None:
        _log(f"[deadline] stage {label!r} failed: {box['err']}")
        return default, "error"
    return box["v"], "ok"


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


def _music_bed(events: list[dict], words: list[dict], onsets: list[float],
               *, merge_gap_s: float = 3.0, min_span_s: float = 1.5) -> list[dict]:
    """Phase 2 — music-bed spans + an `added` heuristic (no TF/Demucs): merge
    music-ish events (CLAP music/suspense_music + PANNs *music*) into spans, then
    flag `added` when a span starts on an abrupt onset AND overlaps speech — i.e.
    a bed an editor dropped under the talking, vs stream-native ambient music.

    Because a quiet bed under speech scores below the SFX floor, music carries a
    LOWER per-label CLAP threshold (config) — so this requires a SUSTAINED span
    (>= min_span_s, or >= 2 windows) before calling it a bed, suppressing the lone
    low-confidence blips that lower floor would otherwise admit. merge_gap_s is
    generous because a bed dips below threshold mid-window under loud speech."""
    music_ev = sorted((e for e in events if "music" in str(e.get("label", "")).lower()),
                      key=lambda e: e["t"])
    spans: list[dict] = []
    for e in music_ev:
        suspense = e.get("label") == "suspense_music"
        if spans and e["t"] <= spans[-1]["end"] + merge_gap_s:
            spans[-1]["end"] = max(spans[-1]["end"], e["end"])
            spans[-1]["_suspense"] = spans[-1].get("_suspense") or suspense
            spans[-1]["_n"] += 1
        else:
            spans.append({"start": e["t"], "end": e["end"], "kind": "music",
                          "_suspense": suspense, "_n": 1})
    out: list[dict] = []
    for sp in spans:
        if (sp["end"] - sp["start"]) < min_span_s and sp["_n"] < 2:
            continue  # lone low-confidence window — not a real bed
        abrupt = any(abs(o - sp["start"]) <= 0.5 for o in onsets)
        under_speech = any(float(w["start"]) < sp["end"] and float(w["end"]) > sp["start"] for w in words)
        starts_mid = sp["start"] > 2.0   # dropped in after the open, not ambient from t=0
        # An editor-added bed plays UNDER speech and either drops in abruptly OR
        # starts mid-clip (the music-onset signal is the span start itself — more
        # reliable than energy flux, which a bed faded-in under speech won't trip).
        added = bool(under_speech and (abrupt or starts_mid))
        rec = {"start": round(sp["start"], 3), "end": round(sp["end"], 3), "kind": "music",
               "abrupt_onset": abrupt, "under_speech": under_speech,
               "starts_mid_clip": starts_mid, "added": added}
        if sp.get("_suspense"):
            rec["mood"], rec["mood_conf"] = "suspenseful?", "low"
        out.append(rec)
    return out


def _family(kind: str) -> str:
    """Map an annotation `kind` OR a detected-signal type onto one detector family,
    so precision/recall is measured per-detector instead of lumping every signal.
    The families mirror what each detector actually produces (see _detected_by_family)."""
    k = (kind or "").lower()
    if "music" in k:
        return "music"
    if "censor" in k or "beep" in k or "quack" in k:
        return "censor"
    if "cut" in k or "transition" in k:
        return "cut"
    if "cold" in k or "teaser" in k or "cold_open" in k:
        return "cold_open"          # no detector today -> always a miss (informative)
    return "sfx"                     # sfx/boom/whoosh/riser/... and unrecognised sounds


def _detected_by_family(timeline: dict) -> dict:
    """Detected signal timestamps grouped into the same families as _family()."""
    return {
        "sfx": [float(e["t"]) for e in (timeline.get("audio_events") or []) if "t" in e],
        # a music SPAN offers two annotate-able instants: the in and the out
        "music": [float(m["start"]) for m in (timeline.get("music") or []) if "start" in m]
                 + [float(m["end"]) for m in (timeline.get("music") or []) if "end" in m],
        "censor": [float(c["t"]) for c in (timeline.get("censor") or []) if "t" in c],
        "cut": [float(c["t"]) for c in (timeline.get("cuts") or []) if "t" in c],
        "cold_open": [],            # no detector
    }


def _score_against_notes(timeline: dict, notes: dict, tol: float = 1.0) -> dict:
    """Per-detector precision + recall against a human-corrected .notes.json.

    RECALL (per family): of the events the owner annotated for this family, how many
    have a detected signal of the SAME family within tol? Low recall = the detector
    MISSES real cues (the owner had to add them).
    PRECISION (per family): of the signals this family's detector fired, how many sit
    near an annotated event? Low precision = the detector cries wolf (owner deleted
    them). This is the number that answers "can it reliably detect" — but only once
    the draft has been CORRECTED (a raw --draft-notes file scores ~1.0 trivially
    because it was generated FROM these same detections)."""
    det = _detected_by_family(timeline)
    ann = [a for a in (notes.get("events") or []) if isinstance(a.get("t"), (int, float))]

    # --- overall (any-family) recall: back-compat with the old sanity metric ---
    all_det = [t for ts in det.values() for t in ts]
    rows, hit = [], 0
    for a in ann:
        t = float(a["t"])
        nearest = min((abs(t - d) for d in all_det), default=None)
        matched = nearest is not None and nearest <= tol
        hit += 1 if matched else 0
        rows.append({"t": t, "kind": a.get("kind"), "family": _family(a.get("kind")),
                     "matched": matched,
                     "nearest_detected_delta_s": round(nearest, 3) if nearest is not None else None})

    # --- per-family precision + recall ---
    fams = set(det) | {_family(a.get("kind")) for a in ann}
    by_family: dict[str, dict] = {}
    for f in sorted(fams):
        af = [float(a["t"]) for a in ann if _family(a.get("kind")) == f]
        df = det.get(f, [])
        recalled = sum(1 for t in af if any(abs(t - d) <= tol for d in df))
        matched_d = sum(1 for d in df if any(abs(d - t) <= tol for t in af))
        by_family[f] = {
            "annotated": len(af), "recalled": recalled,
            "recall": round(recalled / len(af), 3) if af else None,
            "detected": len(df), "matched_detected": matched_d,
            # precision needs ground truth: with zero annotations of this family we
            # can't say a detection is "wrong" (e.g. cuts, which the owner doesn't
            # annotate) -> None, not 0.0.
            "precision": round(matched_d / len(df), 3) if (df and af) else None,
        }
    return {"annotated": len(ann), "matched": hit,
            "recall": round(hit / len(ann), 3) if ann else None,
            "by_family": by_family, "rows": rows,
            "is_draft": bool(notes.get("_draft"))}


def _draft_notes(timeline: dict) -> dict:
    """Phase 7.1 — pre-fill a .notes.json from the tool's OWN detections so the owner
    CORRECTS a draft (delete false positives, add missed cues) instead of annotating
    from a blank page. Correcting a draft is what turns _score_against_notes into a
    real precision/recall measurement (see its docstring). `_auto:true` marks each
    line as machine-proposed; the owner deletes wrong ones and clears `_draft` when
    done. Music spans -> music_in/out; censor -> censor; the most salient distinct
    audio events -> sfx (deduped to ~1.5s, score-ranked, capped)."""
    events: list[dict] = []
    for m in timeline.get("music") or []:
        tag = " (editor-added)" if m.get("added") else ""
        events.append({"t": round(float(m["start"]), 2), "kind": "music_in", "_auto": True,
                       "note": f"detected music bed{tag}, mood={m.get('mood')}"})
        events.append({"t": round(float(m["end"]), 2), "kind": "music_out", "_auto": True,
                       "note": "music bed ends"})
    for c in timeline.get("censor") or []:
        events.append({"t": round(float(c["t"]), 2), "kind": "censor", "_auto": True,
                       "note": f"via={c.get('via')} sfx={c.get('sfx')}"})
    cand = sorted((e for e in (timeline.get("audio_events") or [])
                   if float(e.get("score", 0)) >= 0.35), key=lambda e: -float(e.get("score", 0)))
    picked: list[dict] = []
    for e in cand:
        if all(abs(float(e["t"]) - float(p["t"])) > 1.5 for p in picked):
            picked.append(e)
        if len(picked) >= 10:
            break
    for e in sorted(picked, key=lambda e: float(e["t"])):
        events.append({"t": round(float(e["t"]), 2), "kind": "sfx", "_auto": True,
                       "note": f"detected '{e.get('label')}' ({e.get('source')} {e.get('score')})"})
    events.sort(key=lambda x: x["t"])
    return {
        "clip": timeline.get("clip"),
        "_draft": True,
        "_instructions": ("DRAFT auto-generated from detections. CORRECT it: delete lines "
                          "the tool got wrong, ADD cues it missed (cold_open_teaser, sfx on "
                          "the punchline, music the tool didn't hear), fix why_it_works, then "
                          "DELETE the \"_draft\" key. Corrected files feed corpus precision/recall."),
        "source": "competitor account / platform",
        "why_it_works": "",
        "events": events,
    }


def _llm_config() -> tuple[str, str]:
    """(model, url) for the local LLM: env CLIP_* > config/models.json > default,
    with the Docker-era host.docker.internal rewritten to localhost (bare-metal)."""
    model = os.environ.get("CLIP_TEXT_MODEL")
    url = os.environ.get("CLIP_LLM_URL")
    if not model or not url:
        try:
            cfg = json.loads((REPO / "config" / "models.json").read_text(encoding="utf-8"))
            model = model or cfg.get("text_model")
            url = url or cfg.get("llm_url")
        except Exception:
            pass
    model = model or "qwen/qwen3.6-35b-a3b"
    url = (url or "http://localhost:1234").replace("host.docker.internal", "localhost").rstrip("/")
    return model, url


def _profile_summary(timeline: dict) -> dict:
    """Token-light digest of the timeline for the LLM (counts + key facts, not the
    raw arrays — keeps the prompt small and the synthesis grounded)."""
    from collections import Counter
    dur = float(timeline.get("duration_s") or 0.0)
    cuts = timeline.get("cuts") or []
    labels = Counter(e.get("label") for e in (timeline.get("audio_events") or []))
    music = timeline.get("music") or []
    censor = timeline.get("censor") or []
    caps = timeline.get("captions") if isinstance(timeline.get("captions"), dict) else None
    return {
        "duration_s": round(dur, 2),
        "cuts": len(cuts),
        "cuts_per_min": round(len(cuts) / (dur / 60.0), 2) if dur else None,
        "avg_shot_s": round(dur / (len(cuts) + 1), 2) if dur else None,
        "audio_events_top": labels.most_common(8),
        "music_spans": [{"start": m.get("start"), "end": m.get("end"),
                         "added_by_editor": m.get("added"), "mood": m.get("mood")} for m in music],
        "censor": [{"t": c.get("t"), "via": c.get("via"), "sfx": c.get("sfx")} for c in censor],
        "motion_spikes": len(timeline.get("motion") or []),
        "captions_words_per_s": caps.get("words_per_s") if caps else None,
        "n_spoken_words": timeline.get("n_words"),
    }


_STYLE_PROMPT = """You are a short-form video editing analyst. Below is an automated decomposition of a competitor clip that performs well on TikTok/Shorts. Produce a STYLE PROFILE another editor could follow to replicate its editing essence.

Decomposition (JSON):
{summary}

Output ONLY a JSON object (no prose, no markdown fences) with these keys:
- "summary": one sentence describing the editing style
- "pacing": {{"cuts_per_min": number, "feel": "frantic|brisk|measured|slow"}}
- "sfx_cues": array of {{"beat": "punchline|reveal|fail|transition|tension", "sound": "boom|scratch|whoosh|riser|...", "note": "..."}}
- "music": {{"used": boolean, "added_by_editor": boolean, "mood": "..."}}
- "censor_style": short string ("none"|"beep"|"quack"|...) with a brief note
- "hook": how the opening grabs attention (1 sentence)
- "replication_notes": array of 2-5 concrete, actionable editing instructions
Base every field on the decomposition; if a signal is absent, say so rather than inventing it."""


def _synthesize_style_profile(timeline: dict, *, timeout: float = 90.0) -> dict | None:
    """Phase 4b — turn the timeline into a replicable style profile via the local
    LLM (LM Studio). This is the clip's 'essence' as structured, reusable data.
    Failure-soft: returns None if LM Studio is unreachable or the reply doesn't
    parse (connection-refused returns ~instantly, so a down server costs nothing)."""
    try:
        import lmstudio  # from LIB_DIR (already on sys.path)
    except Exception as e:
        _log(f"lmstudio client unavailable ({type(e).__name__}); style_profile=None")
        return None
    model, url = _llm_config()
    prompt = _STYLE_PROMPT.format(summary=json.dumps(_profile_summary(timeline), default=str))
    reply = lmstudio.chat(prompt, model=model, url=url, timeout=timeout, max_tokens=900)
    if not reply:
        _log(f"LLM unreachable/empty (url={url} model={model}); style_profile=None")
        return None
    obj = lmstudio.loads_lenient(reply)  # tolerant of qwen unterminated-string / trailing-comma glitches
    if obj is None:
        _log("style_profile reply not parseable even leniently; None")
    return obj


def _detect_outro(dur: float, cuts: list[dict], words: list[dict],
                  captions) -> dict:
    """Detect the TikTok DOWNLOAD OUTRO (logo animation + @handle, ~2.5-3.5 s,
    auto-appended to downloaded TikToks) so `trim_end="auto"` can trim exactly
    the clips that have one — the owner's corpus is "most but NOT all", and a
    blanket 4 s cut costs real content on the clips without it.

    Shape: a hard CUT in the last ~6 s, NO speech after it (outros are always
    speechless), optionally confirmed by OCR text containing 'tiktok'/'@' after
    the cut. Returns {"start": float|None, "certain_no": bool, "why": str}."""
    last_word_end = max((float(w.get("end", 0) or 0) for w in words), default=0.0)
    if dur and last_word_end >= dur - 1.0:
        return {"start": None, "certain_no": True,
                "why": f"speech runs to {last_word_end:.1f}s of {dur:.1f}s — no outro"}
    cand = None
    for c in cuts:
        t = float(c.get("t", 0) or 0)
        if dur - 6.0 <= t <= dur - 1.2:
            cand = t if cand is None else max(cand, t)
    if cand is None:
        return {"start": None, "certain_no": False, "why": "no cut in the last 6s"}
    if last_word_end > cand + 0.3:
        return {"start": None, "certain_no": False,
                "why": f"speech continues past the last cut ({last_word_end:.1f}s > {cand:.1f}s)"}
    tail_txt = ""
    if isinstance(captions, dict):
        for s in captions.get("samples") or []:
            if float(s.get("t", 0) or 0) >= cand - 0.2:
                tail_txt += " " + str(s.get("text", "") or "")
    txt = tail_txt.lower()
    if txt.strip():
        if "tiktok" in txt or "@" in txt:
            return {"start": cand, "certain_no": False,
                    "why": f"cut@{cand:.2f}s + TikTok/@handle text after it"}
        return {"start": None, "certain_no": False,
                "why": "tail text after the last cut has no TikTok/@ marker"}
    return {"start": cand, "certain_no": False,
            "why": f"cut@{cand:.2f}s + speechless tail (no OCR text to confirm)"}


def _trim_signals(dur, trim_start, trim_end, events, words, onsets, cuts, motion, captions):
    """Restrict every signal to the analysis window [start, end], dropping
    intro/outro artifacts that aren't the creator's edit — most importantly the
    TikTok DOWNLOAD OUTRO (logo + @handle auto-appended to the last ~3 s of any
    downloaded TikTok). Without this, that outro's whoosh/logo animation and the
    persistent @handle caption get mis-logged as real editing cues, and (with a
    batch) silently poison every style profile. Returns
    (start, end, analyzed_dur, filtered events/words/onsets/cuts/motion/captions)."""
    start = max(0.0, float(trim_start or 0.0))
    end = (dur - max(0.0, float(trim_end or 0.0))) if dur else None
    if end is not None and end <= start:  # bad window -> ignore, analyze whole clip
        return 0.0, dur, dur, events, words, onsets, cuts, motion, captions
    hi = end if end is not None else float("inf")
    ev = [e for e in events if start <= float(e.get("t", 0)) <= hi]
    on = [o for o in onsets if start <= float(o) <= hi]
    cu = [c for c in cuts if start <= float(c.get("t", 0)) <= hi]
    mo = [m for m in motion if start <= float(m.get("t", 0)) <= hi]
    wo = [w for w in words if float(w.get("end", 0)) >= start and float(w.get("start", 0)) <= hi]
    cap = captions
    if isinstance(captions, dict) and captions.get("samples"):
        cap = dict(captions)
        cap["samples"] = [s for s in captions["samples"] if start <= float(s.get("t", 0)) <= hi]
        cap["n_text_frames"] = len(cap["samples"])
        cap["total_words"] = sum(int(s.get("n_words", 0)) for s in cap["samples"])
        wdur = (end if end is not None else dur) - start
        cap["words_per_s"] = round(cap["total_words"] / wdur, 2) if wdur > 0 else None
    analyzed = round((end if end is not None else dur) - start, 3)
    return start, (end if end is not None else dur), analyzed, ev, wo, on, cu, mo, cap


def decompose(clip: Path, *, device: str | None = None,
              window_s: float = 1.0, hop_s: float = 0.5,
              deadline_scale: float = 1.0, ocr: bool = False,
              llm: bool = True, trim_start: float = 0.0,
              trim_end: float | str = 0.0,   # float seconds, or "auto" (outro detection)
              cache_dir: Path | None = None) -> dict:
    import audio_sense  # lazy; from LIB_DIR
    import visual_sense  # lazy; from LIB_DIR

    # R2 (plan-reference-deconstruction): decomposing OUR produced clips uses a
    # separate cache dir so their events/words artifacts never mix into the
    # reference corpus cache. Default (None) = the reference cache, unchanged.
    cache_dir = Path(cache_dir) if cache_dir else (REF_DIR / ".cache")
    cache_dir.mkdir(parents=True, exist_ok=True)
    stem = clip.stem
    dur, fps = _ffprobe(clip)

    scale = max(0.05, float(deadline_scale))
    def _cap(name: str) -> float:
        return _STAGE_DEADLINES[name] * scale

    # Each heavy/hang-prone stage runs under a hard wall-clock cap (see
    # _with_deadline) so the whole run is bounded and never zombies.
    events, st_events = _with_deadline(
        "audio_sense", _cap("audio_sense"),
        lambda: audio_sense.sense_events(
            str(clip), window_s=window_s, hop_s=hop_s, device=device,
            cache_path=str(cache_dir / f"{stem}.events.json")), [])
    words, st_words = _with_deadline(
        "transcribe", _cap("transcribe"),
        lambda: audio_sense.transcribe_words(
            str(clip), device=device, cache_path=str(cache_dir / f"{stem}.words.json")), [])
    onsets, st_onset = _with_deadline(
        "onset", _cap("onset"), lambda: audio_sense.onset_times(str(clip)), [])
    cuts, st_cuts = _with_deadline(
        "scenedetect", _cap("scenedetect"), lambda: _detect_cuts(clip), [])
    motion, st_motion = _with_deadline(             # Phase 3a: cv2 frame-diff (default on)
        "motion", _cap("motion"), lambda: visual_sense.motion_events(str(clip)), [])
    if ocr:                                          # Phase 3b: EasyOCR (opt-in)
        captions, st_caption = _with_deadline(
            "caption_ocr", _cap("caption_ocr"),
            lambda: visual_sense.caption_ocr(str(clip), gpu=(device != "cpu")), None)
    else:
        captions, st_caption = None, "skipped"

    # trim_end="auto" (2026-07-15): detect the outro per clip instead of a blanket
    # cut — the corpus is "most but not all TikTok downloads". Detected -> trim at
    # the outro's own cut; certain-no (speech to the end) -> trim 0; unsure -> the
    # legacy 4.0s blanket (safe default for a TikTok-download corpus).
    outro_info = None
    if isinstance(trim_end, str):
        if trim_end.strip().lower() == "auto":
            outro_info = _detect_outro(dur, cuts, words, captions)
            if outro_info["start"] is not None:
                trim_end = max(0.0, dur - float(outro_info["start"]))
            elif outro_info["certain_no"]:
                trim_end = 0.0
            else:
                trim_end = 4.0
            outro_info["trim_end_applied"] = round(float(trim_end), 3)
            _log(f"outro auto: {outro_info['why']} -> trim_end={trim_end:.2f}s")
        else:
            trim_end = float(trim_end or 0)

    # Drop intro/outro (e.g. the TikTok download outro) BEFORE building music/
    # censor, so the derived signals + the LLM profile never see the artifact.
    trimmed = (float(trim_start or 0) > 0) or (float(trim_end or 0) > 0)
    win_start, win_end, analyzed, events, words, onsets, cuts, motion, captions = _trim_signals(
        dur, trim_start, trim_end, events, words, onsets, cuts, motion, captions)
    if trimmed:
        _log(f"trim: analyzing [{win_start:.2f}, {win_end:.2f}]s of {dur:.2f}s "
             f"(dropped {dur - analyzed:.2f}s of intro/outro)")

    timeline = {
        "clip": clip.name,
        "duration_s": analyzed,                              # true (analyzed) footage
        "source_duration_s": dur,                            # full file incl. any trimmed outro
        "analysis_window": {"start": round(win_start, 3), "end": round(win_end, 3)} if trimmed else None,
        "outro": outro_info,                                 # auto-trim decision (None = fixed trim)
        "fps": fps,
        "n_words": len(words),
        "audio_events": events,
        "music": _music_bed(events, words, onsets),          # Phase 2: spans + `added` heuristic
        "cuts": cuts,
        "censor": _detect_censor(words, events),             # Phase 2: profanity + censor-SFX
        "motion": motion,                                    # Phase 3a: cv2 motion punches
        "captions": captions,                                # Phase 3b: EasyOCR (None unless --ocr)
        # Per-stage watchdog status (ok|timeout|error|skipped) — makes a skipped/
        # hung stage visible in the output instead of looking like "no events".
        "_stages": {"audio_sense": st_events, "transcribe": st_words,
                    "onset": st_onset, "scenedetect": st_cuts,
                    "motion": st_motion, "caption_ocr": st_caption},
        # --- Phase 4 stubs (deferred) ---
        "sfx_matches": [],  # TODO Phase 4a: audfprint vs a seeded SFX library
        "style_profile": None,  # Phase 4b: filled by _synthesize_style_profile below
    }

    # Phase 4b: LLM synthesis of a replicable style profile from the full timeline.
    if llm:
        sp, st_sp = _with_deadline(
            "style_profile", _cap("style_profile"),
            lambda: _synthesize_style_profile(timeline), None)
        timeline["style_profile"] = sp
        timeline["_stages"]["style_profile"] = st_sp
    else:
        timeline["_stages"]["style_profile"] = "skipped"

    np_ = notes_path(clip)
    if np_.exists():
        try:
            notes = json.loads(np_.read_text(encoding="utf-8"))
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
    ap.add_argument("--deadline-scale", type=float,
                    default=float(os.environ.get("CLIP_FORENSICS_DEADLINE_SCALE", "1.0")),
                    help="scale all per-stage wall-clock caps (default 1.0; e.g. 2.0 for "
                         "very long clips, 0.25 to fail fast). Caps stop a hung stage "
                         "wedging the run.")
    ap.add_argument("--ocr", action="store_true",
                    default=os.environ.get("CLIP_FORENSICS_OCR") == "1",
                    help="run burned-in caption OCR (EasyOCR). Off by default — it "
                         "downloads ~75MB of weights on first use and is slower.")
    ap.add_argument("--no-llm", action="store_true",
                    default=os.environ.get("CLIP_FORENSICS_NO_LLM") == "1",
                    help="skip the LLM style-profile synthesis (Phase 4b). On by "
                         "default but failure-soft — a down LM Studio just yields null.")
    ap.add_argument("--trim-end", type=str,
                    default=os.environ.get("CLIP_FORENSICS_TRIM_END", "0"),
                    help="ignore the last N seconds — e.g. the ~3s TikTok DOWNLOAD "
                         "OUTRO (logo + @handle) auto-appended to downloaded clips — "
                         "or 'auto' to DETECT the outro per clip (trims exactly the "
                         "clips that have one; unsure falls back to 4s). Set once "
                         "(or via CLIP_FORENSICS_TRIM_END) for a whole batch.")
    ap.add_argument("--trim-start", type=float,
                    default=float(os.environ.get("CLIP_FORENSICS_TRIM_START", "0")),
                    help="ignore the first N seconds (intro card).")
    ap.add_argument("--draft-notes", action="store_true",
                    help="Phase 7.1: write a pre-filled <clip>.notes.json draft from "
                         "the detections for the owner to CORRECT. Refuses to overwrite "
                         "a file that has already been corrected (no _draft key).")
    ap.add_argument("--cache-dir", default=None,
                    help="R2: where events/words caches live (default reference_clips/.cache). "
                         "Use a run-scoped dir when decomposing OUR produced clips.")
    args = ap.parse_args()

    clip = _resolve_clip(args.clip)
    if clip is None:
        _log(f"clip not found: {args.clip!r} (looked in {REF_DIR})")
        return 1
    _log(f"decomposing {clip.name} ...")
    device = None if args.cuda else "cpu"  # default CPU (offline; avoids Windows CUDA hangs)
    timeline = decompose(clip, device=device, window_s=args.window, hop_s=args.hop,
                         deadline_scale=args.deadline_scale, ocr=args.ocr,
                         llm=not args.no_llm, trim_start=args.trim_start, trim_end=args.trim_end,
                         cache_dir=Path(args.cache_dir) if args.cache_dir else None)
    text = json.dumps(timeline, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        _log(f"wrote {args.out}")
    else:
        print(text)
    # Console summary
    cap = timeline.get("captions")
    cap_s = f" captions_wps={cap.get('words_per_s')}" if isinstance(cap, dict) and cap.get("available") else ""
    _log(f"events={len(timeline['audio_events'])} cuts={len(timeline['cuts'])} "
         f"music={len(timeline['music'])} censor={len(timeline['censor'])} "
         f"motion={len(timeline['motion'])} words={timeline['n_words']} "
         f"dur={timeline['duration_s']}s{cap_s}")
    _log(f"style_profile={'yes' if timeline.get('style_profile') else 'none'}")
    bad = {k: v for k, v in timeline.get("_stages", {}).items() if v in ("timeout", "error")}
    if bad:
        _log(f"WARNING: {len(bad)} stage(s) not ok (ran failure-soft, partial result): {bad}")
    if "notes_eval" in timeline and timeline["notes_eval"].get("recall") is not None:
        ne = timeline["notes_eval"]
        _log(f"notes recall={ne['recall']} ({ne['matched']}/{ne['annotated']} annotated events recovered)"
             + (" [DRAFT — correct it for a real score]" if ne.get("is_draft") else ""))

    if args.draft_notes:
        draft = _draft_notes(timeline)
        dst = notes_path(clip, for_write=True)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            try:
                existing = json.loads(dst.read_text(encoding="utf-8"))
            except Exception:
                existing = {}
            if not existing.get("_draft"):
                _log(f"REFUSING to overwrite corrected annotations at {dst.name} "
                     f"(no _draft key). Delete it first if you meant to regenerate.")
                return 0
        dst.write_text(json.dumps(draft, indent=2), encoding="utf-8")
        _log(f"wrote draft annotations -> {dst.name} ({len(draft['events'])} proposed events; "
             f"CORRECT them + delete the _draft key)")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
