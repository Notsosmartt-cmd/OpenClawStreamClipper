#!/usr/bin/env python3
"""Finals clipper CLI — scan a VOD for THE FINALS revives + end-of-match
screens and cut buffered montage clips.

Usage:
  .venv\\Scripts\\python.exe scripts\\finals\\run_finals.py --vod vods\\my_finals_vod.mp4
  ... --pre 10 --post 5 --end-cap 6          # buffers (seconds)
  ... --start 00:10:00 --end 00:40:00        # scan a window only
  ... --probe 01:23:45                       # classify ONE frame + annotated jpg
  ... --selftest                             # synthetic-frame detector check

Run markers (finals_clips/.run/{pid,done}) let the Finals dashboard detect a
run it didn't launch — same cross-process pattern as the main pipeline (BUG 67).
Every run is bounded by --max-minutes (default 240) so nothing zombies.
"""
from __future__ import annotations

import argparse
import functools
import json
import os
import sys
import time
from dataclasses import asdict

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
PROJECT = os.path.dirname(os.path.dirname(_HERE))
OUT_ROOT = os.path.join(PROJECT, "finals_clips")

import finals_common as fc  # noqa: E402

log = functools.partial(print, flush=True)


def parse_time(s: str) -> float:
    if ":" in s:
        parts = [float(p) for p in s.split(":")]
        t = 0.0
        for p in parts:
            t = t * 60 + p
        return t
    return float(s)


# --- selftest -----------------------------------------------------------------

def _synth(text_calls, bg=(60, 55, 50), noise=True):
    import cv2
    import numpy as np
    rng = np.random.default_rng(3)
    frame = np.full((720, 1280, 3), bg, np.uint8)
    if noise:
        frame = cv2.add(frame, rng.integers(0, 35, (720, 1280, 3)).astype(np.uint8))
    for (text, xf, yf, scale, color, thick) in text_calls:
        cv2.putText(frame, text, (int(xf * 1280), int(yf * 720)),
                    cv2.FONT_HERSHEY_DUPLEX, scale, color, thick, cv2.LINE_AA)
    return frame


