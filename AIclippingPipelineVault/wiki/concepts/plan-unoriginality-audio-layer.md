---
title: "Plan — Unoriginality Fix: the Audio Layer"
type: concept
tags: [plan, originality, tiktok, audio, sfx, voiceover, music, research]
sources: 0
status: planned
updated: 2026-06-12
---

# Plan — Unoriginality Fix: the Audio Layer

Filed from the 2026-06-12 deep evaluation. **Problem:** the owner's posted clips are being flagged "unoriginal content" on TikTok (ineligible for the For You feed and Creator Rewards). Competitor accounts posting the *same source moments* get distribution — their clips carry cliché sound effects at punchlines, voiceover, and music. Platform logos (Twitch/Kick) appear irrelevant in either direction. Reference competitor-clip transcripts live at `B:\AuxCoding\VideoToText-main\transcripts\` (4 files) — analyzed in [[concepts/case-incongruity-comedy]].

> [!note] Core finding
> The [[concepts/originality-stack]] is visually strong but ships clips whose **audio track is essentially raw stream audio**. TikTok fingerprints audio and explicitly survives mirror/speed/color tweaks — the exact transformations Wave A leans on. The fix is **additive creative layers** (SFX/VO/music/dense captions), which is also precisely what TikTok's own policy rewards as originality. Sub-perceptual perturbation is the wrong theory of the problem.

> [!warning] Refined by deeper research — [[concepts/tiktok-originality-mechanics-2026-06]] (2026-06-12)
> A full-verification deep-research pass **sharpens (and partly corrects) this plan**:
> - **Voiceover/commentary is the strongest lever, not SFX/music** — it's TikTok's #1 prescribed remedy AND structurally strong (your own audio isn't in any fingerprint/cover DB). **Promote P1.3 (Piper VO) to P1 #1**, and make it *real commentary*, not the current 8-14 word hook.
> - **"Music bed breaks the fingerprint" is refuted** (0-3): matchers isolate foreground audio, so background beds don't hide the original. Keep the music bed as a *creative/engagement* layer, not as fingerprint evasion.
> - **The frame itself is wrong**: don't try to "perturb the fingerprint" (the matcher is robust; pitch/tempo/speed/crop/flip are all weak or refuted). Invest in *genuine transformation* — VO, creator-presence/face-cam, restructuring — which is the only thing that both satisfies TikTok and makes better clips.
> - **Account-level risk is real**: the flag escalates from per-video to account-wide FYF ineligibility for accounts that repeatedly post ineligible content — exactly the automated-repost profile. Half-measures are account-risky, not just per-clip-ineffective.
> See the ranked Tier A/B/C transformation list in [[concepts/tiktok-originality-mechanics-2026-06]].

---

## What TikTok flags (researched June 2026)

From TikTok's published guidance and third-party detection analysis:

- **Unoriginal** = reposted content without significant changes, mere splicing of clips, content with another platform's visible watermark/superimposed logo, no creative additions.
- **What earns "original"**: appearing on screen, **adding your own voiceover**, own commentary/editing/transitions/captions — "dynamic and intentionally created."
- **Detection stack**: audio fingerprinting (ACRCloud-style derivative-works matching — robust to pitch and timing modification), visual perceptual hashing (robust to cropping, filters, color adjustment, **mirroring, speed changes**), metadata/C2PA tracing, and behavioral patterns (accounts mass-posting similar content with similar captions).
- Consequence: For-You ineligibility + excluded from Creator Rewards (originality is a rewards-formula metric).

Two implications matching the owner's observations:

1. **Logos don't decide it.** Watermark detection is one signal; competitors with Twitch/Kick logos still get reach because the rest of the video reads as transformed.
2. **The fingerprint race is against other clippers, not just the VOD.** A viral moment is uploaded by dozens of accounts within hours; dedup compares against every prior upload of that moment. Differentiation must be *substantial and additive*, not sub-perceptual.

