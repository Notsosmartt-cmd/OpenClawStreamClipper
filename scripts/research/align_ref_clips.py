#!/usr/bin/env python3
"""Path C — align viewer-posted reference clips to local VODs → free positive labels.

The owner's insight (2026-07-05): community highlights in this niche are NOT Twitch
clips — viewers screen-record moments and post them on social media themselves. The
reference_clips/ corpus IS that community signal (each clip = a moment someone cared
enough to record, post, and that survived on the target platform). So aligning a
reference clip back to its source VOD position yields a **platform-validated positive
label** at that timestamp — better ground truth than any Twitch clip count.

Method: transcript shingle matching. Both sides already have word-level transcripts
(reference: `.cache/<stem>.words.json` from corpus_refresh; VODs:
`vods/.transcriptions/*.transcript.json`). Build 5-token shingles of the clip text,
histogram their hits across the VOD token stream (30 s buckets), take the peak. A
confident alignment needs >= --min-hits (default 6) shingles in one bucket — the first
real match scored 48, so the default has huge margin. Screen-recording quality doesn't
matter: whisper heard both sides.

Yield scales with COLLECTION HABIT, not code: only clips whose source VOD is on disk
can align (first corpus pass: 1/36 — the corpus was gathered for style across many
streamers). When saving viewer clips for streams you have VODs of, every one becomes a
free label.

Output: appends {"vod","timestamp","label":1,"clip","hits"} rows to
clips/.diagnostics/labels_social.jsonl (dedup by clip). fit_ranker maps vod→run via
the trace vod stamp (plan-learning-activation L1.1).

Proof-of-value (first run): 'Teacher Explains To Kill a Mockingbird' aligned to the
rakai VOD @ T=5258 (48 hits) — the pipeline had scored that moment 0.9375
cross-validated (final 1.3365, near-top of 257 candidates) and STILL dropped it in
bucket competition. A viewer-validated miss = exactly the class the fitted ranker
exists to rescue. See [[concepts/plan-learning-activation-2026-07]] §Case.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve()
REPO = HERE.parents[2]
REF_CACHE = REPO / "reference_clips" / ".cache"
VOD_TRANS = REPO / "vods" / ".transcriptions"
OUT = REPO / "clips" / ".diagnostics" / "labels_social.jsonl"


def norm_tokens(text: str) -> list[str]:
    return [w for w in re.sub(r"[^a-z0-9' ]", " ", text.lower()).split() if len(w) > 1]


def load_vods() -> dict[str, tuple[list[str], list[float]]]:
    vods = {}
    for vt in sorted(VOD_TRANS.glob("*.transcript.json")):
        try:
            segs = json.loads(vt.read_text(encoding="utf-8"))
        except Exception:
            continue
        toks, times = [], []
        for s in segs or []:
            for w in norm_tokens(s.get("text", "")):
                toks.append(w)
                times.append(float(s.get("start", 0)))
        vods[vt.name[: -len(".transcript.json")]] = (toks, times)
    return vods


def align_clip(ctoks: list[str], vods: dict, *, n: int = 5,
               bucket_s: float = 30.0) -> tuple[str, float, int] | None:
    """Best (vod, timestamp, hits) for one clip's token list, or None."""
    csh = {" ".join(ctoks[i:i + n]) for i in range(len(ctoks) - n + 1)}
    if not csh:
        return None
    best = None
    for vod, (vtoks, vtimes) in vods.items():
        hits: dict[int, int] = defaultdict(int)
        first_t: dict[int, float] = {}
        for i in range(len(vtoks) - n + 1):
            if " ".join(vtoks[i:i + n]) in csh:
                b = int(vtimes[i] // bucket_s)
                hits[b] += 1
                first_t.setdefault(b, vtimes[i])
        if hits:
            b, cnt = max(hits.items(), key=lambda kv: kv[1])
            if best is None or cnt > best[2]:
                best = (vod, first_t[b], cnt)
    return best


def main() -> int:
    ap = argparse.ArgumentParser(description="Align viewer-posted reference clips to local VODs")
    ap.add_argument("--min-hits", type=int, default=6,
                    help="min shingle hits in one 30s bucket to accept (default 6; first real match = 48)")
    ap.add_argument("--min-words", type=int, default=12)
    args = ap.parse_args()

    vods = load_vods()
    if not vods:
        print("[align] no VOD transcripts in vods/.transcriptions — run the pipeline on a VOD first.")
        return 0
    for v, (t, _) in vods.items():
        print(f"[align] VOD {v}: {len(t)} tokens")

    existing = set()
    if OUT.exists():
        for line in OUT.read_text(encoding="utf-8").splitlines():
            try:
                existing.add(json.loads(line).get("clip"))
            except Exception:
                pass

    rows, skipped_short, unmatched = [], 0, 0
    for wj in sorted(REF_CACHE.glob("*.words.json")):
        stem = wj.name[: -len(".words.json")]
        if stem in existing:
            continue
        try:
            words = json.loads(wj.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(words, list) or len(words) < args.min_words:
            skipped_short += 1
            continue
        ctoks = norm_tokens(" ".join(w.get("word", "") for w in words))
        if len(ctoks) < args.min_words:
            skipped_short += 1
            continue
        best = align_clip(ctoks, vods)
        if best and best[2] >= args.min_hits:
            rows.append({"vod": best[0], "timestamp": round(best[1], 1),
                         "label": 1, "clip": stem, "hits": best[2],
                         "source": "viewer-posted-social"})
            print(f"[align] MATCH {stem[:40]} -> {best[0][:36]} T={best[1]:.0f}s hits={best[2]}")
        else:
            unmatched += 1

    if rows:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        with open(OUT, "a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
    print(f"[align] {len(rows)} new label(s) -> {OUT.name} | already-labelled={len(existing)} "
          f"unmatched={unmatched} (source VOD not on disk) too-short={skipped_short}")
    print("[align] yield grows with collection habit: save viewer clips for streams whose "
          "VODs you keep — every matched pair is a free platform-validated label.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
