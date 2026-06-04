#!/usr/bin/env python3
"""Two-tier grounding cascade.

- **Tier 1**: regex denylist + content-word overlap. Stdlib only, <5 ms.
  Catches exact-word hallucinations ("gifted subs" with no "gifted" / "subs"
  in the transcript) and the Phase 2.4d hard-event check (denylist hit on a
  Twitch-event keyword + zero count in chat → instant reject).
- **Tier 2**: LLM-as-judge using the pipeline's main text model. One call
  returns 5-dimensional scores (grounding / setup_payoff / speaker /
  conceptual / callback) which collapse to a 0-10 weighted mean. Pass
  threshold default 5.0. Replaces the previous MiniCheck NLI + Lynx-8B
  cascade tiers (retired 2026-05-01) — the same multimodal model that
  generated the claim now also judges it. Trade-off documented in
  [[concepts/bugs-and-fixes#BUG 52]].

Public API:
- ``check_claim(...)`` → Tier 1 only (preserved for back-compat).
- ``cascade_check(...)`` → full 2-tier cascade.
- ``load_grounding_config(...)`` / ``load_denylist(...)`` → config loaders.
- ``llm_judge(...)`` → call the judge directly.

Wired into two places:

- **Pass B post-parse** (``stage4_moments.py``): null a moment's ``why``
  field if the cascade fails against the ±90 s transcript window.
- **Stage 6 post-parse** (``stage6_vision.py``): null
  ``title``/``hook``/``description`` against the ±8 s transcript window +
  Pass-B why. On fail the caller may regenerate the VLM call once with a
  stricter prompt before giving up.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Iterable, List, Dict, Optional

# The project's config/ dir is bind-mounted into the container at
# /root/.openclaw (see docker-compose.yml: `./config:/root/.openclaw`).
# Allow a runtime override via CLIP_DENYLIST_PATH for testing.
DEFAULT_DENYLIST_PATH = Path(
    os.environ.get("CLIP_DENYLIST_PATH", "/root/.openclaw/denylist.json")
)

# Small hand-maintained stop-word set. Bigger lists (NLTK/spaCy) would drop
# too many short content words that matter in chat/stream language
# ("clip", "sub", "pog"). This is intentionally minimal.
_STOP_WORDS = frozenset(
    """
    a an and are as at be been being but by do does doing from for had has have
    having he her here him his i in into is it its just me my of off on or our
    out so some such than that the their them then there these they this those
    to too us was we were what when where which while who whom why will with
    would you your yours about after all also am any because before can did
    each even every got ever go going however if like no nor not now once only
    other over own same should since still through too two under until up very
    want way yeah yes ve re ll d s m o ok okay gonna wanna
    """.split()
)


def _tokenize(text: str) -> List[str]:
    return re.findall(r"[A-Za-z']+", (text or "").lower())


def _content_tokens(text: str) -> set:
    """Return lowercase tokens minus stopwords and 1-2 letter fragments."""
    return {t for t in _tokenize(text) if len(t) > 2 and t not in _STOP_WORDS}


def content_overlap_ratio(claim: str, reference: str) -> float:
    """Fraction of distinct content tokens in ``claim`` that appear in
    ``reference``. Empty claim → 1.0 (nothing to check). Empty reference
    with non-empty claim → 0.0."""
    claim_toks = _content_tokens(claim)
    if not claim_toks:
        return 1.0
    ref_toks = _content_tokens(reference)
    if not ref_toks:
        return 0.0
    return len(claim_toks & ref_toks) / len(claim_toks)


def load_denylist(path: Optional[str] = None) -> Dict[str, List[re.Pattern]]:
    """Load and compile the denylist JSON. Returns {category: [Pattern,...]}.

    A missing or unparseable file yields an empty dict — the pipeline
    falls back to overlap-only gating, which is still useful.
    """
    p = Path(path) if path else DEFAULT_DENYLIST_PATH
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    out: Dict[str, List[re.Pattern]] = {}
    for cat, spec in (data.get("categories") or {}).items():
        patterns = spec.get("patterns", []) if isinstance(spec, dict) else spec
        compiled: List[re.Pattern] = []
        for pat in patterns:
            try:
                compiled.append(re.compile(pat, re.IGNORECASE))
            except re.error:
                # Bad pattern in config → skip silently; better than
                # crashing the whole pipeline over a typo.
                continue
        if compiled:
            out[cat] = compiled
    return out


def denylist_hits(
    text: str, compiled: Dict[str, List[re.Pattern]]
) -> List[Dict]:
    """Return every denylist hit in ``text`` as a list of dicts."""
    hits: List[Dict] = []
    if not text:
        return hits
    for cat, regexes in compiled.items():
        for rx in regexes:
            m = rx.search(text)
            if m:
                hits.append(
                    {"category": cat, "pattern": rx.pattern, "match": m.group(0)}
                )
    return hits


def _event_contradicts(
    hit: Dict,
    hard_events: Optional[Dict[str, int]],
    event_map: Optional[Dict[str, Dict[str, str]]],
) -> Optional[str]:
    """Phase 2.4d — return an event_key name if a denylist hit is directly
    contradicted by zero-count hard ground truth, else None.

    ``event_map`` shape matches ``config/chat.json``'s ``ground_truth``:
        {denylist_category: {keyword_substring: event_count_key}}

    For each hit, look up the category's keyword→event map; if the
    matched phrase contains any mapped keyword AND ``hard_events[key]``
    is 0, the claim is flat-out contradicted by ground truth (e.g. title
    says "gifted subs" but ``sub_count`` for the window is 0).
    """
    if not hard_events or not event_map:
        return None
    cat = hit.get("category") or ""
    keyword_map = event_map.get(cat) or {}
    if not keyword_map:
        return None
    match_lower = (hit.get("match") or "").lower()
    for kw, event_key in keyword_map.items():
        if kw.lower() in match_lower:
            if int(hard_events.get(event_key, 0) or 0) == 0:
                return event_key
            return None
    return None


def check_claim(
    claim: str,
    references: Iterable[str],
    denylist: Optional[Dict[str, List[re.Pattern]]] = None,
    min_overlap: float = 0.20,
    hard_events: Optional[Dict[str, int]] = None,
    event_map: Optional[Dict[str, Dict[str, str]]] = None,
) -> Dict:
    """Validate ``claim`` against one or more reference strings.

    Passes when:
      (1) no denylist term appears in ``claim``, OR every denylist term
          that appears in ``claim`` ALSO appears in at least one reference
          (i.e. the streamer actually said it); AND
      (2) the content-token overlap between ``claim`` and the concatenated
          references is >= ``min_overlap``.

    Phase 2.4d: when ``hard_events`` + ``event_map`` are supplied, a
    denylist hit whose keyword maps to an event count of zero is an
    automatic hard fail with ``reason="event_contradicts_ground_truth"``.
    This catches the "gifted subs" title with ``sub_count == 0`` case
    that word-overlap alone can't catch.
    """
    ref_text = "\n".join(r for r in references if r)
    out: Dict = {"passed": True, "reason": "ok", "denylist_hits": [], "overlap": 1.0}

    claim_stripped = (claim or "").strip()
    if not claim_stripped:
        out["reason"] = "empty_claim"
        return out

    if denylist:
        hits = denylist_hits(claim_stripped, denylist)

        # Phase 2.4d — hard ground truth wins over everything. Any hit on a
        # Twitch-event phrase when the event count for the window is zero
        # short-circuits as a fail regardless of token overlap.
        contradicted = []
        for h in hits:
            event_key = _event_contradicts(h, hard_events, event_map)
            if event_key:
                h_copy = dict(h)
                h_copy["event_key"] = event_key
                contradicted.append(h_copy)
        if contradicted:
            out["passed"] = False
            out["reason"] = "event_contradicts_ground_truth"
            out["denylist_hits"] = contradicted
            return out

        # A hit is "supported" when every content token in the matched phrase
        # also appears somewhere in the references. We DON'T require the exact
        # adjacent phrasing — "someone gifted 20 subs" in the transcript is
        # enough support for the claim "gifted subs" in the title. This is a
        # deliberate false-positive tradeoff for Tier 1; the LLM judge does
        # real semantic verification on borderline cases.
        ref_content = _content_tokens(ref_text)
        unsupported = []
        for h in hits:
            match_tokens = _content_tokens(h["match"])
            if not match_tokens or not match_tokens.issubset(ref_content):
                unsupported.append(h)
        if unsupported:
            out["passed"] = False
            out["reason"] = "denylist_unsupported"
            out["denylist_hits"] = unsupported
            return out

    overlap = content_overlap_ratio(claim_stripped, ref_text)
    out["overlap"] = round(overlap, 3)
    if overlap < min_overlap:
        out["passed"] = False
        out["reason"] = "low_overlap"
    return out


# ---------------------------------------------------------------------------
# Tier 2 — LLM-as-judge (main pipeline model via LM Studio)
# ---------------------------------------------------------------------------


def _import_lmstudio():
    """Import the sibling lmstudio helper. Tries package-style first, then
    falls back to adding this file's directory to sys.path."""
    try:
        from lmstudio import chat as _chat  # type: ignore
        return _chat
    except ImportError:
        pass
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in sys.path:
            sys.path.insert(0, here)
        from lmstudio import chat as _chat  # type: ignore
        return _chat
    except Exception as e:
        print(f"[GROUND] judge lmstudio helper unavailable: {e}", file=sys.stderr)
        return None


