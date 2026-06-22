#!/usr/bin/env python3
"""visual_sense.py — visual edit decomposition for clip-forensics (Phase 3).

The visual half of "clip essence" (concepts/clip-forensics-research-2026-06):
  - motion_events(): camera/edit motion punches (zoom, shake, fast action) via
    OpenCV frame-difference energy. No model, no download — cv2 is already present
    (PySceneDetect pulls it). Cheap; safe to run by default.
  - caption_ocr(): burned-in caption text + words/sec via EasyOCR. OPT-IN — it
    downloads ~75 MB of detector/recogniser weights on first use and is slower, so
    the forensics CLI gates it behind --ocr. CPU by default (Windows CUDA can hang
    model loads, same as the audio backends).

Mirrors audio_sense.py conventions: lazy imports, failure-soft (each backend → []
/ {} + a stderr note on a missing dep/model, never raises), offline research lane.
"""
from __future__ import annotations

import sys


def _log(msg: str) -> None:
    print(f"[visual_sense] {msg}", file=sys.stderr)


def motion_events(media: str, *, sample_fps: float = 8.0, width: int = 160,
                  z: float = 3.0, min_gap_s: float = 0.3,
                  max_frames: int = 6000) -> list[dict]:
    """Motion/edit punches as timestamps where frame-to-frame change spikes.

    Samples ~sample_fps, downscales to `width` px grayscale, takes mean abs diff
    between consecutive samples (a cheap proxy for cut/zoom/shake energy), then
    emits points whose energy exceeds a robust threshold (median + z*MAD). A spike
    that lands on a scene cut is a hard transition; one that doesn't is camera
    motion / zoom-punch — the caller can correlate against `cuts`. [] on failure.
    """
    try:
        import cv2
        import numpy as np
    except Exception as e:  # pragma: no cover
        _log(f"cv2/numpy unavailable ({type(e).__name__}); motion=[]")
        return []
    cap = None
    try:
        cap = cv2.VideoCapture(media)
        if not cap.isOpened():
            _log("cv2 could not open clip; motion=[]")
            return []
        src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        if src_fps <= 0:
            src_fps = 30.0
        step = max(1, int(round(src_fps / max(0.5, sample_fps))))
        prev = None
        ts: list[float] = []
        energy: list[float] = []
        idx = 0
        read = 0
        while read < max_frames:
            ok = cap.grab()
            if not ok:
                break
            if idx % step == 0:
                ok, frame = cap.retrieve()
                if not ok or frame is None:
                    break
                read += 1
                h, w = frame.shape[:2]
                if w > width:
                    frame = cv2.resize(frame, (width, max(1, int(h * width / w))))
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                if prev is not None:
                    d = float(np.mean(cv2.absdiff(gray, prev)))
                    ts.append(round(idx / src_fps, 3))
                    energy.append(d)
                prev = gray
            idx += 1
        if len(energy) < 3:
            return []
        arr = np.asarray(energy, dtype="float32")
        med = float(np.median(arr))
        mad = float(np.median(np.abs(arr - med))) or 1.0
        thr = med + z * 1.4826 * mad  # 1.4826: MAD→sigma for normal data
        out: list[dict] = []
        last = -10.0
        for t, e in zip(ts, energy):
            if e >= thr and (t - last) >= min_gap_s:
                out.append({"t": t, "kind": "motion",
                            "energy": round(e, 2),
                            "rel": round(e / (med or 1.0), 2)})
                last = t
        return out
    except Exception as e:
        _log(f"motion_events failed ({type(e).__name__}: {e}); []")
        return []
    finally:
        try:
            if cap is not None:
                cap.release()
        except Exception:
            pass


def caption_ocr(media: str, *, sample_fps: float = 2.0, max_frames: int = 120,
                langs: tuple[str, ...] = ("en",), gpu: bool = False,
                min_conf: float = 0.4, band: float = 0.0) -> dict:
    """Burned-in caption OCR via EasyOCR (OPT-IN — downloads ~75 MB on first use).

    Samples frames, OCRs each, keeps detections above `min_conf`, and returns a
    summary: per-sample text + a words/sec estimate (informs the 5–10 wps caption
    pacing anchor in concepts/captions). `band` (0..1) optionally restricts OCR to
    the lower fraction of the frame; 0 = whole frame (TikTok captions float).
    Failure-soft: returns {"available": False, ...} if EasyOCR/model is missing.
    """
    try:
        import cv2
        import easyocr  # noqa: F401
        import numpy as np  # noqa: F401
    except Exception as e:
        _log(f"easyocr/cv2 unavailable ({type(e).__name__}); captions skipped "
             f"(pip install easyocr; CLIP_FORENSICS_OCR=1 to enable)")
        return {"available": False, "reason": f"{type(e).__name__}"}
    cap = None
    try:
        import easyocr
        reader = easyocr.Reader(list(langs), gpu=gpu, verbose=False)
        cap = cv2.VideoCapture(media)
        if not cap.isOpened():
            return {"available": False, "reason": "cv2_open_failed"}
        src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        if src_fps <= 0:
            src_fps = 30.0
        step = max(1, int(round(src_fps / max(0.5, sample_fps))))
        samples: list[dict] = []
        total_words = 0
        idx = 0
        taken = 0
        while taken < max_frames:
            ok = cap.grab()
            if not ok:
                break
            if idx % step == 0:
                ok, frame = cap.retrieve()
                if not ok or frame is None:
                    break
                taken += 1
                if band and 0.0 < band < 1.0:
                    h = frame.shape[0]
                    frame = frame[int(h * (1.0 - band)):, :]
                try:
                    res = reader.readtext(frame, detail=1, paragraph=False)
                except Exception:
                    res = []
                texts = [str(t).strip() for (_b, t, c) in res if float(c) >= min_conf and str(t).strip()]
                if texts:
                    joined = " ".join(texts)
                    nw = len(joined.split())
                    total_words += nw
                    samples.append({"t": round(idx / src_fps, 3), "text": joined, "n_words": nw})
            idx += 1
        dur = idx / src_fps if idx else 0.0
        wps = round(total_words / dur, 2) if dur > 0 else None
        return {"available": True, "n_text_frames": len(samples),
                "total_words": total_words, "words_per_s": wps,
                "samples": samples}
    except Exception as e:
        _log(f"caption_ocr failed ({type(e).__name__}: {e}); skipped")
        return {"available": False, "reason": f"{type(e).__name__}"}
    finally:
        try:
            if cap is not None:
                cap.release()
        except Exception:
            pass


if __name__ == "__main__":  # tiny manual smoke: python visual_sense.py clip.mp4
    if len(sys.argv) > 1:
        m = motion_events(sys.argv[1])
        print(f"motion: {len(m)} spikes; first 5: {m[:5]}")
