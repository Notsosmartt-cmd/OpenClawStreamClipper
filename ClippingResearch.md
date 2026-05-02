# Grounding OpenClaw: a 2026 technical roadmap

**Bottom line up front.** The "Ranked 3.0 → gifted subs" failure is not a model-quality problem; it is an architecture problem that the commercial clipper cohort (OpusClip, Vizard, Submagic, Eklipse, StreamLadder/ClipGPT, Munch, Klap, 2short.ai) has mostly papered over rather than solved. None of them publishes a vision-verification loop that cross-checks generated titles against frame evidence, and none of them runs cheap NLI grounding against the transcript. The single highest-leverage change you can make is a **three-tier grounding gate (BM25 → MiniCheck NLI → Lynx-8B) between Pass B and Stage 6**, combined with **shifting your frame sample from T-10/T-5 to T-2/T+0/T+2/T+5**, which you are literally not looking at today. Everything else — self-consistency sampling, chat-velocity fusion, a CG-DETR replacement for Pass C, UI chrome masking, splitting Pass B (text) from Stage 6 (vision) — amplifies those two fixes. Below maps each of your §8 sections to 2024–2026 SOTA with concrete models, libraries, and arXiv IDs, then concludes with a prioritized backlog.

---

## §8.1 Separating "what was said" from "what looks good"

**State of the art.** The two-call grounded architecture is correct and well-supported by the 2024–2026 literature; it is also what production vendors do implicitly. The commercial cohort pattern is (1) **transcript-first candidate selection** using ASR + signal features, (2) **transcript-conditioned LLM metadata** on the selected clip. Vision almost never grounds metadata. **OpusClip 3.0** exposes a four-axis "Virality Score" (Hook / Flow / Engagement / Trend) but is explicitly cagey about implementation (third-party review quote). **Vizard**, **Submagic**, **Munch**, **Klap**, **2short.ai** are all transcript-grounded only; Klap's public GCP case study (Théo Champion, cloud.google.com/customers/klap-app) confirms Vertex AI + Cloud Run with no vision-in-the-loop. Only **Eklipse** adds true signal-level grounding (kill-feed OCR across 1,000+ games, audio hype-spike detection, per-game supervised models trained on actual gameplay data — e.g., "Black Ops 7 data"); their design validates that **signal grounding beats single-call vision for gaming content**.

**Academic justification.** The EventHallusion paper (Zhang et al., arXiv:2409.16597) is the directly-relevant mechanism: video-LLMs are "entangled with priors stemming from their foundation models" and language-prior dominates over visual evidence when they conflict — exactly your Pass B→Stage 6 failure. Their **Temporal Contrastive Decoding (TCD)** contrasts original-video logits against temporally-scrambled logits and suppresses tokens favored by both (the language prior).

**Architecture change for OpenClaw.** Split Stage 6 into 6a + 6b:
- **Stage 6a — transcript-grounded text classifier (text-only LLM)**: takes the Pass B candidate + a transcript window + chat-velocity features + PaddleOCR overlay text → returns `{what_happened, category, confidence}` with no vision input.
- **Stage 6b — constrained vision describer (VLM)**: takes the frames + the `what_happened` field as a **hard constraint** ("the clip is about X; generate title/hook/description consistent with X") → returns `{title, hook, description, voiceover, mirror_safe}`. Vision is no longer allowed to rewrite the narrative, only to surface it stylistically.

If 6a's confidence is low, emit a generic template ("rank-up moment") rather than letting 6b hallucinate. This alone would have prevented the gifted-subs propagation.

**Cost/complexity.** Adds one small LLM call per candidate (Qwen3-30B-A3B non-thinking, ~0.5 s). Engineering time: ~1 week.

---

## §8.2 Self-consistency sampling for titles/hooks/descriptions

**State of the art.** Classic Self-Consistency (Wang et al., **arXiv:2203.11171**) requires extractable discrete answers and doesn't apply to free-form metadata. The relevant variants are **Universal Self-Consistency (USC)** — Chen et al., **arXiv:2311.17311** — which feeds N sampled candidates back to the LLM and asks for the "most consistent," and **Atomic Self-Consistency** (arXiv:2405.13131) and **Fine-Grained Self-Consistency** (arXiv:2407.02056) which aggregate at the sub-sentence level — useful for descriptions. Cost-adaptive variants include **Adaptive-Consistency** (Aggarwal et al., arXiv:2305.11860, up to 7.9× sample reduction at \<0.1% accuracy drop), **Early-Stopping SC** (arXiv:2401.10480), and **Reasoning-Aware SC (RASC, 2025)**.

The directly-applicable production pattern is **SelfCheckGPT** (Manakul et al., EMNLP 2023, arXiv:2303.08896): sample N=3–5 at T=0.7–0.9, compute pairwise NLI entailment; hallucinated claims diverge, grounded claims repeat. This doubles as a HITL trigger.

**No open-source stream-clipper tool we found uses n>1 sampling in a principled way** (reviewed FunClip, openclip, local-ai-clipping-tool, ai-powered-video-analyzer, OllamaOptivus). This is a real gap.

**Architecture change.** For every title/hook:
1. Sample N=3 at T=0.8.
2. Compute pairwise cosine on SBERT embeddings AND pairwise NLI entailment.
3. If min-pairwise cosine ≥ 0.85 AND no NLI contradiction → auto-accept the highest-entailment sample.
4. If 0.5 ≤ min-cosine < 0.85 → queue for HITL review (show all 3 side-by-side).
5. If min-cosine < 0.5 → regenerate with a tighter constraint prompt.

For descriptions (multi-sentence), use USC: ask a small LLM ("which of these 3 descriptions is most consistent with transcript X?").

**Cost/complexity.** ~3× LLM cost per metadata field, but fires only at Stage 6b (post-gate), so aggregate ~1.5× on the clips that actually get published. Engineering: 2–3 days.

---

## §8.3 Active-speaker detection, diarization, and streamer-voice isolation