# Model resolution order (caller can short-circuit any step):
#   1. ``cfg["model"]`` from config/grounding.json::judge
#   2. env var ``CLIP_GROUNDING_JUDGE_MODEL`` (operator override per run)
#   3. env var ``CLIP_TEXT_MODEL`` (the pipeline's currently-loaded text
#      model — set by clip-pipeline.sh from config/models.json)
#   4. fallback ``"qwen/qwen3.5-9b"`` (a model-neutral safe default — does
#      NOT assume any specific model family is loaded)


_LLM_JUDGE_PROMPT = """/no_think
You are a strict, multi-dimensional faithfulness judge for a livestream-clipping pipeline. Score the CLAIM against the supplied evidence on FIVE independent dimensions, each 0-10.

CLAIM:
{claim}

TRANSCRIPT_WINDOW:
{transcript_window}

OPTIONAL_SETUP (earlier transcript line, if this is a callback):
{optional_setup}

OPTIONAL_SPEAKER_INFO:
{optional_speaker_info}

Score on each dimension 0-10 (0 = no evidence / wrong, 10 = strongly supported / clear). Be conservative — a 5 means "neutral, partially supported".

1. grounding (0-10): How well the literal facts in the claim are supported by the transcript_window. A claim that mentions "gifted subs" with no evidence in the window scores 0; a claim that paraphrases what the streamer actually said scores 8-10.
2. setup_payoff (0-10): Presence of narrative arc structure (build then beat). Pure reactive moments without setup score low; clear setup-then-resolution scores high.
3. speaker (0-10): Multi-speaker / off-screen voice / interruption value. If optional_speaker_info shows 1 speaker, score 0-3. If it shows 2+ speakers AND the claim implies dialogue / interruption, score 7-10.
4. conceptual (0-10): Is the moment IRONIC, CONTRADICTORY, or SURPRISING (high), versus just verbally funny / loud (low)?
5. callback (0-10): If OPTIONAL_SETUP is provided, how strong is the connection? 0 if no setup or unrelated; 8-10 if the payoff clearly references the setup.

Respond with ONLY a single JSON object:
{{"grounding": <int>, "setup_payoff": <int>, "speaker": <int>, "conceptual": <int>, "callback": <int>, "rationale": "one short sentence"}}"""


