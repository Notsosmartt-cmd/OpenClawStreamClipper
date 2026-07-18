#!/usr/bin/env python3
"""s45_text_judge.py — J3 of plan-s45-text-judge-2026-07.

The S4.5 batched TEXT judge: the vision-phase model (35B) judges Pass-B
candidates COMPARATIVELY in groups, from evidence packets, at the phase
boundary that already exists — so top-model judgment costs no extra swaps.

Laws obeyed:
- BUG-73: batch-in-PROMPT, one request in flight at a time (a group of 8
  packets ≈ 7-8k prompt ≪ the 32k pool).
- BUG-74: the model id is an explicit ARGUMENT — no env fallback resolution.
- Failure-soft: a failed group keeps its candidates UNJUDGED (never lose
  moments to an outage); cull floor: at most half killed, never below
  min(8, n) survivors.

The `chat_fn` parameter exists for the selftest (inject a mock instead of
LM Studio).
"""
from __future__ import annotations

import json

GROUP_SIZE = 8
CULL_MAX_FRACTION = 0.5
CULL_MIN_SURVIVORS = 8

_PROMPT = """/no_think
You are the senior clip judge for a short-form (TikTok/Shorts) stream-clipping pipeline. Below are {n} candidate moments from ONE stream VOD, each as an evidence packet: the proposer's claim plus the VERBATIM transcript window (with speaker turns) and audio marks.

Judge each candidate ONLY from its transcript evidence — never trust the proposer's summary. Judge COMPARATIVELY within this group: reserve 8-10 for moments that would stop a scroll, and kill only candidates that are clearly not clip-worthy (no beat, no payoff, pure filler). Borderline-but-plausible stays alive with a middling score. Some packets carry SPECIES NORMS — treat them as typical shape expectations for that kind of moment, never as requirements: strong evidence beats the norm, but a claim that badly mismatches its species' shape deserves scrutiny.

{packets}

Respond with ONLY a JSON object (no prose, no fences):
{{"verdicts": [{{"idx": <candidate number>, "keep": true|false, "score": <0-10>, "subtype": "banter_roast|prank_public|freakout_overreaction|performance_rap|wholesome|solo_monologue|other|unchanged", "rationale": "<= 15 words, cite the evidence"}}, ...]}}
Every candidate number 1..{n} must appear exactly once."""


def _default_chat(prompt: str, model: str, url: str):
    import lmstudio
    return lmstudio.chat(prompt, model=model, url=url, timeout=240,
                         max_tokens=220 * GROUP_SIZE)


def _parse_verdicts(raw: str, lo_idx: int, hi_idx: int, log=None) -> dict[int, dict]:
    """Verdict rows keyed by ABSOLUTE candidate idx; invalid rows dropped.

    BUG 76 (2026-07-18): a verdict carrying keep+rationale but NO `score` key
    used to become a SILENT `score=0.0` (`float(r.get("score", 0))`). That
    fabricates a contradictory row — "keep": true with a glowing rationale and a
    0.0 score — which then (a) overwrites the moment's ranking score with 0.0
    (`m["score"] = d["score"]/10`), (b) makes `CLIP_MIN_JUDGE_SCORE` DROP a clip
    the judge explicitly KEPT, and (c) sinks it to the bottom of the poster's ★
    filter. Observed live: 7/15 candidates in one group. A score-less verdict is
    now treated as UNJUDGED (fail-open — the same path a failed group takes) and
    logged, never silently zeroed."""
    try:
        import lmstudio
        data = lmstudio.loads_lenient(raw)
    except Exception:
        try:
            data = json.loads(raw)
        except Exception:
            return {}
    rows = (data or {}).get("verdicts") if isinstance(data, dict) else None
    out: dict[int, dict] = {}
    _no_score = 0
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        try:
            idx = int(r.get("idx"))
        except (TypeError, ValueError):
            continue
        if not (lo_idx <= idx <= hi_idx):
            continue
        # BUG 76: absent/None/garbage score -> leave UNJUDGED, never fabricate 0.0
        if r.get("score") is None:
            _no_score += 1
            continue
        try:
            score = max(0.0, min(10.0, float(r.get("score"))))
        except (TypeError, ValueError):
            _no_score += 1
            continue
        out[idx] = {
            "keep": bool(r.get("keep", True)),
            "score": round(score, 1),
            "subtype": str(r.get("subtype") or "unchanged").strip().lower(),
            "rationale": str(r.get("rationale") or "")[:160],
        }
    if _no_score and log:
        log(f"[s45] group {lo_idx}-{hi_idx}: {_no_score} verdict(s) had NO usable "
            f"score — left UNJUDGED (fail-open) rather than a fabricated 0.0 (BUG 76)")
    return out


def _apply_cull_floor(moments: list[dict], decisions: dict[int, dict],
                      log=print) -> set[int]:
    """Kill set (1-based idxs) after the floor: at most CULL_MAX_FRACTION
    killed, never below min(CULL_MIN_SURVIVORS, n) survivors. Rescues are
    picked by judge score (best first) and marked in the decision row."""
    n = len(moments)
    kills = {i for i, d in decisions.items() if not d["keep"]}
    min_survivors = min(CULL_MIN_SURVIVORS, n)
    max_kills = min(int(n * CULL_MAX_FRACTION), n - min_survivors)
    if len(kills) <= max_kills:
        return kills
    ranked = sorted(kills, key=lambda i: -decisions[i]["score"])
    rescued = ranked[: len(kills) - max_kills]
    for i in rescued:
        decisions[i]["keep"] = True
        decisions[i]["rescued_by_floor"] = True
    log(f"[s45] cull floor: judge killed {len(kills)}/{n}; "
        f"rescued {len(rescued)} (max kills {max_kills})")
    return set(ranked[len(rescued):])


