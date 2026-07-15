#!/usr/bin/env python3
"""Stage 1 — Discovery + chat fetch. Port of scripts/stages/stage1_discovery.sh.

Selects the VOD to process (honoring --list / --vod / --force / processed.log)
and, when configured, fetches Twitch chat. Sets ctx.vod_path / vod_basename /
vod_duration and writes chat markers to the work dir.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from pipeline import common
from pipeline.common import PipelineExit


def _find_vods(vods_dir: Path) -> list[Path]:
    found = [p for p in vods_dir.glob("*")
             if p.is_file() and p.suffix.lower() in (".mp4", ".mkv")]
    return sorted(found, key=lambda p: p.name)


def _is_processed(processed_log: Path, basename: str) -> bool:
    if not processed_log.exists():
        return False
    try:
        return basename in processed_log.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False


def run(ctx) -> None:
    log = ctx.log
    p = ctx.paths
    common.set_stage(log, "Stage 1/8 — Discovery")
    log.log("=== Stage 1/8 — Discovery ===")

    p.clips_dir.mkdir(parents=True, exist_ok=True)
    p.work_dir.mkdir(parents=True, exist_ok=True)
    p.processed_log.touch()

    all_vods = _find_vods(p.vods_dir)
    if not all_vods:
        log.log(f"No VOD files found in {p.vods_dir}. Nothing to process.")
        raise PipelineExit(0, json.dumps({"status": "no_vods", "clips": 0}))

    # --- --list mode: inventory + exit -------------------------------------
    if ctx.list_mode:
        vods = []
        for vod in all_vods:
            stem = vod.stem
            try:
                size_mb = vod.stat().st_size // 1048576
            except OSError:
                size_mb = 0
            dur = common.ffprobe_duration(vod)
            cached = (p.transcriptions_dir / f"{stem}.transcript.json").exists()
            vods.append({
                "name": vod.name,
                "size_mb": size_mb,
                "duration_min": dur // 60,
                "processed": _is_processed(p.processed_log, vod.name),
                "transcription_cached": cached,
            })
        raise PipelineExit(0, json.dumps({"status": "list", "vods": vods}))

    # --- pick the VOD ------------------------------------------------------
    vod_path: Path | None = None
    if ctx.target_vod:
        # case-insensitive partial match; --vod always (re)processes
        needle = ctx.target_vod.lower()
        for vod in all_vods:
            if needle in vod.name.lower():
                vod_path = vod
                break
        if vod_path is None:
            log.err(f"No VOD matching '{ctx.target_vod}' found in {p.vods_dir}.")
            raise PipelineExit(0, json.dumps({
                "status": "vod_not_found", "clips": 0,
                "searched": ctx.target_vod,
                "available": [v.name for v in all_vods],
            }))
        if _is_processed(p.processed_log, vod_path.name):
            log.log(f"Re-processing VOD: {vod_path.name} (--vod override)")
        else:
            log.log(f"Targeted VOD: {vod_path.name} (--vod match for '{ctx.target_vod}')")
    elif ctx.force:
        vod_path = all_vods[-1]
        log.log(f"Force re-processing latest VOD: {vod_path.name}")
    else:
        new_vods = [v for v in all_vods if not _is_processed(p.processed_log, v.name)]
        if not new_vods:
            log.log(f"All {len(all_vods)} VOD(s) already processed. Nothing new.")
            raise PipelineExit(0, json.dumps({
                "status": "all_processed", "clips": 0,
                "available": [v.name for v in all_vods],
            }))
        vod_path = new_vods[0]

    ctx.vod_path = vod_path
    ctx.vod_basename = vod_path.name
    log.log(f"Processing: {ctx.vod_basename}")
    # Owner req 2026-07-15: the dashboard must always show WHICH VOD is being
    # worked on — single runs included. Batch runs pass their position via
    # ctx.batch_pos; single runs show as 1/1 (the UI renders just the name).
    common.set_vod(ctx.vod_basename, *(getattr(ctx, "batch_pos", None) or (1, 1)))

    # Verify the file is fully transferred (size stable across a short wait).
    try:
        size1 = vod_path.stat().st_size
        time.sleep(5)
        size2 = vod_path.stat().st_size
    except OSError:
        size1, size2 = 0, 1
    if size1 != size2:
        log.err(f"File still being written ({size1} != {size2}). Aborting.")
        raise PipelineExit(1, json.dumps({"status": "file_incomplete", "clips": 0}))

    ctx.vod_duration = common.ffprobe_duration(vod_path)
    log.log(f"VOD duration: {ctx.vod_duration // 60} minutes ({ctx.vod_duration} seconds)")

    _chat_discovery(ctx)


def _chat_discovery(ctx) -> None:
    """Stage 1b — use a local chat file if present, else try auto-fetch.
    Failure collapses gracefully; downstream keys off chat_available.txt."""
    log = ctx.log
    p = ctx.paths
    chat_dir = p.vods_dir / ".chat"
    chat_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(ctx.vod_basename).stem
    chat_file = chat_dir / f"{stem}.jsonl"
    chat_available = False

    if chat_file.exists() and chat_file.stat().st_size > 0:
        n = sum(1 for _ in chat_file.open(encoding="utf-8", errors="ignore"))
        log.log(f"Chat data found: {chat_file} ({n} records)")
        chat_available = True
    else:
        log.log(f"No local chat file at {chat_file} — checking auto-fetch config")
        env = ctx.child_env()
        try:
            r = common.run_module(
                log, "stages/stage1_fetch.py",
                [ctx.vod_basename, str(chat_file)],
                env=env, check=False, capture=True,
            )
            fetch_cmd = (r.stdout or "").strip()
        except Exception as e:  # noqa: BLE001
            fetch_cmd = ""
            log.warn(f"chat fetch-config probe failed: {e}")

        if fetch_cmd.startswith("FETCH"):
            parts = fetch_cmd.split()
            # FETCH <vid> <cid> <delay>
            vid = parts[1] if len(parts) > 1 else ""
            cid = parts[2] if len(parts) > 2 else ""
            delay = parts[3] if len(parts) > 3 else "0"
            log.log(f"Chat auto-fetch: Twitch VOD ID {vid} via GraphQL (delay {delay}ms)")
            try:
                common.run_module(
                    log, "chat_fetch.py",
                    ["fetch", "--vod-id", vid, "--out", str(chat_file),
                     "--client-id", cid, "--delay-ms", delay],
                    env=env, check=False,
                )
            except Exception as e:  # noqa: BLE001
                log.warn(f"Chat auto-fetch failed: {e}")
            if chat_file.exists() and chat_file.stat().st_size > 0:
                chat_available = True
                log.log("Chat auto-fetch succeeded")
            else:
                log.warn("Chat auto-fetch returned 0 records; VOD may be too old/private")
        else:
            log.log(f"Chat auto-fetch: {fetch_cmd or '(no config)'}")

    ctx.chat_available = chat_available
    ctx.chat_path = str(chat_file)
    (p.work_dir / "chat_available.txt").write_text(
        "true" if chat_available else "false", encoding="utf-8")
    (p.work_dir / "chat_path.txt").write_text(str(chat_file), encoding="utf-8")