def selftest(ocr_mode: str) -> int:
    import hud_ocr
    W = (255, 255, 255)
    C = (255, 200, 80)
    RED_BG = (30, 30, 150)
    DARK_RED_BG = (20, 20, 70)
    fails = 0

    def check(name, ok, detail=""):
        nonlocal fails
        log(f"  [{'PASS' if ok else 'FAIL'}] {name}{('  ' + detail) if detail else ''}")
        if not ok:
            fails += 1

    log("[selftest] revive detection")
    f = _synth([("REVIVE", 0.38, 0.695, 1.0, W, 2), ("KINGOFSWING", 0.49, 0.695, 1.0, C, 2),
                ("REVIVE 200 + 200", 0.42, 0.755, 0.55, W, 1)])
    hits = hud_ocr.scan_frame_revive(f, ocr_mode)
    check("completion banner -> strong", any(k == "strong" for k, _, _ in hits), str(hits))
    check("teammate name extracted", any(n for _, n, _ in hits), str([n for _, n, _ in hits]))

    f = _synth([("REVIVE", 0.47, 0.35, 0.35, (200, 200, 200), 1)])
    check("world marker (tiny, high) -> nothing", not hud_ocr.scan_frame_revive(f, ocr_mode))

    f = _synth([("HOLD TO REVIVE", 0.42, 0.60, 0.7, W, 2)])
    hits = hud_ocr.scan_frame_revive(f, ocr_mode)
    check("HOLD TO REVIVE -> hold only",
          hits and all(k == "hold" for k, _, _ in hits), str(hits))

    check("plain gameplay -> nothing", not hud_ocr.scan_frame_revive(_synth([]), ocr_mode))

    log("[selftest] end-screen detection")
    f = _synth([("SUMMARY", 0.05, 0.06, 0.8, W, 2),
                ("STANDOUT CONTESTANTS  TOURNAMENT RESULT  TEAM PERFORMANCE", 0.05, 0.105, 0.45, W, 1),
                ("RANK SCORE UPDATE", 0.30, 0.62, 0.6, W, 1),
                ("FINISHING POSITION", 0.30, 0.70, 0.5, W, 1)], bg=RED_BG)
    check("tournament result", hud_ocr.scan_frame_end(f, ocr_mode) == "end_tournament",
          str(hud_ocr.scan_frame_end(f, ocr_mode)))

    f = _synth([("SUMMARY", 0.05, 0.06, 0.8, W, 2),
                ("COMBAT SCORE", 0.10, 0.62, 0.55, W, 1),
                ("OBJECTIVE SCORE", 0.40, 0.66, 0.55, W, 1),
                ("SUPPORT SCORE", 0.70, 0.70, 0.55, W, 1)], bg=RED_BG)
    check("team performance", hud_ocr.scan_frame_end(f, ocr_mode) == "end_teamperf",
          str(hud_ocr.scan_frame_end(f, ocr_mode)))

    f = _synth([("DEFEATED", 0.05, 0.06, 0.9, W, 2),
                ("PERSONAL PERFORMANCE  MATCH SUMMARY  SCOREBOARD", 0.05, 0.105, 0.45, W, 1),
                ("SUPPORT 6909", 0.10, 0.60, 0.5, W, 1)], bg=DARK_RED_BG)
    check("personal performance", hud_ocr.scan_frame_end(f, ocr_mode) == "end_personal",
          str(hud_ocr.scan_frame_end(f, ocr_mode)))

    check("gameplay frame fails red gate", hud_ocr.scan_frame_end(_synth([]), ocr_mode) is None)

    log("[selftest] grouping")
    hits = [fc.ReviveHit(10.0, "strong", "BOB"), fc.ReviveHit(10.5, "weak"),
            fc.ReviveHit(30.0, "weak"),
            fc.ReviveHit(50.0, "weak"), fc.ReviveHit(51.0, "weak"),
            fc.ReviveHit(70.0, "hold")]
    evs = fc.group_revive_hits(hits)
    check("strong emits, lone weak doesn't, 2x weak does, hold doesn't",
          len(evs) == 2 and evs[0].t == 10.0 and evs[1].t == 50.0,
          f"{[(e.t, e.strong, e.weak) for e in evs]}")

    spans = fc.group_end_hits([fc.EndHit(100, "end_tournament"), fc.EndHit(101, "end_tournament"),
                               fc.EndHit(103, "end_teamperf"), fc.EndHit(104, "end_teamperf"),
                               fc.EndHit(140, "end_tournament")])
    check("spans split on type change and gaps", len(spans) == 3,
          f"{[(s.type, s.t_start, s.t_end) for s in spans]}")

    merged = fc.merge_windows([(5, 20, [0]), (18, 35, [1]), (50, 60, [2])])
    check("overlapping windows merge", merged == [(5, 35, [0, 1]), (50, 60, [2])], str(merged))

    log(f"[selftest] {'ALL PASS' if fails == 0 else f'{fails} FAILURE(S)'}")
    return 0 if fails == 0 else 1


# --- probe ---------------------------------------------------------------------

