---
title: "Plan — Clip Forensics + Semantic Audio/Visual Sensing"
type: concept
tags: [plan, forensics, audio, clap, panns, vision, sensing, reverse-engineering, style-profiles, research-handoff]
sources: 0
status: in-progress
updated: 2026-06-13
---

# Plan — Clip Forensics + Semantic Audio/Visual Sensing

Filed 2026-06-13. **Goal:** give the pipeline the *senses* it lacks ([[concepts/model-senses]]) so it can (a) **decompose a curated competitor clip into its editing "essence"** — what SFX/music/censor/cut happened where — and emit a **replicable style profile**, and (b) reuse that same semantic-sensing layer in the live pipeline (anomaly proposer + acoustic SFX placement). One shared sensing layer, two consumers.

> [!success] Phase 2 SHIPPED + models verified (2026-06-21) — censor detection + music-bed "added" heuristic
> **Models downloaded + verified producing real output** (see [[entities/audio-sense-module]] for exactly which models + install commands): CLAP `laion/clap-htsat-unfused` (1.2 GB), faster-whisper `base` (142 MB), PANNs CNN14 (327 MB, opt-in). Real run on `ReemKnocksClip.MP4` (17.7 s): CLAP → **14 events** (`bruh` cluster + `boing`/`whoosh`), faster-whisper → **18 words**, scenedetect → **6 cuts**, full timeline written. Phase-2 logic: `_detect_censor()` (better-profanity word + co-located censor-SFX = high conf; beep/quack in a word-gap = medium) and `_music_bed()` (merge CLAP music events into spans, flag `added` when the span starts on an abrupt onset AND overlaps speech) **unit-verified** on synthetic inputs (word+SFX, bleeped-gap, music added/not-added all fire correctly); they correctly returned empty on the ReemKnocks clip (uncensored curse, no music bed = no false positives). Deliberately **no inaSpeechSegmenter** (avoids pulling TensorFlow onto the CUDA/torch rig) — music-bed uses the event stream + a numpy onset detector. **Three env fixes drove the final defaults** (all in [[entities/audio-sense-module]]): PANNs stalls on torch≥2.9 → **opt-in** (`CLIP_AUDIO_SENSE_PANNS=1`), CLAP is the default backend; tool defaults to **CPU** (Windows CUDA hung the checkpoint load; `--cuda` opts in); `onset_times()` rewritten from librosa (which **hung** on numba/peak_pick) to a **pure-numpy energy-flux** picker. Phases 3-4 (caption OCR, optical-flow motion, exact-SFX fingerprint, LLM style-profile synthesis) remain stubbed.

> [!success] Phase 1 BUILT (2026-06-13) — audio_sense + clip_forensics
> The offline lane is implemented: **`scripts/lib/audio_sense.py`** (shared sensing layer — `sense_events()` = PANNs CNN14 framewise + CLAP zero-shot, merged + deduped; `music_segments()` = inaSpeechSegmenter; lazy imports, **failure-soft**, CPU default, JSON cache), **`scripts/research/clip_forensics.py`** (reads `reference_clips/`, emits the timeline JSON, scores vs `.notes.json`), **`config/audio_sense_labels.json`** (CLAP vocab + thresholds + PANNs keep-list), **`requirements-forensics.txt`** (commercial-safe deps + the Essentia/Demucs/MS-CLAP flags), and `CLIP_AUDIO_SENSE_LABELS` in `paths.py`. **Verified on real curated clips** (ReemKnocks 17.7 s → 6 cuts, GeorgeBush 14.4 s → 7 cuts): ffprobe duration/fps + PySceneDetect cuts run **for real**; the CLAP/PANNs/inaSpeech audio backends are **verified failure-soft** (clean `[]` when their models/deps are absent, incl. a half-installed dep). Audio events now light up for real (deps installed + CLAP/whisper cached 2026-06-21 — see the Phase 2 callout + [[entities/audio-sense-module]]). Phase 2 (censor + music-bed) shipped; Phase 3-4 (caption OCR, optical-flow motion, exact-SFX fingerprint, LLM style-profile synthesis) are stubbed with TODOs. No live-pipeline code touched (offline research lane only).