def _resolve_judge_model(cfg_model: Optional[str]) -> str:
    """Pick the LLM-judge model. Order: explicit cfg → env override →
    pipeline's currently-loaded text model → safe default. The default is
    model-neutral (does NOT assume any specific model family is loaded)."""
    return (
        (cfg_model or "").strip()
        or (os.environ.get("CLIP_GROUNDING_JUDGE_MODEL") or "").strip()
        or (os.environ.get("CLIP_TEXT_MODEL") or "").strip()
        or "qwen/qwen3.5-9b"
    )


def llm_judge(
    claim: str,
    transcript_window: str,
    optional_setup: str = "",
    optional_speaker_info: str = "",
    lm_studio_model: Optional[str] = None,
    url: Optional[str] = None,
    timeout: float = 30.0,
) -> Optional[Dict]:
    """Single LLM call returning 5-dimensional scores.

    Model-agnostic: the prompt is plain English and works with any
    instruction-tuned chat model LM Studio can serve (Qwen, Gemma, Llama,
    Mistral, etc.). The model name resolves via ``_resolve_judge_model()``
    when ``lm_studio_model`` isn't passed explicitly.

    Returns dict ``{grounding, setup_payoff, speaker, conceptual, callback,
    rationale}`` or None on any failure (network, malformed JSON, missing
    LM Studio).
    """
    if not (claim or "").strip() or not (transcript_window or "").strip():
        return None
    chat = _import_lmstudio()
    if chat is None:
        return None
    prompt = _LLM_JUDGE_PROMPT.format(
        claim=claim[:400],
        transcript_window=transcript_window[:3000],
        optional_setup=optional_setup[:600] or "(none)",
        optional_speaker_info=optional_speaker_info[:200] or "(none)",
    )
    text = chat(
        prompt,
        model=_resolve_judge_model(lm_studio_model),
        url=url or os.environ.get("CLIP_LLM_URL", "http://host.docker.internal:1234"),
        timeout=timeout,
        response_json=True,
        max_tokens=400,
    )
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    out: Dict[str, float] = {}
    for k in ("grounding", "setup_payoff", "speaker", "conceptual", "callback"):
        try:
            out[k] = max(0.0, min(10.0, float(obj.get(k, 0))))
        except (TypeError, ValueError):
            out[k] = 0.0
    out["rationale"] = str(obj.get("rationale") or "")[:200]
    return out


