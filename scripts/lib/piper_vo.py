#!/usr/bin/env python3
"""Generate a Piper voiceover WAV sized to fit the full clip window.

The resulting WAV is exactly ``clip_duration`` seconds long with the TTS
utterance positioned according to ``placement``:

- ``intro``  → utterance starts at 0.2 s
- ``peak``   → utterance is centered on the midpoint of the clip
- ``outro``  → utterance ends ~0.3 s before the clip ends

Everywhere else is silence. The Stage 7 FFmpeg mix just applies gain to this
WAV and sums it with the streamer audio + optional music bed, so no per-clip
offset math is needed in bash.

Piper runs on CPU; a single short line takes roughly 0.2-0.8 s.
"""
import argparse
import os
import subprocess
import sys
import tempfile
import wave

DEFAULT_VOICE_DIR = "/root/.cache/piper"
DEFAULT_VOICE = "en_US-amy-low"  # Small, fast, natural neutral voice


def find_voice_model(voice_name: str) -> str | None:
    """Locate a Piper voice ONNX file. Tries a few standard locations."""
    candidates = [
        os.environ.get("PIPER_VOICE_PATH"),
        os.path.join(DEFAULT_VOICE_DIR, f"{voice_name}.onnx"),
        os.path.join(DEFAULT_VOICE_DIR, voice_name, f"{voice_name}.onnx"),
        f"/usr/share/piper/{voice_name}.onnx",
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return None


def auto_fetch_voice(voice_name: str) -> str | None:
    """On first voiceover, the host-mounted Piper cache may be empty.
    Call ``fetch_assets.py piper <voice>`` to populate it; if the fetch
    succeeds, return the newly-available path. Any failure returns None
    and the caller prints a helpful message."""
    helper = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fetch_assets.py")
    if not os.path.isfile(helper):
        return None
    print(f"piper: voice '{voice_name}' not cached — attempting fetch...",
          file=sys.stderr)
    try:
        proc = subprocess.run(
            ["python3", helper, "piper", voice_name],
            capture_output=True, text=True, timeout=240,
        )
        if proc.returncode == 0:
            return find_voice_model(voice_name)
        print(f"piper: fetch_assets returned rc={proc.returncode}: "
              f"{(proc.stderr or proc.stdout).strip()[:200]}", file=sys.stderr)
    except subprocess.TimeoutExpired:
        print("piper: fetch_assets timed out after 240 s", file=sys.stderr)
    except Exception as e:
        print(f"piper: fetch_assets crashed: {e}", file=sys.stderr)
    return None


def synthesize_piper(text: str, out_path: str, voice_path: str) -> bool:
    """Call the `piper` CLI to synthesize text → out_path WAV."""
    piper_bin = os.environ.get("PIPER_BIN", "piper")
    try:
        proc = subprocess.run(
            [piper_bin, "--model", voice_path, "--output_file", out_path],
            input=text.encode("utf-8"),
            capture_output=True,
            timeout=30,
        )
        if proc.returncode != 0:
            print(f"piper CLI failed (rc={proc.returncode}): "
                  f"{proc.stderr.decode('utf-8', errors='replace')[:200]}",
                  file=sys.stderr)
            return False
    except FileNotFoundError:
        # Fall back to Python package
        try:
            from piper import PiperVoice  # type: ignore
        except ImportError:
            print("piper CLI not found and piper-tts package not installed",
                  file=sys.stderr)
            return False
        try:
            voice = PiperVoice.load(voice_path)
            with wave.open(out_path, "wb") as w:
                voice.synthesize(text, w)
        except Exception as e:
            print(f"piper Python API failed: {e}", file=sys.stderr)
            return False
    except subprocess.TimeoutExpired:
        print("piper CLI timed out after 30 s", file=sys.stderr)
        return False

    return os.path.isfile(out_path) and os.path.getsize(out_path) > 1024


def pad_and_place(raw_path: str, out_path: str, clip_duration: float,
                  placement: str, speed: float) -> bool:
    """Wrap the Piper output in silence so the final WAV is exactly
    clip_duration seconds long with the utterance placed correctly."""
    # Read the raw TTS audio metadata
    try:
        with wave.open(raw_path, "rb") as src:
            sr = src.getframerate()
            nframes = src.getnframes()
            channels = src.getnchannels()
            sampwidth = src.getsampwidth()
            raw_dur = nframes / float(sr)
            raw_pcm = src.readframes(nframes)
    except Exception as e:
        print(f"Unable to read piper output: {e}", file=sys.stderr)
        return False

    # When the host clip is sped up, VO will be played at the same rate
    # (we mix into the source BEFORE the tempo filter? no — we mix after
    # speed adjustment). To keep the VO speed stable with the clip, the
    # caller already handles speed on the source audio only; VO stays at 1x.
    # So we just fit `raw_dur` into `clip_duration / speed`.
    target_dur = clip_duration / max(speed, 0.01)

    if raw_dur >= target_dur:
        # Utterance longer than clip window — trim it.
        max_frames = int(target_dur * sr)
        raw_pcm = raw_pcm[: max_frames * channels * sampwidth]
        raw_dur = max_frames / float(sr)
        lead = 0.0
    else:
        gap = target_dur - raw_dur
        if placement == "peak":
            lead = max(0.0, gap * 0.5 - 0.2)
        elif placement == "outro":
            lead = max(0.0, gap - 0.3)
        else:  # intro
            lead = min(0.2, gap * 0.1)

    silence_frame = b"\x00" * channels * sampwidth
    lead_frames = int(lead * sr)
    tail_frames = max(0, int(target_dur * sr) - lead_frames - int(raw_dur * sr))

    try:
        with wave.open(out_path, "wb") as dst:
            dst.setnchannels(channels)
            dst.setsampwidth(sampwidth)
            dst.setframerate(sr)
            if lead_frames > 0:
                dst.writeframes(silence_frame * lead_frames)
            dst.writeframes(raw_pcm)
            if tail_frames > 0:
                dst.writeframes(silence_frame * tail_frames)
    except Exception as e:
        print(f"Failed to write placed VO WAV: {e}", file=sys.stderr)
        return False

    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--text", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--placement", default="intro",
                        choices=["intro", "peak", "outro"])
    parser.add_argument("--clip-duration", type=float, default=30.0)
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--tone", default="deadpan")
    parser.add_argument("--voice", default=os.environ.get("PIPER_VOICE", DEFAULT_VOICE))
    args = parser.parse_args()

    text = args.text.strip()
    if not text:
        print("empty text — skipping TTS", file=sys.stderr)
        return 1

    voice_path = find_voice_model(args.voice)
    if not voice_path:
        # Host-mounted Piper cache is empty — try an on-demand fetch so the
        # user doesn't have to intervene just because they turned TTS on.
        voice_path = auto_fetch_voice(args.voice)
    if not voice_path:
        print(f"Piper voice model '{args.voice}' not available in {DEFAULT_VOICE_DIR} "
              "and could not be fetched. Either run\n"
              f"  python3 /root/scripts/lib/fetch_assets.py piper {args.voice}\n"
              "inside the container, or click Fetch in the dashboard Asset Cache panel.",
              file=sys.stderr)
        return 1

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        raw_path = tmp.name

    try:
        if not synthesize_piper(text, raw_path, voice_path):
            return 1
        if not pad_and_place(raw_path, args.out, args.clip_duration,
                             args.placement, args.speed):
            return 1
        print(f"VO ok: {args.placement} dur={args.clip_duration}s voice={args.voice}",
              file=sys.stderr)
        return 0
    finally:
        try:
            os.unlink(raw_path)
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
