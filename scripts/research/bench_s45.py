#!/usr/bin/env python3
"""bench_s45.py — J6 of plan-s45-text-judge-2026-07, SECTIONAL edition.

Owner directive (2026-07-15): never process a full VOD to test a section —
run the pipeline only UP TO the sections under test, timing each one.

This bench drives the PRODUCTION stage code directly (same Ctx, same
stage modules, same S4.5 helper) but stops after the judge: no frames, no
vision tournament, no enrichment, no renders, no processed.log entry, no
clips written. Two modes:

  DETECT+JUDGE (first run for a VOD):
    python scripts/research/bench_s45.py --vod 20260712_Raud_2818672353.mp4
    → stages 1-4 (transcript comes from the cache; S4 is the real 9B pass,
      then the S4.5 judge. Saves a pre-judge moments
      snapshot so later benches can skip detection entirely.

  JUDGE-ONLY (re-uses a snapshot — ZERO VOD processing):
    python scripts/research/bench_s45.py --vod <name> --moments clips/.diagnostics/bench_s45_moments_<stem>.json
    → packets + judge sections only (~minutes).

Flags: --no-judge (measure S4 alone), --sections detect|judge|both.

Output: clips/.diagnostics/bench_s45_<stamp>.json — per-section seconds +
candidate/kept/killed counts + the judge's per-kill rationales (via the
stage's own s45_judge_* report). Compare judge configurations or a
judge pass against the production baseline WITHOUT any full run.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "scripts"))          # run_pipeline + pipeline pkg
sys.path.insert(0, str(REPO / "scripts" / "lib"))  # evidence_packets etc.

DIAG = REPO / "clips" / ".diagnostics"


# The bench uses the PRODUCTION common.Logger (stage code calls log.write()
# etc. — a partial shim broke on run_module; second bench-run lesson). The
# ephemeral pipeline log is shared with the dashboard SSE (harmless — the
# bench refuses to run beside a live pipeline anyway); the persistent copy
# gets a bench-prefixed name so run logs stay distinguishable.


def main() -> int:
    ap = argparse.ArgumentParser(description="Sectional S4.5 bench — stages 1-4 + judge, never a full run")
    ap.add_argument("--vod", required=True, help="VOD filename under vods/")
    ap.add_argument("--moments", default="", help="pre-judge moments snapshot → judge-only mode")
    ap.add_argument("--sections", default="", choices=("", "detect", "judge", "both"),
                    help="default: both (or judge when --moments given)")
    ap.add_argument("--no-judge", action="store_true", help="alias for --sections detect")
    ap.add_argument("--max-hours", type=float, default=0.0,
                    help="bound S4 scope: truncate the work-dir TRANSCRIPT to its first N "
                         "hours (0 = no cap) — stage 4 chunks the transcript, so this is "
                         "what actually bounds it. For finder A/Bs on a long VOD: both "
                         "arms get the IDENTICAL slice, and the drop is logged.")
    args = ap.parse_args()
    # (--recall removed 2026-07-16 with the recall feature itself — the same-VOD
    # A/B this bench ran killed it: 4x S4 cost, zero candidate yield.)

    sections = args.sections or ("judge" if args.moments else "both")
    if args.no_judge:
        sections = "detect"
    want_detect = sections in ("detect", "both") and not args.moments
    want_judge = sections in ("judge", "both")

    stamp = time.strftime("%Y%m%d_%H%M%S")
    os.environ.setdefault("CLIP_RUN_STAMP", f"bench45_{stamp}")
    # The judge flag drives the Pass-D skip in the production code paths —
    # set it exactly as a real judged run would.
    os.environ["CLIP_S45_JUDGE"] = "1" if want_judge else "0"

    import run_pipeline
    from pipeline import common
    from pipeline.stages import stage1, stage2, stage3, stage4, stage5

    ctx = run_pipeline.Ctx(argparse.Namespace(
        style="auto", vod=args.vod, type="", list=False, force=True, all=False))
    p = ctx.paths
    persistent = REPO / "clips" / ".pipeline_logs" / f"bench_s45_{stamp}.log"
    log = common.Logger(p.pipeline_log, persistent)
    ctx.log = log
    log.line(f"=== bench_s45 [{stamp}] vod={args.vod} sections={sections} ===")

    timing: dict[str, float] = {}
    report: dict = {"stamp": stamp, "vod": args.vod, "sections": sections,
                    # provenance: which finder / judge produced this row (finder
                    # A/Bs swap passb per arm — the report must say which)
                    "finder_model": ctx.text_model_passb,
                    "judge_model": ctx.vision_model_stage6,
                    "timing_s": timing}

    def _section(name, fn):
        t0 = time.time()
        fn()
        timing[name] = round(time.time() - t0, 1)
        log.line(f"--- section {name}: {timing[name]}s ---")

    try:
        if want_detect:
            _section("s1_discovery", lambda: stage1.run(ctx))
            _section("s2_transcribe", lambda: stage2.run(ctx))
            _section("s3_segments", lambda: stage3.run(ctx))
            # Bounded-scope A/B (2026-07-18): trim the WORK-DIR TRANSCRIPT — that
            # is what stage 4 actually chunks (`max_time = max(end)` over
            # transcript.json; segments.json is only a segment-TYPE map, so
            # truncating it does NOT bound S4 — measured the hard way).
            # Must run AFTER stage 3: its cache key is a sha1 over the transcript
            # bytes, so truncating earlier would miss the segcache and re-roll
            # segments. Persistent caches are never touched. Both arms of a
            # finder A/B must use the same value to stay comparable.
            if args.max_hours > 0:
                tpath = Path(p.transcript_json)
                _all = json.loads(tpath.read_text(encoding="utf-8"))
                _cap = args.max_hours * 3600.0
                _kept = [s for s in _all if float(s.get("end", 0)) <= _cap]
                if _kept:
                    tpath.write_text(json.dumps(_kept, indent=2), encoding="utf-8")
                    _hrs = max(float(s.get("end", 0)) for s in _kept) / 3600.0
                    log.log(f"[bounded-scope] transcript rows {len(_all)} -> {len(_kept)} "
                            f"(cap {args.max_hours}h; {len(_all) - len(_kept)} "
                            f"DROPPED; timeline now ends at {_hrs:.2f}h)")
                    report["scope_cap_hours"] = args.max_hours
                    report["transcript_rows_kept"] = len(_kept)
                    report["transcript_rows_dropped"] = len(_all) - len(_kept)
                    report["scope_hours_kept"] = round(_hrs, 2)
                else:
                    log.warn(f"[bounded-scope] cap {args.max_hours}h kept ZERO "
                             f"transcript rows — ignoring the cap")
            _section("s4_detect", lambda: stage4.run(ctx))
            moments = json.loads(p.hype_moments.read_text(encoding="utf-8"))
            # stamp in the name — two benches on one VOD must never
            # overwrite each other's snapshots (2026-07-16 lesson)
            snap = DIAG / f"bench_s45_moments_{Path(args.vod).stem}_{stamp}.json"
            DIAG.mkdir(parents=True, exist_ok=True)
            snap.write_text(json.dumps(moments, indent=2), encoding="utf-8")
            report["moments_snapshot"] = str(snap)
            log.log(f"pre-judge snapshot: {snap} ({len(moments)} candidates)")
        else:
            src = Path(args.moments)
            moments = json.loads(src.read_text(encoding="utf-8"))
            p.hype_moments.parent.mkdir(parents=True, exist_ok=True)
            p.hype_moments.write_text(json.dumps(moments, indent=2), encoding="utf-8")
            # judge-only: no stage 2 ran, so materialize the transcript (and
            # audio events) from the per-VOD caches — packets need real words.
            stem = Path(args.vod).stem
            for cache_name, work_path in (
                    (f"{stem}.json", p.transcript_json),
                    (f"{stem}.audio_events.json", p.work("audio_events.json"))):
                cached = p.transcriptions_dir / cache_name
                if not Path(work_path).exists() and cached.exists():
                    Path(work_path).write_text(cached.read_text(encoding="utf-8"),
                                               encoding="utf-8")
                    log.log(f"judge-only: materialized {cache_name} from cache")
            if not Path(p.transcript_json).exists():
                log.warn("judge-only: NO transcript available — packets will lack "
                         "verbatim evidence (judge quality meaningless); run the "
                         "detect sections once first")
            log.log(f"judge-only mode: {len(moments)} candidates from {src.name}")

        report["candidates"] = len(moments)

        if want_judge and moments:
            survivors_holder = {}

            def _judge():
                survivors_holder["v"] = stage5._s45_text_judge(ctx, log, moments)
            _section("s45_judge_total", _judge)
            survivors = survivors_holder["v"]
            report["kept"] = len(survivors)
            report["culled"] = len(moments) - len(survivors)
            # the stage helper wrote its own s45_judge_* report (decisions +
            # packets/judge split) — reference it
            report["judge_reports"] = sorted(
                f.name for f in DIAG.glob(f"s45_judge_bench45_{stamp}*_*.json"))
        report["status"] = "ok"
    except common.PipelineExit as e:
        report["status"] = f"pipeline_exit:{e}"
        log.warn(f"stage requested exit: {e}")
    except Exception as e:  # noqa: BLE001
        report["status"] = f"error:{type(e).__name__}:{e}"
        log.err(f"bench failed: {type(e).__name__}: {e}")

    DIAG.mkdir(parents=True, exist_ok=True)
    out = DIAG / f"bench_s45_{stamp}.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    log.line(f"=== bench report: {out} ===")
    log.line(json.dumps({k: v for k, v in report.items()
                         if k not in ("judge_reports",)}, indent=1)[:600])
    return 0 if str(report.get("status")) == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
