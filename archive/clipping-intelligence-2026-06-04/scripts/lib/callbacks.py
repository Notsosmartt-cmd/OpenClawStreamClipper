#!/usr/bin/env python3
"""Long-range callback detector — Tier-2 M3 of the moment-discovery upgrade.

Catches setup-payoff arcs that span minutes to hours — the canonical Lacy
penthouse moment where a claim made early in the stream is contradicted /
fulfilled / referenced much later. Pass B's chunk-local view can't see
these connections; this module surfaces them via semantic search.

Pipeline:

1. After Stage 2 (transcription) is complete, ``embed_segments()`` runs a
   small sentence-transformers model over the transcript. Segments are
   first aggregated into ~30 s windows (single-segment fragments are too
   noisy for cosine similarity).
2. ``build_index()`` builds a per-stream FAISS index over the embeddings.
3. After Pass B, ``detect_callbacks()`` walks the LLM moments. For each
   candidate, it embeds the ±15 s payoff window and FAISS-searches for
   transcript windows with cosine > threshold that occurred ``min_gap``
   seconds earlier. The strongest match becomes the setup candidate.
4. A small Pass-B' LLM call ("is this a callback?") gates each candidate
   to suppress false-positive semantic similarity. Surviving callbacks
   are returned as new moments with ``category="callback"``,
   ``cross_validated=True``, and a 1.5× score boost.

Every dependency is optional. When ``sentence-transformers`` or
``faiss-cpu`` isn't installed the module no-ops cleanly and ``detect_
callbacks()`` returns an empty list — the rest of the pipeline is unaffected.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_WINDOW = 30          # seconds of transcript per embedding window
DEFAULT_STEP = 15            # slide step (50% overlap)
DEFAULT_MIN_GAP = 300        # setup must precede payoff by ≥5 min
DEFAULT_COSINE_THRESHOLD = 0.6
DEFAULT_TOP_K = 20           # how many top Pass-B candidates to evaluate
DEFAULT_MAX_CALLBACKS = 5    # cap callbacks added per VOD


def _try_import_st():
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore
        import numpy as np  # type: ignore
        return SentenceTransformer, np
    except ImportError:
        return None, None


def _try_import_faiss():
    try:
        import faiss  # type: ignore
        return faiss
    except ImportError:
        return None


def aggregate_segments(segments: List[Dict[str, Any]], window: int = DEFAULT_WINDOW, step: int = DEFAULT_STEP) -> List[Dict[str, Any]]:
    """Aggregate raw Whisper segments into overlapping ``window``-second
    embedding windows. Single Whisper segments (2-15 s typical) are too
    noisy to embed individually."""
    if not segments:
        return []
    segments_sorted = sorted(segments, key=lambda s: float(s.get("start", 0)))
    end_time = max(float(s.get("end", 0)) for s in segments_sorted)
    out: List[Dict[str, Any]] = []
    t = float(segments_sorted[0].get("start", 0.0))
    while t < end_time:
        win_end = t + window
        in_win = [
            s for s in segments_sorted
            if float(s.get("end", 0)) > t and float(s.get("start", 0)) < win_end
        ]
        if in_win:
            text = " ".join((s.get("text") or "").strip() for s in in_win).strip()
            if text and len(text.split()) >= 6:
                out.append({
                    "start": round(t, 2),
                    "end": round(min(win_end, end_time), 2),
                    "text": text[:600],   # cap to keep embedding cheap
                })
        t += step
    return out


def embed_segments(
    windows: List[Dict[str, Any]],
    model_name: str = DEFAULT_MODEL,
    cache_dir: Optional[str] = None,
) -> Optional[Tuple[Any, Any]]:
    """Embed each window via sentence-transformers. Returns (embeddings, np)
    or None when the dep is missing / model failed to load."""
    SentenceTransformer, np = _try_import_st()
    if SentenceTransformer is None:
        print("[CALLBACKS] sentence-transformers not installed; skipping callback detection", file=sys.stderr)
        return None
    if not windows:
        return None
    try:
        kwargs = {}
        if cache_dir:
            kwargs["cache_folder"] = cache_dir
        model = SentenceTransformer(model_name, **kwargs)
    except Exception as e:
        print(f"[CALLBACKS] failed to load embedding model {model_name}: {e}", file=sys.stderr)
        return None
    texts = [w["text"] for w in windows]
    try:
        emb = model.encode(
            texts, batch_size=32, show_progress_bar=False,
            convert_to_numpy=True, normalize_embeddings=True,
        )
    except Exception as e:
        print(f"[CALLBACKS] embedding failed: {e}", file=sys.stderr)
        return None
    return emb, np


def build_index(embeddings, np):
    """Wrap embeddings in a FAISS inner-product index (== cosine because
    embeddings are normalized). Falls back to brute-force numpy when FAISS
    isn't available."""
    faiss = _try_import_faiss()
    if faiss is None:
        return ("numpy", embeddings)
    try:
        d = embeddings.shape[1]
        index = faiss.IndexFlatIP(d)
        index.add(embeddings.astype("float32"))
        return ("faiss", index)
    except Exception as e:
        print(f"[CALLBACKS] FAISS init failed ({e}); falling back to numpy", file=sys.stderr)
        return ("numpy", embeddings)


