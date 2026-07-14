"""Stage 7c clip caption timing — master-slice mode + Whisper fallback.

Wave A2' (plan-speed-wave3, 2026-07-14): the pipeline already holds a
word-level master transcript (Stage 2; WhisperX wav2vec2-aligned since Wave 0),
so per-clip caption SRTs can be SLICED from it instead of re-transcribing every
clip with a fresh Whisper load. Master mode removes Stage 7's whole Whisper
step (and its GPU claim), is deterministic, and keeps captions byte-consistent
with the transcript every detection stage read. Universal across hardware
profiles — CPU-only installs save the most.

Mode select (env ``CLIP_CAPTION_SOURCE``, default ``master``):
  * ``master`` — slice ``{work}/transcript.json`` by ``{work}/clip_windows.json``
    (written by stage7.py). Falls back to Whisper when either input is missing.
  * ``whisper`` — the legacy path: one Whisper load, transcribe every
    ``clip_audio_*.wav`` under the work dir.

Legacy env (Whisper path): CLIP_WHISPER_MODEL (default large-v3),
CLIP_WHISPER_DEVICE (default cuda), CLIP_WHISPER_COMPUTE (default
float16/cuda or int8/cpu). CPU int8 fallback on GPU init failure.
"""
import glob
import json
import os
import sys

cache_dir = os.environ.get("WHISPER_MODEL_DIR", "/root/.cache/whisper-models")
temp_dir = os.environ.get("CLIP_WORK_DIR", "/tmp/clipper")
whisper_model = os.environ.get("CLIP_WHISPER_MODEL", "large-v3")
whisper_device = os.environ.get("CLIP_WHISPER_DEVICE", "cuda")
whisper_compute = os.environ.get(
    "CLIP_WHISPER_COMPUTE", "float16" if whisper_device == "cuda" else "int8")
caption_source = os.environ.get("CLIP_CAPTION_SOURCE", "master").strip().lower()


def _ts(t):
    """Seconds -> SRT timestamp HH:MM:SS,mmm."""
    h, r = divmod(max(0.0, float(t)), 3600)
    m, s = divmod(r, 60)
    return f"{int(h):02d}:{int(m):02d}:{s:06.3f}".replace(".", ",")


