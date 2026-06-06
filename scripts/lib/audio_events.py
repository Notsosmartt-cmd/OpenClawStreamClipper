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

# Multiprocessing tuning. Parallel scan loads ~975 MB of audio into a
# shared-memory buffer and fans the per-window HPSS work out across N workers
# on i9-13900K-class hardware. Each window is ~700 ms of single-thread HPSS;
# 8 workers gives a sustained ~6-8x throughput lift over the serial path.
PARALLEL_MIN_WINDOWS = 20      # below this, serial is faster (worker spawn cost dominates)
PARALLEL_DEFAULT_CAP = 8       # cap workers so we don't starve the rest of the pipeline
PARALLEL_CHUNKSIZE = 4         # imap chunksize — amortizes per-task IPC overhead

# Per-worker module-global stash. Each Pool worker is a separate Python
# process (spawn on Windows); it imports librosa once during _worker_init
# and binds the shared audio buffer + sample rate here so _worker_run
# can complete a window without re-importing or re-attaching.
_WORKER_STATE: Dict[str, Any] = {}


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


# RMS energy threshold below which a window is considered silent enough
# that running the (expensive) HPSS-driven detectors is wasted work. The
# value is conservative — a normal speech window sits around 0.05-0.15,
# music around 0.10-0.30, and complete silence near 0.001. 0.01 catches
# dead-air / fade-to-black / "music between segments" with no false
# positives on real speech. Tuneable via ``AUDIO_EVENTS_RMS_GATE`` env var.
_RMS_GATE_DEFAULT = 0.01


def _rms_below_gate(y, np, gate: float) -> bool:
    """Cheap energy check (one numpy reduction) used to early-exit before
    invoking HPSS on a silent window. Returns True if the window is too
    quiet to plausibly contain a clip-worthy signal."""
    try:
        rms = float(np.sqrt(np.mean(np.square(y, dtype=np.float64))))
    except Exception:
        return False
    return rms < gate


def _resolve_rms_gate() -> float:
    """``AUDIO_EVENTS_RMS_GATE`` env override → default. Set to ``0`` to
    disable the gate entirely (run HPSS on every window like before)."""
    env = os.environ.get("AUDIO_EVENTS_RMS_GATE", "").strip()
    if env:
        try:
            v = float(env)
            if v >= 0:
                return v
        except ValueError:
            pass
    return _RMS_GATE_DEFAULT


def _run_detectors(y, sr, librosa, np, rms_gate: Optional[float] = None) -> Dict[str, float]:
    """Run all three detectors on a pre-loaded waveform. Internal helper —
    keeps the per-window cost to STFT/feature ops only (no file I/O).

    Early-exits to a zero result on silent windows (saves the ~700 ms HPSS
    cost on dead-air / fade-to-black / between-segment silences). Pass
    ``rms_gate=0`` to disable the gate; defaults to the module-level
    ``_resolve_rms_gate()`` value (env-overridable).
    """
    if y is None or len(y) < sr // 2:
        return dict(_ZERO_RESULT)
    if rms_gate is None:
        rms_gate = _resolve_rms_gate()
    if rms_gate > 0 and _rms_below_gate(y, np, rms_gate):
        return dict(_ZERO_RESULT)
    return {
        "rhythmic_speech": round(_detect_rhythmic_speech(y, sr, librosa, np), 3),
        "crowd_response": round(_detect_crowd_response(y, sr, librosa, np), 3),
        "music_dominance": round(_detect_music_dominance(y, sr, librosa, np), 3),
    }


# ---------------------------------------------------------------------------
# Multiprocessing worker plumbing (parallel scan path)
# ---------------------------------------------------------------------------

