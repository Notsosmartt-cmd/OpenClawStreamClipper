#!/usr/bin/env python3
"""Speech pipeline — Phase 3 of the 2026 upgrade.

Replaces the 100-line inline transcription heredoc in ``scripts/clip-pipeline.sh``
Stage 2 with a structured, maintainable module. Two backends:

- **whisperx** (preferred): VAD-based chunking + batched faster-whisper +
  wav2vec2 forced alignment. Drops the fragile 20-minute arbitrary
  chunking the old code used to avoid the faster-whisper degenerate-loop
  bug. Produces frame-accurate word-level timestamps.

- **faster-whisper** (fallback): the pre-Phase-3 code path, preserved
  verbatim for environments where WhisperX isn't installed or can't run
  (ImportError, missing pyannote segmentation weights, etc.).

Backend choice is controlled by ``config/speech.json::backend``. Import /
runtime failures transparently degrade to the faster-whisper fallback;
the pipeline never hard-fails on a transcription-layer issue.

Output shape (must stay compatible with Stages 3+):
- ``transcript.json``: list of ``{start, end, text}`` objects (seconds).
  When word-level alignment is available, each segment additionally
  carries ``words: [{word, start, end}]``.
- ``transcript.srt``: SubRip file for ``ffmpeg -vf subtitles=...``.

Also implements Phase 3.5 (streamer-slang biasing) — the VOD basename is
matched against ``config/streamer_prompts.json::channels[*].filename_substrings``
to pick an ``initial_prompt`` that nudges Whisper toward channel-specific
game/character names, emote names, and recurring jargon.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

DEFAULT_SPEECH_CONFIG = Path(
    os.environ.get("CLIP_SPEECH_CONFIG", "/root/.openclaw/speech.json")
)
DEFAULT_STREAMER_PROMPTS = Path(
    os.environ.get("CLIP_STREAMER_PROMPTS", "/root/.openclaw/streamer_prompts.json")
)
WHISPER_CACHE = Path(
    os.environ.get("WHISPER_MODEL_DIR", "/root/.cache/whisper-models")
)


# ---------------------------------------------------------------------------
# Config loaders
# ---------------------------------------------------------------------------


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"[SPEECH] failed to parse {path}: {e}", file=sys.stderr)
        return {}


def load_speech_config(path: Optional[str] = None) -> dict:
    """Load ``config/speech.json`` with safe defaults for missing keys.

    Env-var overrides (for backwards compat with the dashboard Models
    panel and the legacy pre-Phase-3 call sites):

    - ``CLIP_WHISPER_MODEL`` → ``cfg["model"]``
    - ``CLIP_WHISPER_DEVICE`` → ``cfg["device"]``
    - ``CLIP_WHISPER_COMPUTE`` → ``cfg["compute_type"]``
    - ``CLIP_SPEECH_BACKEND`` → ``cfg["backend"]``
    """
    cfg = _read_json(Path(path) if path else DEFAULT_SPEECH_CONFIG)
    cfg.setdefault("backend", "whisperx")
    cfg.setdefault("model", "large-v3-turbo")
    cfg.setdefault("device", "auto")
    cfg.setdefault("compute_type", "auto")
    cfg.setdefault("batch_size", 16)
    cfg.setdefault("language", "en")
    cfg.setdefault("alignment", {"enabled": True})
    cfg.setdefault("vocal_separation", {"enabled": False})
    cfg.setdefault("streamer_prompts_path", str(DEFAULT_STREAMER_PROMPTS))
    cfg.setdefault("fallback_chunk_seconds", 1200)
    # Tier-2 M1 — speaker diarization (off by default; enabled when HF_TOKEN
    # is set AND pyannote is installed AND the user opts in via config).
    cfg.setdefault("diarization", {
        "enabled": True,
        "model": "pyannote/speaker-diarization-3.1",
        "min_speakers": None,
        "max_speakers": None,
    })

    # Env overrides come last so they win over file defaults.
    env_overrides = {
        "CLIP_WHISPER_MODEL": "model",
        "CLIP_WHISPER_DEVICE": "device",
        "CLIP_WHISPER_COMPUTE": "compute_type",
        "CLIP_SPEECH_BACKEND": "backend",
    }
    for env, key in env_overrides.items():
        val = os.environ.get(env)
        if val:
            cfg[key] = val
    return cfg


def pick_initial_prompt(vod_basename: str, prompts_path: Optional[str] = None) -> str:
    """Match the VOD filename against ``config/streamer_prompts.json`` and
    return the best-fitting ``initial_prompt``. Falls back to the global
    default, or an empty string when no file is available."""
    data = _read_json(Path(prompts_path) if prompts_path else DEFAULT_STREAMER_PROMPTS)
    if not data:
        return ""
    vb = (vod_basename or "").lower()
    for _chan, spec in (data.get("channels") or {}).items():
        if not isinstance(spec, dict):
            continue
        subs = spec.get("filename_substrings") or []
        for s in subs:
            if isinstance(s, str) and s and s.lower() in vb:
                return str(spec.get("initial_prompt") or "")
    return str(data.get("default_prompt") or "")


# ---------------------------------------------------------------------------
# Device / compute resolution
# ---------------------------------------------------------------------------


def resolve_device(cfg: dict) -> Tuple[str, str]:
    """Return (device, compute_type) honoring config + CUDA availability."""
    dev = cfg.get("device", "auto")
    compute = cfg.get("compute_type", "auto")
    if dev == "auto":
        try:
            import torch  # type: ignore
            dev = "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:
            dev = "cpu"
    if compute == "auto":
        compute = "float16" if dev == "cuda" else "int8"
    return dev, compute


# ---------------------------------------------------------------------------
# SRT helper
# ---------------------------------------------------------------------------


def _fmt_srt_time(sec: float) -> str:
    h, r = divmod(float(sec), 3600.0)
    m, s = divmod(r, 60.0)
    return f"{int(h):02d}:{int(m):02d}:{s:06.3f}".replace(".", ",")


def write_srt(segments: List[dict], out_path: str) -> None:
    """Write a SubRip file from {start, end, text} segments."""
    lines: List[str] = []
    for i, seg in enumerate(segments, start=1):
        lines.append(str(i))
        lines.append(f"{_fmt_srt_time(seg['start'])} --> {_fmt_srt_time(seg['end'])}")
        lines.append(seg["text"].strip())
        lines.append("")
    Path(out_path).write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Backend 1 — WhisperX (preferred)
# ---------------------------------------------------------------------------


def _whisperx_available() -> bool:
    try:
        import whisperx  # type: ignore  # noqa: F401
        return True
    except ImportError:
        return False


def transcribe_whisperx(
    audio_path: str,
    cfg: dict,
    initial_prompt: str = "",
) -> Tuple[List[dict], str]:
    """Run WhisperX's VAD+ASR+align pipeline. Returns (segments, language)."""
    import whisperx  # type: ignore

    device, compute = resolve_device(cfg)
    model_name = cfg.get("model", "large-v3-turbo")
    language = cfg.get("language") or None
    batch_size = int(cfg.get("batch_size") or 16)

    t0 = time.time()
    print(
        f"[SPEECH] WhisperX backend: model={model_name} device={device} "
        f"compute_type={compute} batch={batch_size} lang={language or 'auto'}",
        file=sys.stderr,
    )
    try:
        asr_model = whisperx.load_model(
            model_name,
            device,
            compute_type=compute,
            language=language,
            download_root=str(WHISPER_CACHE),
            asr_options={
                "initial_prompt": initial_prompt or None,
                "suppress_tokens": [-1],
            },
        )
    except TypeError:
        # Older WhisperX versions don't accept `download_root`/`asr_options`
        # as kwargs here; retry with the minimum.
        asr_model = whisperx.load_model(
            model_name, device, compute_type=compute, language=language
        )

    audio = whisperx.load_audio(audio_path)
    transcribe_kwargs = {"batch_size": batch_size}
    if initial_prompt:
        # Some WhisperX versions accept it on .transcribe() rather than on load.
        transcribe_kwargs["initial_prompt"] = initial_prompt
    try:
        result = asr_model.transcribe(audio, **transcribe_kwargs)
    except TypeError:
        # Drop unsupported kwargs and retry.
        result = asr_model.transcribe(audio, batch_size=batch_size)
    detected_lang = result.get("language") or language or "en"
    segments_raw = result.get("segments") or []
    print(
        f"[SPEECH] WhisperX ASR: {len(segments_raw)} segments in {time.time()-t0:.1f}s "
        f"(detected lang={detected_lang})",
        file=sys.stderr,
    )

    # Optional forced alignment for word-level timestamps.
    aligned_segments = segments_raw
    if cfg.get("alignment", {}).get("enabled", True):
        try:
            t1 = time.time()
            align_model, metadata = whisperx.load_align_model(
                language_code=detected_lang, device=device
            )
            aligned = whisperx.align(
                segments_raw, align_model, metadata, audio, device,
                return_char_alignments=False,
            )
            aligned_segments = aligned.get("segments") or segments_raw
            print(
                f"[SPEECH] WhisperX align: {len(aligned_segments)} aligned segments "
                f"in {time.time()-t1:.1f}s",
                file=sys.stderr,
            )
        except Exception as e:
            print(f"[SPEECH] alignment failed ({e}); using ASR timestamps", file=sys.stderr)

    # Tier-2 M1 — optional speaker diarization. Requires HF_TOKEN env var with
    # access to pyannote/speaker-diarization-3.1, and pyannote-audio installed.
    # Mutates aligned_segments in place to add a 'speaker' field per segment;
    # the segment text remains the source of truth for downstream stages, so
    # diarization failure is non-fatal.
    diar_cfg = cfg.get("diarization", {}) or {}
    if diar_cfg.get("enabled", True):
        aligned_segments = _maybe_diarize(
            aligned_segments, audio, device, diar_cfg, whisperx
        )

    # Normalize into our canonical {start, end, text, words?, speaker?} shape.
    out: List[dict] = []
    for seg in aligned_segments:
        text = (seg.get("text") or "").strip()
        if not text or text in (".", "..", "..."):
            continue
        rec = {
            "start": round(float(seg.get("start") or 0.0), 2),
            "end": round(float(seg.get("end") or 0.0), 2),
            "text": text,
        }
        speaker = seg.get("speaker")
        if isinstance(speaker, str) and speaker:
            rec["speaker"] = speaker
        words = seg.get("words") or []
        if words:
            rec["words"] = [
                {
                    "word": w.get("word") or w.get("text") or "",
                    "start": round(float(w.get("start") or 0.0), 3),
                    "end": round(float(w.get("end") or 0.0), 3),
                }
                for w in words
                if w.get("start") is not None and w.get("end") is not None
            ]
        out.append(rec)
    return out, detected_lang


