---
title: "Missed-clip case study: rap battle on a 'gaming' VOD (rakai 2026-04-24)"
type: concept
tags: [case-study, tuning, pass-a, pass-b, segment-detection, audio-events, freestyle, rap, missed-clip, hub]
sources: 1
updated: 2026-06-04
---

# Case study: the Delaware freestyle that didn't get clipped

A high-quality rap-battle / freestyle moment in [vods/.transcriptions/20260424_2xRaKai_2756365448.transcript.srt](vods/.transcriptions/20260424_2xRaKai_2756365448.transcript.srt) that the pipeline missed entirely. Documented 2026-06-04 to drive Pass A keyword + Stage 3 segment + Pass B prompt tuning so similar moments are caught in future runs.

> [!note] Generalized 2026-06-12
> [[concepts/case-incongruity-comedy]] reframes this miss as one instance of a single architectural gap — **cross-channel incongruity** (rhythm here; prosody/motion in the owner's competitor reference clips) — and proposes one anomaly-proposer lane that subsumes this page's unshipped recs (phonetic rhyme density, verbal-duel detector). Also note: after this page's quick wins shipped, the 2026-06-05 re-run **detected** the moment (Pass B 0.878, cross-validated) but **Pass C dropped it** (axis 1.05 vs 1.55 on a blander competitor) — the deferred rare-pattern bonus and [[concepts/plan-calibration-loop]] are the remaining fixes.

---

## The segment

**Location**: T=654-695s (10:54 → 11:35). About 41 s — perfect clip length.

```
10:52   "Kill him with words"                            ← setup / hype signal
10:54   "Where you from?"
10:56   "Long Beach"
10:59   "You ain't from Long Beach."
11:00   "I'm well aware you're from Delaware."           ← hook line
11:01   "What the hell is there?"
11:03   "Open fields and hella dead"
11:04   "Where I'm from, it rains, it snows, it's hell in there"
11:08   "You don't gotta jump him!"
11:10   "This ass-whippin' is hella fair!"
11:12   "I'll bring the weapon in!"
11:13   "I ain't Obama, but Rock won't leave one Miss Shelling in!"
11:19   "That shit was marijuana!"
11:21-31 "Kill him again!  Kill him again!  Kill him again."  ← crowd reaction loop
11:33   "So take a seat, busboy."
11:35   "You should have taken the fuck home."           ← clean end
```

Pattern: rhymed bars + crowd hype + clean call-and-response setup/payoff. Dense multi-syllable rhyme chain (aware/Delaware/there/fair/Shelling-in) and rhythmic delivery distinct from surrounding conversation.

---

## Diagnosis — three independent failures stacked

### 1. Pass A: zero keyword moments in the rap-battle window

Checked `work/keyword_moments.json` for the morning 2026-06-04 run on this VOD:

| T (s) | Time | Score | Categories | Preview |
|---|---|---|---|---|
| 606 | 10:06 | 0.000 | hype/funny/emotional/controversial | "This guy. Yo, that shotgun is insane..." |
| 626 | 10:26 | 0.000 | (same) | "There's no way, bro..." |
| **654-695** | **10:54-11:35** | — | — | **NO KEYWORD MOMENTS** |

Pass A's keyword categories don't include rap-battle / freestyle vocabulary. None of "kill him with words," "spit," "bars," "rap me," "drop a verse," "go again," "from Delaware" triggered. The Tier-2 M2 [[entities/audio-events]] boosts (`rhythmic_speech` → dancing/hype; `crowd_response` → funny/hype) **couldn't compensate either** — they only run on top of an existing keyword hit, not as a primary signal.

### 2. Stage 3: classified the whole VOD as "gaming"

`work/stream_profile.json`:
```json
{
  "dominant_type": "gaming",
  "dominant_pct": 100.0,
  "type_breakdown": { "gaming": 100.0 }
}
```

So Pass B used **gaming-segment prompts** for the entire stream — looking for clutch plays, rage quits, skill moments. Rap battles don't fit the gaming template.

This is correct in aggregate (the stream IS primarily gaming) but wrong locally — IRL segments like this rap battle are mis-prompted. There's no facility for **per-segment-type local windows** within an otherwise-gaming stream.

### 3. Pass B: Chunk 3 found one unrelated moment

`work/pipeline.log` lines 185-187:
```
Chunk 3 (610s-910s): gaming, 417 words...
Chunk 3: found 1 moments
  T=845s [hot_take] score=0.889 — Pattern setup_external_contradiction: Streamer's earlier cla...
```

Pass B ran on the chunk containing the rap battle (610-910s spans T=654-695 entirely) and found **one** moment at T=845s about something else — a "streamer's claim is later contradicted" pattern. **The rap battle was not detected at all.**

Pass B's gaming-prompt pattern types cited in this run include:
- `setup_external_contradiction`
- `informational_ramble`
- (likely) `clutch_play`, `rage_moment`, etc.

None of these patterns describe "rap battle / freestyle / verbal duel." The LLM had no template to match, and the rap battle's structure (rhymed bars + crowd hype) doesn't trivially fall out of "find a story arc with payoff" without an explicit cue.

### 4. Audio events: skipped on this run (no_audio_source)

Bonus finding: `work/audio_events.json` is empty (`skipped_reason: "no_audio_source"`). This run used a **cached transcript** (transcript.json had been computed previously), and the pipeline's caching path doesn't extract audio.wav fresh — so the audio-events scanner sees no audio file and writes an empty stub. Even if Pass A had a rap-battle keyword, the audio_events boost wouldn't have fired on this re-run.

This is a separate issue worth fixing: cached-transcript runs should still extract audio for Tier-2 detectors that need it. See [[entities/audio-events]] §Skipped reasons.

---

## Characteristics worth detecting (general "freestyle / verbal duel" pattern)

Any pipeline tuning to find similar clips should look for **the combination** of:

| Signal | Detection path | Threshold |
|---|---|---|
| **Rhyme density** (≥3 multi-syllable rhymes in a 20 s window) | Phonetic encoder over transcript words (metaphone / soundex). Pass A could compute this cheaply. | ≥3 matching end-codas in 20 s |
| **Rhythmic delivery** (regular onset intervals) | `audio_events.rhythmic_speech` already does this — purpose-built for freestyles | ≥ 0.7 (existing threshold) |
| **Crowd reaction loop** (repeated short hype phrases by multiple voices) | `audio_events.crowd_response` + transcript regex on "kill him again," "ohhh," "go in" | ≥ 0.5 (existing threshold) |
| **Call-and-response setup** ("Where you from?" / "What's your name?" → punchline) | Pass B prompt pattern: Q→A→twist | needs new pattern |
| **Hype trigger phrases** ("kill him with words", "spit," "bars," "drop a verse") | Pass A keyword list extension | category boost in `funny`/`hype`/`dancing` |
| **Verbal-duel structure** (two voices alternating in tight 1-3 s bursts over ≥30 s) | Diarization signal ([[entities/diarization]]) | ≥2 speaker turns/sec sustained |

The Delaware case had **at least 5 of these 6 signals present simultaneously** — but the pipeline detected none of them because none of the detection paths exist yet.

---

## 2026-06-05 — verification run + Pass C dropout discovery

The rakai VOD was re-run 2026-06-05 11:31 (commit `dad3596`, all three structural fixes in place). Verified end-to-end:

- ✅ Pass A surfaces the rap-battle window — 5 keyword hits at T=619/639/669/699/719, all categorised `dancing` (the new vocabulary)
- ✅ Pass B identifies the pattern — `T=654 pattern=rap_battle_freestyle score=0.878`, citing the exact "Where you from? / Long Beach / I'm well aware you're from Delaware" call-and-response from the enhanced pattern signature
- ✅ Stage 3 segmentation now multi-segment (T=0-1264s is `just_chatting`, not 100% `gaming` as before)
- ❌ **But Pass C selection dropped it.** T=654 (Pass B 0.878, cross-validated to normalized 1.000) lost bucket 0 to T=1828 ("Dirty Booty Ass Confession") whose Pass B score was the bucket's LOWEST at 0.433 — a 2.03× ranking inversion.

The diagnosis: **axis multipliers (arc / reaction / baseline / engagement) compounded to ~1.55× on T=1828 vs ~1.05× on T=654**. T=1828's `irl` segment + cross-validation + funny category triggered baseline-contrast and other axes; T=654's `just_chatting` + rare `rap_battle_freestyle` pattern triggered almost nothing on those axes. The rare-pattern detection works, but the rare-pattern scoring isn't compensated for absent axis support.

Surfaced via the new **`logtool selection`** subcommand (shipped 2026-06-05) which dumps every Pass C deduped candidate's full scoring chain to `{TEMP_DIR}/pass_c_candidates.json`. See `concepts/pipeline-optimizations-2026-06.md §Phase 1`.

The next fix is a **rare-pattern bonus** that compensates the axis gap for patterns we know are rare-but-clip-worthy (rap_battle_freestyle, interview_revelation, social_callout). Tracked as Phase 2 in the optimization sweep.

## Concrete tuning recommendations (ranked by ROI)

> [!success] Quick wins shipped 2026-06-04
> Items 1, 2 and 3 below are now implemented (commit pending). Items 4-6 remain as documented future work.

### Quick wins (low effort, high impact)

1. ✅ **SHIPPED — Pass A rap-battle keywords** added to `KEYWORD_SETS` in `scripts/lib/stages/stage4_moments.py`:
   - **dancing** (+12 phrases): "kill him with words", "kill him again", "drop a verse", "with the gun talk", "let me cook", "rap battle", "freestyle", "go in", "round 2", "go again", "spit some bars", "bars on bars"
   - **controversial** (+6 phrases for social_callout pattern): "look at this guy", "look at this dude", "this dude is", "you see that", "did you see that", "watch this guy"
   - **storytime** (+8 phrases for interview_revelation pattern): "wait so tell me", "what really happened", "be honest with me", "i want to know", "you can tell me", "off the record", "between us", "the real story"
   Conservative selection — only 3+ word phrases or unmistakeable context — to avoid false positives. Verified single dict, no duplicate keys, AST OK.

2. ✅ **SHIPPED — Pattern catalog enhancement** in `config/patterns.json`. The `rap_battle_freestyle` pattern existed but its signature was audio-centric ("music dominance is high"). Added **TRANSCRIPT MARKERS** the LLM can use when audio backend signal is unavailable (e.g. cached-transcript re-runs where audio_events was empty): (a) ≥3 consecutive end-rhymes in ~20s, (b) call-and-response setup + metered punchline, (c) hype-shout interjection loop, (d) clusters of short metered sentences. Also added `audio:rhythmic_speech>=0.7` and `end_rhyme_chain` to the structural signals, plus a worked example using the Delaware transcript itself.

3. ✅ **SHIPPED — Cached-transcript audio extraction** in `scripts/pipeline/stages/stage2.py`. The cached-transcript branch now also extracts `audio.wav` from the source VOD so the Tier-2 M2 audio_events scanner has its input. Pre-fix, cached re-runs wrote `{"skipped_reason": "no_audio_source"}` and silently disabled rhythmic_speech / crowd_response / music_dominance signals on every re-run — one of the three failures stacked behind the Delaware miss.

### Medium effort

4. **Phonetic rhyme-density signal in Pass A** — `metaphone` (CPython stdlib) over the transcript word-time list, sliding 20 s window, count matching end-codas. Add as a new Pass A signal alongside the existing audio_events boosts. Threshold ≥3 hits in 20 s → +1 to funny/hype/dancing categories.

5. **Per-segment-type local windows** — even on a "gaming" dominant stream, allow Stage 3 to mark sub-segments as IRL/just_chatting/reaction within the gaming flow. Then Pass B can switch prompts mid-stream. Currently Stage 3 emits one segment for the entire VOD (`[{start: 10, end: 4272, type: "gaming"}]`) which forces a single prompt template for everything.

### Larger work

6. **Diarization-driven verbal-duel detector** — Tier-2 M1 [[entities/diarization]] already produces speaker labels. Add a derived signal: "≥2 speaker turns/sec sustained for ≥30 s" → boost-only signal for verbal-duel patterns (rap battles, debates, comedy bits).

---

## Verification on the rakai run

After Pass A keyword + Pass B prompt tuning, re-run on the same VOD and confirm:
- Pass A finds ≥1 keyword moment in T=640-700s window
- Pass B Chunk 3 detects T=660s ± 30s as a freestyle / rap-battle pattern with score ≥ 0.7
- The moment survives Pass C ranking and lands in `scored_moments.json`
- Stage 5.5 Vision Judge tournament places it in the top half (clip-worthy)

If the audio is also re-extracted (or this is a fresh transcription run), the audio_events scanner should report `rhythmic_speech ≥ 0.7` and `crowd_response ≥ 0.5` for the T=650-680s window — strong cross-validation.

---

## Related

- [[concepts/highlight-detection]] — Pass A/B/C/D detection stages
- [[concepts/segment-detection]] — Stage 3 single-segment-per-VOD limitation
- [[entities/audio-events]] — Tier-2 M2 rhythmic / crowd / music signals (need cached-run fix)
- [[entities/diarization]] — Tier-2 M1 speaker labels (basis for verbal-duel detector)
- [[concepts/clipping-pipeline]] — full pipeline ordering