def _worker_init(shm_name: str, shm_shape, shm_dtype: str, sr: int) -> None:
    """Pool initializer: each worker process imports librosa once, attaches
    to the parent's shared audio buffer (no copy), and binds the result into
    a module-global stash that ``_worker_run`` reads from.

    On Windows (spawn), this runs inside a freshly-imported copy of this
    module — so the librosa import cost (1-3 s) hits once per worker at pool
    startup, not once per window. Amortized over hundreds of windows the
    spawn cost is negligible vs the HPSS savings.

    2026-06-05 BLAS-thread-pinning fix: ``librosa.effects.hpss()`` uses
    STFT which calls NumPy → OpenBLAS/MKL. Each worker's BLAS library
    defaults to spawning ``cpu_count`` threads. On a 24-core CPU running
    8 workers, that's 8 × 24 = 192 BLAS threads competing for 24 cores —
    massive context-switching kills the parallel speedup. Measured before
    fix: 1.7 win/s with 8 workers (only 2.1× over the 0.8 win/s serial
    baseline) instead of the expected 6-8×. Setting OMP/BLAS thread vars
    to 1 BEFORE NumPy imports (they're cached once numpy.linalg loads)
    gives each worker a clean single-threaded BLAS so the 8 processes
    actually run in parallel on dedicated cores.
    """
    # CRITICAL: set BLAS thread vars BEFORE importing numpy/scipy/librosa.
    # NumPy reads these the first time it imports its BLAS backend; later
    # changes are ignored. ``setdefault`` so an operator who wants more
    # threads per worker (e.g. running 4 workers on a 24-core box) can
    # override via env. Five vars cover the major BLAS implementations.
    _blas_vars = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS",
                  "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS",
                  "VECLIB_MAXIMUM_THREADS")
    for _v in _blas_vars:
        os.environ.setdefault(_v, "1")

    import numpy as np  # type: ignore
    from multiprocessing import shared_memory as _shm_mod

    librosa, _ = _try_import_librosa()
    if librosa is None:
        # If librosa isn't importable in the worker, mark the state empty;
        # _worker_run will fall back to zero-result on every task.
        _WORKER_STATE["librosa"] = None
        _WORKER_STATE["np"] = np
        return

    shm = _shm_mod.SharedMemory(name=shm_name)
    y_full = np.ndarray(shm_shape, dtype=np.dtype(shm_dtype), buffer=shm.buf)

    _WORKER_STATE["librosa"] = librosa
    _WORKER_STATE["np"] = np
    _WORKER_STATE["y_full"] = y_full
    _WORKER_STATE["sr"] = int(sr)
    # MUST keep the SharedMemory handle alive — closing it while ``y_full``
    # still references the buffer is a use-after-free at the C level.
    _WORKER_STATE["shm"] = shm


def _worker_run(task: Tuple[int, int, float, float]) -> Dict[str, Any]:
    """Worker entry point: process one window and return the JSON-ready dict.

    Identical schema to the serial path so the caller can splice results
    back into the windows list unchanged.
    """
    s_idx, e_idx, t_start, t_end = task
    librosa = _WORKER_STATE.get("librosa")
    np = _WORKER_STATE["np"]
    if librosa is None or "y_full" not in _WORKER_STATE:
        return {
            "start": round(t_start, 1),
            "end": round(t_end, 1),
            **_ZERO_RESULT,
        }
    y_full = _WORKER_STATE["y_full"]
    sr = _WORKER_STATE["sr"]
    y = y_full[s_idx:e_idx]
    result = _run_detectors(y, sr, librosa, np)
    return {
        "start": round(t_start, 1),
        "end": round(t_end, 1),
        **result,
    }


def _resolve_worker_count(n_workers: Optional[int]) -> int:
    """Decide how many workers to spin up. Priority order:

    1. Explicit ``n_workers`` argument (CLI ``--workers`` or call site)
    2. ``AUDIO_EVENTS_WORKERS`` env var
    3. Auto: ``min(PARALLEL_DEFAULT_CAP, os.cpu_count() - 2)``, floored at 1
    """
    if n_workers is not None and n_workers > 0:
        return int(n_workers)
    env = os.environ.get("AUDIO_EVENTS_WORKERS", "").strip()
    if env:
        try:
            v = int(env)
            if v > 0:
                return v
        except ValueError:
            pass
    cpu = os.cpu_count() or 4
    return max(1, min(PARALLEL_DEFAULT_CAP, cpu - 2))


def _build_window_tasks(
    duration_hint: float,
    window_size: int,
    step: int,
    sr: int,
    n_samples: int,
) -> List[Tuple[int, int, float, float]]:
    """Pre-compute the (start_idx, end_idx, t_start, t_end) tuples that
    define every window the scan will process. Shared between serial and
    parallel code paths so they can't drift on window boundaries."""
    tasks: List[Tuple[int, int, float, float]] = []
    t = 0.0
    while t < duration_hint:
        end_t = min(duration_hint, t + window_size)
        if end_t - t < 5.0:  # skip tiny tail windows
            break
        s_idx = int(t * sr)
        e_idx = min(int(end_t * sr), n_samples)
        tasks.append((s_idx, e_idx, t, end_t))
        t += step
    return tasks


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


