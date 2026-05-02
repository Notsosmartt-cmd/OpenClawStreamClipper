"""Pass D — structured rubric judge (Tier-4 Phase 4.4).

Re-scores every Pass C survivor using a 7-dimension rubric on the same
multimodal model. Position in pipeline: between Pass C (writes
hype_moments.json) and Phase 4.2 boundary snap.

The model is asked to score each candidate on:
    setup_strength, payoff_strength, originality, broad_appeal,
    replay_value, audio_quality, self_contained

Aggregates into a rubric_score in [0, 1]; blends into a new final_score:
    final_score = blend.pass_c_weight * pass_c_score + blend.rubric_weight * rubric_score

Failure-soft: if a per-moment call fails, that moment keeps its Pass C
score unchanged. Three consecutive network-shaped failures abort Pass D
for the rest of the VOD (mirrors BUG 32 / Pass B fail-fast). The phase
can never delete a candidate.

Reads:
    /tmp/clipper/hype_moments.json     (Pass C output)
    /tmp/clipper/transcript.json        (for the per-moment transcript window)
    /tmp/clipper/conversation_shape.json (optional — Phase 4.2)
    /root/.openclaw/rubric.json         (weights + timeouts)
    /root/.openclaw/patterns.json       (Pattern Catalog)

Writes back:
    /tmp/clipper/hype_moments.json      (each moment gains rubric_scores,
                                         pattern_confirmed, audit_one_liner,
                                         rubric_score, final_score, raw_score)
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

LLM_URL = os.environ.get("LLM_URL", "http://host.docker.internal:1234")
TEXT_MODEL_PASSB = os.environ.get("TEXT_MODEL_PASSB") or os.environ.get("TEXT_MODEL", "")
TEMP_DIR = "/tmp/clipper"

DEFAULT_WEIGHTS = {
    "setup_strength":  0.15,
    "payoff_strength": 0.25,
    "originality":     0.20,
    "broad_appeal":    0.15,
    "replay_value":    0.10,
    "audio_quality":   0.05,
    "self_contained":  0.10,
}

DEFAULT_BLEND = {"pass_c_weight": 0.6, "rubric_weight": 0.4}

NET_PATTERNS = (
    "Network is unreachable",
    "Errno 101", "Errno 111",
    "Connection refused",
    "Name or service not known",
    "timed out", "Read timed out",
)


def _load_rubric_config() -> Dict[str, Any]:
    for path in ("/root/.openclaw/rubric.json", "/root/scripts/lib/../../config/rubric.json"):
        try:
            with open(path) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
    return {}


def _load_patterns() -> List[Dict[str, Any]]:
    for path in ("/root/.openclaw/patterns.json", "/root/scripts/lib/../../config/patterns.json"):
        try:
            with open(path) as f:
                data = json.load(f)
            return list(data.get("patterns") or [])
        except (OSError, json.JSONDecodeError):
            continue
    return []


def _load_segments() -> List[Dict[str, Any]]:
    try:
        with open(f"{TEMP_DIR}/transcript.json") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return list(data.get("segments") or [])
        return list(data or [])
    except (OSError, json.JSONDecodeError):
        return []


def _load_shape_index() -> Dict[str, Any]:
    try:
        with open(f"{TEMP_DIR}/conversation_shape.json") as f:
            return json.load(f) or {}
    except (OSError, json.JSONDecodeError):
        return {}


def _looks_like_outage(err: str) -> bool:
    return any(p in str(err) for p in NET_PATTERNS)


def _transcript_window(segments: Sequence[Dict[str, Any]], t: float, span: float) -> str:
    lo, hi = t - span, t + span
    parts: List[str] = []
    for s in segments:
        if s.get("end", 0) >= lo and s.get("start", 0) <= hi:
            mm = int(s.get("start", 0)) // 60
            ss = int(s.get("start", 0)) % 60
            parts.append(f"[{mm:02d}:{ss:02d}] {s.get('text', '').strip()}")
    return "\n".join(parts)[:1500]


def _shape_for_moment(shape_index: Dict[str, Any], t: float) -> Optional[Dict[str, Any]]:
    """Find the chunk that contains this moment's timestamp."""
    if not shape_index:
        return None
    for _key, rec in shape_index.items():
        if not isinstance(rec, dict):
            continue
        cs = rec.get("chunk_start", 0)
        ce = rec.get("chunk_end", 0)
        if cs <= t <= ce:
            return rec
    return None


def _serialize_shape(rec: Optional[Dict[str, Any]]) -> str:
    if not rec:
        return "(no conversation_shape data)"
    try:
        import conversation_shape as _cs
        return _cs.serialize_for_prompt(rec, max_chars=600)
    except Exception:
        return "(conversation_shape unavailable)"


def _serialize_pattern_signature(catalog: List[Dict[str, Any]], pid: str) -> str:
    if not pid:
        return "(unspecified — let the rubric speak for itself)"
    for p in catalog:
        if p.get("id") == pid:
            return f"{p.get('label', pid)}: {p.get('signature', '')}"
    return f"(unknown pattern id '{pid}')"


