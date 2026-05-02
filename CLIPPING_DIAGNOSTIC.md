# OpenClaw Stream Clipper — Detection Diagnostic

**Purpose.** A reviewable write-up of how this clipper actually decides what to clip and how it tells the model what it found. Intended for research and for implementing industry-grade improvements. Written 2026-04-23 in response to a concrete failure case (a clip whose AI description said the streamer was "reacting to subs being gifted" when he was actually talking about reaching Ranked 3.0).

**Status of this document.** Diagnostic + roadmap, not a spec. File/line references point at the code at the time of writing; a refactor will outdate them. Every claim is verifiable against the source — grep the symbols named below.

---

## 1. Executive summary

The clipper is a three-stage LLM pipeline that runs a cheap keyword scan, an expensive chunked LLM analysis, then a vision pass that generates the human-facing metadata (title, hook, description, voiceover). It never asks the vision model what the streamer *said* — only what a frame looks like, plus the upstream LLM's own summary of the moment. That architectural choice is the root cause of the "Ranked 3.0 → gifted subs" failure and of the broader detection-quality drift you noticed. A Twitch/Kick frame with a sub-alert overlay is visually indistinguishable from a sub-celebration moment, and once the vision model has committed to that interpretation the hook, title, description, and voiceover all carry the same invented narrative forward.

Three concrete findings:

1. **Hallucination propagation.** Each LLM stage receives the *previous* LLM's interpretation as context, not the ground truth. Errors compound.
2. **Prompt bloat.** Vision was recently extended from 4 asks per call (score / category / title / description) to 7 asks (added hook / chrome_regions / mirror_safe / voiceover). More asks per call = less attention budget per field, especially on thinking-mode models. This correlates with the detection drift you reported.
3. **Grounding blindness.** Until 2026-04-23 the vision model never saw the transcript text at the peak moment. It only saw frames + a one-line "why" from the upstream keyword/LLM passes. A fix (see §7) now injects ±8 s of transcript into every vision call; `grounded_in_transcript` is surfaced in the moment data.

Sections 2–5 explain the machinery. §6 walks through the Ranked-3.0 case mechanically. §7 lists what to research next.

---

## 2. System overview

The pipeline is in [scripts/clip-pipeline.sh](scripts/clip-pipeline.sh). 8 primary stages plus two optional ones:

```
1. Discovery           — find the VOD
2. Transcription       — faster-whisper large-v3 → transcript.json
3. Segment Detection   — classify 10-min windows as gaming / irl / just_chatting / reaction / debate
4. Moment Detection    — Pass A keywords, Pass B LLM chunks, Pass C merge+select
4.5 Moment Groups      — narrative arcs + stitch bundles (optional)
5. Frame Extraction    — 6 JPEGs per candidate moment
6. Vision Enrichment   — score boost + title / hook / description / voiceover
6.5 Camera-pan Prep    — OpenCV face tracking (optional)
7. Editing & Export    — per-clip render through FFmpeg
8. Logging             — diagnostics, processed.log
```

Every stage communicates by writing JSON files to `/tmp/clipper/`. This is important for debugging: the full state of detection is on disk after each stage and inspectable with `jq`. `hype_moments.json` (output of Pass C) is the canonical list of "clips we will render"; `scored_moments.json` is the same list enriched by vision.

---

## 3. How detection works

The core claim of the system is **transcript-first detection**. A clip is chosen because of what was *said*, not because of what was *shown*. Vision is layered on top for metadata and score boosts — it cannot eliminate a candidate.

### 3.1 Pass A — Keyword heuristics

Implemented as a Python heredoc in Stage 4 of [clip-pipeline.sh](scripts/clip-pipeline.sh).

- A 30-second window slides across the transcript with a 10-second step.
- Six keyword categories scan the window:
  - **hype** — "oh my god", "no way", "clip that", "let's go", "clutch"
  - **funny** — "i'm dead", "bruh", "you're trolling"
  - **emotional** — "thank you", "mental health", "bottom of my heart"
  - **hot_take** — "hot take", "unpopular opinion"
  - **storytime** — "so basically", "you won't believe"
  - **reactive** — "are you kidding", "rage", "tilted"
- Universal signals stack on top: ALL CAPS streaks, exclamation clusters, rapid-fire sentences, laughter markers, long pauses + speech bursts.
- Segment-specific weight multipliers: e.g. `funny` in `irl` segments gets 1.4×, `storytime` in `just_chatting` gets 1.5×.
- Dynamic thresholds per segment type (`gaming=3` signal points, `irl=2`, …).
- Candidates deduplicate within 20 seconds.