def _scan_parallel(
    y_full,
    sr: int,
    tasks: List[Tuple[int, int, float, float]],
    n_workers: int,
    n_total_estimate: int,
    progress_every: int,
    t_scan: float,
) -> Tuple[List[Dict[str, Any]], Tuple[int, int, int], bool]:
    """Run the per-window detectors across a Pool of ``n_workers`` processes,
    sharing the audio buffer through ``multiprocessing.shared_memory`` so no
    975 MB pickle copies are made per worker.

    Returns ``(windows, (rhythmic_fires, crowd_fires, music_fires), ok)``.
    On any setup failure (shared-memory creation, pool spawn, librosa import
    in workers, etc.) returns ``([], (0,0,0), False)`` — the caller falls
    back to the serial loop with ``y_full`` still in scope.
    """
    try:
        from multiprocessing import Pool
        from multiprocessing import shared_memory
        import numpy as np  # type: ignore
    except Exception as e:
        print(
            f"[AUDIO_EVENTS] parallel scan unavailable ({e}); falling back to serial",
            file=sys.stderr,
        )
        sys.stderr.flush()
        return [], (0, 0, 0), False

    shm = None
    pool: Optional[Any] = None
    try:
        # Materialize the audio into a shared-memory buffer. Workers attach
        # by name (cross-process) and view it as a numpy ndarray with zero
        # extra allocations.
        shm = shared_memory.SharedMemory(create=True, size=int(y_full.nbytes))
        shm_view = np.ndarray(y_full.shape, dtype=y_full.dtype, buffer=shm.buf)
        shm_view[:] = y_full[:]

        print(
            f"[AUDIO_EVENTS] parallel scan: {n_workers} workers, "
            f"{len(tasks)} windows, {y_full.nbytes // (1024*1024)} MB shared "
            f"memory ('{shm.name}')",
            file=sys.stderr,
        )
        sys.stderr.flush()

        windows: List[Dict[str, Any]] = []
        n_fired_rhythmic = 0
        n_fired_crowd = 0
        n_fired_music = 0

        pool = Pool(
            processes=n_workers,
            initializer=_worker_init,
            initargs=(shm.name, tuple(shm_view.shape), str(shm_view.dtype), sr),
        )
        try:
            # imap preserves submission order → windows come back in time
            # order. chunksize amortizes per-task IPC overhead (a single
            # task is ~700 ms of HPSS work; chunksize=4 batches ~2.8 s of
            # work per dispatch which dwarfs the ~50 µs round-trip cost).
            for i, result in enumerate(
                pool.imap(_worker_run, tasks, chunksize=PARALLEL_CHUNKSIZE),
                1,
            ):
                windows.append(result)
                if result["rhythmic_speech"] >= 0.7:
                    n_fired_rhythmic += 1
                if result["crowd_response"] >= 0.5:
                    n_fired_crowd += 1
                if result["music_dominance"] >= 0.6:
                    n_fired_music += 1
                if progress_every > 0 and i % progress_every == 0:
                    elapsed = time.time() - t_scan
                    rate = i / elapsed if elapsed > 0 else 0.0
                    remaining = max(0, n_total_estimate - i)
                    eta = remaining / rate if rate > 0 else 0.0
                    print(
                        f"[AUDIO_EVENTS] {i}/~{n_total_estimate} windows "
                        f"({elapsed:.0f}s elapsed, ~{eta:.0f}s remaining, "
                        f"{rate:.1f} win/s) [parallel x{n_workers}]",
                        file=sys.stderr,
                    )
                    sys.stderr.flush()
        finally:
            try:
                pool.close()
                pool.join()
            except Exception:
                # Pool teardown failure shouldn't poison the results we
                # collected. Just log and move on.
                pass

        return windows, (n_fired_rhythmic, n_fired_crowd, n_fired_music), True

    except Exception as e:
        # Anything in the parallel path failed — clean up and signal the
        # caller to fall back to serial. We don't write a partial result.
        print(
            f"[AUDIO_EVENTS] parallel scan failed ({type(e).__name__}: {e}); "
            f"falling back to serial",
            file=sys.stderr,
        )
        sys.stderr.flush()
        if pool is not None:
            try:
                pool.terminate()
                pool.join()
            except Exception:
                pass
        return [], (0, 0, 0), False
    finally:
        # Always release the shared-memory buffer in the parent. Workers
        # are gone by now (pool was closed/terminated above) so it's safe.
        if shm is not None:
            try:
                shm.close()
                shm.unlink()
            except Exception:
                pass


