"""baseline_contrast.py — Selection Sub-Plan C (baseline contrast).

The most novel selection axis: compute the streamer's *own normal* once per VOD,
then **boost moments that break it**. Relative-to-self anomaly detection, not an
absolute viral template — inherently anti-sameness, and the direct counter to the
energy-bias the user dislikes (a *quiet* beat can win on a hype streamer; a sudden
fast burst can win on a calm one).

Pure stdlib. Two entry points:
  * ``compute_baseline(segments, ...)`` — ONE-TIME per VOD: speaking-rate mean/std
    over rolling windows, the modal segment-type, and the flattened topic-boundary
    times. Returns a baseline dict (with ``ok`` = enough data to trust it).
  * ``evaluate(moment, segments, *, baseline, ...)`` — per moment: a two-sided rate
    z-deviation + a start-aligned topic shift + a genre (segment-type) shift, folded
    into a boost-only multiplier.

Design contract (matches the non-gatekeeping philosophy + the compounding guardrail):
  * Boost-ONLY and bounded — deviation is rewarded; the *absence* of deviation is
    neutral (1.0), never penalized. Multiplier in [1.0, ceil] (default 1.18 — C is
    given the MOST authority of the axes, per the overhaul pre-build eval, because
    it is the corrective for energy bias).
  * Two-sided rate — both unusually fast AND unusually slow are "breaks".
  * Orthogonal to the Tier-2 M1 speaker boost — it measures rate/topic/genre, not
    speaker count, so it never double-counts the existing ×1.15.
  * Cold-start guarded — short VODs / near-uniform pace -> the rate term is dropped
    rather than amplifying noise.
  * Topic signal is start-aligned (a pivot INTO a new topic), NOT a mid-clip
    crossing — so it does not fight Plan A, which penalizes mid-clip crossings.
  * Failure-soft — any missing input returns a neutral 1.0 multiplier.

See AIclippingPipelineVault/wiki/concepts/plan-baseline-contrast.md.
"""
from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

# --- defaults (mirrors config/selection_axes.json::baseline_contrast) ---------
DEFAULTS: Dict[str, Any] = {
    "enabled": True,
    "gain": 0.22,                  # multiplier = 1 + gain * contrast_score
    "multiplier_ceil": 1.18,       # the MOST authority of the axes (eval finding #2)
    "z_norm": 2.5,                 # rate |z| that maps to a full rate term
    "min_windows": 6,              # cold-start guard — need enough windows for a baseline
    "min_rate_std": 0.15,          # below this, pace is too uniform to call deviations
    "window_s": 30.0,
    "step_s": 10.0,
    "topic_near_s": 8.0,           # a topic boundary within this of the clip START = a pivot
    "weights": {"rate": 0.45, "topic": 0.35, "genre": 0.20},
}


def _repo_config_path() -> Path:
    # scripts/lib/baseline_contrast.py -> parents[2] == repo root
    return Path(__file__).resolve().parents[2] / "config" / "selection_axes.json"


def load_config() -> Dict[str, Any]:
    """Load the ``baseline_contrast`` block merged over the built-in defaults.
    Env override -> repo config -> legacy /root path; any failure keeps defaults."""
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
        blk = data.get("baseline_contrast") if isinstance(data, dict) else None
        if isinstance(blk, dict):
            for k, v in blk.items():
                if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                    cfg[k] = {**cfg[k], **v}
                else:
                    cfg[k] = v
            break
    return cfg


def _wps_in(segments: Sequence[Dict[str, Any]], a: float, b: float) -> float:
    """Words-per-second spoken in [a, b], overlap-weighted across segments."""
    if b <= a:
        return 0.0
    words = 0.0
    for s in segments:
        ss, se = float(s.get("start", 0) or 0), float(s.get("end", 0) or 0)
        if se <= a or ss >= b:
            continue
        ov = min(b, se) - max(a, ss)
        seg_dur = max(1e-6, se - ss)
        nwords = len((s.get("text") or "").split())
        words += nwords * (ov / seg_dur)
    return words / (b - a)


def _mean_std(xs: List[float]) -> Any:
    n = len(xs)
    if n == 0:
        return 0.0, 0.0
    mean = sum(xs) / n
    if n < 2:
        return mean, 0.0
    var = sum((x - mean) ** 2 for x in xs) / (n - 1)
    return mean, var ** 0.5


