#!/usr/bin/env python3
"""Profile-mode clip renderer.

Stage 7 dispatches each moment here when CLIP_STYLE_PROFILES=true. We
resolve the per-category profile (style_profiles.py), normalize the
edit-plan JSON (edit_plan.py), build a single FFmpeg filter graph that
layers in zoom punches / freeze frames / slow-mo / meme cutaways /
B-roll inserts / SFX cues / kinetic captions / chat overlay, apply the
always-on audio + container fingerprint perturbation, and run FFmpeg.

Returns 0 on success — Stage 7 marks the clip as rendered and skips its
inline render path. Returns non-zero on any failure (bad input, missing
asset, FFmpeg error) — Stage 7 falls back to its legacy render path so
the user still gets a clip.

CLI:
    python profile_render.py
        --moment-json <path>     # one-moment JSON file
        --src <vod path>
        --srt <clip srt>
        --out <output mp4>
        --clip-start <s>
        --clip-duration <s>
        --speed <x>              # 1.0 default
        --captions {on,off}
        --hook {on,off}
        --hook-text <str>
        [--temp-dir /tmp/clipper]
        [--lib-dir /root/scripts/lib]
        [--music-folder <path>]
        [--clips-dir <path>]

The renderer is intentionally tolerant: when a referenced asset (meme
file, B-roll clip, SFX kind, music folder) is missing, the corresponding
layer is dropped silently instead of failing the whole render.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

# Ensure sibling helper modules import even when called from another cwd.
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import venc  # shared NVENC/libx264 selection (after path insert above)

import chat_overlay as co               # type: ignore
import edit_plan as ep                  # type: ignore
import freeze_frame as ff               # type: ignore
import kinetic_captions as kc           # type: ignore
import meme_pick as mp                  # type: ignore
import broll_pick as bp                 # type: ignore
import sfx_inject as sx                 # type: ignore
import slow_mo as sm                    # type: ignore
import style_profiles as sp             # type: ignore
import zoom_punch as zp                 # type: ignore


def _log(msg: str) -> None:
    print(f"[profile_render] {msg}", file=sys.stderr, flush=True)


def _ensure_chain_label(prev_label: str, fragment: str, out_label: str) -> tuple[str, str]:
    """If `fragment` is empty, the chain stays on prev_label."""
    if not fragment:
        return "", prev_label
    return fragment, out_label


def _build_video_chain(profile: dict, plan: dict, *, source_fps: float,
                       speed: float, out_w: int = 1080, out_h: int = 1920) -> tuple[str, str, list[str]]:
    """Return (filter_complex_fragment, final_video_label, downgrade_zoom_punches).

    The fragment chains: speed → blur_fill → eq/sat/contrast/hue →
    vignette/shake → zoom_punches → freeze → slow_mo → output_label.

    `downgrade_zoom_punches` are extra zoom punches injected when slow_mo
    was downgraded (low source FPS).
    """
    parts: list[str] = []
    cur = "v0"

    # Speed
    speed_vf = f"setpts=PTS/{speed:.4f}" if abs(speed - 1.0) > 1e-3 else "null"

    # Blur-fill base (mirrors stage7_render.sh's BLUR_FILL_VF, parameterized).
    sat = max(0.5, min(2.0, 1.0 + float(profile.get("saturation_boost", 0.0))))
    con = max(0.5, min(2.0, 1.0 + float(profile.get("contrast_boost", 0.0))))
    eq = f"eq=saturation={sat:.3f}:contrast={con:.3f}"

    mirror = "hflip" if profile.get("mirror_prob") else "null"
    base = (
        f"[0:v]{speed_vf},split[bg][fg];"
        f"[bg]scale={out_w}:{out_h}:force_original_aspect_ratio=increase,"
        f"crop={out_w}:{out_h},boxblur=24:5[blurred];"
        f"[fg]scale={out_w}:-2:force_original_aspect_ratio=decrease,"
        f"{mirror}[sharp];"
        f"[blurred][sharp]overlay=(W-w)/2:(H-h)/2,{eq}[v0]"
    )
    parts.append(base)

    # Vignette + shake (cheap, conditional)
    if profile.get("vignette_prob"):
        parts.append(f"[{cur}]vignette=angle=PI/5[v_vig]")
        cur = "v_vig"
    if profile.get("shake_prob"):
        parts.append(
            f"[{cur}]crop=iw-4:ih-4:2+2*sin(t*2):2+2*cos(t*1.5)[v_shake]"
        )
        cur = "v_shake"

    # Slow-mo (decide first; on downgrade we get extra zoom punches)
    extra_zps: list[dict] = []
    sm_plan = sm.plan_slow_mo(cur, "v_slow", **{
        "start": (plan["slow_mo"] or {}).get("start", 0.0),
        "end":   (plan["slow_mo"] or {}).get("end", 0.0),
        "rate":  (plan["slow_mo"] or {}).get("rate", 0.5),
    }, source_fps=source_fps) if plan.get("slow_mo") else {"mode": "noop"}
    if sm_plan["mode"] == "slow_mo":
        parts.append(sm_plan["fragment"])
        cur = sm_plan["out_label"]
    elif sm_plan["mode"] == "downgrade":
        zp_dg = sm_plan["zoom_punch"]
        extra_zps.append(zp_dg)
        _log(f"slow-mo downgraded: {sm_plan['reason']}")

    # Zoom punches (combine plan + slow-mo downgrades)
    all_zps = list(plan.get("zoom_punches") or []) + extra_zps
    frag, cur = _ensure_chain_label(cur, *zp.build_zoom_fragment(
        cur, "v_zoom", all_zps, out_w=out_w, out_h=out_h
    )[::-1] if False else zp.build_zoom_fragment(
        cur, "v_zoom", all_zps, out_w=out_w, out_h=out_h
    ))
    # The double-call above is awkward; do it cleanly:
    # (rewriting:)
    # We'll just call once and use the fragment + label directly.
    # Reset and re-do cleanly below.
    return "; ".join(parts), cur, all_zps


def _ffprobe_fps(src: str) -> float:
    return sm.probe_source_fps(src)


def _resolve_meme_overlay(plan: dict, profile_category: str,
                          seed: object) -> dict | None:
    meme = plan.get("meme_cutaway")
    if not meme:
        return None
    p = mp.pick(profile_category, meme["tag"], seed=seed)
    if p is None:
        return None
    return {"path": str(p), "t": meme["t"], "duration": meme["duration"]}


def _resolve_broll_overlays(plan: dict, profile_category: str,
                            seed: object) -> list[dict]:
    out: list[dict] = []
    pref_sub = "travel" if "travel" in profile_category else None
    for ins in plan.get("broll_inserts") or []:
        p = bp.pick(ins["noun"], seed=(seed, ins["t"]),
                    preferred_subfolder=pref_sub)
        if p is None:
            continue
        out.append({"path": str(p), "t": ins["t"],
                    "duration": ins["duration"], "noun": ins["noun"]})
    return out


def _build_audio_chain(plan: dict, profile: dict, *, src_idx: int,
                       speed: float, sfx_inputs_start: int,
                       music_input_idx: int | None,
                       fingerprint: dict,
                       clip_duration: float) -> tuple[str, list[str], list[str]]:
    """Return (filter_complex_fragment, sfx_input_paths, mix_labels).

    Builds:
      - source audio with speed (rubberband) + pitch jitter (rubberband cents)
      - SFX layers (one per cue) delayed via adelay
      - music input (if present, looped) at -22 dB
      - amix all together with normalize=0 + final volume cushion
      - sidechain duck on music when speech is detected (omitted for now,
        falls under "good-enough" — we just mix at ducked levels)
    """
    parts: list[str] = []

    pitch = float(fingerprint.get("pitch_cents", 0.0))
    # rubberband pitch is in semitones; convert cents→ratio: 2^(c/1200).
    pitch_ratio = 2.0 ** (pitch / 1200.0)
    if abs(speed - 1.0) > 1e-3:
        # Speed-aware audio + pitch jitter in one rubberband call.
        # tempo follows speed; pitch is the jitter ratio multiplied with itself.
        src_filter = f"rubberband=tempo={speed:.4f}:pitch={pitch_ratio * speed:.5f},volume=1.0"
    else:
        src_filter = f"rubberband=pitch={pitch_ratio:.5f},volume=1.0"
    parts.append(f"[{src_idx}:a]{src_filter}[src_audio]")

    # Track stream count separately from the list of label-segments — sfx_layer's
    # mix_inputs is "[sfx0][sfx1]..." (multiple streams concatenated) and would
    # under-count if we just took len(mix_inputs).
    mix_input_segments = ["[src_audio]"]
    n_mix = 1

    sfx_layer = plan.get("_sfx_layer", {"inputs": [], "filter_defs": "",
                                        "mix_inputs": "", "n_inputs": 0})
    if sfx_layer["filter_defs"]:
        parts.append(sfx_layer["filter_defs"])
        mix_input_segments.append(sfx_layer["mix_inputs"])
        n_mix += int(sfx_layer.get("n_inputs", 0))
    sfx_inputs = sfx_layer["inputs"]

    if music_input_idx is not None:
        parts.append(
            f"[{music_input_idx}:a]atrim=0:{clip_duration:.3f},"
            f"volume=0.10[music_audio]"
        )
        mix_input_segments.append("[music_audio]")
        n_mix += 1

    if n_mix == 1:
        # Only the source — skip amix and rename src_audio → aout.
        parts.append(f"[src_audio]volume=0.95[aout]")
    else:
        parts.append(
            f"{''.join(mix_input_segments)}amix=inputs={n_mix}:duration=first:"
            f"dropout_transition=0:normalize=0[a_mixed]"
        )
        parts.append(f"[a_mixed]volume=0.95[aout]")
    return ";".join(parts), sfx_inputs, mix_input_segments


def render(*,
           moment: dict, src: str, srt: str, out: str,
           clip_start: float, clip_duration: float, speed: float,
           captions: bool, hook: bool, hook_text: str,
           temp_dir: str, lib_dir: str,
           music_folder: str | None, clips_dir: str | None) -> int:

    plan = ep.normalize(moment.get("edit_plan") or {})
    category = plan.get("profile") or moment.get("category") or "reactive"
    profile_seed = int(round(float(moment.get("timestamp", 0))))
    profile = sp.get_profile(category, seed=profile_seed)
    fingerprint = sp.fingerprint_params(profile_seed)
    cat_canon = profile["_category"]

    # Honor profile defaults for fields the LLM didn't populate.
    if not plan["caption_preset"]:
        plan["caption_preset"] = profile.get("caption_preset", "clean")

    # When Stage 6 didn't produce an explicit edit_plan, synthesize one
    # from the resolved profile so clips still get meaningful zoom punches
    # and SFX cues. Fields populated by vision are preserved.
    plan = _synthesize_plan(plan, profile, profile_seed, clip_duration)

    # Probe source FPS once per clip
    source_fps = _ffprobe_fps(src) or 30.0
    _log(f"category={cat_canon} fps={source_fps:.1f} preset={plan['caption_preset']} "
         f"zps={len(plan['zoom_punches'])} freeze={'y' if plan['freeze_at'] else 'n'} "
         f"slowmo={'y' if plan['slow_mo'] else 'n'} meme={'y' if plan['meme_cutaway'] else 'n'} "
         f"broll={len(plan['broll_inserts'])} sfx={len(plan['sfx_cues'])}")

    # Build SFX layer ahead of time so we know how many extra inputs to add.
    # base inputs: 0=src VOD; we'll allocate sfx after that.
    sfx_layer = sx.build_sfx_layer(
        cues=plan["sfx_cues"],
        seed=profile_seed,
        base_input_index=1,  # provisional — fixed up below after music decision
        sfx_volume=0.7,
    )

    # Music pick (graceful when folder missing or empty)
    music_path: str | None = None
    if music_folder and Path(music_folder).is_dir():
        try:
            cmd = [
                sys.executable, str(Path(lib_dir) / "music_pick.py"),
                "--library", music_folder,
                "--category", profile.get("music_category", "hype"),
                "--duration", str(clip_duration),
                "--seed", str(profile_seed),
                "--tier-c", "false",
            ]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if r.returncode == 0 and r.stdout.strip() and not r.stdout.startswith("#"):
                cand = Path(r.stdout.strip())
                if cand.is_file():
                    music_path = str(cand)
        except Exception as e:
            _log(f"music_pick failed: {e}")

    # ─── Build the FFmpeg invocation ───────────────────────────────────────
    inputs: list[str] = ["-ss", str(clip_start), "-t", str(clip_duration), "-i", src]

    # Append SFX inputs after src — recompute their input indices now.
    sfx_input_index = 1
    for p in sfx_layer["inputs"]:
        inputs += ["-itsoffset", "0", "-i", p]
    if sfx_layer["inputs"]:
        # Rebuild the layer with the correct base index.
        sfx_layer = sx.build_sfx_layer(
            cues=plan["sfx_cues"],
            seed=profile_seed,
            base_input_index=sfx_input_index,
            sfx_volume=0.7,
        )

    # Music input
    music_input_idx: int | None = None
    if music_path:
        music_input_idx = 1 + len(sfx_layer["inputs"])
        inputs += ["-stream_loop", "-1", "-i", music_path]

    # Meme + B-roll overlays
    meme_overlay = _resolve_meme_overlay(plan, cat_canon, seed=profile_seed)
    broll_overlays = _resolve_broll_overlays(plan, cat_canon, seed=profile_seed)

    overlay_inputs_start = 1 + len(sfx_layer["inputs"]) + (1 if music_input_idx is not None else 0)
    overlay_idx = overlay_inputs_start
    overlay_specs: list[dict] = []
    if meme_overlay:
        inputs += ["-loop", "1", "-i", meme_overlay["path"]]
        overlay_specs.append({**meme_overlay, "kind": "meme", "input_idx": overlay_idx})
        overlay_idx += 1
    for bo in broll_overlays:
        inputs += ["-itsoffset", "0", "-i", bo["path"]]
        overlay_specs.append({**bo, "kind": "broll", "input_idx": overlay_idx})
        overlay_idx += 1

    # Chat overlay (controversy/hot_take profiles). Renders a static PNG of
    # recent chat messages around the clip window and pins it to the right
    # column. Silently skipped when the VOD has no associated chat dump or
    # when Pillow isn't available — see chat_overlay.py for source detection.
    chat_overlay_active = bool(profile.get("chat_overlay")) or bool(plan.get("chat_overlay"))
    if chat_overlay_active:
        try:
            chat_png = co.build_overlay_for_clip(
                vod_path=src,
                temp_dir=os.environ.get("TEMP_DIR") or "/tmp/clipper",
                t_start=float(moment.get("clip_start", 0)) or 0.0,
                t_end=(float(moment.get("clip_start", 0)) or 0.0) + clip_duration,
                out_dir=Path(os.environ.get("TEMP_DIR") or "/tmp/clipper"),
                clip_id=str(int(round(float(moment.get("timestamp", 0))))),
            )
        except Exception as e:
            _log(f"chat overlay build failed: {e}")
            chat_png = None
        if chat_png and Path(chat_png).is_file():
            inputs += ["-loop", "1", "-i", str(chat_png)]
            overlay_specs.append({
                "kind":     "chat",
                "path":     str(chat_png),
                "t":        0.0,
                "duration": clip_duration,
                "input_idx": overlay_idx,
            })
            overlay_idx += 1
        else:
            _log("chat overlay: no source data, skipped")

    # ─── Video filter chain ───────────────────────────────────────────────
    OUT_W, OUT_H = 1080, 1920
    speed_vf = f"setpts=PTS/{speed:.4f}" if abs(speed - 1.0) > 1e-3 else "null"
    sat = max(0.5, min(2.0, 1.0 + float(profile.get("saturation_boost", 0.0))))
    con = max(0.5, min(2.0, 1.0 + float(profile.get("contrast_boost", 0.0))))
    mirror_op = "hflip" if profile.get("mirror_prob") else "null"

    chain_parts: list[str] = [
        f"[0:v]{speed_vf},split[bg][fg];"
        f"[bg]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,"
        f"crop={OUT_W}:{OUT_H},boxblur=24:5[blurred];"
        f"[fg]scale={OUT_W}:-2:force_original_aspect_ratio=decrease,"
        f"{mirror_op}[sharp];"
        f"[blurred][sharp]overlay=(W-w)/2:(H-h)/2,"
        f"eq=saturation={sat:.3f}:contrast={con:.3f}[v_base]"
    ]
    cur = "v_base"

    if profile.get("vignette_prob"):
        chain_parts.append(f"[{cur}]vignette=angle=PI/5[v_vig]")
        cur = "v_vig"
    if profile.get("shake_prob"):
        chain_parts.append(
            f"[{cur}]crop=iw-4:ih-4:2+2*sin(t*2):2+2*cos(t*1.5)[v_shake]"
        )
        cur = "v_shake"

    # Zoom punches (with slow-mo downgrade additions)
    extra_zps: list[dict] = []
    if plan["slow_mo"]:
        sm_plan = sm.plan_slow_mo(
            cur, "v_slow",
            start=plan["slow_mo"]["start"],
            end=plan["slow_mo"]["end"],
            rate=plan["slow_mo"]["rate"],
            source_fps=source_fps,
        )
        if sm_plan["mode"] == "slow_mo":
            chain_parts.append(sm_plan["fragment"])
            cur = sm_plan["out_label"]
        elif sm_plan["mode"] == "downgrade":
            extra_zps.append(sm_plan["zoom_punch"])
            _log(sm_plan["reason"])

    all_zps = list(plan["zoom_punches"]) + extra_zps
    frag, new_cur = zp.build_zoom_fragment(cur, "v_zoom", all_zps, out_w=OUT_W, out_h=OUT_H)
    if frag:
        chain_parts.append(frag)
        cur = new_cur

    # Freeze frame (max one supported in single-pass graph)
    if plan["freeze_at"]:
        ff_frag, ff_cur = ff.build_freeze_fragment(
            cur, "v_freeze",
            t=plan["freeze_at"]["t"],
            duration=plan["freeze_at"]["duration"],
            fps=int(round(source_fps)) or 30,
        )
        if ff_frag:
            chain_parts.append(ff_frag)
            cur = ff_cur

    # Meme + B-roll overlays
    for spec in overlay_specs:
        in_idx = spec["input_idx"]
        t0 = max(0.0, float(spec["t"]))
        t1 = t0 + max(0.4, float(spec["duration"]))
        # Position: meme top-right, B-roll lower third right, chat right column.
        if spec["kind"] == "meme":
            chain_parts.append(
                f"[{in_idx}:v]scale=300:-1,format=rgba,"
                f"setpts=PTS-STARTPTS+{t0}/TB[ov_{in_idx}];"
                f"[{cur}][ov_{in_idx}]overlay=W-w-32:80:enable='between(t,{t0:.3f},{t1:.3f})'"
                f"[v_ov_{in_idx}]"
            )
        elif spec["kind"] == "chat":
            chain_parts.append(
                f"[{in_idx}:v]scale=320:-1,format=rgba[ov_{in_idx}];"
                f"[{cur}][ov_{in_idx}]overlay=W-w-24:200[v_ov_{in_idx}]"
            )
        else:
            chain_parts.append(
                f"[{in_idx}:v]scale=420:-1,setpts=PTS-STARTPTS+{t0}/TB[ov_{in_idx}];"
                f"[{cur}][ov_{in_idx}]overlay=W-w-32:H-h-280:enable='between(t,{t0:.3f},{t1:.3f})'"
                f"[v_ov_{in_idx}]"
            )
        cur = f"v_ov_{in_idx}"

    # Hook caption
    hook_file_path = None
    if hook and hook_text.strip():
        hook_file_path = Path(temp_dir) / f"prof_hook_{int(clip_start)}.txt"
        hook_file_path.write_text(_wrap_hook(hook_text), encoding="utf-8")
        chain_parts.append(
            f"[{cur}]drawtext=textfile='{_ffesc(str(hook_file_path))}':"
            f"fontsize=46:fontcolor=white:"
            f"fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:"
            f"box=1:boxcolor=black@0.85:boxborderw=22:x=(w-text_w)/2:y=80:line_spacing=8"
            f"[v_hook]"
        )
        cur = "v_hook"

    # Kinetic captions (CapCut-style word box by default; shares the bundled
    # font + accent/case dials with the solo render path in stage7.py).
    if captions:
        srt_p = Path(srt)
        if srt_p.is_file():
            ass_path = Path(temp_dir) / f"prof_caps_{int(clip_start)}.ass"
            try:
                cap_font, cap_fonts_dir = kc.resolve_font()
                preset = os.environ.get("CLIP_CAPTION_PRESET", "capcut") or "capcut"
                rc = kc.srt_to_ass(
                    srt_p, ass_path, preset=preset,
                    emphasis_indices=plan["caption_emphasis"] or None,
                    font=cap_font,
                    accent=os.environ.get("CLIP_CAPTION_ACCENT", "yellow"),
                    caps=os.environ.get("CLIP_CAPTION_CAPS", "false").strip().lower()
                         in ("1", "true", "yes"),
                )
                if rc == 0 and ass_path.is_file():
                    chain_parts.append(
                        f"[{cur}]subtitles='{_ffesc(str(ass_path))}'"
                        f":fontsdir='{_ffesc(cap_fonts_dir)}'[v_caps]"
                    )
                    cur = "v_caps"
            except Exception as e:
                _log(f"kinetic_captions failed, skipping: {e}")

    # Final video label rename to [vout] for consistency
    if cur != "vout":
        chain_parts.append(f"[{cur}]null[vout]")

    # ─── Audio filter chain ───────────────────────────────────────────────
    audio_chain, _, _ = _build_audio_chain(
        plan={**plan, "_sfx_layer": sfx_layer},
        profile=profile,
        src_idx=0,
        speed=speed,
        sfx_inputs_start=1,
        music_input_idx=music_input_idx,
        fingerprint=fingerprint,
        clip_duration=clip_duration,
    )

    filter_complex = ";".join(chain_parts) + ";" + audio_chain

    # ─── FFmpeg invocation with container fingerprinting ──────────────────
    base_crf = 20
    crf = max(18, min(24, base_crf + int(fingerprint.get("crf_jitter", 0))))
    gop = int(fingerprint.get("gop", 240))

    cmd = [
        "ffmpeg", "-nostdin", "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", "[vout]", "-map", "[aout]",
        *venc.video_args(crf=crf, preset_libx264="slow"),
        "-profile:v", "high", "-level", "4.2", "-pix_fmt", "yuv420p",
        "-r", "30",
        "-g", str(gop),
        "-b:v", "18M", "-maxrate", "20M", "-bufsize", "40M",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        "-map_metadata", "-1",
        "-fflags", "+bitexact",
        "-metadata", f"comment={fingerprint['encoder_token']}",
        out,
    ]

    try:
        r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                           timeout=600, check=False)
    except subprocess.TimeoutExpired:
        _log("FFmpeg timed out (>10 min)")
        return 2

    if r.returncode != 0:
        # Capture last few lines of stderr for diagnostics — not the whole 50 KB.
        tail = (r.stderr or b"").decode("utf-8", "replace").splitlines()[-10:]
        _log("FFmpeg failed:\n  " + "\n  ".join(tail))
        return 3

    if not Path(out).is_file() or Path(out).stat().st_size < 1024:
        _log(f"FFmpeg returned 0 but output is missing/empty: {out}")
        return 4

    return 0


def _synthesize_plan(plan: dict, profile: dict, seed: int,
                     clip_duration: float) -> dict:
    """Fill in zoom punches and SFX cues from the resolved profile when
    vision didn't emit them. Distributes punches evenly across the clip
    timeline (avoiding the first/last 0.8 s buffer) and pairs each with
    a profile-appropriate cut SFX.

    Vision-supplied lists are preserved unchanged — synthesis only fills
    *empty* slots.
    """
    import random
    rng = random.Random(int(hashlib.md5(f"synth:{seed}".encode()).hexdigest()[:8], 16))

    # Zoom punches
    if not plan["zoom_punches"]:
        n = int(profile.get("zoom_punch_count") or 0)
        n = max(0, min(n, 5))
        if n > 0 and clip_duration > 2.0:
            buf = 0.8
            usable = max(0.5, clip_duration - 2 * buf)
            step = usable / max(n, 1)
            punches = []
            for i in range(n):
                t = round(buf + step * (i + 0.5) + rng.uniform(-0.15, 0.15), 3)
                t = max(buf, min(t, clip_duration - buf))
                punches.append({"t": t, "scale": 1.15, "hold": 0.30})
            plan["zoom_punches"] = punches

    # SFX cues — one cue per zoom punch (sfx_on_cuts pool), one mid-clip
    # peak SFX from sfx_on_peak.
    if not plan["sfx_cues"]:
        cues: list[dict] = []
        cuts_pool = list(profile.get("sfx_on_cuts") or [])
        peak_pool = list(profile.get("sfx_on_peak") or [])
        for zp_ in plan["zoom_punches"]:
            if not cuts_pool:
                break
            kind = rng.choice(cuts_pool)
            cues.append({"t": float(zp_["t"]), "kind": kind})
        if peak_pool and clip_duration > 4.0:
            cues.append({
                "t": round(clip_duration / 2.0, 3),
                "kind": rng.choice(peak_pool),
            })
        if cues:
            plan["sfx_cues"] = cues

    return plan


# Local import — must be at module top, but also used inside _synthesize_plan
import hashlib  # noqa: E402


def _wrap_hook(text: str) -> str:
    import textwrap
    text = (text or "").strip()
    if not text:
        return text
    lines = textwrap.wrap(text, 22)[:3]
    return "\n".join(lines) if lines else text[:60]


def _ffesc(p: str) -> str:
    """Escape a filesystem path for inclusion in an FFmpeg filter argument."""
    return p.replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--moment-json", required=True)
    ap.add_argument("--src", required=True)
    ap.add_argument("--srt", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--clip-start", type=float, required=True)
    ap.add_argument("--clip-duration", type=float, required=True)
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--captions", choices=["on", "off", "true", "false"], default="on")
    ap.add_argument("--hook", choices=["on", "off", "true", "false"], default="on")
    ap.add_argument("--hook-text", default="")
    ap.add_argument("--temp-dir", default=os.environ.get("TEMP_DIR", "/tmp/clipper"))
    ap.add_argument("--lib-dir", default=str(_HERE))
    ap.add_argument("--music-folder", default=os.environ.get("CLIP_MUSIC_BED", ""))
    ap.add_argument("--clips-dir", default=os.environ.get("CLIPS_DIR", ""))
    args = ap.parse_args()

    try:
        moment = json.loads(Path(args.moment_json).read_text(encoding="utf-8"))
    except Exception as e:
        _log(f"failed to read moment JSON: {e}")
        return 1

    return render(
        moment=moment, src=args.src, srt=args.srt, out=args.out,
        clip_start=args.clip_start, clip_duration=args.clip_duration,
        speed=args.speed,
        captions=args.captions in ("on", "true"),
        hook=args.hook in ("on", "true"),
        hook_text=args.hook_text,
        temp_dir=args.temp_dir, lib_dir=args.lib_dir,
        music_folder=(args.music_folder or None),
        clips_dir=(args.clips_dir or None),
    )


if __name__ == "__main__":
    sys.exit(main())
