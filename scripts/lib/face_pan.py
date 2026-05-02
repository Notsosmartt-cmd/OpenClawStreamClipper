#!/usr/bin/env python3
"""Face-tracking camera-pan helper for Wave E.

Two subcommands:

  --prepare --vod PATH --start SEC --duration SEC --out FILE
      Sample frames from the clip window, detect faces (OpenCV Haar),
      build a smoothed virtual-camera crop path, and write it as JSON.
      Intended to be called once per candidate clip when CLIP_CAMERA_PAN=true.

  --emit-filter FILE
      Read the JSON produced by --prepare and emit an FFmpeg crop filter
      expression that encodes the camera path. Stage 7 interpolates this
      into its filter_complex graph.

Design choices:

- OpenCV Haar cascade is used rather than DNN/MTCNN because it ships inside
  opencv-python (no model download) and is fast on CPU.
- Diarization is NOT required. When there are multiple faces visible the
  tracker cycles between them on a timer (roughly every 3-6 s), which is
  enough to break per-frame visual hashing without needing TalkNet/pyannote.
  If you want true active-speaker detection, extend `pick_target_face`.
- Fallback: if zero faces are detected across the clip, the caller sees an
  empty filter and falls back to blur_fill automatically.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

try:
    import cv2
except ImportError:
    cv2 = None  # type: ignore


TARGET_W = 608    # 9:16 slice at 1080 tall source (608/1080 ≈ 0.5625)
TARGET_H = 1080
SAMPLE_FPS = 2.0  # 2 samples per second
SMOOTH_ALPHA = 0.30  # EMA factor; lower = smoother pan
SPEAKER_CYCLE_SEC = 4.0  # cycle between multiple faces every N sec


@dataclass
class Keyframe:
    t: float           # seconds from clip start
    x: int             # crop box left
    y: int             # crop box top
    w: int             # crop box width
    h: int             # crop box height


def load_detector():
    cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"  # type: ignore
    if not os.path.isfile(cascade_path):
        return None
    return cv2.CascadeClassifier(cascade_path)


def detect_faces(detector, frame_bgr):
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.equalizeHist(gray)
    faces = detector.detectMultiScale(
        gray,
        scaleFactor=1.2,
        minNeighbors=4,
        minSize=(60, 60),
    )
    # Return as list of (x, y, w, h)
    return list(faces)


def pick_target_face(faces, prev_center, cycle_idx: int):
    """Pick the face to follow for this sample.

    Strategy:
      - When multiple faces, prefer the one closest to `prev_center` for
        continuity UNLESS we've held on the same face for too long, in
        which case cycle to the next largest.
      - When one face, use it.
      - When no faces, return None.
    """
    if not len(faces):
        return None
    if len(faces) == 1 or prev_center is None:
        fx, fy, fw, fh = max(faces, key=lambda f: f[2] * f[3])
        return fx + fw / 2.0, fy + fh / 2.0, fw, fh

    # Multiple faces: cycle periodically, else stay near prev_center.
    faces_sorted = sorted(faces, key=lambda f: f[2] * f[3], reverse=True)
    if cycle_idx and cycle_idx % max(1, int(SPEAKER_CYCLE_SEC * SAMPLE_FPS)) == 0:
        fx, fy, fw, fh = faces_sorted[min(len(faces_sorted) - 1,
                                          (cycle_idx // int(SPEAKER_CYCLE_SEC * SAMPLE_FPS)) % len(faces_sorted))]
    else:
        fx, fy, fw, fh = min(
            faces_sorted[:3],  # pick from top 3 biggest faces
            key=lambda f: (f[0] + f[2] / 2.0 - prev_center[0]) ** 2
                          + (f[1] + f[3] / 2.0 - prev_center[1]) ** 2,
        )
    return fx + fw / 2.0, fy + fh / 2.0, fw, fh


def clamp_crop(cx: float, cy: float, frame_w: int, frame_h: int,
               crop_w: int, crop_h: int) -> tuple[int, int]:
    """Center the crop on (cx, cy), clamped to frame bounds. Returns top-left."""
    x = int(round(cx - crop_w / 2.0))
    y = int(round(cy - crop_h / 2.0))
    x = max(0, min(frame_w - crop_w, x))
    y = max(0, min(frame_h - crop_h, y))
    return x, y


def prepare(vod_path: str, clip_start: float, duration: float, out_path: str) -> int:
    if cv2 is None:
        print("opencv not available — skipping face-pan prep", file=sys.stderr)
        return 1

    detector = load_detector()
    if detector is None:
        print("haar cascade not found — skipping face-pan prep", file=sys.stderr)
        return 1

    cap = cv2.VideoCapture(vod_path)
    if not cap.isOpened():
        print(f"cannot open vod {vod_path}", file=sys.stderr)
        return 1

    frame_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 1920
    frame_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 1080

    # Source 9:16 crop — if source is portrait already, skip (nothing to do)
    if frame_w <= frame_h:
        print("source is already portrait — no camera pan needed", file=sys.stderr)
        cap.release()
        return 2

    crop_w = min(TARGET_W, frame_w)
    crop_h = min(TARGET_H, frame_h)
    # Ensure even for libx264
    crop_w -= crop_w % 2
    crop_h -= crop_h % 2

    sample_interval = 1.0 / SAMPLE_FPS
    n_samples = max(2, int(duration * SAMPLE_FPS))

    keyframes: list[Keyframe] = []
    prev_center: tuple[float, float] | None = None
    smooth_x = frame_w / 2.0
    smooth_y = frame_h / 2.0
    cycle_idx = 0
    faces_seen = 0

    for i in range(n_samples):
        t = i * sample_interval
        cap.set(cv2.CAP_PROP_POS_MSEC, (clip_start + t) * 1000.0)
        ok, frame = cap.read()
        if not ok or frame is None:
            continue
        faces = detect_faces(detector, frame)
        if faces:
            faces_seen += 1
        target = pick_target_face(faces, prev_center, cycle_idx)
        cycle_idx += 1

        if target is not None:
            cx, cy, fw, fh = target
            prev_center = (cx, cy)
            smooth_x = SMOOTH_ALPHA * cx + (1 - SMOOTH_ALPHA) * smooth_x
            smooth_y = SMOOTH_ALPHA * cy + (1 - SMOOTH_ALPHA) * smooth_y
        # when no face: keep smoothed x/y from last detection (hold shot)

        x, y = clamp_crop(smooth_x, smooth_y, frame_w, frame_h, crop_w, crop_h)
        keyframes.append(Keyframe(t=round(t, 2), x=x, y=y, w=crop_w, h=crop_h))

    cap.release()

    if faces_seen == 0:
        # No faces across the entire window — give up, let caller fall back.
        print("no faces detected in clip window — no pan path emitted", file=sys.stderr)
        return 3

    payload = {
        "vod": str(vod_path),
        "clip_start": clip_start,
        "duration": duration,
        "frame_w": frame_w,
        "frame_h": frame_h,
        "keyframes": [asdict(k) for k in keyframes],
    }
    Path(out_path).write_text(json.dumps(payload), encoding="utf-8")
    print(f"wrote {len(keyframes)} keyframe(s) to {out_path} "
          f"(faces in {faces_seen}/{n_samples} samples)", file=sys.stderr)
    return 0


def emit_filter(campath_json: str) -> int:
    """Emit an FFmpeg crop+scale filter chain encoding the keyframe path.

    The crop filter accepts expressions in `x` and `y` using the `t` variable.
    We build a piecewise-linear interpolation: `if(lt(t,T0), x0, if(lt(t,T1),
    lerp(x0,x1,...), ...))`. For clarity and performance we cap the number of
    segments to ~40 by resampling keyframes when needed.
    """
    try:
        data = json.loads(Path(campath_json).read_text(encoding="utf-8"))
    except Exception as e:
        print(f"unreadable campath: {e}", file=sys.stderr)
        return 1

    kfs = data.get("keyframes") or []
    if not kfs:
        return 1

    # Resample to at most 32 anchor points to keep the expression manageable
    if len(kfs) > 32:
        step = len(kfs) / 32
        resampled = []
        i = 0.0
        while i < len(kfs):
            resampled.append(kfs[int(i)])
            i += step
        kfs = resampled

    crop_w = kfs[0]["w"]
    crop_h = kfs[0]["h"]

    def build_expr(field: str) -> str:
        # Nested if() expressions: if(lt(t,T1), v0+(v1-v0)*(t-T0)/(T1-T0), ...next)
        out = f"{kfs[-1][field]}"  # fallback for t >= last
        for i in range(len(kfs) - 1, 0, -1):
            a, b = kfs[i - 1], kfs[i]
            ta, tb = a["t"], b["t"]
            va, vb = a[field], b[field]
            dt = max(0.01, tb - ta)
            out = (f"if(lt(t,{tb:.2f}),"
                   f"{va}+({vb}-{va})*(t-{ta:.2f})/{dt:.2f},"
                   f"{out})")
        return out

    x_expr = build_expr("x")
    y_expr = build_expr("y")

    # Output the FFmpeg filter chain:
    # crop=W:H:x=expr:y=expr,scale=1080:1920:flags=lanczos
    chain = (f"crop=w={crop_w}:h={crop_h}:x='{x_expr}':y='{y_expr}',"
             f"scale=1080:1920:flags=lanczos")
    print(chain)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="mode", required=True)

    prep = sub.add_parser("prepare")
    prep.add_argument("--vod", required=True)
    prep.add_argument("--start", type=float, required=True)
    prep.add_argument("--duration", type=float, required=True)
    prep.add_argument("--out", required=True)

    emit = sub.add_parser("emit-filter")
    emit.add_argument("path")

    # Also accept --prepare/--emit-filter as positional flags for convenience
    args, extras = parser.parse_known_args()

    if args.mode == "prepare":
        return prepare(args.vod, args.start, args.duration, args.out)
    if args.mode == "emit-filter":
        return emit_filter(args.path)
    return 2


def _entry() -> int:
    """Accept both ``face_pan.py prepare ...`` and ``face_pan.py --emit-filter PATH``.

    The Stage 7 bash caller invokes ``face_pan.py --emit-filter <path>`` so
    we intercept that syntax here for backwards-friendly CLI ergonomics.
    """
    argv = sys.argv[1:]
    if argv and argv[0] == "--emit-filter" and len(argv) >= 2:
        return emit_filter(argv[1])
    if argv and argv[0] == "--prepare":
        # argparse path for full prepare invocation
        sys.argv = [sys.argv[0], "prepare"] + argv[1:]
        return main()
    return main()


if __name__ == "__main__":
    sys.exit(_entry())
