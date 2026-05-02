---
title: "chat_features.py — chat signal feature extractor"
type: entity
tags: [chat, features, emote, z-score, ground-truth, phase-2, module, pass-a, signals]
sources: 2
updated: 2026-05-01
---

# `scripts/lib/chat_features.py`

Stdlib-only feature extractor over the canonical chat JSONL produced by [[entities/chat-fetch]]. Introduced 2026-04-23 as Phase 2.3 of [[sources/implementation-plan]].

> [!note] Consumer scope narrowed 2026-05-01
> The module's full feature surface (msgs/sec, z-score, emote density, top emotes, phrase hits) is preserved, but the pipeline now consumes **only the hard-event counts** (`sub_count`, `bit_count`, `raid_count`, `donation_count`) for grounding. Burst + emote-density scoring was removed from Pass A and Pass B/Stage 6 prompt context blocks because chat is latent vs. the moment — see [[concepts/chat-signal]]. The other features remain available for future consumers and for CLI debugging.

---

## API

```python
import chat_features

feats = chat_features.load("vods/.chat/mystream.jsonl")
if feats.is_empty():
    return  # no chat → skip the whole chat-signal path

# Window query — any (start, end) range in seconds
stats = feats.window(start=300, end=330, baseline_window_sec=300)
# stats = {
#   "start": 300, "end": 330,
#   "msgs": 247, "msgs_per_sec": 8.23,
#   "baseline_per_sec": 0.45, "burst_factor": 18.3, "z_score": 20.1,
#   "unique_chatters": 89,
#   "emote_density": {"laugh": 58, "hype": 3, "tense": 1},
#   "top_emotes": [("KEKW", 58), ("LULW", 31), ("POGGERS", 12)],
#   "phrase_hits": {"laugh": 4, "hype": 1},
#   "sub_count": 0, "bit_count": 0, "raid_count": 0, "donation_count": 0,
# }
```

Also exposes:

- `load_emote_dict(path=None)` — compile `config/emotes.json` into `{emote: category}` + compiled phrase regexes.
- `denylist_event_map(chat_config=None)` — `{category: {keyword: event_count_key}}` from `config/chat.json::ground_truth`. Used by [[entities/grounding]]`.cascade_check(event_map=...)`.
- `message_count`, `duration_sec`, `is_empty()` — top-level stats.

CLI mode: `python3 scripts/lib/chat_features.py --chat file.jsonl --start 100 --end 130`.

---

## What gets computed

### Rate + z-score

`msgs_per_sec` is messages ÷ window duration. The z-score uses a rolling baseline computed from `±baseline_window_sec` seconds on either side of the window, excluding the window itself so spikes don't mask themselves. Default baseline is 300 s (5 minutes).

### Emote density

Tokenizes each message's `emotes` array and groups by category using `config/emotes.json`'s mapping. The pre-shipped dictionary covers:

| Category | Example emotes |
|---|---|
| laugh | KEKW, LULW, OMEGALUL, LUL, LMAO, PepeLaugh |
| hype | PogChamp, POGGERS, PogU, LETSGO, catJAM |
| tense | monkaS, monkaW, Pepega, peepoNervous |
| sad | SADGE, PepeHands, FeelsBadMan, peepoSad |
| big_play | GIGACHAD, Chadge, EZ, EZ Clap |
| loss | L, LLL, COPE, FeelsWeirdMan, ICANT |
| win | W, WWW, GG, WINNING |
| confusion | WICKED, HUH, Wut, BRUH |
| cringe | YIKES, cringe, peepoCringe |

Plus a `phrase_patterns` block for multi-word phrase bursts ("let's go", "i'm dead", "big L"), matched case-insensitively on full messages.

### Hard ground-truth event counts

Records with `type != "chat"` (e.g. `type: "sub" | "bit" | "raid" | "donation"`) are aggregated into per-window counters. The `count` field on each event record (e.g. `count: 5` for a 5-sub subgift) is summed — default 1 when absent. These counters feed the [[entities/grounding]] cascade's `hard_events` kwarg for Phase 2.4d event-ground-truth checks.

### Phrase hits

Simple boolean-per-message recurring-phrase detector keyed by category. A message can hit at most one category (first match wins). Useful for catching "let's go" bursts that don't come with hype emotes attached.

### Top emotes

Top 5 distinct emote tokens by raw count — goes into the Pass B / Stage 6 prompt's chat_context block so the VLM sees exactly what chat was spamming.

---

## Performance

- **Load**: ~1 second for a 2-hour VOD's ~200 k messages. Stores only per-second indices; total memory roughly 50 MB for the largest VODs.
- **Window query**: O(window_seconds + baseline_window_seconds × 2) — microseconds for typical 30 s / 5 min windows.
- **CPU-only**: zero VRAM impact, doesn't interfere with the main pipeline's model load.

---

## Related

- [[entities/chat-fetch]] — input source
- [[concepts/chat-signal]] — architectural overview
- `config/emotes.json` — category dictionary (edit to add channel-specific emotes)
- `config/chat.json` — scoring caps + ground-truth event map
- [[entities/grounding]] — consumer via `cascade_check(hard_events=..., event_map=...)`
