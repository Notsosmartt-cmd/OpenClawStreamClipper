---
title: "Clip Forensics — Research Output + Engineering Prompt (2026-06)"
type: concept
tags: [research, forensics, audio, clap, panns, ocr, licensing, handoff, reference]
sources: 0
status: reference
updated: 2026-06-13
---

# Clip Forensics — Research Output + Engineering Prompt

Executes the Unified Research Handoff Brief in [[concepts/plan-clip-forensics]]. This is the **filled-in answer**: verified tool selection + license flags, the architecture spec, data schemas, and a self-contained engineering prompt to hand a coding agent.

> [!note] Methodology + confidence
> `deep-research` ran clean through search → fetch → **adversarial verify (25 sources → 114 claims → 25 verified → 18 confirmed / 7 refuted)**; only the final *synthesis* step crashed on the Anthropic session limit, so this synthesis is authored by hand from the 18 confirmed claims (each cited with its vote). Confidence: **V** = vote-verified this run · **S** = sourced (in the 25-source set) but its specific claim wasn't in the 25-claim verify sample → treat as medium · **K** = orchestrator domain knowledge, unverified. License facts are the highest-value verified output and drive the picks.

---

## Verified license matrix (the decision-driver)

For a **monetized** channel, anything that ships in the live render path must be commercial-OK. Offline-analysis tools whose *output is just data* (timeline JSON) are unconstrained.

| Tool | License | Commercial-OK? | Conf |
|---|---|---|---|
| **panns_inference** (PANNs CNN14) | MIT | ✅ yes | V (3-0) |
| **YAMNet** | Apache-2.0 | ✅ yes | V (3-0) |
| **inaSpeechSegmenter** | MIT | ✅ yes | V (3-0) |
| **better-profanity** (lexicon) | MIT | ✅ yes | V (3-0) |
| **PySceneDetect** | BSD-3 | ✅ yes | K |
| **EasyOCR** | Apache-2.0 | ✅ yes | S |
| **LAION-CLAP** (HF `ClapModel`) | Apache-2.0 integration (checkpoint varies) | ✅ likely — verify checkpoint | K |
| **MS-CLAP** (microsoft/CLAP) | **UNCERTAIN** (MIT claim voted 1-2, not confirmed) | ⚠️ verify before shipping | V (1-2, refuted) |
| **Demucs** code | MIT, **but pretrained WEIGHTS are research-only** | ⚠️ offline/data-output OK; do NOT ship weights | V (3-0) |
| **Essentia + MTG mood models** | **AGPLv3 + non-commercial models** | ❌ no (needs paid MTG license) | V (3-0) |
| **Panako** (fingerprint) | AGPL | ⚠️ flag | K |
| **audfprint** (fingerprint) | permissive (Dan Ellis) | ✅ likely | K |
| **BEATs** | not verified this run | ⚠️ verify | — |

**Takeaways:** the cheap, commercial-safe backbone is **panns_inference (MIT) + inaSpeechSegmenter (MIT) + better-profanity (MIT) + EasyOCR/PySceneDetect**. **Essentia mood is out** (AGPL/NC) — use a CLAP "suspenseful music" prompt instead. **Demucs** stays offline-only (your `vocal_sep.py` use is fine; never ship the weights in a render). **MS-CLAP license is unconfirmed** — prefer **LAION-CLAP via HF `transformers.ClapModel`** for anything live.

---

## Recommended local stack (per sub-capability)

