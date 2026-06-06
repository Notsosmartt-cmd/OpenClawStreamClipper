"""engagement_signals.py — Selection Sub-Plan E (engagement / discussion-worthiness).

Surfaces the **low-impact but high-engagement** "yap" / take clip: a streamer
giving a clear, relatable opinion on a topic the audience will argue about in the
comments (the DDG AP×Swatch / Joe-Bart pause-and-opine archetype). Engagement is
NOT impact (axes A-D) and NOT retention (Phases 2-4): it is "will viewers
comment / debate / relate?".

Pure stdlib + the optional `conversation_shape` module. Two complementary signals:
  * **Predicted (always)** — a firm stance from `conversation_shape` discourse
    markers (`claim_stake` / `info_ramble_marker` *without* an immediate
    `concession`) plus a held monologue. Kept **modest** — the `hot_take` category
    + the spicy/engagement style already weight stance, so this must not double-count.
  * **Observed (when VOD chat exists)** — **sustained** post-moment discussion over
    `[T, T+post_window_s]` (default 60 s): chat `z_score` breadth gated on
    `unique_chatters`. This is the genuinely-new signal — and it is deliberately a
    *wider, longer* window than Plan B's `[T, T+12]` reaction spike (debate, not a
    pop). The `chat_features` timing fields survived the Pass-A removal and are
    otherwise unused; for engagement the chat *latency* is exactly the point.

Design contract (matches the non-gatekeeping philosophy + the compounding guardrail):
  * Boost-ONLY and bounded — multiplier in [1.0, ceil] (default 1.12). The absence
    of a take is neutral, never penalized.
  * Predicted-only (no chat) tops out low — a stance alone is a small nudge, since
    the existing hot_take machinery already rewards it; observed discussion is what
    pushes toward the ceiling.
  * Category-aware — talky categories (hot_take/controversial/storytime/emotional)
    get the full effect; dancing/hype are damped (those aren't "takes").
  * Failure-soft — any missing input returns a neutral 1.0 multiplier.

See AIclippingPipelineVault/wiki/concepts/plan-engagement-discussion.md.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

# --- defaults (mirrors config/selection_axes.json::engagement) -----------------
DEFAULTS: Dict[str, Any] = {
    "enabled": True,
    "gain": 0.18,
    "multiplier_ceil": 1.12,
    "post_window_s": 60.0,         # SUSTAINED discussion window (vs Plan B's 12 s spike)
    "z_norm": 2.5,                 # chat z_score that maps to a full observed term
    "unique_chatter_min": 6,       # debate BREADTH gate (higher than B — sustained/broad)
    "weights": {"predicted": 0.40, "observed": 0.60},   # observed discussion is the star
    "weights_predicted": {"stance": 0.6, "monologue": 0.4},
    "stance_classes": ["claim_stake", "info_ramble_marker"],
    "concession_classes": ["concession"],
    "category_gain": {
        "hot_take": 1.0, "controversial": 1.0, "storytime": 1.0, "emotional": 1.0,
        "reactive": 0.9, "funny": 0.85, "hype": 0.85, "dancing": 0.7,
    },
    "default_category_gain": 0.9,
}


def _repo_config_path() -> Path:
    # scripts/lib/engagement_signals.py -> parents[2] == repo root
    return Path(__file__).resolve().parents[2] / "config" / "selection_axes.json"


def load_config() -> Dict[str, Any]:
    """Load the ``engagement`` block merged over the built-in defaults. Env override
    -> repo config -> legacy /root path; any read/parse failure keeps defaults."""
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
        blk = data.get("engagement") if isinstance(data, dict) else None
        if isinstance(blk, dict):
            for k, v in blk.items():
                if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                    cfg[k] = {**cfg[k], **v}
                else:
                    cfg[k] = v
            break
    return cfg


def _clip_bounds(moment: Dict[str, Any]) -> Any:
    cs, ce = moment.get("clip_start"), moment.get("clip_end")
    if cs is None or ce is None:
        t = float(moment.get("timestamp", 0) or 0)
        cs, ce = t - 15.0, t + 15.0
    cs, ce = float(cs), float(ce)
    if ce <= cs:
        ce = cs + 1.0
    return cs, ce


def _predicted_stance(moment, segments, cs, ce, shape_module, markers, cfg, signals) -> float:
    """Firm-stance + held-monologue score in [0,1] from conversation_shape."""
    if shape_module is None or markers is None:
        return 0.0
    win = [s for s in segments
           if float(s.get("end", 0) or 0) > cs and float(s.get("start", 0) or 0) < ce]
    if not win:
        return 0.0
    try:
        shape = shape_module.analyze_chunk(win, cs, ce, markers=markers)
    except Exception:
        return 0.0
    mk = shape.get("discourse_markers") or []
    stance_cls = set(cfg.get("stance_classes", []))
    conc_cls = set(cfg.get("concession_classes", []))
    has_stake = any(m.get("class") in stance_cls for m in mk)
    has_conc = any(m.get("class") in conc_cls for m in mk)
    # A firm stance (no backing down) is the most debate-provoking.
    stance_term = 1.0 if (has_stake and not has_conc) else (0.5 if has_stake else 0.0)
    dur = ce - cs
    cov = 0.0
    for r in (shape.get("monologue_runs") or []):
        ov = min(ce, float(r.get("end", 0))) - max(cs, float(r.get("start", 0)))
        if ov > 0:
            cov = max(cov, ov / dur)
    wp = cfg.get("weights_predicted", {}) or {}
    pred = max(0.0, min(1.0, float(wp.get("stance", 0.6)) * stance_term
                        + float(wp.get("monologue", 0.4)) * min(cov, 1.0)))
    if has_stake:
        signals["stance"] = "firm" if not has_conc else "qualified"
    if cov > 0.3:
        signals["monologue_cov"] = round(cov, 2)
    return pred


def evaluate(
    moment: Dict[str, Any],
    segments: Sequence[Dict[str, Any]],
    *,
    chat: Optional[Dict[str, Any]] = None,
    shape_module: Any = None,
    markers: Optional[Dict[str, Any]] = None,
    cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Score one moment's discussion-worthiness.

    ``chat`` is a ``chat_features.window(T, T+post_window_s)`` dict (or None).
    Returns ``{"engagement_score": float|None, "multiplier": float, "signals":
    dict, "reason": str}``; ``multiplier`` is always >= 1.0 (boost-only) and safe.
    """
    cfg = cfg or load_config()
    if not cfg.get("enabled", True):
        return {"engagement_score": None, "multiplier": 1.0, "signals": {}, "reason": "disabled"}

    if shape_module is None:
        try:
            import conversation_shape as shape_module  # type: ignore
        except Exception:
            shape_module = None

    cs, ce = _clip_bounds(moment)
    signals: Dict[str, Any] = {}

    predicted = _predicted_stance(moment, segments, cs, ce, shape_module, markers, cfg, signals)

    # observed sustained discussion (breadth-gated)
    observed = 0.0
    if chat:
        z = float(chat.get("z_score") or 0.0)
        uniq = int(chat.get("unique_chatters") or 0)
        umin = int(cfg.get("unique_chatter_min", 6))
        znorm = float(cfg.get("z_norm", 2.5)) or 2.5
        if z > 0 and uniq >= umin:
            observed = max(0.0, min(z / znorm, 1.0))
            signals["chat_z"] = round(z, 2)
            signals["unique_chatters"] = uniq

    if predicted <= 0 and observed <= 0:
        return {"engagement_score": None, "multiplier": 1.0, "signals": signals, "reason": "flat"}

    w = cfg.get("weights", {}) or {}
    if observed <= 0:
        # 3B (2026-06-06): chat absent/insufficient -> `observed` (0.60 weight)
        # is dead mass that dilutes the score to a near-no-op (median 1.0 with
        # no chat). Renormalize the surviving `predicted` term — a structural,
        # ENERGY-FREE stance/monologue signal from conversation_shape — to full
        # weight so a firm take or sustained monologue still earns its boost.
        # Energy-safe by construction (no audio/loudness here). Only changes
        # chatless VODs; chat VODs keep the original blend. weights_chat_absent
        # in config/selection_axes.json. See clip-quality-remediation-2026-06 3B.
        wp = float((cfg.get("weights_chat_absent", {}) or {}).get("predicted", 1.0))
        score = max(0.0, min(1.0, wp * predicted))
    else:
        score = max(0.0, min(1.0, float(w.get("predicted", 0.40)) * predicted
                             + float(w.get("observed", 0.60)) * observed))

    cat = str(moment.get("primary_category") or "hype").lower()
    cat_gain = float(cfg.get("category_gain", {}).get(cat, cfg.get("default_category_gain", 0.9)))
    eff = score * cat_gain

    gain = float(cfg.get("gain", 0.18))
    ceil = float(cfg.get("multiplier_ceil", 1.12))
    mult = round(max(1.0, min(ceil, 1.0 + gain * eff)), 4)

    signals.update({"predicted": round(predicted, 2), "observed": round(observed, 2),
                    "category_gain": round(cat_gain, 2)})
    return {"engagement_score": round(score, 3), "multiplier": mult,
            "signals": signals, "reason": "ok"}


