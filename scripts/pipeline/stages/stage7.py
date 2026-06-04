#!/usr/bin/env python3
"""Stage 7 — Editing & Export. Port of stage7_render.sh.

Generates the clip manifest, extracts per-clip audio, batch-transcribes
captions, then renders each clip through the originality-aware FFmpeg filter
chain (blur_fill / camera_pan), with optional voiceover + music mix and a
fallback ladder. Stitch groups render last via stitch_render.py.

Windows specifics handled here:
  * hook font resolves to a Windows TTF (no /usr/share/fonts path)
  * in-filter paths (subtitles / textfile / fontfile) get colon-escaped
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from pipeline import common


# ---------------------------------------------------------------------------
# Windows helpers
# ---------------------------------------------------------------------------
def _resolve_font() -> str:
    cand = os.environ.get("CLIP_HOOK_FONT")
    if cand and Path(cand).exists():
        return cand
    for f in (r"C:\Windows\Fonts\arialbd.ttf", r"C:\Windows\Fonts\arial.ttf"):
        if Path(f).exists():
            return f
    return r"C:\Windows\Fonts\arial.ttf"


HOOK_FONT = _resolve_font()


def _ff(path) -> str:
    """Escape a filesystem path for use *inside* an FFmpeg filtergraph value
    (forward slashes + escaped drive colon)."""
    return str(path).replace("\\", "/").replace(":", "\\:")


def _parse_kv(text: str) -> dict:
    """Parse originality.py's shell `KEY=VALUE` output into a dict."""
    d: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        d[k.strip()] = v.strip().strip("'").strip('"')
    return d


# ---------------------------------------------------------------------------
# 7a — manifest
# ---------------------------------------------------------------------------
def _scrub(s) -> str:
    if not isinstance(s, str):
        s = str(s or "")
    return s.replace("|", "-").replace("\r", " ").replace("\n", " ").strip()


def _generate_manifest(ctx) -> list[dict]:
    p = ctx.paths
    moments = json.loads(p.scored_moments.read_text(encoding="utf-8"))
    rows: list[dict] = []
    lines: list[str] = []
    for m in moments:
        title = m["title"].replace("/", "-").replace("\\", "-").replace("|", "-").replace('"', "")
        title = "".join(c for c in title if c.isalnum() or c in " -")[:50].strip()
        if not title:
            title = f"Clip T{m['timestamp']}"
        clip_start = m.get("clip_start", max(0, m["timestamp"] - 15))
        clip_duration = m.get("clip_duration", 30)
        score = m["score"]
        score_str = f"{score:.3f}" if isinstance(score, float) else str(score)
        row = {
            "t": m["timestamp"],
            "title": title,
            "score": score_str,
            "category": _scrub(m.get("category", "unknown")),
            "description": _scrub(m.get("description", ""))[:500],
            "hook": _scrub(m.get("hook", "")),
            "segment_type": _scrub(m.get("segment_type", "unknown")),
            "clip_start": clip_start,
            "clip_duration": clip_duration,
        }
        rows.append(row)
        lines.append("|".join(str(x) for x in (
            row["t"], row["title"], row["score"], row["category"],
            row["description"], row["hook"], row["segment_type"],
            row["clip_start"], row["clip_duration"])))
    p.work("clip_manifest.txt").write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return rows


