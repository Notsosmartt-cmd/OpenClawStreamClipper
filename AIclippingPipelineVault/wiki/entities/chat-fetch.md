---
title: "chat_fetch.py — VOD chat fetcher"
type: entity
tags: [chat, twitch, graphql, twitchdownloader, phase-2, module, stage-1, signals]
sources: 2
updated: 2026-04-23
---

# `scripts/lib/chat_fetch.py`

Two-mode VOD chat acquisition utility introduced 2026-04-23 as Phase 2.2 of [[sources/implementation-plan]]. Produces the canonical JSONL format consumed by [[entities/chat-features]] — see [[concepts/chat-signal]] for the schema.

---

## Mode 1 — Anonymous Twitch GraphQL (`fetch` subcommand)

Streams a VOD's full chat via Twitch's unofficial `/comments` persisted-query endpoint:

```bash
python3 scripts/lib/chat_fetch.py fetch \
    --vod-id 1234567890 \
    --out    vods/.chat/mystream.jsonl \
    --delay-ms 200
```

**How it works**: POSTs the `VideoCommentsByOffsetOrCursor` persisted query (SHA256 hash hard-coded to match the current public hash that community tools use) to `https://gql.twitch.tv/gql`. Follows `pageInfo.hasNextPage` until the end of the VOD, sleeping `--delay-ms` between pages to stay under unofficial rate limits. Safety guards: `max_retries=3` with exponential backoff, cursor deduplication so an echoed cursor doesn't infinite-loop.

**Rate-limit / ToS posture**: the endpoint is not an official Twitch API surface. Using the default public web client ID is what `lay295/TwitchDownloader` and other community tools do, but isn't squeaky-clean from a Twitch ToS perspective. Set your own Helix client ID via `--client-id` if you have one.

**Event extraction quality**: moderate. Bit events are detected reliably via `cheerN` tokens in message text. Sub / resub events are synthesized opportunistically from `"gifted N subs"` patterns and subscriber badges, which catches some but misses many. Use Mode 2 when you need event ground truth.

---

## Mode 2 — TwitchDownloader JSON import (`import` subcommand)

```bash
TwitchDownloaderCLI --mode ChatDownload --id 1234567890 -o td.json
python3 scripts/lib/chat_fetch.py import --source td.json --out vods/.chat/mystream.jsonl
```

Converts TwitchDownloader's `{"comments": [...]}` JSON into the canonical JSONL. Extracts dedicated sub/resub/subgift/raid events via `message_type` field — **much richer event data than Mode 1**. Preferred path for users who care about event ground truth for the [[entities/grounding]] cascade's hard-event check.

---

## Mode 3 — Filename → VOD-ID extraction (`extract-id` subcommand)

```bash
python3 scripts/lib/chat_fetch.py extract-id --filename "lacy_stream_twitch-1234567890.mp4"
```

Regex-matches a VOD ID out of a filename. Used by Stage 1b's auto-fetch hook to decide whether a given VOD is fetchable. The default pattern catches `twitch-NNNNN`, `_vNNNNN`, `_video_NNNNN`; override via `--pattern` or in `config/chat.json::auto_fetch.vod_id_pattern`.

---

## Canonical output schema

See [[concepts/chat-signal]] §Canonical chat JSONL schema. One record per line; each chat message gets a `type: "chat"` record. Events (sub / bit / raid / donation) get an ADDITIONAL synthesized record at the same timestamp with `type: "sub" | "bit" | "raid" | "donation"` and a `count` field.

---

## Integration points

- **Stage 1b** (`scripts/clip-pipeline.sh`): auto-discovery — checks for `vods/.chat/<basename>.jsonl`, falls back to `fetch_twitch_gql()` when enabled in `config/chat.json`.
- **CLI only**: TwitchDownloader import is NOT called automatically — users run it once per VOD manually and drop the output into `vods/.chat/`.

---

## Graceful degradation

- Network error / HTTP error / parse error → print one-line `[CHAT] GQL HTTP error: ...` to stderr and return `0` (Stage 1b logs "auto-fetch failed" and proceeds without chat).
- Malformed TwitchDownloader JSON → print `[CHAT] import parse error: ...` and return `0`.
- Empty / private VOD (0 comments returned) → Stage 1b sees the empty file and sets `chat_available="false"`.

---

## Related

- [[entities/chat-features]] — consumer of the JSONL output
- [[concepts/chat-signal]] — full architecture overview
- `config/chat.json` — `auto_fetch` settings + client ID + rate limits