> [!success] Research handoff executed (2026-06-13) → [[concepts/clip-forensics-research-2026-06]]
> The deep-research run answered the brief below (25 sources, 18 verified claims). The verified tool picks, a **commercial-license matrix**, the architecture spec, data schemas, and a **ready-to-use engineering prompt** live on that page. Headlines: commercial-safe backbone = **PANNs CNN14 (MIT) + LAION-CLAP zero-shot + inaSpeechSegmenter (MIT) + better-profanity (MIT) + EasyOCR/PySceneDetect**; **Essentia mood is AGPL/non-commercial → dropped** (use a CLAP "suspenseful" prompt); **Demucs weights are research-only** (offline cross-check only, never shipped); **MS-CLAP license unconfirmed** → prefer LAION-CLAP. Build phase 1 = `audio_sense.py` (CLAP+PANNs) + a minimal `clip_forensics.py` timeline.

> [!note] Why this is the keystone
> The owner's whole objective is "replicate the patterns of clips that get reach." Today the pipeline **cannot perceive** those patterns: no semantic audio recognition, vision sees stills not motion ([[concepts/model-senses]] §blind spots). Build the sensing layer and three open threads unlock at once: this forensics tool, the [[concepts/case-incongruity-comedy]] anomaly proposer, and better placement for the [[concepts/sfx-cue-taxonomy-2026-06]] cues.

> [!note] Scope for this phase
> **Local curated reference clips only — no yt-dlp URL extraction yet** (deferred by owner). The implementation pulls source clips from a dedicated `reference_clips/` folder the owner curates by hand (see §Reference-clip corpus). URL ingestion is a later add-on.

---

## Part B — The toolbox (what fills the sensing gap, all local, all small)

There is no one-click "clip → editing recipe" product, but every component is open-source and tiny next to the LLMs — the RTX 5060 Ti 16 GB rig runs them trivially (most on CPU). Candidate tools per sub-capability (the research phase picks winners):

| Sub-capability | What it detects | Candidate tools | Notes |
|---|---|---|---|
| **Semantic audio events** (the star) | Arbitrary SFX by *describing* them — "vine boom", "record scratch", "air horn", "duck quack", "applause", "censor beep" | **CLAP** (LAION-CLAP / MS-CLAP) zero-shot; **PANNs** (CNN14, AudioSet 527-class), **YAMNet**, **AST**, **BEATs** | CLAP = prompt-driven zero-shot (best for an open SFX vocabulary). PANNs/YAMNet = fixed AudioSet ontology (Music, Laughter, Applause, "Cartoon", "Sound effect"…). ~15 MB–600 MB |
| **Music-bed detection** ("did the editor add music, and where?") | Music vs speech segmentation; abrupt music onset on a cut | **inaSpeechSegmenter** (purpose-built music/speech); **Demucs** stem split (already in repo via `vocal_sep.py`) + onset; librosa onset | Demucs trick: music/other-stem energy where speech is quiet = added bed; abrupt onset on a cut = editor-added, not ambient |
| **Music mood** ("suspenseful") | tension/dark/dramatic/epic tags | **Essentia** (MTG) pretrained mood/theme models | Fuzziest signal — useful but not reliable; flag as low-confidence |
| **Exact known-SFX match** (most precise) | "*this specific* vine boom from *this* soundboard is at t=7.2 s" | audio fingerprinting (**audfprint** / **Panako** / Chromaprint) or spectrogram cross-correlation vs a reference SFX library | Needs a seeded library of the soundboard sounds to match against |
| **Censor detection** | quack/beep over a curse | Whisper word-timings + profanity lexicon (expected curse position) + CLAP/PANNs burst at that timestamp | High precision because it checks a *predicted* location |
| **Visual edit decomposition** | cuts, captions, zoom punches, freeze, speed ramps | **PySceneDetect** (cuts); **EasyOCR/PaddleOCR** (caption text + density); OpenCV optical-flow (zoom/motion, dep already present via `face_pan.py`); vision LLM (shot description) | The visual half of "essence" |
| **Synthesis → template** | the editing recipe as data | the existing LLM | Merge all signals into a timeline/EDL → LLM emits a reusable style profile |

---