**Output**: `keyword_moments.json` — list of `{timestamp, score, category, primary_category, why}`. `why` here is a mechanical concatenation of the matched keywords, not a narrative.

### 3.2 Pass B — LLM chunk analysis

The transcript is chunked into 5-minute windows with 30-second overlap. Each chunk is sent to the configured text model (Gemma 4 `gemma-4-26b-a4b` or Qwen 3.5 — both multimodal) with a segment-specific system prompt:

- **gaming** → "clutch plays, epic wins/losses, rage quits, skill moments"
- **irl** → "funny stories, emotional moments, surprising encounters"
- **just_chatting** → "hot takes, funny stories, emotional vulnerability, audience interaction"
- **reaction** → "strong reactions, controversial takes, emotional responses"
- **debate** → "persuasive arguments, heated exchanges, mic-drop moments"

The model returns `[{time: "MM:SS", score: 1-10, category, why}]`. The `why` here is narrative — the LLM's own summary of *what makes this moment clip-worthy*. That narrative will reappear in Stage 6 as the context for vision enrichment.

**This is the first place hallucination can enter the pipeline.** If the Pass B LLM misreads the transcript ("he's celebrating subs" when he's actually celebrating a rank), that mistake becomes the "truth" everything downstream operates on.

### 3.3 Pass C — Merge, dedupe, time-bucket, select

Pass A and Pass B are merged into a single candidate list.

- **Score normalization**: keyword-only moments are capped at `0.75` on a 0-1 scale (they lack context understanding). LLM scores are 0-1 already.
- **Cross-validation**: a moment flagged by both passes gets its score multiplied by `1.25`, with `cross_validated=true`. Strong signal the moment is real.
- **Style weighting**: `--style funny` multiplies funny-category scores by 1.3 etc.
- **Length penalty**: longer clips need higher base scores. `≤30s = 1.0 × `, `≤60s = 0.85 × `, `>75s = 0.65 × `.
- **Time-bucket distribution**: VOD is divided into 3-10 equal buckets (2/hour). Phase 1 guarantees one clip per bucket. Phase 2 fills overflow slots by raw score. Phase 3 applies style-aware re-ranking.
- **Minimum spacing**: 30-60 s between final clips depending on duration.
- **Stream position weighting**: `0.88 × ` in the first 10%, `1.05 × ` in the 25-70% zone, `0.92 × ` in the last 10%.

**Output**: `hype_moments.json` — up to `2× target_clips` entries carrying `{timestamp, clip_start, clip_end, clip_duration, score, category, preview, why, source, cross_validated, segment_type, length_penalty, position_weight}`.

### 3.4 Stage 5 — Frame extraction

FFmpeg extracts 6 JPEG frames per moment, sampled every 5 seconds across a 30-second window centered on the peak timestamp. Frames are 960×540, `q:v 2`.

Only frames with indices `03` and `04` are fed to vision (§3.5). Frames `01`, `02`, `05`, `06` exist for inspection but are not used.

### 3.5 Stage 6 — Vision enrichment

The critical stage for this diagnostic. A single LLM call per moment (retried on frame `04` if frame `03` fails JSON parsing). The payload is a chat message with:

- A text prompt (see §4 for the exact content).
- One base64-encoded JPEG.
- `temperature: 0.3`, `max_tokens: 6000`.

The model returns JSON: `{score, category, title, description, hook, mirror_safe, voiceover, grounded_in_transcript}`. The parser harvests those fields and merges them into the moment's entry; `title` becomes the output filename, `hook` becomes a `drawtext` overlay, `voiceover.text` is fed to Piper for the TTS layer.

**Key architectural detail**: vision's category and score are *blended additively* with the transcript's — vision can only boost a score, never reduce it. The moment's existence is never in question by the time we get here. Only its metadata is.

---

## 4. How the AI is coordinated

This is where the pipeline design most affects output quality. Three coordination mechanisms matter:

### 4.1 Context injection

Vision isn't asked "what's happening in this frame?" blind. It receives a context hint built from upstream state:

```
This is a {stream_type} stream.
Currently in a {segment_type} segment.
Flagged as '{transcript_category}' because: {transcript_why}
```

So vision sees: *"This is a gaming stream, currently in a just_chatting segment, flagged as 'hype' because: <Pass B's narrative summary>."* The model must output a title/hook/description *consistent with that context*, because the grading rubric in the prompt rewards coherence.

**Failure mode**: if Pass B's `why` already hallucinated ("streamer is reacting to gifted subs"), vision is being biased toward inventing a matching scene in its output. You have two LLM passes in a row confirming each other with no ground-truth check between them.

### 4.2 Prompt shape

Before 2026-04-22 the vision prompt asked for 4 fields (score, category, title, description). The Wave D originality work extended it to 7 (added hook, chrome_regions, mirror_safe, voiceover). Every added field costs attention budget on a finite-token thinking model. Qwen 3.5 35B is particularly sensitive: its thinking mode eats 2000-4000 tokens before writing the answer, and those tokens get distributed across whatever the prompt asks for. Adding asks *always* degrades per-field quality.

The 2026-04-23 revision (§7) dropped `chrome_regions` back out of the prompt (the consuming `smart_crop` framing mode was also removed), bringing the field count back to 6.

### 4.3 Single-call architecture

One vision call generates every downstream artifact — title, hook, description, voiceover. There is no separate "what did the streamer actually say" call, no self-consistency check (multiple samples with different seeds), no cross-field verification ("does the title match the description?"). When the single call goes wrong, every visible piece of metadata goes wrong with it.

---

## 5. Failure modes

In descending order of impact:

### 5.1 Hallucination propagation

Already introduced. Pass B's `why` becomes Stage 6's context. A single misread at Pass B cascades into every user-facing field. This is the dominant failure mode.

### 5.2 Frame-level deception

A Twitch stream's UI includes persistent overlays — sub alerts, bit animations, donation popups, follower goals. When one of these triggers near a moment the pipeline is analyzing, the frame looks like a "celebration moment" regardless of what the streamer is saying. Vision sees the overlay, pattern-matches common Twitch-clip templates, and generates a celebration-themed title. The audio content is completely ignored because the pipeline doesn't send it to vision.

### 5.3 Thinking-mode budget exhaustion

For Qwen 3.5 35B (`qwen3.5-35b-a3b`) in LM Studio, thinking mode cannot be disabled. The model spends 2000-4000 tokens "reasoning" before emitting the JSON answer. If the prompt asks for 7 fields, the reasoning is shallow on each. We've caught this empirically — extending the prompt past 6 fields produces visibly weaker titles and hooks.

### 5.4 Vision context drift

The only frames vision sees are `03` and `04` out of 6 sampled every 5 seconds. For a 30-second extraction window, those are frames at `T-10s` and `T-5s`. If the *peak* of the moment is at `T+0s` or later, the vision model is literally looking at the setup, not the payoff. We have no evidence this is currently a major issue, but it's a worth-knowing limitation.

### 5.5 Low-signal moments promoted by time buckets

Pass C's time-bucket distribution guarantees one clip per bucket, even if nothing in that bucket is actually clip-worthy. A quiet 10-minute segment can produce a "clip" of the streamer mumbling to himself, which then gets creative-writing'd by vision into a confident-sounding hook. This looks like bad detection when it's actually bad candidate selection.

### 5.6 Whisper transcription errors

large-v3 is extremely accurate on English, but streamer slang, meme references, game-specific jargon, and names of other streamers often transcribe wrong. `"ranked 3.0"` might have come back as `"rank 3.0"` or `"rink three point oh"` to Pass B. If Pass B couldn't parse it, its `why` field falls back to generic stream-template explanations.

---

## 6. Walking through the Ranked-3.0 case

Known facts:

- Streamer audio: discussing reaching "Ranked 3.0" (a rank milestone in some game).
- Vision output: title / hook / description claim he is reacting to subs being gifted.
- The Wave D voiceover layer also produced a sub-themed line.

Mechanical reconstruction of how this almost certainly happened:

1. **Whisper transcribed the segment**, probably correctly as "ranked 3.0" or similar.
2. **Pass A keyword scan**: "ranked", "3.0", ordinal rank celebrations, and the streamer's emotional delivery probably triggered the `hype` category (exclamation clusters, "let's go"-type fillers). The `why` from Pass A is mechanical: the matched keywords.
3. **Pass B LLM analysis** processed the chunk. The model saw the transcript, understood the celebration, but when asked to explain *what* the streamer is celebrating, it reached for the most common explanation for excited streamer audio: sub gifting. Streamer training data is dominated by sub-celebration clips; "ranked 3.0" as a game rank is rarer. The LLM produced `why = "streamer reacting to gifted subs"` or similar.
4. **Pass C merged and selected**. The moment survived. Pass C doesn't verify the `why`; it just carries it forward.
5. **Stage 5** extracted 6 frames. Probably showed the streamer webcam + game UI. Possibly a persistent Twitch sub-goal or chat message visible.
6. **Stage 6 context hint** was built: `"Flagged as 'hype' because: streamer reacting to gifted subs."`.
7. **Stage 6 vision prompt** asked the model to produce title / hook / description / voiceover. The model had:
   - Frames showing a streamer being excited.
   - A strong textual hint saying the reason is gifted subs.
   - No ground-truth transcript to contradict that hint.
   - A prompt rewarding coherent, punchy, clip-voice output.
   The model did exactly what it was asked — it wrote a coherent sub-celebration narrative. "grounded_in_transcript" didn't exist as a field at that time so the model had no explicit incentive to flag uncertainty.
8. **Stage 7** used the vision-generated title as the filename, burned the vision hook onto the video, and generated a Piper voiceover from the vision `voiceover.text`. All three user-facing artifacts carried forward the fabricated narrative.

The streamer's actual words were never inspected by any system component after Pass B finished its chunk. Every later layer was working from Pass B's (likely wrong) interpretation.

---

## 7. Fixes landing in this commit

Written into [clip-pipeline.sh](scripts/clip-pipeline.sh) alongside this document:

1. **Transcript grounding in Stage 6.** The vision prompt now pulls ±8 s of verbatim transcript around the peak and injects it into the prompt text. The model is instructed: "the title / description / hook MUST describe what the transcript literally says or what the frame literally shows. Do NOT invent context." This bypasses Pass B's `why` as the sole narrative authority.
2. **`grounded_in_transcript` field.** The model self-reports whether its output is rooted in the transcript. False values are logged and carried into the moment data, so the dashboard / review tooling can highlight questionable clips.
3. **Prompt field count trimmed.** Removed `chrome_regions` (the consuming framing mode is gone). Back to 6 fields: score / category / title / description / hook / voiceover + the mirror_safe / grounded_in_transcript flags.
4. **Framing modes reduced to two.** `smart_crop` and `centered_square` removed; only `blur_fill` and `camera_pan` remain. Fewer moving pieces for the pipeline to reason about and one less code path to test.

These are small surgical changes. The broader architectural issue (LLM-to-LLM hallucination propagation) remains and needs research, not patches.

---

## 8. Industry-practice recommendations to research

In rough priority order:

### 8.1 Separate the "what was said" call from the "what looks good" call

Stage 6 should be split into two subcalls:

1. **Transcript-grounded classifier** — text-only LLM call, no vision. Input: transcript window + segment type. Output: category + what the streamer is actually doing in plain language.
2. **Visual assessor** — vision call. Input: the transcript classifier's output + frames. Output: score boost + title / hook / description / voiceover, constrained to the already-established topic.

This structurally prevents the pipeline's current failure where a vision hallucination overrides an accurate transcript.

### 8.2 Self-consistency sampling

For title / hook / description, sample the LLM 3-5 times with different seeds and pick the majority category. Divergent samples are a strong signal that the moment is ambiguous — surface it to the user for manual review instead of rendering it.

### 8.3 Active-speaker-only transcription for captions

Stream audio often includes music, game SFX, and Discord call audio. Whisper tries to transcribe all of it. Running a voice-activity + speaker-diarization pass first (pyannote or whisperx) would reduce "phantom" transcribed content that derails Pass B.

### 8.4 Cross-field contradiction checks

Cheap heuristic layer after Stage 6: compare the generated title against the transcript window. If none of the title's content words appear in the transcript (± stemming), flag the clip as likely-hallucinated. Doesn't need another LLM call — a TF-IDF or fuzzy-match check is enough.

### 8.5 Negative-keyword filters for common hallucinations

Empirical: certain words appear in vision output far more often than in reality. "Subs", "donation", "raid", "follower" show up in vision output on clips that don't mention them. A simple "if the title contains X and X doesn't appear in the transcript, rewrite the title" pass would catch the most egregious failures.

### 8.6 Feedback loop

Dashboard should let the user mark a clip as "wrong topic" or "right topic". Those marks become a small eval set. Running the eval after any change to detection gives you a measurable regression signal instead of the current "something feels worse" report.

### 8.7 Variable-length clip windows by detected payoff

Storytime clips especially suffer from the 45-s default — the actual payoff often sits past the window end. An LLM call that reads the full transcript around the peak and returns "include seconds X to Y" would dramatically improve storytime quality. Already partially done via `clip_start/clip_end` but the prompts don't actively search for the payoff.

### 8.8 Industry-standard datasets

For any of the above, benchmarks exist:

- **HowTo100M** — long-form video with aligned transcripts.
- **YouTube-8M** — categorized video segments.
- **TVQA** — dialog-grounded video understanding.
- **VLG-Net** / **CONDENSED-MOVIES** — story-arc detection in long videos.
- **Streamer-specific corpora** don't really exist publicly; worth building one with Twitch's VOD API + manual labels.

### 8.9 Model-selection experiments

The pipeline currently routes text and vision to the same multimodal model (Gemma 4 or Qwen 3.5). Multimodal models *are* more convenient but tend to underperform dedicated text models on long-transcript reasoning. A/B tests:

- Stage 4 Pass B on Gemma 4 vs. a text-only Qwen 3.5 or DeepSeek-V3.
- Stage 6 on Gemma 4 vs. dedicated Qwen2.5-VL or MiniCPM-V.
- Fix which model you benchmark against (currently drift is impossible to attribute to code changes vs. model upgrades).

### 8.10 Human-in-the-loop for borderline clips

Any clip where `grounded_in_transcript=false`, cross_validated=false, or where vision disagreed with transcript category should pause and ask before rendering. Saves embarrassment at render time; the user accepting/rejecting the pause produces training data for §8.6.

---

## 9. Where to look in the code

For the next engineer or researcher reviewing this doc:

| Topic | File | Symbol |
|---|---|---|
| Pass A keyword scanning | [scripts/clip-pipeline.sh](scripts/clip-pipeline.sh) | Stage 4 — look for `keyword_categories = {` inside the first `python3 << 'PYEOF'` heredoc |
| Pass B LLM chunking | [scripts/clip-pipeline.sh](scripts/clip-pipeline.sh) | Stage 4 — segment-specific prompts in the second PYEOF |
| Pass C merging | [scripts/clip-pipeline.sh](scripts/clip-pipeline.sh) | `# ---- TIME-BUCKET DISTRIBUTION ----` around line 1513 |
| Vision prompt | [scripts/clip-pipeline.sh](scripts/clip-pipeline.sh) | Stage 6 — search for `"Analyze this livestream frame"` |
| Vision parser | [scripts/clip-pipeline.sh](scripts/clip-pipeline.sh) | Stage 6 — harvest loop after `best_vision_result = parsed` |
| Moment grouping | [scripts/lib/moment_groups.py](scripts/lib/moment_groups.py) | `build_narrative_groups`, `build_stitch_groups` |
| Vision render metadata | [scripts/clip-pipeline.sh](scripts/clip-pipeline.sh) | Stage 7 — `MOMENT_META=` block |
| Piper TTS mix | [scripts/clip-pipeline.sh](scripts/clip-pipeline.sh) | Stage 7 — `FILTER_COMPLEX=` audio graph |

The wiki entry for this diagnostic is [concepts/originality-stack.md](AIclippingPipelineVault/wiki/concepts/originality-stack.md) and [concepts/vision-enrichment.md](AIclippingPipelineVault/wiki/concepts/vision-enrichment.md). Cross-reference [concepts/bugs-and-fixes.md](AIclippingPipelineVault/wiki/concepts/bugs-and-fixes.md) for historical detection issues.

---

## 10. TL;DR for someone reviewing

The clipper finds candidates from transcript text, then hands a stripped-down summary to a vision model that generates every user-facing piece of metadata. The vision model never reads the transcript; it only reads the upstream LLM's interpretation. When that interpretation is wrong (pattern-matching "excited streamer" to the common "sub celebration" template), every downstream artifact — title, hook, description, voiceover — carries the wrong narrative forward coherently, which makes the error invisible to the user until they compare the clip to the actual audio.

The 2026-04-23 patch injects the real transcript into the vision call and asks the model to self-flag when it's guessing. That closes the specific failure mode that produced the Ranked-3.0 clip. It does not fix the broader architecture — a proper fix requires separating "what did they say" from "what does it look like" into distinct LLM calls, plus self-consistency sampling, plus a measurable feedback loop.
