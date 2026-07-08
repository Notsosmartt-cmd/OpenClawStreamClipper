#!/usr/bin/env python3
"""Stage 2 — Transcription (+ audio events). Port of stage2_transcription.sh."""
from __future__ import annotations

import json
import os
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
    #
    # CLIP_REUSE_TRANSCRIPT (2026-07-04): a dev/harness speed flag that reuses a
    # cached transcript EVEN under --force. Transcription is deterministic, so
    # when you're forcing a reprocess only to re-run detection/render (the
    # phase_runner iteration loop), re-transcribing the same VOD wastes ~10 min
    # for an identical result. Default off = the strict force-re-transcribe
    # behavior above is unchanged.
    _reuse_transcript = os.environ.get("CLIP_REUSE_TRANSCRIPT", "").strip().lower() in (
        "1", "true", "yes", "on")
    _cache_ok = cached_json.exists() and cached_srt.exists()
    if _cache_ok and (not ctx.force or _reuse_transcript):
        if ctx.force and _reuse_transcript:
            log.log(f"CLIP_REUSE_TRANSCRIPT: reusing cached transcription for "
                    f"'{ctx.vod_basename}' despite --force (deterministic; skips ~10 min).")
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
    #
    # Speed #1 (2026-07-08, plan-pipeline-speed-2026-07): the scan output is
    # deterministic per VOD (~10-16 min serial) yet only the transcript was cached —
    # mirror that cache to `<stem>.audio_events.json` so re-runs skip the rescan. Same
    # reuse semantics as the transcript (reuse unless --force, minus the
    # CLIP_REUSE_TRANSCRIPT dev override). audio.wav extraction above is unaffected
    # (Stage 7 clip_tighten/sfx read it); ONLY the scan is skipped on a hit. A scan is
    # cached ONLY when valid (non-empty `windows`, no `skipped_reason`) so a transient
    # scanner error is never immortalized.
    events = p.work("audio_events.json")
    cached_events = p.transcriptions_dir / f"{stem}.audio_events.json"

    def _valid_events(path_) -> bool:
        try:
            d = json.loads(Path(path_).read_text(encoding="utf-8"))
            return isinstance(d.get("windows"), list) and len(d["windows"]) > 0 \
                and not d.get("skipped_reason")
        except Exception:
            return False

    if cached_events.exists() and _valid_events(cached_events) and (not ctx.force or _reuse_transcript):
        log.log(f"Found cached audio events for '{ctx.vod_basename}' — skipping scan.")
        shutil.copyfile(cached_events, events)
    elif audio.exists():
        if ctx.force and cached_events.exists():
            cached_events.unlink(missing_ok=True)  # discard stale under --force, like the transcript
        log.log("Tier-2 M2: scanning audio events (rhythmic / crowd / music)...")
        r = common.run_module(log, "audio_events.py",
                              ["--audio", str(audio), "--out", str(events)],
                              env=ctx.child_env(), check=False)
        if r.returncode != 0:
            events.write_text('{"windows": [], "skipped_reason": "scanner_error"}', encoding="utf-8")
        elif _valid_events(events):
            try:
                shutil.copyfile(events, cached_events)
                log.log(f"Audio events cached to {cached_events.name}")
            except OSError:
                pass
    else:
        events.write_text('{"windows": [], "skipped_reason": "no_audio_source"}', encoding="utf-8")