def _build_prompt(
    moment: Dict[str, Any],
    *,
    transcript_window: str,
    shape_block: str,
    pattern_sig: str,
    pattern_ids: List[str],
) -> str:
    pid_csv = ", ".join(pattern_ids) if pattern_ids else "(no patterns loaded)"
    primary = moment.get("primary_pattern") or "(none)"
    why = (moment.get("why") or "").strip()[:300]
    cs = moment.get("clip_start", moment.get("timestamp"))
    ce = moment.get("clip_end", moment.get("timestamp"))
    return f"""/no_think
You are an editor scoring a clip candidate for replay value on a 0-10 rubric.

CANDIDATE:
- Time window: {int(cs)}s - {int(ce)}s
- Pattern claimed by Pass B: {primary}
- Pass B reasoning: {why}

TRANSCRIPT (verbatim, ~90s window):
\"\"\"{transcript_window}\"\"\"

CONVERSATION SHAPE (from speech analysis):
{shape_block}

PATTERN SIGNATURE (for reference):
{pattern_sig}

VALID PATTERN IDS: {pid_csv}

RATE 0-10 on each dimension:
- setup_strength: how cleanly the moment establishes context the payoff needs (0 = no setup, 10 = perfect setup)
- payoff_strength: how strong the payoff/reaction/punchline lands (0 = no payoff, 10 = chef's kiss)
- originality: how rare this moment-shape is on streams (0 = generic, 10 = unique)
- broad_appeal: how watchable to a stranger who doesn't know the streamer (0 = niche, 10 = anyone laughs)
- replay_value: how rewatchable this is — would you send it to a friend? (0 = once and done, 10 = permanent rotation)
- audio_quality: speech clear, music balanced, no hard cuts (0 = unintelligible, 10 = clean)
- self_contained: works as a clip without prior context (0 = needs minutes of context, 10 = stands alone)

If any dimension scores 0, name a rejection_flag (e.g. "no_payoff", "needs_context", "unintelligible").

Set pattern_confirmed to the pattern id that best fits, or null if no pattern fits. Set pattern_match_strength 0.0-1.0.

Write a 25-word-max audit_one_liner naming what makes this clip work (or fail).

RETURN ONLY JSON:
{{"scores": {{"setup_strength": 0-10, "payoff_strength": 0-10, "originality": 0-10, "broad_appeal": 0-10, "replay_value": 0-10, "audio_quality": 0-10, "self_contained": 0-10}}, "pattern_confirmed": "<pattern_id or null>", "pattern_match_strength": 0.0-1.0, "rejection_flags": ["<flag>", ...], "audit_one_liner": "<text>"}}"""


