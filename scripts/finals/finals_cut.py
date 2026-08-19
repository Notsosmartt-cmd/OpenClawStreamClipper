"""Clip cutting for the Finals clipper.

Clips are montage RAW MATERIAL: source resolution, source audio, no captions,
no 9:16 crop, no SFX — the owner edits the montage themselves. Re-encode (not
stream copy) so cut points are frame-accurate instead of snapping to 2 s
keyframes; NVENC first, libx264 fallback (one global downgrade on first failure).
"""
from __future__ import annotations

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import finals_common as fc  # noqa: E402

_ENCODERS = {
    "nvenc": ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "19", "-b:v", "0"],
    "x264": ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18"],
}
_encoder = "nvenc"


def _cut(vod: str, start: float, dur: float, out_path: str, log=print) -> bool:
    global _encoder
    for enc in ([_encoder] if _encoder == "x264" else ["nvenc", "x264"]):
        cmd = (["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                "-ss", f"{start:.3f}", "-i", vod, "-t", f"{dur:.3f}",
                "-map", "0:v:0", "-map", "0:a:0?"]
               + _ENCODERS[enc]
               + ["-c:a", "aac", "-b:a", "192k", "-pix_fmt", "yuv420p",
                  "-movflags", "+faststart", out_path])
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if r.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            if enc != _encoder:
                _encoder = enc
            return True
        if enc == "nvenc":
            log(f"[cut] h264_nvenc failed ({(r.stderr or '').strip()[:160]}) — "
                f"falling back to libx264 for this run")
            _encoder = "x264"
        else:
            log(f"[cut] FAILED {os.path.basename(out_path)}: {(r.stderr or '').strip()[:200]}")
    return False


def build_windows(result: dict, *, pre_s: float, post_s: float,
                  end_cap_s: float) -> dict:
    """Turn scan events into cut windows.

    Revives: [t - pre, t_last + post], overlapping windows merged (back-to-back
    defib chains become one clip). End screens: a quick clip per span per type,
    capped at end_cap_s.
    """
    duration = result["meta"]["duration"]
    revive_windows = []
    for i, ev in enumerate(result["revive_events"]):
        s = max(0.0, ev.t - pre_s)
        e = min(duration, ev.t_last + post_s)
        revive_windows.append((s, e, [i]))
    revive_windows = fc.merge_windows(revive_windows)

    end_windows = []
    for i, sp in enumerate(result["end_spans"]):
        span_len = sp.t_end - sp.t_start
        dur = max(fc.END_MIN_S, min(span_len + 2.0, end_cap_s))
        s = max(0.0, sp.t_start - 1.0)
        end_windows.append((s, min(duration, s + dur), [i], sp.type))
    return {"revive": revive_windows, "end": end_windows}


def cut_all(result: dict, out_dir: str, *, pre_s: float, post_s: float,
            end_cap_s: float, log=print) -> list[dict]:
    vod = result["meta"]["vod"]
    os.makedirs(out_dir, exist_ok=True)
    windows = build_windows(result, pre_s=pre_s, post_s=post_s, end_cap_s=end_cap_s)
    clips: list[dict] = []

    for n, (s, e, refs) in enumerate(windows["revive"], 1):
        evs = [result["revive_events"][i] for i in refs]
        names = sorted({nm for ev in evs for nm in ev.names})
        tag = f"x{len(refs)}_" if len(refs) > 1 else ""
        fname = f"revive_{n:02d}_{tag}{fc.fmt_ts(s)}.mp4"
        path = os.path.join(out_dir, fname)
        log(f"[cut] revive {n}/{len(windows['revive'])}  {fc.fmt_clock(s)} +{e-s:.1f}s"
            f"{'  (' + ', '.join(names) + ')' if names else ''}")
        if _cut(vod, s, e - s, path, log):
            clips.append({"file": fname, "type": "revive", "start": round(s, 2),
                          "dur": round(e - s, 2), "events": refs, "names": names})

    counts: dict[str, int] = {}
    for (s, e, refs, etype) in windows["end"]:
        counts[etype] = counts.get(etype, 0) + 1
        short = etype.replace("end_", "")
        fname = f"end_{short}_{counts[etype]:02d}_{fc.fmt_ts(s)}.mp4"
        path = os.path.join(out_dir, fname)
        log(f"[cut] {short} {counts[etype]}  {fc.fmt_clock(s)} +{e-s:.1f}s")
        if _cut(vod, s, e - s, path, log):
            clips.append({"file": fname, "type": etype, "start": round(s, 2),
                          "dur": round(e - s, 2), "events": refs})

    return clips
