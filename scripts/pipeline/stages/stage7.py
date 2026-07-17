#!/usr/bin/env python3
"""Stage 7 — Editing & Export. Port of stage7_render.sh.

Generates the clip manifest, extracts per-clip audio, batch-transcribes
captions, then renders each clip through the originality-aware FFmpeg filter
chain (blur_fill / camera_pan), with optional voiceover + music mix and a
fallback ladder. Stitch groups render last via stitch_render.py.

Windows specifics handled here:
  * hook font resolves to a Windows TTF (no /usr/share/fonts path)
  * in-filter paths (subtitles / textfile / fontfile) get colon-escaped

2026-06-04: 7b clip-audio extraction and 7d render loop are now parallel-
dispatched via ``ThreadPoolExecutor`` — each clip's ffmpeg invocation is
independent and CPU-bound (blur-fill + subtitle burn), so running 4
concurrently on the i9-13900K saturates the cores without oversubscription.
Tune via ``STAGE7_WORKERS`` env var (default 4, 1 = force serial).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from pipeline import common


# Default render-worker count. 4 × ~6 threads per ffmpeg ≈ 24 cores on the
# i9-13900K (24c/32t). Higher counts oversubscribe and cause contention.
_DEFAULT_RENDER_WORKERS = 4


def _resolve_render_workers() -> int:
    """``STAGE7_WORKERS`` env override → ``_DEFAULT_RENDER_WORKERS``.
    Set to 1 to force the original serial render loop."""
    env = os.environ.get("STAGE7_WORKERS", "").strip()
    if env:
        try:
            v = int(env)
            if v > 0:
                return v
        except ValueError:
            pass
    return _DEFAULT_RENDER_WORKERS


# ---------------------------------------------------------------------------
# Windows helpers
# ---------------------------------------------------------------------------
def _resolve_font() -> str:
    cand = os.environ.get("CLIP_HOOK_FONT")
    if cand and Path(cand).exists():
        return cand
    # Bundled Montserrat Black first — matches the CapCut subtitle captions.
    bundled = Path(__file__).resolve().parents[3] / "assets" / "fonts" / "Montserrat-Black.ttf"
    if bundled.is_file():
        return str(bundled)
    for f in (r"C:\Windows\Fonts\seguibl.ttf", r"C:\Windows\Fonts\arialbd.ttf", r"C:\Windows\Fonts\arial.ttf"):
        if Path(f).exists():
            return f
    return r"C:\Windows\Fonts\arial.ttf"


HOOK_FONT = _resolve_font()


def _resolve_caption_font() -> tuple[str, str]:
    """(ass_fontname, fontsdir) for CapCut-style captions. Prefer the bundled
    Montserrat Black so output is identical anywhere; else a heavy installed
    sans. fontsdir lets libass find the bundled TTF (not system-installed)."""
    fonts_dir = Path(__file__).resolve().parents[3] / "assets" / "fonts"
    if (fonts_dir / "Montserrat-Black.ttf").is_file():
        return "Montserrat Black", str(fonts_dir)
    for path, ass in ((r"C:\Windows\Fonts\seguibl.ttf", "Segoe UI Black"),
                      (r"C:\Windows\Fonts\ariblk.ttf", "Arial Black")):
        if Path(path).exists():
            return ass, str(Path(path).parent)
    return "Arial", str(fonts_dir)


# CapCut-style captions: bold font + bundled fonts dir + accent/case dials.
CAPTION_FONT, CAPTION_FONTS_DIR = _resolve_caption_font()
CAPTION_PRESET = os.environ.get("CLIP_CAPTION_PRESET", "capcut").strip() or "capcut"
CAPTION_ACCENT = os.environ.get("CLIP_CAPTION_ACCENT", "yellow").strip() or "yellow"
CAPTION_CAPS = os.environ.get("CLIP_CAPTION_CAPS", "false").strip() or "false"


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


def _row_from_moment(m: dict) -> dict:
    """One manifest row from one enriched moment. D6: shared by
    ``_generate_manifest`` AND the stage-6 overlap consumer so early renders
    are built from EXACTLY the same row shape."""
    title = m["title"].replace("/", "-").replace("\\", "-").replace("|", "-").replace('"', "")
    title = "".join(c for c in title if c.isalnum() or c in " -")[:50].strip()
    if not title:
        title = f"Clip T{m['timestamp']}"
    # Anomaly-lane provenance in the FILENAME (owner req 2026-07-08): clips the
    # cross-modal anomaly lane proposed are prefixed ANOMALY_ so they're identifiable
    # at a glance in the clips folder (and thus in effects_log, which keys by title).
    # `src` survives from Stage 4 (hype_moments) through Stage 6 (preserved there).
    if str(m.get("src", "")).upper() == "ANOMALY":
        title = f"ANOMALY_{title}"
    clip_start = m.get("clip_start", max(0, m["timestamp"] - 15))
    clip_duration = m.get("clip_duration", 30)
    score = m["score"]
    score_str = f"{score:.3f}" if isinstance(score, float) else str(score)
    return {
        "t": m["timestamp"],
        "title": title,
        "score": score_str,
        "category": _scrub(m.get("category", "unknown")),
        "description": _scrub(m.get("description", ""))[:500],
        "hook": _scrub(m.get("hook", "")),
        "segment_type": _scrub(m.get("segment_type", "unknown")),
        "clip_start": clip_start,
        "clip_duration": clip_duration,
        # P-TIGHT exemption inputs (owner review 2026-07-08): pattern survives
        # Stage 6 now; without it the rap/freestyle exemption never fired.
        "primary_pattern": _scrub(m.get("primary_pattern") or ""),
        # S4.5 text-judge score (0-10) rides along for the durable per-clip
        # score index written at render time (_record_clip).
        "judge": (m.get("s45_judge") or {}).get("score"),
    }


def _generate_manifest(ctx) -> list[dict]:
    p = ctx.paths
    moments = json.loads(p.scored_moments.read_text(encoding="utf-8"))
    rows: list[dict] = []
    lines: list[str] = []
    for m in moments:
        row = _row_from_moment(m)
        rows.append(row)
        lines.append("|".join(str(x) for x in (
            row["t"], row["title"], row["score"], row["category"],
            row["description"], row["hook"], row["segment_type"],
            row["clip_start"], row["clip_duration"])))
    p.work("clip_manifest.txt").write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    # P2.2 — A/B variants: carry Stage 6's variant-B caption onto the row and tag
    # the top-N clips (moments arrive score-sorted from scored_moments) as
    # eligible for a second render. Only these get a B; default off upstream.
    try:
        _ab_top_n = int(os.environ.get("CLIP_AB_VARIANTS_TOP_N", "5") or "5")
    except ValueError:
        _ab_top_n = 5
    for _i, (_m, _row) in enumerate(zip(moments, rows)):
        _row["hook_variants"] = _m.get("hook_variants") or []
        _row["ab_eligible"] = _i < _ab_top_n
        _row["post_kit"] = _m.get("post_kit") or {}     # P2.3 sidecar payload
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

    # P-TIGHT (owner 2026-07-05): trim setup/filler around the payoff for punchline-type
    # clips only (storytime/rap/emotional exempt). Flag CLIP_TIGHT_PUNCHLINE, default OFF
    # -> returns the inputs unchanged. Applied HERE so SFX-anchor + cold-open + render all
    # use the tightened window. Failure-soft: any error keeps the original boundaries.
    try:
        import sys as _sys
        from pathlib import Path as _P
        _sys.path.insert(0, str(_P(__file__).resolve().parents[2] / "lib"))
        import clip_tighten as _ctgh
        _ns, _nl = _ctgh.tighten(
            {"timestamp": T, "category": category, "primary_category": category,
             "primary_pattern": row.get("primary_pattern", ""),
             "segment_type": seg_type},
            clip_start, clip_length, temp_dir=str(p.work_dir))
        if (abs(_ns - clip_start) > 0.4 or abs(_nl - clip_length) > 0.4):
            _head_cut = _ns - clip_start
            log.log(f"  [p-tight] {clip_start}+{clip_length}s -> {_ns}+{_nl}s (T={T} {category})")
            if _head_cut > 8.0:
                # Title decoherence guard (owner review 2026-07-08): the title/hook were
                # generated at Stage 6 over the FULL window; a deep head cut can remove
                # the content they reference (Coke-Machine case). Flag it for review
                # until tighten runs pre-Stage-6 (single source of truth).
                log.log(f"  [p-tight] WARNING T={T}: head cut {_head_cut:.1f}s — title/hook may "
                        f"reference trimmed setup (re-check caption vs video)")
            clip_start, clip_length = _ns, _nl
    except Exception as _te:
        log.warn(f"p-tight skipped for T={T}: {_te}")

    # Per-moment meta (mirror_safe|vo_line|vo_placement|group_id|kind)
    meta_env = dict(env)
    meta_env["CLIP_T"] = str(T)
    meta = common.run_module(log, "stages/stage7_meta.py", [], env=meta_env, check=False, capture=True)
    parts = (meta.stdout or "").strip().split("|")
    parts += [""] * (5 - len(parts))
    mirror_safe = parts[0] or "false"
    vo_line, vo_placement, group_id = parts[1], parts[2], parts[3]
    kind = parts[4] or "solo"

    if kind == "stitch" and (ctx.stitch or ctx.arc_stitch):
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
                _maybe_companion_short(ctx, row, clip_output, clip_start, clip_length)
                _maybe_cold_open(ctx, row, clip_output, clip_start, clip_length)
                _record_clip(ctx, row, clip_output, clip_length, profile=True)
                _maybe_ab_variant(ctx, row, clip_start, clip_length,
                                  clip_srt_render, moment_json)
                _maybe_write_post_kit(ctx, row)
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
            f"borderw={orig['HOOK_BORDER_W']}:bordercolor={orig['HOOK_BORDER_COLOR']}@0.9:"
            f"x=(w-text_w)/2:y={orig['HOOK_Y']}:line_spacing=8")

    if ctx.captions_enabled:
        # CapCut-style word-box captions: build an ASS from the word-level SRT
        # and burn it with the bundled font dir so libass finds Montserrat.
        clip_ass = p.work(f"clip_{T}.ass")
        cap = common.run_module(log, "kinetic_captions.py", [
            "--srt", str(clip_srt_render), "--out", str(clip_ass),
            "--preset", CAPTION_PRESET, "--font", CAPTION_FONT,
            "--accent", CAPTION_ACCENT, "--caps", CAPTION_CAPS,
        ], env=env, check=False)
        if cap.returncode == 0 and clip_ass.exists():
            render_vf += (f",subtitles='{_ff(clip_ass)}'"
                          f":fontsdir='{_ff(CAPTION_FONTS_DIR)}'")
        else:
            # ASS generation failed (e.g. empty SRT) — fall back to a flat burn.
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
        # Companion punchline-only short (CLIP_COMPANION_SHORTS) BEFORE cold-open: a straight
        # sub-cut of the finished clip so captions/effects are inherited + aligned.
        _maybe_companion_short(ctx, row, clip_output, clip_start, clip_length)
        _maybe_cold_open(ctx, row, clip_output, clip_start, clip_length)
        _record_clip(ctx, row, clip_output, clip_length)
        _maybe_write_post_kit(ctx, row)


def _probe_duration(path: str) -> float | None:
    """ffprobe a file's duration (seconds), or None on any failure. Used to
    integrity-check the cold-open output before atomically replacing the clip —
    ffmpeg can exit 0 with a truncated file (disk full / OOM mid-write)."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=30,
        )
        return float(r.stdout.strip())
    except (subprocess.SubprocessError, ValueError):
        return None