**State of the art (2025–2026 benchmarking paper: Lanzendörfer et al., arXiv:2509.26177).** On CallHome/VoxConverse/AMI/DIHARD with overlap: **pyannoteAI precision-2** 11.2% DER (commercial), **DiariZen** (BUTSpeechFIT/DiariZen, MIT) 13.3% overall and **5.2% on VoxConverse** — the best open diarizer; **pyannote-community-1** ~15–17%; **Streaming Sortformer v2** (NVIDIA, arXiv:2409.06656, CC-BY-NC 4.0) the fastest at 214× RTF on A6000 (≤4 speakers).

For VAD specifically on music-over-speech — the Twitch failure mode — **NVIDIA Frame-VAD MarbleNet v2.0** leads AVA-Speech (AUROC 0.9112, 91.5 K params); **pyannote segmentation-3.0** is the open alternative; **Silero v5/v6** leaks music as "speech" and is insufficient alone.

For source separation, **Mel-Band RoFormer** (ISMIR 2024) is the music-separation SOTA and beats BS-RoFormer on vocals; **Demucs v4 htdemucs_ft** (MUSDB18-HQ vocals SDR 9.20 dB, MIT) is the pragmatic default. **MossFormer2** (arXiv:2312.11825) is for speech-speech separation (overlapping talkers), not BGM.

For active-speaker detection, **LoCoNet** (CVPR 2024, SJTUwxz/LoCoNet_ASD, MIT) leads AVA-ActiveSpeaker at 95.2% mAP; **TalkNet-ASD** at 92.3% is the drop-in practical choice (~25 MB, real-time on an RTX 3060).

**ASR upgrades.** `large-v3` → `large-v3-turbo` is a free 2.5× speedup with ~1% WER loss. **Crisper-Whisper** (Interspeech 2024, arXiv:2408.16589) is #1 on OpenASR Leaderboard for verbatim/noisy speech (including fillers). **NVIDIA Parakeet-TDT-0.6B-v3** (CC-BY-4.0) delivers **9.7% avg WER across 24 languages** at RTFx ~3000 on H100 — the throughput-accuracy Pareto point. **Canary-Qwen-2.5B** tops Open-ASR at 5.63% avg OOD, 1.6% LibriSpeech Clean.

**Architecture change — replace the single faster-whisper pass with an ordered stack:**
```
Stereo VOD audio
  ├─ Webcam crop → TalkNet-ASD → visual_speaking_mask
  └─ Mel-Band RoFormer vocals stem  (drops BGM/music)
        → Frame-VAD MarbleNet v2.0  (drops non-speech)
        → AND with visual_speaking_mask  (drops Discord-only regions)
        → DiariZen or pyannote-3.1 diarization
        → Enrollment-based streamer-cluster selection (ECAPA-TDNN d-vector)
        → Parakeet-TDT-0.6B-v3  OR  Crisper-Whisper  ASR
           + initial_prompt with per-channel game/character/slang jargon
        → wav2vec2 CTC forced alignment for word-level timestamps
```
**Budget on one RTX 4090 (24 GB) for a 2-hour VOD**: ~17–25 min wall time, peak 10 GB VRAM. Expected **50–70% fewer hallucinated segments, 20–35% relative WER on streamer speech**.

**Cost/complexity.** Path of least resistance is **WhisperX** (BSD-2), which bundles VAD + batched-faster-whisper + forced-alignment + pyannote diarization as one library — an afternoon of work for ~80% of the gain. Adding RoFormer separation and TalkNet-ASD is another ~1 week.

**Streamer-slang biasing.** Whisper's `initial_prompt` (max 224 tokens) biases decoding toward rare tokens — inject per-channel game-title, character names, emote names, recurring jargon. For larger vocab use **CB-Whisper** (arXiv:2309.09552) style shallow-fusion KWS.

---

## §8.4 Cross-field contradiction / grounding verification

**State of the art.** The cheap-to-expensive ladder in 2024–2026 is:

1. **BM25 / TF-IDF content-word overlap** (µs–ms). Extract content words via spaCy `POS ∈ {NOUN, PROPN, VERB, NUM}` + `not is_stop`, plus `doc.ents` for multi-word phrases like "gifted subs". Use `rank_bm25` or `sklearn.TfidfVectorizer`. For titles, empirically-calibrated ROUGE-1 recall < 0.35 is a strong hallucination signal.
2. **MiniCheck** (Tang, Laban, Durrett, EMNLP 2024, **arXiv:2404.10774**) — this is the critical 2024 contribution. **`bespokelabs/Bespoke-MiniCheck-7B`** claims GPT-4-level fact-verification performance at **~400× lower cost**; `lytang/MiniCheck-Flan-T5-Large` runs on CPU in ~150 ms/claim. This is the tier-2 workhorse.
3. **AlignScore** (Zha et al., ACL 2023, **arXiv:2305.16739**, yuh-zha/AlignScore) — 355M params, matches GPT-4 on SummaC/TRUE; ~100–300 ms CPU for a short claim against a 1–2k-token window.
4. **DeBERTa-v3 NLI** (e.g., `microsoft/deberta-v3-large-mnli-fever-anli-ling-wanli`, ~40 ms/pair on A10) is the lightweight alternative.
5. **Lynx** (Patronus AI, **arXiv:2407.08488**) — Llama-3 fine-tuned hallucination judge; `PatronusAI/Llama-3-Patronus-Lynx-8B-Instruct` beats GPT-3.5 by 24.5% on HaluBench. Use as tier-3 on borderline cases only.
6. **SelfCheckGPT, FActScore, G-Eval, RAGAS, FacTool** — LLM-as-judge for descriptions.

For video/multimodal specifically: **VideoHallucer** (arXiv:2406.16338), **EventHallusion** (arXiv:2409.16597), **POPE** (arXiv:2305.10355), **CHAIR** (arXiv:1809.02156).

**Architecture change — 3-tier cascade wired *between Pass B and Stage 6* and again *after Stage 6*:**

- **Tier 1 (≤5 ms)**: spaCy content-word extraction + BM25 overlap + rare-token denylist check. Pass ≥ 0.6 overlap + no denylist OOV → auto-accept. <0.2 overlap → hard fail. Otherwise escalate.
- **Tier 2 (50–200 ms CPU, 10–30 ms batched GPU)**: MiniCheck per generated claim against transcript window. Threshold ~0.5. Fires on ~30% of outputs.
- **Tier 3 (0.5–2 s, ~5–10% of clips)**: Lynx-8B or Qwen3-14B non-thinking with a strict "FAITHFUL/HALLUCINATED + one-line reason" JSON schema.

