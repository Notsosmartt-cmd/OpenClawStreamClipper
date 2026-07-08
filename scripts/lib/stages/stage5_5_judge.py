"""Stage 5.5 — Vision Judge (Plan 1.a).

Promotes the multimodal model from "title writer" to **selector**. Runs between
Stage 5 (frame extraction) and Stage 6 (vision enrichment), after the vision
model is already loaded. Re-ranks the Pass C shortlist with a seeded Swiss
tournament of pairwise "which clip is more engaging sound-off?" comparisons
(see vlm_judge), then:
  * stamps `vision_rank` / `vision_win_count` / `judge_rationale` on each clip,
  * applies a **bounded** `raw_score` reweight (+/- reweight_span) so the
    judge's verdict propagates through Stage 6's raw_score sort — never zeroes a
    clip, never drops one,
  * re-sorts hype_moments.json so the order reflects the verdict.

Failure-soft (mirrors stage4_rubric): disabled / too-few moments / persistent
LM Studio outage / too-few completed comparisons all leave hype_moments.json in
its incoming Pass C order. Config: config/judge.json (CLIP_JUDGE_CONFIG).

Reads:  {CLIP_WORK_DIR}/hype_moments.json, {CLIP_WORK_DIR}/transcript.json,
        {CLIP_WORK_DIR}/frames_{T}_{label}.jpg
Writes: {CLIP_WORK_DIR}/hype_moments.json  (re-ranked + reweighted in place)
"""
from __future__ import annotations

import json
import math
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # scripts/lib on path
import vlm_judge  # noqa: E402

TEMP_DIR = os.environ.get("CLIP_WORK_DIR", "/tmp/clipper")

DEFAULTS: Dict[str, Any] = {
    "enabled": True,
    "shortlist_min": 3,
    "shortlist_max": 12,
    "frames_per_clip": 4,
    "rounds_extra": 1,
    "max_comparisons": 30,
    "reweight_span": 0.25,
    "per_pair_timeout_seconds": 60,
    "max_tokens": 1200,
    "stage_timeout_seconds": 600,
    "fail_streak_limit": 3,
}