def scan_audio_events(
    audio_path: str,
    out_path: str,
    window_size: int = WINDOW_SIZE_DEFAULT,
    step: int = STEP_DEFAULT,
    duration_hint: Optional[float] = None,
    progress_every: int = 100,
    n_workers: Optional[int] = None,
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

    ``n_workers`` controls the parallel scan path (2026-06-04 fix):
    - ``None`` (default): auto-resolve via :func:`_resolve_worker_count`
      (``AUDIO_EVENTS_WORKERS`` env var → ``min(8, cpu-2)`` floor)
    - ``1``: force serial path (the original loop)
    - ``>=2``: spawn that many worker processes
    Parallel is only used when ≥ ``PARALLEL_MIN_WINDOWS`` windows are
    queued (below that the spawn/SHM overhead exceeds the savings). On
    Windows (spawn semantics) the per-worker librosa import is paid
    once at pool startup; amortized over hundreds of windows it's
    invisible vs the HPSS cost.
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

    # Pre-build the window task list — shared by serial + parallel paths so
    # they can't drift on window boundaries.
    tasks = _build_window_tasks(duration_hint, window_size, step, sr, len(y_full))

    # Resolve worker count and decide which path to take. Parallel is only
    # worth it when there are enough tasks to amortize pool startup.
    resolved_workers = _resolve_worker_count(n_workers)
    use_parallel = (
        resolved_workers >= 2
        and len(tasks) >= PARALLEL_MIN_WINDOWS
    )

    windows: List[Dict[str, Any]] = []
    n_fired_rhythmic = 0
    n_fired_crowd = 0
    n_fired_music = 0
    t_scan = time.time()
    backend_used = "librosa"

    if use_parallel:
        windows, fires, parallel_ok = _scan_parallel(
            y_full=y_full, sr=sr, tasks=tasks,
            n_workers=resolved_workers,
            n_total_estimate=n_total_estimate,
            progress_every=progress_every,
            t_scan=t_scan,
        )
        if parallel_ok:
            n_fired_rhythmic, n_fired_crowd, n_fired_music = fires
            backend_used = f"librosa+mp{resolved_workers}"
            del y_full
        else:
            # Parallel path bailed (shared-memory setup failure, librosa
            # missing in workers, etc.) — fall through to the serial loop
            # with y_full still in scope.
            windows = []
            use_parallel = False

    if not use_parallel:
        print(
            f"[AUDIO_EVENTS] serial scan: {len(tasks)} windows",
            file=sys.stderr,
        )
        sys.stderr.flush()
        for i, (s_idx, e_idx, t_start, t_end) in enumerate(tasks, 1):
            y = y_full[s_idx:e_idx]
            result = _run_detectors(y, sr, librosa, np)
            windows.append({
                "start": round(t_start, 1),
                "end": round(t_end, 1),
                **result,
            })
            if result["rhythmic_speech"] >= 0.7:
                n_fired_rhythmic += 1
            if result["crowd_response"] >= 0.5:
                n_fired_crowd += 1
            if result["music_dominance"] >= 0.6:
                n_fired_music += 1
            if progress_every > 0 and i % progress_every == 0:
                elapsed = time.time() - t_scan
                rate = i / elapsed if elapsed > 0 else 0.0
                remaining = max(0, n_total_estimate - i)
                eta = remaining / rate if rate > 0 else 0.0
                print(
                    f"[AUDIO_EVENTS] {i}/~{n_total_estimate} windows "
                    f"({elapsed:.0f}s elapsed, ~{eta:.0f}s remaining, "
                    f"{rate:.1f} win/s) [serial]",
                    file=sys.stderr,
                )
                sys.stderr.flush()
        del y_full

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps({
        "windows": windows,
        "window_size": window_size,
        "step": step,
        "duration": round(duration_hint, 1),
        "backend": backend_used,
    }), encoding="utf-8")
    elapsed = time.time() - t_scan
    rate = len(windows) / elapsed if elapsed > 0 else 0.0
    print(
        f"[AUDIO_EVENTS] scanned {len(windows)} windows of {window_size}s in "
        f"{elapsed:.1f}s ({rate:.1f} win/s, backend={backend_used}; "
        f"rhythmic_fires={n_fired_rhythmic} crowd_fires={n_fired_crowd} "
        f"music_fires={n_fired_music})",
        file=sys.stderr,
    )
    return {
        "windows": len(windows),
        "backend": backend_used,
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
    ap.add_argument(
        "--workers", type=int, default=0,
        help="Parallel worker count for the per-window scan. 0=auto "
             "(env AUDIO_EVENTS_WORKERS, else min(8, cpu-2)); 1=force serial; "
             "N>=2=that many workers (default: 0)",
    )
    args = ap.parse_args()
    summary = scan_audio_events(
        args.audio, args.out,
        window_size=args.window, step=args.step,
        duration_hint=args.duration,
        n_workers=(args.workers if args.workers > 0 else None),
    )
    json.dump(summary, sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    # Multiprocessing spawn on Windows imports this module fresh in each
    # worker. Keeping the Pool setup inside scan_audio_events (not at
    # module top level) plus this __main__ guard makes the import safe.
    _cli()
