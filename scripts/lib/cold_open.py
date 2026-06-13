#!/usr/bin/env python3
"""Cold-open teaser: prepend a short tease of the clip's run-up to the payoff,
then a whoosh + white flash into the full clip.

Implements wiki concepts/hook-engineering-2026-06 (the verified cold-open
finding): lead with a striking snippet so the viewer knows within ~2 s what
they'll get, but TEASE — do NOT spoil. So the teaser is cut from
[payoff - lead, payoff - tail], i.e. the build-up to the punchline, stopping
*before* the payoff lands (the resolution stays in the main clip).

Stage 7 calls this on the already-rendered clip when CLIP_COLD_OPEN is on. It
re-cuts the teaser from the source VOD (blur-fill 9:16 to match the clip canvas),
concatenates teaser + clip with a white-flash + whoosh seam, and writes a new
mp4. On ANY failure it exits non-zero and writes nothing, so Stage 7 keeps the
original clip untouched (failure-soft — never drops a clip).

Teaser length (lead/tail) is a heuristic default to A/B-test, not an
evidence-backed constant (the research gave opening *windows*, not a teaser
duration). Tune via --lead / --tail or CLIP_COLD_OPEN_LEAD / _TAIL.

CLI:
    python cold_open.py --vod <vod> --clip <rendered.mp4> --out <out.mp4>
        --payoff <abs_s> --clip-start <abs_s> --clip-duration <s>
        [--lead 1.5] [--tail 0.3]
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

try:
    import venc  # type: ignore
except Exception:
    venc = None

try:
    import sfx_inject as _sx  # type: ignore
except Exception:
    _sx = None


def _log(msg: str) -> None:
    print(f"[cold-open] {msg}", file=sys.stderr)


def _probe_duration(path: str) -> float | None:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=30,
        )
        return float(r.stdout.strip())
    except (subprocess.SubprocessError, ValueError):
        return None


def _venc_args() -> list[str]:
    if venc is not None:
        try:
            return list(venc.video_args(crf=20, preset_libx264="fast"))
        except Exception:
            pass
    return ["-c:v", "libx264", "-crf", "20", "-preset", "fast"]


# Blur-fill 9:16 to the 1080x1920 canvas — matches Stage 7 so the teaser and the
# main clip concat without letterbox/scale mismatches.
_TEASER_VF = (
    "split[bg][fg];"
    "[bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
    "boxblur=20:4[blurred];"
    "[fg]scale=1080:-2:force_original_aspect_ratio=decrease[sharp];"
    "[blurred][sharp]overlay=(W-w)/2:(H-h)/2,fps=30,format=yuv420p"
)


def _render_teaser(vod: str, start: float, dur: float, out: str) -> bool:
    cmd = [
        "ffmpeg", "-nostdin", "-y", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}",
        "-i", vod, "-vf", _TEASER_VF, "-r", "30",
        *_venc_args(),
        "-profile:v", "high", "-level", "4.2", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out,
    ]
    try:
        r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                           timeout=300, check=False)
    except subprocess.SubprocessError as e:
        _log(f"teaser render error: {e}")
        return False
    if r.returncode != 0:
        tail = (r.stderr or b"").decode("utf-8", "replace").splitlines()[-6:]
        _log("teaser ffmpeg failed:\n  " + "\n  ".join(tail))
        return False
    return Path(out).is_file() and Path(out).stat().st_size > 1024


def _concat(teaser: str, clip: str, out: str, seam: float, whoosh: str | None) -> bool:
    seam = max(0.0, float(seam))
    fa = max(0.0, seam - 0.06)
    fb = seam + 0.06
    fc = [
        "[0:v]scale=1080:1920,setsar=1,fps=30,format=yuv420p[tv]",
        "[1:v]scale=1080:1920,setsar=1,fps=30,format=yuv420p[mv]",
        "[0:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[ta]",
        "[1:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[ma]",
        "[tv][ta][mv][ma]concat=n=2:v=1:a=1[cv][ca]",
        # Transient white flash at the seam — drawbox+enable (NOT fade, which
        # would hold white outside its window; see BUG 64).
        f"[cv]drawbox=x=0:y=0:w=iw:h=ih:t=fill:color=white@0.5:"
        f"enable='between(t,{fa:.3f},{fb:.3f})'[vout]",
    ]
    inputs = ["-i", teaser, "-i", clip]
    if whoosh:
        inputs += ["-i", whoosh]
        wh_ms = int(round(max(0.0, seam - 0.15) * 1000))
        # normalize=0 sums inputs, so clip(~0.95) + whoosh would clip past 1.0
        # at the seam. Keep the clip at full level, drop the whoosh a touch, and
        # cap peaks with a true-peak limiter (only acts at the overlap, so the
        # rest of the clip audio is untouched — avoids quieting the whole clip).
        fc.append(f"[2:a]adelay={wh_ms}|{wh_ms},volume=0.5[wh]")
        fc.append("[ca][wh]amix=inputs=2:duration=first:dropout_transition=0:"
                  "normalize=0,alimiter=limit=0.95[aout]")
    else:
        fc.append("[ca]anull[aout]")
    cmd = [
        "ffmpeg", "-nostdin", "-y", *inputs,
        "-filter_complex", ";".join(fc),
        "-map", "[vout]", "-map", "[aout]",
        *_venc_args(),
        "-profile:v", "high", "-level", "4.2", "-pix_fmt", "yuv420p", "-r", "30",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out,
    ]
    try:
        r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                           timeout=600, check=False)
    except subprocess.SubprocessError as e:
        _log(f"concat error: {e}")
        return False
    if r.returncode != 0:
        tail = (r.stderr or b"").decode("utf-8", "replace").splitlines()[-8:]
        _log("concat ffmpeg failed:\n  " + "\n  ".join(tail))
        return False
    return Path(out).is_file() and Path(out).stat().st_size > 1024


def build(vod: str, clip: str, out: str, payoff: float, clip_start: float,
          clip_duration: float, lead: float = 1.5, tail: float = 0.3) -> bool:
    """Render teaser + concat. Returns True only when `out` is a valid new file."""
    if not (Path(vod).is_file() and Path(clip).is_file()):
        _log("vod or clip missing")
        return False
    clip_end = clip_start + clip_duration
    teaser_start = max(clip_start + 0.3, payoff - lead)
    teaser_end = min(payoff - tail, clip_end - 0.2)
    teaser_dur = teaser_end - teaser_start
    _log(f"teaser window: start={teaser_start:.2f}s end={teaser_end:.2f}s "
         f"dur={teaser_dur:.2f}s (payoff={payoff:.2f} lead={lead} tail={tail})")
    if teaser_dur < 0.6:
        _log("teaser window too short — skipping (tune --lead/--tail or "
             "CLIP_COLD_OPEN_LEAD/_TAIL; payoff may be too close to clip_start)")
        return False

    work = Path(out).parent
    teaser_tmp = str(work / (Path(out).stem + "_teaser.mp4"))
    try:
        if not _render_teaser(vod, teaser_start, teaser_dur, teaser_tmp):
            return False
        seam = _probe_duration(teaser_tmp) or teaser_dur
        whoosh = None
        if _sx is not None:
            try:
                w = _sx.pick_sfx("whoosh", seed=("coldopen", payoff))
                whoosh = str(w) if w else None
            except Exception:
                whoosh = None
        return _concat(teaser_tmp, clip, out, seam, whoosh)
    finally:
        try:
            if os.path.exists(teaser_tmp):
                os.remove(teaser_tmp)
        except OSError:
            pass


def _cli() -> int:
    ap = argparse.ArgumentParser(description="Prepend a cold-open teaser to a clip")
    ap.add_argument("--vod", required=True)
    ap.add_argument("--clip", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--payoff", type=float, required=True)
    ap.add_argument("--clip-start", type=float, required=True)
    ap.add_argument("--clip-duration", type=float, required=True)
    ap.add_argument("--lead", type=float,
                    default=float(os.environ.get("CLIP_COLD_OPEN_LEAD", "1.5") or "1.5"))
    ap.add_argument("--tail", type=float,
                    default=float(os.environ.get("CLIP_COLD_OPEN_TAIL", "0.3") or "0.3"))
    args = ap.parse_args()
    ok = build(args.vod, args.clip, args.out, args.payoff, args.clip_start,
               args.clip_duration, lead=args.lead, tail=args.tail)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(_cli())