def _maybe_cold_open(ctx, row, clip_output: Path, clip_start, clip_length) -> None:
    """When CLIP_COLD_OPEN is on, prepend a cold-open teaser (tease of the
    run-up to the payoff + whoosh/flash into the clip) in place. Implements
    concepts/hook-engineering-2026-06. Failure-soft: on any non-zero / error the
    original clip is left untouched (cold_open.py writes a temp and we only swap
    it in on success)."""
    if not getattr(ctx, "cold_open", False):
        return
    try:
        T = row["t"]
        tmp = ctx.paths.work(f"clip_{T}_coldopen.mp4")
        r = common.run_module(ctx.log, "cold_open.py", [
            "--vod", str(ctx.vod_path), "--clip", str(clip_output),
            "--out", str(tmp), "--payoff", str(float(T)),
            "--clip-start", str(float(clip_start)),
            "--clip-duration", str(float(clip_length)),
        ], env=ctx.child_env(), check=False)
        if r.returncode == 0 and tmp.exists() and tmp.stat().st_size > 1024:
            # Integrity gate before the atomic swap: the cold-open output is the
            # clip PLUS the teaser, so its duration must be >= ~the original
            # clip. A shorter result means a truncated/corrupt encode — keep the
            # good clip rather than os.replace-ing it away (the BUG 64 lesson).
            dur = _probe_duration(str(tmp))
            if dur is not None and dur >= float(clip_length) * 0.9:
                # Cross-drive safe swap: the work dir (tmp, e.g. C:) can be on a
                # different drive than the clips dir (clip_output, e.g. G:), and
                # os.replace() across drives fails on Windows (WinError 17 — this
                # silently killed EVERY cold-open teaser in the 2026-07-04 p4cal
                # run). Stage the copy onto the destination drive, then os.replace
                # there (same-drive = atomic; keeps the BUG 64 "never destroy the
                # good clip on a partial write" guarantee — clip_output is only
                # swapped once the full copy is on its own drive).
                _stage = clip_output.with_name(clip_output.name + ".coldopen.tmp")
                shutil.copyfile(str(tmp), str(_stage))
                os.replace(str(_stage), str(clip_output))
                tmp.unlink(missing_ok=True)
                ctx.log.log(f"  [cold-open] prepended teaser to T={T} ({dur:.1f}s)")
                try:  # effects manifest (logging only)
                    import sys as _s
                    from pathlib import Path as _P
                    _s.path.insert(0, str(_P(__file__).resolve().parents[2] / "lib"))
                    import effects_log as _efl
                    _efl.log_effect(clip_output.stem, "cold_open",
                                    {"payoff": float(T), "tease_start": max(0.0, float(T) - 1.5),
                                     "tease_dur": 1.2, "final_dur": dur},
                                    vod=str(getattr(ctx, "vod_path", "")))
                except Exception:
                    pass
            else:
                ctx.log.warn(f"cold-open output failed integrity check "
                             f"(dur={dur}, expected >= {clip_length}); keeping original T={T}")
                try:
                    tmp.unlink()
                except OSError:
                    pass
        elif tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    except Exception as e:
        ctx.log.warn(f"cold-open teaser failed for T={row.get('t')}: {e}")