| Sub-capability | Pick | Why | Conf |
|---|---|---|---|
| **Semantic audio events (open vocab)** | **LAION-CLAP** (HF `ClapModel`) zero-shot over a SMALL describable label set (boom/scratch/quack/airhorn/applause/riser/boing) | CLAP is strong on *small, distinct* class sets and weak as a broad tagger (ReCLAP: 26.1% AudioSet, 29.2% VGGSound) — the clipper's ~10-label vocab is squarely in CLAP's strong regime | V (2-1) |
| **Common audio classes + temporal localization** | **PANNs CNN14** (`panns_inference`) — `framewise_output` for per-frame SED + `clipwise_output` | MIT, gives both tagging and **temporal localization** (when, not just what); covers music/laughter/applause reliably | V (3-0) |
| — CPU-only alternative | **YAMNet** or MobileNetV3-AudioSet (`mn10_as`) | YAMNet Apache-2.0, 521 classes, 16 kHz mono → scores + 1024-d embedding; MobileNetV3 lightweight runs <0.25 s/10 s even on a Raspberry Pi 4B | V (3-0) |
| **Music-bed detection** | **inaSpeechSegmenter** (speech/music/noise CNN) | MIT, purpose-built, CPU; cross-check with Demucs-stem energy *offline only* | V (3-0) |
| **Music mood ("suspenseful")** | **CLAP prompt** ("suspenseful/tense music") — NOT Essentia | Essentia AGPL + NC models = unshippable; mood is low-confidence regardless | V (3-0) |
| **Exact known-SFX match** | **audfprint** (or spectrogram cross-correlation) vs a seeded SFX library | handles short queries; permissive license; avoid **Panako (AGPL)** | S |
| **Censor detection** | build: Whisper word-gaps + **better-profanity** predicted position + CLAP/PANNs burst at that t | no off-the-shelf Python tool (bleep-that-shit is browser-only, Transformers.js, 10-min cap) | V (3-0) |
| **Scene cuts** | **PySceneDetect** `ContentDetector` (fast-cut short-form); `AdaptiveDetector` for camera motion | BSD-3, standard | S |
| **Caption OCR** | **EasyOCR** (Apache-2.0, maintained) | prefer over PaddleOCR given this repo's PaddleOCR wedge history ([[concepts/chrome-masking]]) | S |
| **Zoom/motion** | **OpenCV** optical flow / frame-diff (already a dep via `face_pan.py`) | no new dep | K/S |
| **Synthesis → style profile** | the existing LLM | merge the timeline → emit an `edit_plan`/`sfx_cues`-shaped profile | K |

### Strongest single recommendation
**Hybrid audio sensing: PANNs CNN14 (fixed AudioSet backbone, MIT, framewise SED) + LAION-CLAP zero-shot (open meme-SFX vocab).** This is the plan's original hypothesis, now evidence-backed: CLAP alone collapses on broad tag sets, fixed taggers miss the meme vocab, so run both and merge.

---

## Per-RQ findings (cited)

