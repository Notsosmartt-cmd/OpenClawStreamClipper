---
title: "Reference Humor — clipping jokes whose context lives outside the VOD (2026-07 evaluation)"
type: concept
tags: [research, humor, memes, references, detection, chat, incongruity, evaluation, reference]
sources: 0
status: reference
updated: 2026-07-02
---

# Reference Humor — externally-referenced jokes (2026-07 evaluation)

Owner's question (2026-07-02): the **George Bush clip** — streamer says *"ever heard of George?"* then tries (and fails) to push the other streamer into a bush. The joke = a known meme format (*"ever heard of George?" + push into bush → George **Bush***). The reference is **never stated in the VOD** — it's pulled from outside culture. *Is there any way to engineer for this scheme, without losing the existing storytime/conversation and blatant-banger lanes?* Evaluation only — **no code changed**. Companion to [[concepts/case-incongruity-comedy]] (this exact clip's incongruity half) and [[concepts/multimodal-fusion-2026-07]] (the fusion prerequisite).

---

## Verdict on the assumption

**"Hard to clip because it requires the external reference" — TRUE for *understanding*, OVERSTATED for *detection*.** Split the problem into three jobs with different requirements:

| Job | Needs the reference? | What it actually needs |
|---|---|---|
| **Detect** (propose the moment) | **No** | Reaction proxies: laughter, the other streamer's reaction, motion chaos, a fail beat, chat spike |
| **Score/appreciate** (rank it correctly) | Partly | Recognizing "this is a known format" raises the ceiling; proxies alone under-rank deadpan executions |
| **Present** (title/hook/captions) | **Yes** | "George Bush moment 💀" as the hook requires naming the reference |

The pipeline can *clip* this moment without ever getting the joke — but it will title it blandly and may under-rank it. Full value needs the reference recognized.

## Four key insights

1. **The audience is the external-knowledge oracle.** Humor you don't understand still produces laughter you can hear ([[entities/audio-sense-module]] CLAP `laughter`), reactions you can see (motion spikes), and — decisive for streams — **chat that names the joke**. Viewers who get it type "GEORGE BUSH LMAOO". The reference *does* enter the data — through the chat sidecar, not the audio. The pipeline already ingests chat (grounding cascade, Stage 6 `chat_context_block`), so the external culture is already flowing in; nothing mines it for reference names yet.
2. **The pun is textually decodable once the senses fuse.** "George" is *spoken*; the bush is *seen*. Neither modality alone contains the pun — but a fused representation (timeline: `TEXT "ever heard of George?" | MOTION shove | VISION plant/bush | AUDIO laughter`) puts both tokens in one context, where wordplay detection becomes a straight LLM task. Reference humor is therefore **downstream of the fusion work** ([[concepts/multimodal-fusion-2026-07]] option 1): assemble first, then recognize.
3. **The LLM already knows most memes — nobody asks.** The 35B was trained on internet culture; "ever heard of George? push into bush" is likely *in there*. The failure isn't missing knowledge, it's (a) the evidence is never assembled (insight 2) and (b) no prompt ever asks "is this a known format?". A **reference-recognition probe** is prompt engineering, not new infrastructure. The genuine knowledge gap is only **post-cutoff / ultra-niche formats**.
4. **The truly hard residual: deadpan reference with no reaction.** No laughter, no chat, no physical chaos, joke never explained → nothing to detect *or* recognize. Accept the miss; this is rare on stream (live chat almost always reacts).

## Engineering options (ranked; all additive, boost-only, flag-gated — the storytime/blatant lanes are untouched per repo convention)

1. **Unexplained-reaction proxy lane (cheapest; detects without understanding).** Propose moments where reaction signals are strong but the transcript shows no textual joke: `laughter/cheer (CLAP) high + motion spike + keyword score low` → `src=ANOMALY` candidate. This is the same lane as the incongruity proposer — reference humor and incongruity comedy share the signature *"audience reacts, words don't explain it."* Catches George Bush for **detection** today-ish.
2. **Chat reference mining (the reference leaks in from culture).** Around each candidate window, scan the chat sidecar for burst n-grams/emote spikes ("GEORGE BUSH", format names, dead/skull emotes). A named burst = (a) score boost (audience confirmed the bit landed), (b) **title/hook material** — the presentation job solved by the viewers themselves. Uses already-ingested data; a small offline miner + a boost hook.
3. **Reference-recognition probe in the joint prompts.** Add to the Stage 5.5 judge / Stage 6 enrichment (and the future anomaly-verifier) prompt: *"Could this be a known meme, skit, or joke format? Also check wordplay between spoken words and visible objects/actions. Name it: `known_format: {name, confidence}`."* Zero new models; triggers the latent meme knowledge insight 3 says is already there. Depends on fusion option 1/3 so the prompt actually contains both halves of the pun.
4. **Meme-format library (RAG for formats; covers post-cutoff/niche).** A curated, growing `config/meme_formats.json`: format name, verbal trigger (embedding/regex), visual signature, example. Match candidate windows via the sentence-transformers stack already in the repo (callback detection). **Growth loops:** (a) the owner's `reference_clips/*.notes.json` sidecars name formats; (b) [[concepts/plan-clip-forensics]] decompositions of competitor clips tag formats via the style-profile LLM; (c) later, periodic deep-research/yt-dlp ingests of trending formats (yt-dlp already deferred by owner). Mirrors the existing `channel_keywords.json` pack pattern.
5. **Omni-model perception** ([[concepts/multimodal-fusion-2026-07]] §5) — better *assembly* of the evidence, but note it doesn't remove the knowledge problem: a model that perceives the push perfectly still needs the meme in its weights (probe #3) or in a library (#4).

## A2 mining mechanics — how chat is actually grabbed from the video (designed 2026-07-03)

Owner clarified (2026-07-02) that chat arrives **burned into the video** (streamer-composited overlay), not as a data file — and asked how mining would work given fast-moving chat and reaction latency. The design:

- **No second LLM.** The only extra model is **EasyOCR** — already installed and verified (13.08 wps caption extraction) — a small OCR net run in **offline batch** after Stage 2, not live. One big model (the 35B) still does all reasoning; OCR just produces symbols for its timeline.
- **NOT a second-by-second full scan.** A 2 h VOD at 1 fps = 7,200 OCR calls (hours on CPU). Instead, two tiers:
  1. **Chat *velocity* without OCR** — frame-diff pixel-change rate *inside the chat region only*, sampled ~2–4 fps. Fast scroll = many new messages = burst. Same cheap trick as `motion_events`, restricted to an ROI. This is the *detection* signal (and a reaction-proxy input to the A1 anomaly lane by itself).
  2. **OCR only bursts + candidate windows** — for a burst/candidate at time T, OCR the chat ROI at ~1 fps across [T−2, T+20]; **diff consecutive samples to keep only NEW lines** (chat scrolls up, new messages enter at the bottom) with fuzzy dedup. ≈20–40 OCR calls per candidate, not thousands.
- **Chat region (ROI):** static per streamer → per-channel config (the `streamer_prompts.json` pattern), with an auto-detect fallback (text-density heatmap over a few sampled frames).
- **Output:** `chat_events.json` — a `{t, velocity}` series + deduped `{t, text}` lines — exactly parallel to `audio_events.json`, folded into the [[concepts/multimodal-fusion-2026-07]] A1 timeline as a `CHAT` track alongside `TEXT/AUDIO/MOTION`.
- **Latency: yes, modeled explicitly.** The burned-in overlay shows what the streamer saw at T (composited live), but viewers watched a **delayed feed**: a reaction to moment M appears at ≈ M + broadcast delay (2–6 s low-latency) + read/react (1–3 s) + typing (2–5 s) → **~5–12 s typical lag**. So: mine *forward* of a moment ([T, T+20]) and attribute bursts *backward* (burst at B → moment ≈ B − lag). Lag is a per-channel config value with an **auto-calibration trick**: cross-correlate the CLAP laughter/cheer series with the chat-velocity series — the peak-correlation offset *is* that channel's empirical lag.
- **Honest caveats:** EasyOCR on small chat fonts is imperfect; **emotes are images, not text** (an all-emote burst registers on velocity but is unreadable as words); very fast scroll can skip messages between samples — acceptable, because the burst n-grams we want ("GEORGE BUSH" spam) are *repeated by nature*, so capture probability stays high.

## Recommended sequence

Detection first, understanding second: **(1) proxy lane** (shared with the incongruity proposer — one lane serves both) → **(2) chat mining** (cheap, solves presentation for the common case) → **(3) recognition probe** once fusion lands → **(4) format library** as the corpus grows. All boost-only entries into Pass C; existing lanes (storytime/conversation via Pass B, blatant bangers via Pass A/B) continue unchanged — this adds a third proposal source, it replaces nothing.

## Related
- [[concepts/case-incongruity-comedy]] · [[concepts/multimodal-fusion-2026-07]] · [[concepts/model-senses]] · [[entities/audio-sense-module]] · [[concepts/plan-clip-forensics]] · [[concepts/plan-calibration-loop]]
