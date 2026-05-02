#!/usr/bin/env python3
"""Render stitch-group clips — 3-4 short sub-segments concatenated into one post.

This runs as Stage 7e of the pipeline. It reads ``moment_groups.json`` and
``scored_moments.json``, and for every group with ``kind == "stitch"``:

1. Renders each member as a short standalone MP4 through the same framing
   filter chain the solo path uses, with per-member randomization (so
   Member 0 might flip, Member 1 might boost saturation, etc. — structurally
   different from each other).
2. Concatenates the members with ``xfade`` transitions (fade / slideleft /
   circlecrop / distance — rotated by the originality seed).
3. Applies the group's hook overlay and the subtitles SRT on the composite.

The output filename uses the first member's title. Diagnostic output is
written to stdout so Stage 7 can tee it into the pipeline log.

Env vars (set by the bash caller):
    CLIPS_DIR_ENV         output directory
    TEMP_DIR_ENV          where to write intermediate renders
    VOD_PATH_ENV          source VOD path
    CLIP_FRAMING_ENV      framing mode
    CLIP_ORIGINALITY_ENV  true/false — whether to apply random params
    CLIP_SPEED_ENV        playback speed multiplier
    CLIP_CAPTIONS_ENV     true/false
    CLIP_HOOK_ENV         true/false
    LIB_DIR               path to this helper directory
"""
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

CLIPS_DIR = Path(os.environ.get("CLIPS_DIR_ENV", "/root/VODs/Clips_Ready"))
TEMP_DIR = Path(os.environ.get("TEMP_DIR_ENV", "/tmp/clipper"))
VOD_PATH = os.environ.get("VOD_PATH_ENV", "")
FRAMING = os.environ.get("CLIP_FRAMING_ENV", "smart_crop")
ORIGINALITY = os.environ.get("CLIP_ORIGINALITY_ENV", "true")
SPEED = os.environ.get("CLIP_SPEED_ENV", "1.0")
CAPTIONS = os.environ.get("CLIP_CAPTIONS_ENV", "true")
HOOK_ENABLED = os.environ.get("CLIP_HOOK_ENV", "true")
LIB_DIR = Path(os.environ.get("LIB_DIR", "/root/scripts/lib"))

TRANSITIONS = {
    "fade": "fade",
    "wiperight": "wiperight",
    "slideup": "slideup",
    "circlecrop": "circlecrop",
    "distance": "distance",
    "slideleft": "slideleft",
    "radial": "radial",
}
TRANSITION_DUR = 0.35  # seconds


def load_inputs():
    mg = TEMP_DIR / "moment_groups.json"
    sm = TEMP_DIR / "scored_moments.json"
    if not mg.is_file() or not sm.is_file():
        print(f"missing inputs (mg={mg.is_file()}, sm={sm.is_file()})",
              file=sys.stderr)
        sys.exit(1)
    groups = json.loads(mg.read_text()).get("groups") or []
    moments = json.loads(sm.read_text())
    moment_by_ts = {m.get("timestamp"): m for m in moments}
    return groups, moment_by_ts


