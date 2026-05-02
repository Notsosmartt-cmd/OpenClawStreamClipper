# Moment Discovery Upgrade Plan

**Created:** 2026-04-27
**Companion doc:** [IMPLEMENTATION_PLAN.md](./IMPLEMENTATION_PLAN.md) (Phases 0–5)
**Status:** all items unstarted; this is a planning artifact, not a status tracker

---

## 1. Executive summary

The current pipeline catches **local** clip-worthy moments well (a single funny line, a hype reaction, a hot take inside one 5-min chunk), but it systematically misses three classes of moments that are arguably the highest-value content on a livestream:

1. **Long-range setup–payoff arcs** — a claim made minutes ago becomes ironic / contradicted later. Example: streamer pitching a "how to buy a penthouse" course, then someone off-camera says *"this is [someone else's] penthouse"* → the streamer pivots and admits it. The setup and payoff are 10+ minutes apart and span chunks. The current pipeline processes each chunk independently and cannot see the connection.
2. **Multi-modal events** — freestyles, off-screen interruptions, audience reactions, music performances. The current pipeline reads only the Whisper transcript; speaker changes, rhythmic delivery, crowd response, and visual events (the friend's face appearing off-frame) are invisible.
3. **Narrative continuity** — long storytimes that exceed a single chunk's window or get cut mid-arc by the 90-second clip cap.

This plan proposes a **layered upgrade** in three tiers — quick prompt/config wins, medium-cost feature additions, and architectural changes — that progressively close those gaps. Each item below documents the **why**, the **how** (with file paths and approximate line numbers), the **validation criterion**, and the **risk profile**.

The plan is sequenced for ROI per engineering hour: **Tier 1 ships first**; Tier 2 should not start until Tier 1's effect on null-rate and clip diversity is measurable; Tier 3 requires Tier 2's signals to be valuable.

---

## 2. Framework — the four signals every clip carries

Every nuanced clip-worthy moment encodes four signals. The pipeline currently captures only the first two.

| # | Signal | What it is | Current status | Who owns it after this plan |
|---|---|---|---|---|
| 1 | Local energy | Word density, exclamations, laughter, chat burst | ✅ Pass A keyword + chat features | Pass A (unchanged) |
| 2 | Local narrative | Setup-payoff inside a 5-min window | ✅ Pass B prompt asks for it | Pass B (Tier-1 prompt upgrade) |
| 3 | Long-range narrative | Setup-payoff spanning 5–60 min | ❌ Each chunk is processed independently | Tier-1 Q1 (cheap), Tier-2 M3 (full), Tier-3 A1 (best) |
| 4 | Multi-modal events | Speaker change, music/rhythm, crowd response, off-screen voice | ❌ Only text via Whisper | Tier-2 M1, M2 |

Every fix below maps to one or more of these signals. The Lacy penthouse moment requires #3 and #4 simultaneously and is the canonical test case for whether the upgrade succeeded.

---

## 3. Tier 1 — Quick wins (≤ 1 day each, low risk, no new dependencies)

These five items are all changes inside `scripts/clip-pipeline.sh` and `config/`. None require new ML models, new packages, or schema changes. They can ship as a single coherent patch.

### Q1 — Inject prior-chunk summaries into the Pass B prompt

**Signals:** #3 (long-range narrative)

**Why.** Pass B today sees only its own 5-minute chunk. Any setup that happened in a previous chunk is invisible. The Lacy penthouse moment depended on a setup that occurred ~10 minutes before the payoff; Pass B couldn't see it. Adding 2–3 lines of prior-chunk context lets the LLM write a "why" that names the contradiction and lets it judge whether THIS chunk's content is interesting *because of* what came before.

**How.** In `scripts/clip-pipeline.sh::Pass B chunk loop` (~line 1564):

1. After parsing each chunk's moments, ask the LLM for a one-sentence summary of the chunk: `"Summarize the streamer's main claim, topic, or activity in this chunk in 15 words or less. Output a single quoted line, nothing else."` Use a separate `call_llm()` call with `max_tokens=80`. Cache as `chunk_summary[chunk_count]`.

2. At the start of each chunk's Pass B prompt construction (~line 1517 where `prompt = f"""/no_think ...` is built), prepend a `prior_context_block`:

   ```python
   prior_context_block = ""
   if chunk_count >= 2 and chunk_summaries:
       recent = chunk_summaries[-2:]
       prior_context_block = (
           "\nEarlier in this stream:\n"
           + "\n".join(f"  • ({(chunk_count - len(recent) + i + 1)}/{total_chunks}) {s}" for i, s in enumerate(recent))
           + "\nLook for SETUP–PAYOFF arcs where something the streamer said earlier "
             "is now contradicted, fulfilled, or referenced. These callbacks are "
             "the highest-value clips. Mention the callback explicitly in 'why'.\n"
       )
   ```

3. Inject `prior_context_block` into the prompt template right after `STYLE: {style_hint}\n{chat_context_block}`.

**Cost.** 1 extra small LLM call per chunk (~2 s on Gemma 4-26B with max_tokens=80). On a 3-hour VOD with 37 chunks, that's ~74 s additional Pass B time.

**Validation.**
- Look for "callback", "contradicts", "earlier they said", "referenced" in Pass B "why" outputs across at least one VOD with known callback-worthy content.
- Re-run on the Lacy penthouse VOD and check whether the moment surfaces with a callback-naming "why".

**Risk.** Low. Worst case the prior_context_block is noise the LLM ignores — falls through to current behavior.

---

### Q2 — Few-shot examples in the Pass B prompt

**Signals:** #2 (local narrative), #3 (when paired with Q1)

**Why.** The current Pass B prompt is a wall of prose explaining what setup–payoff and irony look like. LLMs (especially smaller ones with thinking-leakage like Gemma 4) follow few-shot examples *much* more reliably than prose instructions. The current prompt at `clip-pipeline.sh:1519` already uses the Lacy penthouse moment as a verbal example — but it's buried in paragraph form. Three explicit `transcript → JSON moment` examples will dominate the LLM's behavior.

**How.** In `scripts/clip-pipeline.sh::Pass B prompt template` (~line 1519), add an `EXAMPLES` block right before `Transcript (timestamps MM:SS from stream start):`. Three examples covering:

1. **Setup–payoff with off-screen voice (the Lacy archetype)**
   - Sample transcript: streamer says "this is my penthouse, here's how to get one with my course" → off-screen voice "this is MY penthouse" → streamer "oh yeah this is [name]'s penthouse"
   - Expected JSON: `{"time": "MM:SS_payoff", "start_time": "MM:SS_setup", "end_time": "MM:SS_after_admission", "score": 9, "category": "controversial", "why": "Streamer pitching a course on getting wealthy in 'his' penthouse — friend off-screen exposes that it's not his, streamer admits it on the spot."}`

2. **Long-form storytime with payoff**
   - Sample: "let me tell you about the time I…" 90-second narrative → punchline at the end
   - Expected JSON with `category: storytime`, `start_time` at the "let me tell you", `end_time` after the payoff, ~90 s duration

3. **Hot take with audience pushback**
   - Sample: streamer makes a controversial claim, chat reacts strongly, streamer doubles down
   - Expected JSON with `category: hot_take`, `score: 8-9`

**Cost.** Prompt grows by ~600 tokens. On Gemma 4-26B with the existing 32K context, this is negligible.

**Validation.**
- Compare moment counts and category distributions before/after on the same VOD. Expect: more `storytime` moments (currently rare), more `controversial` with explicit setup-payoff "why" texts.
- Spot-check 5 moments with the new prompt vs the old: are the "why" texts more specific?

**Risk.** Very low. Worst case the model anchors to the example *too* hard and produces clones — mitigated by using diverse examples covering different categories.

---

### Q3 — Drop the keyword ceiling for narrative-rare phrases

**Signals:** #1 (local energy)

**Why.** `KEYWORD_CEILING = 0.75` at `clip-pipeline.sh:1801` caps every keyword-only moment. This was the right call for high-noise phrases (`bruh`, `lmao`, `bro`) — but it punishes high-signal phrases like `"let me tell you"`, `"so basically"`, `"true story"`, `"unpopular opinion"`. A cluster of 3+ of those phrases inside 30 s is *very* high signal and almost never filler. Currently a strong storytime indicator phrase cluster maxes at 0.75, while a generic LLM-only moment can hit 1.0.

**How.** In `scripts/clip-pipeline.sh::all_moments construction` (~line 1801):

1. Define a per-category ceiling map:
   ```python
   KEYWORD_CEILING = {
       "hype": 0.75, "funny": 0.70, "reactive": 0.75, "dancing": 0.70,
       # Categories where keyword phrases are RARE and semantically specific:
       "storytime": 0.90, "hot_take": 0.85, "emotional": 0.85, "controversial": 0.85,
   }
   ```
2. Replace `m["normalized_score"] = min(m["score"], KEYWORD_CEILING)` with:
   ```python
   ceiling = KEYWORD_CEILING.get(m.get("primary_category", "hype"), 0.75)
   m["normalized_score"] = min(m["score"], ceiling)
   ```

**Cost.** None. ~5-line config change.

**Validation.** Count keyword-only `storytime` and `hot_take` moments selected before/after. Expect more storytime picks in `just_chatting` segment streams.

**Risk.** Low. Could over-promote noisy storytime keyword fires; mitigated by Pass C cross-validation rate (most still need LLM agreement).

---

### Q4 — Variable chunk size by segment type

**Signals:** #2 (local narrative), #3 (mid-range narrative)

**Why.** Storytimes and arguments often run 4–8 minutes; the uniform 5-minute chunk size at `clip-pipeline.sh:1461` (`CHUNK_DURATION = 300`) cuts those arcs in half. Reactions and gameplay moments are 30 s–2 min; 5 min is unnecessarily large there. Sizing chunks to the segment type is more honest about what kind of arc the LLM should look for.

**How.** In `scripts/clip-pipeline.sh::Pass B chunk loop` (~line 1460), replace the constant with a per-segment table:

```python
CHUNK_DURATION_BY_SEGMENT = {
    "just_chatting": 480,   # 8 min — storytimes need room
    "irl":           480,
    "debate":        360,   # 6 min — argument arcs
    "reaction":      300,   # 5 min — current default
    "gaming":        300,
}
CHUNK_OVERLAP_BY_SEGMENT = {
    "just_chatting": 60,    # bigger overlap for longer chunks
    "irl":           60,
    "debate":        45,
    "reaction":      30,
    "gaming":        30,
}
```

Inside the loop, look up the chunk's midpoint segment_type FIRST, then pick the chunk window:
```python
seg_type = get_segment_type(chunk_start + 150)  # peek at +2.5 min as guess
CHUNK_DURATION = CHUNK_DURATION_BY_SEGMENT.get(seg_type, 300)
CHUNK_OVERLAP = CHUNK_OVERLAP_BY_SEGMENT.get(seg_type, 30)
```

Note: this changes the relationship between chunk_count and total_chunks — recompute `total_chunks` lazily by walking the timeline once at the top.

**Cost.** Slight increase in Pass B time on `just_chatting`-heavy streams (longer chunks → longer prompts → slightly slower per call, but FEWER chunks total). On a 3-hour `just_chatting` stream: 37 chunks @ 5 min → ~22 chunks @ 8 min. Net Pass B time: roughly the same.

**Validation.** Storytime moments per VOD should rise; storytime moments cut at the 90-s clip cap should fall.

**Risk.** Medium. A `just_chatting` stream that drifts into `gaming` mid-chunk gets a less-appropriate chunk size for the latter part. Mitigated by using `chunk_start + 150` (chunk midpoint) as the segment-type query, not the start.

---

### Q5 — Allow longer clips for storytime/emotional categories

**Signals:** #2 (local narrative)

**Why.** The Pass B prompt (`clip-pipeline.sh:1558`) currently says *"Maximum clip: 90 seconds. Most clips should be 25-45 seconds."* This caps real long-form storytelling. A genuine penthouse-callback or a freestyle is naturally 90–120 s.

**How.** In `scripts/clip-pipeline.sh::Pass B prompt template` (~line 1555), replace:
```
- Minimum clip: 15 seconds. Maximum clip: 90 seconds. Most clips should be 25-45 seconds.
- Short reactions/one-liners: 15-25 seconds
- Standard moments (funny, hype, hot takes): 25-45 seconds
- Storytime/emotional with narrative arc: 45-75 seconds
- Only exceed 60 seconds for genuinely exceptional stories with clear setup+payoff
```
with:
```
- Minimum clip: 15 seconds. Maximum: 150 seconds for storytime/emotional, 90 seconds for everything else.
- One-liner reactions: 15-25 s
- Standard funny/hype/hot_take: 25-50 s
- Storytime/emotional with narrative arc: 60-120 s (default 90)
- Setup-payoff callbacks with multi-minute setup: up to 150 s (cite the setup line in 'why')
```

Also update `parse_llm_moments` (~line 1447) where duration is clamped:
```python
elif duration > 90:
    # Trim to 90s centered on the peak timestamp
```
to be category-aware:
```python
max_dur = 150 if m.get("category") in ("storytime", "emotional") else 90
elif duration > max_dur:
    clip_start_time = max(chunk_start, ts - max_dur // 2)
    clip_end_time = min(chunk_end, clip_start_time + max_dur)
```

**Cost.** None directly. Stage 7 already handles arbitrary clip lengths.

**Validation.** Average clip duration for `storytime` category should rise to ~90 s on storytime-heavy streams.

**Risk.** Low. A 150-s clip with bland midsection is bad UX; mitigated by Stage 6's vision check rejecting bland-frame moments and by length_penalty (already in `final_score` calculation).

---

## 4. Tier 2 — Medium fixes (~1 week each)

These items add new signals to the pipeline. Each requires a new module / dependency / config but does not change the pipeline's overall shape.

### M1 — Speaker diarization

**Signals:** #4 (multi-modal — speaker change)

**Why.** Off-screen voices, friend interruptions, multi-speaker comedy, and the "caught lying" pattern (someone else corrects the streamer mid-claim) all involve a speaker change. WhisperX (already in `scripts/lib/speech.py`) supports diarization via `pyannote-audio`. Without it, the pipeline can't distinguish `streamer monologuing for 60 s` from `streamer + 3 friends bantering for 60 s` — but those are very different content profiles.

This is the primary signal for catching off-screen comments like the Lacy penthouse moment.

**How.**

1. **Enable diarization in speech.py.**
   - Update `scripts/lib/speech.py` to call WhisperX's `DiarizationPipeline` after alignment. Output JSON gains a `speaker` field per segment (e.g., `"SPEAKER_00"`, `"SPEAKER_01"`).
   - Auth: requires HuggingFace token with access to `pyannote/speaker-diarization-3.1`. Add `HF_TOKEN` env var, fall through gracefully when missing (no diarization → behavior identical to today).

2. **New Pass A signal: speaker_change_density.**
   - In `scripts/clip-pipeline.sh::keyword_scan` (~line 950), add a count of distinct speakers in each 30 s window. If `n_speakers >= 2 and any single speaker contributes <30% of the window's audio`, treat it as a speaker_change event.
   - Add as a new universal signal contributing to `funny` and `controversial` categories (mirrors the existing exclamation-cluster signal at line 990).

3. **New per-moment field: dominant_speaker.**
   - Each Pass A and Pass B moment carries `dominant_speaker` and `speaker_count` for downstream use (Stage 6 prompt, Pass C diversity).

4. **Boost in Pass C ranking.**
   - Where we currently apply the `cross_validated × 1.20` boost, add: if `speaker_count >= 2 and dominant_speaker_share < 0.7`, apply `× 1.15`.

**Cost.**
- Compute: pyannote-audio adds ~30 % to Stage 2 wall time (CPU diarization).
- VRAM: zero (diarization runs CPU after Whisper).
- New dependency: `pyannote-audio` (~150 MB).

**Validation.**
- Manually verify on a known VOD with a friend interjection: does `speaker_count >= 2` fire on that window?
- The Lacy penthouse VOD's payoff window should show at least 2 speakers.

**Risk.** Medium. Diarization can mis-merge similar-voiced speakers. We use the signal as a BOOST, not a gate, so false negatives (missing a real speaker change) just leave the moment unboosted; false positives (spurious speaker_change) only nudge the score, don't create moments.

---

### M2 — Audio-event detector for freestyles, music, audience reaction

**Signals:** #4 (multi-modal — rhythm + crowd response)

**Why.** Freestyles, dance performances, and crowd reactions are visible in the audio waveform but invisible in the transcript. The transcript of a freestyle looks like normal speech with rhymes; the *rhythm* is the signal. Audience laughter/cheering is also signature in the audio spectrum.

**How.** New module `scripts/lib/audio_events.py` — ~120 lines, `librosa` only (already a dependency for tier-C music matching).

1. **`detect_rhythmic_speech(audio_window)`**: chunk the window into syllable-level frames via librosa onset detection; compute beat-alignment regularity; return 0.0–1.0. > 0.7 strongly indicates rhythmic delivery (freestyle, song, dance hype-up).
2. **`detect_crowd_response(audio_window)`**: detect sudden RMS spike followed by sustained chatter spectrum (laughter / cheering signature). Return 0.0–1.0.
3. **`detect_music_dominance(audio_window)`**: ratio of harmonic to percussive components (librosa's HPSS). High harmonic ratio = music playing; matters for tagging dance/music moments.

Wire results into `clip-pipeline.sh::keyword_scan` (~line 989) as new universal signals:
- `rhythmic_speech > 0.7` → +1 signal in `dancing` category, +1 signal in `hype`
- `crowd_response > 0.5` → +1 in `funny` and `hype`
- `music_dominance > 0.6` → +1 in `dancing`

**Cost.**
- Compute: ~5 ms per 30 s window. On a 3-hour VOD with 1080 windows that's ~5 s total.
- VRAM: zero.
- New dependency: none (librosa already installed).

**Validation.** Run on a known freestyle stream — `rhythmic_speech` should fire across the freestyle's duration. Run on a hype-moment-heavy gaming stream — `crowd_response` should fire on big-win moments.

**Risk.** Low. All signals are boost-only, never gate.

---

### M3 — Long-range callback detector via semantic search

**Signals:** #3 (long-range narrative)

**Why.** This is the single biggest unlock for Lacy-class moments. The setup ("I'm in my penthouse, buy my course") and the payoff ("oh, this is [someone]'s penthouse") are semantically related but minutes apart. Sentence-transformer embeddings + a per-stream FAISS index can find pairs of distant transcript segments that are topically related but tonally inverted.

**How.** New module `scripts/lib/callbacks.py` — ~200 lines.

1. **After Stage 2** (transcription), embed every transcript segment with `sentence-transformers/all-MiniLM-L6-v2` (90 MB, CPU, ~10 ms/segment). Persist embeddings as `/tmp/clipper/segment_embeddings.npy` alongside the transcript.

2. **Build a per-stream FAISS index** (`faiss-cpu`, ~5 MB dependency) over the embeddings.

3. **After Pass B** completes, for each candidate moment T:
   - Embed the moment's transcript window (±15 s around T).
   - FAISS-search for transcript segments with cosine similarity > 0.6 that occurred ≥ 5 minutes earlier.
   - Rank-1 match = the strongest "setup" candidate.

4. **Pass B' callback prompt** (small, ~200 tokens): for each top-K candidate with a strong setup match, ask Gemma:
   ```
   At <setup_time> the streamer said: "<setup_segment>"
   At <payoff_time> the streamer said: "<payoff_segment>"
   
   Is this a callback / contradiction / irony / fulfillment worth clipping as a SINGLE clip? If yes, return JSON: {is_callback: true, kind: "irony"|"contradiction"|"fulfillment"|"theme_return", clip_start_time: "MM:SS", clip_end_time: "MM:SS", why: "..."}. The clip can include either a brief flashback to the setup OR just the payoff with the setup named in 'why'. If no, return {is_callback: false}.
   ```

5. **Output**: callback moments get added to `llm_moments` with `category="callback"` (new), `cross_validated=true`, and a 1.5× score boost.

6. **Stage 6 awareness**: when rendering a callback moment, the prompt explicitly receives the setup and payoff text; the title/description should name the callback.

**Cost.**
- Compute: ~10 ms × N segments embedding (~30 s on a 3-hour VOD). Pass B' callback judgment: ~3 s × top-20 candidates = ~60 s.
- VRAM: 90 MB sentence-transformer (CPU). Negligible.
- New dependencies: `sentence-transformers`, `faiss-cpu`.

**Validation.**
- The Lacy penthouse VOD should produce at least one callback moment with the setup ("buying a penthouse") and payoff ("this is [someone]'s penthouse") explicitly named in the "why".
- Ground-truth: hand-label 3 known callback moments on 3 different VODs; the detector should surface ≥ 2 of 3.

**Risk.** Medium. False-positive callbacks (semantically related but not actually ironic) are a real failure mode — mitigated by (a) the > 0.6 cosine threshold, (b) the secondary LLM judgment in step 4. False negatives (missing real callbacks because cosine didn't fire) we accept as a tolerable loss; the framework is incremental.

---

### M4 — Self-consistency wired in for the top-K candidates

**Signals:** #2 (local narrative — confidence calibration)

**Why.** `scripts/lib/self_consistency.py` exists (Phase 5.2) but is unused. For high-stakes ranking decisions (top-20 candidates), running Pass B at three temperatures and comparing reveals two things: (a) moments that survive all three runs are high-confidence, (b) moments that appear in only one run are *unusual* — possibly the nuanced moments a deterministic pass tends to suppress.

**How.**

1. After the deterministic Pass B finishes, take the top-20 candidates by `final_score`.

2. For each, gather the chunk that contained it. Re-run Pass B's chunk-level prompt at temperatures `[0.5, 0.7, 0.9]` (3 calls) → 3 sets of moments per chunk.

3. Use `scripts/lib/self_consistency.py::rank_field_dict()` to score:
   - **Stable** moments (appear in ≥ 2 of 3 runs at similar T): `confidence=0.9`, score boost ×1.10
   - **Unique** moments (appear in only 1 run): `confidence=0.5`, mark as `nuanced=true` — these enter a *separate* candidate pool that fills overflow slots
   - **Reject** moments (appear once with low score in just 1 run): drop

4. Pass C now picks from BOTH pools — main "stable" pool + a small "nuanced" pool that gets at least 1–2 slots out of MAX_CLIPS.

**Cost.** 60 chunk-level Pass B calls additional (top-20 × 3 temperatures). On Gemma 4-26B ~60 × 30 s = 30 min added wall time. Heavy. Justified only when quality > speed.

**Validation.** Manual: do clips selected from the "nuanced" pool feel different/more interesting than the stable pool? Track via a 5-VOD A/B comparison.

**Risk.** Medium. Self-consistency can also amplify noise — bad moments with consistent hallucinations also "survive" all runs. Mitigated by keeping the stable pool secondary in selection rules.

**Alternative.** Skip M4 entirely and rely on M3's long-range detection for nuance. M4 adds 30 min of wall time for marginal gain. **Recommendation: do not implement until A1 is in place; A1 makes M4 redundant.**

---

## 5. Tier 3 — Architectural changes (~1 month each)

These items change the pipeline's fundamental shape. Do not start until Tiers 1 and 2 are deployed and measured.

### A1 — Two-stage Pass B: local + global

**Signals:** #3 (long-range), #2 (local — re-evaluated with global context)

**Why.** Tier-1 Q1 patches the symptom (no prior context) with a 2-line summary; Tier-2 M3 patches it with semantic search; A1 is the *correct* solution: have Gemma read a condensed view of the entire stream first, identify multi-chunk arcs explicitly, then run local Pass B with that arc list as context.

**How.**

1. **Stream skeletonization.** After Pass B-local finishes (chunk-level), build a "stream skeleton" by asking Gemma per chunk: `"In 2 lines: (1) the streamer's main claim/topic, (2) any payoff or surprise."` Concatenate into a 30–60-line document (~3000 tokens for a 3-hour stream).

2. **Pass B-global.** ONE Gemma call with the skeleton + a focused prompt:
   ```
   Below is a skeleton of a 3-hour stream, line by line, with timestamps.
   Identify SETUP-PAYOFF arcs that span MULTIPLE chunks. Look for:
   - A claim made early that's contradicted/fulfilled later (Lacy penthouse)
   - A theme introduced and revisited 30+ minutes later (callback)
   - A friend / off-screen voice exposing a fake (caught moment)
   - A long storytelling arc that crosses chunks
   
   For each arc return: {setup_time: "MM:SS", payoff_time: "MM:SS", arc_type, why}.
   ```

3. **Arcs become first-class moments.** Each arc is a candidate with custom clip boundaries that may include ONLY the payoff (with setup named in 'why') OR a stitched setup+payoff if they're within 90 s of each other (Stage 4.5's existing stitch logic).

4. **Pass C consumes both** local moments and global arcs, ranking them together. Arcs get a 1.4× initial boost (they're discovered with full-stream context — high signal).

**Cost.** 1 large Gemma call per stream (~30 s wall time). Very cheap given the unlock.

**Validation.** Lacy penthouse moment should surface as an arc from B-global, not be missed by B-local.

**Risk.** Medium. Skeletonization quality determines arc-detection quality; bad summaries → bad arcs. Mitigated by re-using Tier-1 Q1's chunk summaries (already produced).

---

### A2 — Visual setup-payoff verification in Stage 6

**Signals:** #4 (multi-modal — visual continuity)

**Why.** Stage 6 today shows the multimodal model 6 frames around T (T-2 to T+5). For arc-based moments (A1) and callback moments (M3), the model should ALSO see frames from the SETUP — otherwise Stage 6 can't visually verify that the same person who set up the joke is the one paying it off, or that the visual context supports the irony.

**How.**

1. For moments tagged `callback` (M3) or `arc` (A1), Stage 5 extracts 2 additional frames from the setup window (`setup_time - 1`, `setup_time + 1`).

2. Stage 6's vision payload becomes 8 frames instead of 6 with prompt: `"Frames 1-2 are from earlier in the stream (setup). Frames 3-8 are from now (payoff). Does the visual context match the claimed callback? Score 0-10 and explain."`

3. The 0-10 visual-callback-confirmed score boosts or penalizes the moment's `final_score` × `[0.85, 1.20]`.

**Cost.** +2 frames per callback moment (~5 % more vision tokens). Negligible.

**Validation.** Stage 6's "why" or `description` text on a callback clip should reference the visual continuity (e.g., "same person who pitched the course is now admitting it's not his apartment").

**Risk.** Low. Falls through to current 6-frame behavior when no callback tag is present.

---

### A3 — Replace MiniCheck Tier 2 with a Gemma single-shot judge

**Signals:** #2, #3, #4 (unified scoring + grounding)

**Why.** This is the single-model architecture you asked about. Tier 2's MiniCheck-Flan-T5-Large is the only non-Gemma ML model in the LLM stack. It's tuned for QA-style literal entailment and consistently mis-rejects inferential summaries. A Gemma "judge" call can score grounding, nuance, callback potential, irony, and visual continuity *simultaneously* in one call — and Gemma understands inference better than NLI.

**How.** New function `gemma_judge(claim, transcript_window, optional_setup, optional_speaker_info)` in `scripts/lib/grounding.py` that calls Gemma with a single structured prompt:

```
Given:
- claim: "<the LLM's why>"
- transcript_window: "<±60 s around moment>"
- optional_setup: "<earlier transcript line, if callback>"
- optional_speaker_info: "speakers in window: [SPEAKER_00, SPEAKER_01]"

Score this clip moment on FIVE dimensions, 0-10 each:
1. Grounding: how well the claim is supported by the transcript
2. Setup-payoff: presence of narrative arc structure
3. Speaker dynamics: multi-speaker / off-screen voice / interruption value
4. Conceptual humor: ironic, contradictory, or surprising vs just verbally funny
5. Callback strength: if a setup is provided, how strong the connection is (0 if no setup)

Return JSON: {grounding, setup_payoff, speaker, conceptual, callback, rationale}.
```

Replace Tier 2's binary pass/fail with a weighted sum of the five scores. Tunable weights in `config/grounding.json`.

**Cost.** ~3 s per moment × ~100 moments = ~5 min added wall time. Replaces ~150 ms × 100 = 15 s for MiniCheck. So +5 min net per stream.

**Eliminates dependencies:**
- Tier 2 MiniCheck-Flan-T5-Large model
- Tier 3 Lynx-8B model (these scores subsume what Lynx checks)
- The 1.5 GB MiniCheck model download

**Validation.** Comparison run: same VOD, same Pass B output, scored under (a) MiniCheck cascade vs (b) Gemma judge. Manually rate the 10 selected clips for "is the description accurate / is this a worthy clip". Gemma judge should win on accuracy.

**Risk.** Higher. Self-judgment bias is a real failure mode — Gemma scoring its own claims could be lenient. Mitigated by:
- Asking for FIVE independent dimensions (forcing the model to commit to specifics rather than a single "is this good?" verdict)
- Running judgment with `temperature=0.0` for deterministic output
- Keeping Tier 1 (regex denylist + word overlap) as a hard pre-gate — Gemma judge is consulted only AFTER Tier 1 passes

---

## 6. Cross-cutting concerns

### 6.1 Wiki update obligations

Per [CLAUDE.md](./CLAUDE.md), every code change requires a wiki update. For this plan:

- **When implementing each Tier 1 item**: update [concepts/highlight-detection.md](./AIclippingPipelineVault/wiki/concepts/highlight-detection.md) (Pass B prompt changes) and [concepts/clipping-pipeline.md](./AIclippingPipelineVault/wiki/concepts/clipping-pipeline.md) (chunk size, max duration).
- **When implementing M1**: update [entities/speech-module.md](./AIclippingPipelineVault/wiki/entities/speech-module.md), add [entities/diarization.md](./AIclippingPipelineVault/wiki/entities/diarization.md).
- **When implementing M2**: add [entities/audio-events.md](./AIclippingPipelineVault/wiki/entities/audio-events.md), update [concepts/clipping-pipeline.md](./AIclippingPipelineVault/wiki/concepts/clipping-pipeline.md).
- **When implementing M3**: add [concepts/callback-detection.md](./AIclippingPipelineVault/wiki/concepts/callback-detection.md), [entities/callback-module.md](./AIclippingPipelineVault/wiki/entities/callback-module.md).
- **When implementing A1**: add [concepts/two-stage-passb.md](./AIclippingPipelineVault/wiki/concepts/two-stage-passb.md).
- **When implementing A3**: update [entities/grounding.md](./AIclippingPipelineVault/wiki/entities/grounding.md) substantially; potentially deprecate [entities/lmstudio.md](./AIclippingPipelineVault/wiki/entities/lmstudio.md) (its only consumer was Tier 3).
- **Every implementation**: a new entry in [wiki/log.md](./AIclippingPipelineVault/wiki/log.md) and a new BUG-style entry in [concepts/bugs-and-fixes.md](./AIclippingPipelineVault/wiki/concepts/bugs-and-fixes.md) if the implementation reveals a regression.

### 6.2 Backward compatibility

Every item must degrade gracefully when its dependency is absent (`HF_TOKEN` missing → no diarization; `sentence-transformers` not installed → no callback detection; etc.). The pipeline today already follows this pattern; preserve it. **No item below Tier 1 is allowed to hard-fail.**

### 6.3 VRAM budget impact

| Item | VRAM | Notes |
|---|---|---|
| Q1–Q5 | None | Prompt / config only |
| M1 (diarization) | None | CPU pyannote |
| M2 (audio events) | None | CPU librosa |
| M3 (callback detector) | None | CPU sentence-transformers + FAISS |
| M4 (self-consistency) | None | Re-uses existing Gemma load |
| A1 (two-stage Pass B) | None | Same model |
| A2 (visual verification) | None | +2 frames per callback (negligible) |
| A3 (Gemma judge) | **−1.5 GB** | Removes MiniCheck model |

Net VRAM impact: SAVES 1.5 GB by retiring MiniCheck.

### 6.4 Wall-time impact (estimate, 3-hour VOD on RTX 5060 Ti)

| Phase | Current | After Tier 1 | After Tier 2 | After Tier 3 |
|---|---|---|---|---|
| Stage 2 (transcription) | ~12 min | 12 | **15 (+M1 diarization)** | 15 |
| Stage 4 Pass B | ~25 min | **27 (+Q1 summaries)** | 27 | **27** (A1 absorbs into B-global) |
| Pass B' callback (M3) | — | — | **+1 min** | (subsumed by A1) |
| Pass B-global (A1) | — | — | — | **+1 min** |
| Self-consistency (M4) | — | — | +30 min (optional) | (skip per recommendation) |
| Stage 6 vision | ~20 min | 20 | 20 | 20 |
| Tier 2 grounding | ~15 s | 15 s | 15 s | **+5 min (Gemma judge)** |
| **TOTAL** | ~60 min | ~62 min | ~64 min | ~70 min |

Tier 1 + Tier 2 (without M4) buys substantial quality at +4 min wall time. Tier 3 adds ~10 min.

---

## 7. Validation matrix

For each item, the success criterion that determines whether to keep it:

| Item | Validation | Pass criterion |
|---|---|---|
| Q1 | Pass B "why" texts mentioning prior context | ≥ 1 callback-named "why" per typical VOD |
| Q2 | Storytime moment count, "why" specificity | +30 % `storytime` selections vs current |
| Q3 | Storytime/hot_take selections from keyword-only | +1 storytime keyword-only pick per VOD on average |
| Q4 | Storytime moments cut at 90 s cap | drops to < 5 % of storytime selections |
| Q5 | Average duration of storytime selections | rises from ~50 s to ~80 s |
| M1 | Lacy-style off-screen voice moments | speaker_count ≥ 2 fires on the payoff window |
| M2 | Freestyle moments on freestyle streams | rhythmic_speech > 0.7 fires for ≥ 60 s on a known freestyle |
| M3 | Lacy penthouse moment | surfaces as a callback with both setup and payoff named in "why" |
| M4 | Nuanced clip count | ≥ 2 of 10 selected clips come from the "nuanced" pool |
| A1 | Multi-chunk arcs | ≥ 1 arc per typical VOD, distinct from local moments |
| A2 | Callback descriptions | Stage 6 description for callback clips references both setup and payoff visually |
| A3 | Description accuracy | Manual rating on 10 clips × 5 VODs improves over MiniCheck baseline |

If an item fails its validation, revert and document in [bugs-and-fixes.md](./AIclippingPipelineVault/wiki/concepts/bugs-and-fixes.md).

---

## 8. Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Q1 prior summaries hallucinate, poison Pass B prompt | Low | Med | Use temperature 0.0 for summary calls; cap at 80 tokens |
| Q2 few-shot examples bias output toward example clones | Medium | Med | Use 3 diverse examples spanning categories |
| Q4 chunk-size change breaks chunk-count math elsewhere | Medium | Low | Compute total_chunks lazily by walking the timeline once |
| M1 diarization mis-merges similar voices | Medium | Low | Speaker change is a BOOST signal, not a gate |
| M2 audio events false-fire on background music | High | Low | All boost-only; 0.7 threshold prevents most false fires |
| M3 false-positive callbacks (semantically related but not ironic) | Medium | Med | Cosine threshold 0.6 + secondary Gemma judgment in step 4 |
| M4 self-consistency amplifies systematic biases | Medium | Med | Treat "stable" pool as primary, "nuanced" as opt-in 1–2 slots |
| A1 skeleton quality limits arc detection | High | Med | Re-use Tier-1 Q1 summaries; skeleton quality grows with that |
| A2 setup frames don't visually relate to payoff | Medium | Low | Include only if `setup_time` is in same scene/location (heuristic via face-pan module) |
| A3 Gemma self-judgment is lenient | High | High | Force 5-dimensional output; keep Tier 1 as hard pre-gate; A/B vs MiniCheck before retiring it |

---

## 9. Recommended sequencing

Ship items in this order. Each batch should land as one cohesive change with its own commit, log entry, and validation pass before moving on.

| Order | Items | Batch | Engineering | Wall time after |
|---|---|---|---|---|
| 1 | Q2, Q5 | Prompt batch — examples + duration cap | ~3 hours | +0 min |
| 2 | Q1, Q3 | Context batch — prior summaries + per-cat ceilings | ~1 day | +2 min |
| 3 | Q4 | Chunk-size batch | ~4 hours | 0 (slight reduction) |
| **— validate Tier 1 against a known VOD —** | | | | |
| 4 | M2 | Audio events (cheapest Tier-2 win) | ~3 days | +0 min |
| 5 | M1 | Speaker diarization | ~1 week | +3 min (Stage 2) |
| 6 | M3 | Long-range callback detector | ~1 week | +1 min |
| **— validate Tier 2 against the Lacy penthouse VOD —** | | | | |
| 7 | A1 | Two-stage Pass B | ~2 weeks | +1 min |
| 8 | A2 | Visual setup-payoff (depends on A1) | ~1 week | +0 min |
| 9 | A3 | Gemma judge (replaces Tier 2/3) | ~2 weeks | +5 min |

**Skip M4 (self-consistency)** — it costs 30 min wall time for marginal gain that A1 subsumes.

---

## 10. Success criteria (project-level)

The plan succeeds when, on the Lacy penthouse VOD:

1. **The penthouse moment appears in the selected 10 clips** with a "why" / description that names the contradiction (someone exposes the streamer's fake apartment claim).
2. **At least one freestyle / multi-speaker / off-screen-voice moment** appears per VOD on streams that contain such moments.
3. **Storytime clips average 60–90 s** (vs current ~30–40 s), with no truncation at 90 s for genuine storytimes.
4. **Distribution is even**: each of the 6 time-buckets contains 1–2 of the 10 selected clips. No bucket has > 3.
5. **Description accuracy**: ≥ 8 of 10 selected clips' descriptions match the actual on-screen content (manual rating).

---

## 11. Out of scope

- Replacing Whisper. Speech-to-text is a different problem class; Whisper large-v3 is fine.
- Replacing the multimodal vision model. Gemma 4-26B works; this plan accepts its thinking-leakage cost.
- Live streaming (vs VOD post-processing). The pipeline is VOD-batch by design.
- Translating Pass A's keyword sets to other languages. English-only assumption.
- Replacing FAISS with a vector DB. Per-stream FAISS files are sufficient and disposable.

---

## 12. Open questions

1. **Should A1 obviate M3?** A1's two-stage Pass B already does long-range narrative discovery via the skeleton. M3's semantic search is a different mechanism but covers similar ground. After Tier-2 lands, evaluate whether M3 adds value beyond A1.
2. **Do we need a "negative example" few-shot in Q2?** Currently Q2 specifies 3 positive examples. A 4th example showing what NOT to clip (generic "oh my god" reactions) might help — but Pass B's prompt already lists those in prose.
3. **Should diarization output be user-editable?** Per-VOD speaker label edits in the dashboard would help when WhisperX merges similar voices. Probably out of scope for v1.
4. **What's the right 'max clips' default for VODs > 4 hours?** Currently scales as `target ≈ vod_hours × 3` (so 9 for 3 hours, 12 for 4 hours). Long streams may benefit from 15–20 clips with stricter bucket caps.

---

*End of plan.*
