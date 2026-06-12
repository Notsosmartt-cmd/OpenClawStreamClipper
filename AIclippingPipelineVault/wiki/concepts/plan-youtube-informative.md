---
title: "Plan — Storytelling upgrades + YouTube/informative ingest"
type: concept
tags: [plan, storytime, youtube, informative, ingestion, segment-detection, clip-duration]
sources: 0
status: planned
updated: 2026-06-12
---

# Plan — Storytelling upgrades + YouTube/informative ingest

Filed from the 2026-06-12 deep evaluation. **Goal:** (a) make 1.5–3 min storytime moments survive end-to-end, and (b) let the owner drop long-form YouTube videos (essays, podcasts, info content) into the pipeline and get "informative"/"storytelling" clips out.

---

## Storytime today — what already works

26-phrase storytime keyword set (incl. "long story short" — would catch the TBVNKS reference clip); `just_chatting`/`irl` get 480 s chunks (a 2-min story fits one chunk); highest keyword ceiling (0.90); 150 s Pass B cap for storytime/emotional; **length-neutral scoring default ON** since 2026-06-06 (word-density tightness replaced the 0.65× long-clip penalty) — see [[concepts/detection-improvements-plan]] Fix 4.

## Four remaining weak points (fix in this order)

1. **No-boundary fallback = 45 s truncation.** If Pass B omits `start_time`/`end_time`, storytime falls to a 45 s default — a 2-min story loses its setup. Fix: storytime-specific boundary inference (extend `_infer_content_window` to walk back to the story-opener discourse marker).
2. **Narrative-group 90 s hard cap** (`moment_groups.py` `NARRATIVE_MAX_DURATION=90`) truncates merged stories. Raise for storytime-only groups, or skip grouping when a single member exceeds ~75 s.
3. **Vision sees only payoff frames** (T−2…T+5) unless the moment is an A1/M3 arc — a 2-min story's setup is invisible to titling and the judge. Extend setup-frames to any moment >90 s.
4. **Bucket competition** — a long story occupies a slot 3 hype clips wanted. By design; let the [[concepts/plan-calibration-loop]] fitter set this tradeoff instead of a hand-tuned curve.

---

## YouTube readiness — gaps by effort

| Gap | Effort | Note |
|---|---|---|
| Chat absence | 0 d | Already graceful (reaction/engagement axes reweight when chat missing) |
| "Streamer"/"clip scout" prompt language | ~0.5 d | Parameterize per source type (Pass B `stage4_moments.py:1667,1709`; Stage 6 `stage6_vision.py:491,510,523`) |
| **`informative` category** | ~1 d | **Critical — an LLM emitting "informative" is canonicalized away today.** Add keywords ("the key is", "what you need to know", "breaking down", "research shows"…), prompt line, segment weights, style entry |
| Longer chunks for long-form | ~0.5 d | 480 s → 900 s when source=youtube (watch token cost per Pass B call) |
| Segment taxonomy (`educational`/`podcast`) | 2–3 d | Or soft-hint via the existing `--type` flag first |
| yt-dlp ingestion flag | ~1 d (~80 lines) | Zero yt-dlp references in the repo today |

**Recommended shape:** one **`--source youtube` content profile** that switches taxonomy, category vocabulary, prompt language, and chunking together — not five scattered edits.

> [!note] Works today, degraded
> Manually downloaded YouTube files dropped in `vods/` already run end-to-end: decent story detection, weak "informative" detection (maps awkwardly to `hot_take`/`storytime`), `just_chatting` prompt emphasizes interactive/social dynamics that video essays don't have.

## Related

- [[concepts/clip-duration]], [[concepts/boundary-snap]], [[concepts/segment-detection]]
- [[concepts/arc-aware-extraction]] — chunk cards help long-form cross-chunk arcs
- [[concepts/case-incongruity-comedy]] — TBVNKS reference clip analysis
- [[concepts/chat-signal]] — what chat absence costs
