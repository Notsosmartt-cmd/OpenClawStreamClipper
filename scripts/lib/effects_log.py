#!/usr/bin/env python3
"""effects_log.py — per-clip record of applied special effects (owner request
2026-07-04: "log which clips received the special effects into a file so I can
know what I am reviewing/critiquing").

Appends one JSON line per event to  clips/.diagnostics/effects_log.jsonl
(the diagnostics dir persists across runs; the work dir is cleaned). Each line:

    {"ts": <epoch>, "run": "<stamp>", "vod": ..., "clip": "<title/path>",
     "type": "render_plan" | "cold_open" | "transitions",
     "data": {...effect specifics: sfx cues w/ kind+t+gain_db, zoom punches,
              flashes w/ t, teaser span, ...}}

FAILURE-SOFT and logging-only: never raises, never alters render behavior.
Read it back with:  python scripts/lib/effects_log.py [--clip SUBSTR] [--last N]
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path


def _out_path() -> Path:
    try:
        import paths as _p  # scripts/lib on sys.path in pipeline children
        d = _p.PATHS.diagnostics_dir
    except Exception:
        base = os.environ.get("CLIP_CLIPS_DIR") or str(
            Path(__file__).resolve().parents[2] / "clips")
        d = Path(base) / ".diagnostics"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return d / "effects_log.jsonl"


def log_effect(clip: str, etype: str, data: dict, *, vod: str | None = None) -> None:
    """Append one effect record. Never raises."""
    try:
        # Stable per-RUN stamp: CLIP_RUN_STAMP is set once by run_pipeline, so all
        # of a run's clips group under one id. The old strftime-at-log-time gave
        # each clip its own "run" (clips render seconds apart) — the 2026-07-04
        # manifest looked like 1/10 coverage when it was really 10 runs of 1.
        rec = {"ts": round(time.time(), 1),
               "run": os.environ.get("CLIP_RUN_STAMP") or time.strftime("%Y%m%d_%H%M%S"),
               "vod": vod or os.environ.get("CLIP_CURRENT_VOD") or "",
               "clip": str(clip)[:160], "type": str(etype), "data": data}
        with open(_out_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str) + "\n")
    except Exception:
        pass


def read_effects(clip_substr: str | None = None, last: int = 50) -> list[dict]:
    """Read records back (newest last), optionally filtered by clip substring."""
    p = _out_path()
    if not p.exists():
        return []
    out: list[dict] = []
    try:
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if clip_substr and clip_substr.lower() not in str(rec.get("clip", "")).lower():
                continue
            out.append(rec)
    except Exception:
        return []
    return out[-last:]


def _fmt(rec: dict) -> str:
    d = rec.get("data", {})
    bits = []
    if rec["type"] == "render_plan":
        for c in d.get("sfx_cues", []):
            bits.append(f"SFX {c.get('kind')}@{c.get('t', c.get('at', '?'))}s ({c.get('gain_db', 0)}dB)")
        for z in d.get("zoom_punches", []):
            t = z.get("t", z) if isinstance(z, dict) else z
            bits.append(f"zoom@{t}s")
        if d.get("freeze_at"):
            bits.append(f"freeze@{d['freeze_at']}s")
        if d.get("slow_mo"):
            bits.append(f"slowmo {d['slow_mo']}")
        if d.get("preset"):
            bits.append(f"preset={d['preset']}")
    elif rec["type"] == "cold_open":
        bits.append(f"teaser {d.get('tease_start')}s+{d.get('tease_dur')}s")
    elif rec["type"] == "transitions":
        for fl in d.get("flashes", []):
            bits.append(f"flash@{fl.get('t')}s")
        if d.get("jump_cuts"):
            bits.append(f"jumpcuts={d['jump_cuts']}")
    return f"[{rec['type']}] {rec['clip'][:48]} :: " + ("; ".join(bits) or json.dumps(d)[:120])


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Read the per-clip effects log")
    ap.add_argument("--clip", help="filter by clip-title substring")
    ap.add_argument("--last", type=int, default=50)
    a = ap.parse_args()
    for rec in read_effects(a.clip, a.last):
        print(_fmt(rec))