def compute_baseline(
    segments: Sequence[Dict[str, Any]],
    *,
    segment_at: Optional[Callable[[float], Optional[str]]] = None,
    topic_boundaries: Optional[Sequence[float]] = None,
    cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Compute the streamer's per-VOD 'normal' ONCE. Returns
    ``{rate_mean, rate_std, n_windows, modal_segment, topic_boundaries, ok}``.
    ``ok`` is False when there isn't enough data to trust the rate baseline."""
    cfg = cfg or load_config()
    out: Dict[str, Any] = {
        "rate_mean": 0.0, "rate_std": 0.0, "n_windows": 0,
        "modal_segment": None,
        "topic_boundaries": sorted(float(t) for t in (topic_boundaries or [])),
        "ok": False,
    }
    if not segments:
        return out

    win = float(cfg.get("window_s", 30.0))
    step = float(cfg.get("step_s", 10.0)) or 10.0
    t0 = min(float(s.get("start", 0) or 0) for s in segments)
    t1 = max(float(s.get("end", 0) or 0) for s in segments)

    rates: List[float] = []
    types: List[str] = []
    w = t0
    while w < t1:
        rates.append(_wps_in(segments, w, w + win))
        if segment_at is not None:
            try:
                st = segment_at(w + win / 2.0)
                if st:
                    types.append(str(st))
            except Exception:
                pass
        w += step

    # rate baseline over windows that actually contain speech (silence windows
    # would drag the mean toward 0 and inflate every deviation).
    speaking = [r for r in rates if r > 0.05]
    mean, std = _mean_std(speaking)
    out["rate_mean"] = round(mean, 3)
    out["rate_std"] = round(std, 3)
    out["n_windows"] = len(speaking)
    out["ok"] = len(speaking) >= int(cfg.get("min_windows", 6)) and std >= float(cfg.get("min_rate_std", 0.15))
    if types:
        out["modal_segment"] = Counter(types).most_common(1)[0][0]
    return out


def _clip_bounds(moment: Dict[str, Any]) -> Any:
    cs, ce = moment.get("clip_start"), moment.get("clip_end")
    if cs is None or ce is None:
        t = float(moment.get("timestamp", 0) or 0)
        cs, ce = t - 15.0, t + 15.0
    cs, ce = float(cs), float(ce)
    if ce <= cs:
        ce = cs + 1.0
    return cs, ce


def evaluate(
    moment: Dict[str, Any],
    segments: Sequence[Dict[str, Any]],
    *,
    baseline: Optional[Dict[str, Any]] = None,
    segment_at: Optional[Callable[[float], Optional[str]]] = None,
    cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Score how much one moment breaks the streamer's own baseline.

    Returns ``{"contrast_score": float|None, "multiplier": float, "signals": dict,
    "reason": str}``; ``multiplier`` is always >= 1.0 (boost-only) and safe.
    """
    cfg = cfg or load_config()
    if not cfg.get("enabled", True):
        return {"contrast_score": None, "multiplier": 1.0, "signals": {}, "reason": "disabled"}
    if not baseline:
        return {"contrast_score": None, "multiplier": 1.0, "signals": {}, "reason": "no_baseline"}

    cs, ce = _clip_bounds(moment)
    w = cfg.get("weights", {}) or {}
    signals: Dict[str, Any] = {}

    # 1) two-sided speaking-rate deviation (cold-start guarded)
    rate_term = 0.0
    if baseline.get("ok"):
        std = float(baseline.get("rate_std", 0.0)) or 0.0
        if std > 0:
            local = _wps_in(segments, cs, ce)
            z = (local - float(baseline.get("rate_mean", 0.0))) / std
            znorm = float(cfg.get("z_norm", 2.5)) or 2.5
            rate_term = max(0.0, min(abs(z) / znorm, 1.0))
            signals["rate_z"] = round(z, 2)
            signals["local_wps"] = round(local, 2)

    # 2) start-aligned topic pivot (NOT a mid-clip crossing — orthogonal to Plan A)
    near = float(cfg.get("topic_near_s", 8.0))
    tb = baseline.get("topic_boundaries") or []
    topic_hit = any(abs(float(t) - cs) <= near for t in tb)
    topic_term = 1.0 if topic_hit else 0.0
    if topic_hit:
        signals["topic_pivot"] = True

    # 3) genre shift — this moment is in an unusual SEGMENT TYPE for this VOD
    genre_term = 0.0
    modal = baseline.get("modal_segment")
    if segment_at is not None and modal is not None:
        try:
            here = segment_at(0.5 * (cs + ce))
            if here and str(here) != str(modal):
                genre_term = 1.0
                signals["genre_shift"] = f"{modal}->{here}"
        except Exception:
            pass

    score = (float(w.get("rate", 0.45)) * rate_term
             + float(w.get("topic", 0.35)) * topic_term
             + float(w.get("genre", 0.20)) * genre_term)
    score = max(0.0, min(1.0, score))

    gain = float(cfg.get("gain", 0.22))
    ceil = float(cfg.get("multiplier_ceil", 1.18))
    mult = round(max(1.0, min(ceil, 1.0 + gain * score)), 4)
    return {"contrast_score": round(score, 3), "multiplier": mult,
            "signals": signals, "reason": "ok" if baseline.get("ok") or topic_hit or genre_term else "flat"}


# --- self-test ---------------------------------------------------------------
def _selftest() -> int:
    """Synthetic sanity check: anomalous moments (fast burst, dead-air pause,
    topic pivot, genre shift) beat a typical moment; cold-start/degraded are
    neutral; the multiplier stays boost-only within [1.0, ceil]."""
    cfg = load_config()

    # A steady ~2.5 wps baseline from 0..300s (10-word segments every 4s), all
    # "gaming" segment type, with one topic boundary at 200s.
    segs: List[Dict[str, Any]] = []
    t = 0.0
    while t < 300.0:
        segs.append({"start": t, "end": t + 4.0, "text": "one two three four five six seven eight nine ten"})
        t += 4.0
    # A fast burst 120..132s (~6 wps): 24 words in 12s
    burst = [{"start": 120.0, "end": 126.0, "text": " ".join(["w"] * 18)},
             {"start": 126.0, "end": 132.0, "text": " ".join(["w"] * 18)}]
    segs_fast = [s for s in segs if not (120.0 <= s["start"] < 132.0)] + burst
    segs_fast.sort(key=lambda s: s["start"])

    seg_at = lambda _t: "gaming"           # modal type
    seg_at_shift = lambda _t: "just_chatting" if 150.0 <= _t <= 170.0 else "gaming"
    tboundaries = [200.0]

    base = compute_baseline(segs, segment_at=seg_at, topic_boundaries=tboundaries, cfg=cfg)
    base_shift = compute_baseline(segs, segment_at=seg_at_shift, topic_boundaries=tboundaries, cfg=cfg)

    m_typical = {"clip_start": 60.0, "clip_end": 84.0, "timestamp": 72.0, "primary_category": "hype"}
    m_fast = {"clip_start": 118.0, "clip_end": 134.0, "timestamp": 126.0, "primary_category": "hype"}
    m_topic = {"clip_start": 198.0, "clip_end": 222.0, "timestamp": 210.0, "primary_category": "hype"}
    m_genre = {"clip_start": 150.0, "clip_end": 170.0, "timestamp": 160.0, "primary_category": "hype"}

    r_typ = evaluate(m_typical, segs, baseline=base, segment_at=seg_at, cfg=cfg)
    r_fast = evaluate(m_fast, segs_fast, baseline=base, segment_at=seg_at, cfg=cfg)
    r_topic = evaluate(m_topic, segs, baseline=base, segment_at=seg_at, cfg=cfg)
    r_genre = evaluate(m_genre, segs, baseline=base_shift, segment_at=seg_at_shift, cfg=cfg)
    r_degraded = evaluate(m_fast, segs_fast, baseline=None, segment_at=seg_at, cfg=cfg)
    # cold-start: a tiny VOD -> baseline not ok
    base_cold = compute_baseline(segs[:3], segment_at=seg_at, topic_boundaries=[], cfg=cfg)
    r_cold = evaluate(m_typical, segs[:3], baseline=base_cold, segment_at=seg_at, cfg=cfg)

    print("baseline      :", base)
    print("typical       :", r_typ)
    print("fast burst    :", r_fast)
    print("topic pivot   :", r_topic)
    print("genre shift   :", r_genre)
    print("degraded      :", r_degraded)
    print("cold-start    :", r_cold, "| base.ok =", base_cold["ok"])

    ok = True
    if not base["ok"]:
        print("FAIL: baseline should be usable on a 300s VOD"); ok = False
    if not (r_fast["multiplier"] > r_typ["multiplier"]):
        print("FAIL: fast burst should beat typical"); ok = False
    if not (r_topic["multiplier"] > r_typ["multiplier"]):
        print("FAIL: topic pivot should beat typical"); ok = False
    if not (r_genre["multiplier"] > r_typ["multiplier"]):
        print("FAIL: genre shift should beat typical"); ok = False
    if r_degraded["multiplier"] != 1.0:
        print("FAIL: degraded (no baseline) must be neutral 1.0"); ok = False
    if base_cold["ok"]:
        print("FAIL: a 3-segment VOD must NOT yield an ok baseline (cold-start)"); ok = False
    for r in (r_typ, r_fast, r_topic, r_genre, r_degraded, r_cold):
        if r["multiplier"] < 1.0 or r["multiplier"] > cfg["multiplier_ceil"] + 1e-9:
            print("FAIL: multiplier out of [1.0, ceil] (boost-only)"); ok = False
    print("SELFTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    print(json.dumps(load_config(), indent=2))
