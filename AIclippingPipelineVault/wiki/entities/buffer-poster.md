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
by the UI (`/api/job`) with per-card chips (uploading… / posting… / per-network
`tt✓ ig…` marks).

**Caption** = the clip's auto-generated title (the filename) minus the trailing
`" (B)"` / `" (Short)"` variant marker (owner spec) — editable per card before
posting. Same caption goes to both networks. **Hashtags** (2026-07-16, owner
req): one input applied to every post — normalized (`fyp, streamer` →
`#fyp #streamer`, deduped, `#` auto-prefixed) and appended to each caption on
its own line at post time; persisted in localStorage between batches.

## Verification + Retry (createPost success ≠ published)

> [!warning] Buffer publishing is ASYNC. `createPost` success only means Buffer
> *accepted* the post — actual publishing happens 1–3 min later and can fail
> server-side. Proven on the owner's first real batch (2026-07-16): 4/4 posts
> accepted, but one TikTok post later hit Buffer's transient "An unknown error
> has occurred. Please retry" while the UI said "✓ posted". (The other
> "missing" post was just still publishing — IG took ~3 min.)

So after posting, the batch enters a **verifying** phase: one `posts` list
query per 20 s round (newest-first, one call regardless of batch size; ≤10
rounds ≈ 3.5 min) until every post is terminal (`sent`/`error`); statuses land
in the job items, the UI chips (`tt✓ ig✗`), and the ledger. Clips with a
failed network get a red **posted ⚠** badge, and a **Retry failed (N)** button
re-posts *only the errored clip+channel pairs*, reusing the hosted Cloudinary
URL (no re-upload) and merging results back without clobbering the sibling
network's record.

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
- **Per-channel DAILY posting caps** (query: `dailyPostingLimits(input:
  {channelIds})` → `{sent, scheduled, limit, isAtLimit}`): **TikTok 25/day,
  Instagram 50/day** on this account.

> [!warning] TikTok bulk-posting lockout (learned 2026-07-16, the 112-clip batch)
> TikTok's OWN anti-spam trips **far below** Buffer's 25/day cap when posts land
> in rapid succession: a `shareNow` batch firing ~25 s apart got **6 posts
> through, then 100 straight failures** with *"TikTok has detected a large
> number of posts published through the API for this channel. Wait 24 hours
> before trying to post again."* The lockout is ~24 h and channel-wide.
> Practical law: **never `shareNow` more than a handful to TikTok** — use
> `addToQueue` against a Buffer posting schedule (a few slots/day) and keep
> TikTok volume ≈ 10-15/day spaced out. Instagram tolerated the same pattern
> fine (50/day cap). After a lockout, DON'T mash Retry failed — it re-fires
> everything immediately and re-trips the detector.

## Rate guard (shipped 2026-07-16, same night)

The worker now enforces all of the above mechanically (`worker._ChannelGate`;
per-channel, per-batch):

- **Live quota strip** in the UI + `/api/limits` (60 s cache): rolling-24h
  room per channel from `dailyPostingLimits`.
- **Pre-flight cap gate**: immediate-mode posts beyond a channel's remaining
  quota become `skipped_cap` records (no API call, red ⏸, picked up by Retry
  failed later); if EVERY channel is at cap the batch is refused up front
  with the numbers.
- **TikTok burst guard**: immediate modes allow 3 rapid posts per channel per
  batch (10 for other networks); overflow is auto-converted to
  `customScheduled` posts spaced 90 min — Buffer publishes them on time, no
  local pacing thread. Overrides: `config/buffer_poster.json` →
  `rate_guard: {tiktok_burst, default_burst, auto_spacing_min}`.
- **Drip mode** (new UI default): every post scheduled at a chosen spacing
  (30 m–3 h; 90 min preselected = 16/day, under TikTok's 25 cap). Safe way to
  push 100+ clips in one click — they publish over days.
- **Creation throttle** stretches to 12 s/post on >80-post batches so post
  *creation* stays under Buffer's 100-req/15-min window.
- **Refresh statuses** button (`/api/verify-ledger`): one-pass re-check of all
  non-terminal ledger posts (scheduled ones aren't polled by the batch
  verifier). Retry failed now covers `error` + `skipped_cap`.

Chip legend: `tt✓` sent · `tt✗` failed · `tt⏱` scheduled · `tt⏸` cap-skipped.

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
per-clip error). **Owner setup DONE 2026-07-16**: free account created, creds
saved via `/api/hosting`, and the production upload path verified end-to-end
(real clip up → public URL 200 → destroyed). Poster is fully armed.

