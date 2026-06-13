---
title: "SFX Cue Taxonomy for Short-Form Comedy/Reaction Editing (2026-06 research)"
type: concept
tags: [research, sfx, audio, originality, edit-plan, sound-design, reference, tiktok]
sources: 0
status: shipped
updated: 2026-06-13
---

# SFX Cue Taxonomy (2026-06 research)

Deep-research output: the sound-effect vocabulary high-performing TikTok/Shorts clip channels use, mapped **beat-type → sound-kind → timing offset → mix level**, with CC0/royalty-free sourcing. Commissioned to feed [[concepts/plan-unoriginality-audio-layer]] P1 (punchline-anchored SFX) — the un-perturbed audio channel is the leading suspect for the owner's TikTok "unoriginal" flag, and competitor clips that *do* get reach carry exactly these cues.

> [!success] Shipped 2026-06-13 — acoustic-anchor SFX cues
> The taxonomy is now wired into the render path. **`config/sfx_cues.json`** holds the beat→sound→offset→mix table (the §5 JSON, lightly adapted). **`scripts/lib/sfx_cues.py`** is a deterministic cue builder run in `profile_render.py` (after `_synthesize_plan`, gated `CLIP_SFX_ANCHOR`, default ON; `=0` reverts to the legacy zoom-tied synthesis): it maps the moment's `category` → beat-types (`category_beats`) → the **first sound kind whose `assets/sfx/<kind>/` folder has audio** (`sfx_inject.has_assets`), anchoring on the payoff (moment timestamp), a build-up riser before it, and transcript laughter markers. Each cue carries `gain_db`; **`sfx_inject.build_sfx_layer` now applies per-cue volume** (`_cue_volume`, dB→linear) so a punchline **boom rides hot (~0 dB, at speech)** while most SFX duck under speech — the research's per-kind mix policy. **New kinds** (boom, sad_trombone, sad_violin, crickets, applause, boing, pop, bruh) added to `edit_plan.py` `VALID_SFX_KINDS`. **boom works today** via `assets/sfx/boom/library.json` aliasing the seeded impact library; the other new kinds fall through the priority list to a seeded kind until their own CC0 assets are added (the `category_beats`→`beat_defaults` "first available kind" logic). `emotional` stays silent (payoff=null). **Deferred:** the −14 LUFS `loudnorm` in `global_mix` is documented but not applied (kept out to avoid pumping/perf risk on the existing audio chain). Smoke-tested: funny moment → boom at payoff + laughter; per-kind gain exact (−6 dB → 0.5012).

> [!warning] Methodology + confidence caveat (read first)
> Produced by the `deep-research` workflow across **two runs** (~46 sources, ~218 extracted claims). The 5-search + fetch + claim-extraction phases completed; the **3-vote adversarial verification layer crashed both times on Anthropic session limits**, so almost every claim carries a 0-0 "abstain" verdict that the harness mislabels as "refuted." Those are *not* genuine refutations. **Only 4 claims got real votes before the first crash** — all licensing/inventory facts (marked ✅ VERIFIED below). Everything else is **single-/multi-source practitioner convention**, and confidence here comes from **cross-source agreement**, not the formal panel. Treat timing-frame numbers (esp. from `sfxengine.com`, which cites an unsourced "65%" stat) as **starting defaults to A/B-test**, not measured ground truth. Re-running the verify layer after the limit resets would upgrade these.

---

## 1. Beat → sound-kind map

Confidence: **H** = ≥3 independent sources agree · **M** = 2 sources · **L** = single source / promotional.

| Narrative beat | Canonical sound(s) | Conf | Sources |
|---|---|---|---|
| **Punchline / shock / reveal** | Vine boom (deep bass thud) | H | KYM, voicy, epidemicsound |
| **Punchline (lighter)** | short percussive pop / beep; cartoon "boing" | M | epidemicsound (TikTok) |
| **Fail / mishap / "L"** | record scratch; sad trombone ("wah-wah"); "Mission Failed"; sad violin (ironic mock-sympathy) | H | epidemicsound, uppbeat, voicy, sfxengine |
| **Reveal / payoff** | applause; ascending ding/chime; Vine boom | M | epidemicsound (TikTok) |
| **Transition / hard cut** | whoosh; cartoon boing (fast cut) | H | epidemicsound (TikTok), pixflow, flexclip |
| **Build-up / tension before a beat** | riser / rising whistle | M | sfxengine |
| **Awkward silence / dead air** | crickets; tumbleweed; distant single cough | M | uppbeat, sfxengine |
| **Disbelief reaction** | "bruh" | L | voicy |
| **Hook (first ~3 s of clip)** | the clip's single loudest/most-impactful cue, placed early to stop the scroll | M | epidemicsound |