def search_setup(
    backend, query_emb, np, top_k: int = 5,
):
    """Return [(window_idx, cosine_score), ...] sorted by score desc."""
    kind, store = backend
    if kind == "faiss":
        scores, idxs = store.search(query_emb.reshape(1, -1).astype("float32"), top_k)
        return [(int(idxs[0][i]), float(scores[0][i])) for i in range(top_k) if int(idxs[0][i]) >= 0]
    # numpy fallback
    sims = (store @ query_emb.reshape(-1)).astype(float)
    order = np.argsort(-sims)[:top_k]
    return [(int(i), float(sims[i])) for i in order]


_JUDGE_PROMPT = """/no_think
You are reviewing a candidate "callback" moment from a livestream — where something the streamer said earlier in the stream is referenced, contradicted, fulfilled, or made ironic by something said later.

EARLIER (potential setup, at {setup_time_str}):
"{setup_text}"

LATER (potential payoff, at {payoff_time_str}, {gap_min:.1f} minutes later):
"{payoff_text}"

Decide whether this is a real callback worth clipping as a SINGLE clip. A real callback:
- Has a clear logical/narrative connection between the two moments (not just shared topic)
- Has a payoff that is interesting on its own (irony, contradiction, fulfillment, surprise)
- Would make sense as a clip with the setup mentioned briefly in the title/description

Reject if:
- They merely share a keyword but don't actually reference each other
- The "payoff" is just continuing the same conversation without a beat
- The connection is too weak to explain in one sentence

Respond with ONLY a single JSON object:
{{"is_callback": true|false, "kind": "irony|contradiction|fulfillment|theme_return", "clip_start_time": "MM:SS", "clip_end_time": "MM:SS", "why": "one sentence naming the callback explicitly"}}

If is_callback is false, just return {{"is_callback": false}}."""


def _format_mmss(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 60:02d}:{s % 60:02d}"


def _parse_mmss(s: str) -> Optional[int]:
    try:
        parts = (s or "").strip().split(":")
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    except (ValueError, AttributeError):
        return None
    return None


