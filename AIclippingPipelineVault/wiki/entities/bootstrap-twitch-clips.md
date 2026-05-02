---
title: "bootstrap_twitch_clips.py — Twitch clip dataset bootstrap"
type: entity
tags: [research, dataset, twitch, eval, phase-5, module, chat]
sources: 1
updated: 2026-04-24
---

# `scripts/research/bootstrap_twitch_clips.py`

Phase 5.3 standalone research tool. NOT wired into the pipeline — this builds an offline eval / training dataset from Twitch's public clip catalog. Lives in `scripts/research/` to keep it separate from runtime code.

Per `ClippingResearch.md` §8.6: a Twitch clip is itself a user-labeled positive example with span boundaries (`vod_offset`, `duration`). 50 streamers × ~100 top clips each = ~50 k labeled triples — strictly better than any published academic clip-worthiness benchmark for the streaming domain, and free to produce.

---

## Subcommands

### `fetch-clips`

Download top-N clips metadata for a list of broadcasters.

```bash
python3 scripts/research/bootstrap_twitch_clips.py fetch-clips \
    --broadcasters lacy,xqc,pokimane --limit 100 \
    --out dataset/clips.jsonl
```

Two auth paths:

- **Helix API** (preferred): set `TWITCH_CLIENT_ID` and `TWITCH_OAUTH_TOKEN` env vars. The tool calls `/helix/users` + `/helix/clips` normally.
- **GraphQL** (fallback, no auth): when the env vars are absent, calls the unofficial `gql.twitch.tv/gql` persisted-query endpoint with the same public web client_id that TwitchDownloader uses. Subject to the same ToS caveat as Phase 2.2's `chat_fetch.py`.

Output is JSONL with one record per clip:

```json
{"id": "SparklyHomelyTildeAllenHuhu", "title": "insane clutch", "view_count": 50234,
 "duration": 30, "vod_offset": 12040, "video_id": "1234567890",
 "game_id": "516575", "game_name": "VALORANT", "broadcaster": "lacy",
 "url": "https://clips.twitch.tv/SparklyHomelyTildeAllenHuhu", "created_at": "2024-10-15T20:41:22Z"}
```

### `pair`

Pair positives (span around each clip's `vod_offset`) with sampled negatives from the same VOD.

```bash
python3 scripts/research/bootstrap_twitch_clips.py pair \
    --clips dataset/clips.jsonl \
    --negatives-per-positive 3 \
    --min-gap-sec 300 \
    --positive-margin-sec 60 \
    --out dataset/triples.jsonl
```

Output is JSONL with alternating positive + sampled-negative records:

```json
{"label": "positive", "video_id": "1234567890", "broadcaster": "lacy",
 "start": 11980.0, "end": 12130.0, "duration": 150.0,
 "clip_id": "SparklyHomelyTildeAllenHuhu", "clip_title": "...", "clip_view_count": 50234, ...}

{"label": "negative", "video_id": "1234567890", "broadcaster": "lacy",
 "start": 4532.1, "end": 4569.3, "duration": 37.2,
 "paired_with_clip_id": "SparklyHomelyTildeAllenHuhu"}
```

- `positive_margin_sec` = how much context to include before/after the clip's own boundaries (default 60 s → clip span + ~1 min each side).
- `min_gap_sec` = minimum distance between any negative span and any positive span (default 300 s → 5 min).
- `negatives_per_positive` × `positives` = negative spans per VOD, up to 50 sampling attempts per negative before giving up.
- Deterministic: `--seed` controls the RNG.

### `summary`

Print broadcaster / clip-count / span-duration / view-count stats.

```bash
python3 scripts/research/bootstrap_twitch_clips.py summary --clips dataset/clips.jsonl
```

```json
{"total_clips": 247, "broadcasters": 3,
 "per_broadcaster": {"xqc": 100, "lacy": 100, "pokimane": 47},
 "duration_median_sec": 29.0, "duration_max_sec": 60.0,
 "views_median": 12400, "views_max": 523100}
```

---

## Intended uses

1. **Eval harness for Phase 4.2 CG-DETR** — when someone ships CG-DETR moment retrieval, run it on this dataset to measure R1@0.5 / R1@0.7 / mAP against the Twitch-clip positives. This is the "why CG-DETR ships in Phase 5+, not Phase 4" gating signal.
2. **Bootstrap DPO training data** (Phase 5.4) — positive clip titles become the "chosen" in `(prompt, chosen, rejected)` triples; model-generated rejected titles paired against the same VOD become the "rejected".
3. **Regression testing** — run the full OpenClaw pipeline on VODs in this dataset and measure whether each positive span is detected as a clip candidate (Pass A/B/C) and how the detected span compares to Twitch's `vod_offset + duration` ground truth.

---

## ToS / rate-limit posture

- **Helix path** respects standard Twitch API rate limits (800 points/min for public endpoints).
- **GraphQL path** uses the same community-known public web client_id that TwitchDownloader has relied on for years. Same ToS caveat applies: the endpoint is not an official API surface and could change or rate-limit without notice.
- Built-in `--delay-sec` (default 0.5) between broadcasters. For bulk runs (50+ broadcasters), raise this to 2-5 s.

---

## Not shipped / future work

- **VOD download wiring** — this tool only fetches clip metadata. Downloading the actual VOD segments is left to `twitch-dl` or `TwitchDownloaderCLI` invoked separately; the `video_id` + `start` / `end` in the paired triples map directly to those tools.
- **Kick equivalent** — `kick.com/api/v2/channels/{slug}/clips` has the same shape, but the auth/format is different and not yet plumbed.
- **Automatic transcription** of the positive / negative spans — obvious next step for eval, but not in Phase 5.3 scope.

---

## Related

- [[entities/chat-fetch]] — Phase 2.2 VOD chat tool uses the same GraphQL endpoint for a different purpose
- `IMPLEMENTATION_PLAN.md` — Phase 5.3 definition + future 5.4 HITL/DPO wiring
