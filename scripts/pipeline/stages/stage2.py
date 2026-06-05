#!/usr/bin/env python3
"""Stage 2 — Transcription (+ audio events). Port of stage2_transcription.sh."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from pipeline import common


def _print_cached_stats(ctx) -> None:
    try:
        data = json.loads(ctx.paths.transcript_json.read_text(encoding="utf-8"))
        if data:
            ctx.log.line(json.dumps({
                "duration_min": round(data[-1]["end"] / 60, 1),
                "segments": len(data),
                "words": sum(len(s["text"].split()) for s in data),
                "cached": True,
            }))
    except Exception:
        pass


def run(ctx) -> None:
    log = ctx.log
    p = ctx.paths
    common.set_stage(log, "Stage 2/8 — Audio Transcription")
    log.log("=== Stage 2/8 — Audio Transcription ===")

    # Free VRAM: unload every LM Studio model before Whisper needs the GPU.
    for m in dict.fromkeys([ctx.text_model, ctx.vision_model,
                            ctx.text_model_passb, ctx.vision_model_stage6]):
        common.unload_model(log, ctx.llm_url, m)

    audio = p.work("audio.wav")
    p.transcriptions_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(ctx.vod_basename).stem
    cached_json = p.transcriptions_dir / f"{stem}.transcript.json"
    cached_srt = p.transcriptions_dir / f"{stem}.transcript.srt"

    # --force (the dashboard "Force reprocess" checkbox / ctx.force) must
    # re-transcribe from scratch and REPLACE any stale cache — otherwise a bad
    # or outdated transcript would be reused forever. Without force we reuse the
    # cache (transcription is the slowest GPU stage).
    if cached_json.exists() and cached_srt.exists() and not ctx.force:
        log.log(f"Found cached transcription for '{ctx.vod_basename}'. Skipping transcription.")
        shutil.copyfile(cached_json, p.transcript_json)
        shutil.copyfile(cached_srt, p.transcript_srt)
        _print_cached_stats(ctx)
        # 2026-06-04 (Delaware fix): even on the cached-transcript path we
        # still need audio.wav for the Tier-2 M2 audio-events scan below.
        # Before this fix, cached re-runs wrote `{"skipped_reason":
        # "no_audio_source"}` because audio extraction only happened in the
        # non-cached branch. That silently disabled rhythmic_speech /
        # crowd_response / music_dominance signals on every re-run, which
        # was one of the three failures stacked behind the rakai Delaware
        # rap battle being missed. See case-rap-battle-missed §Diagnosis 4.
        if not audio.exists():
            log.log("Cached transcript path — extracting audio track for Tier-2 M2 audio events...")
            common.run_ffmpeg(["ffmpeg", "-y", "-i", str(ctx.vod_path), "-vn",
                               "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", str(audio)])
    else:
        if ctx.force and (cached_json.exists() or cached_srt.exists()):
            log.log(f"Force reprocess: discarding cached transcription for '{ctx.vod_basename}' and re-transcribing.")
            cached_json.unlink(missing_ok=True)
            cached_srt.unlink(missing_ok=True)
        else:
            log.log("No cached transcription found.")
        log.log("Transcribing via speech module...")
        env = ctx.child_env()
        env["CLIP_WHISPER_MODEL"] = ctx.whisper_model
        log.log("Extracting audio track...")
        common.run_ffmpeg(["ffmpeg", "-y", "-i", str(ctx.vod_path), "-vn",
                           "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", str(audio)])
        log.log(f"Audio duration: {common.ffprobe_duration(audio)}s")
        try:
            common.run_module(log, "speech.py", [
                "--audio", str(audio),
                "--out-json", str(p.transcript_json),
                "--out-srt", str(p.transcript_srt),
                "--vod", ctx.vod_basename,
            ], env=env, check=True)
        except subprocess.CalledProcessError:
            log.err("speech.py transcription failed")
            raise common.PipelineExit(1, json.dumps({"status": "transcription_failed", "clips": 0}))
        shutil.copyfile(p.transcript_json, cached_json)
        shutil.copyfile(p.transcript_srt, cached_srt)
        log.log(f"Transcription cached to {p.transcriptions_dir}")

    log.log(f"Transcription complete. Output: {p.transcript_json}")

    # Tier-2 M2 — audio events scan (boost-only signals for Pass A).
    # The cached-transcript path above now also extracts audio.wav, so
    # this branch fires on both fresh and cached runs (post-2026-06-04).
    events = p.work("audio_events.json")
    if audio.exists():
        log.log("Tier-2 M2: scanning audio events (rhythmic / crowd / music)...")
        r = common.run_module(log, "audio_events.py",
                              ["--audio", str(audio), "--out", str(events)],
                              env=ctx.child_env(), check=False)
        if r.returncode != 0:
            events.write_text('{"windows": [], "skipped_reason": "scanner_error"}', encoding="utf-8")
    else:
        events.write_text('{"windows": [], "skipped_reason": "no_audio_source"}', encoding="utf-8")