> [!note] Maps onto the pipeline's existing kinds
> [edit_plan.py](scripts/lib/edit_plan.py) already defines `VALID_SFX_KINDS = {whoosh, impact, scratch, ding, riser}`. The research's `whoosh`→transition, `riser`→build-up, `scratch`→fail, `impact`/`ding`→punchline/reveal already fit. **Net-new kinds worth adding** for the comedy/reaction register: `boom` (Vine boom — the single highest-value one), `sad_trombone`, `sad_violin`, `crickets`, `applause`, `boing`. The bus/George-Bush reference clips ([[concepts/case-incongruity-comedy]]) call specifically for `boom` on the overreaction and `crickets`/`sad_trombone` on the failed push.

---

## 2. Timing offsets (relative to the detected beat)

All values are **starting defaults to tune**, not measured. Frames assume the source/render fps (the pipeline standardizes Stage 7 output to 30 fps, so 1 frame ≈ 33 ms; the sfxengine numbers below were quoted at 24 fps ≈ 42 ms/frame — convert when implementing).

| Sound-kind | Offset vs beat | In seconds | Conf | Source |
|---|---|---|---|---|
| General comedic SFX | +0.2 to +0.5 s **after** | +0.2…+0.5 s | L | sfxengine (promotional stat) |
| Impact / bonk / pop | +2 to +5 frames after | ~+0.08…+0.20 s | L | sfxengine |
| Riser / build-up | **−10 to −30 frames before** | ~−0.4…−1.25 s before | L | sfxengine |
| Sad trombone / realization | +20 to +40 frames after | ~+0.8…+1.7 s | L | sfxengine |
| Crickets / awkward silence | +60+ frames after | ~+2.5 s+ | L | sfxengine |
| Transition whoosh | +1 to +2 frames after the cut | ~+0.03…+0.08 s | M | flexclip |
| Hook cue | within first 3 s of the clip | absolute | M | epidemicsound |

**Practical rule that survives the weak sourcing:** reaction/impact sounds land **on or just after** the beat (the payoff word/laugh/fail); only **risers** precede it. This matches the FunnyNet/FunnyNet-W convention (academic, run 1) that a funny window *ends* at laughter onset — i.e., the reaction cue belongs at the *end* of the detected funny window, not its start. The pipeline already has the anchors to place these: word-level SRT timestamps, Pass A laughter markers, and `crowd_response`/`rhythmic_speech` peaks from [[entities/audio-events]].

---

## 3. Mix levels relative to speech

| Guidance | Value | Conf | Source |
|---|---|---|---|
| SFX sit **below** the speech/voiceover track (default rule) | speech stays dominant | H | epidemicsound, flexclip, premieregal |
| Music bed | ≈ −20 dB | M | krotos |
| Subtle SFX (footsteps/ambience) | ≈ −20 dB | M | krotos |
| Impact SFX (hits/cracks) | ≈ −10 dB (may peak to −8 dB) | M | krotos, wevideo |
| SFX EQ shaping | +3…+6 dB boost in 2–5 kHz, high-pass below 100–150 Hz | L | sfxengine |
| Compression on SFX | 1–5 ms attack, 50–100 ms release; sidechain-duck music under the SFX | L | sfxengine |
| Platform integrated loudness target | ≈ −14 LUFS (TikTok/Reels), achievable via FFmpeg `loudnorm` | M | apu.software, mitz17 |

> [!note] The "loud = funny" exception
> The Vine-boom ironic-edit register deliberately **violates** the duck-below-speech rule — KYM documents the "21st-century humor / loud = funny" convention where the boom is mixed *hot*, at or above speech. So a **per-kind mix policy** beats one global level: duck most SFX under speech (−10…−20 dB), but let `boom` on a punchline ride at/just above dialogue. This is a knob the [[concepts/plan-calibration-loop]] fitter could eventually tune against engagement.

---

## 4. CC0 / royalty-free sources (the verified part)

