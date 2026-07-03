---
title: "Master-Proposal Research — RQ1-RQ4 consolidated findings (2026-07)"
type: concept
tags: [research, omni, llama-cpp, lm-studio, funnynet, smile, chat-ocr, meme-library, fusion, reference]
sources: 23
status: reference
updated: 2026-07-03
---

# Master-Proposal Research (2026-07) — RQ1-RQ4 findings

Deep-research output for [[concepts/master-proposal-2026-07]] §5 (all four prompts in one combined run, 2026-07-03).

> [!warning] Methodology — verification layer died; treat every claim as single-fetch UNVERIFIED
> The pipeline ran clean through search + fetch + extraction: **5 angles → 23 sources (21 primary) → 114 claims**. The 3-vote adversarial verification layer then failed **completely** — all 25 selected panels hit the Anthropic session limit (the third recurrence of this pattern). So NOTHING below carries a real confirmed/refuted verdict. Mitigations: claims were extracted from **primary sources fetched 2026-07-03** (official docs/repos/model cards/papers), and quotes are specific; but a single fetch can misread. **The run is resumable**: `Workflow({name:"deep-research", resumeFromRunId:"wf_edb4d979-c18"})` after the limit resets re-runs ONLY the verify+synthesize phases (search/fetch results are cached). High-stakes RQ1 claims should also be hands-on validated (they're one `llama-server` command away).

---

## RQ1 — Local omni serving: **conditional GO — the dual-GPU catch-22 DISSOLVES**

The [[concepts/multimodal-fusion-2026-07]] catch-22 was "the server that owns the 28 GB pool (LM Studio) can't hear; the servers that hear (PyTorch) only reach the 16 GB card." The research found the missing third path: **`llama-server` — the same llama.cpp engine LM Studio wraps — natively serves omni GGUFs with audio input through its own OpenAI-compatible `/chat/completions`, on the same Vulkan dual-GPU pool.**

| Finding | Source |
|---|---|
| llama.cpp `libmtmd` officially supports **image + audio + video** input; served via llama-server's OpenAI-compat API | llama.cpp docs/multimodal.md (fetched 2026-07-03) |
| **Qwen3-Omni-30B-A3B-Instruct**: official ggml-org GGUF, one-line load (`-hf`); **Q4_K_M = 18.6 GB** + mmproj encoders **1.33 GB Q8_0** → ~20 GB + KV → **needs the 28 GB Vulkan pool, NOT the 16 GB card** | ggml-org HF repo (≈Apr 2026, 9.1k downloads/mo) |
| Qwen2.5-Omni 3B & 7B: official GGUFs since llama.cpp PR #13784 (May 2025); text+audio+image in, **no video, no audio-out** ("input-only omni" — talker never implemented) | PR #13784, discussion #13759 |
| **LM Studio itself: still text+images only** (fetched 2026-07-03 — no audio/video content parts, no audio endpoints, open unanswered feature request since 2026-03-31) | lmstudio.ai/docs, bug-tracker #1715 |
| Qwen2.5-Omni-7B **GPTQ-Int4 (CUDA lane): 11.64 GB @ 15 s video, 17.43 @ 30 s, 29.51 @ 60 s** (+"actual ≥1.2×") → the 16 GB card handles only ~15 s **video** windows; audio-only input is far cheaper | Qwen model card / QwenLM README |
| vLLM upstream serves Qwen3-Omni (thinker/text-out only); transformers needs ≥5.2 + `decord` (often not installable on native Windows → WSL2 for video) | QwenLM/Qwen3-Omni |
| Licenses: Apache-2.0 (commercial OK) | model cards |

**Risk flags (why "conditional"):** maintainer labels audio input **"highly experimental"**; a Dec-2025 user report shows llama-server's OpenAI endpoint rejecting an audio content part (400) for some request shapes; an **AMD Vulkan (RADV) crash on Q8_0 mmproj** with `--no-mmproj-offload` as workaround — directly relevant to this rig's AMD card in the pool. And LM Studio + llama-server would contend for the same VRAM — omni judging is a **swap-in phase**, not co-resident.

**Recipe to validate (one command):** `llama-server -hf ggml-org/Qwen3-Omni-30B-A3B-Instruct-GGUF` (Vulkan build; try `--no-mmproj-offload` if the AMD FPE crash appears), then POST audio to `/v1/chat/completions`. → **A7 moves from "deferred on tooling" to "pending a hands-on smoke test."**

## RQ2 — Anomaly-proposer: **the symbolic-timeline bet has direct academic support**

The headline: **SMILE/SMILE-Next found a text-only LLM fed a *textualized* multimodal representation BEATS video-LLMs and audio-visual LLMs on laughter tasks** — BLEU4 0.279/0.270 vs Video-LLaMA 0.226; preferred over a video-LLM at **55.7%** and over an AV-LLM at **69.0%** win rate; transcript-only lost to full-fusion in 72.2% of human evals. SMILE's representation = visual cues (facial expressions + scene descriptions) + **prosody stats (pitch/intensity/jitter/shimmer)** + transcript — near-identical to our planned timeline. *Our option-1-over-option-5 ranking was the literature's answer before we ran our own benchmark.*

Design parameters that transfer directly:
- **Window: ~8 s** pre-reaction (FunnyNet ablated 2–16 s; 8 optimal), frames at 1 FPS, audio 16 kHz log-Mel.
- **Laughter-anchored auto-labeling**: "the n seconds before a detected laugh = funny" — free training/eval labels from our own corpus; FunnyNet's unsupervised laughter detector (stereo voice-removal → energy peaks → K-means on audio embeddings) hits 78.4% temporal / 95.2% detection precision.
- **Audio is the single most discriminative modality** for funny-moment detection (pitch changes + pauses); generalizes out-of-domain.
- **Few-shot is load-bearing for the LLM verifier**: LLaMa-2 judging transcripts = 71.1 F1 with task prompt + 20 examples, **14.5 F1 generic zero-shot**. Our verifier prompt must ship exemplars.
- MSAM's interpretability gap ("attended words are often not identifiably humorous") is direct motivation for the *unexplained-reaction* lane; it windows by **dialogue turns**, an alternative to fixed seconds.
- **Critical transfer caveat**: FunnyNet zero-shot on laugh-track-free content = **55.4% vs 50% chance** — reaction-anchored detection does NOT transfer to deadpan material. Confirms the "accept the deadpan miss" scope guard; streams (with chat/co-streamer laughter) keep the anchor.
- **25-clip benchmark method** (BottleHumor pattern): LLM-based precision/recall of extracted statements aggregated as macro-F1 vs human references — works at small corpus size; BottleHumor also shows 7B-class open VLMs are viable for the *explanation/verifier* stage (+F1 up to 8.2 via iterative knowledge elicitation).

## RQ3 — Chat-overlay mining: **7 s forward window is the empirical lag anchor**

- **The lag number exists**: the EMNLP-2017 Twitch highlight study aligned chat to video with a **FORWARD window after each frame, swept 5–9 s → 7 s optimal**. Seed our per-channel lag at **7 s**, then auto-calibrate (cross-correlate CLAP laughter × chat velocity) — Twitch publishes **no official numeric latency** (Low Latency is default-on since Mar 2019, per-viewer heterogeneous, live-only Video Stats readout), so empirical calibration is the *only* robust approach. Our design already said this; now it has a literature-grounded starting value.
- **Chat is worth mining**: chat+video fusion beat video-only (74.7 vs 72.2 F) and chat-only (43.2) for highlight detection. **Char-level beats word-level by 22.3 F** on Twitch text (misspellings/elongations/emote-words) — burst n-gram extraction should operate on characters/substrings, not clean tokens.
- **The OCR mechanics have prior art with exact parameters** (videocr-PaddleOCR): frame-differencing gates (`similar_image_threshold`, `similar_pixel_threshold`), **Levenshtein dedup at sim 80/100**, per-word **confidence floor 75**, optional brightness-threshold preprocessing, `frames_to_skip` sampling; **no existing tool auto-detects the region** (default = bottom third) — our text-density auto-ROI is the novel piece. Port the parameter shapes to EasyOCR.
- **Structured path**: TwitchDownloader exports full VOD chat to **JSON** (canonical intermediate; MIT; native Windows; active — v1.56.4 Feb 2026). Emotes confirmed as **image assets** (BTTV/FFZ/7TV) — invisible to OCR, caught by velocity.
- Bonus: ground-truth harvesting pattern — align community highlight reels back to VODs via color-grid template matching → free labels for the calibration loop (B-workstream).

## RQ4 — Meme-format library: **precision-first, classical-features-first, scrape-yourself**

- **Seed sources**: KYMKB (5,220 templates + 49,531 instances from KnowYourMeme) and CM50 (33k memes / 50 templates) prove KYM is machine-harvestable — **but KYMKB does NOT redistribute data** (KYM policy); a commercial pipeline must scrape its own copy (caution for monetized use). CM50's annotation schema is the template: `{template, title, image caption, meme caption, embedded text, literary devices}` + **KYM "About" text injected into prompts** — that grounding lifts a local open annotator to **89.8% of GPT-4o quality (vs 82.3% without)**.
- **Matching method**: zero-shot nearest-neighbor over embeddings with **per-template distance thresholds derived from each template's own examples** (median), global fallback for example-less entries; **precision is the governing metric** (false matches corrupt everything downstream). Template-first clustering beats one-shot clustering **0.87 vs 0.54** consistency at 11k images.
- **Two cautions that flip defaults**: (a) **CLIP underperformed a text-blind ViT** for template clustering — keep visual and textual channels separate rather than defaulting to joint embeddings; (b) **a prompted 7B VLM underperformed classical similarity measures** on non-template matching — use embeddings+thresholds for matching, reserve the LLM for the `known_format` probe/explanation.
- **The gap that makes ours novel**: all of this literature is *image*-meme matching. No existing library covers **skit/audio/video formats** (the George-Bush class) — our `meme_formats.json` (verbal trigger + visual signature + audio cue, embedding-matched via the repo's sentence-transformers stack, grown from `.notes.json` + forensics decompositions) has no off-the-shelf equivalent to buy; the schema and precision discipline above are what transfer.

---

## Consequences for the roadmap ([[concepts/master-proposal-2026-07]])
1. **A7 (omni)**: deferred → **"pending hands-on smoke test"** (llama-server + Qwen3-Omni GGUF on the Vulkan pool; watch the AMD mmproj crash + experimental-audio flags). LM Studio itself confirmed still deaf.
2. **A1 (anomaly lane)**: unchanged plan, now with literature-backed parameters (8 s windows, prosody features added to the timeline, few-shot verifier, laughter-anchored auto-labels) and *prior evidence the symbolic approach beats omni watching* — the 25-clip A/B may be confirmatory rather than decisive.
3. **A2 (chat mining)**: seed lag = **7 s forward**, char-level burst extraction, port videocr's dedup/confidence parameters; auto-ROI remains our novel piece.
4. **A4 (meme library)**: adopt CM50 schema + per-template thresholds + classical-features-first matching; scrape KYM ourselves; the audio/skit dimension is greenfield.
5. **Verification debt**: resume `wf_edb4d979-c18` after the session-limit reset to run the adversarial panel over these claims.

## Related
- [[concepts/master-proposal-2026-07]] · [[concepts/multimodal-fusion-2026-07]] · [[concepts/reference-humor-2026-07]] · [[concepts/case-incongruity-comedy]] · [[concepts/plan-calibration-loop]]
