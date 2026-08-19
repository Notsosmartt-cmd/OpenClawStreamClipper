"""Frame scanner: decode the VOD at a low sample rate and run the HUD detectors.

Decode is one ffmpeg rawvideo pipe (`fps=SAMPLE_FPS, scale=960:-2`) — NVDEC
(-hwaccel cuda) is tried first and silently falls back to CPU decode; NVDEC's
few-hundred-MB surface pool is safe even with LM Studio models loaded.

Revive gate runs on every sampled frame (banner persists ~2.5 s, so 2 fps sees
it several times); the end-screen gate runs at ~1 fps with a timed
unconditional OCR backstop in case the red gate ever misses a variant screen.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import finals_common as fc  # noqa: E402
import hud_ocr  # noqa: E402

SCAN_W = 960
SAMPLE_FPS = 2.0


def probe_video(path: str) -> dict:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,avg_frame_rate",
         "-show_entries", "format=duration",
         "-of", "json", path],
        capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {r.stderr.strip()[:300]}")
    data = json.loads(r.stdout)
    st = data["streams"][0]
    return {
        "width": int(st["width"]), "height": int(st["height"]),
        "duration": float(data["format"]["duration"]),
    }


def _ffmpeg_cmd(path: str, fps: float, scale_h: int, start: float, end: float,
                hwaccel: bool) -> list[str]:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin"]
    if hwaccel:
        cmd += ["-hwaccel", "cuda"]
    if start > 0:
        cmd += ["-ss", f"{start:.3f}"]
    if end > 0:
        cmd += ["-to", f"{end:.3f}"]
    cmd += ["-i", path, "-vf", f"fps={fps},scale={SCAN_W}:{scale_h}",
            "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"]
    return cmd


def scan(vod: str, *, fps: float = SAMPLE_FPS, ocr_mode: str = "auto",
         revives: bool = True, endscreens: bool = True,
         start: float = 0.0, end: float = 0.0,
         max_minutes: float = 240.0, log=print,
         hwaccel: bool = True) -> dict:
    """Scan a VOD; returns {meta, revive_events, end_spans, stats}."""
    info = probe_video(vod)
    duration = info["duration"]
    if end <= 0 or end > duration:
        end = duration
    start = max(0.0, min(start, end))
    span = end - start
    scale_h = int(round(info["height"] * SCAN_W / info["width"] / 2)) * 2
    frame_bytes = SCAN_W * scale_h * 3
    end_stride = max(1, int(round(fps)))  # ~1 fps end-screen cadence

    gpu = hud_ocr.pick_gpu(ocr_mode)
    log(f"[scan] {os.path.basename(vod)}  {info['width']}x{info['height']}  "
        f"window {fc.fmt_clock(start)}-{fc.fmt_clock(end)} ({span/60:.1f} min)  "
        f"sample {fps:g} fps @ {SCAN_W}x{scale_h}  ocr={'gpu' if gpu else 'cpu'}")
    hud_ocr.get_reader(ocr_mode)  # init up front so failures surface early

    revive_hits: list[fc.ReviveHit] = []
    end_hits: list[fc.EndHit] = []
    stats = {"frames": 0, "revive_gate": 0, "end_gate": 0, "ocr_calls": 0,
             "hwaccel": hwaccel, "truncated": False}

    deadline = time.time() + max_minutes * 60
    t_wall0 = time.time()
    last_progress = t_wall0
    last_end_ocr = -1e9

    for attempt_hw in ([True, False] if hwaccel else [False]):
        proc = subprocess.Popen(_ffmpeg_cmd(vod, fps, scale_h, start, end, attempt_hw),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        idx = 0
        got_any = False
        try:
            while True:
                buf = proc.stdout.read(frame_bytes)
                if len(buf) < frame_bytes:
                    break
                got_any = True
                frame = np.frombuffer(buf, np.uint8).reshape(scale_h, SCAN_W, 3)
                t = start + idx / fps
                stats["frames"] += 1

                if revives:
                    if hud_ocr.bright_gate(frame):
                        stats["revive_gate"] += 1
                        stats["ocr_calls"] += 1
                        for kind, name, text in hud_ocr.classify_revive_lines(
                                hud_ocr.ocr_lines(hud_ocr.crop(frame, fc.REVIVE_BAND), ocr_mode),
                                scale_h):
                            revive_hits.append(fc.ReviveHit(t=t, kind=kind, name=name, text=text))

                if endscreens and idx % end_stride == 0:
                    force = (t - last_end_ocr) >= fc.END_OCR_EVERY_S
                    if force or hud_ocr.red_gate(frame):
                        stats["end_gate"] += 1
                        stats["ocr_calls"] += 1
                        last_end_ocr = t
                        etype = hud_ocr.scan_frame_end(frame, ocr_mode, force=True)
                        if etype:
                            end_hits.append(fc.EndHit(t=t, type=etype))

                idx += 1
                now = time.time()
                if now - last_progress >= 15:
                    last_progress = now
                    done_s = idx / fps
                    rate = done_s / max(1e-6, now - t_wall0)
                    eta = (span - done_s) / max(1e-6, rate)
                    log(f"[scan] {100*done_s/max(1e-6,span):5.1f}%  t={fc.fmt_clock(start+done_s)}"
                        f"/{fc.fmt_clock(end)}  revive_hits={len(revive_hits)} "
                        f"end_hits={len(end_hits)} ocr={stats['ocr_calls']} "
                        f"({rate:.0f}x realtime, eta {fc.fmt_clock(eta)})")
                if now > deadline:
                    stats["truncated"] = True
                    log(f"[scan] WARNING: --max-minutes ({max_minutes:g}) reached at "
                        f"t={fc.fmt_clock(start+done_s)} — stopping scan, keeping "
                        f"events found so far")
                    break
        finally:
            try:
                proc.kill()
            except Exception:
                pass
            proc.wait(timeout=10)

        if got_any or not attempt_hw:
            break
        err = (proc.stderr.read() or b"").decode(errors="replace")[:200] if proc.stderr else ""
        log(f"[scan] NVDEC decode produced no frames ({err.strip() or 'no stderr'}) — "
            f"retrying with CPU decode")
        stats["hwaccel"] = False

    events = fc.group_revive_hits(revive_hits) if revives else []
    spans = [s for s in (fc.group_end_hits(end_hits) if endscreens else [])]
    holds = sum(1 for h in revive_hits if h.kind == "hold")
    wall = time.time() - t_wall0
    log(f"[scan] done in {fc.fmt_clock(wall)} — {len(events)} revive event(s) "
        f"({holds} hold-prompt sighting(s), not clipped), {len(spans)} end-screen span(s); "
        f"{stats['frames']} frames, {stats['ocr_calls']} OCR calls "
        f"({'gpu' if hud_ocr.reader_is_gpu() else 'cpu'})")

    return {
        "meta": {"vod": os.path.abspath(vod), **info, "scan_start": start,
                 "scan_end": end, "sample_fps": fps,
                 "ocr": "gpu" if hud_ocr.reader_is_gpu() else "cpu"},
        "revive_events": events,
        "end_spans": spans,
        "revive_holds": holds,
        "stats": {**stats, "wall_s": round(wall, 1)},
    }
