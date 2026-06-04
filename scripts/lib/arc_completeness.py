"""arc_completeness.py — Selection Sub-Plan A (arc completeness).

Structural "is this a complete, self-contained setup->payoff arc?" scorer.
Pure stdlib + the optional `conversation_shape` module. Produces a 0-1
completeness score and a gentle, **category-aware** multiplier that Pass C folds
into a moment's raw_score.

Design contract (matches the pipeline's non-gatekeeping philosophy):
  * Boost-leaning and bounded — the multiplier lives in [floor, ceil] (default
    ~[0.85, 1.12]); it can re-rank but never zero a moment, never drop a clip.
  * Category-aware — arc-dependent categories (storytime/emotional/controversial/
    hot_take/arc) are scored on opener+resolution+coherence; intentionally
    setup-light categories (reactive/hype/funny/dancing) are near-neutral so a
    clean one-liner isn't punished for "lacking a setup".
  * Failure-soft — any missing input (no `conversation_shape`, empty window,
    analyze error) returns a neutral result; it can never raise into Pass C.

The completeness value is also stamped onto the moment so the future Stage 5.5
Vision Judge (Plan 1.a) and diagnostics can consume it.

See AIclippingPipelineVault/wiki/concepts/plan-arc-completeness.md.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

# --- defaults (mirrors config/selection_axes.json::arc_completeness) ----------
DEFAULTS: Dict[str, Any] = {
    "enabled": True,
    "neutral_point": 0.55,
    "arc_weight": 0.20,
    "light_weight": 0.07,
    "multiplier_floor": 0.85,
    "multiplier_ceil": 1.12,
    "arc_categories": ["storytime", "emotional", "controversial", "hot_take", "arc"],
    "light_categories": ["reactive", "hype", "funny", "dancing"],
    "complete_arc_patterns": [
        "storytelling_arc", "setup_external_contradiction", "challenge_and_fold",
        "hot_take_pushback", "interview_revelation", "social_callout", "informational_ramble",
    ],
    "opener_classes": ["story_opener", "claim_stake", "info_ramble_marker"],
    "resolution_classes": ["concession", "agreement", "pushback"],
    "continuation_starts": ["and", "but", "so", "because", "cause", "which",
                            "anyway", "also", "plus", "then", "or", "yeah"],
    "weights_arc": {"baseline": 0.50, "opener": 0.20, "resolution": 0.20,
                    "pattern": 0.15, "monologue": 0.10, "topic_penalty": 0.15,
                    "mid_start_penalty": 0.15},
    "weights_light": {"baseline": 0.65, "opener": 0.05, "resolution": 0.10,
                      "pattern": 0.05, "monologue": 0.00, "topic_penalty": 0.05,
                      "mid_start_penalty": 0.20},
}


def _repo_config_path() -> Path:
    # scripts/lib/arc_completeness.py -> parents[2] == repo root
    return Path(__file__).resolve().parents[2] / "config" / "selection_axes.json"


def load_config() -> Dict[str, Any]:
    """Load the arc_completeness block, merged over the built-in defaults.
    Tries the env override, then the repo-relative config, then a legacy /root
    path. Any read/parse failure silently keeps the defaults."""
    cfg = json.loads(json.dumps(DEFAULTS))  # deep copy
    candidates = [
        os.environ.get("CLIP_SELECTION_AXES_CONFIG"),
        str(_repo_config_path()),
        "/root/.openclaw/selection_axes.json",
    ]
    for p in candidates:
        if not p:
            continue
        try:
            data = json.loads(Path(p).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        blk = data.get("arc_completeness") if isinstance(data, dict) else None
        if isinstance(blk, dict):
            for k, v in blk.items():
                if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                    cfg[k] = {**cfg[k], **v}
                else:
                    cfg[k] = v
            break
    return cfg


def _starts_mid(window: Sequence[Dict[str, Any]], cfg: Dict[str, Any]) -> bool:
    """First spoken word is a continuation/conjunction => starts mid-thought."""
    if not window:
        return False
    first = (window[0].get("text") or "").strip().lower()
    toks = re.findall(r"[a-z']+", first)
    if not toks:
        return False
    return toks[0] in set(cfg.get("continuation_starts", []))


def _to_multiplier(completeness: float, arc_weight: float, cfg: Dict[str, Any]) -> float:
    neutral = float(cfg.get("neutral_point", 0.55))
    floor = float(cfg.get("multiplier_floor", 0.85))
    ceil = float(cfg.get("multiplier_ceil", 1.12))
    mult = 1.0 + arc_weight * (completeness - neutral)
    return round(max(floor, min(ceil, mult)), 4)


def evaluate(
    moment: Dict[str, Any],
    segments: Sequence[Dict[str, Any]],
    *,
    shape_module: Any = None,
    markers: Optional[Dict[str, Any]] = None,
    cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Score one moment's arc completeness.

    Returns ``{"completeness": float|None, "multiplier": float,
    "signals": dict, "reason": str}``. ``multiplier`` is always safe to
    multiply into a score (1.0 on any degraded path).
    """
    cfg = cfg or load_config()
    if not cfg.get("enabled", True):
        return {"completeness": None, "multiplier": 1.0, "signals": {}, "reason": "disabled"}

    if shape_module is None:
        try:
            import conversation_shape as shape_module  # type: ignore
        except Exception:
            shape_module = None

    cat = str(moment.get("primary_category") or "hype").lower()
    pat = str(moment.get("primary_pattern") or "")
    cs = moment.get("clip_start")
    ce = moment.get("clip_end")
    if cs is None or ce is None:
        t = float(moment.get("timestamp", 0) or 0)
        cs, ce = t - 15.0, t + 15.0
    cs, ce = float(cs), float(ce)
    if ce <= cs:
        ce = cs + 1.0

    win = [s for s in segments
           if float(s.get("end", 0)) > cs and float(s.get("start", 0)) < ce]

    is_arc = cat in set(cfg.get("arc_categories", []))
    W = cfg.get("weights_arc" if is_arc else "weights_light", {})
    arc_weight = float(cfg.get("arc_weight" if is_arc else "light_weight",
                               0.20 if is_arc else 0.07))
    signals: Dict[str, Any] = {"group": "arc" if is_arc else "light", "win_segs": len(win)}

    # Degraded paths: no shape engine / markers / window -> baseline (+ cheap
    # mid-start text check, which needs no shape data).
    if shape_module is None or markers is None or not win:
        completeness = float(W.get("baseline", 0.55))
        if win and _starts_mid(win, cfg):
            completeness -= float(W.get("mid_start_penalty", 0.15))
            signals["mid_start"] = True
        completeness = max(0.0, min(1.0, completeness))
        reason = "empty_window" if (shape_module is not None and markers is not None and not win) else "no_shape"
        return {"completeness": round(completeness, 3),
                "multiplier": _to_multiplier(completeness, arc_weight, cfg),
                "signals": signals, "reason": reason}

    try:
        shape = shape_module.analyze_chunk(win, cs, ce, markers=markers)
    except Exception as e:  # noqa: BLE001 — never raise into Pass C
        return {"completeness": None, "multiplier": 1.0,
                "signals": {"error": str(e)[:80]}, "reason": "shape_error"}

    dur = ce - cs
    mk = shape.get("discourse_markers") or []
    opener_classes = set(cfg.get("opener_classes", []))
    resolution_classes = set(cfg.get("resolution_classes", []))
    early_cut = cs + 0.45 * dur          # opener should land in the first ~45%
    late_cut = cs + 0.40 * dur           # resolution should land in the last ~60%

    has_opener = any(m.get("class") in opener_classes
                     and float(m.get("t", cs)) <= early_cut for m in mk)
    has_resolution = any(m.get("class") in resolution_classes
                         and float(m.get("t", cs)) >= late_cut for m in mk)
    if not has_resolution and (shape.get("off_screen_intrusions")):
        has_resolution = True            # external contradiction == payoff beat
    pattern_complete = pat in set(cfg.get("complete_arc_patterns", []))

    cov = 0.0
    for r in (shape.get("monologue_runs") or []):
        ov = min(ce, float(r.get("end", 0))) - max(cs, float(r.get("start", 0)))
        if ov > 0:
            cov = max(cov, ov / dur)
    crossings = sum(1 for b in (shape.get("topic_boundaries") or [])
                    if cs < float(b.get("t", cs)) < ce)
    mid_start = _starts_mid(win, cfg)

    score = float(W.get("baseline", 0.5))
    if has_opener:
        score += float(W.get("opener", 0.0))
    if has_resolution:
        score += float(W.get("resolution", 0.0))
    if pattern_complete:
        score += float(W.get("pattern", 0.0))
    score += float(W.get("monologue", 0.0)) * min(cov, 1.0)
    score -= float(W.get("topic_penalty", 0.0)) * (min(crossings, 2) / 2.0)
    if mid_start:
        score -= float(W.get("mid_start_penalty", 0.0))
    completeness = max(0.0, min(1.0, score))

    signals.update({
        "has_opener": has_opener,
        "has_resolution": has_resolution,
        "pattern_complete": pattern_complete,
        "monologue_cov": round(cov, 2),
        "topic_crossings": crossings,
        "mid_start": mid_start,
    })
    return {"completeness": round(completeness, 3),
            "multiplier": _to_multiplier(completeness, arc_weight, cfg),
            "signals": signals, "reason": "ok"}


