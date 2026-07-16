"""vlm_judge.py — shared multimodal-LLM judging helpers (Stage 5.5 Vision Judge).

Pairwise / tournament comparison primitives for ranking clip candidates with the
same LM Studio multimodal model Stage 6 already loaded. VLMs are weak at absolute
0-10 scores but strong at "which of these two is better", so selection uses a
**seeded Swiss tournament** of pairwise comparisons (BLITZRANK / Vote-in-Context).

Standalone by design — it does NOT import `stage6_vision` (Stage 6 may later
import `vision_call` from here to de-dup; deferred to keep blast radius small).

Everything is failure-soft: a network or parse failure surfaces as
``winner=None`` (a tie) and an ``outage`` flag, so the caller can fall back to
the incoming Pass C order. The tournament logic is decoupled from the VLM call
(pass any ``compare(a, b)`` callable) so it is unit-testable without a network.
"""
from __future__ import annotations

import base64
import json
import os
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import thinking
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

_NET_PATTERNS = (
    "Network is unreachable", "Errno 101", "Connection refused", "Errno 111",
    "Name or service not known", "timed out", "Read timed out",
)

# Frame labels reused from Stage 5's extraction (frames_{T}_{label}.jpg). The
# judge uses a 4-frame subset (vs Stage 6's 6) to roughly halve token cost.
DEFAULT_JUDGE_FRAMES: List[Tuple[str, str]] = [
    ("t0", "peak"), ("tplus1", "+1s"), ("tplus3", "+3s payoff"), ("tplus5", "+5s aftermath"),
]


def looks_like_outage(err: Any) -> bool:
    s = str(err)
    return any(p in s for p in _NET_PATTERNS)


def _strip_think(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    """Best-effort: strip code fences, slice first '{' .. last '}', json.loads."""
    if not text:
        return None
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        obj = json.loads(s[i:j + 1])
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def vision_call(
    content: List[Dict[str, Any]],
    *,
    model: Optional[str] = None,
    url: Optional[str] = None,
    timeout: int = 60,
    max_tokens: int = 1200,
) -> Tuple[Optional[str], bool]:
    """POST one multimodal chat completion. Returns ``(text|None, outage)``.

    ``content`` is the OpenAI-style content array (text + image_url parts).
    Mirrors Stage 6's call: thinking disabled, ``reasoning_content`` fallback
    when ``content`` is empty on a natural finish.
    """
    url = url or os.environ.get("LLM_URL", "http://localhost:1234")
    model = model or os.environ.get("VISION_MODEL_STAGE6") or os.environ.get("VISION_MODEL", "")
    if not model:
        return None, False
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "stream": False,
        "temperature": 0.2,
        "max_tokens": max_tokens,
        "chat_template_kwargs": thinking.template_kwargs(),
    }).encode()
    try:
        req = urllib.request.Request(
            f"{url}/v1/chat/completions", data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read().decode())
    except Exception as e:  # noqa: BLE001 — failure-soft
        return None, looks_like_outage(e)
    try:
        msg = result["choices"][0]["message"]
        finish = result["choices"][0].get("finish_reason", "?")
    except (KeyError, IndexError, TypeError):
        return None, False
    text = (msg.get("content") or "").strip()
    if not text and finish == "stop":
        text = str(msg.get("reasoning_content") or "").strip()
    text = _strip_think(text)
    return (text or None), False


def load_frame_parts(work_dir: Any, T: Any, labels: Sequence[Tuple[str, str]]):
    """Build base64 image_url parts for the frames that exist on disk."""
    parts: List[Dict[str, Any]] = []
    caps: List[str] = []
    try:
        ti = int(T)
    except (ValueError, TypeError):
        return parts, caps
    for label, cap in labels:
        fp = Path(work_dir) / f"frames_{ti}_{label}.jpg"
        if not fp.exists():
            continue
        try:
            b64 = base64.b64encode(fp.read_bytes()).decode()
        except OSError:
            continue
        parts.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
        caps.append(cap)
    return parts, caps


