---
title: "Chat Signal (Pass A') — Phase 2"
type: concept
tags: [chat, twitch, emote, eventsub, grounding, phase-2, stage-4, pass-a, pass-b, signals]
sources: 3
updated: 2026-05-01
---

# Chat Signal (Pass A')

Per `ClippingResearch.md` §Additional topic 4 and Song et al. 2021 (EPJ Data Science 10:43), **chat emote signatures alone hit ~0.75 F1 on epic-moment detection — within 0.05 of vision-alone (0.70)**, and the two signals are additively composable (0.82 multimodal). This makes chat the highest-value "free" detection input available — it's bandwidth-cheap (KB/min even on top channels), cannot be drowned out by music overlay, and exposes HARD GROUND TRUTH (sub/bit/donation event counts) that kills the entire "gifted subs" hallucination class once and for all.

Shipped 2026-04-23 as Phase 2 of [[sources/implementation-plan]]. Only the **VOD path** is implemented in Phase 2 — the live EventSub daemon is tracked as a separate product for future work.

> [!warning] Burst + emote-density scoring removed 2026-05-01
> The Pass A' burst-factor and emote-density scoring contributions were removed because **chat is latent vs. the moment**. Chat reactions lag the actual on-stream event by 2-5 seconds, and Pass A's keyword-window timing wasn't designed to absorb that. The latent signal was biasing scoring toward the *previous* keyword cluster.
>
> The Pass B / Stage 6 chat_context informational blocks were removed at the same time — same latency issue, plus prompt clutter.
>
> **What's preserved:** the hard-event ground-truth check (`sub_count` / `bit_count` / `raid_count` / `donation_count`) flows through unchanged. Hard events are factual records, not timing measurements — they kill the "gifted subs" hallucination class regardless of when chat catches up. Stage 6's HARD GROUND-TRUTH RULE is now emitted only when the ±8 s chat window has **zero** events of every type (the case where the cascade will reject claims of subs/bits/raids/donations).
>
> The "Scoring contribution" and "Prompt context" sections below are kept as historical record. The [[entities/grounding]] cascade integration (last section) is still live.

---

## Data flow

```
    vods/.chat/<basename>.jsonl            ← manually dropped,
                                              or Stage 1b auto-fetches
                                              via Twitch GraphQL
            │
            ▼
scripts/lib/chat_features.py — load()      (stdlib only)
            │
            ▼
  /tmp/clipper/chat_available.txt marker   (Stage 1b writes "true"/"false")
            │
            ├─▶ Pass A: chat_burst + emote_density → +≤2 to raw signal count
            │
            ├─▶ Pass B prompt: 4-line chat_context block per 5-min chunk
            │
            ├─▶ Stage 6 prompt: 5-line chat_context block per ±8 s window
            │                   + explicit "if sub_count=0 you may NOT say 'gifted subs'" rule
            │
            └─▶ Grounding cascade: hard_events={sub_count, bit_count, raid_count, donation_count}
                                   → Tier 1 hard-reject when a denylist hit contradicts ground truth
```

When no chat file is available for a VOD, every consumer short-circuits and behaves exactly as it did before Phase 2. There is no throughput penalty and no behavioral regression.

---

## VOD chat acquisition

Two supported paths, both produce the canonical JSONL at `vods/.chat/<basename>.jsonl`:

### A — Anonymous Twitch GraphQL auto-fetch

When `config/chat.json` has `auto_fetch.enabled = true` AND the VOD filename matches `vod_id_pattern` (default extracts `twitch-NNNNN`, `_vNNNNN`, `_video_NNNNN`), Stage 1b calls [[entities/chat-fetch]]`.fetch_twitch_gql()` to stream the full chat via Twitch's unofficial `/comments` persisted-query endpoint.

> [!warning] Unofficial API
> The `/comments` endpoint is not part of Twitch's documented API. It can change without notice, and heavy usage could be rate-limited or blocked. Using the public web client ID is what community tools like `lay295/TwitchDownloader` do, but it is not squeaky-clean from a ToS perspective. Set your own `twitch_client_id` in `config/chat.json` if you have Helix credentials.

`auto_fetch` is **disabled by default** in `config/chat.json` — users have to opt in explicitly.

### B — TwitchDownloader JSON import (preferred for ToS-conscious users)

Run `TwitchDownloaderCLI --mode ChatDownload` externally, save the JSON, then:

```bash
python3 scripts/lib/chat_fetch.py import \
    --source path/to/twitchdownloader.json \
    --out   vods/.chat/<basename>.jsonl
```

This path produces **richer event data** than the GraphQL path — TwitchDownloader captures dedicated `message_type: subscription | resub | raid` events that the /comments endpoint omits. When you care about hard ground truth for the cascade's event check, prefer this path.

### C — Manual drop-in

Any tool that produces the canonical JSONL shape (see [[entities/chat-features]]) works. Filename must match the VOD basename (without extension).

---

## Canonical chat JSONL schema

One JSON object per line. Minimum fields:

