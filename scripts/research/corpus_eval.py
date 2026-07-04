#!/usr/bin/env python3
"""Phase 7.1 — corpus-wide detector reliability from human-corrected annotations.

This is the tool that answers "can clip_forensics RELIABLY detect the audio/visual
effects and cues?" with a number instead of a vibe. It aggregates per-clip
precision/recall (see clip_forensics._score_against_notes) across every CORRECTED
`<clip>.notes.json` in reference_clips/ and reports it per detector family
(sfx / music / censor / cut / cold_open).

The loop it closes:
  1. `clip_forensics --clip X --ocr --draft-notes`  -> writes X.notes.json DRAFT
     (or `corpus_eval --draft-from-cache` to batch-draft everything already decomposed)
  2. Owner CORRECTS each draft (delete false positives, add missed cues, drop `_draft`)
  3. `corpus_eval`  -> corpus precision/recall per detector; low precision => the CLAP
     threshold for that label is too loose (feeds E1 threshold calibration); low
     recall => the detector misses a real cue class.

Prefers cached `.cache/<stem>.timeline.json`; a clip with notes but no cached
timeline is reported as "needs decompose" rather than silently skipped (decomposing
here would be slow + GPU-touching; keep this tool cheap and offline)."""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve()
REPO = HERE.parents[2]
REF_DIR = REPO / "reference_clips"
CACHE = REF_DIR / ".cache"
sys.path.insert(0, str(HERE.parent))
import clip_forensics as cf  # noqa: E402  (_score_against_notes, _draft_notes, _family)


def _load(p: Path) -> dict | None:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _timeline_for(stem: str) -> dict | None:
    return _load(CACHE / f"{stem}.timeline.json")


def draft_from_cache() -> int:
    """Write a draft .notes.json for every cached timeline that has no notes yet —
    cheap (no decompose), gives the owner a batch to correct."""
    made = 0
    for tj in sorted(CACHE.glob("*.timeline.json")):
        stem = tj.name[: -len(".timeline.json")]
        # find the media file (any extension) to place the sidecar next to it
        media = next((m for m in REF_DIR.glob(f"{stem}.*")
                      if m.suffix.lower() in (".mp4", ".mov", ".mkv", ".webm")), None)
        dst = (media.with_suffix(".notes.json") if media
               else REF_DIR / f"{stem}.notes.json")
        if dst.exists():
            existing = _load(dst) or {}
            if not existing.get("_draft"):
                continue  # never clobber a corrected file
        tl = _load(tj)
        if not tl:
            continue
        draft = cf._draft_notes(tl)
        dst.write_text(json.dumps(draft, indent=2), encoding="utf-8")
        made += 1
        print(f"  draft -> {dst.name} ({len(draft['events'])} proposed events)")
    print(f"[corpus_eval] wrote {made} draft(s). Correct them + delete each _draft key, "
          f"then re-run `corpus_eval` for the real score.")
    return 0


def evaluate() -> int:
    notes_files = sorted(REF_DIR.glob("*.notes.json"))
    if not notes_files:
        print("[corpus_eval] no .notes.json files. Decompose clips with --ocr, then "
              "`corpus_eval --draft-from-cache` to seed drafts to correct.")
        return 0

    corrected, drafts, missing_tl, template = [], [], [], []
    agg: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    per_clip = []
    for nf in notes_files:
        notes = _load(nf)
        if not notes:
            continue
        stem = nf.name[: -len(".notes.json")]
        if not any((REF_DIR / f"{stem}.{ext}").exists()
                   for ext in ("mp4", "MP4", "mov", "MOV", "mkv", "webm")):
            template.append(nf.name)          # e.g. example.notes.json (no media)
            continue
        if notes.get("_draft"):
            drafts.append(nf.name)
            continue
        tl = _timeline_for(stem)
        if not tl:
            missing_tl.append(nf.name)
            continue
        ev = cf._score_against_notes(tl, notes)
        corrected.append(nf.name)
        per_clip.append((nf.name, ev))
        for fam, v in ev["by_family"].items():
            agg[fam]["annotated"] += v["annotated"]
            agg[fam]["recalled"] += v["recalled"]
            agg[fam]["detected"] += v["detected"]
            agg[fam]["matched_detected"] += v["matched_detected"]

    print(f"[corpus_eval] corrected={len(corrected)} draft(pending)={len(drafts)} "
          f"needs-decompose={len(missing_tl)} template={len(template)}")
    if drafts:
        print(f"  drafts awaiting correction: {', '.join(drafts[:6])}"
              + (" ..." if len(drafts) > 6 else ""))
    if missing_tl:
        print(f"  have notes but no cached timeline (decompose them): {', '.join(missing_tl[:6])}"
              + (" ..." if len(missing_tl) > 6 else ""))
    if not corrected:
        print("\n  No CORRECTED annotations yet -> no reliability number to report.\n"
              "  Correct the drafts (delete wrong lines, add missed cues, drop _draft) and re-run.")
        return 0

    print(f"\n  Corpus detector reliability across {len(corrected)} corrected clip(s):")
    print(f"  {'family':<10} {'precision':>10} {'recall':>8}   {'detected':>8} {'annotated':>9}")
    out = {}
    for fam in sorted(agg):
        a = agg[fam]
        prec = round(a["matched_detected"] / a["detected"], 3) if (a["detected"] and a["annotated"]) else None
        rec = round(a["recalled"] / a["annotated"], 3) if a["annotated"] else None
        out[fam] = {"precision": prec, "recall": rec, **a}
        print(f"  {fam:<10} {str(prec):>10} {str(rec):>8}   {a['detected']:>8} {a['annotated']:>9}")

    summary = {"corrected_clips": corrected, "by_family": out,
               "pending_drafts": drafts, "needs_decompose": missing_tl}
    (CACHE).mkdir(exist_ok=True)
    (CACHE / "corpus_eval.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\n  wrote {CACHE / 'corpus_eval.json'}")
    print("  Low precision on a family => that detector's threshold is too loose (feeds E1).")
    print("  Low recall on a family => it misses that cue class (cold_open has no detector by design).")
    return 0


def main() -> int:
    if "--draft-from-cache" in sys.argv:
        return draft_from_cache()
    return evaluate()


if __name__ == "__main__":
    sys.exit(main())