## Part C — How it plugs into the pipeline (two payoffs)

1. **Upgrade [[entities/audio-events]] from DSP dials → semantic sensing.** Replacing/augmenting the three librosa scalars with CLAP/PANNs event scores is the single change that feeds: the **anomaly-proposer** ([[concepts/case-incongruity-comedy]] — finally "hears" the bus-clip shout / boom), **SFX placement** ([[concepts/sfx-cue-taxonomy-2026-06]] — put a boom where competitors put booms), and the **originality goal** ([[concepts/plan-unoriginality-audio-layer]] — know what to add).
2. **New clip-forensics tool.** Curated reference clip → decomposition → **style profile** that drops into the existing `style_profiles.py` / `edit_plan.py` / `config/sfx_cues.json` structures. The renderer already consumes those, so a decomposed competitor recipe becomes a tunable config with no new render code.

Both consumers share one `scripts/lib/audio_sense.py` (semantic sensing) + a `scripts/research/clip_forensics.py` (offline decomposition). Keep the live-pipeline use **flag-gated + failure-soft** per repo convention; the forensics tool is offline (research lane), so it can be heavier.

---

## Reference-clip corpus (this phase's input)

- **Folder:** `reference_clips/` at repo root (scaffolded 2026-06-13 with a README). The owner drops curated competitor clips here. **Not** `vods/` (that's clip-input, Stage 1 scans it) and **not** `assets/` (that's injection material).
- **Per-clip annotation sidecar** (optional, owner-authored): `reference_clips/<name>.notes.json` — what the owner thinks works ("suspense music in at 0:04; quack censor 0:09; vine boom on punchline 0:12"). Gives the forensics output a human ground-truth to validate against.
- **Git:** media gitignored (binary, large); README + `.notes.json` sidecars tracked.
- yt-dlp URL ingestion is deferred — the corpus is hand-curated for now.

---

## Limits (be honest in the plan)
- It's a **build**, not an install — a few hundred lines wiring these libraries; no end-to-end product exists.
- **Mood ("suspenseful")** is the least reliable signal; event detection (boom/quack/applause/music-on) is solid.
- **Exact-SFX ID** needs a seeded reference library of the target sounds.
- **Editor-added vs stream-native music** is a heuristic (Demucs stem + abrupt-onset), not certain.

---

## Unified Research Handoff Brief

> Hand this section to a research agent. Its job: **research + verify the tool choices below, then produce (1) a concrete implementation architecture and (2) an engineering prompt** the owner can hand to a coding agent. It should NOT write the production code itself.

### Objective
Design a **local, offline-first semantic sensing layer** and a **clip-forensics decomposer** that turns a curated reference clip into a replicable style profile, and specify how the same sensing layer later upgrades live detection. Unify both (B = the sensing tech, C = the two consumers) into one architecture.

### Hard constraints
- **Local-only, no cloud APIs.** Runs on RTX 5060 Ti 16 GB / i9-13900K / 64 GB (see the hardware-specs memory). Prefer CPU-runnable for the offline tool.
- **Commercial-use licenses** for anything that ships in the render path (the channel is monetized) — but the *offline analysis* tools can be any OSS license since their output is data, not shipped assets. Flag any model whose license restricts commercial use.
- **Conventions:** failure-soft, flag-gated for any live-pipeline change, config-driven, must follow the wiki-update + commit mandate ([[overview]] / project CLAUDE.md).
- **This phase = local `reference_clips/` folder; no yt-dlp.**