```
{"t": 12.4, "user": "xqc", "text": "KEKW insane",
 "emotes": ["KEKW"], "badges": ["subscriber/12"],
 "bits": 0, "type": "chat"}
```

Additional event record types (sub / bit / raid / donation) synthesized alongside the chat message where possible:

```
{"t": 45.0, "user": "x", "text": "X gifted 5 subs!", "emotes": [],
 "type": "sub", "count": 5, "tier": "1000"}
```

Required: `t` (seconds from stream start, number), `text` (string). Everything else optional.

---

## Scoring contribution (Pass A')

Inside the 30 s keyword-scan window, when chat features are available:

- **Burst bonus** — `log1p(z_score - 1) * 0.6`, capped at `scoring.max_burst_bonus` (default 2.0). Fires when the window's msgs/sec z-score vs the ±5 min baseline is ≥ 2.0. Added to the raw signal count, NOT tied to a specific keyword category (a burst alone is ambiguous).
- **Emote-density bonus** — when ≥ 5 matched emote tokens land in the window, the dominant emote category is mapped to a keyword category (laugh→funny, hype→hype, sad→emotional, etc.) and adds `dom_count / 20` to that category's weight, capped at `scoring.max_emote_bonus` (default 1.5).

Both contributions are additive; the moment's total signal count is then compared to the segment's threshold as before. The `keyword_moments.json` records get `chat_z`, `chat_msgs`, `chat_sub_count`, `chat_bit_count` fields for diagnostic surfacing.

---

## Prompt context (Pass B + Stage 6)

Both prompts get a structured chat_context block when chat is available:

```
Chat activity in this chunk [T=300..600s]:
- 247 messages over 300s (baseline 0.45/s, burst factor 1.8×, z=4.5)
- Dominant emotes: KEKW×58, LULW×31, POGGERS×12
- Events in window: sub_count=0 bit_count=0 raid_count=0 donation_count=0
Rule: if sub_count/bit_count/donation_count are all 0 in this window, you may NOT describe any moment as 'gifted subs', 'sub train', 'hype train', 'bits rain', or 'donations' — no matter what the transcript sounds like.
```

Stage 6 uses the same block sized to the ±8 s window around the peak, with a slightly stronger "HARD GROUND-TRUTH RULE" prefix. See [[concepts/vision-enrichment]].

---

## Grounding cascade — hard event check (Phase 2.4d)

`grounding.cascade_check()` accepts two new kwargs:

- `hard_events={"sub_count": N, "bit_count": N, "raid_count": N, "donation_count": N}` — per-window ground truth (from `chat_features.window().sub_count` etc.)
- `event_map={category: {keyword: event_count_key}}` — from `chat.json`'s `ground_truth` block via `chat_features.denylist_event_map()`.

When both are supplied, every denylist hit whose keyword maps to a zero-count event is a **hard Tier-1 fail** with `reason="event_contradicts_ground_truth"`. This overrides token-overlap and the LLM judge — the premise is that EventSub (or its closest VOD proxy) IS the authoritative answer to "did the streamer actually get gifted subs?" The 2-tier cascade was simplified from the original 3-tier (MiniCheck + Lynx) on 2026-05-01, but Tier 1's hard-event check stayed structurally identical because it's the single sharpest anti-hallucination signal in the system and is independent of any LLM.

In practice this means:

| Claim | sub_count | Tier 1 word-match | Verdict |
|---|---|---|---|
| "Streamer Reacts To Gifted Subs" | 1 | pass (words supported) | **pass** — legit sub event |
| "Streamer Reacts To Gifted Subs" | 0 | pass (words supported) | **hard fail** — event_contradicts_ground_truth |
| "Ranked 3.0 Clutch" | 0 | pass (no denylist) | **pass** — not an event claim |

Without `hard_events`, the cascade behaves exactly as its Phase-1 form.

---

## Cost / risk

| Dimension | Cost |
|---|---|
| VRAM | zero — chat fetch + features are stdlib-only, CPU-bound |
| Pipeline wall time | negligible; adds ~2-5 s for chat_features load on a 2-hour VOD |
| Network | one POST per ~100 chat messages if auto-fetching; sleep 200 ms between pages |
| Fetch-time for a typical 2-hr VOD | ~1-2 minutes (one-time per VOD) |
| Docker deps | none — no new packages |

Every consumer (Pass A, Pass B, Stage 6, cascade) short-circuits cleanly when chat is unavailable: the VOD renders with pre-Phase-2 behavior and a single `[CHAT] no chat data available for this VOD` log line.

---

## Related

- [[entities/chat-fetch]] — Twitch GraphQL + TwitchDownloader importer
- [[entities/chat-features]] — stdlib feature extractor
- [[entities/grounding]] — Phase 2.4d hard-event integration
- [[concepts/highlight-detection]] — Pass A uses chat burst + emote density
- [[concepts/vision-enrichment]] — Stage 6 prompt includes chat_context + HARD rule
- `IMPLEMENTATION_PLAN.md` — Phase 2 definition + deferred live EventSub path
