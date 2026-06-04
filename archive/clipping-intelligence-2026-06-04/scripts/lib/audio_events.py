#!/usr/bin/env python3
"""Audio-event detector — Tier-2 M2 of the moment-discovery upgrade.

Catches three classes of clip-worthy signal that are visible in the audio
waveform but invisible in the Whisper transcript:

- ``rhythmic_speech`` — freestyles, chants, song delivery. Detected via
  beat-alignment regularity from librosa onset detection.
- ``crowd_response`` — sudden RMS spike followed by sustained chatter
  spectrum (laughter/cheering signature).
- ``music_dominance`` — ratio of harmonic to percussive components from
  librosa HPSS. High = music playing, matters for tagging dance moments.

Output is a per-window JSON list aligned to Pass A's 30 s / 10 s window
grid so ``keyword_scan`` can do an O(1) lookup. Every signal is BOOST
ONLY — never gates a moment, only nudges its score.

Dependencies:
- ``librosa`` is gated behind ``ORIGINALITY_STACK`` in the Dockerfile and
  is already used by ``scan_music.py``. When it isn't importable, this
  module writes an empty events file and the caller no-ops.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

WINDOW_SIZE_DEFAULT = 30
STEP_DEFAULT = 10
SAMPLE_RATE = 22050  # matches scan_music.py for cache reuse opportunities


def _try_import_librosa():
    try:
        import librosa  # type: ignore
        import numpy as np  # type: ignore
        return librosa, np
    except ImportError:
        return None, None


def _detect_rhythmic_speech(y, sr, librosa, np) -> float:
    """Return 0.0-1.0 where 1.0 = strongly rhythmic (likely freestyle/song).

    Approach: librosa onset detection -> measure beat-alignment regularity
    by looking at the standard deviation of inter-onset intervals.  Tight
    regular intervals = rhythmic; jittery wide intervals = conversational.
    """
    try:
        onset_frames = librosa.onset.onset_detect(y=y, sr=sr, units="frames")
        if len(onset_frames) < 6:
            return 0.0
        onset_times = librosa.frames_to_time(onset_frames, sr=sr)
        intervals = np.diff(onset_times)
        if len(intervals) < 4:
            return 0.0
        # Normalize by the mean interval; coefficient of variation < ~0.4
        # implies tight rhythm.  Map CV in [0.15, 0.7] -> [1.0, 0.0].
        mean = float(np.mean(intervals))
        if mean <= 0:
            return 0.0
        cv = float(np.std(intervals) / mean)
        if cv <= 0.15:
            return 1.0
        if cv >= 0.7:
            return 0.0
        # Linear interpolation between the two thresholds.
        return float(max(0.0, min(1.0, (0.7 - cv) / (0.7 - 0.15))))
    except Exception:
        return 0.0


def _detect_crowd_response(y, sr, librosa, np) -> float:
    """Return 0.0-1.0 where 1.0 = clear crowd reaction (laughter/cheering).

    Approach: look for a sudden RMS spike in the second half of the window
    relative to the first half AND a high-frequency-energy ratio
    consistent with laughter (200-2000 Hz dominant).
    """
    try:
        # RMS spike detection: split window in halves, ratio of late vs early.
        rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=512)[0]
        if len(rms) < 4:
            return 0.0
        mid = len(rms) // 2
        early = float(np.mean(rms[:mid]) + 1e-9)
        late = float(np.mean(rms[mid:]) + 1e-9)
        spike_ratio = late / early
        # Laughter spectrum check: zero-crossing rate elevated in laughter.
        zcr = librosa.feature.zero_crossing_rate(y, frame_length=2048, hop_length=512)[0]
        zcr_late = float(np.mean(zcr[mid:])) if len(zcr) >= 2 else 0.0
        # Combined score: spike_ratio in [1.3, 3.0] -> [0.3, 1.0],
        # weighted by zcr_late (typical laughter > 0.06).
        if spike_ratio < 1.3 or zcr_late < 0.04:
            return 0.0
        spike_norm = max(0.0, min(1.0, (spike_ratio - 1.3) / (3.0 - 1.3)))
        zcr_norm = max(0.0, min(1.0, (zcr_late - 0.04) / (0.10 - 0.04)))
        return float(0.6 * spike_norm + 0.4 * zcr_norm)
    except Exception:
        return 0.0


def _detect_music_dominance(y, sr, librosa, np) -> float:
    """Return 0.0-1.0 where 1.0 = music dominates the window.

    Approach: HPSS (harmonic/percussive source separation) ratio. Music
    has both strong harmonic AND percussive components; speech is mostly
    harmonic with little percussion.
    """
    try:
        y_h, y_p = librosa.effects.hpss(y, margin=1.0)
        h_energy = float(np.sum(y_h ** 2) + 1e-9)
        p_energy = float(np.sum(y_p ** 2) + 1e-9)
        # Music: percussive/total > ~0.25; pure speech < 0.10.
        ratio = p_energy / (h_energy + p_energy)
        if ratio <= 0.10:
            return 0.0
        if ratio >= 0.40:
            return 1.0
        return float((ratio - 0.10) / (0.40 - 0.10))
    except Exception:
        return 0.0


_ZERO_RESULT = {
    "rhythmic_speech": 0.0,
    "crowd_response": 0.0,
    "music_dominance": 0.0,
}


def _run_detectors(y, sr, librosa, np) -> Dict[str, float]:
    """Run all three detectors on a pre-loaded waveform. Internal helper —
    keeps the per-window cost to STFT/feature ops only (no file I/O)."""
    if y is None or len(y) < sr // 2:
        return dict(_ZERO_RESULT)
    return {
        "rhythmic_speech": round(_detect_rhythmic_speech(y, sr, librosa, np), 3),
        "crowd_response": round(_detect_crowd_response(y, sr, librosa, np), 3),
        "music_dominance": round(_detect_music_dominance(y, sr, librosa, np), 3),
    }


def detect_window(
    audio_path: str,
    start_s: float,
    end_s: float,
    librosa=None,
    np=None,
) -> Dict[str, float]:
    """Run all three detectors on ``[start_s, end_s)``. Loads the slice
    via librosa each call — convenient for ad-hoc inspection but DO NOT
    use in a hot loop (file-open overhead per window kills throughput on
    long VODs). ``scan_audio_events`` uses the in-memory fast path."""
    if librosa is None:
        librosa, np = _try_import_librosa()
        if librosa is None:
            return dict(_ZERO_RESULT)
    duration = max(0.5, end_s - start_s)
    try:
        y, sr = librosa.load(
            audio_path, sr=SAMPLE_RATE, mono=True,
            offset=float(start_s), duration=float(duration),
        )
    except Exception:
        return dict(_ZERO_RESULT)
    return _run_detectors(y, sr, librosa, np)


def scan_audio_events(
    audio_path: str,
    out_path: str,
    window_size: int = WINDOW_SIZE_DEFAULT,
    step: int = STEP_DEFAULT,
    duration_hint: Optional[float] = None,
    progress_every: int = 100,
) -> Dict[str, Any]:
    """Slide ``window_size`` / ``step`` windows across ``audio_path`` and
    write per-window detector outputs to ``out_path`` as JSON.

    Loads the audio file ONCE up-front and slices in-memory per window.
    The previous approach (``librosa.load`` per window) re-opened the
    audio file ~1160 times for a 3-hour VOD — minutes of wall time
    spent on file I/O instead of feature computation.

    Memory budget: a 4-hour 22050 Hz mono float32 buffer is ~1.3 GB. On
    extremely long VODs (>~6 hours) this can OOM; we catch MemoryError
    and write an empty events file so the pipeline degrades cleanly.

    Progress is logged every ``progress_every`` windows with an explicit
    ``stderr.flush()`` so callers see updates in real time even when
    stderr is block-buffered through a pipe.
    """
    librosa, np = _try_import_librosa()
    if librosa is None:
        print(
            "[AUDIO_EVENTS] librosa not available — writing empty events file",
            file=sys.stderr,
        )
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_text(
            json.dumps({"windows": [], "skipped_reason": "librosa_missing"}),
            encoding="utf-8",
        )
        return {"windows": 0, "backend": "none"}

    # Resolve total duration so we can stop the slide at end-of-audio.
    if duration_hint is None:
        try:
            duration_hint = float(librosa.get_duration(path=audio_path))
        except Exception:
            try:
                duration_hint = float(librosa.get_duration(filename=audio_path))
            except Exception:
                duration_hint = 0.0
    if not duration_hint or duration_hint <= 0:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_text(
            json.dumps({"windows": [], "skipped_reason": "zero_duration"}),
            encoding="utf-8",
        )
        return {"windows": 0, "backend": "librosa"}

    # Load the entire audio in one shot — far faster than ~N opens of the
    # file for the same total bytes. Falls through to an empty events
    # file on MemoryError or any other load failure.
    n_total_estimate = max(1, int((duration_hint - window_size) / step) + 1)
    print(
        f"[AUDIO_EVENTS] loading {duration_hint:.0f}s of audio into memory "
        f"for {n_total_estimate} windows (this takes 5-30s on long VODs)...",
        file=sys.stderr,
    )
    sys.stderr.flush()
    t_load = time.time()
    try:
        y_full, sr = librosa.load(audio_path, sr=SAMPLE_RATE, mono=True)
    except MemoryError:
        print(
            "[AUDIO_EVENTS] OOM loading full audio (VOD too long for in-memory scan); "
            "writing empty events file. Pass A will run without audio signals.",
            file=sys.stderr,
        )
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_text(
            json.dumps({"windows": [], "skipped_reason": "load_oom"}),
            encoding="utf-8",
        )
        return {"windows": 0, "backend": "librosa"}
    except Exception as e:
        print(
            f"[AUDIO_EVENTS] failed to load audio ({e}); writing empty events file",
            file=sys.stderr,
        )
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_text(
            json.dumps({"windows": [], "skipped_reason": "load_failed"}),
            encoding="utf-8",
        )
        return {"windows": 0, "backend": "librosa"}
    print(
        f"[AUDIO_EVENTS] loaded {len(y_full)/sr:.0f}s in {time.time()-t_load:.1f}s "
        f"({len(y_full) * 4 // (1024*1024)} MB), scanning windows...",
        file=sys.stderr,
    )
    sys.stderr.flush()

    windows: List[Dict[str, Any]] = []
    n_fired_rhythmic = 0
    n_fired_crowd = 0
    n_fired_music = 0
    t = 0.0
    t_scan = time.time()
    while t < duration_hint:
        end_t = min(duration_hint, t + window_size)
        if end_t - t < 5.0:  # avoid tiny tail windows
            break
        s_idx = int(t * sr)
        e_idx = min(int(end_t * sr), len(y_full))
        y = y_full[s_idx:e_idx]
        result = _run_detectors(y, sr, librosa, np)
        windows.append({
            "start": round(t, 1),
            "end": round(end_t, 1),
            **result,
        })
        if result["rhythmic_speech"] >= 0.7:
            n_fired_rhythmic += 1
        if result["crowd_response"] >= 0.5:
            n_fired_crowd += 1
        if result["music_dominance"] >= 0.6:
            n_fired_music += 1
        if progress_every > 0 and len(windows) % progress_every == 0:
            elapsed = time.time() - t_scan
            rate = len(windows) / elapsed if elapsed > 0 else 0.0
            remaining = max(0, n_total_estimate - len(windows))
            eta = remaining / rate if rate > 0 else 0.0
            print(
                f"[AUDIO_EVENTS] {len(windows)}/~{n_total_estimate} windows "
                f"({elapsed:.0f}s elapsed, ~{eta:.0f}s remaining, "
                f"{rate:.1f} win/s)",
                file=sys.stderr,
            )
            sys.stderr.flush()
        t += step

    # Free the full waveform before writing — small but courteous on
    # tight-RAM hosts where Stage 3+ are about to load the LLM.
    del y_full

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps({
        "windows": windows,
        "window_size": window_size,
        "step": step,
        "duration": round(duration_hint, 1),
        "backend": "librosa",
    }), encoding="utf-8")
    print(
        f"[AUDIO_EVENTS] scanned {len(windows)} windows of {window_size}s in "
        f"{time.time()-t_scan:.1f}s (rhythmic_fires={n_fired_rhythmic} "
        f"crowd_fires={n_fired_crowd} music_fires={n_fired_music})",
        file=sys.stderr,
    )
    return {
        "windows": len(windows),
        "backend": "librosa",
        "rhythmic_fires": n_fired_rhythmic,
        "crowd_fires": n_fired_crowd,
        "music_fires": n_fired_music,
    }


def load_events(path: str) -> Dict[Tuple[float, float], Dict[str, float]]:
    """Helper for keyword_scan: load events as a dict keyed by (start, end)
    so a Pass A window can do an O(1) lookup. Returns {} on missing file or
    parse error so callers fall through to the no-events behavior."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}
    out: Dict[Tuple[float, float], Dict[str, float]] = {}
    for w in (data.get("windows") or []):
        try:
            s = float(w["start"])
            e = float(w["end"])
        except (KeyError, TypeError, ValueError):
            continue
        out[(s, e)] = {
            "rhythmic_speech": float(w.get("rhythmic_speech", 0.0)),
            "crowd_response": float(w.get("crowd_response", 0.0)),
            "music_dominance": float(w.get("music_dominance", 0.0)),
        }
    return out


