#!/usr/bin/env python3
"""attribute_cards.py — Phase R1 of concepts/plan-reference-deconstruction-2026-07.

Turn a reference clip's forensic timeline (clip_forensics) + its transcript + a
handful of sampled FRAMES into a structured EDITORIAL attribute card — the
"linguistic deconstruction" an editor / agent / diff-tool can act on. ONE
multimodal call per clip on the vision model; the VLM **reads on-screen text
from the frames directly** (EasyOCR garble poisoned caption-voice v1 — we do NOT
trust it for language).

Split of responsibility:
  - NUMERIC fields (cut cadence, SFX/30s, caption density, avg shot) are computed
    in Python from the timeline — deterministic, authoritative.
  - EDITORIAL fields (hook mechanic, arc shape, comedy device, caption voice,
    engagement, essence paragraph, category) come from the LLM reading frames +
    transcript + the numeric facts as grounding.

Output: reference_clips/.cache/<stem>.card.json (schema v1). Failure-soft per
clip; a down LM Studio / missing timeline just skips that clip.

Usage:
  python scripts/research/attribute_cards.py --clip <name|path>
  python scripts/research/attribute_cards.py --all [--missing] [--limit N]
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve()
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE.parent))          # scripts/research (clip_forensics)
sys.path.insert(0, str(REPO / "scripts" / "lib"))  # lmstudio.loads_lenient

import clip_forensics as cf  # noqa: E402  — reuse _resolve_clip / _llm_config / REF_DIR / _ffprobe

CACHE = cf.REF_DIR / ".cache"
# v2 (2026-07-11): engagement.chat_overlay split into source_chat_visible vs
# added_chat_overlay — v1 conflated the stream's own chat panel with an
# editor-composited overlay (owner catch; the 89%-vs-31% diff line was an artifact).
CARD_SCHEMA_VERSION = 2


def _log(msg: str) -> None:
    print(f"[attribute_cards] {msg}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Deterministic numeric facts from the timeline
# ---------------------------------------------------------------------------
def _load_timeline(stem: str, cache_dir: Path = CACHE) -> dict | None:
    p = cache_dir / f"{stem}.timeline.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _load_words(stem: str, cache_dir: Path = CACHE) -> list[dict]:
    p = cache_dir / f"{stem}.words.json"
    if not p.exists():
        return []
    try:
        w = json.loads(p.read_text(encoding="utf-8"))
        return w if isinstance(w, list) else []
    except Exception:
        return []


def _facts(timeline: dict, words: list[dict]) -> dict:
    """Everything we can measure deterministically — the numbers the LLM must NOT
    re-estimate (it gets them as grounding and fills only editorial fields)."""
    dur = float(timeline.get("duration_s") or 0.0) or 0.0
    cuts = timeline.get("cuts") or []
    events = timeline.get("audio_events") or []
    motion = timeline.get("motion") or []
    music = timeline.get("music") or []
    censor = timeline.get("censor") or []
    caps = timeline.get("captions") if isinstance(timeline.get("captions"), dict) else None
    n_words = int(timeline.get("n_words") or len(words) or 0)
    per30 = (lambda n: round(n / dur * 30.0, 2)) if dur else (lambda n: None)
    labels = Counter(str(e.get("label")) for e in events)
    return {
        "duration_s": round(dur, 2),
        "n_cuts": len(cuts),
        "cuts_per_30s": per30(len(cuts)),
        "avg_shot_s": round(dur / (len(cuts) + 1), 2) if dur else None,
        "n_audio_events": len(events),
        "audio_events_per_30s": per30(len(events)),
        "audio_event_labels_top": labels.most_common(6),
        "n_motion_spikes": len(motion),
        "music_added_by_editor": any(m.get("added") for m in music),
        "n_censor": len(censor),
        "n_words": n_words,
        "speech_words_per_s": round(n_words / dur, 2) if dur else None,
        "caption_words_per_s": (caps or {}).get("words_per_s"),
        "ocr_available": bool((caps or {}).get("available")),
    }


# ---------------------------------------------------------------------------
# Frame sampling (time-ordered across the analysis window)
# ---------------------------------------------------------------------------
def _window(timeline: dict, clip: Path) -> tuple[float, float]:
    aw = timeline.get("analysis_window")
    if isinstance(aw, dict) and aw.get("end"):
        return float(aw.get("start") or 0.0), float(aw["end"])
    dur = float(timeline.get("duration_s") or 0.0)
    if not dur:
        dur, _ = cf._ffprobe(clip)
    return 0.0, float(dur or 0.0)


def _sample_times(win_start: float, win_end: float, n: int) -> list[float]:
    span = max(0.0, win_end - win_start)
    if span < 0.5 or n <= 1:
        return [max(0.0, win_start + 0.3)]
    lo, hi = win_start + 0.3, max(win_start + 0.3, win_end - 0.3)
    return [round(lo + (hi - lo) * i / (n - 1), 2) for i in range(n)]


def _extract_frames(clip: Path, times: list[float], workdir: Path) -> list[tuple[float, str]]:
    """Extract one JPEG per timestamp (scaled to 512px wide), return
    [(t, base64), ...] in time order. Frames that fail to extract are skipped."""
    out: list[tuple[float, str]] = []
    for i, t in enumerate(times):
        jpg = workdir / f"f{i:02d}.jpg"
        try:
            r = subprocess.run(
                ["ffmpeg", "-nostdin", "-y", "-ss", f"{t:.2f}", "-i", str(clip),
                 "-frames:v", "1", "-vf", "scale=512:-1", "-q:v", "4", str(jpg)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
            if r.returncode == 0 and jpg.exists() and jpg.stat().st_size > 0:
                out.append((t, base64.b64encode(jpg.read_bytes()).decode()))
        except Exception:
            continue
    return out


# ---------------------------------------------------------------------------
# The multimodal editorial pass
# ---------------------------------------------------------------------------
_CARD_PROMPT = """/no_think
You are a short-form (TikTok/Reels/Shorts) video-editing analyst. You are given TIME-ORDERED frames sampled across ONE competitor clip that performs well, plus its transcript and an automated signal decomposition. Produce a structured EDITORIAL card another editor could act on.

