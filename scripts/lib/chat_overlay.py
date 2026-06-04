#!/usr/bin/env python3
"""Twitch chat overlay generator (controversy/hot_take profiles).

Renders a vertical strip of recent chat messages around a moment timestamp
into a short MP4 that can be overlaid on the right side of a clip via
FFmpeg's `overlay` filter.

DEPENDENCY NOTES:
  - Requires a chat dump for the VOD. We look in this order:
      $TEMP_DIR/chat.json
      <vod_path>.chat.json
      <vod_path>.chatty.txt   (Chatty plain-text export)
      $CHAT_PATH                env var (set by chat-fetch upstream)
  - When no chat data is available, this module short-circuits and
    returns None — the renderer skips the overlay and logs a one-line
    "chat overlay: no source data, skipped" message.
  - When Pillow is unavailable (older container builds), we likewise
    short-circuit. Pillow is a small dep but not yet in the runtime
    requirements; future versions of the editing-profiles pipeline will
    add it.

This is the Phase 5 stub — the architecture is in place; the actual
chat-MP4 renderer is implemented behind a Pillow availability check so
the rest of the profile system works on day one and the overlay layer
turns on automatically when the dependency is added.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont  # type: ignore
    _PIL_OK = True
except Exception:
    _PIL_OK = False


def _find_chat_source(vod_path: str | None, temp_dir: str | None) -> Path | None:
    candidates: list[Path] = []
    env_path = os.environ.get("CHAT_PATH")
    if env_path:
        candidates.append(Path(env_path))
    if temp_dir:
        candidates.append(Path(temp_dir) / "chat.json")
    if vod_path:
        v = Path(vod_path)
        candidates += [
            v.with_suffix(v.suffix + ".chat.json"),
            v.with_suffix(".chat.json"),
            v.with_suffix(".chatty.txt"),
        ]
    for p in candidates:
        if p.is_file():
            return p
    return None


def load_chat_window(source: Path, t_start: float, t_end: float) -> list[dict]:
    """Load chat messages whose offset falls in [t_start, t_end]."""
    try:
        if source.suffix.lower() == ".json":
            data = json.loads(source.read_text(encoding="utf-8", errors="replace"))
            comments = data.get("comments") or data.get("messages") or data
            out = []
            for c in comments if isinstance(comments, list) else []:
                t = c.get("content_offset_seconds") or c.get("offset") or c.get("t")
                msg = c.get("message") or c.get("body") or {}
                if isinstance(msg, dict):
                    text = msg.get("body") or msg.get("text") or ""
                else:
                    text = str(msg)
                user = (c.get("commenter") or {}).get("display_name") or c.get("user") or "user"
                if t is None or not text:
                    continue
                ti = float(t)
                if t_start <= ti <= t_end:
                    out.append({"t": ti, "user": str(user), "text": str(text)})
            return out
        # Chatty plain-text export — "[hh:mm:ss] user: msg"
        out: list[dict] = []
        for line in source.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line.startswith("["):
                continue
            try:
                ts_part, rest = line.split("]", 1)
                ts = ts_part.strip("[]").strip()
                hh, mm, ss = ts.split(":")
                ti = int(hh) * 3600 + int(mm) * 60 + int(ss)
                if not (t_start <= ti <= t_end):
                    continue
                user_part, msg = rest.split(":", 1)
                out.append({"t": float(ti), "user": user_part.strip(), "text": msg.strip()})
            except Exception:
                continue
        return out
    except OSError:
        return []


def render_overlay_mp4(messages: list[dict], out_path: Path,
                       width: int = 360, height: int = 1280,
                       fps: int = 30, duration: float = 30.0) -> Path | None:
    """Render `messages` into a vertical scrolling MP4 strip.

    No-op if Pillow is unavailable. Caller should treat None as "skip
    overlay layer for this clip."
    """
    if not _PIL_OK:
        return None
    if not messages:
        return None

    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Render a single PNG that holds every message stacked top-to-bottom,
    # then animate vertical scroll via FFmpeg in the consumer (not here).
    # That's a separate pass; for now we just emit the static image.
    img_w, line_h = width, 60
    height_px = max(line_h * len(messages), height)
    img = Image.new("RGBA", (img_w, height_px), (0, 0, 0, 178))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 22)
        font_b = ImageFont.truetype("DejaVuSans-Bold.ttf", 22)
    except Exception:
        font = ImageFont.load_default()
        font_b = font

    y = 8
    for m in messages:
        user = m["user"][:18]
        text = m["text"][:80]
        draw.text((12, y), f"{user}:", fill=(180, 220, 255, 255), font=font_b)
        # Compute name width via bbox to place message after it.
        try:
            bbox = draw.textbbox((12, y), f"{user}:", font=font_b)
            x_off = bbox[2] + 8
        except AttributeError:  # Pillow <8.0
            x_off = 12 + 22 * (len(user) + 1)
        draw.text((x_off, y), text, fill=(255, 255, 255, 255), font=font)
        y += line_h
        if y > height_px - line_h:
            break

    png_path = out_path.with_suffix(".png")
    img.save(png_path)
    return png_path  # caller wraps with FFmpeg overlay scroll


def build_overlay_for_clip(vod_path: str | None, temp_dir: str | None,
                           t_start: float, t_end: float, out_dir: Path,
                           clip_id: str) -> Path | None:
    """High-level: find chat source → load window → render PNG. Returns
    the PNG path the renderer will scroll, or None when unavailable."""
    src = _find_chat_source(vod_path, temp_dir)
    if not src:
        return None
    msgs = load_chat_window(src, t_start, t_end)
    if not msgs:
        return None
    out = out_dir / f"chat_overlay_{clip_id}.png"
    return render_overlay_mp4(msgs, out)


def _cli() -> int:
    import argparse, sys
    ap = argparse.ArgumentParser()
    ap.add_argument("--vod", default=None)
    ap.add_argument("--temp", default="/tmp/clipper")
    ap.add_argument("--start", type=float, required=True)
    ap.add_argument("--end", type=float, required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--clip-id", required=True)
    args = ap.parse_args()
    p = build_overlay_for_clip(args.vod, args.temp,
                               args.start, args.end,
                               Path(args.out_dir), args.clip_id)
    if p:
        print(p)
        return 0
    return 1


if __name__ == "__main__":
    import sys
    sys.exit(_cli())
