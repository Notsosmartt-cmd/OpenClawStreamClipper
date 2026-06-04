"""reaction_signals.py — Selection Sub-Plan B (reaction-worthy).

A cheap, failure-soft "did a genuine reaction *intensity* happen here?" signal.
Pure stdlib. It takes PRE-EXTRACTED scalar signals (an audio crowd-response dict
and a post-beat chat-window dict) rather than the heavy audio/chat modules, so
it is fully unit-testable offline and decoupled from their internals.

Design contract (matches the non-gatekeeping philosophy + the cross-axis
compounding guardrail — see clipping-quality-overhaul eval findings #1/#2):
  * Boost-ONLY and bounded — the absence of a reaction never penalizes (calm
    clips are Plan A/E territory). Multiplier in [1.0, ceil] (default 1.10).
  * Deliberately the SMALLEST ceiling of the axes — reaction/energy is already
    the most-rewarded thing in Pass C (cross_validated x1.20, the speaker-change
    boost x1.15, Pass A's crowd-response gating), so B is intentionally the
    lightest axis to avoid amplifying the energy-bias the user dislikes.
  * Authenticity is NOT decided here — "earned beat vs forced hype" is the Vision
    Judge's job (its base instruction already prefers a real beat over loudness).
    This module only measures that *some* reaction intensity is present.
  * Category-aware — a crowd pop matters more for reactive/funny than for a calm
    storytime/emotional clip, so the boost is scaled down for calm categories.
  * Failure-soft — any missing input returns a neutral 1.0 multiplier; it can
    never raise into Pass C.

See AIclippingPipelineVault/wiki/concepts/plan-reaction-worthy.md.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

# --- defaults (mirrors config/selection_axes.json::reaction) ------------------
DEFAULTS: Dict[str, Any] = {
    "enabled": True,
    "gain": 0.18,                  # multiplier = 1 + gain * effective_score
    "multiplier_ceil": 1.10,       # rebalanced DOWN — the smallest axis (eval #2)
    "z_norm": 3.0,                 # chat z_score that maps to a full chat term
    "unique_chatter_min": 4,       # breadth gate — emote spam from 1-2 users != reaction
    "post_window_s": 12.0,         # [T, T+12] reaction window (vs E's sustained [T, T+60])
    "weights": {"audio": 0.55, "chat": 0.40, "rhythm": 0.05},
    "sub_legitimacy_bonus": 0.10,  # real money (subs/bits) in the window == a real reaction
    # reaction matters more for reactive/funny than for a calm story/take.
    "category_gain": {
        "reactive": 1.0, "funny": 1.0, "hype": 1.0, "dancing": 0.9,
        "controversial": 0.8, "hot_take": 0.7, "storytime": 0.6, "emotional": 0.6,
    },
    "default_category_gain": 0.85,
}


def _repo_config_path() -> Path:
    # scripts/lib/reaction_signals.py -> parents[2] == repo root
    return Path(__file__).resolve().parents[2] / "config" / "selection_axes.json"


def load_config() -> Dict[str, Any]:
    """Load the ``reaction`` block merged over the built-in defaults. Tries the
    env override, then the repo-relative config, then a legacy /root path. Any
    read/parse failure silently keeps the defaults."""
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
        blk = data.get("reaction") if isinstance(data, dict) else None
        if isinstance(blk, dict):
            for k, v in blk.items():
                if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                    cfg[k] = {**cfg[k], **v}
                else:
                    cfg[k] = v
            break
    return cfg


def _audio_term(audio: Optional[Dict[str, Any]]) -> Any:
    if not audio:
        return 0.0, 0.0, {}
    crowd = max(0.0, min(1.0, float(audio.get("crowd_response") or 0.0)))
    rhythm = max(0.0, min(1.0, float(audio.get("rhythmic_speech") or 0.0)))
    sig = {"crowd_response": round(crowd, 3)}
    if rhythm:
        sig["rhythmic_speech"] = round(rhythm, 3)
    return crowd, rhythm, sig


def _chat_term(chat: Optional[Dict[str, Any]], cfg: Dict[str, Any]) -> Any:
    """Post-beat chat *breadth* spike. Gated on unique chatters so a single user
    spamming emotes can't manufacture a reaction. Returns (term 0-1, signals)."""
    if not chat:
        return 0.0, {}
    z = float(chat.get("z_score") or 0.0)
    uniq = int(chat.get("unique_chatters") or 0)
    znorm = float(cfg.get("z_norm", 3.0)) or 3.0
    umin = int(cfg.get("unique_chatter_min", 4))
    term = 0.0 if (z <= 0 or uniq < umin) else max(0.0, min(z / znorm, 1.0))
    sig: Dict[str, Any] = {"chat_z": round(z, 2), "unique_chatters": uniq}
    return term, sig


