#!/usr/bin/env python3
"""Bootstrap a Twitch-clip-worthiness labeled dataset — Phase 5.3.

Standalone research tool. NOT wired into the pipeline — this builds an
offline eval / training dataset from Twitch's public clip catalog.

Per ClippingResearch.md §8.6, a Twitch clip is itself a user-labeled
positive example with span boundaries (`vod_offset`, `duration`). 50
streamers x ~100 top clips each = ~50k labeled triples — strictly better
than any published academic clip-worthiness benchmark for the streaming
domain, and free to produce.

Three subcommands:

    fetch-clips  — download top clips metadata per broadcaster
                   (Helix API if credentials are set; otherwise GraphQL
                    persisted-query endpoint that TwitchDownloader uses)
    pair         — convert fetched clips.jsonl into {positive, negative}
                   span triples suitable for eval-harness loading
    summary      — print broadcaster / clip-count / span-duration stats

Typical workflow:

    # 1. Fetch top-100 clips for a list of streamers
    python3 scripts/research/bootstrap_twitch_clips.py fetch-clips \\
        --broadcasters lacy,xqc,pokimane --limit 100 \\
        --out dataset/clips.jsonl

    # 2. Pair with sampled negatives from the same VODs
    python3 scripts/research/bootstrap_twitch_clips.py pair \\
        --clips dataset/clips.jsonl \\
        --negatives-per-positive 3 --min-gap-sec 300 \\
        --out dataset/triples.jsonl

    # 3. Inspect
    python3 scripts/research/bootstrap_twitch_clips.py summary \\
        --clips dataset/clips.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional

DEFAULT_HELIX_URL = "https://api.twitch.tv/helix"
DEFAULT_GQL_URL = "https://gql.twitch.tv/gql"
PUBLIC_WEB_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"
USER_AGENT = "OpenClawStreamClipper/1.0 bootstrap_twitch_clips"


def _helix_get(path, params, client_id, oauth_token, timeout=30.0):
    url = f"{DEFAULT_HELIX_URL}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(
        url,
        headers={
            "Client-ID": client_id,
            "Authorization": f"Bearer {oauth_token}",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, TimeoutError) as e:
        print(f"[BOOT] Helix GET {path} failed: {e}", file=sys.stderr)
        return None


def helix_resolve_user(login, client_id, oauth_token):
    resp = _helix_get("/users", {"login": login}, client_id, oauth_token)
    if not resp:
        return None
    data = resp.get("data") or []
    return data[0] if data else None


def helix_get_top_clips(user_id, client_id, oauth_token, limit=100):
    out = []
    cursor = None
    while len(out) < limit:
        params = {"broadcaster_id": user_id, "first": min(100, limit - len(out))}
        if cursor:
            params["after"] = cursor
        resp = _helix_get("/clips", params, client_id, oauth_token)
        if not resp:
            break
        data = resp.get("data") or []
        if not data:
            break
        out.extend(data)
        pag = (resp.get("pagination") or {}).get("cursor")
        if not pag or pag == cursor:
            break
        cursor = pag
    return out[:limit]


def gql_top_clips(login, client_id, limit=100):
    """Fetch top clips via the unofficial GraphQL endpoint. No auth."""
    clips = []
    cursor = None
    while len(clips) < limit:
        body = [
            {
                "operationName": "ClipsCards__User",
                "variables": {
                    "login": login,
                    "limit": min(100, limit - len(clips)),
                    "criteria": {"filter": "ALL_TIME", "shouldFilterByGame": False},
                    "cursor": cursor,
                },
                "extensions": {
                    "persistedQuery": {
                        "version": 1,
                        "sha256Hash": "b73ad2bfaecfd30a9e6c28fada15bd97032c83ec77a0440766a56fe0bd632777",
                    }
                },
            }
        ]
        req = urllib.request.Request(
            DEFAULT_GQL_URL,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Client-ID": client_id,
                "User-Agent": USER_AGENT,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30.0) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, TimeoutError) as e:
            print(f"[BOOT] GQL clips failed for {login}: {e}", file=sys.stderr)
            break
        first = raw[0] if isinstance(raw, list) and raw else raw
        edges = (
            ((first.get("data") or {}).get("user") or {})
            .get("clips", {})
            .get("edges", [])
        )
        if not edges:
            break
        for edge in edges:
            node = edge.get("node") or {}
            clips.append(
                {
                    "id": node.get("slug") or node.get("id"),
                    "title": node.get("title"),
                    "view_count": node.get("viewCount"),
                    "duration": node.get("durationSeconds"),
                    "vod_offset": node.get("videoOffsetSeconds"),
                    "video_id": (node.get("video") or {}).get("id"),
                    "game_id": (node.get("game") or {}).get("id"),
                    "game_name": (node.get("game") or {}).get("name"),
                    "broadcaster": login,
                    "url": node.get("url"),
                    "created_at": node.get("createdAt"),
                }
            )
        next_cursor = edges[-1].get("cursor")
        if not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor
    return clips[:limit]


def cmd_fetch_clips(args):
    broadcasters = [b.strip() for b in (args.broadcasters or "").split(",") if b.strip()]
    if not broadcasters:
        print("error: --broadcasters is required (comma-separated login names)", file=sys.stderr)
        return 2

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    helix_id = os.environ.get("TWITCH_CLIENT_ID")
    helix_token = os.environ.get("TWITCH_OAUTH_TOKEN")
    use_helix = bool(helix_id and helix_token)

    if use_helix:
        print(f"[BOOT] Using Helix API with client_id={helix_id[:6]}...", file=sys.stderr)
    else:
        print(
            "[BOOT] No TWITCH_CLIENT_ID/TWITCH_OAUTH_TOKEN env; falling back to "
            "unofficial GraphQL endpoint (public web client_id)",
            file=sys.stderr,
        )

    written = 0
    with out_path.open("w", encoding="utf-8") as f:
        for login in broadcasters:
            if use_helix:
                user = helix_resolve_user(login, helix_id, helix_token)
                if not user:
                    print(f"[BOOT] Helix couldn't resolve '{login}'; skipping", file=sys.stderr)
                    continue
                clips = helix_get_top_clips(user["id"], helix_id, helix_token, args.limit)
                for c in clips:
                    rec = {
                        "id": c.get("id"),
                        "title": c.get("title"),
                        "view_count": c.get("view_count"),
                        "duration": c.get("duration"),
                        "vod_offset": c.get("vod_offset"),
                        "video_id": c.get("video_id"),
                        "game_id": c.get("game_id"),
                        "game_name": None,
                        "broadcaster": login,
                        "url": c.get("url"),
                        "created_at": c.get("created_at"),
                    }
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    written += 1
            else:
                clips = gql_top_clips(login, PUBLIC_WEB_CLIENT_ID, args.limit)
                for rec in clips:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    written += 1
            print(f"[BOOT] {login}: {len(clips)} clip(s)", file=sys.stderr)
            time.sleep(args.delay_sec)

    print(f"[BOOT] wrote {written} clip records to {out_path}", file=sys.stderr)
    return 0 if written else 1


def cmd_pair(args):
    clips_path = Path(args.clips)
    if not clips_path.exists():
        print(f"error: {clips_path} not found", file=sys.stderr)
        return 2

    by_video = {}
    all_records = []
    for line in clips_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        vid = rec.get("video_id")
        if not vid:
            continue
        by_video.setdefault(vid, []).append(rec)
        all_records.append(rec)

    print(f"[BOOT] {len(all_records)} clips across {len(by_video)} VOD(s)", file=sys.stderr)

    rng = random.Random(args.seed)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    min_gap = float(args.min_gap_sec)
    pos_margin = float(args.positive_margin_sec)
    written = 0

    with out_path.open("w", encoding="utf-8") as f:
        for vid, vod_clips in by_video.items():
            pos_spans = []
            for c in vod_clips:
                start = max(0.0, float(c.get("vod_offset") or 0.0) - pos_margin)
                end = float(c.get("vod_offset") or 0.0) + float(c.get("duration") or 30.0) + pos_margin
                pos_spans.append((start, end, c))

            vod_dur = max((e for _s, e, _c in pos_spans), default=0.0)
            if vod_dur <= 0:
                continue

            for (ps, pe, pc) in pos_spans:
                positive = {
                    "video_id": vid,
                    "broadcaster": pc.get("broadcaster"),
                    "label": "positive",
                    "start": round(ps, 2),
                    "end": round(pe, 2),
                    "duration": round(pe - ps, 2),
                    "clip_id": pc.get("id"),
                    "clip_title": pc.get("title"),
                    "clip_view_count": pc.get("view_count"),
                    "clip_url": pc.get("url"),
                }
                f.write(json.dumps(positive, ensure_ascii=False) + "\n")
                written += 1

                negs = 0
                attempts = 0
                while negs < args.negatives_per_positive and attempts < 50:
                    attempts += 1
                    nstart = rng.uniform(0, max(1.0, vod_dur - 30.0))
                    nend = nstart + rng.uniform(15.0, 45.0)
                    overlap = any(
                        (nstart < pse + min_gap) and (nend > psp - min_gap)
                        for psp, pse, _c in pos_spans
                    )
                    if overlap:
                        continue
                    negative = {
                        "video_id": vid,
                        "broadcaster": pc.get("broadcaster"),
                        "label": "negative",
                        "start": round(nstart, 2),
                        "end": round(nend, 2),
                        "duration": round(nend - nstart, 2),
                        "paired_with_clip_id": pc.get("id"),
                    }
                    f.write(json.dumps(negative, ensure_ascii=False) + "\n")
                    negs += 1
                    written += 1

    print(f"[BOOT] wrote {written} triples to {out_path}", file=sys.stderr)
    return 0 if written else 1


def cmd_summary(args):
    path = Path(args.clips)
    if not path.exists():
        print(f"error: {path} not found", file=sys.stderr)
        return 2
    by_bc = {}
    durations = []
    views = []
    n = 0
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            n += 1
            bc = rec.get("broadcaster") or "?"
            by_bc[bc] = by_bc.get(bc, 0) + 1
            if rec.get("duration") is not None:
                durations.append(float(rec["duration"]))
            if rec.get("view_count") is not None:
                views.append(int(rec["view_count"]))
    summary = {
        "total_clips": n,
        "broadcasters": len(by_bc),
        "per_broadcaster": dict(sorted(by_bc.items(), key=lambda kv: -kv[1])[:20]),
        "duration_median_sec": round(sorted(durations)[len(durations) // 2], 2) if durations else None,
        "duration_max_sec": round(max(durations), 2) if durations else None,
        "views_median": sorted(views)[len(views) // 2] if views else None,
        "views_max": max(views) if views else None,
    }
    json.dump(summary, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Twitch clip dataset bootstrap (Phase 5.3)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    ap_fetch = sub.add_parser("fetch-clips", help="Fetch top clips per broadcaster")
    ap_fetch.add_argument("--broadcasters", required=True, help="comma-separated logins")
    ap_fetch.add_argument("--limit", type=int, default=100)
    ap_fetch.add_argument("--out", required=True)
    ap_fetch.add_argument("--delay-sec", type=float, default=0.5)

    ap_pair = sub.add_parser("pair", help="Pair positives with sampled negatives")
    ap_pair.add_argument("--clips", required=True, help="fetch-clips output JSONL")
    ap_pair.add_argument("--out", required=True)
    ap_pair.add_argument("--negatives-per-positive", type=int, default=3)
    ap_pair.add_argument("--min-gap-sec", type=float, default=300.0)
    ap_pair.add_argument("--positive-margin-sec", type=float, default=60.0)
    ap_pair.add_argument("--seed", type=int, default=42)

    ap_sum = sub.add_parser("summary", help="Print stats for a clips.jsonl")
    ap_sum.add_argument("--clips", required=True)

    args = ap.parse_args()
    if args.cmd == "fetch-clips":
        return cmd_fetch_clips(args)
    if args.cmd == "pair":
        return cmd_pair(args)
    if args.cmd == "summary":
        return cmd_summary(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