Read the frames in order (they move forward in time). READ any on-screen text/captions/overlays directly from the frames — do NOT rely on the noisy OCR numbers. Base every judgement on the frames + transcript; if a signal is absent, say so rather than inventing it.

Transcript (what is said):
\"\"\"{transcript}\"\"\"

Automated signals (already measured — do NOT recompute these numbers, use them as grounding):
{facts}

Respond with ONLY a JSON object (no prose, no markdown fences):
{{
  "category": "street_interview|news_compilation|irl_moment|reaction|rap_freestyle|gaming|story|skill|controversy|other",
  "hook": {{"mechanic": "how the first ~2s grabs attention, 1 sentence", "first_2s": "what literally happens in frame 1-2", "text_hook_style": "the on-screen text hook if any (quote it), else 'none'"}},
  "arc": {{"shape": "setup_payoff|escalation|instant|list|story", "setup_s": <seconds or null>, "payoff_s": <seconds the payoff/punchline lands, or null>}},
  "comedy": {{"device": "what makes it funny/engaging, 1 phrase", "verbal_vs_visual": "verbal|visual|both"}},
  "edit_grammar": {{"cut_alignment": "on-beat|on-punchline|loose|none", "zooms": <int>, "freezes": <int>}},
  "sfx_grammar": {{"kinds": ["boom","whoosh","..."], "offset_from_payoff_ms": <int or null>, "loudness_vs_speech": "over|under|ducked|none"}},
  "captions": {{"casing": "all-lowercase|Title Case|SCREAMING CAPS|sentence case|mixed|none", "voice": "the caption WRITING voice in 1 phrase", "on_screen_text_samples": ["verbatim lines you can read in the frames"]}},
  "engagement": {{"source_chat_visible": true/false (the STREAM's own chat panel, part of the source footage), "added_chat_overlay": true/false (a chat box the EDITOR composited in — different style/position than the stream layout), "emoji": true/false, "freeze_bait": true/false}},
  "essence_commentary": "one plain-language paragraph: what an editor should copy from this clip",
  "confidence": <0.0-1.0>
}}"""


def _call_vlm(prompt: str, frames_b64: list[str], model: str, url: str,
              timeout: float = 180.0) -> dict | None:
    image_parts = [{"type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b}"}} for b in frames_b64]
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}, *image_parts]}],
        "stream": False,
        "temperature": 0.3,
        "max_tokens": 1600,
        "chat_template_kwargs": {"enable_thinking": False},
    }).encode()
    try:
        req = urllib.request.Request(f"{url}/v1/chat/completions", data=payload,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.loads(r.read().decode())
    except Exception as e:
        _log(f"VLM call failed ({type(e).__name__}: {e})")
        return None
    try:
        msg = resp["choices"][0]["message"]
        content = msg.get("content") or msg.get("reasoning_content") or ""
    except Exception:
        return None
    if not content:
        return None
    try:
        import lmstudio
        obj = lmstudio.loads_lenient(content)
        if obj is not None:
            return obj
    except Exception:
        pass
    s, e = content.find("{"), content.rfind("}")
    if s < 0 or e <= s:
        return None
    try:
        return json.loads(content[s:e + 1])
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# Build one card
# ---------------------------------------------------------------------------
def build_card(clip: Path, *, n_frames: int = 8, model: str | None = None,
               url: str | None = None, cache_dir: Path = CACHE) -> dict | None:
    """cache_dir: where the timeline/words live AND where the card is written.
    Default = the reference-corpus cache; R2 passes a run-scoped dir for OUR clips."""
    stem = clip.stem
    timeline = _load_timeline(stem, cache_dir)
    if timeline is None:
        _log(f"{stem}: no timeline.json — run clip_forensics first (R0). Skipping.")
        return None
    words = _load_words(stem, cache_dir)
    facts = _facts(timeline, words)
    transcript = " ".join(str(w.get("word", "")) for w in words).strip()[:3000] \
        or "(transcript unavailable)"

    win_start, win_end = _window(timeline, clip)
    times = _sample_times(win_start, win_end, n_frames)
    _m, _u = cf._llm_config()
    model, url = model or _m, url or _u

    with tempfile.TemporaryDirectory(prefix="attrcard_") as td:
        frames = _extract_frames(clip, times, Path(td))
        if not frames:
            _log(f"{stem}: no frames extracted — skipping")
            return None
        prompt = _CARD_PROMPT.format(transcript=transcript,
                                     facts=json.dumps(facts, default=str))
        editorial = _call_vlm(prompt, [b for _, b in frames], model, url)

    if editorial is None:
        _log(f"{stem}: VLM returned nothing parseable — skipping")
        return None

    # Merge: LLM editorial + Python-authoritative numerics + provenance.
    editorial.setdefault("edit_grammar", {})
    editorial["edit_grammar"]["cuts_per_30s"] = facts["cuts_per_30s"]
    editorial["edit_grammar"]["avg_shot_s"] = facts["avg_shot_s"]
    editorial.setdefault("sfx_grammar", {})
    editorial["sfx_grammar"]["count_per_30s"] = facts["audio_events_per_30s"]
    editorial.setdefault("captions", {})
    editorial["captions"]["density_wps"] = facts["caption_words_per_s"] or facts["speech_words_per_s"]
    card = {
        "clip": clip.name,
        "_schema": CARD_SCHEMA_VERSION,
        "_generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "_model": model,
        "_frames_sampled": [t for t, _ in frames],
        "_facts": facts,
        **editorial,
    }
    out = cache_dir / f"{stem}.card.json"
    out.write_text(json.dumps(card, indent=2, ensure_ascii=False), encoding="utf-8")
    _log(f"{stem}: card written (category={card.get('category')} "
         f"conf={card.get('confidence')} frames={len(frames)})")
    return card


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _all_stems() -> list[str]:
    vids = []
    for f in sorted(cf.REF_DIR.iterdir()):
        if f.is_file() and f.suffix.lower() in (".mp4", ".mkv", ".webm", ".mov"):
            vids.append(f.stem)
    return vids


def _cli() -> int:
    ap = argparse.ArgumentParser(description="Build editorial attribute cards from reference clips (R1)")
    ap.add_argument("--clip", help="one clip (name under reference_clips/ or a path)")
    ap.add_argument("--all", action="store_true", help="every reference clip that has a timeline")
    ap.add_argument("--missing", action="store_true", help="with --all: only clips lacking a card.json")
    ap.add_argument("--limit", type=int, default=0, help="cap the number built (0 = no cap)")
    ap.add_argument("--frames", type=int, default=8)
    ap.add_argument("--model", default=None)
    ap.add_argument("--url", default=None)
    args = ap.parse_args()

    if args.clip:
        clip = cf._resolve_clip(args.clip)
        if not clip:  # allow a bare stem (no extension)
            for ext in (".mp4", ".MP4", ".mkv", ".webm", ".mov"):
                clip = cf._resolve_clip(args.clip + ext)
                if clip:
                    break
        if not clip:
            _log(f"clip not found: {args.clip!r}")
            return 1
        return 0 if build_card(clip, n_frames=args.frames, model=args.model, url=args.url) else 1

    if not args.all:
        _log("nothing to do — pass --clip <name> or --all")
        return 1

    built = skipped = 0
    for stem in _all_stems():
        if not (CACHE / f"{stem}.timeline.json").exists():
            continue
        if args.missing and (CACHE / f"{stem}.card.json").exists():
            continue
        clip = cf._resolve_clip(stem) or cf._resolve_clip(stem + ".mp4")
        if not clip:
            continue
        if build_card(clip, n_frames=args.frames, model=args.model, url=args.url):
            built += 1
        else:
            skipped += 1
        if args.limit and built >= args.limit:
            break
    _log(f"done: {built} card(s) built, {skipped} skipped")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