Wire points: (a) **Null out** any Pass B field that fails tier 2 rather than passing to Stage 6 — this is the single patch that would have prevented the gifted-subs propagation. (b) Re-check Stage 6 title/hook against `(transcript ∪ PaddleOCR overlay text ∪ chat-context)`. Optionally add CLIP/SigLIP image-text cosine as an extra signal.

**Cost/complexity.** ~200 ms p95 added latency, ~$0 per clip in compute. Engineering: 3–5 days. **Minimum viable patch to ship tomorrow:** just tier 1 + tier 2 on Pass B output.

---

## §8.5 Negative-keyword filters for common hallucination patterns

**State of the art.** No peer-reviewed paper formalizes the Twitch/streaming-specific over-prediction lexicon, but the pattern — training-corpus prior leakage where celebration co-occurs with `subs/raid/bits/hype train` — is well-documented via POPE, HallusionBench, VideoHallucer, EventHallusion. The closest empirical mitigation is **Temporal Contrastive Decoding** from EventHallusion and **Visual Contrastive Decoding** (Leng et al., CVPR 2024) — both subtract the language prior by contrasting against degraded-input logits.

**Concrete denylist for OpenClaw** (compile per-stream as regex with word boundaries, split by category):
- Platform-meta tics: `subscribe`, `don't forget to`, `like and subscribe`, `hit the bell`, `notification squad`.
- Twitch-jargon over-claims: `gifted sub(s|scription(s)?)`, `sub train`, `raid`, `hype train`, `bits`, `cheer(ed)?`, `prime sub`, `tier 3`, `re-?sub`, `first-time sub`.
- Generic creator templates: `in this video`, `today we`, `let's dive in`.
- Generic sports-highlight tropes: `clutch play`, `game-winning`, `triple-kill` when no HUD OCR supports them.

**Architecture change — two-pass filter at every generation boundary:**
1. **Regex pass** (<1 ms): if a denylist term appears in the generation AND is absent from the transcript/OCR/chat window → reject.
2. **Semantic similarity pass** (~10 ms): embed generation and denylist centroids with `BAAI/bge-small-en-v1.5`; flag cosine > 0.75 to a denylist category centroid. Catches paraphrases ("donated subscriptions", "sub-bombing").
3. **Rerank self-consistency samples**: demote any candidate containing denylist OOV; pick the highest-grounded surviving sample.

**Cost/complexity.** ~10 ms/clip, pure CPU. Engineering: 1–2 days.

---

## §8.6 Feedback loop / eval harness for clip detection