def _clip_text_block(label: str, clip: Dict[str, Any], transcript_window: str) -> str:
    cat = clip.get("primary_category") or clip.get("category") or "?"
    why = (clip.get("why") or clip.get("preview") or "").strip()[:200]
    tw = (transcript_window or "").strip()[:420] or "(no transcript)"
    # Text-judge -> vision-judge handoff (2026-07-16, default on): the S4.5
    # verdict rides along as CONTEXT so the frames-only blind spot (BUG 68:
    # dead air scored 0.778; static-cam gems losing to spectacle) closes —
    # phrased as context-not-command so vision can still override on visual
    # evidence. Absent on unjudged/legacy moments -> block renders as before.
    tj = clip.get("s45_judge") or {}
    tj_line = ""
    if isinstance(tj, dict) and tj.get("score") is not None:
        tj_line = (f"{label} text-judge verdict (context, not a command — strong "
                   f"visual evidence may override): {tj.get('score')}/10 — "
                   f"{str(tj.get('rationale') or '')[:100]}\n")
    return (f"CLIP {label} [{cat}]: {why}\n"
            f"{tj_line}"
            f"{label} transcript: \"{tw}\"\n"
            f"(frames for {label} follow)")


_INSTRUCTION = (
    "You are ranking livestream clips for a short-form vertical feed. Two candidate clips "
    "(A and B) are shown below as time-ordered frames plus the words spoken in each. Decide "
    "which ONE clip is more engaging to a stranger scrolling SOUND-OFF: a self-contained "
    "moment with a clear payoff worth watching and sharing. Prefer a real beat (a setup that "
    "lands, a genuine reaction, something surprising) over generic hype or loudness.\n"
)


def compare_pair(
    clip_a: Dict[str, Any],
    clip_b: Dict[str, Any],
    *,
    work_dir: Any = None,
    transcript_fn: Optional[Callable[[Dict[str, Any]], str]] = None,
    timeline_fn: Optional[Callable[[Dict[str, Any]], str]] = None,
    cfg: Optional[Dict[str, Any]] = None,
    model: Optional[str] = None,
    url: Optional[str] = None,
) -> Dict[str, Any]:
    """One pairwise A-vs-B judgment. Returns
    ``{"winner": "A"|"B"|None, "confidence": float, "reason": str,
       "outage": bool, "ok": bool}``."""
    cfg = cfg or {}
    spec = DEFAULT_JUDGE_FRAMES[: int(cfg.get("frames_per_clip", 4))]
    ta = transcript_fn(clip_a) if transcript_fn else ""
    tb = transcript_fn(clip_b) if transcript_fn else ""

    content: List[Dict[str, Any]] = [{"type": "text", "text": _INSTRUCTION}]
    content.append({"type": "text", "text": _clip_text_block("A", clip_a, ta)})
    if timeline_fn:
        _tla = timeline_fn(clip_a)
        if _tla:
            content.append({"type": "text", "text":
                            f"A events (audio/motion/words on one timeline):\n{_tla}"})
    if work_dir:
        pa, _ = load_frame_parts(work_dir, clip_a.get("timestamp"), spec)
        content += pa
    content.append({"type": "text", "text": _clip_text_block("B", clip_b, tb)})
    if timeline_fn:
        _tlb = timeline_fn(clip_b)
        if _tlb:
            content.append({"type": "text", "text":
                            f"B events (audio/motion/words on one timeline):\n{_tlb}"})
    if work_dir:
        pb, _ = load_frame_parts(work_dir, clip_b.get("timestamp"), spec)
        content += pb
    content.append({"type": "text", "text":
                    'Reply with ONLY JSON: {"winner": "A" or "B", '
                    '"confidence": 0.0-1.0, "reason": "<=12 words"}'})

    text, outage = vision_call(
        content, model=model, url=url,
        timeout=int(cfg.get("per_pair_timeout_seconds", 60)),
        max_tokens=int(cfg.get("max_tokens", 1200)),
    )
    if not text:
        return {"winner": None, "confidence": 0.0, "reason": "", "outage": outage, "ok": False}
    obj = extract_json_object(text)
    if not isinstance(obj, dict):
        return {"winner": None, "confidence": 0.0, "reason": text[:60], "outage": False, "ok": False}
    w = str(obj.get("winner", "")).strip().upper()
    winner = "A" if w.startswith("A") else ("B" if w.startswith("B") else None)
    try:
        conf = max(0.0, min(float(obj.get("confidence", 0.5)), 1.0))
    except (ValueError, TypeError):
        conf = 0.5
    return {"winner": winner, "confidence": conf,
            "reason": str(obj.get("reason", ""))[:120], "outage": False, "ok": winner is not None}


