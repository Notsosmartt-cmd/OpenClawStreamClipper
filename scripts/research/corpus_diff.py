#!/usr/bin/env python3
"""corpus_diff.py — Phase R3 of concepts/plan-reference-deconstruction-2026-07.

The tedium killer: diff the REFERENCE attribute cards (what proven competitor
clips do) against OUR produced clips' cards (what our pipeline does), per
category, and emit a gap report the owner can read in minutes — every gap item
carrying a `lever:` naming the existing config/prompt knob it maps to (or
`feature-card` when no lever exists yet).

Numbers are computed deterministically from the cards (OUR side prefers the
`_ground_truth` effects-log data where present — we KNOW what we injected).
One optional LLM pass writes the narrative; if LM Studio is down the report
still emits with tables + deterministic gap items only.

Outputs (into clips/.diagnostics/):
  corpus_diff_<date>.md    — the owner-readable report
  corpus_diff_<date>.json  — machine-readable items[] (feeds the R6 tab's
                             approve/reject flow later)

Usage:
  python scripts/research/corpus_diff.py --run 20260710_143929 [--no-llm]
"""
from __future__ import annotations

import argparse
import json
import statistics as st
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve()
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(REPO / "scripts" / "lib"))

import clip_forensics as cf  # noqa: E402  (_llm_config)

REF_CACHE = REPO / "reference_clips" / ".cache"
DIAG = REPO / "clips" / ".diagnostics"


def _log(m: str) -> None:
    print(f"[corpus_diff] {m}", file=sys.stderr, flush=True)


def _load_cards(folder: Path) -> list[dict]:
    out = []
    for f in sorted(folder.glob("*.card.json")):
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            continue
    return out


# ---------------------------------------------------------------------------
# Deterministic aggregation
# ---------------------------------------------------------------------------
def _num(v):
    return v if isinstance(v, (int, float)) else None


def _sfx_per_30s(card: dict) -> float | None:
    """OUR cards: exact injected cue count from _ground_truth when present
    (CLAP-derived counts over-fire on speech exclamations); else the card fact."""
    gt = card.get("_ground_truth") or {}
    dur = _num((gt or {}).get("clip_duration")) or _num((card.get("_facts") or {}).get("duration_s"))
    if gt.get("sfx_cues") is not None and dur:
        return round(len(gt["sfx_cues"]) / dur * 30.0, 2)
    return _num((card.get("sfx_grammar") or {}).get("count_per_30s"))


def _agg(cards: list[dict], ours: bool) -> dict:
    """Aggregate one card set into the comparable stat block."""
    def med(vals):
        vals = [v for v in vals if v is not None]
        return round(st.median(vals), 2) if vals else None

    hooks = [(c.get("hook") or {}).get("text_hook_style") or "none" for c in cards]
    casings = Counter((c.get("captions") or {}).get("casing") or "?" for c in cards)
    arcs = Counter((c.get("arc") or {}).get("shape") or "?" for c in cards)
    vvv = Counter((c.get("comedy") or {}).get("verbal_vs_visual") or "?" for c in cards)
    align = Counter((c.get("edit_grammar") or {}).get("cut_alignment") or "?" for c in cards)
    return {
        "n": len(cards),
        "cuts_per_30s_med": med([_num((c.get("edit_grammar") or {}).get("cuts_per_30s")) for c in cards]),
        "sfx_per_30s_med": med([(_sfx_per_30s(c) if ours else
                                 _num((c.get("sfx_grammar") or {}).get("count_per_30s"))) for c in cards]),
        "sfx_offset_ms_med": med([_num((c.get("sfx_grammar") or {}).get("offset_from_payoff_ms")) for c in cards]),
        "zooms_med": med([_num((c.get("edit_grammar") or {}).get("zooms")) for c in cards]),
        "caption_wps_med": med([_num((c.get("captions") or {}).get("density_wps")) for c in cards]),
        "pct_text_hook": round(100 * sum(1 for h in hooks if h and h.lower() != "none") / len(cards)) if cards else None,
        "cut_alignment_top": align.most_common(2),
        "caption_casing_top": casings.most_common(2),
        "arc_shapes": arcs.most_common(3),
        "verbal_vs_visual": vvv.most_common(3),
        # schema v2: only EDITOR-ADDED overlays count (v1's chat_overlay conflated the
        # stream's own on-screen chat with an added overlay — owner-rejected artifact).
        # v1 cards lack the field → None (excluded), never silently mixed.
        "chat_overlay_pct": round(100 * sum(
            1 for c in cards if (c.get("engagement") or {}).get("added_chat_overlay")) / len(cards))
            if cards and any("added_chat_overlay" in (c.get("engagement") or {}) for c in cards) else None,
    }


