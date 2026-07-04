#!/usr/bin/env python3
"""anomaly_propose.py — reaction-anchored anomaly lane (upgrade plan Phase 1b).

Proposes clip candidates the transcript-only detector misses: windows where the
AUDIENCE/vocal reaction is strong but the WORDS don't explain it — cross-modal
incongruity + externally-referenced humor (the bus clip / George-Bush class;
concepts/case-incongruity-comedy, concepts/reference-humor-2026-07). Emits
`src=ANOMALY` candidates that enter Pass C boost-only (never gate/evict).

Research-parameterized (concepts/master-research-2026-07 RQ2 — FunnyNet/SMILE):
  * 8 s windows / 2 s stride (FunnyNet ablation optimum).
  * reaction = CLAP laughter/cheer/meme-SFX scores + motion energy.
  * "unexplained" = 1 − keyword-explained score (injected; Pass A in the live path).
  * precision controls: min-reaction floor + top-K cap + a MANDATORY few-shot
    LLM verifier (SMILE: few-shot is load-bearing — 71.1 vs 14.5 F1 zero-shot).

Pure logic + optional LM Studio verify. Callers inject the timeline (from
event_timeline.build_timeline) and a keyword_fn, so this unit-tests with no model.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable

# CLAP/PANNs labels that signal an audience/vocal REACTION (not content).
REACTION_LABELS = ("laughter", "cheering", "applause", "crowd", "bruh",
                   "boom", "airhorn", "boing", "scratch", "whoosh", "gasp")
MOTION_WEIGHT = 0.12       # motion rel-energy contribution per unit
MIN_GAP_S = 45.0           # pipeline's inter-clip spacing rule


def _reaction_score(win_events: list[dict]) -> tuple[float, list[str]]:
    """Reaction strength of a window + the cue labels that drove it."""
    score = 0.0
    cues: list[str] = []
    for e in win_events:
        if e["kind"] == "AUDIO" and any(r in e.get("label", "").lower() for r in REACTION_LABELS):
            score += float(e.get("score", 0.0))
            cues.append(f'{e["label"]}({e.get("score",0):.2f})')
        elif e["kind"] == "MOTION":
            score += MOTION_WEIGHT * float(e.get("rel", 0.0))
            if float(e.get("rel", 0)) >= 3.0:
                cues.append(f'motion {e.get("rel",0):.1f}x')
    return score, cues


def score_windows(timeline: list[dict], keyword_fn: Callable[[float, float], float],
                  *, window_s: float = 8.0, stride_s: float = 2.0) -> list[dict]:
    """Slide an 8 s window; score reaction vs keyword-explained. keyword_fn(t0,t1)
    returns 0..1 (how well the transcript keywords already explain the window)."""
    if not timeline:
        return []
    t_end = max(e["t"] for e in timeline)
    out: list[dict] = []
    t0 = 0.0
    while t0 <= t_end:
        t1 = t0 + window_s
        we = [e for e in timeline if t0 <= e["t"] <= t1]
        reaction, cues = _reaction_score(we)
        if reaction > 0:
            explained = max(0.0, min(1.0, float(keyword_fn(t0, t1))))
            anomaly = reaction * (1.0 - explained)
            out.append({"t0": round(t0, 2), "t1": round(t1, 2),
                        "t": round(t0 + window_s / 2, 2),
                        "reaction": round(reaction, 3), "explained": round(explained, 3),
                        "anomaly": round(anomaly, 3), "cues": cues})
        t0 += stride_s
    return out


def _dedup(scored: list[dict], min_gap_s: float) -> list[dict]:
    """Greedy non-max suppression: highest anomaly first, drop anything within
    min_gap_s of an already-kept center."""
    kept: list[dict] = []
    for c in sorted(scored, key=lambda x: x["anomaly"], reverse=True):
        if all(abs(c["t"] - k["t"]) >= min_gap_s for k in kept):
            kept.append(c)
    return sorted(kept, key=lambda x: x["t"])


def propose(timeline: list[dict], keyword_fn: Callable[[float, float], float], *,
            top_k: int = 6, min_reaction: float = 0.35, min_anomaly: float = 0.20,
            window_s: float = 8.0, stride_s: float = 2.0,
            verify_fn: Callable[[dict, str], dict | None] | None = None,
            render_fn: Callable[[float, float], str] | None = None) -> list[dict]:
    """Full lane: score -> floor -> NMS -> top-K -> (optional) verify -> moments.
    Returns [{timestamp, clip_start, category, why, src, score, cues, verified}]."""
    scored = [c for c in score_windows(timeline, keyword_fn, window_s=window_s, stride_s=stride_s)
              if c["reaction"] >= min_reaction and c["anomaly"] >= min_anomaly]
    deduped = _dedup(scored, MIN_GAP_S)
    candidates = sorted(deduped, key=lambda x: x["anomaly"], reverse=True)[:top_k]
    moments: list[dict] = []
    for c in sorted(candidates, key=lambda x: x["t"]):
        verdict = None
        if verify_fn is not None:
            render = render_fn(c["t0"], c["t1"]) if render_fn else ""
            verdict = verify_fn(c, render)
            if verdict is not None and not verdict.get("keep", False):
                continue  # verifier killed it
        moments.append({
            "timestamp": c["t"], "clip_start": c["t0"], "clip_end": c["t1"],
            "category": (verdict or {}).get("category", "anomaly"),
            "why": (verdict or {}).get("why", f"strong reaction ({', '.join(c['cues'])}) with weak transcript signal"),
            "src": "ANOMALY", "score": c["anomaly"], "cues": c["cues"],
            "verified": bool(verdict) if verify_fn else None,
        })
    return moments


# --- LLM verifier (few-shot; SMILE: few-shot is load-bearing) -----------------
_VERIFY_SYSTEM = """You judge whether a moment from a livestream is a clip-worthy highlight whose humor/impact is NOT explained by the spoken words alone — it lives in the AUDIENCE REACTION, physical action, or an outside reference (a meme, a running bit).