| Source | License | Attribution | Format | Notes | Status |
|---|---|---|---|---|---|
| **Pixabay** | Pixabay Content License (**not** CC0) | None required | MP3 | 120,000+ SFX; 1,505 "vine boom" hits | ✅ VERIFIED (3-0 / 2-0) |
| **Freesound** (CC0 tag) | CC0 1.0 (per-sound; uploader also offers CC-BY / CC-BY-NC) | None for CC0 | WAV/MP3/etc | 428 SFX under the CC0 tag; **filter to CC0** — CC-BY-NC can't be used in monetized clips | ✅ VERIFIED (3-0 inventory) |
| **Mixkit** | Mixkit own royalty-free license (not CC0) | None; no account | MP3 | commercial OK | sourced, unverified |
| **ZapSplat** | has a CC0 1.0 section + own license tiers | None for the CC0 set | MP3/WAV | free tier exists | sourced, unverified |
| **TunePocket** (Epic Vine Boom) | own license (**not** CC0) | n/a | — | watermarked unless paid — **avoid for automation** | sourced, unverified |

> [!warning] License hygiene for a monetized pipeline
> "Royalty-free" ≠ CC0. Pixabay/Mixkit grant under *their own* licenses (fine for use, but not public-domain and not redistributable as a bundle). For a redistributable seed pack ([[concepts/asset-libraries]] / `seed_libraries.py`), prefer **Freesound CC0** and **ZapSplat CC0** — true public-domain dedication, safe to bundle and ship. **Exclude CC-BY-NC** from any monetized-channel pipeline. The "vine boom" itself is meme-ubiquitous but originates from a copyrighted source clip — use a **CC0 re-creation** from Freesound, not a rip.

---

## 5. JSON cue taxonomy (drop-in for edit_plan.py / sfx_inject.py)

Shape mirrors [edit_plan.py](scripts/lib/edit_plan.py)'s `sfx_cues` (`{t, kind}`) but adds the beat→kind defaults, per-kind offset, and per-kind mix the injector would apply. `offset_s` is added to the beat timestamp; `gain_db` is relative to the ducked source (negative = under speech). Defaults to A/B-test, not gospel.

```json
{
  "version": 1,
  "_note": "SFX cue defaults from 2026-06 deep research. Confidence: licensing VERIFIED; beat/timing/mix are cross-source practitioner convention, not formally verified (adversarial layer crashed on session limits). Tune against engagement once calibration loop exists.",
  "beat_defaults": {
    "punchline":       [{"kind": "boom",         "offset_s": 0.10,  "gain_db": 0.0}],
    "punchline_light": [{"kind": "pop",          "offset_s": 0.08,  "gain_db": -8.0}],
    "fail":            [{"kind": "scratch",      "offset_s": 0.05,  "gain_db": -8.0},
                        {"kind": "sad_trombone", "offset_s": 0.80,  "gain_db": -9.0}],
    "reveal":          [{"kind": "ding",         "offset_s": 0.10,  "gain_db": -8.0},
                        {"kind": "applause",     "offset_s": 0.15,  "gain_db": -12.0}],
    "transition":      [{"kind": "whoosh",       "offset_s": 0.05,  "gain_db": -6.0}],
    "buildup":         [{"kind": "riser",        "offset_s": -1.00, "gain_db": -10.0}],
    "awkward_silence": [{"kind": "crickets",     "offset_s": 2.50,  "gain_db": -12.0}],
    "disbelief":       [{"kind": "bruh",         "offset_s": 0.10,  "gain_db": -6.0}]
  },
  "kind_sources": {
    "boom":         {"label": "Vine boom", "pool": "freesound_cc0", "valid_in_pipeline": false},
    "pop":          {"label": "percussive pop/beep", "pool": "freesound_cc0", "valid_in_pipeline": false},
    "scratch":      {"label": "record scratch", "pool": "freesound_cc0", "valid_in_pipeline": true},
    "sad_trombone": {"label": "sad trombone (wah-wah)", "pool": "freesound_cc0", "valid_in_pipeline": false},
    "sad_violin":   {"label": "sad violin", "pool": "freesound_cc0", "valid_in_pipeline": false},
    "ding":         {"label": "ascending chime/ding", "pool": "freesound_cc0", "valid_in_pipeline": true},
    "applause":     {"label": "applause", "pool": "freesound_cc0", "valid_in_pipeline": false},
    "whoosh":       {"label": "whoosh", "pool": "freesound_cc0", "valid_in_pipeline": true},
    "riser":        {"label": "riser / rising whistle", "pool": "freesound_cc0", "valid_in_pipeline": true},
    "crickets":     {"label": "crickets / tumbleweed", "pool": "freesound_cc0", "valid_in_pipeline": false},
    "bruh":         {"label": "'bruh' vocal", "pool": "freesound_cc0", "valid_in_pipeline": false},
    "impact":       {"label": "generic impact/bonk", "pool": "freesound_cc0", "valid_in_pipeline": true}
  },
  "global_mix": {
    "duck_speech_db": -6.0,
    "loudnorm_target_lufs": -14.0,
    "hot_kinds": ["boom"]
  }
}
```