# metric -> (lever, note). `feature-card` = no existing knob; becomes a wiki card.
_LEVERS = {
    "sfx_per_30s_med": ("config/sfx_cues.json", "beat density: category_beats / max_cues / add multi-hit beats (roast-cadence)"),
    "sfx_offset_ms_med": ("config/sfx_cues.json", "payoff_delay_s per beat"),
    "cuts_per_30s_med": ("CLIP_JUMP_CUTS + scripts/lib/clip_cuts.py", "jump-cut compression density (gaps/llm modes) + transitions"),
    "zooms_med": ("scripts/lib/style_profiles.py", "zoom_punch_count per category profile"),
    "pct_text_hook": ("config/hook_templates.json + CLIP_HOOK_CAPTION", "hook-card presence/phrasing (voice contract already governs style)"),
    "caption_casing_top": ("config/caption_style.json (P1.5 voice bank)", "curate + enable; the voice contract bans Title Case already"),
    "caption_wps_med": ("scripts/lib/kinetic_captions.py preset", "caption pacing/word-grouping"),
    "chat_overlay_pct": ("style_profiles chat_overlay flag (render feature, needs a chat dump)",
                         "EDITOR-ADDED overlays only (schema v2); the stream's own on-screen chat doesn't count"),
    "cut_alignment_top": ("clip_cuts seed/beats", "align cuts to punchline beats vs loose"),
    "category_coverage": ("feature-card", "formats the corpus has that we never produce (e.g. news_compilation -> plan-news-compilation-2026-07)"),
}


def _gap_items(ref_by_cat: dict, ours_by_cat: dict, ref_all: dict, ours_all: dict) -> list[dict]:
    """Deterministic gap items. Per-category where the reference has >=2 cards in
    OUR category; global (ALL-reference vs ALL-ours) rows always included."""
    items: list[dict] = []

    def _cmp(scope: str, ref: dict, ours: dict):
        for metric in ("sfx_per_30s_med", "cuts_per_30s_med", "zooms_med",
                       "pct_text_hook", "caption_wps_med", "chat_overlay_pct",
                       "sfx_offset_ms_med"):
            r, o = ref.get(metric), ours.get(metric)
            if r is None or o is None:
                continue
            gap = round(o - r, 2)
            rel = abs(gap) / abs(r) if r else (1.0 if gap else 0.0)
            if rel < 0.25:      # within 25% of reference = not a gap worth a line
                continue
            lever, note = _LEVERS.get(metric, ("?", ""))
            items.append({"id": f"{scope}:{metric}", "scope": scope, "metric": metric,
                          "reference": r, "ours": o, "gap": gap,
                          "lever": lever, "note": note})
        # categorical: casing + alignment mismatch (top-1 differs)
        for metric in ("caption_casing_top", "cut_alignment_top"):
            rt = (ref.get(metric) or [("?", 0)])[0][0]
            ot = (ours.get(metric) or [("?", 0)])[0][0]
            if rt != "?" and ot != "?" and rt != ot:
                lever, note = _LEVERS.get(metric, ("?", ""))
                items.append({"id": f"{scope}:{metric}", "scope": scope, "metric": metric,
                              "reference": rt, "ours": ot, "gap": "categorical",
                              "lever": lever, "note": note})

    _cmp("ALL", ref_all, ours_all)
    for cat, ocards in ours_by_cat.items():
        rcards = ref_by_cat.get(cat) or []
        if len(rcards) >= 2 and len(ocards) >= 2:
            _cmp(cat, _agg(rcards, ours=False), _agg(ocards, ours=True))

    # coverage: reference categories we produce ZERO clips in
    missing = [c for c, cards in ref_by_cat.items()
               if len(cards) >= 3 and c not in ours_by_cat]
    for c in missing:
        items.append({"id": f"coverage:{c}", "scope": "coverage", "metric": "category_coverage",
                      "reference": f"{len(ref_by_cat[c])} reference clips", "ours": "0 produced",
                      "gap": "missing format", "lever": _LEVERS["category_coverage"][0],
                      "note": f"'{c}' — " + _LEVERS["category_coverage"][1]})
    return items


_NARRATIVE_PROMPT = """/no_think
You are an editorial analyst for a stream-clipping pipeline. Below: aggregate stats from PROVEN competitor reference clips vs the clips OUR pipeline produced, plus computed gap items. Write a short, plain-language gap narrative for the pipeline owner.

REFERENCE (by category): {ref}
OURS (by category): {ours}
GAP ITEMS: {items}

Write 5-9 sentences max, prioritized by likely viewer impact. Be concrete (cite the numbers). Do not invent metrics not shown. End with the single highest-leverage change.
"""