## Module map

| File | Role |
|---|---|
| `poster/app.py` | entrypoint (port resolve, no-cache index shell) |
| `poster/_state.py` | paths (paths.json), key/config loading, job globals |
| `poster/buffer_client.py` | GraphQL client: channels, `create_video_post` |
| `poster/media_host.py` | Cloudinary signed upload + credential verify |
| `poster/worker.py` | bounded batch thread + async-publish verification |
| `poster/scores.py` | clip score index (clip_scores.jsonl + trace joins) |
| `poster/routes.py` | `/api/{status,clips,channels,limits,hosting,post,retry,job,verify-ledger}` |
| `poster/templates` + `static/` | UI (dashboard theme copied; `poster.css/js`) |

## Top-rated filter + posted_clips auto-move (v1.3, 2026-07-16)

**★ Top rated** (clips toolbar): keeps the best 20/33/50% **per scoring
tier** — judge scores (0-10, comparable across eras) rank first, composite-
only clips rank in their own tier — sorted best-first; unscored clips hide
with an explicit count, never silently. Card chip: `⚖ 7.5 · ★ 1.62` (judge ·
composite). Scores come from `poster/scores.py`, which joins (newest-first):
the durable `clips/.diagnostics/clip_scores.jsonl` (Stage 7 writes it at
render time; **the retro scorer backfills old clips**) → `last_run_*` traces
via `clips_made` rows (direct filename join) → `enriched_<t>`/`moment_<t>`
sidecars + `hype_moments.data` via the stage7 title sanitize.

**Retro scorer** (`scripts/research/score_clips.py`, 2026-07-17): judges
FINISHED clips without reprocessing VODs — whisper-base (CPU, VRAM-neutral
beside the resident 35B) transcribes each clip's own audio, the 35B scores
postability 0-10 in batches of 8 (S4.5 pattern), rows append to
`clip_scores.jsonl` with rationales. First run: **131/131 scored, 0 failed
(~6 min)** → 134/134 folder coverage; healthy spread (median ~3.5, 15 clips
≥7, top 8.5). Honest limit: it judges the TRANSCRIPT — visual-only clips
(screaming/physical comedy that whisper hears as noise) underrate; treat low
scores on caption-light clips with an eyeball.

> [!warning] Coverage gap on 2026-07-16: traces are per-INVOCATION snapshots
> written at batch end — a batch STOPPED mid-queue writes none (batch A), and
> batch B ALSO ended with no trace (see the reports bug below). Hence 2/134
> scored on ship day — clips rendered before 07-17 stay unscored.
>
> **FIXED FORWARD same night**: `stage7._record_clip` now appends every
> rendered file to `clips/.diagnostics/clip_scores.jsonl` at render time
> (`{clip: stem incl. variant, score, judge, category, run, ts}`; judge rides
> the manifest row from the moment's `s45_judge`). Failure-soft, stop-proof
> (written per render, not at batch end). Writer↔reader round-trip selftests
> PASS. Every clip from the NEXT run onward is scored automatically; stitch
> compilations (stitch_render's own clips_made append) are the one un-indexed
> lane. Still open (queued chip): why the run-end reporting block
> (run_metrics + last_run) stopped firing on 07-16's runs.

**posted_clips auto-move** (owner req): after verification, any clip whose
posts ALL read `sent` (strict — one error/skip/scheduled keeps it in place)
moves `clips/` → `clips/posted_clips/`, so the working folder is the unposted
backlog. Sweep runs at batch-verify end and on every Refresh-statuses click
(a drip clip moves once its last scheduled post publishes). The poster lists
and serves BOTH folders (cards show `📁 posted`); retries of moved clips
reuse the hosted Cloudinary URL. First live sweep moved 8/8 correct clips
(the 2×2 first batch — incl. the healed retry — plus the 6 pre-lockout
TikTok sends); the 106 lockout failures correctly stayed.

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

Shipped + live-verified 2026-07-16. **First real batch same evening (owner's
click): 2 clips × 2 networks → 3/4 sent, 1 transient TikTok error** — which
drove the verification phase + Retry button (see above). Agents never created
a social post; retries are the owner's click too. Verified safe beside a
running pipeline: read-only toward `clips/`, plus the render-stability guard.