def _maybe_diarize(segments, audio, device, diar_cfg, whisperx):
    """Tier-2 M1 — run WhisperX/pyannote diarization and assign speakers to
    aligned segments. Falls through unchanged on any error so the speech
    pipeline never hard-fails on diarization."""
    hf_token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    if not hf_token:
        print(
            "[SPEECH] M1: HF_TOKEN unset; skipping diarization (set HF_TOKEN "
            "and grant access to pyannote/speaker-diarization-3.1 to enable)",
            file=sys.stderr,
        )
        return segments
    try:
        # WhisperX exposes DiarizationPipeline; older versions used `whisperx.DiarizationPipeline`,
        # newer ones expose `whisperx.diarize.DiarizationPipeline`.  Probe both.
        DiarizationPipeline = getattr(whisperx, "DiarizationPipeline", None)
        if DiarizationPipeline is None:
            try:
                from whisperx.diarize import DiarizationPipeline  # type: ignore
            except Exception:
                DiarizationPipeline = None
        if DiarizationPipeline is None:
            print(
                "[SPEECH] M1: WhisperX has no DiarizationPipeline (upgrade whisperx "
                "or install pyannote-audio); skipping diarization",
                file=sys.stderr,
            )
            return segments
        t0 = time.time()
        diarize_kwargs = {"use_auth_token": hf_token, "device": device}
        try:
            diarize_model = DiarizationPipeline(
                model_name=diar_cfg.get("model") or "pyannote/speaker-diarization-3.1",
                **diarize_kwargs,
            )
        except TypeError:
            # Older signatures don't accept model_name; fall back to default.
            diarize_model = DiarizationPipeline(**diarize_kwargs)
        run_kwargs = {}
        if diar_cfg.get("min_speakers") is not None:
            run_kwargs["min_speakers"] = int(diar_cfg["min_speakers"])
        if diar_cfg.get("max_speakers") is not None:
            run_kwargs["max_speakers"] = int(diar_cfg["max_speakers"])
        diarize_segments = diarize_model(audio, **run_kwargs)
        result = whisperx.assign_word_speakers(
            diarize_segments, {"segments": segments}
        )
        annotated = result.get("segments") or segments
        n_with_speaker = sum(1 for s in annotated if s.get("speaker"))
        print(
            f"[SPEECH] M1: diarization assigned speakers to {n_with_speaker}/{len(annotated)} "
            f"segments in {time.time()-t0:.1f}s",
            file=sys.stderr,
        )
        return annotated
    except Exception as e:
        print(f"[SPEECH] M1: diarization failed ({e}); proceeding without speaker labels", file=sys.stderr)
        return segments