def lookup_window(
    events: Dict[Tuple[float, float], Dict[str, float]],
    window_start: float,
    window_size: float = WINDOW_SIZE_DEFAULT,
) -> Dict[str, float]:
    """Find the best-matching scanned window for a Pass A window. Pass A
    uses the same 30 s / 10 s grid we scanned, so an exact match is the
    common case; the nearest-start fallback covers timeline edges."""
    target_end = window_start + window_size
    direct = events.get((round(window_start, 1), round(target_end, 1)))
    if direct is not None:
        return direct
    # Fallback: nearest start within +/- step. Avoids missing the window
    # when grids drift by 0.1 s due to rounding.
    best_key = None
    best_dist = 999.0
    for k in events.keys():
        d = abs(k[0] - window_start)
        if d < best_dist and d <= STEP_DEFAULT:
            best_dist = d
            best_key = k
    if best_key is None:
        return {"rhythmic_speech": 0.0, "crowd_response": 0.0, "music_dominance": 0.0}
    return events[best_key]


def _cli() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="Audio-event detector (Tier-2 M2)")
    ap.add_argument("--audio", required=True, help="Path to 16kHz mono WAV (or any librosa-readable file)")
    ap.add_argument("--out", required=True, help="Path to write events JSON")
    ap.add_argument("--window", type=int, default=WINDOW_SIZE_DEFAULT, help="Window size (sec)")
    ap.add_argument("--step", type=int, default=STEP_DEFAULT, help="Slide step (sec)")
    ap.add_argument("--duration", type=float, default=None, help="Audio duration hint (sec)")
    args = ap.parse_args()
    summary = scan_audio_events(
        args.audio, args.out,
        window_size=args.window, step=args.step,
        duration_hint=args.duration,
    )
    json.dump(summary, sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    _cli()