def _snap_short_start(ctx, T, clip_start, speed: float, start_r: float) -> float:
    """Nudge the companion short's start back to a word boundary so it doesn't open
    mid-word. clip_<T>.srt is 0-based SOURCE time from clip_start; the short runs in
    RENDERED time (source/speed). Returns start_r unchanged on any issue (failure-soft)."""
    try:
        srt = ctx.paths.work(f"clip_{T}.srt")
        if not srt.exists():
            return start_r
        import re
        target_src = start_r * speed  # desired start in SRT's source-time base
        best = None
        for m in re.finditer(r"(\d\d):(\d\d):(\d\d),(\d\d\d)\s*-->", srt.read_text(
                encoding="utf-8", errors="replace")):
            h, mi, s, ms = map(int, m.groups())
            ws = h * 3600 + mi * 60 + s + ms / 1000.0
            # nearest word-start at/just-before the target, within 2.5 s back
            if ws <= target_src + 0.3 and (target_src - ws) <= 2.5:
                best = ws if best is None else max(best, ws)
        if best is not None:
            return max(0.0, best / speed)
    except Exception:
        pass
    return start_r


def _maybe_companion_short(ctx, row, clip_output: Path, clip_start, clip_length) -> None:
    """CLIP_COMPANION_SHORTS (default OFF): for a LONG clip with a confident payoff, also
    emit a punchline-only SHORT — a payoff-centered sub-cut of the FINISHED clip (so its
    captions / blur-fill / colors are inherited and stay aligned; no re-caption needed).
    Owner req 2026-07-09 (the 'Yo!' Freestyle: post the full clip AND a small ending clip
    for quick sharing). ADDITIVE: never touches the full clip. Runs BEFORE cold-open so the
    payoff offset is clean. Failure-soft. Skipped for storytime/emotional (a payoff-only cut
    loses the essential buildup) — but NOT for rap/freestyle (the owner's actual use case)."""
    if os.environ.get("CLIP_COMPANION_SHORTS", "").strip().lower() not in ("1", "true", "yes", "on"):
        return
    try:
        T = float(row["t"])
        cat = str(row.get("category", "")).lower()
        seg = str(row.get("segment_type", "")).lower()
        exempt = os.environ.get("CLIP_COMPANION_EXEMPT", "storytime,emotional").lower()
        if any(e and (e in cat or e in seg) for e in exempt.split(",")):
            return
        # Floor at 30 s: below this a payoff-only short isn't meaningfully shorter (the
        # owner's motivating 'Yo!' Freestyle clip is 36 s, so 45 was too high).
        min_full = float(os.environ.get("CLIP_COMPANION_MIN_FULL_S", "30") or "30")
        if float(clip_length) < min_full:
            return
        try:
            speed = float(ctx.clip_speed or "1.0") or 1.0
        except (TypeError, ValueError):
            speed = 1.0
        lead = float(os.environ.get("CLIP_COMPANION_LEAD_S", "5") or "5")
        tail = float(os.environ.get("CLIP_COMPANION_TAIL_S", "10") or "10")
        rendered_dur = _probe_duration(str(clip_output)) or (float(clip_length) / speed)
        payoff_r = (T - float(clip_start)) / speed            # payoff position in the rendered clip
        start_r = _snap_short_start(ctx, T, clip_start, speed, max(0.0, payoff_r - lead))
        end_r = min(rendered_dur, payoff_r + tail)
        short_len = end_r - start_r
        min_short = float(os.environ.get("CLIP_COMPANION_MIN_S", "6") or "6")
        if short_len < min_short or short_len >= rendered_dur * 0.75:
            return  # too short to matter, or not meaningfully shorter than the full clip
        short_out = ctx.paths.clips_dir / f"{row['title']} (Short).mp4"
        r = subprocess.run(
            ["ffmpeg", "-y", "-ss", f"{start_r:.3f}", "-i", str(clip_output),
             "-t", f"{short_len:.3f}", "-c:v", "h264_nvenc", "-preset", "p4",
             "-c:a", "aac", "-b:a", "128k", "-avoid_negative_ts", "make_zero", str(short_out)],
            capture_output=True, timeout=300)
        if r.returncode == 0 and short_out.exists() and short_out.stat().st_size > 1024:
            ctx.log.log(f"  [companion-short] {row['title']} -> +{short_len:.0f}s punchline short "
                        f"(payoff T={T:.0f}, window {start_r:.0f}-{end_r:.0f}s of clip)")
            _record_clip(ctx, {**row, "title": f"{row['title']} (Short)"}, short_out, round(short_len, 1))
        else:
            short_out.unlink(missing_ok=True)
            ctx.log.warn(f"companion-short render failed for T={T:.0f} "
                         f"({(r.stderr or b'')[-120:]!r})")
    except Exception as e:  # noqa: BLE001 — additive extra never breaks the run
        ctx.log.warn(f"companion-short skipped for T={row.get('t')}: {e}")