`valid_in_pipeline: false` = the kind isn't in `VALID_SFX_KINDS` yet; adding `boom`/`sad_trombone`/`crickets`/`applause`/`boing`/`pop`/`bruh` to [edit_plan.py](scripts/lib/edit_plan.py) + seeding the CC0 assets is the implementation step.

---

## 6. How this plugs into the pipeline

1. **Anchor beats acoustically, not by LLM guess** — the current `sfx_cues` come from the vision model's `edit_plan` JSON. Replace/augment with deterministic anchors the pipeline already computes: payoff word (word-level SRT), Pass A laughter markers, `crowd_response`/`rhythmic_speech` peaks ([[entities/audio-events]]). Beat-type → use the moment's `primary_pattern`/`primary_category` to pick the row above (e.g. `fail`/`reactive` → scratch+trombone; `hype` → boom).
2. **Per-kind mix policy** in [sfx_inject.py](scripts/lib/sfx_inject.py) — duck most kinds under speech, let `hot_kinds` ride; run a final `loudnorm` to −14 LUFS.
3. **Seed CC0 assets** via [[concepts/asset-libraries]] / `seed_libraries.py` into `assets/sfx/<kind>/`, Freesound-CC0 + ZapSplat-CC0 only.
4. **Defaults, then fit** — every offset/gain here is a starting guess; once [[concepts/plan-calibration-loop]] has outcome labels, these become fittable constants.

---

## Sources

Primary/licensing (best verified): [Pixabay SFX](https://pixabay.com/sound-effects/), [Pixabay vine-boom search](https://pixabay.com/sound-effects/search/vine%20boom/), [Freesound CC0 tag](https://freesound.org/browse/tags/cc0), [Freesound FAQ](https://freesound.org/help/faq/), [Mixkit](https://mixkit.co/free-sound-effects/), [ZapSplat CC0](https://www.zapsplat.com/license-type/cc0-1-0-universal/).
Convention/practitioner: [Epidemic Sound — meme SFX](https://www.epidemicsound.com/youtube/meme-sound-effects/), [Epidemic Sound — TikTok SFX](https://www.epidemicsound.com/tiktok/tik-tok-sound-effects/), [Uppbeat meme SFX](https://uppbeat.io/blog/sound-effects/meme-sound-effects), [Voicy meme sounds](https://blog.voicy.network/memes/sounds/top-meme-sound-effects-for-editing/), [KnowYourMeme — Vine boom](https://knowyourmeme.com/memes/vine-thud-boom-sound-effect), [SFXEngine timing](https://sfxengine.com/blog/sound-effects-timing-in-comedy-videos), [FlexClip transitions](https://www.flexclip.com/learn/transition-sound-effects.html), [Krotos mix balance](https://krotos.studio/blog/how-to-balance-music-and-sound-effects), [WeVideo audio levels](https://www.wevideo.com/blog/how-to-set-audio-levels).
Academic: [FunnyNet (ACCV 2022)](https://openaccess.thecvf.com/content/ACCV2022/papers/Liu_FunnyNet_Audiovisual_Learning_of_Funny_Moments_in_Videos_ACCV_2022_paper.pdf), [FunnyNet-W (arXiv 2401.04210)](https://arxiv.org/pdf/2401.04210).

## Related
- [[concepts/plan-unoriginality-audio-layer]] — the plan this research feeds (P1 punchline-anchored SFX)
- [[concepts/case-incongruity-comedy]] — the reference clips that need boom/crickets/trombone cues
- [[concepts/style-profiles]] / [[concepts/asset-libraries]] — where SFX assets + the injector live
- [[entities/audio-events]] — the acoustic anchors for beat timing
- [[concepts/plan-calibration-loop]] — turns these defaults into fitted constants