# Default per-dimension weights and pass threshold. Tunable via
# ``config/grounding.json::judge``. Sum should be 1.0 for the weighted-mean
# to land in [0, 10]; pass_threshold is on that 0-10 scale.
_JUDGE_DEFAULT_WEIGHTS = {
    "grounding": 0.55,    # most important — claim must be supported
    "setup_payoff": 0.15,
    "speaker": 0.05,      # bonus, not gating
    "conceptual": 0.15,
    "callback": 0.10,     # only contributes when callback context supplied
}
_JUDGE_DEFAULT_PASS_THRESHOLD = 5.0   # weighted mean >= 5/10 -> pass


def _judge_weighted_score(
    dimensions: Dict, weights: Optional[Dict[str, float]] = None
) -> float:
    """Reduce a 5-dimensional llm_judge result to a single 0-10 score
    via weighted mean.  Missing dimensions count as 0."""
    w = dict(_JUDGE_DEFAULT_WEIGHTS)
    if weights:
        w.update({k: float(v) for k, v in weights.items() if k in w})
    total_w = sum(w.values()) or 1.0
    return sum(float(dimensions.get(k, 0.0)) * w[k] for k in w) / total_w


# ---------------------------------------------------------------------------
# Cascade
# ---------------------------------------------------------------------------

_DEFAULT_GROUNDING_CONFIG_PATH = Path(
    os.environ.get("CLIP_GROUNDING_CONFIG", "/root/.openclaw/grounding.json")
)
_GROUNDING_CONFIG_CACHE: Optional[dict] = None


def load_grounding_config(path: Optional[str] = None) -> dict:
    """Load ``config/grounding.json``. Cached per-process when no override
    path is passed. Missing / unparseable file → empty dict (cascade
    collapses to Tier 1 + module defaults)."""
    global _GROUNDING_CONFIG_CACHE
    if path is None and _GROUNDING_CONFIG_CACHE is not None:
        return _GROUNDING_CONFIG_CACHE
    p = Path(path) if path else _DEFAULT_GROUNDING_CONFIG_PATH
    cfg: dict = {}
    if p.exists():
        try:
            cfg = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cfg = {}
    if path is None:
        _GROUNDING_CONFIG_CACHE = cfg
    return cfg