def _judge_callback(
    setup_window: Dict[str, Any],
    payoff_window: Dict[str, Any],
    call_llm_fn: Callable[..., Optional[str]],
) -> Optional[Dict[str, Any]]:
    """Ask the configured LLM to decide if this is a real callback. Returns
    parsed dict on success, None on failure or rejection."""
    setup_t = float(setup_window["start"])
    payoff_t = float(payoff_window["start"])
    gap_min = (payoff_t - setup_t) / 60.0
    prompt = _JUDGE_PROMPT.format(
        setup_time_str=_format_mmss(setup_t),
        setup_text=setup_window["text"][:400],
        payoff_time_str=_format_mmss(payoff_t),
        payoff_text=payoff_window["text"][:400],
        gap_min=gap_min,
    )
    try:
        resp = call_llm_fn(prompt, max_tokens=400, max_retries=0)
    except Exception as e:
        print(f"[CALLBACKS] judge LLM call failed: {e}", file=sys.stderr)
        return None
    if not resp:
        return None
    text = resp.strip()
    # Tolerate code fences
    if "```" in text:
        parts = text.split("```")
        if len(parts) >= 2:
            text = parts[1]
            if text.startswith("json"):
                text = text[4:]
    obj_start = text.find("{")
    obj_end = text.rfind("}") + 1
    if obj_start < 0 or obj_end <= obj_start:
        return None
    try:
        parsed = json.loads(text[obj_start:obj_end])
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict) or not parsed.get("is_callback"):
        return None
    return parsed


def detect_callbacks(
    segments: List[Dict[str, Any]],
    llm_moments: List[Dict[str, Any]],
    call_llm_fn: Callable[..., Optional[str]],
    top_k: int = DEFAULT_TOP_K,
    cosine_threshold: float = DEFAULT_COSINE_THRESHOLD,
    min_gap: int = DEFAULT_MIN_GAP,
    max_callbacks: int = DEFAULT_MAX_CALLBACKS,
    cache_dir: Optional[str] = None,
    model_name: str = DEFAULT_MODEL,
) -> List[Dict[str, Any]]:
    """Build embeddings, scan candidates, judge each, return callback moments.
    Empty list when any dep is missing or no candidates survive."""
    if not segments or not llm_moments:
        return []
    t0 = time.time()
    windows = aggregate_segments(segments)
    if len(windows) < 4:
        print("[CALLBACKS] too few transcript windows for callback search", file=sys.stderr)
        return []
    embed_result = embed_segments(windows, model_name=model_name, cache_dir=cache_dir)
    if embed_result is None:
        return []
    embeddings, np = embed_result
    backend = build_index(embeddings, np)
    print(
        f"[CALLBACKS] indexed {len(windows)} transcript windows "
        f"({backend[0]} backend) in {time.time()-t0:.1f}s",
        file=sys.stderr,
    )

    # Rank candidates by their existing score so we judge the most promising first.
    candidates = sorted(
        [m for m in llm_moments if isinstance(m.get("timestamp"), (int, float))],
        key=lambda m: float(m.get("score", 0.0)),
        reverse=True,
    )[:top_k]

    callbacks_out: List[Dict[str, Any]] = []
    judged = 0
    for moment in candidates:
        if len(callbacks_out) >= max_callbacks:
            break
        payoff_t = float(moment["timestamp"])
        # Aggregate the moment's payoff window from raw segments.
        payoff_segs = [
            s for s in segments
            if float(s.get("end", 0)) > payoff_t - 15 and float(s.get("start", 0)) < payoff_t + 15
        ]
        payoff_text = " ".join((s.get("text") or "").strip() for s in payoff_segs).strip()
        if not payoff_text or len(payoff_text.split()) < 6:
            continue
        # Embed payoff window with the same model.
        payoff_emb_res = embed_segments(
            [{"start": payoff_t, "end": payoff_t + 30, "text": payoff_text[:600]}],
            model_name=model_name, cache_dir=cache_dir,
        )
        if payoff_emb_res is None:
            return callbacks_out
        payoff_emb, _ = payoff_emb_res
        hits = search_setup(backend, payoff_emb[0], np, top_k=8)
        # Filter to setups that occurred ≥min_gap seconds earlier and pass threshold.
        best = None
        for idx, score in hits:
            if idx < 0 or idx >= len(windows):
                continue
            setup_win = windows[idx]
            if float(setup_win["start"]) > payoff_t - min_gap:
                continue
            if score < cosine_threshold:
                continue
            best = (setup_win, score)
            break
        if best is None:
            continue
        setup_win, sim = best
        judged += 1
        verdict = _judge_callback(setup_win, {"start": payoff_t, "text": payoff_text}, call_llm_fn)
        if not verdict:
            continue
        clip_start_s = _parse_mmss(verdict.get("clip_start_time", ""))
        clip_end_s = _parse_mmss(verdict.get("clip_end_time", ""))
        if clip_start_s is None or clip_end_s is None:
            # Default: the payoff window with a short pre-roll.
            clip_start_s = max(0, int(payoff_t) - 10)
            clip_end_s = min(int(payoff_t) + 35, int(payoff_t) + 45)
        if clip_end_s - clip_start_s < 15:
            clip_end_s = clip_start_s + 30
        if clip_end_s - clip_start_s > 150:
            clip_end_s = clip_start_s + 150
        # Score: take the candidate moment's existing score and apply 1.5×
        # boost (capped at 1.0). cross_validated marks it for Pass C.
        base = float(moment.get("score", 0.5))
        boosted = min(base * 1.5, 1.0)
        callback_kind = (verdict.get("kind") or "callback").lower()
        why = (verdict.get("why") or "").strip()[:200]
        callbacks_out.append({
            "timestamp": int(payoff_t),
            "score": round(boosted, 3),
            "preview": why or moment.get("preview", "")[:120],
            "categories": ["callback", moment.get("primary_category", "controversial")],
            "primary_category": "callback",
            "source": "callback",
            "why": why,
            "clip_start": clip_start_s,
            "clip_end": clip_end_s,
            "callback_kind": callback_kind,
            "setup_time": int(setup_win["start"]),
            "setup_text": setup_win["text"][:300],
            "callback_cosine": round(float(sim), 3),
            "cross_validated": True,
        })
        print(
            f"[CALLBACKS] +callback T={int(payoff_t)}s setup_T={int(setup_win['start'])}s "
            f"cos={sim:.2f} kind={callback_kind} why={why[:60]}",
            file=sys.stderr,
        )

    print(
        f"[CALLBACKS] judged {judged} candidates, kept {len(callbacks_out)} callbacks "
        f"in {time.time()-t0:.1f}s total",
        file=sys.stderr,
    )
    return callbacks_out