def _maybe_write_post_kit(ctx, row) -> None:
    """P2.3 — write the "<title>.post.json" sidecar (per-platform post copy,
    generated in Stage 6). One per primary clip; failure-soft. DEFAULT ON since
    2026-07-10 (kill switch CLIP_POST_KIT=0 upstream → row['post_kit'] empty →
    no-op here). Sidecars live in clips/post_kits/ (owner req 2026-07-10: keep
    the clips folder video-only)."""
    pk = row.get("post_kit")
    if not pk:
        return
    try:
        kit_dir = ctx.paths.clips_dir / "post_kits"
        kit_dir.mkdir(parents=True, exist_ok=True)
        out = kit_dir / f"{row['title']}.post.json"
        out.write_text(json.dumps(pk, indent=2, ensure_ascii=False), encoding="utf-8")
        ctx.log.log(f"  [post-kit] post_kits/{row['title']}.post.json")
    except Exception as e:  # noqa: BLE001
        ctx.log.warn(f"post-kit write skipped for T={row.get('t')}: {e}")


def _maybe_ab_variant(ctx, row, clip_start, clip_length, clip_srt_render, moment_json) -> None:
    """P2.2 — classic A/B: render variant B of an eligible clip. B is a FULL
    INDEPENDENT profile render with (a) the alternate-angle hook from Stage 6 and
    (b) a PERTURBED seed (CLIP_VARIANT_SEED_OFFSET, default 1) so its SFX + visual
    effects differ from A — the owner wants varied sound AND visuals per A/B side,
    which is why this can't reuse a shared master. Gated by CLIP_AB_VARIANTS>=2 +
    row.ab_eligible (top-N). Additive + failure-soft: a failed B never touches A.
    Needs profile mode (that's where the SFX/visual variety lives); logged-skip
    otherwise. DEFAULT ON since 2026-07-10 (owner promotion: 9/9-GOOD spot-check
    on run 20260710_202308); kill switch CLIP_AB_VARIANTS=0.
    See concepts/plan-captions-and-ab-variants-2026-07 §P2.2."""
    try:
        n = int(os.environ.get("CLIP_AB_VARIANTS", "2") or "2")
    except ValueError:
        n = 2
    if n < 2 or not row.get("ab_eligible"):
        return
    b = next((v for v in (row.get("hook_variants") or [])
              if str(v.get("label", "")).upper() == "B"), None)
    if not b or not (b.get("hook") or b.get("title")):
        return
    if not ctx.style_profiles:
        ctx.log.log(f"  [ab-variant] T={row['t']}: skipped — needs CLIP_STYLE_PROFILES "
                    f"for varied SFX/visual (a hook-only B isn't the owner's A/B)")
        return
    try:
        T = row["t"]
        seed_off = int(os.environ.get("CLIP_VARIANT_SEED_OFFSET", "1") or "1") or 1
        b_hook = b.get("hook") or row.get("hook", "")
        out_b = ctx.paths.clips_dir / f"{row['title']} (B).mp4"
        r = common.run_module(ctx.log, "profile_render.py", [
            "--moment-json", str(moment_json), "--src", str(ctx.vod_path),
            "--srt", str(clip_srt_render), "--out", str(out_b),
            "--clip-start", str(clip_start), "--clip-duration", str(clip_length),
            "--speed", ctx.clip_speed,
            "--captions", "true" if ctx.captions_enabled else "false",
            "--hook", "true" if ctx.hook_caption_enabled else "false",
            "--hook-text", b_hook, "--temp-dir", str(ctx.paths.work_dir),
            "--music-folder", ctx.music_bed,
            "--seed-offset", str(seed_off),
        ], env=ctx.child_env(), check=False)
        if r.returncode == 0 and out_b.exists():
            ctx.log.log(f"  [ab-variant] {row['title']} (B) [{b.get('angle', 'alt')}] "
                        f"hook=\"{b_hook}\" seed+{seed_off}")
            _record_clip(ctx, {**row, "title": f"{row['title']} (B)", "hook": b_hook},
                         out_b, clip_length, profile=True)
        else:
            out_b.unlink(missing_ok=True)
            ctx.log.warn(f"ab-variant B render failed for T={T}")
    except Exception as e:  # noqa: BLE001 — additive extra never breaks the run
        ctx.log.warn(f"ab-variant skipped for T={row.get('t')}: {e}")