- **RQ1 audio tagger:** zero-shot CLAP works for a *small describable* vocab but is weak as a broad tagger (ReCLAP 26.1% AudioSet, [arXiv:2409.09213](https://arxiv.org/html/2409.09213v1), V 2-1). MS-CLAP supports open-vocab zero-shot ([arXiv:2206.04769](https://arxiv.org/abs/2206.04769), V 3-0). PANNs CNN14 = `Cnn14_mAP=0.431.pth`, MIT, framewise+clipwise ([panns_inference](https://github.com/qiuqiangkong/panns_inference), V 3-0). BEATs SOTA 50.6% mAP ([arXiv:2212.09058](https://arxiv.org/abs/2212.09058), V 2-1) but heavier/license-unverified. MobileNetV3 lightweight is the CPU-efficient option ([arXiv:2509.14049](https://arxiv.org/html/2509.14049v1), V 3-0). *Refuted:* that CNN14 is "thermally dangerous" on CPU (0-3 — it's heavier, not unusable); that prompt-describing acoustics boosts accuracy 1-18% (0-3).
- **RQ2 music-bed:** inaSpeechSegmenter = MIT CNN speech/music/noise ([ina-foss](https://github.com/ina-foss/inaSpeechSegmenter), V 3-0). Demucs stem split works but **weights research-only** ([demucs#327](https://github.com/facebookresearch/demucs/issues/327), V 3-0).
- **RQ3 mood:** Essentia AGPLv3, models non-commercial, commercial needs paid MTG license ([essentia licensing](https://essentia.upf.edu/licensing_information.html) + [models](https://essentia.upf.edu/models.html), V 3-0). → use CLAP prompt instead.
- **RQ4 fingerprint:** audfprint + Panako sources fetched ([audfprint](https://github.com/dpwe/audfprint), [Panako](https://github.com/JorenSix/Panako)); specific accuracy claims not in the verified sample (S).
- **RQ5 censor:** better-profanity MIT ([pypi](https://pypi.org/project/better-profanity/), V 3-0); no usable off-the-shelf tool — bleep-that-shit is browser-only ([repo](https://github.com/neonwatty/bleep-that-shit), V 3-0). Build the transcript+lexicon+burst approach.
- **RQ6 visual:** PySceneDetect ([docs](https://www.scenedetect.com/docs/latest/api/detectors.html)), EasyOCR Apache-2.0 ([license](https://github.com/JaidedAI/EasyOCR/blob/master/LICENSE)), PaddleOCR-vs-Tesseract ([codesota](https://www.codesota.com/ocr/paddleocr-vs-tesseract)), optical flow ([learnopencv](https://learnopencv.com/optical-flow-in-opencv/)) — all S.
- **RQ7 prior art:** no slam-dunk reverse-EDL project surfaced → confirms it's a **build**, not an install.

---

## Architecture spec

**`scripts/lib/audio_sense.py`** — the shared semantic-sensing layer (the reusable dependency).
- API: `sense_events(wav_path, *, window_s=1.0, hop_s=0.5, labels=None) -> list[{t, end, label, score, source}]` where `source ∈ {clap, panns}`. Merge CLAP (open vocab) + PANNs framewise (common classes) per window; dedup overlapping labels.
- `music_segments(wav_path) -> list[{start, end, kind: speech|music|noise}]` (inaSpeechSegmenter).
- Lazy-import heavy deps; **failure-soft** (missing model → `[]`, log to stderr). Cache results to `{work}/audio_sense_<hash>.json`.
- CPU default; optional CUDA. Models cached under `models/` (gitignored).

**`scripts/research/clip_forensics.py`** — offline decomposer (research lane, can be heavy).
- Input: a clip from `reference_clips/`. Pipeline: extract wav (ffmpeg) → `audio_sense.sense_events` + `music_segments` → PySceneDetect cuts → EasyOCR caption sample → optical-flow zoom/motion → optional audfprint match vs a seeded SFX library → censor pass (Whisper words + better-profanity + burst).
- Output: a **timeline JSON** (below) + an LLM-synthesized **style profile**. Validate against the clip's `.notes.json` if present (precision/recall on annotated events).

**Live integration (later):** `scripts/lib/audio_events.py` ([[entities/audio-events]]) gains an opt-in path (`CLIP_AUDIO_SENSE=1`, default off, failure-soft) that calls `audio_sense` and exposes named events to the [[concepts/case-incongruity-comedy]] anomaly proposer + [[concepts/sfx-cue-taxonomy-2026-06]] placement.

---

## Data schemas

**Timeline / EDL** (`clip_forensics.py` output):
```json
{
  "clip": "reemknocks_bus.mp4", "duration_s": 12.4, "fps": 30,
  "audio_events": [{"t": 7.2, "end": 7.6, "label": "boom", "score": 0.81, "source": "clap"}],
  "music": [{"start": 4.0, "end": 12.4, "kind": "music", "added": true, "mood": "suspenseful?", "mood_conf": "low"}],
  "censor": [{"t": 9.1, "word": "****", "sfx": "quack", "score": 0.7}],
  "cuts": [{"t": 0.0}, {"t": 2.1}], "captions": {"present": true, "wps_est": 6.2},
  "motion": [{"t": 7.2, "kind": "zoom_punch", "magnitude": 0.4}]
}
```
**Style-profile output** maps onto existing structures: `audio_events`→`config/sfx_cues.json` beat_defaults; `cuts`/`motion`→`edit_plan.py` (zoom_punches, cuts); caption density → [[concepts/captions]]; overall → a `style_profiles.py` per-category entry. **`.notes.json` sidecar:** already specified in `reference_clips/README.md`.

---

## Engineering prompt (hand this to a coding agent)

> **Task:** Build the local, offline-first clip-forensics tool + shared audio-sensing layer for the OpenClaw Stream Clipper, per `AIclippingPipelineVault/wiki/concepts/plan-clip-forensics.md` and this research page. Do NOT add cloud APIs. Follow repo conventions: failure-soft, flag-gated for any live-pipeline change, config-driven, and the wiki-update + commit mandate in the project `CLAUDE.md`.
>
> **Phase 1 (build first):**
> 1. `pip` deps (commercial-safe): `laion-clap` or `transformers` (`ClapModel`), `panns-inference`, `inaSpeechSegmenter`, `better-profanity`, `scenedetect[opencv]`, `easyocr`. Pin versions; add to a new `requirements-forensics.txt` (offline lane, not the base install). Demucs is already present (`scripts/lib/vocal_sep.py`) — reuse for offline music-stem cross-check ONLY (weights are research-only; never ship them in a render).
> 2. Create `scripts/lib/audio_sense.py` with `sense_events()` (LAION-CLAP zero-shot over a small label set from a new `config/audio_sense_labels.json` + PANNs CNN14 framewise, merged) and `music_segments()` (inaSpeechSegmenter). Lazy imports, failure-soft (`[]` + stderr on any missing model), JSON cache in the work dir. CPU default, optional CUDA.
> 3. Create `scripts/research/clip_forensics.py`: read one clip from `reference_clips/`, run audio_sense + music_segments + PySceneDetect `ContentDetector`, print the timeline JSON above. Defer OCR/optical-flow/censor/fingerprint to Phase 2-4 (stub with TODOs).
> 4. Smoke-test on a file in `reference_clips/`; if a `.notes.json` exists, print recovered-vs-annotated event precision.
>
> **Conventions + integration points** (file:line anchors in [[concepts/model-senses]] + [[concepts/plan-clip-forensics]]): live-pipeline hook is `scripts/lib/audio_events.py` (currently 3 librosa dials) behind `CLIP_AUDIO_SENSE=1` (default off); SFX consumer is `scripts/lib/sfx_cues.py`/`config/sfx_cues.json`; style-profile target is `scripts/lib/style_profiles.py`/`scripts/lib/edit_plan.py`. **Licenses:** prefer LAION-CLAP over MS-CLAP (MS-CLAP license unconfirmed); never ship Essentia (AGPL/NC) or Demucs weights in the render path.
> 5. **Verify** (AST + a smoke run), then **update the wiki** (flip [[concepts/plan-clip-forensics]] to in-progress→shipped for Phase 1, log entry, hot refresh) and **commit**.
>
> **Deferred:** Phase 2 censor + music-bed "added" heuristic; Phase 3 EasyOCR caption density + optical-flow zoom/motion; Phase 4 audfprint exact-SFX library + LLM style-profile synthesis; later yt-dlp ingestion.

---

## Phasing
1. **CLAP+PANNs `audio_sense` + minimal `clip_forensics` timeline** (the keystone; commercial-safe stack).
2. **Censor detection** (better-profanity + burst) + **inaSpeechSegmenter music-bed**.
3. **Visual**: PySceneDetect cuts + EasyOCR caption density + optical-flow zoom/motion.
4. **Exact-SFX fingerprint library** (audfprint) + **LLM style-profile synthesis** → `sfx_cues`/`edit_plan`.
5. **Deferred:** Essentia mood (license-blocked), yt-dlp URL ingestion.

## Related
- [[concepts/plan-clip-forensics]] — the plan + handoff brief this answers
- [[concepts/model-senses]] — the perception gap being filled
- [[concepts/case-incongruity-comedy]] · [[concepts/sfx-cue-taxonomy-2026-06]] · [[entities/audio-events]] — the consumers