def _cli() -> None:
    """Standalone CLI for ad-hoc inspection: prints aggregated windows + sample
    nearest-neighbor pairs. Not used by the pipeline."""
    import argparse
    ap = argparse.ArgumentParser(description="Callback detector inspector (Tier-2 M3)")
    ap.add_argument("--transcript", required=True, help="path to transcript.json")
    ap.add_argument("--top", type=int, default=10, help="how many top setup-payoff pairs to print")
    args = ap.parse_args()
    segments = json.loads(Path(args.transcript).read_text(encoding="utf-8"))
    windows = aggregate_segments(segments)
    print(f"aggregated {len(windows)} windows", file=sys.stderr)
    embed_result = embed_segments(windows)
    if embed_result is None:
        sys.exit(1)
    embeddings, np = embed_result
    backend = build_index(embeddings, np)
    pairs = []
    for i, w in enumerate(windows):
        hits = search_setup(backend, embeddings[i], np, top_k=5)
        for j, sim in hits:
            if j == i:
                continue
            if windows[j]["start"] >= w["start"]:
                continue
            if w["start"] - windows[j]["start"] < DEFAULT_MIN_GAP:
                continue
            if sim < DEFAULT_COSINE_THRESHOLD:
                continue
            pairs.append((sim, j, i))
    pairs.sort(reverse=True)
    for sim, j, i in pairs[:args.top]:
        print(f"cos={sim:.3f}  setup@{int(windows[j]['start'])}s -> payoff@{int(windows[i]['start'])}s")
        print(f"  setup:  {windows[j]['text'][:160]}")
        print(f"  payoff: {windows[i]['text'][:160]}")
        print()


if __name__ == "__main__":
    _cli()