def _wrap_hook(hook: str) -> str:
    import textwrap
    lines = textwrap.wrap(hook.strip(), 18)[:3]
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
        # D6: during the Stage-6 overlap, scored_moments.json doesn't exist yet —
        # the consumer PRE-STAGES moment_<T>.json from the enrichment sidecar.
        # Honor a pre-staged file so early renders keep the profile-mode path
        # instead of silently degrading to the legacy renderer.
        if out.exists():
            return True
        return False


# --- Video-encode profiles (Stage 7 NVENC, 2026-06-06) ---------------------
# The model is unloaded before rendering (run() below), so the full GPU is free
# for hardware encode. h264_nvenc on the dedicated NVENC ASIC is several times
# faster than libx264 -preset slow AND offloads the CPU so the parallel filter
# work (blur-fill, captions) runs faster too. libx264 stays as the per-clip
# fallback. Choose with STAGE7_ENCODER=auto|nvenc|libx264 (auto probes NVENC and
# uses it only when it actually encodes on this machine). NVENC `-rc vbr -cq 20`
# + the 18M cap targets ~the libx264 crf-20 quality. See concepts/clip-rendering.
_VENC_LIBX264 = ["-c:v", "libx264", "-crf", "20", "-preset", "slow", "-profile:v", "high",
                 "-level", "4.2", "-pix_fmt", "yuv420p", "-r", "30",
                 "-b:v", "18M", "-maxrate", "20M", "-bufsize", "40M",
                 "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"]

_VENC_NVENC = ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "20",
               "-profile:v", "high", "-pix_fmt", "yuv420p", "-r", "30",
               "-b:v", "18M", "-maxrate", "20M", "-bufsize", "40M",
               "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"]

# Active primary encoder for this run; set in run() after the model unload.
# Defaults to libx264 so any render before run() resolves it still works.
_ACTIVE_VENC = _VENC_LIBX264


def _nvenc_works() -> bool:
    """True iff h264_nvenc can actually encode on this machine right now (build
    has it AND the driver/GPU accept a session). One-shot 0.1 s null-muxed test
    encode — definitive, vs just grepping `-encoders` which only proves the
    build has it."""
    try:
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
             "-i", "color=c=black:s=256x256:r=30:d=0.1", "-c:v", "h264_nvenc",
             "-f", "null", "-"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30,
        )
        return r.returncode == 0
    except Exception:
        return False


