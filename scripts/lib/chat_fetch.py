#!/usr/bin/env python3
"""VOD chat fetcher — Phase 2.2 of the 2026 upgrade.

Two input paths, one canonical output:

1. **Anonymous Twitch GraphQL** (``fetch_twitch_gql``). Uses the
   unofficial ``/comments`` persisted-query endpoint that community tools
   like ``lay295/TwitchDownloader`` rely on. No auth required. Writes
   ``vods/.chat/{basename}.jsonl``. **Rate-limited and documented:** the
   endpoint is not an official Twitch API surface and can change without
   notice. Use at your own risk; users preferring a supported path should
   run TwitchDownloaderCLI externally and import the result.

2. **TwitchDownloader JSON import** (``import_twitchdownloader_json``).
   For users with ToS concerns or who want richer event metadata (real
   sub/resub/subgift events): run TwitchDownloaderCLI normally, then
   ``python3 chat_fetch.py --import chat.json --out vods/.chat/...jsonl``.

Canonical JSONL output (one message per line):

    {"t": 12.4, "user": "xqc", "text": "KEKW insane",
     "emotes": ["KEKW"], "badges": ["subscriber/12"],
     "bits": 0, "type": "chat"}

    {"t": 45.0, "user": "x", "text": "X gifted 5 subs!",
     "emotes": [], "badges": [], "bits": 0,
     "type": "sub", "count": 5, "tier": "1000"}

Consumed by ``chat_features.py`` and the Stage 1 auto-discovery hook in
``clip-pipeline.sh``.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, Iterable, List, Optional

DEFAULT_GQL_URL = "https://gql.twitch.tv/gql"
DEFAULT_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"   # Twitch's public web client ID
VIDEO_COMMENTS_HASH = (
    "b70a3591ff0f4e0313d126c6a1502d79a1c02baebb288227c582044aa76adf6a"
)
USER_AGENT = "OpenClawStreamClipper/1.0 chat_fetch (+openclaw)"

# Regex helpers for best-effort event extraction from chat messages.
CHEER_RE = re.compile(r"\bcheer(\d+)\b", re.IGNORECASE)
GIFT_RE = re.compile(r"\bgift(ed)?\s+(\d+)\s+sub", re.IGNORECASE)
SUB_KEYWORDS = re.compile(r"\b(sub|subbed|subscribed|resub)\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Anonymous Twitch GraphQL fetch
# ---------------------------------------------------------------------------


def _build_gql_payload(video_id: str, cursor: Optional[str] = None, offset: int = 0) -> bytes:
    variables: Dict = {"videoID": str(video_id)}
    if cursor:
        variables["cursor"] = cursor
    else:
        variables["contentOffsetSeconds"] = int(offset)

    body = [
        {
            "operationName": "VideoCommentsByOffsetOrCursor",
            "variables": variables,
            "extensions": {
                "persistedQuery": {
                    "version": 1,
                    "sha256Hash": VIDEO_COMMENTS_HASH,
                }
            },
        }
    ]
    return json.dumps(body).encode("utf-8")


def _gql_post(
    payload: bytes, client_id: str, url: str, timeout: float
) -> Optional[dict]:
    """POST a GraphQL payload. Returns the first response dict or None on failure."""
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Client-ID": client_id,
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        print(f"[CHAT] GQL HTTP error: {e}", file=sys.stderr)
        return None
    except (json.JSONDecodeError, TimeoutError) as e:
        print(f"[CHAT] GQL parse error: {e}", file=sys.stderr)
        return None
    if isinstance(data, list) and data:
        return data[0]
    return None


def _normalize_comment_node(node: dict) -> Optional[dict]:
    """Turn a single GraphQL comment edge.node into our canonical record.

    Returns None for nodes we can't make sense of.
    """
    t = node.get("contentOffsetSeconds")
    if not isinstance(t, (int, float)):
        return None
    commenter = node.get("commenter") or {}
    user = (
        commenter.get("displayName")
        or commenter.get("login")
        or commenter.get("name")
        or ""
    )
    message = node.get("message") or {}
    fragments = message.get("fragments") or []

    text_parts: List[str] = []
    emotes: List[str] = []
    for frag in fragments:
        frag_text = frag.get("text") or ""
        text_parts.append(frag_text)
        emote = frag.get("emote")
        if emote and frag_text:
            emotes.append(frag_text.strip())
    text = "".join(text_parts).strip()

    badges_raw = message.get("userBadges") or []
    badges = []
    for b in badges_raw:
        bid = b.get("setID") or b.get("id") or ""
        ver = b.get("version") or ""
        if bid:
            badges.append(f"{bid}/{ver}" if ver else bid)

    bits = 0
    for m in CHEER_RE.finditer(text):
        try:
            bits += int(m.group(1))
        except ValueError:
            continue

    entry = {
        "t": float(t),
        "user": user,
        "text": text,
        "emotes": emotes,
        "badges": badges,
        "bits": bits,
        "type": "chat",
    }

    # Best-effort event extraction. Twitch's GraphQL VOD endpoint doesn't
    # include dedicated system messages, but we can still detect some
    # events from message text / badges. Higher-fidelity event data comes
    # from TwitchDownloader (see import_twitchdownloader_json).
    gift = GIFT_RE.search(text)
    if gift:
        try:
            entry_count = int(gift.group(2))
        except ValueError:
            entry_count = 1
        # Emit TWO records: the chat message and a synthesized sub event.
        return [
            entry,
            {
                "t": float(t),
                "user": user,
                "text": text,
                "emotes": [],
                "badges": badges,
                "bits": 0,
                "type": "sub",
                "count": entry_count,
                "synthetic": True,
            },
        ]  # type: ignore[return-value]
    if bits > 0:
        return [
            entry,
            {
                "t": float(t),
                "user": user,
                "text": text,
                "emotes": [],
                "badges": badges,
                "bits": bits,
                "type": "bit",
                "count": bits,
                "synthetic": True,
            },
        ]  # type: ignore[return-value]
    return entry


def fetch_twitch_gql(
    video_id: str,
    out_path: str,
    client_id: str = DEFAULT_CLIENT_ID,
    url: str = DEFAULT_GQL_URL,
    request_delay_ms: int = 200,
    max_retries: int = 3,
    max_pages: int = 100000,
    request_timeout: float = 30.0,
) -> int:
    """Download a VOD's full chat via Twitch GraphQL, one page at a time.

    Returns the number of messages written (including synthesized sub/bit
    event records). Raises no exceptions — network failures log and leave
    whatever was already written in place.
    """
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    cursor: Optional[str] = None
    page = 0
    seen_cursors: set = set()

    with out.open("w", encoding="utf-8") as f:
        while page < max_pages:
            page += 1
            payload = _build_gql_payload(video_id, cursor=cursor, offset=0)
            resp = None
            for attempt in range(max_retries):
                resp = _gql_post(payload, client_id, url, request_timeout)
                if resp is not None:
                    break
                backoff = (attempt + 1) * 1.5
                print(
                    f"[CHAT] retry {attempt+1}/{max_retries} in {backoff:.1f}s (page {page})",
                    file=sys.stderr,
                )
                time.sleep(backoff)
            if resp is None:
                print(f"[CHAT] giving up at page {page}", file=sys.stderr)
                break

            data = (resp.get("data") or {}).get("video") or {}
            comments = data.get("comments") or {}
            edges = comments.get("edges") or []
            if not edges:
                break

            for edge in edges:
                node = edge.get("node") or {}
                rec = _normalize_comment_node(node)
                if rec is None:
                    continue
                records = rec if isinstance(rec, list) else [rec]
                for r in records:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
                    written += 1

            page_info = comments.get("pageInfo") or {}
            if not page_info.get("hasNextPage"):
                break
            last_cursor = edges[-1].get("cursor")
            if not last_cursor or last_cursor in seen_cursors:
                # Safety: Twitch occasionally echoes the same cursor —
                # treat that as end-of-stream rather than infinite-looping.
                break
            seen_cursors.add(last_cursor)
            cursor = last_cursor

            if request_delay_ms:
                time.sleep(request_delay_ms / 1000.0)

    print(
        f"[CHAT] fetched {written} messages across {page} page(s) → {out}",
        file=sys.stderr,
    )
    return written


# ---------------------------------------------------------------------------
# TwitchDownloader JSON import
# ---------------------------------------------------------------------------


def import_twitchdownloader_json(json_path: str, out_path: str) -> int:
    """Convert a TwitchDownloaderCLI --mode ChatDownload JSON file into
    our canonical JSONL.

    TwitchDownloader's format: ``{"comments": [{"content_offset_seconds":
    ..., "commenter": {...}, "message": {...}}]}``. Events (subs, resubs,
    subgifts, cheers) are represented either as chat messages with
    identifying badges or as separate ``"message_type"`` fields — we
    extract whichever we find.
    """
    p = Path(json_path)
    if not p.exists():
        print(f"[CHAT] import source missing: {p}", file=sys.stderr)
        return 0
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"[CHAT] import parse error: {e}", file=sys.stderr)
        return 0

    comments = data.get("comments") or []
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    written = 0

    with out.open("w", encoding="utf-8") as f:
        for c in comments:
            t = c.get("content_offset_seconds")
            if not isinstance(t, (int, float)):
                continue
            commenter = c.get("commenter") or {}
            user = (
                commenter.get("display_name")
                or commenter.get("name")
                or commenter.get("login")
                or ""
            )
            message = c.get("message") or {}
            text = (message.get("body") or "").strip()

            emotes: List[str] = []
            for em in message.get("emoticons") or ():
                # TD stores {"_id": ..., "begin": N, "end": M}; slice the text
                try:
                    begin = int(em.get("begin", -1))
                    end = int(em.get("end", -2))
                    if 0 <= begin <= end < len(text):
                        emotes.append(text[begin : end + 1])
                except (TypeError, ValueError):
                    continue
            badges = []
            for b in message.get("user_badges") or ():
                bid = b.get("_id") or b.get("id") or ""
                ver = b.get("version") or ""
                if bid:
                    badges.append(f"{bid}/{ver}" if ver else bid)

            bits_cheered = int(message.get("bits_spent") or 0)
            for m in CHEER_RE.finditer(text):
                try:
                    bits_cheered += int(m.group(1))
                except ValueError:
                    continue

            record = {
                "t": float(t),
                "user": user,
                "text": text,
                "emotes": emotes,
                "badges": badges,
                "bits": bits_cheered,
                "type": "chat",
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            written += 1

            # Synthesize event records from explicit message types / badges.
            msg_type = (c.get("message_type") or "").lower()
            gift_match = GIFT_RE.search(text)
            if msg_type in ("subscription", "resub", "sub") or gift_match or "subscriber/0" in badges:
                count = 1
                if gift_match:
                    try:
                        count = int(gift_match.group(2))
                    except ValueError:
                        count = 1
                f.write(
                    json.dumps(
                        {
                            "t": float(t),
                            "user": user,
                            "text": text,
                            "emotes": [],
                            "badges": badges,
                            "bits": 0,
                            "type": "sub",
                            "count": count,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                written += 1
            if bits_cheered > 0:
                f.write(
                    json.dumps(
                        {
                            "t": float(t),
                            "user": user,
                            "text": text,
                            "emotes": [],
                            "badges": badges,
                            "bits": bits_cheered,
                            "type": "bit",
                            "count": bits_cheered,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                written += 1
            if msg_type == "raid":
                f.write(
                    json.dumps(
                        {
                            "t": float(t),
                            "user": user,
                            "text": text,
                            "emotes": [],
                            "badges": badges,
                            "bits": 0,
                            "type": "raid",
                            "count": 1,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                written += 1

    print(
        f"[CHAT] imported {written} records (incl. events) from {p.name} → {out}",
        file=sys.stderr,
    )
    return written


# ---------------------------------------------------------------------------
# VOD filename → Twitch video ID
# ---------------------------------------------------------------------------


def extract_vod_id(filename: str, pattern: str) -> Optional[str]:
    """Try to pull a Twitch video ID out of a filename using `pattern`.

    `pattern` is a Python regex whose first non-None capture group is
    assumed to be the numeric video ID. Returns None if no match.
    """
    try:
        rx = re.compile(pattern)
    except re.error:
        return None
    m = rx.search(filename)
    if not m:
        return None
    for g in m.groups():
        if g and g.isdigit():
            return g
    return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli() -> None:
    ap = argparse.ArgumentParser(description="VOD chat fetcher (Phase 2.2)")
    sub = ap.add_subparsers(dest="mode", required=True)

    ap_fetch = sub.add_parser("fetch", help="Anonymous Twitch GraphQL fetch")
    ap_fetch.add_argument("--vod-id", required=True, help="Twitch video ID")
    ap_fetch.add_argument("--out", required=True, help="output JSONL path")
    ap_fetch.add_argument("--client-id", default=DEFAULT_CLIENT_ID)
    ap_fetch.add_argument("--url", default=DEFAULT_GQL_URL)
    ap_fetch.add_argument("--delay-ms", type=int, default=200)

    ap_import = sub.add_parser("import", help="Import TwitchDownloader JSON")
    ap_import.add_argument("--source", required=True, help="TwitchDownloader JSON")
    ap_import.add_argument("--out", required=True, help="output JSONL path")

    ap_extract = sub.add_parser(
        "extract-id", help="Extract a VOD ID from a filename using a regex"
    )
    ap_extract.add_argument("--filename", required=True)
    ap_extract.add_argument(
        "--pattern", default=r"(?:twitch[-_]|_v|_video_|id[-_])(\d{9,})"
    )

    args = ap.parse_args()
    if args.mode == "fetch":
        n = fetch_twitch_gql(
            args.vod_id,
            args.out,
            client_id=args.client_id,
            url=args.url,
            request_delay_ms=args.delay_ms,
        )
        sys.exit(0 if n > 0 else 1)
    elif args.mode == "import":
        n = import_twitchdownloader_json(args.source, args.out)
        sys.exit(0 if n > 0 else 1)
    elif args.mode == "extract-id":
        vid = extract_vod_id(args.filename, args.pattern)
        if vid:
            print(vid)
            sys.exit(0)
        print("(no match)", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    _cli()