def judge_moments(moments: list[dict], packets: list[str], *, model: str,
                  url: str, chat_fn=None, group_size: int = GROUP_SIZE,
                  log=print) -> dict:
    """Judge all candidates; returns {survivors, killed, decisions, groups_failed}.

    survivors: the input moments (order preserved) minus kills, each kept
    moment annotated: score_passb (original), score (judge/10), s45_judge
    {score, rationale, subtype, rescued?}. Unjudged (failed-group) moments
    survive untouched.
    """
    chat = chat_fn or _default_chat
    decisions: dict[int, dict] = {}
    groups_failed = 0
    for g0 in range(0, len(moments), group_size):
        lo, hi = g0 + 1, min(g0 + group_size, len(moments))
        block = "\n\n---\n\n".join(packets[g0:hi])
        prompt = _PROMPT.format(n=len(moments), packets=block)
        try:
            raw = chat(prompt, model, url)
            got = _parse_verdicts(raw or "", lo, hi, log=log)
        except Exception as e:  # noqa: BLE001
            log(f"[s45] group {lo}-{hi} FAILED ({type(e).__name__}: {e}) — kept unjudged")
            got = {}
        if not got:
            groups_failed += 1
            log(f"[s45] group {lo}-{hi}: no verdicts parsed — kept unjudged")
            continue
        decisions.update(got)
        ks = sum(1 for d in got.values() if not d["keep"])
        log(f"[s45] group {lo}-{hi}: {len(got)} verdicts, {ks} kill(s)")

    kills = _apply_cull_floor(moments, decisions, log=log)
    survivors, killed = [], []
    for i, m in enumerate(moments, 1):
        d = decisions.get(i)
        if i in kills:
            killed.append({"idx": i, "timestamp": m.get("timestamp"),
                           "why": m.get("why"), "judge": d})
            continue
        if d is not None:
            m = dict(m)
            m["score_passb"] = m.get("score")
            m["score"] = round(d["score"] / 10.0, 3)
            if d["subtype"] not in ("unchanged", "", "none") and d["subtype"] != m.get("subtype"):
                m["subtype_passb"] = m.get("subtype")
                m["subtype"] = d["subtype"]
            m["s45_judge"] = d
        survivors.append(m)
    return {"survivors": survivors, "killed": killed,
            "decisions": {str(k): v for k, v in decisions.items()},
            "groups_failed": groups_failed}


def _selftest() -> int:
    moments = [{"timestamp": 100 + i, "score": 0.5, "why": f"m{i}",
                "subtype": "other"} for i in range(12)]
    packets = [f"CANDIDATE {i}\nclaim: x\nTRANSCRIPT (verbatim):\nyo" for i in range(1, 13)]

    def mock_ok(prompt, model, url):
        # verdicts for whichever candidates appear in this prompt
        idxs = [int(l.split()[1]) for l in prompt.splitlines() if l.startswith("CANDIDATE ")]
        return json.dumps({"verdicts": [
            {"idx": i, "keep": i % 3 != 0, "score": (i % 10) + 0.5,
             "subtype": "banter_roast" if i == 1 else "unchanged",
             "rationale": f"r{i}"} for i in idxs]})

    r = judge_moments(moments, packets, model="m", url="u", chat_fn=mock_ok, log=lambda *_: None)
    assert len(r["survivors"]) + len(r["killed"]) == 12
    assert all("s45_judge" in m for m in r["survivors"]), "annotation missing"
    assert r["survivors"][0]["subtype"] == "banter_roast", "subtype override missing"
    assert r["survivors"][0]["score_passb"] == 0.5 and 0 <= r["survivors"][0]["score"] <= 1

    # floor: judge tries to kill EVERYTHING → min(8, n) must survive
    def mock_killall(prompt, model, url):
        idxs = [int(l.split()[1]) for l in prompt.splitlines() if l.startswith("CANDIDATE ")]
        return json.dumps({"verdicts": [
            {"idx": i, "keep": False, "score": i / 2.0, "rationale": "kill"} for i in idxs]})
    r2 = judge_moments(moments, packets, model="m", url="u", chat_fn=mock_killall, log=lambda *_: None)
    assert len(r2["survivors"]) >= 8, f"floor broken: {len(r2['survivors'])}"
    assert any(d.get("rescued_by_floor") for d in
               (m.get("s45_judge") or {} for m in r2["survivors"])), "no rescue marks"

    # outage: chat raises → everything survives unjudged
    def mock_boom(prompt, model, url):
        raise RuntimeError("server down")
    r3 = judge_moments(moments, packets, model="m", url="u", chat_fn=mock_boom, log=lambda *_: None)
    assert len(r3["survivors"]) == 12 and r3["groups_failed"] == 2
    assert all("s45_judge" not in m for m in r3["survivors"])

    # garbage response → unjudged, not crashed
    r4 = judge_moments(moments, packets, model="m", url="u",
                       chat_fn=lambda *a: "not json at all", log=lambda *_: None)
    assert len(r4["survivors"]) == 12
    print("s45_text_judge selftest: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
