#!/usr/bin/env python3
"""news_compile.py — the "Streamer News Today" third output mode (v1).

Implements concepts/plan-news-compilation-2026-07 with the owner's decisions:
a SEPARATE, explicit action (never part of the standard clip flow) that takes
one-or-more ALREADY-CLIPPED VODs and compiles ONE "today this happened" video:

    [intro grid card + boom + piper VO]
      -> [story 1: payoff sub-cut of that VOD's best finished clip
          + lower-third headline + whoosh + piper anchor VO]
      -> [story 2 ...] -> output mp4

Architecture (deliberate, mirrors companion-shorts): stories are sub-cuts of
FINISHED clips, so captions/SFX/blur-fill are inherited and aligned for free —
no re-detection, no re-captioning. Selection joins the newest diagnostics trace
per VOD (selected candidates: timestamp + final_score) to the newest effects_log
run per VOD (clip titles + windows), then to the mp4s on disk.

Piper VO (owner: v1 flagship of the dormant Wave-D TTS): a news-anchor line per
story (the clip's already-human title) + an intro line. Failure-soft — if piper
or the voice is missing, the compilation still builds with text headlines only.

Usage:
  python scripts/news_compile.py --vods 20260424_2xRaKai_2756365448[,other,...]
      [--budget 90] [--max-per-vod 2] [--no-vo] [--out <mp4>]
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CLIPS = REPO / "clips"
DIAG = CLIPS / ".diagnostics"
FONT = REPO / "assets" / "fonts" / "Montserrat-Black.ttf"
VOICE = REPO / "assets" / "piper" / "en_US-ryan-high.onnx"

sys.path.insert(0, str(REPO / "scripts" / "research"))
import our_clip_cards as occ  # noqa: E402  (_norm, _title_match — same join rules)


def log(m: str) -> None:
    print(f"[news_compile] {m}", flush=True)


def _ff(path) -> str:
    return str(path).replace("\\", "/").replace(":", "\\:")


def _run(cmd: list[str], timeout: int = 240) -> bool:
    r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=timeout)
    if r.returncode != 0:
        tail = (r.stderr or b"").decode("utf-8", "replace").splitlines()[-6:]
        log("ffmpeg failed:\n  " + "\n  ".join(tail))
        return False
    return True


def _streamer(vod_stem: str) -> str:
    m = re.match(r"\d{8}_(.+?)_\d+$", vod_stem)
    return (m.group(1) if m else vod_stem)


# ---------------------------------------------------------------------------
# Story selection: trace (T, score) x effects (title, window) x disk
# ---------------------------------------------------------------------------
def _newest_trace_for(vod_stem: str) -> dict | None:
    best = None
    for p in sorted(DIAG.glob("last_run_*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        pc = d.get("pass_c_candidates") if isinstance(d.get("pass_c_candidates"), dict) else d
        vod = str((pc or {}).get("vod") or "")
        if vod.rsplit(".", 1)[0].lower() == vod_stem.lower():
            best = pc  # glob sorted ascending -> last match is newest
    return best


def _newest_effects_for(vod_stem: str) -> list[dict]:
    """render_plan rows of the NEWEST effects run whose vod matches."""
    fp = DIAG / "effects_log.jsonl"
    if not fp.exists():
        return []
    by_run: dict[str, list[dict]] = {}
    for line in fp.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("type") != "render_plan":
            continue
        if str(r.get("vod") or "").rsplit(".", 1)[0].lower() != vod_stem.lower():
            continue
        by_run.setdefault(str(r.get("run")), []).append(r)
    if not by_run:
        return []
    newest = sorted(by_run)[-1]
    return by_run[newest]


_SKIP_DIRS = {"post_kits", ".diagnostics", ".pipeline_logs"}


def _all_clip_files() -> list[Path]:
    """Every clip mp4 under clips/, RECURSIVE — the owner organizes finished
    clips into subfolders (2xBvnks/741/keep/…), so a root-only glob goes blind
    after a reorganize. Root files first (freshest pipeline output), then
    subfolders; utility dirs skipped."""
    out = [f for f in sorted(CLIPS.glob("*.mp4"))]
    for f in sorted(CLIPS.rglob("*.mp4")):
        if f.parent == CLIPS:
            continue
        if any(part in _SKIP_DIRS for part in f.relative_to(CLIPS).parts):
            continue
        out.append(f)
    return out


def _clip_file_for(title: str, variant: str = "") -> Path | None:
    """Find the on-disk clip for an effects_log title. variant='B' looks for the
    A/B '<title> (B).mp4' render instead (None if that story has no B).
    'STREAMERS UPDATE' outputs are never story sources."""
    tnorm = occ._norm(title)
    for f in _all_clip_files():
        stem = f.stem
        if stem.startswith("STREAMERS UPDATE") or stem.endswith(" (Short)"):
            continue
        is_b = stem.endswith(" (B)")
        if (variant == "B") != is_b:
            continue
        base = stem[:-4] if is_b else stem
        if occ._title_match(occ._norm(base), tnorm):
            return f
    return None


# NEWS-mode weighting (owner 2026-07-11): a news compilation should surface
# "story times, controversial, or impactful events" — not only funny peaks. The
# standard ranking is comedy/highlight-tuned; these multipliers re-weight the
# already-scored candidates for the news context. Applied on top of final_score;
# category comes from the effects_log render_plan (the profile category).
NEWS_WEIGHTS = {
    "controversial": 1.35, "controversy": 1.35,
    "storytime": 1.20, "story": 1.20,
    "hype": 1.15, "emotional": 1.10,
    "reactive": 1.0, "funny": 1.0,
}


def _news_score(s: dict) -> float:
    return s["score"] * NEWS_WEIGHTS.get(str(s.get("category") or "").lower(), 1.0)


def select_stories(vod_stems: list[str], max_per_vod: int, budget_s: float,
                   story_len: float) -> list[dict]:
    per_vod: dict[str, list[dict]] = {}
    for stem in vod_stems:
        tr = _newest_trace_for(stem)
        rows = _newest_effects_for(stem)
        if not tr or not rows:
            log(f"SKIP {stem}: no trace/effects — run normal clipping on it first")
            continue
        sel = [c for c in (tr.get("candidates") or []) if c.get("selected")]
        stories = []
        _missing = 0
        for c in sel:
            try:
                T = float(c.get("timestamp"))
            except (TypeError, ValueError):
                continue
            row = next((r for r in rows
                        if (r.get("data") or {}).get("clip_start") is not None
                        and float(r["data"]["clip_start"]) <= T
                        <= float(r["data"]["clip_start"]) + float(r["data"].get("clip_duration") or 0)),
                       None)
            if row is None:
                continue
            f = _clip_file_for(str(row.get("clip") or ""))
            if f is None:
                _missing += 1
                continue
            stories.append({
                "vod": stem, "streamer": _streamer(stem),
                "T": T, "score": float(c.get("final_score") or 0),
                "category": str((row.get("data") or {}).get("category") or ""),
                "title": str(row.get("clip")), "file": f,
                "clip_start": float(row["data"]["clip_start"]),
                "clip_duration": float(row["data"].get("clip_duration") or 30),
            })
        if _missing:
            log(f"{stem}: {_missing} candidate(s) matched the run's effects but their clip "
                f"mp4s are no longer under clips/ (moved/archived after posting?) — "
                f"re-clip the VOD to compile from fresh sources")
        stories.sort(key=lambda s: -_news_score(s))
        # de-dup same clip file (two candidates can share a window)
        seen, uniq = set(), []
        for s in stories:
            if s["file"].name in seen:
                continue
            seen.add(s["file"].name)
            uniq.append(s)
        if uniq:
            per_vod[stem] = uniq[:max_per_vod]
    # round-robin: everyone's #1 first (per-VOD/streamer guarantee — the multi-
    # streamer coverage IS the format), then #2s, under budget. Within a rank,
    # CATEGORY DIVERSITY: if a vod's rank-r story duplicates an already-picked
    # category and its next story brings a NEW category at >=85% of the news
    # score, swap them — a news video wants varied story types, not four
    # punchlines in a row.
    picked: list[dict] = []
    picked_cats: set[str] = set()
    rank = 0
    while True:
        added = False
        for stem in vod_stems:
            lst = per_vod.get(stem) or []
            if rank >= len(lst):
                continue
            cand = lst[rank]
            cat = str(cand.get("category") or "").lower()
            if cat and cat in picked_cats and rank + 1 < len(lst):
                alt = lst[rank + 1]
                alt_cat = str(alt.get("category") or "").lower()
                if alt_cat and alt_cat not in picked_cats and \
                        _news_score(alt) >= 0.85 * _news_score(cand):
                    lst[rank], lst[rank + 1] = alt, cand
                    cand, cat = alt, alt_cat
            if (len(picked) + 1) * story_len <= budget_s:
                picked.append(cand)
                picked_cats.add(cat)
                added = True
        if not added:
            break
        rank += 1
    return picked


# ---------------------------------------------------------------------------
# Piper VO (failure-soft)
# ---------------------------------------------------------------------------
_VOICE_CACHE = {"v": None, "tried": False}


def _voice():
    if _VOICE_CACHE["tried"]:
        return _VOICE_CACHE["v"]
    _VOICE_CACHE["tried"] = True
    try:
        from piper import PiperVoice
        if VOICE.exists():
            _VOICE_CACHE["v"] = PiperVoice.load(str(VOICE))
            log(f"piper voice loaded: {VOICE.name}")
        else:
            log(f"piper voice missing at {VOICE} — text-only headlines")
    except Exception as e:
        log(f"piper unavailable ({type(e).__name__}: {e}) — text-only headlines")
    return _VOICE_CACHE["v"]


def synth_vo(text: str, out_wav: Path) -> float:
    """Synthesize text -> wav; returns duration_s (0.0 = no VO)."""
    v = _voice()
    if v is None or not text.strip():
        return 0.0
    try:
        import wave
        with wave.open(str(out_wav), "wb") as w:
            v.synthesize_wav(text.strip(), w)
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "csv=p=0", str(out_wav)], capture_output=True, text=True, timeout=30)
        return float(r.stdout.strip() or 0.0)
    except Exception as e:
        log(f"VO synth failed ({type(e).__name__}) — skipping line")
        return 0.0


# Variant knob: 0 = compilation A, 1 = compilation B (rotates the SFX draw so
# the two versions differ in sound furniture too, per the A/B varied-AV doctrine).
_SFX_INDEX = 0


def _pick_sfx(kind: str) -> Path | None:
    d = REPO / "assets" / "sfx" / kind
    if not d.is_dir():
        return None
    cands: list[Path] = []
    # honor a library.json manifest when present (same rule as sfx_inject)
    man = d / "library.json"
    if man.exists():
        try:
            entries = json.loads(man.read_text(encoding="utf-8"))
            names = [e.get("file") for e in entries if isinstance(e, dict) and e.get("file")] \
                if isinstance(entries, list) else \
                [e.get("file") for e in entries.get("sounds", []) if e.get("file")]
            cands = [d / n for n in names if n and (d / n).exists()]
        except Exception:
            cands = []
    if not cands:
        cands = [f for f in sorted(d.iterdir()) if f.suffix.lower() in (".mp3", ".wav", ".ogg")]
    if not cands:
        return None
    return cands[_SFX_INDEX % len(cands)]


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
_ENC = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-r", "30", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]


def _wrap(text: str, width: int = 30, max_lines: int = 2) -> str:
    import textwrap
    lines = textwrap.wrap(text.strip(), width)[:max_lines]
    return "\n".join(lines) if lines else text[:width]


def render_story(story: dict, idx: int, work: Path, story_len: float, use_vo: bool) -> Path | None:
    src = story["file"]
    payoff_r = max(0.0, story["T"] - story["clip_start"])   # speed 1.0 assumed (dashboard default)
    dur = story["clip_duration"]
    start = max(0.0, min(payoff_r - 4.5, max(0.0, dur - story_len)))
    length = min(story_len, dur - start)
    if length < 6:
        start, length = 0.0, min(story_len, dur)

    head_file = work / f"head_{idx}.txt"
    head_file.write_text(f"{story['streamer'].upper()}\n" + _wrap(story["title"]), encoding="utf-8")

    vo_wav, vo_len = work / f"vo_{idx}.wav", 0.0
    if use_vo:
        vo_len = synth_vo(f"{story['streamer']} — {story['title']}.", vo_wav)
    whoosh = _pick_sfx("whoosh")

    # video: sub-cut + lower-third headline banner (above the burned captions)
    vf = (f"drawtext=textfile='{_ff(head_file)}':fontfile='{_ff(FONT)}':fontsize=44:"
          f"fontcolor=white:box=1:boxcolor=black@0.55:boxborderw=18:line_spacing=10:"
          f"x=(w-text_w)/2:y=h-620")
    inputs = ["-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", str(src)]
    n, fc_a = 1, []
    amix_in = "[0:a]volume=0.95[a0]"
    mix = "[a0]"
    if whoosh:
        inputs += ["-i", str(whoosh)]
        fc_a.append(f"[{n}:a]atrim=0:1.6,volume=0.5[aw]")
        mix += "[aw]"
        n += 1
    if vo_len > 0:
        inputs += ["-i", str(vo_wav)]
        fc_a.append(f"[{n}:a]adelay=350|350,volume=1.5[av]")
        mix += "[av]"
        n += 1
    filter_complex = (f"[0:v]{vf}[vout];{amix_in};" + (";".join(fc_a) + ";" if fc_a else "")
                      + f"{mix}amix=inputs={1 + len(fc_a)}:duration=first:"
                        f"dropout_transition=0:normalize=0[aout]")
    out = work / f"story_{idx}.mp4"
    ok = _run(["ffmpeg", "-nostdin", "-y", *inputs, "-filter_complex", filter_complex,
               "-map", "[vout]", "-map", "[aout]", *_ENC, str(out)])
    return out if ok and out.exists() else None


def render_intro(stories: list[dict], work: Path, use_vo: bool) -> Path | None:
    # thumbnails at each story's payoff, from the FINISHED clips
    thumbs = []
    for i, s in enumerate(stories[:4]):
        t = max(0.0, (s["T"] - s["clip_start"]))
        th = work / f"thumb_{i}.jpg"
        if _run(["ffmpeg", "-nostdin", "-y", "-ss", f"{t:.2f}", "-i", str(s["file"]),
                 "-frames:v", "1", "-vf", "scale=540:960:force_original_aspect_ratio=increase,crop=540:960",
                 "-q:v", "3", str(th)], timeout=60):
            thumbs.append(th)
    if not thumbs:
        return None
    while len(thumbs) < 4:                       # tile to a full 2x2
        thumbs.append(thumbs[len(thumbs) % max(1, len(thumbs) - 1)])

    date_txt = time.strftime("%m/%d/%Y")
    title_file = work / "intro_title.txt"
    title_file.write_text(f"STREAMERS\nUPDATE\n{date_txt}", encoding="utf-8")

    vo_wav, vo_len = work / "vo_intro.wav", 0.0
    if use_vo:
        vo_len = synth_vo(f"Streamers update, {time.strftime('%B %d')}.", vo_wav)
    intro_len = max(2.8, min(4.5, vo_len + 0.6))
    boom = _pick_sfx("boom")

    inputs = []
    for th in thumbs[:4]:
        inputs += ["-loop", "1", "-t", f"{intro_len:.2f}", "-i", str(th)]
    fc = ("[0:v][1:v][2:v][3:v]xstack=inputs=4:layout=0_0|w0_0|0_h0|w0_h0[grid];"
          f"[grid]drawbox=x=0:y=760:w=1080:h=420:color=black@0.6:t=fill,"
          f"drawtext=textfile='{_ff(title_file)}':fontfile='{_ff(FONT)}':fontsize=96:"
          f"fontcolor=white:line_spacing=14:x=(w-text_w)/2:y=800[vout]")
    n = 4
    a_parts, mix, n_mix = [], "", 0
    if boom:
        inputs += ["-i", str(boom)]
        a_parts.append(f"[{n}:a]adelay=120|120,volume=0.9[ab]")
        mix += "[ab]"; n += 1; n_mix += 1
    if vo_len > 0:
        inputs += ["-i", str(vo_wav)]
        a_parts.append(f"[{n}:a]adelay=500|500,volume=1.5[av]")
        mix += "[av]"; n += 1; n_mix += 1
    if n_mix == 0:
        fc += f";anullsrc=r=48000:cl=stereo,atrim=0:{intro_len:.2f}[aout]"
    else:
        a_parts.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{intro_len:.2f}[sil]")
        fc += ";" + ";".join(a_parts) + f";[sil]{mix}amix=inputs={n_mix + 1}:duration=first:normalize=0[aout]"
    out = work / "intro.mp4"
    ok = _run(["ffmpeg", "-nostdin", "-y", *inputs, "-filter_complex", fc,
               "-map", "[vout]", "-map", "[aout]", "-t", f"{intro_len:.2f}", *_ENC, str(out)])
    return out if ok and out.exists() else None


def concat(pieces: list[Path], out: Path, work: Path) -> bool:
    inputs, pads = [], []
    for i, p in enumerate(pieces):
        inputs += ["-i", str(p)]
        pads.append(f"[{i}:v][{i}:a]")
    fc = "".join(pads) + f"concat=n={len(pieces)}:v=1:a=1[vout][aout]"
    return _run(["ffmpeg", "-nostdin", "-y", *inputs, "-filter_complex", fc,
                 "-map", "[vout]", "-map", "[aout]", *_ENC, str(out)], timeout=600)


def main() -> int:
    ap = argparse.ArgumentParser(description="Compile a 'Streamers Update' news video from clipped VODs")
    ap.add_argument("--vods", required=True, help="comma-separated VOD stems (already clipped)")
    ap.add_argument("--budget", type=float, default=90.0, help="target total seconds (default 90)")
    ap.add_argument("--max-per-vod", type=int, default=2)
    ap.add_argument("--story-len", type=float, default=14.0)
    ap.add_argument("--no-vo", action="store_true", help="skip piper narration (text headlines only)")
    ap.add_argument("--ab", action="store_true", help="also build the (B) compilation")
    ap.add_argument("--no-ab", action="store_true", help="never build the (B) compilation")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    stems = [v.strip() for v in args.vods.split(",") if v.strip()]
    stories = select_stories(stems, args.max_per_vod, args.budget, args.story_len)
    if not stories:
        log("no stories selected — have these VODs been clipped? (need trace + effects + clips on disk)")
        return 1
    log(f"{len(stories)} stories: " + " | ".join(
        f"{s['streamer']}[{s.get('category') or '?'}]:{s['title'][:30]}" for s in stories))

    use_vo = not args.no_vo
    # A/B extension (owner 2026-07-11): the compilation follows the clip-level
    # A/B doctrine. Version B swaps in each story's '(B)' clip render where one
    # exists (different hook + SFX/effects already baked in) + a different
    # whoosh draw. Default follows CLIP_AB_VARIANTS (>=2 = on, the pipeline
    # default) — CLI --ab/--no-ab override.
    try:
        _ab_env = int(__import__("os").environ.get("CLIP_AB_VARIANTS", "2") or "2") >= 2
    except ValueError:
        _ab_env = True
    build_b = args.ab or (_ab_env and not args.no_ab)

    def _compile(story_set: list[dict], out_path: Path, sfx_index: int, tag: str) -> bool:
        with tempfile.TemporaryDirectory(prefix="news_") as td:
            work = Path(td)
            global _SFX_INDEX
            _SFX_INDEX = sfx_index
            pieces: list[Path] = []
            intro = render_intro(story_set, work, use_vo)
            if intro:
                pieces.append(intro)
            for i, s in enumerate(story_set):
                log(f"[{tag}] story {i + 1}/{len(story_set)}: {s['streamer']} — {s['title'][:45]}")
                seg = render_story(s, i, work, args.story_len, use_vo)
                if seg:
                    pieces.append(seg)
            if len(pieces) < 2:
                log(f"[{tag}] not enough rendered pieces — aborting this variant")
                return False
            return concat(pieces, out_path, work)

    date_name = time.strftime("%m-%d-%Y")
    out = Path(args.out) if args.out else CLIPS / f"STREAMERS UPDATE {date_name}.mp4"
    if not _compile(stories, out, 0, "A"):
        return 1
    log(f"DONE -> {out}")

    b_out = None
    if build_b:
        stories_b, swapped = [], 0
        for s in stories:
            bf = _clip_file_for(s["title"], variant="B")
            sb = dict(s)
            if bf is not None:
                sb["file"] = bf
                swapped += 1
            stories_b.append(sb)
        if swapped == 0:
            log("A/B: no story has a (B) clip render — skipping the B compilation")
        else:
            b_out = out.with_name(out.stem + " (B).mp4")
            if _compile(stories_b, b_out, 1, "B"):
                log(f"DONE (B, {swapped}/{len(stories_b)} stories from (B) clips) -> {b_out}")
            else:
                b_out = None

    # review sidecar (post_kits/ keeps the clips root video-only)
    kit_dir = CLIPS / "post_kits"
    kit_dir.mkdir(exist_ok=True)
    (kit_dir / f"{out.stem}.news.json").write_text(json.dumps({
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "vods": stems, "vo": bool(use_vo and _VOICE_CACHE["v"]),
        "variant_b": str(b_out) if b_out else None,
        "stories": [{k: (str(v) if isinstance(v, Path) else v) for k, v in s.items()} for s in stories],
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