def probe(vod: str, t: float, ocr_mode: str) -> int:
    import cv2
    import numpy as np
    import subprocess

    import finals_scan
    import hud_ocr

    info = finals_scan.probe_video(vod)
    scale_h = int(round(info["height"] * finals_scan.SCAN_W / info["width"] / 2)) * 2
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
         "-ss", f"{t:.3f}", "-i", vod, "-frames:v", "1",
         "-vf", f"scale={finals_scan.SCAN_W}:{scale_h}",
         "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"],
        capture_output=True, timeout=120)
    frame_bytes = finals_scan.SCAN_W * scale_h * 3
    if len(r.stdout) < frame_bytes:
        log(f"[probe] could not extract a frame at {fc.fmt_clock(t)}: {r.stderr.decode(errors='replace')[:200]}")
        return 1
    frame = np.frombuffer(r.stdout[:frame_bytes], np.uint8).reshape(scale_h, finals_scan.SCAN_W, 3).copy()

    revive_lines = hud_ocr.ocr_lines(hud_ocr.crop(frame, fc.REVIVE_BAND), ocr_mode)
    revive_hits = hud_ocr.classify_revive_lines(revive_lines, scale_h)
    end_type = hud_ocr.scan_frame_end(frame, ocr_mode, force=True)

    out = {
        "t": t, "bright_gate": hud_ocr.bright_gate(frame), "red_gate": hud_ocr.red_gate(frame),
        "revive_band_lines": [{"text": ln["text"], "h_px": round(ln["h_px"], 1)} for ln in revive_lines],
        "revive_hits": [{"kind": k, "name": n, "text": x} for k, n, x in revive_hits],
        "end_screen": end_type,
    }
    log(json.dumps(out, indent=2))

    h, w = frame.shape[:2]
    for box, color in ((fc.REVIVE_BAND, (0, 255, 255)), (fc.TOP_STRIP, (255, 200, 0)),
                       (fc.BODY_BAND, (180, 120, 255))):
        x0, y0, x1, y1 = box
        cv2.rectangle(frame, (int(x0 * w), int(y0 * h)), (int(x1 * w), int(y1 * h)), color, 1)
    bx0, by0 = int(fc.REVIVE_BAND[0] * w), int(fc.REVIVE_BAND[1] * h)
    for ln in revive_lines:
        for (x0, y0, x1, y1) in ln["boxes"]:
            cv2.rectangle(frame, (bx0 + int(x0), by0 + int(y0)),
                          (bx0 + int(x1), by0 + int(y1)), (0, 0, 255), 1)
    probe_dir = os.path.join(OUT_ROOT, "_probe")
    os.makedirs(probe_dir, exist_ok=True)
    jpg = os.path.join(probe_dir, f"probe_{os.path.splitext(os.path.basename(vod))[0]}_{fc.fmt_ts(t)}.jpg")
    cv2.imwrite(jpg, frame)
    log(f"[probe] annotated frame -> {jpg}")
    return 0


# --- run markers (dashboard cross-process detection) -----------------------------

def _run_dir() -> str:
    d = os.path.join(OUT_ROOT, ".run")
    os.makedirs(d, exist_ok=True)
    return d


def write_pid_marker(vods: list[str], index: int) -> None:
    """(Re)written once per queue item so a poller can show 'VOD 2/4'. The
    started= timestamp is only set on the FIRST write of a run (queue_started
    file) so the whole-batch elapsed time is still recoverable."""
    done = os.path.join(_run_dir(), "done")
    if os.path.exists(done):
        os.remove(done)
    started_path = os.path.join(_run_dir(), "queue_started")
    if index == 0:
        with open(started_path, "w", encoding="utf-8") as fh:
            fh.write(str(int(time.time())))
    started = int(time.time())
    if os.path.exists(started_path):
        try:
            started = int(open(started_path, encoding="utf-8").read().strip())
        except Exception:
            pass
    with open(os.path.join(_run_dir(), "pid"), "w", encoding="utf-8") as fh:
        fh.write(f"pid={os.getpid()}\nvod={vods[index]}\nindex={index}\ntotal={len(vods)}\n"
                 f"queue={'|'.join(vods)}\nstarted={started}\n")


def write_done_marker(code: int) -> None:
    try:
        with open(os.path.join(_run_dir(), "done"), "w", encoding="utf-8") as fh:
            fh.write(f"exit_code={code}\nfinished={int(time.time())}\n")
        started_path = os.path.join(_run_dir(), "queue_started")
        if os.path.exists(started_path):
            os.remove(started_path)
    except Exception:
        pass


