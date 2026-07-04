#!/usr/bin/env python3
"""Phase 7.3 — classify each reference clip as TRANSCRIPT-carried vs REACTION-carried.

Answers "does the clip's transcription have value on its own?" per clip, and — the
strategic payoff — labels which corpus clips are the ANOMALY-LANE class (the value is
in the audience reaction / visual, not the words), giving that lane a ground-truth
eval set instead of the 2 hand-analyzed cases.

Three signals per clip, then a combined label:
  * wps            — words/sec from the cached transcript (dead air vs dense talk)
  * keyword_score  — density of Pass-A highlight keywords in the transcript (0..1).
                     Uses the REAL Pass-A lexicon (KEYWORD_SETS), AST-extracted from
                     stage4_moments.py so this tool stays offline (no torch import).
  * reaction_score — fraction of audio events that are crowd/laughter reactions
                     (anomaly_propose.REACTION_LABELS) — the "words don't explain it"
                     tell.
  * llm_verdict    — LM Studio judges "would the TEXT ALONE justify a clip?"
                     (failure-soft: skipped if the server is down).

Reads cached `.cache/<stem>.words.json` + `.cache/<stem>.timeline.json` (produced by
clip_forensics). Writes per-clip `.cache/<stem>.value.json` and a corpus summary
`.cache/transcript_value.json` listing the reaction-carried clips."""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
REPO = HERE.parents[2]
CACHE = REPO / "reference_clips" / ".cache"
STAGE4 = REPO / "scripts" / "lib" / "stages" / "stage4_moments.py"
sys.path.insert(0, str(REPO / "scripts" / "lib"))
sys.path.insert(0, str(HERE.parent))

try:
    from anomaly_propose import REACTION_LABELS  # pure module, no heavy deps
except Exception:
    REACTION_LABELS = ("laughter", "cheering", "applause", "crowd", "bruh",
                       "scream", "gasp", "yell", "clap")

_KW_CACHE: dict | None = None


def load_keyword_sets() -> dict:
    """AST-extract the real Pass-A KEYWORD_SETS literal from stage4_moments.py WITHOUT
    importing the module (it pulls torch). Faithful to the pipeline's lexicon; falls
    back to a compact builtin set if the source layout ever changes."""
    global _KW_CACHE
    if _KW_CACHE is not None:
        return _KW_CACHE
    try:
        tree = ast.parse(STAGE4.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                    getattr(t, "id", None) == "KEYWORD_SETS" for t in node.targets):
                _KW_CACHE = ast.literal_eval(node.value)
                return _KW_CACHE
    except Exception as e:
        print(f"[transcript_value] KEYWORD_SETS extract failed ({type(e).__name__}); builtin fallback")
    _KW_CACHE = {"hype": ["insane", "clutch", "no way", "let's go", "crazy"],
                 "funny": ["bruh", "lol", "i'm dead", "bro", "nah"]}
    return _KW_CACHE


def keyword_score(text: str) -> tuple[float, list[str]]:
    """0..1 density of distinct Pass-A keywords in the transcript, plus the hits.
    Normalised so ~5 distinct highlight keywords saturates (short clips)."""
    t = " " + text.lower() + " "
    hits = []
    for cat, kws in load_keyword_sets().items():
        for kw in kws:
            if kw in t:
                hits.append(kw)
    distinct = sorted(set(hits))
    return min(1.0, len(distinct) / 5.0), distinct


def reaction_score(timeline: dict) -> float:
    """0..1 — fraction of audio events that are crowd/laughter reactions. High +
    low keyword_score = the anomaly-lane signature (reaction the words don't explain)."""
    evs = timeline.get("audio_events") or []
    if not evs:
        return 0.0
    rx = sum(1 for e in evs if any(r in str(e.get("label", "")).lower() for r in REACTION_LABELS))
    return round(rx / len(evs), 3)


_LLM_PROMPT = """You judge short-form clip value. Below is the full transcript of a clip
that performed well. Decide whether the TEXT ALONE would justify clipping it, or
whether the value is really in the audience REACTION / visual (the words alone are
unremarkable).

Transcript:
\"\"\"{text}\"\"\"

Output ONLY JSON: {{"carried": "transcript|reaction|mixed", "confidence": 0.0-1.0,
"reason": "one short sentence"}}"""