### Research questions (verify, don't assume)
1. **Audio tagger choice:** CLAP vs PANNs vs BEATs vs YAMNet for an *open, describable* SFX vocabulary on this hardware — accuracy on real meme SFX, latency per minute of audio, model size, license. Is zero-shot CLAP good enough, or is a fixed AudioSet tagger more reliable for the common kinds (music/laughter/applause)? Likely answer: **CLAP for open vocab + a fixed tagger as a cross-check** — verify.
2. **Music-bed detection:** inaSpeechSegmenter vs Demucs-stem-energy vs librosa onset — which most reliably flags "editor added music here" and distinguishes it from stream-native audio? (Demucs is already a dep.)
3. **Mood/suspense:** is Essentia mood good enough to be worth shipping, or should "suspenseful music" be left as a CLAP prompt? Quantify reliability.
4. **Exact-SFX fingerprinting:** audfprint vs Panako vs simple spectrogram cross-correlation for matching short (<2 s) SFX against a seeded library — accuracy/false-positive rate on overlapping speech.
5. **Censor detection:** verify the transcript-gap + profanity-lexicon + audio-burst approach; what lexicon/threshold.
6. **Visual decomposition:** PySceneDetect params for short-form; OCR engine choice (EasyOCR vs PaddleOCR — note PaddleOCR's prior wedge history in [[concepts/chrome-masking]]); optical-flow zoom/motion detection approach.
7. **Synthesis format:** the timeline/EDL JSON schema, and the mapping from decomposed timeline → a `style_profiles.py`/`edit_plan.py`/`config/sfx_cues.json`-shaped output.

### Candidate tool matrix to fill in
For each tool: role · model size · CPU or GPU · license (commercial OK?) · maturity · accuracy on the reference clips · integration friction. Cover: CLAP, PANNs/CNN14, YAMNet, AST, BEATs, inaSpeechSegmenter, Demucs (already present), Essentia, audfprint/Panako/Chromaprint, PySceneDetect, EasyOCR/PaddleOCR, OpenCV optical flow.

### Evaluation method
Use the owner's curated `reference_clips/` + their `.notes.json` human annotations + the 4 existing competitor transcripts (`B:\AuxCoding\VideoToText-main\transcripts\`, analyzed in [[concepts/case-incongruity-comedy]]) as the validation set. Score each sub-capability: does it recover the human-annotated events (music-in, censor, boom, cuts) with acceptable precision?

### Deliverables the research agent must produce
1. **Verified tool selection** per sub-capability (the matrix, filled, with the pick + rationale).
2. **Architecture spec:** `scripts/lib/audio_sense.py` (the shared sensing layer — API, inputs/outputs, caching) + `scripts/research/clip_forensics.py` (offline decomposer — reads `reference_clips/`, emits timeline JSON + a style profile) + the live-pipeline integration point (how `audio_events.py` / the anomaly proposer would consume `audio_sense`).
3. **Data schemas:** the timeline/EDL JSON; the `.notes.json` sidecar; the style-profile output and its mapping to `edit_plan.py` / `config/sfx_cues.json` / `style_profiles.py`.
4. **Reference-clip folder spec** (confirm/refine `reference_clips/` layout + sidecar).
5. **The engineering prompt** — a self-contained prompt the owner hands to a coding agent to build the above, including: the chosen tools + install lines, the file-by-file plan, the failure-soft/flag-gated/config conventions, the integration points (file:line anchors from [[concepts/model-senses]] + this page), the verification plan (smoke tests on `reference_clips/`), and the wiki-update + commit obligations.
6. **Phasing:** what to build first (recommend: CLAP-based `audio_sense` + a minimal `clip_forensics.py` that prints a timeline for one local clip), and what's deferred (mood, exact-SFX library, visual decomposition, yt-dlp).

### Integration points (for the agent)
- Sensing upgrade target: `scripts/lib/audio_events.py` ([[entities/audio-events]]) — currently 3 librosa dials.
- SFX placement consumer: `scripts/lib/sfx_cues.py` + `config/sfx_cues.json` ([[concepts/sfx-cue-taxonomy-2026-06]]).
- Style-profile output target: `scripts/lib/style_profiles.py`, `scripts/lib/edit_plan.py` ([[concepts/style-profiles]]).
- Anomaly-proposer consumer: [[concepts/case-incongruity-comedy]].
- Existing source-separation dep to reuse: `scripts/lib/vocal_sep.py` (Demucs).

---

## Related
- [[concepts/model-senses]] — the perception inventory this plan fills the gaps in (Part A)
- [[concepts/case-incongruity-comedy]] — the anomaly proposer that consumes the new sensing
- [[concepts/sfx-cue-taxonomy-2026-06]] — SFX placement that the sensing improves
- [[concepts/plan-unoriginality-audio-layer]] — the originality goal this serves
- [[entities/audio-events]] · [[entities/vocal-sep-module]]
