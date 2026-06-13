---
title: "Hook Engineering for Storytime/Informative Clips (2026-06 research)"
type: concept
tags: [research, hooks, retention, captions, cold-open, storytime, informative, reference]
sources: 0
status: reference
updated: 2026-06-12
---

# Hook Engineering (2026-06 research)

Deep-research output answering research prompt #3 of [[concepts/plan-unoriginality-audio-layer]]: what opening-second patterns maximize retention for 60–180 s storytime/informative clips, as implementable rules for the clipper (teaser length, where to cut it from, hook-text template library by category).

> [!note] Methodology — verified cleanly; most numbers got killed
> Full pipeline ran (no crash): 20 sources → 82 claims → 25 adversarially verified → **9 confirmed / 16 refuted** → 6 findings. **The headline methodological result: adversarial review knocked out (0-3) nearly every specific numeric retention threshold** as un-sourced blog folklore. What survived is a small set of platform-official anchors + qualitative technique. Source quality is mixed — only the TikTok-ads anchors rest on a primary source; the technique/templates come from creator/vendor blogs (opus.pro/OpusClip and fluxnote.io both *sell* AI hook tools), so treat templates as "documented and usable," not "each empirically proven."

---

## What survived verification (use these)

| Finding | Conf | Source |
|---|---|---|
| **Hook within first 6 s; content proposition within first 3 s** | HIGH (3-0) | TikTok ads creative best-practices (primary) |
| **On-screen text density 5–10 words/sec** (caption pacing ceiling) | HIGH (3-0) | TikTok ads (primary) |
| **Cold-open / payoff-teaser improves retention** — lead with the result/most striking moment, viewer knows within ~2 s what they'll get; zero setup | HIGH (3-0 / 2-1) | opus.pro, wistia, dynamoi, socialync |
| **Critical refinement: TEASE the climax, don't SPOIL it** — preview a snippet to open a curiosity loop; keep the full payoff at the end. Over-resolving early lets viewers leave satisfied and the algorithm penalizes under-delivered teasers | HIGH (verifier-unanimous) | socialync (Hook→Body→Payoff), snshelper, conbersa |
| **Category-organized first-line hook templates** are well-documented + directly usable | HIGH (3-0) | fluxnote, socialync (+ recurring across 6 sources) |
| **Early swipe-away (within 1–2 s) = weak hook** — the one surviving *metric*, grounded in YouTube Studio's real "Viewed vs. Swiped Away" Shorts analytic | MED (2-1) | reelrise + YouTube support |

> [!warning] Do NOT hard-code these — all refuted 0-3 as folklore
> "+20% retention for payoff in first 15 s"; "first 3 s outperform by 30–40%"; "pattern interrupt every 4 s → 58% vs 41%"; "65–70% 3-second-retention threshold"; "70%+ retention → 2.2–7× views"; "algorithm decides at ~1.5 s"; "avg retention 30–40% for 60s–3min"; hook-rate "25% minimum" (1-2, unconfirmed). **Any retention target the clipper uses must be derived from the user's own channel analytics, not imported from these blogs.**

---

## Implementable rules for the clipper

### Cold-open teaser (informs [[concepts/plan-unoriginality-audio-layer]] P2 reorder)
- **Place a short teaser of the clip's most striking moment as the opening**, before the setup. The viewer should know within ~2 s what they'll get.
- **Teaser length: ~1–2 s flash** — but this is a **heuristic default to A/B-test**, not an evidence-backed constant (sources give the 2s/3s/6s *windows* but explicitly do *not* prescribe a teaser duration; the one numbered version was refuted).
- **Tease, don't resolve.** Cut the teaser from the climax/peak but **do not include the resolution** — the payoff stays at the end (Hook→Body→Payoff). This is the single most important nuance; it overrides any naive "front-load the whole climax."
- **Where to cut it from**: the clip's peak/most-striking moment. The pipeline already locates this — the moment `timestamp` (payoff-centered), or the `crowd_response`/`rhythmic_speech`/laughter peak from [[entities/audio-events]]. (Sources give no automatable "find the striking moment" rule — this is the clipper's own peak-detection job. Open question.)