def llm_verdict(text: str, *, timeout: float = 60.0) -> dict | None:
    if not text.strip():
        return None
    try:
        import lmstudio
        import clip_forensics as cf
    except Exception:
        return None
    model, url = cf._llm_config()
    reply = lmstudio.chat(_LLM_PROMPT.format(text=text[:2500]), model=model, url=url,
                          timeout=timeout, max_tokens=200)
    return lmstudio.loads_lenient(reply) if reply else None


def classify(kw: float, rx: float, wps: float, verdict: dict | None) -> tuple[str, str]:
    """Combine the signals into a label + a one-line rationale. The LLM leads when
    present; the lexical/acoustic signals break ties and set confidence."""
    v = (verdict or {}).get("carried")
    if v in ("transcript", "reaction", "mixed"):
        base = {"transcript": "transcript-carried", "reaction": "reaction-carried",
                "mixed": "mixed"}[v]
        why = f"LLM: {(verdict or {}).get('reason', '')[:80]} (kw={kw} rx={rx} wps={wps})"
        return base, why
    # no LLM -> decide from signals
    if kw >= 0.5 and kw >= rx:
        return "transcript-carried", f"keyword-dense transcript (kw={kw}, rx={rx})"
    if rx >= 0.35 and rx > kw:
        return "reaction-carried", f"strong reaction, sparse keywords (rx={rx}, kw={kw})"
    return "mixed", f"no dominant signal (kw={kw}, rx={rx}, wps={wps})"


def _words_text(words) -> tuple[str, int, float]:
    if not isinstance(words, list) or not words:
        return "", 0, 0.0
    txt = " ".join(str(w.get("word", "")).strip() for w in words)
    span = max((float(w.get("end", 0)) for w in words), default=0.0) - \
        min((float(w.get("start", 0)) for w in words), default=0.0)
    wps = round(len(words) / span, 2) if span > 0 else 0.0
    return txt, len(words), wps


def evaluate_clip(stem: str, *, use_llm: bool = True) -> dict | None:
    wj, tj = CACHE / f"{stem}.words.json", CACHE / f"{stem}.timeline.json"
    if not wj.exists():
        return None
    try:
        words = json.loads(wj.read_text(encoding="utf-8"))
        timeline = json.loads(tj.read_text(encoding="utf-8")) if tj.exists() else {}
    except Exception:
        return None
    text, n, wps = _words_text(words)
    kw, hits = keyword_score(text)
    rx = reaction_score(timeline)
    verdict = llm_verdict(text) if use_llm else None
    label, why = classify(kw, rx, wps, verdict)
    return {"clip": stem, "n_words": n, "wps": wps, "keyword_score": kw,
            "keyword_hits": hits, "reaction_score": rx,
            "llm_verdict": verdict, "label": label, "why": why}


def main() -> int:
    use_llm = "--no-llm" not in sys.argv
    stems = sorted({p.name[: -len(".words.json")] for p in CACHE.glob("*.words.json")})
    if not stems:
        print("[transcript_value] no cached transcripts. Decompose clips first "
              "(clip_forensics --clip X).")
        return 0
    results = []
    for stem in stems:
        r = evaluate_clip(stem, use_llm=use_llm)
        if r:
            results.append(r)
            print(f"  {r['label']:<18} {stem[:40]:<40} kw={r['keyword_score']} "
                  f"rx={r['reaction_score']} wps={r['wps']}")
            (CACHE / f"{stem}.value.json").write_text(json.dumps(r, indent=2), encoding="utf-8")
    reaction_carried = [r["clip"] for r in results if r["label"] == "reaction-carried"]
    summary = {
        "n_clips": len(results),
        "transcript_carried": [r["clip"] for r in results if r["label"] == "transcript-carried"],
        "reaction_carried": reaction_carried,   # <-- the anomaly-lane ground-truth eval set
        "mixed": [r["clip"] for r in results if r["label"] == "mixed"],
        "clips": results,
    }
    (CACHE / "transcript_value.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\n[transcript_value] {len(results)} clip(s): "
          f"{len(summary['transcript_carried'])} transcript-carried, "
          f"{len(reaction_carried)} reaction-carried, {len(summary['mixed'])} mixed.")
    if reaction_carried:
        print(f"  anomaly-lane eval set (reaction-carried): {', '.join(c[:24] for c in reaction_carried)}")
    print(f"  wrote {CACHE / 'transcript_value.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
