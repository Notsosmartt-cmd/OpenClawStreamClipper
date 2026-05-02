"""Stage 7c batch clip transcription (one Whisper load for all clips).

Reads env: CLIP_WHISPER_MODEL (default large-v3), CLIP_WHISPER_DEVICE
(default cuda), CLIP_WHISPER_COMPUTE (default float16/cuda or int8/cpu).
For each clip_audio_*.wav under /tmp/clipper, writes clip_<ts>.srt with
word-level timestamps. CPU int8 fallback on GPU init failure.
"""
import json, sys, os, glob

from faster_whisper import WhisperModel

cache_dir = "/root/.cache/whisper-models"
temp_dir = "/tmp/clipper"
whisper_model = os.environ.get("CLIP_WHISPER_MODEL", "large-v3")
whisper_device = os.environ.get("CLIP_WHISPER_DEVICE", "cuda")
whisper_compute = os.environ.get("CLIP_WHISPER_COMPUTE", "float16" if whisper_device == "cuda" else "int8")

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
        for seg in segments:
            start_h, start_r = divmod(seg.start, 3600)
            start_m, start_s = divmod(start_r, 60)
            end_h, end_r = divmod(seg.end, 3600)
            end_m, end_s = divmod(end_r, 60)
            srt_lines.append(f"{idx}")
            srt_lines.append(
                f"{int(start_h):02d}:{int(start_m):02d}:{start_s:06.3f}".replace(".", ",") +
                " --> " +
                f"{int(end_h):02d}:{int(end_m):02d}:{end_s:06.3f}".replace(".", ",")
            )
            srt_lines.append(seg.text.strip())
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