# ---------------------------------------------------------------------------
# render one clip
# ---------------------------------------------------------------------------
def _render_clip(ctx, row, speed_vf, speed_audio_filter) -> None:
    log = ctx.log
    p = ctx.paths
    env = ctx.child_env()
    T = row["t"]
    title = row["title"]
    category = row["category"]
    seg_type = row["segment_type"]
    hook = row["hook"]
    clip_start = max(0, int(float(row["clip_start"]))) if row["clip_start"] != "" else max(0, int(T) - 22)
    clip_length = int(float(row["clip_duration"])) if row["clip_duration"] != "" else 45

    # Per-moment meta (mirror_safe|vo_line|vo_placement|group_id|kind)
    meta_env = dict(env)
    meta_env["CLIP_T"] = str(T)
    meta = common.run_module(log, "stages/stage7_meta.py", [], env=meta_env, check=False, capture=True)
    parts = (meta.stdout or "").strip().split("|")
    parts += [""] * (5 - len(parts))
    mirror_safe = parts[0] or "false"
    vo_line, vo_placement, group_id = parts[1], parts[2], parts[3]
    kind = parts[4] or "solo"

    if kind == "stitch" and ctx.stitch:
        log.log(f"  Deferring stitch group member T={T} (group={group_id})")
        return

    orig = _parse_kv(common.run_module(
        log, "originality.py",
        [str(T), "true" if ctx.originality else "false", mirror_safe, ctx.framing, category],
        env=env, check=False, capture=True).stdout)

    clip_srt = p.work(f"clip_{T}.srt")
    if ctx.clip_speed != "1.0":
        clip_srt_render = p.work(f"clip_{T}_scaled.srt")
        common.run_module(log, "stages/helpers/srt_rescale.py",
                          [str(clip_srt), str(clip_srt_render), ctx.clip_speed], env=env, check=False)
    else:
        clip_srt_render = clip_srt

    clip_output = p.clips_dir / f"{title}.mp4"

    # --- AI editing-profiles dispatch -------------------------------------
    if ctx.style_profiles:
        moment_json = p.work(f"moment_{T}.json")
        if _extract_moment(p.scored_moments, T, moment_json):
            log.log(f"  [profile-mode] Rendering: {title} (T={T}s, category={category})")
            r = common.run_module(log, "profile_render.py", [
                "--moment-json", str(moment_json), "--src", str(ctx.vod_path),
                "--srt", str(clip_srt_render), "--out", str(clip_output),
                "--clip-start", str(clip_start), "--clip-duration", str(clip_length),
                "--speed", ctx.clip_speed,
                "--captions", "true" if ctx.captions_enabled else "false",
                "--hook", "true" if ctx.hook_caption_enabled else "false",
                "--hook-text", hook, "--temp-dir", str(p.work_dir),
                "--music-folder", ctx.music_bed,
            ], env=env, check=False)
            if r.returncode == 0 and clip_output.exists():
                _record_clip(ctx, row, clip_output, clip_length, profile=True)
                return
            log.warn(f"Profile render failed for T={T} — falling back to legacy render")

    # --- filter fragments --------------------------------------------------
    mirror_vf = ",hflip" if orig.get("MIRROR") == "true" else ""
    color_vf = (f"eq=brightness={orig['EQ_BRIGHTNESS']}:saturation={orig['EQ_SATURATION']}:"
                f"contrast={orig['EQ_CONTRAST']}:gamma={orig['EQ_GAMMA']},hue=h={orig['HUE_SHIFT']}")
    if orig.get("USE_VIGNETTE") == "true":
        color_vf += ",vignette=angle=PI/5"
    shake_vf = ""
    if orig.get("USE_SHAKE") == "true":
        a = orig["SHAKE_AMP"]
        shake_vf = f",crop=iw-{a}*2:ih-{a}*2:{a}+{a}*sin(t*2):{a}+{a}*cos(t*1.5)"

    blur_fill_vf = (
        f"{speed_vf},split[bg][fg];[bg]scale=1080:1920:force_original_aspect_ratio=increase,"
        f"crop=1080:1920,boxblur={orig['BLUR_RADIUS']}:{orig['BLUR_PASSES']}[blurred];"
        f"[fg]scale=1080:-2:force_original_aspect_ratio=decrease{mirror_vf}[sharp];"
        f"[blurred][sharp]overlay=(W-w)/2:(H-h)/2,{color_vf}{shake_vf}")

    if ctx.framing == "camera_pan":
        pan_path = p.work(f"clip_{T}_campath.json")
        pan_expr = ""
        if ctx.camera_pan and pan_path.exists():
            pan_expr = common.run_module(log, "face_pan.py", ["--emit-filter", str(pan_path)],
                                         env=env, check=False, capture=True).stdout.strip()
        if pan_expr:
            frame_vf = f"{speed_vf},{pan_expr}{mirror_vf},{color_vf}{shake_vf}"
        else:
            frame_vf = blur_fill_vf
    else:
        frame_vf = blur_fill_vf

    render_vf = frame_vf

    if ctx.hook_caption_enabled and hook:
        hook_file = p.work(f"clip_{T}_hook.txt")
        hook_file.write_text(_wrap_hook(hook), encoding="utf-8")
        render_vf += (
            f",drawtext=textfile='{_ff(hook_file)}':fontsize={orig['HOOK_FONTSIZE']}:"
            f"fontcolor={orig['HOOK_FG_COLOR']}:fontfile='{_ff(HOOK_FONT)}':box=1:"
            f"boxcolor={orig['HOOK_BOX_COLOR']}:boxborderw={orig['HOOK_BOX_BORDER']}:"
            f"x=(w-text_w)/2:y={orig['HOOK_Y']}:line_spacing=8")

    if ctx.captions_enabled:
        render_vf += (
            f",subtitles='{_ff(clip_srt_render)}':force_style='FontSize={orig['SUB_FONTSIZE']},"
            f"Bold=1,PrimaryColour={orig['SUB_PRIMARY']},OutlineColour={orig['SUB_OUTLINE_COL']},"
            f"Outline={orig['SUB_OUTLINE']},Alignment=2,MarginV={orig['SUB_MARGIN_V']}'")

    # --- audio layers ------------------------------------------------------
    vo_wav = ""
    music_wav = ""
    if ctx.tts_vo and vo_line:
        vw = p.work(f"clip_{T}_vo.wav")
        common.run_module(log, "piper_vo.py", [
            "--text", vo_line, "--out", str(vw), "--placement", vo_placement,
            "--clip-duration", str(clip_length), "--speed", ctx.clip_speed, "--tone", category,
        ], env=env, check=False)
        vo_wav = str(vw) if vw.exists() else ""
    if ctx.music_bed and Path(ctx.music_bed).is_dir():
        r = common.run_module(log, "music_pick.py", [
            "--library", ctx.music_bed, "--category", category, "--segment", seg_type,
            "--duration", str(clip_length), "--tier-c", "true" if ctx.music_tier_c else "false",
            "--seed", str(T),
        ], env=env, check=False, capture=True)
        music_wav = (r.stdout or "").strip()

    render_ok = _ffmpeg_render(ctx, clip_start, clip_length, render_vf,
                               speed_audio_filter, vo_wav, music_wav, clip_output, clip_length)

    if not render_ok:
        log.warn(f"Render failed for {title}. Retrying legacy blur-fill...")
        render_ok = _ffmpeg_legacy(ctx, clip_start, clip_length, speed_vf,
                                   speed_audio_filter, clip_output)
        if not render_ok:
            log.warn(f"Render completely failed for T={T}")
            return

    if clip_output.exists():
        _record_clip(ctx, row, clip_output, clip_length)


