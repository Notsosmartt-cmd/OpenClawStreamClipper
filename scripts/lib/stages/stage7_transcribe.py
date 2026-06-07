"""Stage 7c batch clip transcription (one Whisper load for all clips).

Reads env: CLIP_WHISPER_MODEL (default large-v3), CLIP_WHISPER_DEVICE
(default cuda), CLIP_WHISPER_COMPUTE (default float16/cuda or int8/cpu).
For each clip_audio_*.wav under /tmp/clipper, writes clip_<ts>.srt with
word-level timestamps. CPU int8 fallback on GPU init failure.
"""
import json, sys, os, glob

from faster_whisper import WhisperModel

cache_dir = os.environ.get("WHISPER_MODEL_DIR", "/root/.cache/whisper-models")
temp_dir = os.environ.get("CLIP_WORK_DIR", "/tmp/clipper")
whisper_model = os.environ.get("CLIP_WHISPER_MODEL", "large-v3")
whisper_device = os.environ.get("CLIP_WHISPER_DEVICE", "cuda")
whisper_compute = os.environ.get("CLIP_WHISPER_COMPUTE", "float16" if whisper_device == "cuda" else "int8")


def _ts(t):
    """Seconds -> SRT timestamp HH:MM:SS,mmm."""
    h, r = divmod(max(0.0, float(t)), 3600)
    m, s = divmod(r, 60)
    return f"{int(h):02d}:{int(m):02d}:{s:06.3f}".replace(".", ",")


# Load Whisper ONCE for all clips
if whisper_device == "cuda":
    try:
        model = WhisperModel(whisper_model, device="cuda", compute_type=whisper_compute, download_root=cache_dir)
        print(f"[WHISPER] Batch caption mode: GPU ({whisper_compute}) with {whisper_model}", file=sys.stderr)
    except Exception as e:
        print(f"[WHISPER] GPU failed ({e}), using CPU", file=sys.stderr)
        model = WhisperModel(whisper_model, device="cpu", compute_type="int8", download_root=cache_dir)
else:
    model = WhisperModel(whisper_model, device="cpu", compute_type="int8", download_root=cache_dir)
    print(f"[WHISPER] Batch caption mode: CPU (int8) with {whisper_model}", file=sys.stderr)

# Find all clip audio files
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
        print(f"  T={ts}: {idx-1} SRT segments", file=sys.stderr)
    except Exception as e:
        print(f"  T={ts}: transcription failed: {e}", file=sys.stderr)
        # Write empty SRT so rendering can fall back to no-subtitle mode
        with open(srt_path, "w") as f:
            f.write("")

print("[WHISPER] Batch transcription complete.", file=sys.stderr)