### Caption pacing (informs [[concepts/captions]])
- **On-screen text ≤ 5–10 words/sec.** The CapCut word-box captions already animate at speech rate, which is consistent with this; the rule matters most for the **hook card** — keep it readable, not a wall of text.
- Proposition (what the clip is about) must be **readable by the 3 s mark**; hook lands by 6 s.

### Hook-text template library (for the Stage 6 hook card)
Map the moment's `primary_category` → a template; fill slots from the title/transcript. Templates are documented + usable, not individually proven — rotate and let the future [[concepts/plan-calibration-loop]] fitter learn which land.

```json
{
  "version": 1,
  "_note": "Hook-card first-line templates by category (2026-06 research). Documented/usable, not individually retention-proven. Fill [slots] from the moment title/transcript. Keep <= the hook card's word budget; readable by the 3s mark.",
  "storytime": [
    "This is the story of how I [outcome]",
    "Last week something happened that changed everything",
    "POV: you're [relatable scenario]"
  ],
  "informative": [
    "Here's how to [outcome] in [short time]",
    "The reason [common thing] is actually [unexpected]",
    "I was today years old when I learned [fact]",
    "[N] [things] — #2 [open-loop withhold]"
  ],
  "hot_take": [
    "Most people get this wrong",
    "Unpopular opinion: [take]",
    "I bet you didn't know this about [topic]"
  ],
  "authority": [
    "As a [role] with [X] years experience"
  ],
  "funny": [
    "_lead with the catchiest moment, no setup (front-load rule)_"
  ]
}
```

The patterns: **open loop / curiosity gap** ("nobody talks about this, but…"), **numbered promise with a withheld item** ("3 apps — #2 nobody knows about"), **authority** ("as a [role]…"), **stakes/challenge** ("most people get this wrong"). These recur across all 6 template sources, so they're standard, not proprietary.

---

## Open questions
1. **Evidence-backed teaser length** — sources give opening *windows* (2s/3s/6s), not a teaser duration. The ~1–2 s flash is a heuristic; A/B-test on the clipper's own output.
2. **Auto-locating the "most striking moment"** to cut the teaser from — no automatable rule in sources; relies on the clipper's peak/climax detection.
3. **Per-platform / per-category transfer** — most quantified evidence is TikTok-ads-centric; storytime vs informative and TikTok vs Shorts vs Reels differences are unquantified.
4. **Actual retention targets** — the common 65%/70% benchmarks were all refuted; a defensible target must come from the user's own analytics.

---

## Sources
Primary: [TikTok ads — creative best practices](https://ads.tiktok.com/help/article/creative-best-practices?lang=en) (6s/3s windows, 5–10 words/sec), [YouTube Studio Shorts "Viewed vs Swiped Away"](https://support.google.com/youtube) (the surviving metric).
Technique/templates (creator/vendor blogs): [opus.pro — hook formulas](https://www.opus.pro/blog/tiktok-hook-formulas), [opus.pro — hooks that go viral 2026](https://www.opus.pro/blog/tiktok-hooks-that-go-viral-2026), [Wistia — cold opens](https://wistia.com/learn/production/what-your-video-series-can-learn-from-these-cold-opens), [socialync — short-form structure 2026](https://www.socialync.io/blog/short-form-video-structure-guide-2026), [fluxnote — 47 hooks](https://fluxnote.io/blog/best-hooks-for-viral-short-form-video), [dynamoi — 3-second rule](https://dynamoi.com/learn/tiktok-music-promotion/what-is-the-3-second-rule-on-tiktok), [reelrise — viewed vs swiped](https://reelrise.app/guide/viewed-vs-swiped-away-the-only-youtube-shorts-metric-that-matters/).

## Related
- [[concepts/plan-unoriginality-audio-layer]] — research prompt #3; the cold-open teaser feeds P2 reorder
- [[concepts/plan-youtube-informative]] — storytime/informative retention is core to the long-form work
- [[concepts/captions]] — hook card + caption density (5–10 words/sec)
- [[concepts/tiktok-originality-mechanics-2026-06]] — restructuring/added-framing is also Tier A originality
- [[concepts/plan-calibration-loop]] — would learn which hook templates/teaser lengths actually land
