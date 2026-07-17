---
title: "Buffer Clip Poster (poster/ app)"
type: entity
tags: [poster, buffer, publishing, tiktok, instagram, dashboard-sibling]
sources: 0
updated: 2026-07-16
---

# Buffer Clip Poster

A **separate** Flask app (`poster/`) that batch-publishes finished clips from the
clips folder to **TikTok + Instagram Reels** through the owner's Buffer.io
account via Buffer's GraphQL API. Owner directive (2026-07-16): *"similar but
separate from the dashboard — I don't want to fully integrate it but it can
still interact with everything the dashboard interacts with; start it up like
the dashboard but on a different port."*

- **Start**: `start-poster.cmd` (or `.venv\Scripts\python.exe poster\app.py`)
- **Port**: default **5100** (`POSTER_PORT` pins; same roll-forward logic as the
  dashboard — 5100 sits outside the dashboard's 5001–5013 roll range)
- **Shares the world, not the code**: reads `config/paths.json` for the clips
  folder like [[entities/dashboard]] does, but imports nothing from `dashboard/`

## What one "Post" click does

For each selected clip: stability check (size must hold still — don't ship a
file Stage 7 is mid-render on) → upload to Cloudinary → **one `createPost`
per checked channel** (each clip = its own unique post on each network) →
entry in the posted ledger. 2 s throttle between clips; one 429 retry
honoring `Retry-After` (capped 15 min); per-clip failures never kill the batch.
One batch at a time; cancel stops after the current clip. Job status is polled
by the UI (`/api/job`) with per-card chips (uploading… / posting… / ✓ / ✗).

**Caption** = the clip's auto-generated title (the filename) minus the trailing
`" (B)"` / `" (Short)"` variant marker (owner spec) — editable per card before
posting. Same caption goes to both networks.

## Buffer API facts (verified live 2026-07-16)

- GraphQL at `https://api.buffer.com`, `Authorization: Bearer <key>`; the key
  lives in **`BufferIOapiKey.txt`** (repo root, **gitignored**, never logged).
- Enums confirmed by live introspection: `schedulingType: automatic|notification`;
  `mode: addToQueue | shareNow | shareNext | customScheduled` (UI exposes the
  first three; default **addToQueue** = paced by the owner's Buffer schedule).
- Instagram video posts require `metadata.instagram = {type: reel,
  shouldShareToFeed: true}` → publishes as a **Reel**. TikTok video posts need
  no metadata block (caption rides in `text`).
- Owner's channels: `tiktok/enderclips4k` + `instagram/ender40clips`
  (both unpaused; channel list cached 5 min, `?refresh=1` forces).
- **Rate limits (free plan)**: 100 req/15 min, **250 req/24 h**, 3 000/30 d —
  each clip costs ~2 requests, so ~100 clips/day is the practical ceiling.

## Media hosting (the one non-obvious constraint)

> [!warning] Buffer has **no upload endpoint** — media must sit at a public,
> direct, **stable** HTTPS URL until the post publishes (their docs explicitly
> warn against expiring/pre-signed links, which fail *silently* on queued posts).

Chosen host: **Cloudinary** (Buffer's own recommendation; free tier is plenty
for short-form clips). Signed REST upload, no SDK (`poster/media_host.py`);
credentials entered once in the Setup panel → stored in
**`config/buffer_poster.json`** (gitignored). Uploaded assets are **not**
auto-deleted — `addToQueue` posts fetch the media at publish time, possibly
hours later. Free-plan cap: 100 MB per video (guarded at 98 MB with a clear
per-clip error). **Owner one-time setup**: create the free Cloudinary account
and paste cloud name / API key / API secret into the Setup panel.

## Module map

| File | Role |
|---|---|
| `poster/app.py` | entrypoint (port resolve, no-cache index shell) |
| `poster/_state.py` | paths (paths.json), key/config loading, job globals |
| `poster/buffer_client.py` | GraphQL client: channels, `create_video_post` |
| `poster/media_host.py` | Cloudinary signed upload + credential verify |
| `poster/worker.py` | bounded batch thread (statuses, throttle, ledger) |
| `poster/routes.py` | `/api/{status,clips,channels,hosting,post,job}` |
| `poster/templates` + `static/` | UI (dashboard theme copied; `poster.css/js`) |

## Posted ledger

`clips/.posted.buffer.json` — filename → {posted_at, caption, posts
(service/post_id/due_at), cloudinary_public_id, times, partial?}. Drives the
green "posted" badges and the **Select unposted** button; partial successes
(e.g. TikTok ok, IG failed) are recorded and flagged.

> [!note] Lazy previews (learned the hard way)
> 88 `<video preload="metadata">` elements froze the tab for 30 s+ on first
> render. Cards now use `preload="none"` + an IntersectionObserver that flips
> to metadata only when scrolled into view — initial load is instant at any
> library size. Same pattern is worth porting to the dashboard clips gallery
> if its library grows.

## Status

Shipped + live-verified 2026-07-16 (channels/clips/captions/selection tested
read-only against the real account; **no post was created by the agent** — the
first real publish is the owner's click). Verified safe beside a running
pipeline: read-only toward `clips/`, plus the render-stability guard.