# --- self-test ---------------------------------------------------------------
def _selftest() -> int:
    """Synthetic sanity check: a clean story arc should outscore a fragment."""
    import conversation_shape as cs_mod
    markers = cs_mod.load_discourse_markers()
    cfg = load_config()

    complete_story = [
        {"start": 100.0, "end": 104.0, "text": "Okay let me tell you about the wildest night.", "speaker": "S0"},
        {"start": 104.0, "end": 112.0, "text": "So basically we drove three hours to this venue and it was completely empty.", "speaker": "S0"},
        {"start": 112.0, "end": 120.0, "text": "We waited and waited and the security guard kept staring at us the whole time.", "speaker": "S0"},
        {"start": 120.0, "end": 126.0, "text": "And then the guy comes out and goes okay you're right, the show was yesterday.", "speaker": "S0"},
        {"start": 126.0, "end": 130.0, "text": "My bad, I had the wrong date the entire time.", "speaker": "S0"},
    ]
    fragment = [
        {"start": 200.0, "end": 203.0, "text": "and so then he just left right there.", "speaker": "S0"},
        {"start": 203.0, "end": 206.0, "text": "anyway the new patch dropped and the servers are down.", "speaker": "S0"},
    ]
    m_story = {"primary_category": "storytime", "primary_pattern": "storytelling_arc",
               "clip_start": 100, "clip_end": 130, "timestamp": 115}
    m_frag = {"primary_category": "storytime", "primary_pattern": "",
              "clip_start": 200, "clip_end": 206, "timestamp": 203}
    m_oneliner = {"primary_category": "reactive", "primary_pattern": "",
                  "clip_start": 300, "clip_end": 318, "timestamp": 309}
    oneliner = [{"start": 300.0, "end": 318.0, "text": "Wait, did you just see that? That's insane.", "speaker": "S0"}]

    r_story = evaluate(m_story, complete_story, shape_module=cs_mod, markers=markers, cfg=cfg)
    r_frag = evaluate(m_frag, fragment, shape_module=cs_mod, markers=markers, cfg=cfg)
    r_one = evaluate(m_oneliner, oneliner, shape_module=cs_mod, markers=markers, cfg=cfg)
    # Degraded path must be safe.
    r_safe = evaluate(m_story, complete_story, shape_module=None, markers=None, cfg=cfg)

    print("complete story :", r_story)
    print("fragment       :", r_frag)
    print("one-liner      :", r_one)
    print("no-shape (safe):", r_safe)

    ok = True
    if not (r_story["completeness"] > r_frag["completeness"]):
        print("FAIL: complete story should outscore fragment"); ok = False
    if not (r_story["multiplier"] > r_frag["multiplier"]):
        print("FAIL: story multiplier should exceed fragment multiplier"); ok = False
    if not (0.85 <= r_frag["multiplier"] <= 1.12):
        print("FAIL: multiplier out of bounds"); ok = False
    if r_safe["multiplier"] != 1.0 and not (0.85 <= r_safe["multiplier"] <= 1.12):
        print("FAIL: degraded path unsafe"); ok = False
    # one-liner (light category) should be near-neutral, not heavily penalized
    if r_one["multiplier"] < 0.95:
        print("FAIL: one-liner over-penalized"); ok = False
    print("SELFTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    # default: print config
    print(json.dumps(load_config(), indent=2))