def cascade_check(
    claim: str,
    references: Iterable[str],
    denylist: Optional[Dict[str, List[re.Pattern]]] = None,
    config: Optional[dict] = None,
    min_overlap: float = 0.15,
    hard_events: Optional[Dict[str, int]] = None,
    event_map: Optional[Dict[str, Dict[str, str]]] = None,
    optional_setup: str = "",
    optional_speaker_info: str = "",
) -> Dict:
    """Run the 2-tier cascade. Returns the same shape as ``check_claim``
    plus a ``tier`` field (``1`` or ``"judge"``) and an ``escalations``
    list with each tier's intermediate result for diagnostics.

    Cascade logic:
      - Tier 1 always runs (cheap).
      - Stop early on a clear pass (overlap >= clear_pass_overlap, no
        denylist hits) or a hard fail (denylist unsupported, or Phase 2.4d
        event-contradicts-ground-truth).
      - Otherwise run the LLM judge if enabled.
      - If the judge is disabled or returns None (LM Studio unreachable,
        malformed JSON, etc.), fall back to the Tier 1 verdict — Tier 1's
        denylist + hard-event check is the safety net.
    """
    cfg = config if config is not None else load_grounding_config()
    t1_cfg = cfg.get("tier_1", {}) or {}
    judge_cfg = cfg.get("judge") or {}

    ref_list = [r for r in (references or []) if r]
    ref_text = "\n".join(ref_list)

    # --- Tier 1 ---
    t1 = check_claim(
        claim, ref_list, denylist, min_overlap,
        hard_events=hard_events, event_map=event_map,
    )
    escalations: List[Dict] = [{"tier": 1, **t1}]

    clear_pass = float(t1_cfg.get("clear_pass_overlap", 0.55))
    t1_clear_pass = t1["passed"] and t1.get("overlap", 0.0) >= clear_pass
    t1_hard_fail = (not t1["passed"]) and t1.get("reason") in (
        "denylist_unsupported",
        "event_contradicts_ground_truth",
    )

    judge_enabled = bool(judge_cfg.get("enabled", True))
    if t1_clear_pass or t1_hard_fail or not judge_enabled:
        return {**t1, "tier": 1, "escalations": escalations}

    # --- Tier 2 — LLM-as-judge ---
    gj = llm_judge(
        claim, ref_text, optional_setup, optional_speaker_info,
        lm_studio_model=judge_cfg.get("model"),
        url=os.environ.get("CLIP_LLM_URL"),
        timeout=float(judge_cfg.get("timeout_s", 30)),
    )
    if gj is None:
        # Judge unavailable — fall back to Tier 1 verdict. Tier 1's hard
        # checks (denylist + zero-count event) still ran, so we never lose
        # the most dangerous-class hallucinations.
        escalations.append({"tier": "judge", "passed": None, "reason": "judge_unavailable"})
        return {**t1, "tier": 1, "escalations": escalations}

    weighted = _judge_weighted_score(gj, judge_cfg.get("weights"))
    pass_threshold = float(
        judge_cfg.get("pass_threshold", _JUDGE_DEFAULT_PASS_THRESHOLD)
    )
    gj_passed = weighted >= pass_threshold
    dims = {k: gj[k] for k in ("grounding", "setup_payoff", "speaker", "conceptual", "callback")}
    escalations.append({
        "tier": "judge",
        "passed": gj_passed,
        "weighted": round(weighted, 2),
        "dims": dims,
        "rationale": gj["rationale"],
    })
    return {
        "passed": gj_passed,
        "reason": "judge_pass" if gj_passed else "judge_low_weighted",
        "overlap": t1.get("overlap"),
        "denylist_hits": t1.get("denylist_hits", []),
        "judge_weighted": round(weighted, 2),
        "judge_dims": dims,
        "judge_rationale": gj["rationale"],
        "tier": "judge",
        "escalations": escalations,
    }


def _cli() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="Grounding cascade check")
    ap.add_argument("--claim", required=True, help="Generated text to check")
    ap.add_argument(
        "--ref",
        action="append",
        default=[],
        help="Reference text (repeatable — transcript/chat/OCR)",
    )
    ap.add_argument("--denylist", default=str(DEFAULT_DENYLIST_PATH))
    ap.add_argument("--config", default=str(_DEFAULT_GROUNDING_CONFIG_PATH))
    ap.add_argument("--min-overlap", type=float, default=0.15)
    ap.add_argument(
        "--tier-1-only",
        action="store_true",
        help="Skip the judge even if enabled in config (useful for offline tests)",
    )
    args = ap.parse_args()

    dl = load_denylist(args.denylist)
    if args.tier_1_only:
        result = check_claim(args.claim, args.ref, dl, args.min_overlap)
        result["tier"] = 1
    else:
        cfg = load_grounding_config(args.config)
        result = cascade_check(args.claim, args.ref, dl, cfg, args.min_overlap)

    json.dump(result, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")
    sys.exit(0 if result["passed"] else 1)


if __name__ == "__main__":
    _cli()