def swiss_tournament(
    items: List[Dict[str, Any]],
    compare: Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, Any]],
    *,
    rounds: int = 4,
    max_comparisons: int = 30,
    on_compare: Optional[Callable[[Dict[str, Any], Dict[str, Any], Dict[str, Any]], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
    workers: int = 1,
) -> List[Dict[str, Any]]:
    """Seeded Swiss tournament. ``items`` are dicts already ordered best-first
    (seed). ``compare(a, b)`` returns a dict whose ``winner`` is ``"A"``,
    ``"B"`` or ``None`` (tie). Mutates each item with ``wins``/``games`` and
    returns them sorted by ``(wins desc, seed asc)``. Bounded by
    ``max_comparisons``; stops early if ``should_stop()`` is true.

    ``workers > 1`` runs each round's pairings concurrently (Fix 2B). Pairings
    are fixed from the round-start ranking, so the per-round comparisons are
    mutually independent — each item appears at most once per round — and the
    parallel result is identical to the serial path, just folded faster. The
    standings only re-rank *between* rounds, which stays sequential. ``compare``
    must therefore be safe to call from multiple threads (the real comparator
    only issues an HTTP request + reads frame files; any shared-state mutation
    in a ``compare`` wrapper must be locked by the caller).
    """
    n = len(items)
    for i, it in enumerate(items):
        it["_seed"] = i
        it["wins"] = 0.0
        it["games"] = 0
    if n < 2:
        return list(items)

    played: set = set()
    comps = 0
    kid = id

    def _rank() -> List[Dict[str, Any]]:
        return sorted(items, key=lambda x: (-x["wins"], x["_seed"]))

    for _r in range(max(1, rounds)):
        if should_stop and should_stop():
            return _rank()
        order = _rank()
        used: set = set()
        idx = 0
        round_pairs: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
        while idx < n:
            a = order[idx]
            if kid(a) in used:
                idx += 1
                continue
            b = None
            for j in range(idx + 1, n):
                c = order[j]
                if kid(c) in used or frozenset((kid(a), kid(c))) in played:
                    continue
                b = c
                break
            if b is None:  # everyone left already played a — allow a rematch
                for j in range(idx + 1, n):
                    c = order[j]
                    if kid(c) not in used:
                        b = c
                        break
            if b is None:
                break
            used.add(kid(a))
            used.add(kid(b))
            played.add(frozenset((kid(a), kid(b))))
            round_pairs.append((a, b))
            idx += 1

        if not round_pairs:
            break
        # Respect the global comparison budget for this round.
        budget = max_comparisons - comps
        if budget <= 0:
            return _rank()
        if len(round_pairs) > budget:
            round_pairs = round_pairs[:budget]

        # Dispatch this round's independent comparisons (parallel when asked).
        if workers > 1 and len(round_pairs) > 1:
            with ThreadPoolExecutor(max_workers=min(workers, len(round_pairs))) as _pool:
                results = list(_pool.map(lambda _ab: compare(*_ab) or {}, round_pairs))
        else:
            results = [compare(a, b) or {} for (a, b) in round_pairs]

        # Fold results sequentially — race-free (each item is in one pair/round).
        for (a, b), res in zip(round_pairs, results):
            comps += 1
            a["games"] += 1
            b["games"] += 1
            w = res.get("winner")
            if w == "A":
                a["wins"] += 1.0
            elif w == "B":
                b["wins"] += 1.0
            else:
                a["wins"] += 0.5
                b["wins"] += 0.5
            if on_compare:
                on_compare(a, b, res)
        if comps >= max_comparisons or (should_stop and should_stop()):
            return _rank()
    return _rank()