def load_config() -> Dict[str, Any]:
    cfg = dict(DEFAULTS)
    repo_cfg = Path(__file__).resolve().parents[3] / "config" / "judge.json"
    for p in (os.environ.get("CLIP_JUDGE_CONFIG"), str(repo_cfg), "/root/.openclaw/judge.json"):
        if not p:
            continue
        try:
            data = json.loads(Path(p).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        if isinstance(data, dict):
            cfg.update({k: v for k, v in data.items() if not k.startswith("_")})
            break
    # Item B (2026-06-06): env overrides for the key speed/quality dials so a
    # run can be tuned without editing judge.json (mirrors JUDGE_WORKERS).
    for _env, _key in (
        ("JUDGE_MAX_COMPARISONS", "max_comparisons"),
        ("JUDGE_FRAMES_PER_CLIP", "frames_per_clip"),
        ("JUDGE_SHORTLIST_MAX", "shortlist_max"),
    ):
        _v = os.environ.get(_env, "").strip()
        if _v:
            try:
                cfg[_key] = int(_v)
            except ValueError:
                pass
    return cfg


def _raw_of(m: Dict[str, Any]) -> float:
    try:
        return float(m.get("raw_score", m.get("score", 0)) or 0)
    except (ValueError, TypeError):
        return 0.0


def _rounds_for(n: int, cfg: Dict[str, Any]) -> int:
    return max(1, int(math.ceil(math.log2(max(2, n)))) + int(cfg.get("rounds_extra", 1)))


def _resolve_judge_workers(cfg: Dict[str, Any]) -> int:
    """Parallel pairwise-judge calls (Fix 2B). ``JUDGE_WORKERS`` env overrides
    judge.json ``workers`` (default 2 — matches Stage 6's conservative cap for
    the shared LM Studio vision model on the split GPU pool; the encoder may
    serialize internally so >2-3 yields little)."""
    raw = os.environ.get("JUDGE_WORKERS", "").strip()
    if raw:
        try:
            v = int(raw)
            if v >= 1:
                return v
        except ValueError:
            pass
    try:
        return max(1, int(cfg.get("workers", 2)))
    except (ValueError, TypeError):
        return 2


def run_judge(
    moments: List[Dict[str, Any]],
    *,
    cfg: Dict[str, Any],
    work_dir: Optional[str] = None,
    transcript: Optional[Sequence[Dict[str, Any]]] = None,
    compare_fn: Optional[Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, Any]]] = None,
    log: Callable[[str], None] = lambda s: None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Re-rank + reweight ``moments`` in place. Returns ``(moments, info)``.

    Pass ``compare_fn`` to bypass the VLM (unit tests); otherwise a real
    pairwise comparator over frames+transcript is built from ``work_dir`` /
    ``transcript``.
    """
    if not cfg.get("enabled", True):
        return moments, {"status": "disabled"}
    n = len(moments)
    smin = int(cfg.get("shortlist_min", 3))
    smax = int(cfg.get("shortlist_max", 12))
    if n < max(2, smin):
        return moments, {"status": "too_few", "n": n}

    order = sorted(range(n), key=lambda i: _raw_of(moments[i]), reverse=True)
    shortlist = [moments[i] for i in order[: min(smax, n)]]

    outage_streak = [0]
    fail_limit = int(cfg.get("fail_streak_limit", 3))
    deadline = time.time() + float(cfg.get("stage_timeout_seconds", 600))

    if compare_fn is None:
        tindex = list(transcript or [])

        def _tw(clip: Dict[str, Any]) -> str:
            cs = clip.get("clip_start")
            ce = clip.get("clip_end")
            if cs is None or ce is None:
                t = float(clip.get("timestamp", 0) or 0)
                cs, ce = t - 8, t + 8
            segs = [s for s in tindex
                    if float(s.get("end", 0)) >= float(cs) and float(s.get("start", 0)) <= float(ce)]
            return " ".join((s.get("text", "") or "").strip() for s in segs)[:480]

        # Fusion option 3 (Activation Wave 0.3): behind CLIP_JUDGE_TIMELINE (default OFF,
        # activated in Run 2 per plan-activation-wave-2026-07), give the judge a
        # time-aligned event stream per clip (words + the Stage-2 crowd_response audio
        # dial) so it can weigh reaction cues, not just frames+words. Full value arrives
        # only with CLAP-live (named events vs one energy dial). Failure-soft: any error
        # -> no timeline, judge falls back to frames+words exactly as before.
        _tl_fn = None
        if os.environ.get("CLIP_JUDGE_TIMELINE", "").strip().lower() in ("1", "true", "yes", "on"):
            try:
                import event_timeline as _et
                _aud: List[Dict[str, Any]] = []
                try:
                    import audio_events as _ae
                    _raw = _ae.load_events(f"{work_dir}/audio_events.json") if work_dir else {}
                    _aud = [{"t": (s + e) / 2.0, "label": "crowd",
                             "score": float(v.get("crowd_response", 0.0))}
                            for (s, e), v in (_raw or {}).items()
                            if float(v.get("crowd_response", 0.0)) >= 0.40]
                except Exception:
                    _aud = []
                _words = [{"word": (s.get("text", "") or "").strip(),
                           "start": float(s.get("start", 0) or 0)}
                          for s in tindex if (s.get("text") or "").strip()]
                _tl_full = _et.build_timeline(words=_words, audio_events=_aud)

                def _tl_fn(clip, _tl=_tl_full, _et=_et):  # noqa: E306
                    cs, ce = clip.get("clip_start"), clip.get("clip_end")
                    if cs is None or ce is None:
                        t = float(clip.get("timestamp", 0) or 0)
                        cs, ce = t - 8, t + 8
                    try:
                        return _et.render_for_prompt(_tl, float(cs), float(ce), max_lines=30)
                    except Exception:
                        return ""
                log(f"[judge] timeline fusion ON ({len(_words)} words, {len(_aud)} audio events)")
            except Exception as _tle:
                log(f"[judge] timeline fusion unavailable ({type(_tle).__name__}); frames+words only")
                _tl_fn = None

        _outage_lock = threading.Lock()

        def _cmp(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
            # Called concurrently when workers>1 (Fix 2B). compare_pair only
            # reads a/b + issues an HTTP request, so the only shared state is
            # the outage circuit-breaker counter — guard it.
            res = vlm_judge.compare_pair(a, b, work_dir=work_dir, transcript_fn=_tw,
                                         timeline_fn=_tl_fn, cfg=cfg)
            with _outage_lock:
                if res.get("outage"):
                    outage_streak[0] += 1
                elif res.get("ok"):
                    outage_streak[0] = 0
            return res

        compare = _cmp
    else:
        compare = compare_fn

    def _should_stop() -> bool:
        return outage_streak[0] >= fail_limit or time.time() > deadline

    rationales: Dict[int, str] = {}
    games_log: List[Dict[str, Any]] = []  # the pairwise bracket, for diagnostics

    def _on(a: Dict[str, Any], b: Dict[str, Any], res: Dict[str, Any]) -> None:
        w = res.get("winner")
        if w == "A" and res.get("reason"):
            rationales[id(a)] = res["reason"]
        elif w == "B" and res.get("reason"):
            rationales[id(b)] = res["reason"]
        games_log.append({
            "a": a.get("timestamp"), "b": b.get("timestamp"),
            "winner": (a.get("timestamp") if w == "A" else b.get("timestamp") if w == "B" else None),
            "confidence": res.get("confidence"),
            "reason": (res.get("reason") or "")[:160],
        })

    _judge_workers = _resolve_judge_workers(cfg)
    print(
        f"[JUDGE] Swiss tournament: {len(shortlist)} clips, "
        f"<= {int(cfg.get('max_comparisons', 30))} comparisons, "
        f"{_judge_workers} worker(s) (JUDGE_WORKERS to change)",
        file=sys.stderr,
    )
    ranked = vlm_judge.swiss_tournament(
        shortlist, compare,
        rounds=_rounds_for(len(shortlist), cfg),
        max_comparisons=int(cfg.get("max_comparisons", 30)),
        on_compare=_on, should_stop=_should_stop,
        workers=_judge_workers,
    )

    total_games = sum(int(it.get("games", 0)) for it in ranked)
    if total_games < 2:
        for it in ranked:
            it.pop("_seed", None)
            it.pop("wins", None)
            it.pop("games", None)
        return moments, {"status": "insufficient", "games": total_games,
                         "outage": outage_streak[0] >= fail_limit, "games_log": games_log}

    N = len(ranked)
    span = float(cfg.get("reweight_span", 0.25))
    for rank, it in enumerate(ranked, start=1):
        it["vision_rank"] = rank
        it["vision_win_count"] = round(float(it.get("wins", 0.0)), 1)
        if id(it) in rationales:
            it["judge_rationale"] = rationales[id(it)]
        factor = 1.0 + span * (1.0 - 2.0 * (rank - 1) / max(1, (N - 1)))
        base_raw = _raw_of(it)
        it["pass_c_raw_score"] = round(base_raw, 4)
        new_raw = base_raw * factor
        it["raw_score"] = round(new_raw, 4)
        it["score"] = round(max(0.0, min(new_raw, 1.0)), 3)
        it.pop("_seed", None)
        it.pop("wins", None)
        it.pop("games", None)

    # Item B (2026-06-06): rank-churn — how much did the judge actually reorder
    # the incoming Pass C shortlist? This is the metric for deciding whether
    # Stage 5.5 earns its (parallelized) cost: if churn is ~0 across runs, the
    # tournament is re-deriving Pass C's order and you can skip it
    # (judge.json enabled=false). `shortlist` is the incoming Pass C order;
    # `ranked` holds the SAME dicts reordered, so id() matches.
    _old_pos = {id(it): i for i, it in enumerate(shortlist)}
    _moved = sum(1 for i, it in enumerate(ranked) if _old_pos.get(id(it)) != i)
    _top_changed = bool(shortlist and ranked and id(shortlist[0]) != id(ranked[0]))
    print(
        f"[JUDGE] rank churn: {_moved}/{N} clips moved; "
        f"#1 {'CHANGED' if _top_changed else 'unchanged'} "
        f"({total_games} comparisons). If churn stays ~0 across runs, Stage 5.5 "
        f"isn't earning its cost — set judge.json enabled=false to skip it.",
        file=sys.stderr,
    )

    moments_sorted = sorted(moments, key=_raw_of, reverse=True)
    return moments_sorted, {"status": "ok", "ranked": N, "games": total_games,
                            "rank_churn": _moved, "top_changed": _top_changed,
                            "games_log": games_log}


def main(argv: Sequence[str]) -> int:
    cfg = load_config()
    moments_path = argv[1] if len(argv) > 1 else f"{TEMP_DIR}/hype_moments.json"
    try:
        with open(moments_path, encoding="utf-8") as f:
            moments = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"[JUDGE] couldn't load {moments_path}: {e}", file=sys.stderr)
        return 0
    if not isinstance(moments, list) or not moments:
        print("[JUDGE] no moments to judge; exiting", file=sys.stderr)
        return 0

    transcript: List[Dict[str, Any]] = []
    try:
        with open(f"{TEMP_DIR}/transcript.json", encoding="utf-8") as f:
            tr = json.load(f)
        transcript = tr.get("segments") if isinstance(tr, dict) else tr
        transcript = transcript or []
    except (OSError, json.JSONDecodeError):
        transcript = []

    t0 = time.time()
    print(f"[JUDGE] Vision Judge starting on {len(moments)} moments "
          f"(shortlist<= {cfg.get('shortlist_max')}, max_comparisons={cfg.get('max_comparisons')})",
          file=sys.stderr)

    new_moments, info = run_judge(moments, cfg=cfg, work_dir=TEMP_DIR,
                                  transcript=transcript, log=lambda s: print(s, file=sys.stderr))

    # Persist the pairwise tournament bracket for post-run judge tuning (captured
    # by the Stage-8 diagnostics dump; read back via `logtool axes`). Written even
    # on a partial/insufficient run so an aborted tournament is still inspectable.
    if info.get("games_log"):
        try:
            with open(f"{TEMP_DIR}/judge_tournament.json", "w", encoding="utf-8") as f:
                json.dump({"status": info.get("status"), "ranked": info.get("ranked"),
                           "games": info.get("games"), "comparisons": info["games_log"]},
                          f, indent=2)
            print(f"[JUDGE] tournament bracket ({len(info['games_log'])} comparisons) -> judge_tournament.json",
                  file=sys.stderr)
        except OSError:
            pass

    if info.get("status") != "ok":
        print(f"[JUDGE] no re-rank applied ({info}); Pass C order preserved", file=sys.stderr)
        return 0

    with open(moments_path, "w", encoding="utf-8") as f:
        json.dump(new_moments, f, indent=2)

    ranked = [m for m in new_moments if m.get("vision_rank")]
    ranked.sort(key=lambda m: m["vision_rank"])
    order_str = " > ".join(f"T={m['timestamp']}(#{m['vision_rank']})" for m in ranked[:8])
    print(f"[JUDGE] re-ranked {info['ranked']} clips in {info['games']} comparisons, "
          f"{time.time()-t0:.1f}s — {order_str}", file=sys.stderr)
    for m in ranked:
        print(f"  [JUDGE] #{m['vision_rank']} T={m['timestamp']} wins={m.get('vision_win_count')} "
              f"raw {m.get('pass_c_raw_score')}->{m.get('raw_score')} — {m.get('judge_rationale','')[:50]}",
              file=sys.stderr)
    return 0


# --- self-test (no network) --------------------------------------------------
def _selftest() -> int:
    """Mock comparator (clip with higher hidden 'q' wins) must drive the known
    best to rank 1, keep comparisons bounded, and reweight within bounds."""
    cfg = load_config()
    cfg["max_comparisons"] = 30
    moments = [
        {"timestamp": 100 + i * 50, "primary_category": "storytime",
         "raw_score": round(0.4 + 0.03 * i, 3), "score": round(min(1.0, 0.4 + 0.03 * i), 3),
         "q": q}
        for i, q in enumerate([3, 9, 1, 7, 5, 8, 2, 6])  # hidden quality, NOT correlated with seed
    ]

    def mock_compare(a, b):
        if a["q"] == b["q"]:
            return {"winner": None, "confidence": 0.5, "reason": "tie", "ok": True}
        win = "A" if a["q"] > b["q"] else "B"
        return {"winner": win, "confidence": 0.8, "reason": f"q {a['q']} vs {b['q']}", "ok": True}

    out, info = run_judge(moments, cfg=cfg, compare_fn=mock_compare)
    ranked = sorted([m for m in out if m.get("vision_rank")], key=lambda m: m["vision_rank"])
    print("status:", info)
    for m in ranked:
        print(f"  #{m['vision_rank']} q={m['q']} wins={m.get('vision_win_count')} "
              f"raw {m.get('pass_c_raw_score')}->{m.get('raw_score')}")
    ok = True
    if info.get("status") != "ok":
        print("FAIL: expected status ok"); ok = False
    if ranked and ranked[0]["q"] != 9:
        print("FAIL: highest-quality clip (q=9) should rank #1"); ok = False
    if ranked and ranked[-1]["q"] != 1:
        print("FAIL: lowest-quality clip (q=1) should rank last"); ok = False
    # reweight bounds: every new raw within +/- span of its base
    span = float(cfg["reweight_span"])
    for m in ranked:
        base = m["pass_c_raw_score"]
        if base > 0 and not (base * (1 - span) - 1e-6 <= m["raw_score"] <= base * (1 + span) + 1e-6):
            print(f"FAIL: reweight out of bounds for T={m['timestamp']}"); ok = False
    if any(m.get("raw_score", 1) <= 0 for m in ranked):
        print("FAIL: a clip was zeroed"); ok = False
    print("SELFTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    raise SystemExit(main(sys.argv))
