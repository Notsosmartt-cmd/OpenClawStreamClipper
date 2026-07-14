#!/usr/bin/env python3
"""Stage 2 — Transcription (+ audio events). Port of stage2_transcription.sh.

Wave A1 (plan-speed-wave3, 2026-07-14): the Tier-2 M2 audio-events scan is
CPU-bound and the transcription is GPU-bound, yet they ran sequentially. Both
only need ``audio.wav``, so the scan now launches in a background thread the
moment extraction finishes and joins after transcription — on a fresh VOD the
scan's ~3-4 min disappear into Whisper's wall time. ``CLIP_STAGE2_OVERLAP=0``
restores the exact serial order.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
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
    _will_transcribe = not (_cache_ok and (not ctx.force or _reuse_transcript))

    # C3 (Speed Wave 2, plan-serving-stack-2026-07): only free VRAM when Whisper
    # will actually run. On the cached-transcript path Whisper is skipped and the
    # remaining Stage-2 work (ffmpeg audio extract + the CPU audio-events scan) needs
    # no GPU — so evicting every LM Studio model just forces Stage 3 to reload the
    # 35B (~30-60 s) for nothing. Keeping it resident on cached re-runs makes Stage 3's
    # load_model() a no-op (it already skips when the model is loaded). Behavior on the
    # fresh path is unchanged: Whisper still gets a fully free GPU. Escape hatch:
    # CLIP_STAGE2_ALWAYS_UNLOAD=1 restores the old unconditional eviction.
    _always_unload = os.environ.get("CLIP_STAGE2_ALWAYS_UNLOAD", "").strip().lower() in (
        "1", "true", "yes", "on")
    if _will_transcribe or _always_unload:
        log.log("Freeing VRAM: unloading LM Studio models before Whisper needs the GPU.")
        for m in dict.fromkeys([ctx.text_model, ctx.vision_model,
                                ctx.text_model_passb, ctx.vision_model_stage6]):
            common.unload_model(log, ctx.llm_url, m)
    else:
        log.log("Cached transcript path — keeping LM Studio model resident "
                "(no Whisper this run; skips a needless ~30-60 s reload in Stage 3).")

    # ------------------------------------------------------------------
    # Audio extraction — hoisted ahead of BOTH branches (A1). The WAV is the
    # shared input of transcription, the audio-events scan, and Stage 7's
    # clip_tighten/sfx, so it must exist before anything else starts.
    # (2026-06-04 Delaware fix preserved: cached-transcript runs need it too.)
    # ------------------------------------------------------------------
    if _will_transcribe or not audio.exists():
        log.log("Extracting audio track...")
        common.run_ffmpeg(["ffmpeg", "-y", "-i", str(ctx.vod_path), "-vn",
                           "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", str(audio)])
        if _will_transcribe:
            log.log(f"Audio duration: {common.ffprobe_duration(audio)}s")

    # ------------------------------------------------------------------
    # Tier-2 M2 — audio events scan (boost-only signals for Pass A).
    #
    # Speed #1 (2026-07-08, plan-pipeline-speed-2026-07): deterministic per VOD →
    # mirrored to `<stem>.audio_events.json`; re-runs skip the rescan. A scan is
    # cached ONLY when valid (non-empty `windows`, no `skipped_reason`) so a
    # transient scanner error is never immortalized.
    #
    # Wave A1: on a scan-needed run the scan launches HERE (audio.wav is ready)
    # in a background thread and joins after transcription. CPU (scan) and GPU
    # (Whisper) don't contend; Logger.write is lock-protected so interleaved
    # child lines are safe (same contract as Pass B's moment-parallel logs).
    # ------------------------------------------------------------------
    events = p.work("audio_events.json")
    cached_events = p.transcriptions_dir / f"{stem}.audio_events.json"

    def _valid_events(path_) -> bool:
        try:
            d = json.loads(Path(path_).read_text(encoding="utf-8"))
            return isinstance(d.get("windows"), list) and len(d["windows"]) > 0 \
                and not d.get("skipped_reason")
        except Exception:
            return False

    def _run_scan() -> None:
        """The exact pre-A1 scan body: scan → sentinel-on-error → mirror cache."""
        log.log("Tier-2 M2: scanning audio events (rhythmic / crowd / music)...")
        r = common.run_module(log, "audio_events.py",
                              ["--audio", str(audio), "--out", str(events)],
                              env=ctx.child_env(), check=False)
        if r.returncode != 0:
            events.write_text('{"windows": [], "skipped_reason": "scanner_error"}',
                              encoding="utf-8")
        elif _valid_events(events):
            try:
                shutil.copyfile(events, cached_events)
                log.log(f"Audio events cached to {cached_events.name}")
            except OSError:
                pass

    _overlap = os.environ.get("CLIP_STAGE2_OVERLAP", "1").strip().lower() in (
        "1", "true", "yes", "on")
    _scan_needed = False
    scan_thread: threading.Thread | None = None

    if cached_events.exists() and _valid_events(cached_events) and (not ctx.force or _reuse_transcript):
        log.log(f"Found cached audio events for '{ctx.vod_basename}' — skipping scan.")
        shutil.copyfile(cached_events, events)
    elif audio.exists():
        if ctx.force and cached_events.exists():
            cached_events.unlink(missing_ok=True)  # discard stale under --force, like the transcript
        _scan_needed = True
        if _overlap:
            log.log("A1 overlap: audio-events scan running in parallel with transcription.")
            scan_thread = threading.Thread(target=_run_scan, name="audio-events-scan",
                                           daemon=True)
            scan_thread.start()
    else:
        events.write_text('{"windows": [], "skipped_reason": "no_audio_source"}',
                          encoding="utf-8")

    # ------------------------------------------------------------------
    # Transcription (foreground; GPU) — cached copy or the speech module.
    # The scan join lives in `finally` so even a transcription failure can't
    # leave an orphan scan child writing into the shared work dir (BUG 72
    # family); the scan is bounded (~minutes), so a slow-fail is acceptable.
    # ------------------------------------------------------------------
    try:
        if _cache_ok and (not ctx.force or _reuse_transcript):
            if ctx.force and _reuse_transcript:
                log.log(f"CLIP_REUSE_TRANSCRIPT: reusing cached transcription for "
                        f"'{ctx.vod_basename}' despite --force (deterministic; skips ~10 min).")
            log.log(f"Found cached transcription for '{ctx.vod_basename}'. Skipping transcription.")
            shutil.copyfile(cached_json, p.transcript_json)
            shutil.copyfile(cached_srt, p.transcript_srt)
            _print_cached_stats(ctx)
        else:
            if ctx.force and (cached_json.exists() or cached_srt.exists()):
                log.log(f"Force reprocess: discarding cached transcription for "
                        f"'{ctx.vod_basename}' and re-transcribing.")
                cached_json.unlink(missing_ok=True)
                cached_srt.unlink(missing_ok=True)
            else:
                log.log("No cached transcription found.")
            log.log("Transcribing via speech module...")
            env = ctx.child_env()
            env["CLIP_WHISPER_MODEL"] = ctx.whisper_model
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
    finally:
        if scan_thread is not None:
            if scan_thread.is_alive():
                log.log("A1 overlap: waiting for the audio-events scan to finish...")
            scan_thread.join()

    # Serial fallback (CLIP_STAGE2_OVERLAP=0) — the exact legacy order.
    if _scan_needed and scan_thread is None:
        _run_scan()