def evaluate(
    moment: Dict[str, Any],
    segments: Sequence[Dict[str, Any]],
    *,
    audio: Optional[Dict[str, Any]] = None,
    chat: Optional[Dict[str, Any]] = None,
    cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Score one moment's reaction *intensity*.

    ``audio`` is a crowd-response dict (e.g. ``audio_events.lookup_window``);
    ``chat`` is a ``chat_features.window(T, T+post_window_s)`` dict. Either may
    be ``None``. Returns ``{"reaction_score": float|None, "multiplier": float,
    "signals": dict, "reason": str}``; ``multiplier`` is always >= 1.0 and safe.
    """
    cfg = cfg or load_config()
    if not cfg.get("enabled", True):
        return {"reaction_score": None, "multiplier": 1.0, "signals": {}, "reason": "disabled"}

    if not audio and not chat:
        return {"reaction_score": None, "multiplier": 1.0, "signals": {}, "reason": "no_signal"}

    w = cfg.get("weights", {}) or {}
    crowd, rhythm, asig = _audio_term(audio)
    chat_t, csig = _chat_term(chat, cfg)

    score = (float(w.get("audio", 0.55)) * crowd
             + float(w.get("chat", 0.40)) * chat_t
             + float(w.get("rhythm", 0.05)) * rhythm)
    sub = int((chat or {}).get("sub_count") or 0)
    if sub > 0:
        score += float(cfg.get("sub_legitimacy_bonus", 0.10))
    score = max(0.0, min(1.0, score))

    cat = str(moment.get("primary_category") or "hype").lower()
    cat_gain = float(cfg.get("category_gain", {}).get(cat, cfg.get("default_category_gain", 0.85)))
    eff = score * cat_gain

    gain = float(cfg.get("gain", 0.18))
    ceil = float(cfg.get("multiplier_ceil", 1.10))
    mult = round(max(1.0, min(ceil, 1.0 + gain * eff)), 4)

    signals: Dict[str, Any] = {"category_gain": round(cat_gain, 2)}
    signals.update(asig)
    if chat:
        signals.update(csig)
        if sub > 0:
            signals["sub_count"] = sub
    return {"reaction_score": round(score, 3), "multiplier": mult,
            "signals": signals, "reason": "ok"}


# --- self-test ---------------------------------------------------------------
def _selftest() -> int:
    """Synthetic sanity check: a real reaction beats a flat moment; chat spam
    (no breadth) and calm categories are damped; boost-only floor holds."""
    cfg = load_config()

    m_funny = {"primary_category": "funny", "timestamp": 100}
    m_calm = {"primary_category": "storytime", "timestamp": 100}

    strong_audio = {"crowd_response": 0.85, "rhythmic_speech": 0.0}
    strong_chat = {"z_score": 4.0, "unique_chatters": 25, "sub_count": 2}
    spam_chat = {"z_score": 6.0, "unique_chatters": 2, "sub_count": 0}
    # A moderate reaction stays below the ceiling so the category scaling is
    # observable (a saturating reaction maxes the small boost regardless).
    mod_audio = {"crowd_response": 0.4}
    mod_chat = {"z_score": 1.5, "unique_chatters": 8, "sub_count": 0}

    r_strong = evaluate(m_funny, [], audio=strong_audio, chat=strong_chat, cfg=cfg)
    r_flat = evaluate(m_funny, [], audio={"crowd_response": 0.0}, chat=None, cfg=cfg)
    r_spam = evaluate(m_funny, [], audio=None, chat=spam_chat, cfg=cfg)
    r_mod_funny = evaluate(m_funny, [], audio=mod_audio, chat=mod_chat, cfg=cfg)
    r_mod_calm = evaluate(m_calm, [], audio=mod_audio, chat=mod_chat, cfg=cfg)
    r_none = evaluate(m_funny, [], audio=None, chat=None, cfg=cfg)

    print("strong reaction :", r_strong)
    print("flat moment     :", r_flat)
    print("chat spam (no breadth):", r_spam)
    print("moderate funny  :", r_mod_funny)
    print("moderate calm   :", r_mod_calm)
    print("no signal       :", r_none)

    ok = True
    if not (r_strong["multiplier"] > r_flat["multiplier"]):
        print("FAIL: strong reaction should outscore flat"); ok = False
    if not (r_strong["multiplier"] > r_spam["multiplier"]):
        print("FAIL: real reaction should beat breadth-less chat spam"); ok = False
    if not (r_mod_calm["multiplier"] < r_mod_funny["multiplier"]):
        print("FAIL: calm category should be damped vs funny (moderate input)"); ok = False
    if not (abs(r_strong["multiplier"] - cfg["multiplier_ceil"]) < 1e-9):
        print("FAIL: a saturating reaction should reach the ceiling"); ok = False
    if r_none["multiplier"] != 1.0:
        print("FAIL: no-signal must be neutral 1.0"); ok = False
    for r in (r_strong, r_flat, r_spam, r_mod_funny, r_mod_calm, r_none):
        if r["multiplier"] < 1.0 or r["multiplier"] > cfg["multiplier_ceil"] + 1e-9:
            print("FAIL: multiplier out of [1.0, ceil] (boost-only)"); ok = False
    print("SELFTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    print(json.dumps(load_config(), indent=2))