Sources: [TikTok Creator Academy originality policy](https://www.tiktok.com/creator-academy/article/tiktok-originality-policy), [Community Guidelines — Integrity & Authenticity](https://www.tiktok.com/safety/en/policies-and-engagement/integrity-authenticity), [RenderIO — duplicate content detection](https://renderio.dev/blogs/tiktok-duplicate-content-detection/), [Napolify — how TikTok detects duplicates](https://napolify.com/blogs/news/duplicate-content-detection), [Napolify — algorithm penalty](https://napolify.com/blogs/news/tiktok-duplicate-penalty), [ALM Corp — TikTok × ACRCloud](https://almcorp.com/blog/tiktok-acrcloud-derivative-works-detection/).

---

## Where the stack is strong vs exposed (audit of defaults, 2026-06-12)

**Strong (visual, mostly ON):** per-clip blur radius [18–32], ~45% mirror, color/hue/gamma jitter, 35% micro-shake, 30% vignette, randomized hook palette/position, GOP/CRF jitter, metadata strip, white-flash beats (BUG 64 fixed), CapCut word-box captions ON, hook card ON. See [[concepts/originality-stack]].

**Exposed (audio, mostly OFF in `config/originality.json` as of 2026-06-12):** `tts_vo: false`, `music_bed: ""`, `music_tier_c: false`. Profile-mode pitch jitter (±2–5¢) is *designed to be inaudible* — i.e. designed not to change the fingerprint meaningfully. `eq_tilt_db` is **computed but never wired** into the filter graph (`scripts/lib/style_profiles.py:166-167`, firequalizer pending). Net: the audio channel of a default render is raw stream audio — and it matches every other clipper's upload of the same moment.

---

## The plan

### P1 — Turn on and harden the audio layer (highest leverage)

1. **Punchline-anchored SFX.** Machinery exists: `sfx_cues` in `scripts/lib/edit_plan.py:22-41` with 5 kinds (`whoosh|impact|scratch|ding|riser`), injected by `sfx_inject.py` under `CLIP_STYLE_PROFILES` (ON). Gap: timing comes from the **vision model's guess** or profile defaults, not acoustics. Add a deterministic cue pass using anchors the pipeline already computes — word-level SRT timestamps, Pass A laughter markers, `crowd_response`/`rhythmic_speech` peaks from [[entities/audio-events]]: `impact` on the payoff word, `riser` 2–3 s before it, `scratch` on fail/awkward beats, `ding` on chat-callout beats. LLM plan as fallback, acoustic anchors as primary. (This is the competitor pattern the owner observed.) **The beat→sound→offset→mix defaults + CC0 sourcing are now researched and JSON-ready in [[concepts/sfx-cue-taxonomy-2026-06]]** — includes the net-new kinds to add (`boom`, `sad_trombone`, `crickets`, `applause`) and a per-kind mix policy (duck most under speech, let the Vine `boom` ride hot on punchlines).
2. **Music bed default-ON.** Tier A (folder convention) and Tier C (librosa BPM/energy matching) are built (`music_pick.py`); CC0 seed packs via `seed_libraries.py` — **verify `assets/` is actually seeded**. A −22 dB bed materially changes the fingerprint and reads as production.
3. **Piper TTS voiceover for the hook.** Stage 6 already generates an 8–14 word creator-POV `voiceover` line with placement; flip `CLIP_TTS_VO` ON. Caveat: `en_US-amy-low` is robotic — fine for an intro hook; voice upgrade (better Piper voice / Kokoro-class local TTS) is a cheap follow-up. Voiceover is the most-cited originality signal in TikTok's own guidance.
4. **Wire `eq_tilt_db`** (pending firequalizer) and consider a bolder micro-treatment (e.g. ±2% tempo on non-speech-critical clips) — supporting, not primary.

### P2 — Structural edits (change the temporal fingerprint AND read as editing)

- Flip `CLIP_JUMP_CUTS` to `gaps` (silence removal) after the pending clean validation run ([[concepts/transition-animations]]). Re-times every downstream frame.
- **Cold-open hook reorder** (new feature, medium effort): prepend a teaser of the most-striking moment before the setup, whoosh + white flash into the start. Buildable as a 2-segment stitch variant on `stitch_render.py`; payoff timestamp already known. **Rules now researched in [[concepts/hook-engineering-2026-06]]: ~1–2 s teaser (heuristic, A/B-test it), TEASE don't spoil (resolution stays at the end), proposition readable by 3 s / hook by 6 s.**

### P3 — Visual engagement sync

Zoom punch ON the punchline frame (exists — anchor to the same acoustic cue as the SFX); meme/B-roll cutaways for funny/reactive (exist, asset-gated); camera-pan Wave E for IRL VODs (+2–4 s/clip).

### P4 — Account-level hygiene (outside the pipeline)

Vary captions/hashtags per clip (Stage 6 already produces unique titles — ensure the posting flow uses them); avoid burst-posting many clips of one VOD; appeal false flags (appeals reportedly succeed and may matter for account standing).

### P5 — Measure it

Add a small `posted.log` (clip → treatments applied → flagged? → views). This becomes a second label stream for [[concepts/plan-calibration-loop]]: fit not just "what's clip-worthy" but "which treatment bundle avoids the flag." Without it, which layer mattered is guesswork.

---

## Research prompts (ready to fire via deep research)

1. ~~**TikTok originality mechanics (2026)**~~ — **DONE 2026-06-12**, filed as [[concepts/tiktok-originality-mechanics-2026-06]] (ranked Tier A/B/C transformation list + the refinement above).
2. ~~**Sound-design pattern library**~~ — **DONE 2026-06-12**, filed as [[concepts/sfx-cue-taxonomy-2026-06]] (beat→sound→offset→mix taxonomy + CC0 sources + JSON drop-in).
3. ~~**Hook engineering**~~ — **DONE 2026-06-12**, filed as [[concepts/hook-engineering-2026-06]] (cold-open teaser rules, caption density, hook-text template library; informs the P2 cold-open reorder + the Stage 6 hook card).

---

## Related

- [[concepts/originality-stack]] — the existing waves A–E this plan extends
- [[concepts/case-incongruity-comedy]] — the competitor reference clips + detection blind spot
- [[concepts/plan-calibration-loop]] — consumes P5's posted.log labels
- [[concepts/transition-animations]] — jump cuts / white flash status
- [[concepts/style-profiles]], [[concepts/asset-libraries]] — SFX/music/meme asset layer
- [[entities/piper]], [[entities/librosa]], [[entities/audio-events]]