def _resolve_encoder(log):
    """Pick the Stage 7 primary video encoder. STAGE7_ENCODER=auto|nvenc|libx264
    (default auto). auto uses NVENC only when the probe confirms it encodes.
    A per-clip NVENC failure still falls back to libx264 (_encode_with_fallback),
    so this only chooses the primary."""
    choice = os.environ.get("STAGE7_ENCODER", "auto").strip().lower()
    if choice == "libx264":
        log.log("  [encode] STAGE7_ENCODER=libx264 — CPU encode (preset slow)")
        return _VENC_LIBX264
    if choice == "nvenc":
        log.log("  [encode] STAGE7_ENCODER=nvenc — h264_nvenc (GPU); libx264 per-clip fallback")
        return _VENC_NVENC
    if _nvenc_works():
        log.log("  [encode] NVENC probe OK — h264_nvenc (GPU); libx264 per-clip fallback")
        return _VENC_NVENC
    log.log("  [encode] NVENC unavailable — libx264 (CPU, preset slow)")
    return _VENC_LIBX264


def _encode_with_fallback(ctx, base_cmd, out) -> bool:
    """Append the active encoder + output to base_cmd and run. If NVENC is
    active and the render fails (session limit / driver / odd input), retry the
    SAME render once with libx264 so a flaky NVENC session never drops a clip."""
    if common.run_ffmpeg(base_cmd + _ACTIVE_VENC + [str(out)]) == 0:
        return True
    if _ACTIVE_VENC is _VENC_NVENC:
        ctx.log.warn(f"NVENC render failed for {Path(out).name}; retrying with libx264")
        return common.run_ffmpeg(base_cmd + _VENC_LIBX264 + [str(out)]) == 0
    return False


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
        base_cmd = ["ffmpeg", "-nostdin", "-y", "-ss", str(start), "-t", str(length), *mix_args,
                    "-filter_complex", filter_complex, "-map", "[vout]", "-map", "[aout]"]
    else:
        af = ["-af", speed_audio_filter] if speed_audio_filter else []
        base_cmd = ["ffmpeg", "-nostdin", "-y", "-ss", str(start), "-t", str(length),
                    "-i", str(ctx.vod_path), "-vf", render_vf, *af]
    return _encode_with_fallback(ctx, base_cmd, out)


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
    # Durable per-clip score index (clips/.diagnostics/clip_scores.jsonl),
    # consumed by the poster's Top-rated filter (poster/scores.py). Written
    # HERE, the moment the file exists, because clips_made.txt is work-dir
    # ephemeral and last_run traces only survive a batch that ENDS cleanly —
    # a stopped batch used to leave every clip unscored (2026-07-16, 2/134).
    # One line per rendered file; the variant marker stays in `clip`.
    try:
        _score = None
        try:
            _score = float(row["score"])
        except (TypeError, ValueError, KeyError):
            pass
        diag = ctx.paths.diagnostics_dir
        diag.mkdir(parents=True, exist_ok=True)
        with open(diag / "clip_scores.jsonl", "a", encoding="utf-8") as jf:
            jf.write(json.dumps({
                "clip": out.stem,
                "score": _score,
                "judge": row.get("judge"),
                "category": row.get("category"),
                "run": os.environ.get("CLIP_RUN_STAMP", ""),
                "ts": int(time.time()),
            }) + "\n")
    except Exception:
        pass  # the index is a convenience — never fail a render over it


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

    # Pick the video encoder now that the model is unloaded (GPU is free for
    # NVENC). Per-clip libx264 fallback keeps it reliable. STAGE7_ENCODER env.
    global _ACTIVE_VENC
    _ACTIVE_VENC = _resolve_encoder(log)

    # 7a — manifest
    log.log("  Generating clip manifest...")
    rows = _generate_manifest(ctx)
    log.log(f"  Manifest: {len(rows)} clips to process")

    # 7b/7c — per-clip caption timing.
    #
    # A2' (plan-speed-wave3, 2026-07-14): the DEFAULT is now slicing each clip's
    # word-SRT out of the Stage-2 master transcript (wav2vec2-aligned since
    # Wave 0) via a clip_windows.json manifest — deterministic, keeps captions
    # identical to the text every detection stage read, and removes Stage 7's
    # whole Whisper load/claim. The legacy per-clip audio rip + batch Whisper
    # path remains as the fallback (CLIP_CAPTION_SOURCE=whisper forces it, and
    # any missing slice output triggers it automatically). Works on every
    # hardware profile; CPU-only installs save the most.
    def _row_window(row):
        start = max(0, int(float(row["clip_start"]))) if row["clip_start"] != "" else max(0, int(row["t"]) - 22)
        length = int(float(row["clip_duration"])) if row["clip_duration"] != "" else 45
        return start, length

    windows = {}
    for row in rows:
        _ws, _wl = _row_window(row)
        windows[str(row["t"])] = {"start": _ws, "duration": _wl}
    try:
        p.work("clip_windows.json").write_text(json.dumps(windows), encoding="utf-8")
    except OSError as _we:
        log.warn(f"  could not write clip_windows.json ({_we}) — Whisper fallback will handle captions")

    cap_env = dict(env)
    cap_env["CLIP_WHISPER_MODEL"] = ctx.whisper_model
    _cap_source = os.environ.get("CLIP_CAPTION_SOURCE", "master").strip().lower()
    _master_ready = _cap_source == "master" and p.transcript_json.exists()

    audio_workers = _resolve_render_workers()  # share the env knob

    def _extract_clip_audio(row):
        start, length = _row_window(row)
        common.run_ffmpeg(["ffmpeg", "-nostdin", "-y", "-ss", str(start), "-t", str(length),
                           "-i", str(ctx.vod_path), "-vn", "-acodec", "pcm_s16le", "-ar", "16000",
                           "-ac", "1", str(p.work(f"clip_audio_{row['t']}.wav"))])

    def _legacy_caption_pass():
        # 7b — extract clip audio (parallel, 2026-06-04). One ffmpeg per clip,
        # each is fast (seek + 16k mono PCM rip ≈ 0.5-1 s) but invocation
        # overhead adds up across 10 clips. Same ThreadPool pattern as 7d.
        log.log("  Extracting audio for all clips...")
        if audio_workers <= 1 or len(rows) <= 1:
            for row in rows:
                _extract_clip_audio(row)
        else:
            with ThreadPoolExecutor(max_workers=audio_workers) as pool:
                for fut in as_completed({pool.submit(_extract_clip_audio, row): row for row in rows}):
                    fut.result()
        # 7c — batch caption transcription (single Whisper load)
        log.log("  Batch transcribing all clips (single Whisper load)...")
        _wenv = dict(cap_env)
        _wenv["CLIP_CAPTION_SOURCE"] = "whisper"
        common.run_module(log, "stages/stage7_transcribe.py", [], env=_wenv, check=False)

    if _master_ready:
        log.log("  Caption timing sliced from the master transcript (A2' — no per-clip Whisper)...")
        common.run_module(log, "stages/stage7_transcribe.py", [], env=cap_env, check=False)
        # An empty SRT is a legit silent clip; only a MISSING file means the
        # slicer didn't cover that window — run the legacy pass for everything.
        _missing = [row["t"] for row in rows if not p.work(f"clip_{row['t']}.srt").exists()]
        if _missing:
            log.warn(f"  master-slice missed {len(_missing)} clip SRT(s) ({_missing[:4]}...) — "
                     "running the legacy Whisper pass.")
            _legacy_caption_pass()
    else:
        if _cap_source == "master":
            log.log("  Master transcript unavailable — using the legacy Whisper caption pass.")
        _legacy_caption_pass()

    # 7d — render. Parallelized 2026-06-04: each clip's render is an
    # independent ffmpeg invocation (blur-fill + subtitle burn + audio mix).
    # On a 24-core i9-13900K, 4 concurrent ffmpegs each using ~6 threads
    # saturate CPU without oversubscription. ThreadPool (not Process) because
    # ``ctx`` and ``log`` aren't pickle-friendly; subprocess work releases
    # the GIL so threads parallelise fine. Tune via ``STAGE7_WORKERS``;
    # set to 1 to force the original serial path.
    log.log(f"  Rendering all clips (framing={ctx.framing}, originality={ctx.originality})...")
    speed_vf = f"setpts=PTS/{ctx.clip_speed}" if ctx.clip_speed != "1.0" else "null"
    speed_audio_filter = (f"rubberband=tempo={ctx.clip_speed}:pitch={ctx.clip_speed}"
                          if ctx.clip_speed != "1.0" else "")

    # D6 (plan-speed-wave3): clips the stage-6 overlap consumer already rendered
    # (while enrichment was still running) are skipped here — everything else
    # (manifest, captions, stitch groups, summary) still covers the full set.
    _early = getattr(ctx, "early_rendered", None) or set()
    if _early:
        _skipped = [r["t"] for r in rows if r["t"] in _early]
        rows_to_render = [r for r in rows if r["t"] not in _early]
        log.log(f"  [D6] {len(_skipped)} clip(s) already rendered during Stage 6 "
                f"(T={_skipped[:6]}{'...' if len(_skipped) > 6 else ''}) — rendering "
                f"the remaining {len(rows_to_render)}.")
    else:
        rows_to_render = rows

    render_workers = _resolve_render_workers()
    if render_workers <= 1 or len(rows_to_render) <= 1:
        for row in rows_to_render:
            try:
                _render_clip(ctx, row, speed_vf, speed_audio_filter)
            except Exception as e:  # noqa: BLE001
                log.warn(f"render error for T={row['t']}: {e}")
    else:
        log.log(f"  [parallel] dispatching {len(rows_to_render)} renders across "
                f"{render_workers} workers...")
        with ThreadPoolExecutor(max_workers=render_workers) as pool:
            futs = {
                pool.submit(_render_clip, ctx, row, speed_vf, speed_audio_filter): row
                for row in rows_to_render
            }
            for fut in as_completed(futs):
                row = futs[fut]
                try:
                    fut.result()
                except Exception as e:  # noqa: BLE001
                    log.warn(f"render error for T={row['t']}: {e}")

    # 7d.5 — transition animations (jump-cuts + white flashes), gated + failure-
    # soft. Runs on the FINISHED clips so burned captions/effects stay in sync
    # (they're pixels by now — no SRT remap needed). CLIP_JUMP_CUTS=off|gaps|llm|on,
    # CLIP_FLASH_CUTS=off|on. See scripts/lib/clip_cuts.py + concepts/transition-animations.
    _jump_mode = os.environ.get("CLIP_JUMP_CUTS", "off").strip().lower()
    _flash_mode = os.environ.get("CLIP_FLASH_CUTS", "off").strip().lower()
    if _jump_mode not in ("", "off") or _flash_mode not in ("", "off"):
        try:
            import clip_cuts
            import edit_plan as _ep
            _moments_by_t = {
                int(round(float(_m.get("timestamp", -1)))): _m
                for _m in json.loads(p.scored_moments.read_text(encoding="utf-8"))
            }
            # v2 J1: the SFX cue times already placed on each clip (clip-relative),
            # so a cut keeps its joins clear of them and the effects-log ground truth
            # can be remapped onto the compressed timeline. Read this run's render_plan
            # rows once; failure-soft (no map -> no effect-aware protection, still safe).
            _run_stamp = os.environ.get("CLIP_RUN_STAMP", "")
            _cues_by_title: dict[str, list[float]] = {}
            try:
                import effects_log as _efl
                for _r in _efl.read_effects(last=1000000):
                    if _r.get("run") != _run_stamp or _r.get("type") != "render_plan":
                        continue
                    _ts: list[float] = []
                    for _c in (_r.get("data") or {}).get("sfx_cues") or []:
                        _v = _c.get("t", _c.get("at")) if isinstance(_c, dict) else None
                        try:
                            if _v is not None:
                                _ts.append(float(_v))
                        except (TypeError, ValueError):
                            pass
                    if _ts:
                        _cues_by_title[str(_r.get("clip"))] = _ts
            except Exception:
                _cues_by_title = {}
            _n_mod = 0
            for row in rows:
                clip_file = p.clips_dir / f"{row['title']}.mp4"
                if not clip_file.exists():
                    continue
                try:
                    cs = (float(row["clip_start"]) if row["clip_start"] != ""
                          else max(0.0, float(row["t"]) - 15))
                    dur = float(row["clip_duration"]) if row["clip_duration"] != "" else 30.0
                except (TypeError, ValueError):
                    continue
                _plan = _ep.normalize(
                    _moments_by_t.get(int(round(float(row["t"]))), {}).get("edit_plan") or {})
                # v2 J5: word-level items for the (opt-in) filler micro-lane. The
                # per-clip word SRT (stage7_transcribe) is clip-relative → offset by cs.
                _words = []
                if os.environ.get("CLIP_CUT_FILLERS", "0").strip().lower() in ("1", "true", "on", "yes"):
                    try:
                        import cut_inference as _ci
                        _words = _ci.load_word_srt(str(p.work(f"clip_{row['t']}.srt")), offset=cs)
                    except Exception:
                        _words = []
                if clip_cuts.process_clip_transitions(
                        str(clip_file), cuts=_plan.get("cuts", []),
                        flashes=_plan.get("flashes", []), clip_start=cs, duration=dur,
                        temp_dir=str(p.work_dir), jump_mode=_jump_mode,
                        flash_mode=_flash_mode, seed=int(round(float(row["t"]))),
                        category=row["category"], payoff_abs=float(row["t"]),
                        effect_cues=_cues_by_title.get(row["title"], []),
                        run_stamp=_run_stamp, clip_title=row["title"],
                        word_items=_words, log=log.log):
                    _n_mod += 1
            if _n_mod:
                log.log(f"  [transitions] applied to {_n_mod}/{len(rows)} clip(s) "
                        f"(jump={_jump_mode} flash={_flash_mode})")
        except Exception as e:  # noqa: BLE001
            log.warn(f"transitions pass failed (clips unaffected): {e}")

    # 7d.6 — A/B CUTS EXPERIMENT (owner measurement lane, opt-in, default off).
    # Independent of CLIP_JUMP_CUTS: compress ONLY the (B) variant with silence-gap
    # cuts, leaving A uncompressed, so A-vs-B is a labelable pair — the owner's
    # GOOD/BAD labels then measure whether compression actually helps BEFORE any
    # default flip. Gaps-only (no LLM) for a clean, safe comparison. See
    # concepts/plan-jump-cuts-v2-2026-07 J6.
    if os.environ.get("CLIP_AB_CUTS_EXPERIMENT", "0").strip().lower() in ("1", "true", "on", "yes"):
        try:
            import clip_cuts
            _run_stamp = os.environ.get("CLIP_RUN_STAMP", "")
            _nb = 0
            for row in rows:
                b_file = p.clips_dir / f"{row['title']} (B).mp4"
                if not b_file.exists():
                    continue
                try:
                    cs = (float(row["clip_start"]) if row["clip_start"] != ""
                          else max(0.0, float(row["t"]) - 15))
                    dur = float(row["clip_duration"]) if row["clip_duration"] != "" else 30.0
                except (TypeError, ValueError):
                    continue
                if clip_cuts.process_clip_transitions(
                        str(b_file), cuts=[], flashes=[], clip_start=cs, duration=dur,
                        temp_dir=str(p.work_dir), jump_mode="gaps", flash_mode="off",
                        seed=int(round(float(row["t"]))), category=row["category"],
                        payoff_abs=float(row["t"]), run_stamp=_run_stamp,
                        clip_title=f"{row['title']} (B)", log=log.log):
                    _nb += 1
            if _nb:
                log.log(f"  [ab-cuts-experiment] compressed {_nb} (B) variant(s) — "
                        f"A (uncut) vs B (jump-cut) is now a labelable pair")
        except Exception as e:  # noqa: BLE001
            log.warn(f"A/B cuts experiment failed (clips unaffected): {e}")

    # 7e — stitch groups (regular stitch + Fix 3 arc-stitch share this renderer)
    groups_file = p.work("moment_groups.json")
    if (ctx.stitch or ctx.arc_stitch) and groups_file.exists():
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