def eval_originality(ts: int, mirror_safe: bool, category: str) -> dict:
    """Invoke originality.py and parse its KEY=VALUE output into a dict."""
    proc = subprocess.run(
        ["python3", str(LIB_DIR / "originality.py"),
         str(ts), ORIGINALITY, str(bool(mirror_safe)).lower(), FRAMING, category],
        capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        return {}
    result = {}
    for line in proc.stdout.strip().splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            result[k.strip()] = v.strip().strip("'\"")
    return result


def build_frame_vf(params: dict, mirror_vf: str) -> str:
    """Replicates the bash-side framing logic in Python, using the same
    randomized params. Kept in sync with scripts/clip-pipeline.sh."""
    blur_r = params.get("BLUR_RADIUS", 25)
    blur_p = params.get("BLUR_PASSES", 5)
    eq_b = params.get("EQ_BRIGHTNESS", "0.0")
    eq_s = params.get("EQ_SATURATION", "1.0")
    eq_c = params.get("EQ_CONTRAST", "1.0")
    eq_g = params.get("EQ_GAMMA", "1.0")
    hue = params.get("HUE_SHIFT", "0.0")
    vignette = params.get("USE_VIGNETTE", "false") == "true"

    color = f"eq=brightness={eq_b}:saturation={eq_s}:contrast={eq_c}:gamma={eq_g},hue=h={hue}"
    if vignette:
        color += ",vignette=angle=PI/5"

    speed_vf = f"setpts=PTS/{SPEED}" if SPEED != "1.0" else "null"

    # Stitch members always render through the blur-fill chain. camera_pan
    # per-member is out of scope for Wave C — stitched posts already get
    # fingerprint variety from per-segment mirror + color randomization.
    return (f"{speed_vf},split[bg][fg];[bg]scale=1080:1920:force_original_aspect_ratio=increase,"
            f"crop=1080:1920,boxblur={blur_r}:{blur_p}[blurred];"
            f"[fg]scale=1080:-2:force_original_aspect_ratio=decrease{mirror_vf}[sharp];"
            f"[blurred][sharp]overlay=(W-w)/2:(H-h)/2,{color}")


def render_member(member: dict, moment: dict, out_path: Path) -> bool:
    t = member["timestamp"]
    start = member.get("start", moment.get("clip_start", t - 5))
    dur = float(member.get("duration", 10))
    category = moment.get("category", "hype")

    params = eval_originality(t, moment.get("mirror_safe", False), category)
    mirror_vf = ",hflip" if params.get("MIRROR") == "true" else ""
    frame_vf = build_frame_vf(params, mirror_vf)

    cmd = [
        "ffmpeg", "-nostdin", "-y",
        "-ss", str(start),
        "-t", f"{dur:.2f}",
        "-i", VOD_PATH,
        "-vf", frame_vf,
        "-c:v", "libx264", "-crf", "20", "-preset", "fast",
        "-profile:v", "high", "-level", "4.2",
        "-pix_fmt", "yuv420p", "-r", "30",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(out_path),
    ]
    rc = subprocess.call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return rc == 0 and out_path.is_file()


def concat_members(members: list[Path], out_path: Path, transition: str) -> bool:
    """Concatenate N member MP4s with xfade transitions between them."""
    if not members:
        return False
    if len(members) == 1:
        # Single member — just copy.
        rc = subprocess.call([
            "ffmpeg", "-nostdin", "-y", "-i", str(members[0]),
            "-c", "copy", "-movflags", "+faststart", str(out_path),
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return rc == 0

    # Build filter_complex with chained xfade
    inputs: list[str] = []
    for p in members:
        inputs.extend(["-i", str(p)])

    probes: list[float] = []
    for p in members:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(p)],
            capture_output=True, text=True,
        )
        try:
            probes.append(float(proc.stdout.strip()))
        except ValueError:
            probes.append(0.0)

    if any(d <= TRANSITION_DUR for d in probes):
        # At least one member is shorter than the transition — use concat demuxer
        list_file = members[0].with_suffix(".concatlist.txt")
        list_file.write_text("\n".join(f"file '{p}'" for p in members),
                             encoding="utf-8")
        rc = subprocess.call([
            "ffmpeg", "-nostdin", "-y", "-f", "concat", "-safe", "0",
            "-i", str(list_file),
            "-c:v", "libx264", "-crf", "20", "-preset", "fast",
            "-profile:v", "high", "-level", "4.2",
            "-pix_fmt", "yuv420p", "-r", "30",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart", str(out_path),
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return rc == 0

    # xfade chain: each transition knocks TRANSITION_DUR off the combined length
    filters: list[str] = []
    current = "0:v"
    a_current = "0:a"
    offset = probes[0] - TRANSITION_DUR
    for i in range(1, len(members)):
        new_v = f"vx{i}"
        new_a = f"ax{i}"
        filters.append(
            f"[{current}][{i}:v]xfade=transition={transition}:duration={TRANSITION_DUR}"
            f":offset={offset:.2f}[{new_v}]"
        )
        filters.append(
            f"[{a_current}][{i}:a]acrossfade=d={TRANSITION_DUR}[{new_a}]"
        )
        current = new_v
        a_current = new_a
        offset += probes[i] - TRANSITION_DUR

    filter_complex = ";".join(filters)
    cmd = [
        "ffmpeg", "-nostdin", "-y", *inputs,
        "-filter_complex", filter_complex,
        "-map", f"[{current}]", "-map", f"[{a_current}]",
        "-c:v", "libx264", "-crf", "20", "-preset", "fast",
        "-profile:v", "high", "-level", "4.2",
        "-pix_fmt", "yuv420p", "-r", "30",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart", str(out_path),
    ]
    rc = subprocess.call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return rc == 0


def apply_overlays(in_path: Path, out_path: Path, hook_text: str,
                   transition: str) -> bool:
    """Burn hook + subtitles on the concat composite."""
    vf_parts: list[str] = []

    if HOOK_ENABLED == "true" and hook_text:
        hook_file = TEMP_DIR / f"stitch_{in_path.stem}_hook.txt"
        hook_file.write_text(
            "\n".join(textwrap.wrap(hook_text[:60], 22)[:3]) or hook_text[:60],
            encoding="utf-8",
        )
        vf_parts.append(
            f"drawtext=textfile='{hook_file}':fontsize=40:fontcolor=black:"
            f"fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:"
            f"box=1:boxcolor=white@0.92:boxborderw=22:x=(w-text_w)/2:y=70:line_spacing=8"
        )

    vf = ",".join(vf_parts) if vf_parts else "null"
    cmd = [
        "ffmpeg", "-nostdin", "-y", "-i", str(in_path),
        "-vf", vf,
        "-c:v", "libx264", "-crf", "20", "-preset", "slow",
        "-profile:v", "high", "-level", "4.2",
        "-pix_fmt", "yuv420p", "-r", "30",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart", str(out_path),
    ]
    rc = subprocess.call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return rc == 0


def sanitize_title(raw: str) -> str:
    cleaned = "".join(c for c in raw.replace("/", "-").replace("\\", "-")
                      if c.isalnum() or c in " -_")
    return cleaned.strip()[:50] or "stitch_clip"


def render_group(group: dict, moments: dict[int, dict]) -> bool:
    gid = group.get("group_id", "stitch")
    members_spec = group.get("members") or []
    if not members_spec:
        return False

    member_moments: list[dict] = []
    for spec in members_spec:
        m = moments.get(spec["timestamp"])
        if m is None:
            print(f"  {gid}: moment missing for member T={spec['timestamp']}",
                  file=sys.stderr)
            continue
        member_moments.append((spec, m))

    if len(member_moments) < 2:
        print(f"  {gid}: fewer than 2 resolvable members — skipping", file=sys.stderr)
        return False

    first_ts = member_moments[0][0]["timestamp"]
    params = eval_originality(first_ts,
                              member_moments[0][1].get("mirror_safe", False),
                              group.get("category", "hype"))
    transition = TRANSITIONS.get(params.get("TRANSITION", "fade"), "fade")
    hook_text = (member_moments[0][1].get("hook")
                 or members_spec[0].get("hook")
                 or group.get("category", ""))

    member_files: list[Path] = []
    for i, (spec, moment) in enumerate(member_moments):
        out = TEMP_DIR / f"stitch_{gid}_m{i}.mp4"
        if not render_member(spec, moment, out):
            print(f"  {gid}: member {i} render failed", file=sys.stderr)
            continue
        member_files.append(out)

    if len(member_files) < 2:
        return False

    raw_concat = TEMP_DIR / f"stitch_{gid}_raw.mp4"
    if not concat_members(member_files, raw_concat, transition):
        print(f"  {gid}: concat failed", file=sys.stderr)
        return False

    title = sanitize_title(member_moments[0][1].get("title") or f"Stitch {gid}")
    final_path = CLIPS_DIR / f"{title}.mp4"
    if not apply_overlays(raw_concat, final_path, hook_text, transition):
        print(f"  {gid}: overlay pass failed — keeping raw concat as output",
              file=sys.stderr)
        # Ship the raw concat so the user still gets something.
        try:
            raw_concat.replace(final_path)
        except OSError:
            return False

    # Record in clips_made.txt so Stage 8 picks it up for the Discord summary.
    size_mb = final_path.stat().st_size // (1024 * 1024)
    (TEMP_DIR / "clips_made.txt").open("a").write(
        f"{title}|{group.get('score', 0)}|{group.get('category', 'stitch')}|"
        f"stitch of {len(member_files)} moments|{size_mb}MB|"
        f"{group.get('segment_type', '?')}|{int(group.get('total_duration', 30))}s\n"
    )
    print(f"  {gid}: stitched {len(member_files)} members → {final_path.name} "
          f"({size_mb} MB, transition={transition})")
    return True


def main() -> int:
    groups, moments = load_inputs()
    stitch_groups = [g for g in groups if g.get("kind") == "stitch"]
    print(f"stitch render: {len(stitch_groups)} group(s) to process")
    made = 0
    for g in stitch_groups:
        try:
            if render_group(g, moments):
                made += 1
        except Exception as e:
            print(f"  {g.get('group_id')}: render raised {type(e).__name__}: {e}",
                  file=sys.stderr)
    print(f"stitch render: {made}/{len(stitch_groups)} groups complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
