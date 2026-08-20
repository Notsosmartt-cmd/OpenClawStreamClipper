"""Clip cutting + montage assembly for the Finals clipper.

Individual clips are montage RAW MATERIAL: source resolution, source audio, no
captions, no 9:16 crop, no SFX. Re-encode (not stream copy) so cut points are
frame-accurate instead of snapping to 2 s keyframes; NVENC first, libx264
fallback (one global downgrade on first failure).

build_montage() (owner ask 2026-08-20) additionally concatenates the run's
clips chronologically into one sped-up montage per VOD (default 1.75x —
owner wanted "1.5x-2x for more speed"). The individual clips are kept.
"""
from __future__ import annotations

import json
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


# --- montage ----------------------------------------------------------------------

def _probe_clip(path: str) -> tuple[float, bool, float]:
    """(fps, has_audio, duration) of a finished clip. fps defaults to 60."""
    fps, has_audio, dur = 60.0, False, 0.0
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "stream=codec_type,avg_frame_rate", "-show_entries", "format=duration",
             "-of", "json", path],
            capture_output=True, text=True, timeout=30)
        data = json.loads(r.stdout)
        for st in data.get("streams", []):
            if st.get("codec_type") == "video":
                num, _, den = (st.get("avg_frame_rate") or "60/1").partition("/")
                if float(den or 1) > 0 and float(num) > 0:
                    fps = float(num) / float(den or 1)
            elif st.get("codec_type") == "audio":
                has_audio = True
        dur = float(data.get("format", {}).get("duration", 0.0))
    except Exception:
        pass
    return fps, has_audio, dur


def build_montage(clips: list[dict], out_dir: str, *, speed: float = 1.75,
                  log=print) -> dict | None:
    """Concatenate this run's clips chronologically into one sped-up montage.

    Chronological order interleaves naturally: a match's revives, then its end
    screens, then the next match. All clips come from the same VOD via the same
    _cut() settings, so the concat demuxer's uniform-stream assumption holds
    (an nvenc->x264 mid-run fallback still yields same-codec/same-size h264).
    Speed uses setpts (video) + atempo (audio, pitch-preserving); atempo is
    chained above 2.0x since one instance caps there on older ffmpeg builds.
    """
    global _encoder
    items = sorted((c for c in clips if c.get("type") != "montage"),
                   key=lambda c: c.get("start") or 0)
    paths = [os.path.join(out_dir, c["file"]) for c in items]
    paths = [p for p in paths if os.path.exists(p)]
    if not paths:
        log("[montage] no clips to combine — skipping")
        return None
    speed = max(1.0, min(3.0, float(speed)))
    fps, has_audio, _ = _probe_clip(paths[0])

    list_path = os.path.join(out_dir, ".montage_list.txt")
    with open(list_path, "w", encoding="utf-8") as fh:
        for p in paths:
            fh.write("file '" + p.replace("\\", "/").replace("'", "'\\''") + "'\n")

    name = f"montage_{speed:g}x.mp4"
    out_path = os.path.join(out_dir, name)
    vf = f"setpts=PTS/{speed:g},fps={fps:g}"
    af = f"atempo={speed:g}" if speed <= 2.0 else f"atempo=2.0,atempo={speed/2.0:g}"
    log(f"[montage] combining {len(paths)} clip(s) at {speed:g}x -> {name}")

    ok = False
    for enc in ([_encoder] if _encoder == "x264" else ["nvenc", "x264"]):
        cmd = (["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                "-f", "concat", "-safe", "0", "-fflags", "+genpts", "-i", list_path,
                "-vf", vf] + _ENCODERS[enc]
               + (["-af", af, "-c:a", "aac", "-b:a", "192k"] if has_audio else ["-an"])
               + ["-pix_fmt", "yuv420p", "-movflags", "+faststart", out_path])
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if r.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            if enc != _encoder:
                _encoder = enc
            ok = True
            break
        if enc == "nvenc":
            log(f"[montage] h264_nvenc failed ({(r.stderr or '').strip()[:160]}) — "
                f"falling back to libx264")
            _encoder = "x264"
        else:
            log(f"[montage] FAILED: {(r.stderr or '').strip()[:200]}")
    try:
        os.remove(list_path)
    except OSError:
        pass
    if not ok:
        return None

    _, _, dur = _probe_clip(out_path)
    log(f"[montage] done — {dur:.1f}s from {sum(c.get('dur') or 0 for c in items):.1f}s of clips")
    return {"file": name, "type": "montage", "speed": speed, "start": None,
            "dur": round(dur, 2), "events": [], "names": [],
            "source_clips": len(paths)}