# --- self-test ---------------------------------------------------------------
def _selftest() -> int:
    """Synthetic sanity check: a firm take with sustained chat discussion beats a
    flat moment; predicted-only is a small nudge; chat spam (no breadth) is gated;
    dancing is damped; degraded is neutral; boost-only bounds hold."""
    import conversation_shape as cs_mod
    markers = cs_mod.load_discourse_markers()
    cfg = load_config()

    # A firm info-ramble take (info_ramble_marker + claim_stake, NO concession).
    take = [
        {"start": 100.0, "end": 106.0, "text": "Okay the reality is these collabs are a total scam.", "speaker": "S0"},
        {"start": 106.0, "end": 113.0, "text": "What people don't realize is you're paying double for half the watch.", "speaker": "S0"},
        {"start": 113.0, "end": 120.0, "text": "Trust me, I own three of them and they're useless now.", "speaker": "S0"},
    ]
    flat = [{"start": 200.0, "end": 205.0, "text": "yeah we go left here then grab the loot.", "speaker": "S0"}]

    m_take = {"primary_category": "hot_take", "clip_start": 100, "clip_end": 120, "timestamp": 110}
    m_flat = {"primary_category": "hype", "clip_start": 200, "clip_end": 205, "timestamp": 202}
    m_take_dance = {"primary_category": "dancing", "clip_start": 100, "clip_end": 120, "timestamp": 110}

    debate_chat = {"z_score": 3.2, "unique_chatters": 22}
    spam_chat = {"z_score": 6.0, "unique_chatters": 2}

    r_full = evaluate(m_take, take, chat=debate_chat, shape_module=cs_mod, markers=markers, cfg=cfg)
    r_pred = evaluate(m_take, take, chat=None, shape_module=cs_mod, markers=markers, cfg=cfg)
    r_spam = evaluate(m_take, take, chat=spam_chat, shape_module=cs_mod, markers=markers, cfg=cfg)
    r_flat = evaluate(m_flat, flat, chat=None, shape_module=cs_mod, markers=markers, cfg=cfg)
    # Category damping is observable only below the ceiling, so compare at the
    # predicted-only level (a saturating take+chat maxes the boost either way).
    r_dance = evaluate(m_take_dance, take, chat=None, shape_module=cs_mod, markers=markers, cfg=cfg)
    r_deg = evaluate(m_take, take, chat=None, shape_module=None, markers=None, cfg=cfg)

    print("take + chat   :", r_full)
    print("predicted only:", r_pred)
    print("take + spam   :", r_spam)
    print("flat          :", r_flat)
    print("dancing (pred):", r_dance)
    print("degraded      :", r_deg)

    ok = True
    if not (r_full["multiplier"] > r_pred["multiplier"]):
        print("FAIL: observed discussion should beat predicted-only"); ok = False
    if not (r_pred["multiplier"] > 1.0):
        print("FAIL: a firm take should give some predicted boost"); ok = False
    if not (r_full["multiplier"] > r_spam["multiplier"]):
        print("FAIL: real debate should beat breadth-less chat spam"); ok = False
    if not (abs(r_full["multiplier"] - cfg["multiplier_ceil"]) < 1e-9):
        print("FAIL: a firm take + sustained debate should reach the ceiling"); ok = False
    if not (r_dance["multiplier"] < r_pred["multiplier"]):
        print("FAIL: dancing should be damped vs hot_take (predicted-only)"); ok = False
    if r_flat["multiplier"] != 1.0:
        print("FAIL: flat moment must be neutral 1.0"); ok = False
    if r_deg["multiplier"] != 1.0:
        print("FAIL: degraded (no shape, no chat) must be neutral 1.0"); ok = False
    for r in (r_full, r_pred, r_spam, r_flat, r_dance, r_deg):
        if r["multiplier"] < 1.0 or r["multiplier"] > cfg["multiplier_ceil"] + 1e-9:
            print("FAIL: multiplier out of [1.0, ceil] (boost-only)"); ok = False
    print("SELFTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    print(json.dumps(load_config(), indent=2))
