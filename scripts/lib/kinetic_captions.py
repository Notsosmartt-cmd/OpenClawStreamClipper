#!/usr/bin/env python3
"""Convert a Whisper word-level SRT into an ASS file with karaoke-style
word-by-word reveals + per-preset typography.

Input: a `.srt` file produced by Stage 7's batch-Whisper transcription
(word-level timestamps). Output: a `.ass` file the FFmpeg `subtitles`
filter can burn in.

Five presets ship with the library, each as an `.ass.tpl` file under
`assets/caption_styles/`:
    neon, bouncy, clean, news, soft

Each preset defines a single `[V4+ Styles]` block + a `{kinetic}` macro
that this script substitutes per-word with the active-word color/scale.

When the SRT lacks word-level timing (just sentence-level entries), we
fall back to whole-sentence reveals using the same preset typography.
"""
from __future__ import annotations

import re
from pathlib import Path

ASSETS_ROOT = Path(__file__).resolve().parent.parent.parent / "assets"
STYLES_ROOT = ASSETS_ROOT / "caption_styles"

# Hardcoded fallback templates — used when the .ass.tpl files are missing
# from disk. Keeps the pipeline working out of the box without requiring
# the asset shipping step. The on-disk templates can override these.
FALLBACK_TEMPLATES: dict[str, dict[str, str]] = {
    "neon": {
        "header": (
            "[Script Info]\n"
            "ScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n"
            "[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
            "OutlineColour, BackColour, Bold, Italic, BorderStyle, Outline, "
            "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
            "Style: Default,Arial Black,84,&H00FFFFFF,&H000000FF,&H00FF00FF,"
            "&H00000000,1,0,1,5,2,2,40,40,90,1\n\n"
            "[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, "
            "MarginV, Effect, Text\n"
        ),
        "active_color": "&H0000FFFF",  # cyan
        "scale_pop":    True,
    },
    "bouncy": {
        "header": (
            "[Script Info]\n"
            "ScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n"
            "[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
            "OutlineColour, BackColour, Bold, Italic, BorderStyle, Outline, "
            "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
            "Style: Default,Komika Axis,76,&H00FFFFFF,&H000000FF,&H00000000,"
            "&H00000000,1,0,1,4,2,2,40,40,80,1\n\n"
            "[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, "
            "MarginV, Effect, Text\n"
        ),
        "active_color": "&H0000E5FF",  # warm yellow
        "scale_pop":    True,
    },
    "clean": {
        "header": (
            "[Script Info]\n"
            "ScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n"
            "[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
            "OutlineColour, BackColour, Bold, Italic, BorderStyle, Outline, "
            "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
            "Style: Default,Arial,72,&H00FFFFFF,&H000000FF,&H00000000,"
            "&H00000000,1,0,1,3,1,2,50,50,80,1\n\n"
            "[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, "
            "MarginV, Effect, Text\n"
        ),
        "active_color": "&H00FFFFFF",  # white (no color shift, just bold the word)
        "scale_pop":    False,
    },
    "news": {
        "header": (
            "[Script Info]\n"
            "ScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n"
            "[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
            "OutlineColour, BackColour, Bold, Italic, BorderStyle, Outline, "
            "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
            "Style: Default,Arial,68,&H00FFFFFF,&H000000FF,&H00000000,"
            "&H00000000,1,0,3,2,1,2,40,40,90,1\n\n"
            "[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, "
            "MarginV, Effect, Text\n"
        ),
        "active_color": "&H000000FF",  # red
        "scale_pop":    False,
    },
    "soft": {
        "header": (
            "[Script Info]\n"
            "ScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n"
            "[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
            "OutlineColour, BackColour, Bold, Italic, BorderStyle, Outline, "
            "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
            "Style: Default,Helvetica,66,&H00FFFFFF,&H000000FF,&H00282828,"
            "&H00000000,0,0,1,2,1,2,60,60,80,1\n\n"
            "[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, "
            "MarginV, Effect, Text\n"
        ),
        "active_color": "&H00CCFFFF",  # very pale yellow
        "scale_pop":    False,
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# SRT parsing
# ─────────────────────────────────────────────────────────────────────────────

_TIME_RE = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)")


def _parse_time(s: str) -> float:
    m = _TIME_RE.search(s)
    if not m:
        return 0.0
    h, mi, se, ms = m.groups()
    return int(h) * 3600 + int(mi) * 60 + int(se) + int(ms) / 1000.0


def _ass_time(t: float) -> str:
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def parse_srt(path: Path) -> list[dict]:
    """Return [{start, end, text}, ...] from a Whisper SRT.

    For word-level SRTs, each entry is one word. For sentence-level, each
    entry is a sentence — caller treats those as a single 'word' per entry.
    """
    if not path.is_file():
        return []
    blocks = path.read_text(encoding="utf-8", errors="replace").strip().split("\n\n")
    out: list[dict] = []
    for blk in blocks:
        lines = blk.strip().splitlines()
        if len(lines) < 2:
            continue
        time_line = next((l for l in lines if "-->" in l), None)
        if not time_line:
            continue
        a, _, b = time_line.partition("-->")
        text = " ".join(l for l in lines[lines.index(time_line) + 1:]).strip()
        if not text:
            continue
        out.append({
            "start": _parse_time(a.strip()),
            "end":   _parse_time(b.strip()),
            "text":  text,
        })
    return out


def _ass_escape(s: str) -> str:
    # ASS uses {} for override codes — escape literal braces.
    return s.replace("{", "(").replace("}", ")").replace("\\", "/")


# ─────────────────────────────────────────────────────────────────────────────
# Word grouping (~3 words per visible chunk for muted-watch readability)
# ─────────────────────────────────────────────────────────────────────────────

def _group_words(words: list[dict], group_size: int = 3) -> list[dict]:
    """Group consecutive Whisper words into 3-word chunks. Each chunk has
    .start, .end, and .word_specs (list of per-word [start_in_chunk, text])."""
    chunks: list[dict] = []
    for i in range(0, len(words), group_size):
        slice_ = words[i:i + group_size]
        if not slice_:
            continue
        chunks.append({
            "start": slice_[0]["start"],
            "end":   slice_[-1]["end"],
            "words": [
                {"start": w["start"], "end": w["end"], "text": w["text"]}
                for w in slice_
            ],
        })
    return chunks


# ─────────────────────────────────────────────────────────────────────────────
# Render
# ─────────────────────────────────────────────────────────────────────────────

def _load_template(preset: str) -> dict[str, str]:
    """Resolve a preset name to its header + style hints, preferring
    on-disk `.ass.tpl` files when present."""
    tpl_path = STYLES_ROOT / f"{preset}.ass.tpl"
    if tpl_path.is_file():
        try:
            txt = tpl_path.read_text(encoding="utf-8")
            # First line of the template is `# active_color: <hex>` etc;
            # we keep header + extract metadata via simple comments.
            meta = {"active_color": "&H00FFFFFF", "scale_pop": False}
            header_lines = []
            for line in txt.splitlines():
                if line.startswith("# active_color:"):
                    meta["active_color"] = line.split(":", 1)[1].strip()
                    continue
                if line.startswith("# scale_pop:"):
                    meta["scale_pop"] = line.split(":", 1)[1].strip().lower() == "true"
                    continue
                header_lines.append(line)
            return {"header": "\n".join(header_lines) + "\n",
                    "active_color": meta["active_color"],
                    "scale_pop": bool(meta["scale_pop"])}
        except OSError:
            pass
    return FALLBACK_TEMPLATES.get(preset, FALLBACK_TEMPLATES["clean"])


def render_ass(words: list[dict], preset: str = "clean",
               emphasis_indices: list[int] | None = None) -> str:
    """Build the full ASS file contents (header + dialogue events).

    `emphasis_indices`: optional list of WORD indices (0-based across the
    full input) that should be rendered with extra visual weight (color
    swap to `active_color`, scale pop). Stage 6 vision can populate these
    from the moment's punchline tagging.
    """
    tpl = _load_template(preset)
    active_color = tpl.get("active_color", "&H00FFFFFF")
    scale_pop = bool(tpl.get("scale_pop", False))
    lines: list[str] = [tpl["header"].rstrip(), ""]

    # Tag each word with its global index so emphasis lookups work.
    flat_words: list[dict] = []
    for w in words:
        flat_words.append(dict(w, _idx=len(flat_words)))
    chunks = _group_words(flat_words, group_size=3)

    emph = set(emphasis_indices or [])
    for ch in chunks:
        # Build the chunk's text with override tags on the active word.
        # We render the chunk full duration; the active word is highlighted
        # via \k karaoke timing inside the chunk.
        parts: list[str] = []
        chunk_start = ch["start"]
        for w in ch["words"]:
            wd = max(0.05, w["end"] - max(w["start"], chunk_start))
            kt = int(round(wd * 100))  # \k uses centiseconds
            highlight = w.get("_idx", -1) in emph
            tag = []
            if highlight:
                tag.append(f"\\1c{active_color}")
                if scale_pop:
                    tag.append("\\fscx115\\fscy115")
            else:
                tag.append("\\1c&H00FFFFFF&")
            override = "{" + "".join(tag) + f"\\k{kt}" + "}"
            parts.append(override + _ass_escape(w["text"]) + " ")
        text = "".join(parts).rstrip()
        lines.append(
            f"Dialogue: 0,{_ass_time(ch['start'])},{_ass_time(ch['end'])},"
            f"Default,,0,0,0,,{text}"
        )

    return "\n".join(lines) + "\n"


def srt_to_ass(srt_path: Path, ass_path: Path,
               preset: str = "clean",
               emphasis_indices: list[int] | None = None) -> int:
    """Read SRT, write ASS. Returns 0 on success, 1 if SRT was empty."""
    words = parse_srt(srt_path)
    if not words:
        return 1
    ass = render_ass(words, preset=preset, emphasis_indices=emphasis_indices)
    ass_path.parent.mkdir(parents=True, exist_ok=True)
    ass_path.write_text(ass, encoding="utf-8")
    return 0


def _cli() -> int:
    import argparse, sys
    ap = argparse.ArgumentParser()
    ap.add_argument("--srt", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--preset", default="clean")
    ap.add_argument("--emphasis", default="",
                    help="comma-separated word indices to emphasize")
    args = ap.parse_args()
    emph = [int(x) for x in args.emphasis.split(",") if x.strip().isdigit()]
    rc = srt_to_ass(Path(args.srt), Path(args.out), args.preset, emph)
    if rc != 0:
        print(f"# kinetic_captions: SRT empty or unreadable: {args.srt}", file=sys.stderr)
    return rc


if __name__ == "__main__":
    import sys
    sys.exit(_cli())