def _call_llm(prompt: str, *, timeout: int) -> Optional[str]:
    if not TEXT_MODEL_PASSB:
        return None
    payload = {
        "model": TEXT_MODEL_PASSB,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "max_tokens": 1000,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    # response_format intentionally omitted — Gemma-4 26B rejects it with HTTP 400
    # (BUG 33 cascade). _parse_response below handles freeform JSON via
    # `find("{")` / `rfind("}")` so the prompt's "RETURN ONLY JSON" instruction
    # is enough.
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{LLM_URL}/v1/chat/completions",
        data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    choice = (body.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    content = (msg.get("content") or "").strip()
    if not content:
        # 35B+ Gemma reasoning fallback per BUG 17.
        content = (msg.get("reasoning_content") or "").strip()
    return content or None


def _parse_response(text: str, valid_ids: set) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    s = text.strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s, flags=re.IGNORECASE)
    js = s.find("{")
    je = s.rfind("}")
    if js < 0 or je <= js:
        return None
    try:
        obj = json.loads(s[js:je + 1])
    except json.JSONDecodeError:
        return None
    scores = obj.get("scores")
    if not isinstance(scores, dict):
        return None
    cleaned: Dict[str, int] = {}
    for k in DEFAULT_WEIGHTS:
        v = scores.get(k)
        try:
            cleaned[k] = max(0, min(int(round(float(v))), 10))
        except (ValueError, TypeError):
            cleaned[k] = 0
    pattern_confirmed = obj.get("pattern_confirmed")
    if pattern_confirmed and (not isinstance(pattern_confirmed, str) or pattern_confirmed not in valid_ids):
        pattern_confirmed = None
    try:
        strength = float(obj.get("pattern_match_strength") or 0.0)
        strength = max(0.0, min(strength, 1.0))
    except (ValueError, TypeError):
        strength = 0.0
    flags_raw = obj.get("rejection_flags") or []
    flags = [str(f).strip() for f in flags_raw if isinstance(f, str)] if isinstance(flags_raw, list) else []
    one_liner = str(obj.get("audit_one_liner") or "")[:200]
    return {
        "scores": cleaned,
        "pattern_confirmed": pattern_confirmed,
        "pattern_match_strength": strength,
        "rejection_flags": flags,
        "audit_one_liner": one_liner,
    }


def _aggregate_rubric_score(scores: Dict[str, int], weights: Dict[str, float]) -> float:
    total_w = sum(weights.values())
    if total_w <= 0:
        return 0.0
    s = sum((scores.get(k, 0) / 10.0) * w for k, w in weights.items())
    return max(0.0, min(s / total_w, 1.0))


def run_pass_d(moments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Score every moment with the rubric and blend into final_score.

    Mutates each moment in-place AND returns the list (for caller convenience).
    Failure-soft: per-moment failures preserve the moment's Pass C score.
    """
    cfg = _load_rubric_config()
    weights = {**DEFAULT_WEIGHTS, **(cfg.get("weights") or {})}
    blend = {**DEFAULT_BLEND, **(cfg.get("blend") or {})}
    per_timeout = int(cfg.get("per_moment_timeout_seconds", 60))
    stage_timeout = int(cfg.get("stage_timeout_seconds", 600))
    fail_limit = int(cfg.get("fail_streak_limit", 3))
    demote_step = float(cfg.get("rejection_flag_demote", -0.5))

    catalog = _load_patterns()
    valid_pattern_ids = {p["id"] for p in catalog if isinstance(p, dict) and p.get("id")}
    segments = _load_segments()
    shape_index = _load_shape_index()

    print(
        f"[PASS D] Rubric judge starting on {len(moments)} moments "
        f"(weights total={sum(weights.values()):.2f}, blend={blend['pass_c_weight']:.1f}/"
        f"{blend['rubric_weight']:.1f})",
        file=sys.stderr,
    )

    started = time.time()
    fail_streak = 0
    aborted = False

    for idx, moment in enumerate(moments, start=1):
        if aborted:
            continue
        if (time.time() - started) > stage_timeout:
            print(f"[PASS D] stage timeout exceeded after {idx - 1} moments — keeping remainder unchanged", file=sys.stderr)
            aborted = True
            continue

        t = float(moment.get("timestamp", 0))
        transcript = _transcript_window(segments, t, span=45)
        shape_rec = _shape_for_moment(shape_index, t)
        shape_block = _serialize_shape(shape_rec)
        pattern_sig = _serialize_pattern_signature(catalog, moment.get("primary_pattern") or "")

        prompt = _build_prompt(
            moment,
            transcript_window=transcript,
            shape_block=shape_block,
            pattern_sig=pattern_sig,
            pattern_ids=sorted(valid_pattern_ids),
        )

        try:
            raw = _call_llm(prompt, timeout=per_timeout)
        except Exception as e:  # noqa: BLE001 — we fall back per-moment
            err = str(e)
            if _looks_like_outage(err):
                fail_streak += 1
                print(f"[PASS D] T={int(t)} network error ({err}); streak={fail_streak}/{fail_limit}", file=sys.stderr)
                if fail_streak >= fail_limit:
                    print("[PASS D] Aborting — persistent LM Studio outage.", file=sys.stderr)
                    aborted = True
            else:
                fail_streak = 0
                print(f"[PASS D] T={int(t)} call failed ({err}); keeping Pass C score", file=sys.stderr)
            continue

        fail_streak = 0
        parsed = _parse_response(raw or "", valid_pattern_ids)
        if not parsed:
            print(f"[PASS D] T={int(t)} unparseable response; keeping Pass C score", file=sys.stderr)
            continue

        rubric_score = _aggregate_rubric_score(parsed["scores"], weights)
        if parsed["rejection_flags"]:
            rubric_score = max(0.0, rubric_score + demote_step * len(parsed["rejection_flags"]))

        pass_c_score = float(moment.get("score", 0) or 0)
        raw_score = (
            blend["pass_c_weight"] * pass_c_score
            + blend["rubric_weight"] * rubric_score
        )
        final_score = max(0.0, min(raw_score, 1.0))

        moment["rubric_scores"] = parsed["scores"]
        moment["rubric_score"] = round(rubric_score, 3)
        moment["pattern_confirmed"] = parsed["pattern_confirmed"]
        moment["pattern_match_strength"] = round(parsed["pattern_match_strength"], 3)
        moment["rejection_flags"] = parsed["rejection_flags"]
        moment["audit_one_liner"] = parsed["audit_one_liner"]
        moment["raw_score"] = round(raw_score, 4)
        moment["score"] = round(final_score, 3)

        ol = parsed["audit_one_liner"][:60].replace("\n", " ")
        print(
            f"[PASS D] T={int(t)} rubric={rubric_score:.3f} "
            f"final={final_score:.3f} pattern={parsed['pattern_confirmed']} — {ol}",
            file=sys.stderr,
        )

    return moments


def main(argv: Sequence[str]) -> int:
    moments_path = argv[1] if len(argv) > 1 else f"{TEMP_DIR}/hype_moments.json"
    try:
        with open(moments_path) as f:
            moments = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"[PASS D] couldn't load {moments_path}: {e}", file=sys.stderr)
        return 0

    if not moments:
        print("[PASS D] no moments to score; exiting", file=sys.stderr)
        return 0

    sys.path.insert(0, "/root/scripts/lib")
    run_pass_d(moments)

    with open(moments_path, "w") as f:
        json.dump(moments, f)
    print(f"[PASS D] wrote {len(moments)} moments back to {moments_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