def _narrative(ref_by_cat, ours_by_cat, items) -> str | None:
    try:
        import lmstudio
    except Exception:
        return None
    model, url = cf._llm_config()
    ref_s = {k: _agg(v, ours=False) for k, v in ref_by_cat.items()}
    ours_s = {k: _agg(v, ours=True) for k, v in ours_by_cat.items()}
    prompt = _NARRATIVE_PROMPT.format(
        ref=json.dumps(ref_s, default=str)[:2500],
        ours=json.dumps(ours_s, default=str)[:1500],
        items=json.dumps(items, default=str)[:2500])
    return lmstudio.chat(prompt, model=model, url=url, timeout=90, max_tokens=600) or None


def _fmt_stats(title: str, s: dict) -> str:
    return (f"| {title} | {s['n']} | {s['cuts_per_30s_med']} | {s['sfx_per_30s_med']} | "
            f"{s['zooms_med']} | {s['pct_text_hook']}% | {s['caption_casing_top'][0][0] if s['caption_casing_top'] else '?'} | "
            f"{s['chat_overlay_pct']}% |")


def main() -> int:
    ap = argparse.ArgumentParser(description="Reference-vs-ours attribute diff report (R3)")
    ap.add_argument("--run", required=True, help="our-cards run stamp (clips/.diagnostics/cards/<run>/)")
    ap.add_argument("--no-llm", action="store_true")
    args = ap.parse_args()

    ref = _load_cards(REF_CACHE)
    ours = _load_cards(DIAG / "cards" / args.run)
    if not ref or not ours:
        _log(f"need both card sets (ref={len(ref)}, ours={len(ours)})")
        return 1

    ref_by_cat: dict[str, list] = defaultdict(list)
    for c in ref:
        ref_by_cat[c.get("category") or "?"].append(c)
    ours_by_cat: dict[str, list] = defaultdict(list)
    for c in ours:
        ours_by_cat[c.get("category") or "?"].append(c)

    ref_all, ours_all = _agg(ref, ours=False), _agg(ours, ours=True)
    items = _gap_items(ref_by_cat, ours_by_cat, ref_all, ours_all)
    narrative = None if args.no_llm else _narrative(ref_by_cat, ours_by_cat, items)

    date = time.strftime("%Y%m%d")
    md = [f"# Corpus diff — reference vs ours (run {args.run}, {time.strftime('%Y-%m-%d')})",
          "",
          f"Reference cards: **{len(ref)}** ({', '.join(f'{k}:{len(v)}' for k, v in sorted(ref_by_cat.items()))})",
          f"Our cards: **{len(ours)}** ({', '.join(f'{k}:{len(v)}' for k, v in sorted(ours_by_cat.items()))})",
          "",
          "| set | n | cuts/30s | sfx/30s | zooms | text-hook | casing | chat-ovl |",
          "|---|---|---|---|---|---|---|---|",
          _fmt_stats("REFERENCE (all)", ref_all),
          _fmt_stats("OURS (all)", ours_all)]
    for cat in sorted(set(ref_by_cat) & set(ours_by_cat)):
        if len(ref_by_cat[cat]) >= 2 and len(ours_by_cat[cat]) >= 2:
            md += [_fmt_stats(f"ref:{cat}", _agg(ref_by_cat[cat], ours=False)),
                   _fmt_stats(f"ours:{cat}", _agg(ours_by_cat[cat], ours=True))]
    md += ["", "## Gap items (approve → the agent applies the lever; reject → drop)", ""]
    if not items:
        md.append("_No gaps above the 25% threshold — we match the reference corpus on every measured axis._")
    for it in items:
        md.append(f"- [ ] **{it['id']}** — reference `{it['reference']}` vs ours `{it['ours']}` "
                  f"(gap {it['gap']}) → **lever:** `{it['lever']}` — {it['note']}")
    if narrative:
        md += ["", "## Narrative", "", narrative.strip()]
    md += ["", "---", f"_Method: deterministic aggregates from attribute cards; OUR sfx counts use "
                      f"effects_log ground truth where present. Gap threshold 25% relative. "
                      f"Cards: reference_clips/.cache + clips/.diagnostics/cards/{args.run}._"]

    out_md = DIAG / f"corpus_diff_{date}.md"
    out_js = DIAG / f"corpus_diff_{date}.json"
    out_md.write_text("\n".join(md), encoding="utf-8")
    out_js.write_text(json.dumps({"run": args.run, "date": date, "items": items},
                                 indent=2), encoding="utf-8")
    _log(f"wrote {out_md.name} ({len(items)} gap items) + {out_js.name}")
    print("\n".join(md))
    return 0


if __name__ == "__main__":
    sys.exit(main())