def _master_slice() -> bool:
    """Write clip_<T>.srt for every window by slicing the master transcript.
    Returns False (having written NOTHING) when inputs are absent/invalid so
    the caller can fall back to the Whisper path."""
    master_path = os.path.join(temp_dir, "transcript.json")
    windows_path = os.path.join(temp_dir, "clip_windows.json")
    if caption_source != "master":
        return False
    if not (os.path.exists(master_path) and os.path.exists(windows_path)):
        print("[CAPTIONS] master-slice inputs missing "
              f"(transcript={os.path.exists(master_path)} windows={os.path.exists(windows_path)}) "
              "— falling back to Whisper.", file=sys.stderr)
        return False
    try:
        with open(master_path, encoding="utf-8") as f:
            segs = json.load(f)
        with open(windows_path, encoding="utf-8") as f:
            wins = json.load(f)
    except Exception as e:  # noqa: BLE001
        print(f"[CAPTIONS] master-slice unreadable ({e}) — falling back to Whisper.",
              file=sys.stderr)
        return False
    if not (isinstance(segs, list) and segs and isinstance(wins, dict) and wins):
        print("[CAPTIONS] master-slice inputs empty — falling back to Whisper.",
              file=sys.stderr)
        return False

    # Flatten to (start, end, text) word items; segments without word timing
    # contribute one whole block (same fallback the Whisper path used).
    flat = []
    for s in segs:
        words = s.get("words") or []
        emitted = False
        for w in words:
            txt = (w.get("word") or "").strip()
            if txt and w.get("start") is not None and w.get("end") is not None:
                flat.append((float(w["start"]), float(w["end"]), txt))
                emitted = True
        if not emitted:
            txt = (s.get("text") or "").strip()
            if txt and s.get("start") is not None and s.get("end") is not None:
                flat.append((float(s["start"]), float(s["end"]), txt))
    if not flat:
        print("[CAPTIONS] master transcript has no timed items — falling back to Whisper.",
              file=sys.stderr)
        return False
    flat.sort(key=lambda x: x[0])

    for ts_key, win in wins.items():
        try:
            cs = float(win.get("start", 0.0))
            dur = max(1.0, float(win.get("duration", 45.0)))
        except (TypeError, ValueError):
            cs, dur = 0.0, 45.0
        ce = cs + dur
        lines, idx = [], 1
        for (ws_, we_, txt) in flat:
            if we_ <= cs or ws_ >= ce:
                continue
            rs = max(0.0, ws_ - cs)
            re_ = min(dur, we_ - cs)
            if re_ <= rs:                      # boundary-clipped sliver
                re_ = min(dur, rs + 0.12)
            lines += [str(idx), f"{_ts(rs)} --> {_ts(re_)}", txt, ""]
            idx += 1
        with open(os.path.join(temp_dir, f"clip_{ts_key}.srt"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print(f"  T={ts_key}: {idx - 1} SRT blocks (master-slice)", file=sys.stderr)

    print(f"[CAPTIONS] master-slice mode: {len(wins)} clip SRT(s) from the master "
          "transcript — no per-clip Whisper pass.", file=sys.stderr)
    return True


def _whisper_batch() -> None:
    """Legacy path: one Whisper load, transcribe every clip_audio_*.wav."""
    from faster_whisper import WhisperModel  # lazy: master mode never imports it

    if whisper_device == "cuda":
        try:
            model = WhisperModel(whisper_model, device="cuda",
                                 compute_type=whisper_compute, download_root=cache_dir)
            print(f"[WHISPER] Batch caption mode: GPU ({whisper_compute}) with {whisper_model}",
                  file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            print(f"[WHISPER] GPU failed ({e}), using CPU", file=sys.stderr)
            model = WhisperModel(whisper_model, device="cpu", compute_type="int8",
                                 download_root=cache_dir)
    else:
        model = WhisperModel(whisper_model, device="cpu", compute_type="int8",
                             download_root=cache_dir)
        print(f"[WHISPER] Batch caption mode: CPU (int8) with {whisper_model}", file=sys.stderr)

    audio_files = sorted(glob.glob(f"{temp_dir}/clip_audio_*.wav"))
    print(f"[WHISPER] Transcribing {len(audio_files)} clip segments...", file=sys.stderr)

    for audio_path in audio_files:
        # Extract timestamp from filename: clip_audio_1234.wav -> 1234
        basename = os.path.basename(audio_path)
        ts = basename.replace("clip_audio_", "").replace(".wav", "")
        srt_path = f"{temp_dir}/clip_{ts}.srt"

        try:
            segments, info = model.transcribe(audio_path, beam_size=5, word_timestamps=True)

            srt_lines = []
            idx = 1
            # Emit ONE SRT block per WORD (word_timestamps=True) so the caption
            # renderer can highlight/box each word in turn (CapCut-style karaoke).
            # Falls back to a whole-segment block when a segment has no word timing.
            for seg in segments:
                words = getattr(seg, "words", None) or []
                emitted = False
                for w in words:
                    wt = (getattr(w, "word", "") or "").strip()
                    if not wt:
                        continue
                    srt_lines.append(f"{idx}")
                    srt_lines.append(f"{_ts(w.start)} --> {_ts(w.end)}")
                    srt_lines.append(wt)
                    srt_lines.append("")
                    idx += 1
                    emitted = True
                if not emitted:
                    stext = seg.text.strip()
                    if stext:
                        srt_lines.append(f"{idx}")
                        srt_lines.append(f"{_ts(seg.start)} --> {_ts(seg.end)}")
                        srt_lines.append(stext)
                        srt_lines.append("")
                        idx += 1

            with open(srt_path, "w") as f:
                f.write("\n".join(srt_lines))
            print(f"  T={ts}: {idx - 1} SRT segments", file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            print(f"  T={ts}: transcription failed: {e}", file=sys.stderr)
            # Write empty SRT so rendering can fall back to no-subtitle mode
            with open(srt_path, "w") as f:
                f.write("")

    print("[WHISPER] Batch transcription complete.", file=sys.stderr)


if not _master_slice():
    _whisper_batch()