# ---------------------------------------------------------------------------
# Backend 2 — faster-whisper (fallback)
# ---------------------------------------------------------------------------


def transcribe_faster_whisper(
    audio_path: str,
    cfg: dict,
    initial_prompt: str = "",
) -> Tuple[List[dict], str]:
    """Pre-Phase-3 code path: 20-minute chunking + beam=5 decode. Kept as
    the fallback when WhisperX isn't available. Matches the legacy
    transcript.json shape exactly."""
    import glob
    import subprocess

    from faster_whisper import WhisperModel  # type: ignore

    device, compute = resolve_device(cfg)
    model_name = cfg.get("model", "large-v3-turbo")
    chunk_seconds = int(cfg.get("fallback_chunk_seconds") or 1200)
    language = cfg.get("language") or None

    print(
        f"[SPEECH] faster-whisper fallback: model={model_name} device={device} "
        f"compute_type={compute} chunk={chunk_seconds}s lang={language or 'auto'}",
        file=sys.stderr,
    )

    # Try GPU first, fall back to CPU on any error.
    try:
        model = WhisperModel(
            model_name, device=device, compute_type=compute,
            download_root=str(WHISPER_CACHE),
        )
    except Exception as e:
        print(f"[SPEECH] GPU load failed ({e}); retrying on CPU int8", file=sys.stderr)
        model = WhisperModel(
            model_name, device="cpu", compute_type="int8",
            download_root=str(WHISPER_CACHE),
        )

    # Chunk the audio with ffmpeg — this is the legacy path's main anti-
    # degenerate-loop guard. WhisperX uses VAD internally and doesn't need it.
    tmp_dir = Path("/tmp/clipper/audio_chunks_fallback")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    for existing in tmp_dir.glob("chunk_*.wav"):
        existing.unlink()

    # Get duration via ffprobe.
    try:
        dur_str = subprocess.check_output(
            [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "csv=p=0", audio_path,
            ],
            text=True,
        ).strip()
        audio_dur = int(float(dur_str.split(".")[0] or 0))
    except Exception:
        audio_dur = 0

    chunk_idx = 0
    offset = 0
    while offset < audio_dur:
        out_wav = tmp_dir / f"chunk_{chunk_idx:03d}.wav"
        subprocess.run(
            [
                "ffmpeg", "-y", "-ss", str(offset), "-t", str(chunk_seconds),
                "-i", audio_path, "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
                str(out_wav),
            ],
            check=False,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        chunk_idx += 1
        offset += chunk_seconds

    chunk_files = sorted(tmp_dir.glob("chunk_*.wav"))
    print(f"[SPEECH] fallback: {len(chunk_files)} chunks of ≤{chunk_seconds}s", file=sys.stderr)

    all_out: List[dict] = []
    detected_lang = language or "en"
    for ci, chunk in enumerate(chunk_files):
        chunk_offset = ci * chunk_seconds
        try:
            segments, info = model.transcribe(
                str(chunk),
                beam_size=5,
                word_timestamps=True,
                language=language,
                initial_prompt=initial_prompt or None,
            )
            if info and getattr(info, "language", None):
                detected_lang = info.language
            for seg in segments:
                text = (seg.text or "").strip()
                if not text or text in (".", "..", "..."):
                    continue
                abs_start = round(seg.start + chunk_offset, 2)
                abs_end = round(seg.end + chunk_offset, 2)
                rec: Dict = {"start": abs_start, "end": abs_end, "text": text}
                if getattr(seg, "words", None):
                    rec["words"] = [
                        {
                            "word": w.word,
                            "start": round(w.start + chunk_offset, 3),
                            "end": round(w.end + chunk_offset, 3),
                        }
                        for w in seg.words
                        if w.start is not None and w.end is not None
                    ]
                all_out.append(rec)
        except Exception as e:
            print(f"[SPEECH] chunk {ci} failed: {e}", file=sys.stderr)

    # Clean up chunk files eagerly so the /tmp dir doesn't bloat.
    for f in chunk_files:
        try:
            f.unlink()
        except OSError:
            pass
    return all_out, detected_lang


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------


def transcribe(
    audio_path: str,
    out_json: str,
    out_srt: str,
    vod_basename: str = "",
    config_path: Optional[str] = None,
) -> Dict:
    """Full Stage-2 transcription pipeline.

    Returns a summary dict: ``{duration_min, segments, words, backend, language}``.
    Writes ``out_json`` and ``out_srt`` with the canonical format.
    """
    cfg = load_speech_config(config_path)

    # Optional vocal separation — pre-processing step.
    if (cfg.get("vocal_separation") or {}).get("enabled"):
        try:
            # Import here so the base dep footprint stays unchanged.
            from vocal_sep import separate as _vocal_sep  # type: ignore
            vocal_path = f"{Path(audio_path).parent}/{Path(audio_path).stem}_vocals.wav"
            separated = _vocal_sep(
                audio_path, vocal_path,
                backend=(cfg["vocal_separation"].get("backend") or "demucs"),
                model=(cfg["vocal_separation"].get("model") or "htdemucs_ft"),
            )
            if separated and Path(separated).exists():
                print(
                    f"[SPEECH] using separated vocals from {separated}",
                    file=sys.stderr,
                )
                audio_path = separated
            else:
                print(
                    "[SPEECH] vocal_separation enabled but returned no file; "
                    "falling back to raw audio",
                    file=sys.stderr,
                )
        except Exception as e:
            print(
                f"[SPEECH] vocal_separation failed ({e}); falling back to raw audio",
                file=sys.stderr,
            )

    # Streamer-slang biasing.
    prompt = pick_initial_prompt(vod_basename, cfg.get("streamer_prompts_path"))
    if prompt:
        print(f"[SPEECH] initial_prompt: {prompt[:120]}{'...' if len(prompt) > 120 else ''}",
              file=sys.stderr)

    requested_backend = (cfg.get("backend") or "whisperx").lower()
    backend_used = None
    segments: List[dict] = []
    language = cfg.get("language") or "en"

    if requested_backend == "whisperx":
        if _whisperx_available():
            try:
                segments, language = transcribe_whisperx(audio_path, cfg, prompt)
                backend_used = "whisperx"
            except Exception as e:
                print(
                    f"[SPEECH] whisperx runtime error ({e}); "
                    f"falling back to faster-whisper",
                    file=sys.stderr,
                )
        else:
            print(
                "[SPEECH] whisperx package not available; "
                "falling back to faster-whisper",
                file=sys.stderr,
            )

    if backend_used is None:
        segments, language = transcribe_faster_whisper(audio_path, cfg, prompt)
        backend_used = "faster-whisper"

    # Write outputs.
    Path(out_json).parent.mkdir(parents=True, exist_ok=True)
    Path(out_json).write_text(json.dumps(segments, indent=2), encoding="utf-8")
    write_srt(segments, out_srt)

    duration_min = (segments[-1]["end"] / 60.0) if segments else 0.0
    word_count = sum(len((s.get("text") or "").split()) for s in segments)
    summary = {
        "duration_min": round(duration_min, 1),
        "segments": len(segments),
        "words": word_count,
        "backend": backend_used,
        "language": language,
    }
    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="Speech pipeline (Phase 3)")
    ap.add_argument("--audio", required=True, help="path to 16kHz mono WAV")
    ap.add_argument("--out-json", required=True, help="path to write transcript.json")
    ap.add_argument("--out-srt", required=True, help="path to write transcript.srt")
    ap.add_argument("--vod", default="", help="VOD basename for streamer-prompt match")
    ap.add_argument("--config", default=None, help="override config/speech.json path")
    args = ap.parse_args()

    summary = transcribe(
        args.audio, args.out_json, args.out_srt,
        vod_basename=args.vod, config_path=args.config,
    )
    json.dump(summary, sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    _cli()