You are given a fused timeline window: TEXT (words spoken), AUDIO (detected sounds like laughter/booms), MOTION (visual motion spikes), CHAT (viewer messages). Decide if this is a real highlight.

Examples:
WINDOW:
[t=6.0] TEXT "ever heard of george?"
[t=7.2] MOTION 6.3x
[t=7.5] AUDIO laughter(0.41)
VERDICT: {"keep": true, "category": "funny", "why": "verbal setup + physical action + audience laughter = a bit the words alone don't convey (George Bush push reference)"}

WINDOW:
[t=40.0] TEXT "so anyway i went to the store and bought some milk"
[t=41.0] AUDIO laughter(0.36)
VERDICT: {"keep": false, "category": "none", "why": "mundane speech; the single weak laugh is likely ambient, no real payoff"}

WINDOW:
[t=12.0] AUDIO cheering(0.52)
[t=12.3] MOTION 7.1x
[t=13.0] TEXT "OH MY GOD did you SEE that"
VERDICT: {"keep": true, "category": "hype", "why": "crowd cheer + big motion + shocked reaction = a hype moment carried by reaction, not content"}

Reply with ONLY a JSON object: {"keep": bool, "category": str, "why": str}."""


def verify_via_lmstudio(candidate: dict, render: str, *, model: str | None = None,
                        url: str | None = None, timeout: float = 45.0) -> dict | None:
    """Few-shot verify one candidate via LM Studio. None on any failure (fail-open
    is the caller's choice; propose() treats None as 'no verdict' = keep)."""
    try:
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import lmstudio
    except Exception:
        return None
    if not model or not url:
        try:
            cfg = json.loads((Path(__file__).resolve().parents[1] / "config" / "models.json").read_text())
            model = model or cfg.get("text_model")
            url = url or cfg.get("llm_url")
        except Exception:
            pass
    model = model or "qwen/qwen3.6-35b-a3b"
    url = (url or "http://localhost:1234").replace("host.docker.internal", "localhost").rstrip("/")
    prompt = f"{_VERIFY_SYSTEM}\n\nWINDOW:\n{render}\nVERDICT:"
    reply = lmstudio.chat(prompt, model=model, url=url, timeout=timeout, max_tokens=200)
    if not reply:
        return None
    try:
        s, e = reply.find("{"), reply.rfind("}")
        return json.loads(reply[s:e + 1]) if 0 <= s < e else None
    except Exception:
        return None


if __name__ == "__main__":  # smoke
    import event_timeline as ET
    tl = ET.build_timeline(
        words=[{"word": "ever heard of george", "start": 6.0, "end": 7.0}],
        audio_events=[{"t": 7.5, "label": "laughter", "score": 0.41}],
        motion_events=[{"t": 7.2, "energy": 52.0, "rel": 6.3}])
    print(propose(tl, keyword_fn=lambda a, b: 0.1))