def remontage(stem: str, speed: float) -> int:
    """Rebuild the montage for an EXISTING finals_clips/<stem> run — no scan,
    no re-cut. Lets runs made before the montage feature (or with a different
    speed) get a montage from their already-cut clips. Old montage files are
    left on disk (never hard-delete user-visible output); events.json's clip
    list swaps to the new one."""
    stem = os.path.splitext(os.path.basename(stem.strip().strip('"')))[0]
    out_dir = os.path.join(OUT_ROOT, stem)
    events_path = os.path.join(out_dir, "events.json")
    if not os.path.exists(events_path):
        log(f"[remontage] no run found at finals_clips/{stem} (missing events.json)")
        return 1

    import finals_cut
    with open(events_path, encoding="utf-8") as fh:
        payload = json.load(fh)
    source = [c for c in payload.get("clips", []) if c.get("type") != "montage"]
    if not source:
        log(f"[remontage] run {stem} has no clips to combine")
        return 1
    old = [c for c in payload.get("clips", []) if c.get("type") == "montage"]
    for c in old:
        log(f"[remontage] previous montage {c['file']} left in place (replaced in events.json)")

    write_pid_marker([stem], 0)
    code = 1
    try:
        m = finals_cut.build_montage(source, out_dir, speed=speed, log=log)
        if m:
            payload["clips"] = source + [m]
            payload.setdefault("params", {})["montage"] = True
            payload["params"]["montage_speed"] = m["speed"]
            with open(events_path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
            log(f"[finals] montage {m['file']} ({m['dur']:.1f}s @ {m['speed']:g}x) "
                f"from {m['source_clips']} clip(s)")
            code = 0
    finally:
        write_done_marker(code)
    return code


def resolve_vod(raw: str) -> str | None:
    vod = raw
    if not os.path.isabs(vod) and not os.path.exists(vod):
        candidate = os.path.join(PROJECT, "vods", vod)
        if os.path.exists(candidate):
            vod = candidate
    if not os.path.exists(vod):
        return None
    return os.path.abspath(vod)


def process_one(vod: str, args, log=log) -> bool:
    """Scan + cut one VOD into finals_clips/<stem>/. Returns True on success —
    failures are logged and swallowed so one bad VOD doesn't kill the queue
    (same resilience spirit as the main pipeline's per-VOD checkpointing)."""
    stem = os.path.splitext(os.path.basename(vod))[0]
    out_dir = os.path.abspath(args.out) if (args.out and len(args._vods) == 1) \
        else os.path.join(OUT_ROOT, stem)
    os.makedirs(out_dir, exist_ok=True)

    try:
        import finals_scan
        import finals_cut

        result = finals_scan.scan(
            vod, fps=args.fps, ocr_mode=args.ocr,
            revives=not args.no_revives, endscreens=not args.no_endscreens,
            start=args.start, end=args.end, max_minutes=args.max_minutes,
            log=log, hwaccel=not args.no_hwaccel)

        payload = {
            "meta": result["meta"],
            "params": {"pre_s": args.pre, "post_s": args.post, "end_cap_s": args.end_cap,
                       "fps": args.fps, "revives": not args.no_revives,
                       "endscreens": not args.no_endscreens,
                       "montage": not args.no_montage,
                       "montage_speed": args.montage_speed},
            "revive_events": [asdict(e) for e in result["revive_events"]],
            "end_spans": [asdict(s) for s in result["end_spans"]],
            "revive_holds": result["revive_holds"],
            "stats": result["stats"],
            "clips": [],
        }

        if not args.scan_only:
            payload["clips"] = finals_cut.cut_all(
                result, out_dir, pre_s=args.pre, post_s=args.post,
                end_cap_s=args.end_cap, log=log)
            if payload["clips"] and not args.no_montage:
                m = finals_cut.build_montage(payload["clips"], out_dir,
                                             speed=args.montage_speed, log=log)
                if m:
                    payload["clips"].append(m)

        events_path = os.path.join(out_dir, "events.json")
        with open(events_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        _m = next((c for c in payload["clips"] if c["type"] == "montage"), None)
        log(f"[finals] {len(payload['revive_events'])} revive event(s) -> "
            f"{sum(1 for c in payload['clips'] if c['type'] == 'revive')} clip(s); "
            f"{len(payload['end_spans'])} end-screen span(s) -> "
            f"{sum(1 for c in payload['clips'] if c['type'].startswith('end_'))} clip(s)"
            + (f"; montage {_m['file']} ({_m['dur']:.1f}s @ {_m['speed']:g}x)" if _m else ""))
        log(f"[finals] output: {out_dir}")
        return True
    except Exception as e:
        log(f"[finals] FAILED on {os.path.basename(vod)}: {e}")
        return False


# --- main ------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="THE FINALS revive + end-screen clipper")
    ap.add_argument("--vod", action="append", default=[],
                    help="VOD path (absolute, or a name inside vods/) — repeatable")
    ap.add_argument("--vods", default="",
                    help="comma-separated VOD names/stems to process sequentially "
                         "(dashboard multi-select — same convention as run_pipeline.py)")
    ap.add_argument("--out", help="output dir override — only honored for a single VOD")
    ap.add_argument("--pre", type=float, default=fc.DEFAULT_PRE_S, help="seconds before the revive")
    ap.add_argument("--post", type=float, default=fc.DEFAULT_POST_S, help="seconds after the revive")
    ap.add_argument("--end-cap", type=float, default=fc.DEFAULT_END_CAP_S,
                    help="max length of each end-screen quick clip")
    ap.add_argument("--fps", type=float, default=2.0, help="sample rate (frames/sec scanned)")
    ap.add_argument("--ocr", choices=("auto", "gpu", "cpu"), default="auto")
    ap.add_argument("--no-revives", action="store_true")
    ap.add_argument("--no-endscreens", action="store_true")
    ap.add_argument("--start", type=parse_time, default=0.0, help="scan window start (s or HH:MM:SS)")
    ap.add_argument("--end", type=parse_time, default=0.0, help="scan window end")
    ap.add_argument("--max-minutes", type=float, default=240.0,
                    help="hard wall-clock bound PER VOD (the queue re-arms it for each item)")
    ap.add_argument("--no-hwaccel", action="store_true")
    ap.add_argument("--no-montage", action="store_true",
                    help="skip the per-VOD sped-up montage of this run's clips")
    ap.add_argument("--montage-speed", type=float, default=1.75,
                    help="montage playback speed 1.0-3.0 (owner default 1.75x)")
    ap.add_argument("--scan-only", action="store_true", help="detect + write events.json, no cutting")
    ap.add_argument("--probe", type=parse_time, default=None, metavar="T",
                    help="classify one frame at T, save annotated jpg, exit (single VOD only)")
    ap.add_argument("--remontage", metavar="STEM",
                    help="rebuild the montage for an existing finals_clips/<stem> run "
                         "(uses --montage-speed; no scan, no re-cut)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest(args.ocr)

    if args.remontage:
        return remontage(args.remontage, args.montage_speed)

    raw_vods = list(args.vod)
    raw_vods += [v.strip() for v in args.vods.split(",") if v.strip()]
    if not raw_vods:
        ap.error("--vod (repeatable) or --vods (comma-separated) is required (unless --selftest)")

    vods: list[str] = []
    for raw in raw_vods:
        resolved = resolve_vod(raw)
        if not resolved:
            log(f"[finals] VOD not found: {raw}")
            return 1
        if resolved not in vods:  # de-dupe (same VOD picked via dropdown + typed path)
            vods.append(resolved)
    args._vods = vods

    if args.probe is not None:
        if len(vods) != 1:
            ap.error("--probe takes exactly one --vod")
        return probe(vods[0], args.probe, args.ocr)

    log(f"[finals] queue: {len(vods)} VOD(s)")
    ok_count = 0
    code = 1
    try:
        for i, vod in enumerate(vods):
            write_pid_marker(vods, i)
            log(f"[queue] VOD {i + 1}/{len(vods)}: {os.path.basename(vod)}")
            if process_one(vod, args):
                ok_count += 1
        log(f"[finals] queue done — {ok_count}/{len(vods)} VOD(s) succeeded")
        code = 0 if ok_count == len(vods) else 1
    finally:
        write_done_marker(code)
    return code


if __name__ == "__main__":
    sys.exit(main())