def _wrap_hook(hook: str) -> str:
    import textwrap
    lines = textwrap.wrap(hook.strip(), 22)[:3]
    return "\n".join(lines) if lines else hook[:60]


def _extract_moment(scored_path: Path, T, out: Path) -> bool:
    try:
        moments = json.loads(scored_path.read_text(encoding="utf-8"))
        m = next((x for x in moments if int(float(x.get("timestamp", -1))) == int(float(T))), None)
        if m is None:
            return False
        out.write_text(json.dumps(m, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False


_VENC = ["-c:v", "libx264", "-crf", "20", "-preset", "slow", "-profile:v", "high",
         "-level", "4.2", "-pix_fmt", "yuv420p", "-r", "30",
         "-b:v", "18M", "-maxrate", "20M", "-bufsize", "40M",
         "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"]


def _ffmpeg_render(ctx, start, length, render_vf, speed_audio_filter,
                   vo_wav, music_wav, out, clip_length) -> bool:
    if vo_wav or music_wav:
        mix_args = ["-i", str(ctx.vod_path)]
        idx = 1
        src_af = (f"{speed_audio_filter}," if speed_audio_filter else "") + "volume=1.0"
        audio_defs = f"[0:a]{src_af}[src_audio]"
        mix_ins = "[src_audio]"
        if vo_wav:
            mix_args += ["-i", vo_wav]
            audio_defs += f";[{idx}:a]volume=1.6,apad=whole_dur={clip_length}[vo_audio]"
            mix_ins += "[vo_audio]"
            idx += 1
        if music_wav:
            mix_args += ["-stream_loop", "-1", "-i", music_wav]
            audio_defs += f";[{idx}:a]atrim=0:{clip_length},volume=0.08[music_audio]"
            mix_ins += "[music_audio]"
            idx += 1
        filter_complex = (f"[0:v]{render_vf}[vout];{audio_defs};{mix_ins}"
                          f"amix=inputs={idx}:duration=first:dropout_transition=0:normalize=0[amixed];"
                          f"[amixed]volume=0.95[aout]")
        cmd = ["ffmpeg", "-nostdin", "-y", "-ss", str(start), "-t", str(length), *mix_args,
               "-filter_complex", filter_complex, "-map", "[vout]", "-map", "[aout]", *_VENC, str(out)]
    else:
        af = ["-af", speed_audio_filter] if speed_audio_filter else []
        cmd = ["ffmpeg", "-nostdin", "-y", "-ss", str(start), "-t", str(length),
               "-i", str(ctx.vod_path), "-vf", render_vf, *af, *_VENC, str(out)]
    return common.run_ffmpeg(cmd) == 0


def _ffmpeg_legacy(ctx, start, length, speed_vf, speed_audio_filter, out) -> bool:
    legacy_bg = ("split[bg][fg];[bg]scale=1080:1920:force_original_aspect_ratio=increase,"
                 "crop=1080:1920,boxblur=25:5[blurred];[fg]scale=1080:-2:"
                 "force_original_aspect_ratio=decrease[sharp];[blurred][sharp]overlay=(W-w)/2:(H-h)/2")
    af = ["-af", speed_audio_filter] if speed_audio_filter else []
    cmd = ["ffmpeg", "-nostdin", "-y", "-ss", str(start), "-t", str(length),
           "-i", str(ctx.vod_path), "-vf", f"{speed_vf},{legacy_bg}", *af,
           "-c:v", "libx264", "-crf", "23", "-preset", "medium",
           "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(out)]
    return common.run_ffmpeg(cmd) == 0


def _record_clip(ctx, row, out: Path, clip_length, profile: bool = False) -> None:
    try:
        mb = out.stat().st_size // 1048576
    except OSError:
        mb = 0
    tag = "[profile-mode] " if profile else ""
    ctx.log.log(f"  Done {tag}: {row['title']} — {mb}MB (category={row['category']})")
    with open(ctx.paths.clips_made, "a", encoding="utf-8") as f:
        f.write(f"{row['title']}|{row['score']}|{row['category']}|{row['description']}|"
                f"{mb}MB|{row['segment_type']}|{clip_length}s\n")


# ---------------------------------------------------------------------------
# stage entry
# ---------------------------------------------------------------------------
def run(ctx) -> None:
    log = ctx.log
    p = ctx.paths
    env = ctx.child_env()
    common.set_stage(log, "Stage 7/8 — Editing and Export")
    log.log("=== Stage 7/8 — Editing and Export ===")

    common.unload_model(log, ctx.llm_url, ctx.vision_model_stage6)

    # 7a — manifest
    log.log("  Generating clip manifest...")
    rows = _generate_manifest(ctx)
    log.log(f"  Manifest: {len(rows)} clips to process")

    # 7b — extract clip audio
    log.log("  Extracting audio for all clips...")
    for row in rows:
        start = max(0, int(float(row["clip_start"]))) if row["clip_start"] != "" else max(0, int(row["t"]) - 22)
        length = int(float(row["clip_duration"])) if row["clip_duration"] != "" else 45
        common.run_ffmpeg(["ffmpeg", "-nostdin", "-y", "-ss", str(start), "-t", str(length),
                           "-i", str(ctx.vod_path), "-vn", "-acodec", "pcm_s16le", "-ar", "16000",
                           "-ac", "1", str(p.work(f"clip_audio_{row['t']}.wav"))])

    # 7c — batch caption transcription (single Whisper load)
    log.log("  Batch transcribing all clips (single Whisper load)...")
    cap_env = dict(env)
    cap_env["CLIP_WHISPER_MODEL"] = ctx.whisper_model
    common.run_module(log, "stages/stage7_transcribe.py", [], env=cap_env, check=False)

    # 7d — render
    log.log(f"  Rendering all clips (framing={ctx.framing}, originality={ctx.originality})...")
    speed_vf = f"setpts=PTS/{ctx.clip_speed}" if ctx.clip_speed != "1.0" else "null"
    speed_audio_filter = (f"rubberband=tempo={ctx.clip_speed}:pitch={ctx.clip_speed}"
                          if ctx.clip_speed != "1.0" else "")
    for row in rows:
        try:
            _render_clip(ctx, row, speed_vf, speed_audio_filter)
        except Exception as e:  # noqa: BLE001
            log.warn(f"render error for T={row['t']}: {e}")

    # 7e — stitch groups
    groups_file = p.work("moment_groups.json")
    if ctx.stitch and groups_file.exists():
        st_env = dict(env)
        st_env.update({
            "CLIPS_DIR_ENV": str(p.clips_dir), "TEMP_DIR_ENV": str(p.work_dir),
            "VOD_PATH_ENV": str(ctx.vod_path), "CLIP_FRAMING_ENV": ctx.framing,
            "CLIP_ORIGINALITY_ENV": "true" if ctx.originality else "false",
            "CLIP_SPEED_ENV": ctx.clip_speed,
            "CLIP_CAPTIONS_ENV": "true" if ctx.captions_enabled else "false",
            "CLIP_HOOK_ENV": "true" if ctx.hook_caption_enabled else "false",
        })
        log.log("  Rendering stitch group(s)...")
        common.run_module(log, "stitch_render.py", [], env=st_env, check=False)