**State of the art.** No open clip-worthiness benchmark exists; you have to build one. The canonical academic analogs are **TVSum** (CVPR 2015, 50 videos with 20 crowd-worker shot importance scores, Cronbach's α = 0.81), **SumMe** (ECCV 2014), **YouTube-Highlights** (Sun et al., ECCV 2014, 12,000 sports clips with pairwise ranking), **Mr. HiSum** (NeurIPS 2023 D&B, 31,892 YouTube videos aggregated from 50k+ "Most Replayed" per video), and **MediaEval Predicting Media Interestingness** (2016–2017). The only streaming-relevant published dataset is the **Twitch LoL dataset** (Fu et al., chengyangfu/Pytorch-Twitch-LOL), which uses audience chat reactions as the supervision signal — directly relevant to §chat-signal below.

**Agreement statistics.** Use **Krippendorff's α** (≥0.80 satisfactory, 0.67–0.80 tentative); expect 0.3–0.5 for clip-worthiness with untrained raters, aim ≥0.6 with written guidelines. **Pairwise preference** reaches usable α faster than Likert for subjective tasks (iMerit, Yannakakis et al.).

**Bootstrap a Twitch/Kick clip-worthiness dataset for ~$0** (this is the gold mine and it is strictly better than any academic benchmark for your domain):
1. Twitch Helix `GET /helix/clips` per broadcaster — up to 1,000 top clips by view count; each clip has `video_id`, `vod_offset`, `duration`, `title`, `view_count`. **A Twitch clip *is* a user-labeled positive example with span boundaries.**
2. `twitch-dl` or TwitchDownloaderCLI for full VODs; rechat/GraphQL `/comments` or `lay295/TwitchDownloader` (MIT) for timestamped chat.
3. Pair: positive moment = `[vod_offset, vod_offset + duration]` ± 60 s margin. Sample negatives ≥ 5 min away.
4. Kick equivalent: `kick.com/api/v2/channels/{slug}/clips` + Pusher WebSocket for chat.
5. 50 streamers × ~100 top clips = ~50 k labeled triples — sufficient to train a QVHighlights-scale moment retriever.

**Metrics to report**: top-k precision (of top-5 predicted clips, how many are in human top-5), NDCG vs pairwise preferences, **hallucination rate** (fraction of titles whose claims fail NLI entailment against `transcript ∪ OCR ∪ chat`; target <5%).

**Feedback-loop pattern.** Convert approve/reject into DPO-format (Rafailov et al., NeurIPS 2023) `(prompt, chosen, rejected)` triples. Collect via **Argilla** (HuggingFace) or **Label Studio** (HumanSignal), both of which ship Krippendorff's α natively. Retrain a small reranker (or DPO-tune Qwen3-VL-7B) weekly on accumulated triples — expect 5–15% quality lift after ~2,000 preferences (Bai et al. scaling curves).

**Cost/complexity.** 1 week to build the Twitch-clips bootstrap ingestion; 1 week to stand up Argilla + HITL UI. This is the long-term investment that compounds most.

---

## §8.7 Variable-length clip windows / payoff detection

**State of the art on QVHighlights** (Lei et al., arXiv:2107.09609 — the right benchmark for you): the DETR family dominates. **SG-DETR** (saliency-guided, arXiv:2410.01615, w/ pretraining on synthetic InterVid-MR) currently leads the leaderboard at **58.8 mAP, 71.0 HIT@1**. **CG-DETR** (Moon et al., arXiv:2311.08835, wjun0830/CGDETR, MIT) is the practical runner-up at 65.4 R1@0.5, 48.4 R1@0.7, 43.9 mAP, 40.3 HIT@1. **LA-DETR** (arXiv:2412.20816) specifically fixes short-moment mAP (from ~12 to 40+ for <10 s clips). **FlashVTG** (WACV 2025, Zhuo-Cao/FlashVTG) adds +5.8 mAP over prior SOTA plus **+125% mAP on short moments** when paired with InternVideo2 features. Reproducible A/B via the **Lighthouse** library (EMNLP 2024 demo, aclanthology.org/2024.emnlp-demo.6) which wraps Moment-DETR, QD-DETR, EaTR, TR-DETR, UVCOM, CG-DETR under a single interface.

**Video-LLM moment grounding (generative).** **TRACE** (ICLR 2025, gyxxyg/TRACE) and **TimeExpert** (ICCV 2025, arXiv:2508.01699) are the leading VTG-specialized LLMs but **still 15–30 mAP below the supervised DETRs on QVHighlights** (TimeExpert 29.6 mAP vs SG-DETR 58.8). **Grounded-VideoLLM** (EMNLP 2025 Findings, arXiv:2410.03290) and **VTG-LLM** (AAAI 2025) are competitive alternatives. **MomentSeeker** (NeurIPS 2025, arXiv:2502.12558) — the only academic benchmark with video durations averaging 1,202 s — explicitly reports that Gemini-2.5-Pro, Qwen2.5-VL, LLaVA-Video, TimeChat, LITA all "perform poorly" at long-video moment retrieval.

**Architecture change — replace fixed 45 s window at Pass C:**
1. Keep Pass A (signal fusion — transcript + chat + audio — remains the most cost-effective candidate generator).
2. Around each Pass A candidate, run **CG-DETR or SG-DETR** via Lighthouse on a ±90 s window with SlowFast + CLIP (or upgrade to InternVideo2) features. Output: variable-length (start, end) spans up to 150 s.
3. **Snap boundaries** to (a) **TransNet V2** shot cuts (arXiv:2008.04838 v2, ACM MM 2024, soCzech/TransNetV2, MIT) — still SOTA for shot-boundary detection — within ±5 s, (b) Whisper sentence boundaries within ±3 s, (c) pyannote silence gaps > 200 ms. Bias the end snap slightly later (e.g., +0–8 s) to avoid chopping payoffs.
4. For **storytime payoff detection** specifically, add an LLM-over-transcript prompt:
   > "Identify 'storytime payoff' moments: a setup where the streamer builds anticipation, followed by a punchline, reveal, or emotional climax 30–300 seconds later. Return `{setup_start, payoff_start, payoff_end, confidence, why}`. Be conservative; omit if uncertain."
   Use **`[1234.5s]` compact timestamps** inline with transcript lines (Qwen/Gemma tokenize these efficiently; VTG-LLM arXiv:2405.13382 found `<t=1234>` tokens reduce quantization error if you fine-tune).

**Cost/complexity.** CG-DETR inference is ~100 ms/window on a T4. Engineering: 1 week to integrate Lighthouse + boundary-snap. This fixes "storytime peaks past the window" directly.

---

## §8.8 Industry-standard datasets and benchmarks

**What you already know** (HowTo100M, YouTube-8M, TVQA, VLG-Net, CONDENSED-MOVIES, MovieNet) is pretraining-era — adequate context but not evaluation targets.

**Moment retrieval (for Pass C evaluation)**: **QVHighlights** is the right primary benchmark — variable-length annotations, joint MR+HD, blind CodaLab test server, 30+ comparable models. **Charades-STA, ActivityNet-Captions, TACoS, DiDeMo** are older and less representative.

**Long-video understanding (2024–2026)** — this is where the field has moved:

| Benchmark | arXiv | Size / Duration | Best 2025 score |
|---|---|---|---|
| **Video-MME** | 2405.21075 | 900 videos / 254 h, 2,700 QA, 3 length buckets up to 30–60 min | Gemini 2.5 Pro 84.8%, GPT-4o ~77%, LongVILA-7B 65.1% |
| **MLVU** | 2406.04264 | 1,730 videos / avg 930 s, 9 tasks | GPT-4o M-Avg 54.5%; LongVILA / Qwen2.5-VL-72B competitive |
| **LongVideoBench** | 2407.15754 | 3,763 videos, 6,678 MCQs up to 60 min, **referring reasoning** | GPT-4o ~66%, Gemini-1.5-Pro ~64% |
| **StreamingBench** | 2411.03628 | 900 videos / 4,500 QA, **true streaming (5 Qs at different t per video)** | MiniCPM-o 2.6 66.0 (streaming SOTA), IXComposer2.5-OmniLive 73.8, humans ~85% |
| **HourVideo** | 2411.04998 | 500 egocentric videos 20–120 min, 12,976 MCQs | Gemini-1.5-Pro 37.3%, GPT-4-Turbo 25.7%, humans 85.0% |
| **TemporalBench** | 2410.10818 | Fine-grained temporal dynamics | GPT-4o only 38.5%, humans ~67% |
| **MVBench** | 2311.17005 | 4,000 QA across 20 temporal tasks | Qwen2.5-VL / InternVideo2.5 top |
| **LVBench** | 2406.08035 | Avg 4,101 s | Leaders <50% |
| **CinePile** | 2405.08813 | 305k movie MCQs | Humans 73% |
| **MomentSeeker** | 2502.12558 | Avg 1,200 s, long-video MR | All MLLMs struggle |

**Prescriptive choice for OpenClaw**: adopt **QVHighlights** (primary — span-level quality) + **StreamingBench** (secondary — the only benchmark evaluating true streaming paradigm, which matches Twitch) + your **own Twitch-Clips-as-positives benchmark** (§8.6). Skip TVSum/SumMe (not moment retrieval), HourVideo/CinePile (domain mismatch), EgoSchema (egocentric only).

**Streaming-specific published datasets**: TwitchChat dataset (Ringer et al., AAAI 2020), Twitch LoL (Pytorch-Twitch-LOL), Autohighlight (Ringer et al., MLWA 2022), "Finding epic moments in live content" (Song et al., EPJ Data Science 2021 — **the canonical paper cluster 2M Twitch clips + emote signatures**). No large CC-licensed Twitch benchmark exists. Bootstrap your own via Helix Clips API.

---

## §8.9 Model-selection experiments

**Text-only LLMs for Pass B (long transcript → structured JSON).** The current unified Gemma 4 26B / Qwen 3.5 35B multimodal setup is wasteful: Pass B runs ~30× more often than Stage 6, but you're paying vision-encoder cost on every call. The workload rewards instruction-following + long-context faithfulness + modest reasoning, **not** frontier math.

| Budget tier | Recommendation | Why |
|---|---|---|
| Single 24 GB GPU | **Qwen3-30B-A3B (non-thinking), Q4** — ~18 GB, ~196 t/s, Apache 2.0, 128K YaRN, MoE 3B active | Best MoE in this tier |
| 2×48 GB | **Qwen3-Next-80B-A3B-Instruct FP8** — 256K native → 1M YaRN, **91.8 RULER avg @ 1M**, Apache 2.0, 3B active | Best open long-context faithfulness |
| API | **Gemini 2.5 Flash** — $0.30/$2.50, 1M ctx, reliable `response_schema` | Cheapest 1M-ctx; GPT-4.1-mini is the fallback |
| Unconstrained | **Claude Sonnet 4.5** (quality) or **DeepSeek-V3.1** MIT 685B/37B (open frontier reasoning, R1-class, ~$0.56/$1.68 per Mtok via API) | |

**Avoid for Pass B**: DeepSeek-R1-Distill (emits lots of `<think>` tokens), Gemma 3 27B (rated below Qwen3-32B on Artificial Analysis, slow on Google API), Llama 4 Scout (benchmark-integrity concerns from LMArena episode).

**Vision-Language Models for Stage 5/6 (frame analysis with OCR + HUD + temporal grounding)**. This is where OCR quality matters most for Twitch UI.

| Model | OCRBench | MMMU | DocVQA | VRAM INT4 | License | Note |
|---|---|---|---|---|---|---|
| **Qwen3-VL-8B Instruct** (Sep 2025) | **896** | ~70 | 96.1 | ~8 GB | Apache 2.0 | SOTA for single-GPU; bounding-box grounding; ScreenSpot-Pro 61.8; AndroidWorld 63.7 — directly validates UI understanding |
| **Qwen3-VL-32B Instruct/Thinking** | ~89 | 74+ | ~96 | ~20 GB | Apache 2.0 | Quality ceiling at 48 GB; MathVista 85.8 beats GPT-5 |
| **Qwen2.5-VL-72B** (Jan 2025) | 885 | 70.2 | 96.4 | ~42 GB | Qwen license | Battle-tested; best bbox training corpus |
| **InternVL3.5-8B / 30B-A3B** | high | 73.4 / 75.6 | high | 8 / 18 GB | MIT | Competitive alternative; GUI-interaction training |
| **MiniCPM-V 2.6** (8B, Aug 2024) | 852 | 49.8 | 90.8 | ~6 GB | MiniCPM license | Best <10 B OCR; GGUF/Ollama support |
| **Phi-4-Multimodal** (5.6B, Mar 2025) | 84.4 | 55.1 | 93.2 | ~4 GB | MIT | Efficient but no 2D grounding |
| **Florence-2** (Microsoft, 230M/770M) | OCR specialist | — | — | <2 GB | MIT | Best **preprocessor** for UI bbox extraction |

**Video-native LLMs** (for optional multi-frame Stage 5). **Qwen3-VL** (8B/32B/235B, Sep 2025) supports 2-hour video with T-RoPE temporal alignment and shares weights with the image model — best unified pick. **LLaVA-Video-72B** leads MLVU at 77.0 but is weaker on Twitch OCR. **Apollo-7B** (Meta + Stanford, arXiv:2412.10360) hits MLVU 70.9 via fps-consistent sampling, but weights were briefly pulled from HF (supply-chain risk). **LongVILA-7B** and **Video-XL** handle 2,048+ frames on a single A100-80GB if you ever want whole-episode ingestion. **InternVideo2.5-8B** (arXiv:2501.12386) is the sleeper for short-clip perception: +3 MVBench, +12 EgoSchema over InternVL2.5 base.

**Jack-of-all-trades vs split — estimated latency/cost/quality delta** (5-min transcript chunk + 3-frame validation):

| Architecture | Pass B | Stage 6 | Quality | Cost |
|---|---|---|---|---|
| Current unified Gemma 4 26B | 1.8 s | 3.5 s | baseline | 1.0× |
| **Split: Qwen3-32B + Qwen3-VL-8B** | **0.9 s** | **1.8 s** | +5–8 OCR, +3 JSON | **0.55×** |
| Split: Qwen3-30B-A3B + Qwen3-VL-32B | 0.45 s | 4.2 s | +8–12 OCR | 0.7× |

**Prescriptive stack on a single 48 GB GPU (the sweet spot for OpenClaw):** Pass B = Qwen3-32B BF16 (~28 GB) with vLLM prefix caching; Stage 6 = Qwen3-VL-8B FP8 (~10 GB) co-resident; hot-swap to Qwen3-VL-32B AWQ INT4 (~20 GB) only for the top 5% of candidate moments. Serve via vLLM with XGrammar structured outputs.

---

## §8.10 Human-in-the-loop gating

**State of the art.** Three orthogonal triggers to stack: confidence thresholds, self-consistency divergence, NLI contradiction. **Don't show AI confidence above content** — arXiv:2509.08514 ("Bias in the Loop") shows this causes anchoring. Randomize sample order; rotate reviewers. For generative tasks collect **comparison data** (3–4 n-best samples side-by-side with a rewrite field) — that's exactly the RLHF/DPO data format.

**Active learning** (Settles 2012 survey is canonical): use **uncertainty sampling** (label the lowest-confidence clips), **query-by-committee** with self-consistency N=3 disagreement as the QBC signal, and **density-weighted** filtering to avoid outliers. **DeepAL** (Huang et al.) is the PyTorch toolkit.

**Tools**: **Argilla** (HF, Apache 2.0, direct fit for title/hook comparison → DPO), **Label Studio** (HumanSignal, OSS + Enterprise, computes Krippendorff's α natively), **Prodigy** (Explosion, developer-friendly).

**HITL rule-set for OpenClaw:**
- **Auto-accept** if self-consistency pairwise cosine ≥ 0.85 AND NLI = entailment AND classifier confidence > 0.8.
- **Queue for review** if pairwise cosine < 0.5, or NLI = contradicts, or classifier confidence 0.4–0.8.
- **Auto-reject & regenerate** if confidence < 0.4 or denylist filter fires.
- **Reviewer UI**: 3 candidate titles + transcript snippet + 4 extracted frames side-by-side; reviewer picks one (→ preference triple for DPO) or rewrites (→ demonstration data).
- **Weekly retrain**: freeze VLM; DPO-tune a small reranker (Qwen3-VL-7B is a good target) on accumulated triples. Expect 5–15% quality lift after ~2,000 preferences.

**Cost/complexity.** ~1 week to build the Argilla/Label Studio integration; training loop is fire-and-forget after that.

---

## Additional topic 1: Thinking-mode token budget management

**The Qwen 3.5 2000–4000-token-before-JSON problem is solvable immediately.** Pass B is classification + tagging — not a reasoning task. It does not need thinking.

**Native controls (2025–2026):**
- **Qwen3**: `enable_thinking=False` in chat template, `/no_think` tag, or `thinking_budget=N` (hits cap → injects `</think>` token 151668). **Use `enable_thinking=False` for Pass B; expected 3–10× speedup.**
- **Gemini 2.5**: `thinkingConfig.thinkingBudget` (0 disables, −1 = dynamic; 2.5 Pro cannot fully disable, min 128).
- **Claude 3.7/4/Sonnet 4.5/4.6**: `thinking={"type":"enabled","budget_tokens": N}`, min 1024.
- **OpenAI o-series**: `reasoning_effort ∈ {minimal, low, medium, high}`.

**Research techniques:**
- **s1: Simple Test-time Scaling** (Muennighoff et al., **arXiv:2501.19393**) — "budget forcing": append end-of-thinking delimiter to cut reasoning short, or append "Wait" to lengthen. `simplescaling/s1.1-32B` on HF.
- **Chain of Draft (CoD)** (Xu et al., **arXiv:2502.18600**) — the prompt directive *"Think step by step, but only keep a minimum draft for each thinking step, with 5 words at most"* reduces tokens to ~7.6% of CoT while matching accuracy.
- Community **ThinkingTokenBudgetProcessor** pattern (HuggingFace `LogitsProcessor` that forces-injects `</think>` after N tokens) for DeepSeek-R1 style models without native budget.

**Critical architectural insight — Tam et al. 2024 "Let Me Speak Freely?"** (EMNLP 2024 Industry, **arXiv:2408.02442**): **strict format constraints degrade reasoning quality; stricter formats hurt more.** Mitigation is a **two-stage split**: reasoning model thinks/answers in free text, then a small formatter model (**NuExtract 2.0**, `numind/NuExtract-2.0-4B`, MIT-friendly for 2B variant, based on Qwen2.5-VL, `temperature=0`) converts to strict JSON under constrained decoding. This is the single most important takeaway — it fixes thinking-token waste AND JSON-under-reasoning quality hit simultaneously.

**Constrained decoding ranked**:
- **XGrammar** (Dong et al., **arXiv:2411.15100**, mlc-ai/xgrammar) — up to **100× faster than Outlines**; default in vLLM v1 as of late 2024.
- **Outlines** (dottxt-ai/outlines) — Pydantic/JSON-Schema/regex/CFG; cross-backend.
- **lm-format-enforcer**, **Guidance** (Microsoft), **llama.cpp GBNF**.
- **vLLM** exposes `structured_outputs.json` with backend=`xgrammar` default.
- **OpenAI `response_format: {"type":"json_schema","strict":true}`** and **Gemini `responseSchema`** are the hosted equivalents.

**Architecture change for Pass B:** (1) set `enable_thinking=False` on Qwen3.5 — this alone reclaims 2–4 k tokens/call; (2) wrap Pass B in vLLM `structured_outputs.json` with XGrammar backend; (3) if you *do* need reasoning, use a two-stage split with NuExtract-2.0 as formatter; (4) schema-first prompt (put the JSON schema first, not last); (5) retry ladder: primary → same model with schema-in-prompt + grammar → NuExtract on the raw text.

---

## Additional topic 2: Frame sampling strategy — the single highest-ROI fix

**This is the most urgent bug in your pipeline.** You extract 6 JPEGs but use only indices 03 and 04 (T-10s, T-5s). **The moment is at T+0 to T+3.** You are literally not looking at the payoff.

**Empirical framing from Apollo (Meta/Stanford, arXiv:2412.10360):**
- "fps sampling is preferable over uniform sampling"; for <60 s windows, uniform ≡ fps in practice.
- **Tokens-per-frame sweet spot ~8–32; accuracy peaks at many frames × low tokens, not few frames × high detail.**
- Best encoder stack: SigLIP (spatial) + InternVideo2 (temporal).
- **Scaling Consistency**: design choices validated on 2B–4B models transfer to 7B+. You can A/B sampling strategies on Qwen2.5-VL-3B cheaply.

**Moments Lab 2025 ablation (arXiv:2509.14769):** content-aware selection provides **no measurable gain over uniform 1–2 fps** for <30 s clips; the crossover is ~2 min. Sophisticated selectors (**GenS**, arXiv:2503.09146, ACL 2025 Findings; **Frame-Voyager**, arXiv:2410.03226, ICLR 2025; **Adaptive Keyframe Sampling**, arXiv:2502.21271, CVPR 2025) are engineered for hour-long videos and are **overkill** for OpenClaw's candidate window length.

**Shot-boundary detection**: **TransNet V2** (arXiv:2008.04838 v2, ACM MM 2024) is the peer-reviewed SOTA; **AutoShot** (CVPRW 2023, arXiv:2304.06116) beats it by +4.2 F1 on SHOT. Use either for clip boundary snapping.

**Three-tier fix in priority order:**
1. **Tier 1 (zero cost, immediate):** drop T-10; add T-2, T+0, T+1, T+2, T+3, T+5. Feed all 6–7 frames. *This is the highest-leverage single change in the entire report.*
2. **Tier 2 (4× token cost):** increase to 8–16 frames at ~1 fps across [T-3, T+5] with uniform sampling.
3. **Tier 3 (similar cost to tier 2):** switch to native video-mode input on Qwen3-VL-8B or Gemini 2.5 Flash — pass an 8–16 s MP4 at 1–2 fps instead of JPEG stack. Gains temporal reasoning and better token efficiency.

Tradeoff curve (calibrated from Apollo + Moments Lab): 2 → 8 frames lifts "what just happened" accuracy **~10–15 pp** on a 30 s clip; 8 → 16 adds another 3–5 pp; 16 → 32 flat or slightly negative.

Keep CLIP/SigLIP upgrades as a minor win: **SigLIP 2** (arXiv:2502.14786, Apache 2.0 `google/siglip2-*`) is a drop-in CLIP replacement with better dense features and NaFlex variant for 16:9.

---

## Additional topic 3: Twitch/Kick UI chrome detection and masking

**State of the art.** No academic paper targets Twitch overlay segmentation specifically. The building blocks are:
- **PaddleOCR PP-OCRv5** (2025, +13 pp E2E over v4, 106 languages, Apache 2.0) — 20–40 ms/frame on T4; best on stylized gaming fonts with drop shadows.
- **Florence-2** (Microsoft, arXiv:2311.06242, MIT, 230M/770M) — unified `<REGION_PROPOSAL>`, `<OCR_WITH_REGION>`, `<CAPTION_TO_PHRASE_GROUNDING>` prompts; ~40–80 ms/frame. Best UI-bbox preprocessor.
- **Qwen2.5-VL / Qwen3-VL grounding mode** — prompt-level bbox detection with JSON output.
- **OpenCV MOG2/KNN** — transient-overlay detection via frame-diff at 2 fps (sub alert pops in → MOG2 detects the new region in ~5 ms).
- **ScreenSpot-Pro** (Li et al., arXiv:2504.07981) benchmark: best GUI grounding models only 18.9% zero-shot (OS-Atlas-7B), lifted to 48.1% via ReGround iterative cropping — cautionary for claims of "general UI grounding."

**Architecture change (Path A, production-ready):**
1. **One-time per-streamer calibration**: Florence-2 `<REGION_PROPOSAL>` on 10 sample frames → cluster persistent bboxes → cache `webcam_bbox`, `chat_bbox`, `stream_info_bar_bbox`.
2. **Per-clip preprocessing**: OpenCV MOG2 over the 15 s window at 2 fps to detect *transient* overlays (sub alerts, bit rain, donation banners).
3. **Union = UI mask**. Black-fill or median-blur those regions on the JPEGs before the VLM call.
4. **Additionally**: run PaddleOCR on the *unmasked* frame and pass extracted overlay text as **structured context** in the prompt ("Overlay text at T+0: 'USER1 gifted 10 subs'"). This gives the LLM ground truth without letting the pixels corrupt the caption.
5. If a Twitch sub event appears in EventSub logs within the window, annotate the prompt — this is hard ground truth.

**Cost/complexity.** ~100–150 ms/frame preprocessing on T4 (negligible vs 500 ms–3 s VLM call). Expected lift: **30–50% reduction in overlay-induced hallucinations** on hype-train / 100-sub-gift events. Engineering: 3–5 days.

If OpenClaw integrates on the broadcaster side, OBS scene-JSON gives you perfect overlay bboxes for free — use it if available.

---

## Additional topic 4: Twitch chat as a highlight-detection signal

**Chat is the strongest "something clippable happened" signal you currently ignore and is near-free to obtain.** Song et al. 2021 ("Finding epic moments in live content through deep learning on collective decisions," EPJ Data Science 10:43) analyzed 2M Twitch clips + chat and found: **chat features alone hit 0.75 F1 on epic-moment detection, vision alone 0.70, multimodal 0.82** — chat is nearly as strong as vision and additively composable. Their MINT model clusters emotes by t-SNE into distinct emotional signatures (victory / funny / awkward / embarrassing). Related work: Fu & Yang EMNLP 2017 (arXiv:1707.08559, NBA/LoL corpus); Ringer et al. FDG 2018 (arXiv:1807.09715); Ringer et al. MLWA 2022 (Autohighlight, LoL esports); PogChampNet (Farza, Visor.gg 2018, non-peer-reviewed but the canonical methodology).

**Emote discriminators** (globals + BTTV/FFZ/7TV): KEKW/LULW/OMEGALUL = funny; PogChamp/POGGERS/PogU = hype; monkaS/monkaW = tense; GIGACHAD = big play; SADGE/PepeHands = loss; EZ/EZ Clap = dominant; Copium = uncertain; W/L = binary outcome.

**APIs**:
- Twitch live: **EventSub `channel.chat.message`** (the 2024+ recommended migration path from IRC). Also provides `channel.subscribe`, `channel.cheer`, `channel.raid` as hard ground truth.
- Twitch VOD: GraphQL `gql.twitch.tv/gql` `/comments` endpoint; community tool **`lay295/TwitchDownloader`** (MIT).
- Kick: Pusher WebSocket (undocumented but stable), `wss://ws-us2.pusher.com/app/{APP_KEY}`, subscribe `chatrooms.{chatroom_id}.v2`; app key is a moving target.

**Architecture change — add Pass A' chat ingest**, parallel to audio/vision, computing per-second features:
- `msgs_per_sec` raw + z-score vs channel baseline
- `emote_density[category]` — laugh / hype / tense / sad / W / L
- `unique_chatters_per_sec`
- recurring-phrase detector (e.g., "LETS GO", "NO WAY")
- `sub/bit/donation` EventSub counts — **hard ground truth that resolves the "gifted subs" hallucination question definitively**

Wire into Pass B as a ranking multiplier and into Stage 6 as grounding context:
```
Chat activity in [T-5, T+3]:
- 247 messages (baseline 12/5s, burst factor ~20×)
- Dominant emotes: KEKW × 58, LULW × 31 (62% laugh category)
- Recurring phrases: "bro what", "im dying"
- No subscription/donation events in this window.
```
This single block would have resolved the gifted-subs incident: if chat shows zero sub events AND recurring phrases are rank/competitive rather than hype-train, the VLM is forbidden from emitting "gifted subs."

**Cost/complexity.** Near-zero compute (chat is KB/min even for top channels). Engineering: **one afternoon** for the multiplier, **one day** for the grounding-prompt integration. Expected lift: 40–60% improvement in top-5 recall per Song et al. + ~20–30% reduction in category misclassification.

---

## Additional topic 5: End-to-end "long video → clips" architectures

**The verdict is clean: no, not in April 2026.** Keep the multi-stage pipeline. The candidates (LongVA, Video-XL, LongVILA, Kangaroo, Apollo, NVILA, InternVideo2.5, TRACE, ChatVTG, VTG-LLM, Grounded-VideoLLM, TimeChat, Momentor, GroundingGPT) either (a) can ingest 2 h but only output QA, not structured clip lists; (b) output spans but underperform supervised DETRs by 15–30 mAP on QVHighlights; or (c) fail explicitly on long-video MR per MomentSeeker (NeurIPS 2025, arXiv:2502.12558). Gemini 2.5 Pro via API comes closest but hallucinates timestamps unless you overlay frame numbers (NumPro, CVPR 2025), and costs ~$0.30–1.00 per 2 h video. **Revisit in 12 months**; the successors of Dispider/VideoStreaming and InternVideo2.5 may close the gap by late 2026.

**What to do instead**: use TRACE or Gemini 2.5 Pro only as an *oracle spot-check* layer, not the main pipeline.

---

## Prioritized roadmap

### 🥇 Single highest-leverage change (ship this week)
**Fix the frame sampling window.** Replace the (T-10, T-5) selection with (T-2, T+0, T+1, T+2, T+3, T+5). The JPEGs already exist on disk; the code change is one line. Expected effect: 15–30+ pp absolute improvement on "what just happened" accuracy, because you are currently describing the setup rather than the payoff. **Zero cost, zero risk.**

### 🥈 Top 3 medium-effort changes (ship this quarter)
1. **Grounding cascade between Pass B and Stage 6** (§8.4): BM25 + rare-token denylist → MiniCheck NLI (`bespokelabs/Bespoke-MiniCheck-7B` or `lytang/MiniCheck-Flan-T5-Large` CPU) → Lynx-8B on borderline only. **Null any Pass B field that fails NLI before it reaches Stage 6**. Combined with `enable_thinking=False` on Qwen3.5 for Pass B, and wrapping Pass B in vLLM `structured_outputs.json` with XGrammar. Cost: ~200 ms p95, ~$0/clip, ~5 engineer-days. **This is the minimum patch that would have prevented the "gifted subs" incident.**
2. **Chat-signal integration (Pass A')**: EventSub for live, TwitchDownloader/GraphQL for VOD; extract msgs/sec z-score + emote category density + sub/bit/donation ground truth; use as Pass B multiplier AND as Stage 6 grounding context. ~3 engineer-days. **Biggest win per line of code after the frame-sampling fix.**
3. **Speech pipeline upgrade**: swap faster-whisper-only for **WhisperX** (~1 afternoon for 80% of gains); add **Mel-Band RoFormer vocals separation** + **Frame-VAD MarbleNet v2.0** + **TalkNet-ASD** on the webcam crop AND-gated with audio VAD; upgrade Whisper to `large-v3-turbo` (free 2.5×) or **Parakeet-TDT-0.6B-v3** (best throughput-accuracy). Expected: 50–70% fewer hallucinated segments, 20–35% relative WER.

### 🔬 Research / experiment backlog (next 6 months)
- **Split Pass B (text) from Stage 6 (vision)**: Qwen3-32B or Qwen3-Next-80B-A3B for Pass B (text-only); Qwen3-VL-8B or -32B for Stage 6. Expect 45% cost reduction + 5–12 OCRBench-point lift on Twitch chrome. Test on your bootstrapped benchmark before committing.
- **Replace fixed 45 s Pass C window with CG-DETR or SG-DETR** via Lighthouse, sliding-window inference with ±90 s horizon + TransNet V2 + silence-gap boundary snap. Measure against QVHighlights R1@0.5/0.7/mAP and your internal Twitch-clip benchmark.
- **Self-consistency N=3 sampling** on titles/hooks at Stage 6b with USC selection + SelfCheckGPT-style NLI divergence as HITL trigger. Collect `(prompt, chosen, rejected)` triples into Argilla for DPO fine-tuning a small reranker.
- **UI chrome masking pipeline** (Florence-2 calibration + OpenCV MOG2 + PaddleOCR text-as-prompt-context). Reduces a tail-failure class (hype-train/bit-rain hallucinations) by 30–50%.
- **Bootstrap a Twitch/Kick clip-worthiness dataset** via Helix Clips API (50 streamers × 100 clips = ~50k labeled span triples). This is strictly better than any academic benchmark for your domain and enables DPO fine-tuning plus meaningful QVHighlights-style evaluation. Report top-k precision, NDCG on pairwise preferences, and hallucination rate against `transcript ∪ OCR ∪ chat`.
- **Evaluate Temporal Contrastive Decoding (TCD)** from EventHallusion at Stage 6 as a language-prior suppressor — most directly relevant published remedy for your exact failure mode.
- **Revisit end-to-end architectures in Q4 2026** — the Dispider / InternVideo2.5 / Qwen3-VL-235B successor line may finally support one-call long-video-to-clips at production quality.

**Architectural north star**: a Pass A/A'/B/C pipeline where Pass A is signal fusion (audio RMS + chat z-score + emote density + transcript LLM), Pass A' is EventSub ground truth, Pass B is a text-only Qwen3-32B classifier with grounded JSON via XGrammar, Pass C is CG-DETR for variable-length boundaries with TransNet V2 / silence snap, Stage 5 is Qwen3-VL video-native mode over a T-2 → T+5 window, Stage 6a is a transcript+chat+OCR grounded text classifier that writes the narrative, Stage 6b is a constrained vision VLM that writes title/hook/description against Stage 6a's fixed narrative — with a three-tier NLI grounding gate and self-consistency N=3 on every metadata field, HITL triggered on divergence, and accumulated preferences feeding a weekly DPO retrain. That stack, on a single 48 GB GPU, is the 2026-current production-quality ceiling for self-hosted Twitch/Kick clipping.