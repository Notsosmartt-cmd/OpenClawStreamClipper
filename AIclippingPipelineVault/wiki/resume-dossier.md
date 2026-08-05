---
title: "Resume Dossier — OpenClaw Stream Clipper"
type: overview
tags: [resume, handoff, project-summary, hub]
sources: 0
updated: 2026-08-04
---

# Resume Dossier — OpenClaw Stream Clipper

> [!note] Purpose of this page
> This page is **raw material, not resume copy**. It exists to be handed to another
> agent (or read directly) for the specific task of *evaluating and drafting resume
> content* about this project. Nothing below is phrased as a resume bullet on
> purpose — no "Spearheaded," no invented percentages, no compressed claims. The
> receiving agent's job is to select, verify against this page, and phrase; this
> page's job is to be complete and accurate. Every number here is pulled directly
> from the git history, the codebase, or this project's own wiki — none are
> estimated for effect. Where something is a judgment call rather than a fact
> (e.g., how to characterize AI-assisted development), it's flagged explicitly
> rather than resolved on the receiving agent's or resume-writer's behalf.

---

## One-paragraph description

OpenClaw Stream Clipper is a self-hosted system that automatically finds and extracts short-form highlight clips (TikTok/Reels/Shorts format) from long-form livestream recordings. It runs an 8-stage AI pipeline — transcription, semantic segmentation, moment detection, a multi-pass quality-judgment system, multimodal vision analysis, and video rendering — entirely on local hardware, orchestrating multiple locally-hosted LLMs (text and vision) with zero cloud API dependency for the core pipeline. It's controlled through a Discord bot (natural-language commands) or a web dashboard, and includes a second, independent application for batch-publishing finished clips to social platforms via the Buffer API. The system is solo-built, has been in continuous active development for roughly 4.5 months (first commit 2026-03-27, still active), and was substantially re-architected mid-project (a full migration off Docker to a native bare-metal design) as scaling and reliability requirements became clear.

---

## Verified scale metrics

Pulled directly from `git log`, `wc -l`, and file counts — not estimates.

| Metric | Value |
|---|---|
| Total commits | 348 |
| Active development span | 2026-03-27 → present (~4.5 months, ongoing) |
| Python source (tracked files) | ~50,800 lines |
| JavaScript + HTML (tracked files) | ~24,600 lines |
| Backend Python modules (`scripts/lib/`) | 64 |
| Pipeline stage modules | 8 top-level stages (some with sub-stages: 4.5, 5.5, 6.5) |
| Dashboard backend route blueprints | 11 |
| Dashboard frontend JS modules | 9 |
| Config files (behavior-driving JSON) | 32 |
| Documented, root-caused production bugs | 77 (see [[concepts/bugs-and-fixes]]) |
| Project wiki pages (self-maintained documentation) | 134 (92 concept pages, 33 entity pages, 25 dated engineering-plan pages) |
| Distinct engineering "plan" documents (research → design → ship) | 25 |

---

## Technical stack

- **Languages**: Python (backend/pipeline/ML orchestration), JavaScript (frontend, vanilla ES modules — no framework), Bash (legacy/retired path), PowerShell (Windows process orchestration)
- **Web**: Flask (two independent apps: main dashboard + a separate clip-posting app), Server-Sent Events for live log streaming, vanilla-JS single-page frontends
- **AI/ML runtime**: [LM Studio](https://lmstudio.ai) as a local OpenAI-compatible inference server; local LLMs used for text classification/extraction and multimodal vision analysis; local speech-to-text via faster-whisper / WhisperX (word-level timestamps + speaker diarization)
- **Media processing**: FFmpeg (custom filter-graph construction for 9:16 vertical rendering, dynamic cropping, caption burn-in, audio mixing), OpenCV (motion analysis for automatic camera framing), NumPy/librosa-class audio DSP (tonality/music-bed detection)
- **Automation**: Discord bot built on the OpenClaw agent framework (Node.js) — natural-language command parsing that invokes the Python pipeline via a defined tool-call/skill contract
- **External APIs**: Buffer (social media scheduling/publishing GraphQL API), Cloudinary (media hosting leg required because Buffer has no direct upload endpoint)
- **Infra evolution**: originally Docker (single container + Docker Compose); migrated to a native bare-metal Windows architecture mid-project to eliminate an entire class of environment friction (see [Architecture](#architecture--major-engineering-decisions) below)

---

## Architecture & major engineering decisions

### Multi-model, multi-GPU local AI orchestration
The system explicitly manages VRAM as a scarce resource across a dual-vendor GPU setup (NVIDIA + AMD). It pools both GPUs (~28 GB combined) via LM Studio's Vulkan backend to run larger models than either card holds alone, while routing GPU-bound tasks (Whisper transcription) to CUDA specifically. Models are loaded and unloaded programmatically between pipeline stages to stay within budget, with automated pre-flight checks (e.g., detecting when a "thinking"-mode model would exhaust its token budget before ever running a batch) and fail-fast guards rather than silent degradation.

### Bare-metal migration (a deliberate infrastructure rewrite, not a patch)
The project began as a single Docker container. As the system grew, Docker became the dominant source of environmental complexity: a `docker exec` execution bridge, Docker-internal hostname rewriting to reach the host-native LLM server, and WSL2 filesystem-boundary overhead. Rather than patching around these, the entire orchestration layer (~1,800 lines of bash) was rewritten as a native Python orchestrator with one module per pipeline stage, and the Docker path was retired to a documented `legacy/` fallback. This is a concrete example of recognizing that an initial infrastructure choice had become the limiting factor and executing a full replacement rather than accumulating workarounds.

### A quality-judgment pipeline stage, not just a detection pipeline
Most of a project like this could stop at "detect candidate moments, render them." This system inserts an explicit quality-judgment stage between detection and rendering: every candidate moment is re-evaluated by a separate model pass against its verbatim transcript evidence (not the detector's own summary of itself, to avoid a model grading its own homework) before any expensive frame extraction or rendering work happens on it. Weak candidates are killed early with a logged rationale. This stage is designed fail-open by construction (a judge outage or a candidate the judge never scored must never silently disappear) — see [[concepts/bugs-and-fixes#BUG 76]] below for a case where that invariant was violated by a subtle bug and how it was caught.

### A reference-corpus comparison and calibration loop
A distinct subsystem ("Reference Lab") decomposes a corpus of externally-sourced, high-performing short-form clips into structured attribute cards (cut cadence, caption style, framing, sound-effect density, hook mechanics, arc shape, etc.), decomposes the system's own output the same way, and produces a quantified gap report — each gap item mapped to a specific, existing configuration lever the system could pull to close it. This turned "make the clips better" from a subjective, endless task into a measurable, falsifiable one. It's also the origin of several of the bug list's entries: building the *comparison methodology itself* correctly (avoiding apples-to-oranges metrics, avoiding a model hallucinating conclusions from real numbers, avoiding blended statistics that hide subgroup effects) was its own significant engineering problem.

### Feature-flag discipline
Essentially every behavioral change in the render pipeline ships behind an environment-variable flag defaulting to the pre-existing behavior, with an explicit verification step (a real pipeline run, output inspected before the flag is ever flipped to default-on). This is standard practice at mature engineering organizations and was applied consistently on a solo project — new capabilities are shipped dark and promoted only after evidence.

### Checkpointing and resumability
Long VOD-processing runs (a single stream recording can take well over an hour to fully process) snapshot progress after each major pipeline stage, so a crash or manual stop resumes from the last completed stage per-video rather than restarting a multi-hour batch from zero.

---

## AI/ML engineering depth (likely the strongest material for an ML/AI-adjacent role)

- **Prompt engineering across a multi-stage LLM pipeline**: distinct, purpose-built prompts for segment classification, moment extraction, quality judgment, and multimodal enrichment — each iterated against measured output quality, not just "wrote a prompt and shipped it."
- **A pairwise vision-tournament re-ranker**: rather than having a vision model simply score clips independently (which measured as weak and inconsistent), the system runs pairwise comparisons between candidate clips and lets the vision model actually select winners — a from-scratch design choice, not an off-the-shelf technique.
- **Rigorous, controlled model-choice experiments**: when evaluating whether to swap a component model (e.g., comparing two candidate "finder" models for the moment-detection stage), the methodology used was a controlled head-to-head: identical input data, identical downstream judge, only the model under test varied, with the *exclusive* (non-overlapping) findings of each model scored separately from the overlapping ones — because two models scoring similarly overall while one's unique contributions are weaker is the actual signal that determines which one should keep the job. This exact methodology also caught and reversed an initial wrong verdict when a downstream bug (a judge silently scoring some outputs as zero) had made one model look artificially better.
- **Statistical discipline in applying findings**: when incorporating "typical" values learned from the reference corpus into the live system's prompts, a hard minimum-sample-size gate (n≥8) was enforced before any subgroup's numbers were trusted enough to influence behavior, with sub-threshold groups explicitly flagged as "insufficient sample" rather than silently used.
- **Systematic hallucination/attribution checking**: an LLM-generated narrative summary of a data report was caught fabricating a plausible-sounding but incorrect causal claim (garbling a correct data point into an incorrect recommendation) — the fix was to explicitly tier findings by evidence strength (deterministic measurement vs. eyeballed observation vs. model judgment) and flag the model-judgment tier as the weakest, rather than presenting all findings with equal confidence.
- **Multimodal grounding discipline**: vision-model judgments are explicitly instructed to re-derive conclusions from the actual frames/transcript provided, not to trust a summary generated by an earlier stage — a deliberate anti-anchoring design to prevent error compounding across a multi-stage pipeline.

---

## Software engineering practices demonstrated

- **Large-scale modularization**: three separate ~1,800–4,000-line monolithic files (a bash pipeline script, a Flask app, a frontend JS bundle) were each decomposed into thin orchestrators plus focused single-responsibility modules in a deliberate refactor pass, each verified against the pre-refactor behavior before being called done.
- **Self-authored test discipline**: numerous modules carry their own executable self-test blocks (asserted behavior, run via `python module.py`) rather than relying solely on manual spot-checks, including for video-filter-graph generation logic that's otherwise easy to silently regress.
- **Root-cause bug tracking as a durable artifact**: 77 production bugs are individually documented with symptom, root cause, fix, and — notably — the general lesson extracted from each one (e.g., "a `dict.get(key, 0)` default on a semantically meaningful field is a silent-corruption generator" from a case where a missing quality score was defaulted to zero instead of being left unscored, which nearly caused a quality filter to silently discard clips it should have kept).
- **Observability as a first-class concern**: structured per-run diagnostic snapshots, per-stage timing breakdowns, and a dedicated log-query tool were built specifically to support a "change → measure → compare" engineering loop rather than debugging via ad hoc print statements.
- **Config-file-driven behavior over hardcoded constants**: 32 JSON configuration files externalize tunable behavior (sound-effect placement rules, caption voice/style, per-content-type editing templates, selection weighting) so behavior changes don't require code changes or redeploys.
- **API integration with real-world operational constraints**: the social-publishing integration includes rate-limit-aware request pacing and burst protection engineered specifically after hitting a real third-party platform's anti-spam velocity lockout in production — i.e., a genuine operational incident that was diagnosed and then engineered around, not just wrapped in a generic retry loop.

---

## Full-stack / systems breadth

| Layer | What's there |
|---|---|
| **Backend / orchestration** | Python process orchestrator managing an 8-stage pipeline, subprocess lifecycle, model load/unload sequencing |
| **Web backend** | Two independent Flask applications (REST + Server-Sent Events streaming) |
| **Web frontend** | Vanilla JS single-page apps, no framework — module-per-panel structure |
| **Video/audio engineering** | Hand-constructed FFmpeg filter graphs (dynamic cropping, blur-fill compositing, caption rendering, multi-layer audio mixing), OpenCV-based motion analysis, custom audio DSP for music/tonality detection |
| **AI/ML** | Multi-model local LLM orchestration (text + vision), prompt engineering, a custom pairwise re-ranking system, a reference-corpus-driven calibration loop |
| **Bot/automation framework** | Discord bot on an agentic framework, translating natural language into structured pipeline invocations |
| **External integrations** | Third-party publishing (Buffer) and media-hosting (Cloudinary) APIs, including rate-limit engineering |
| **DevOps/infra** | A full infra migration (containerized → bare-metal native), process supervision scripts, Windows-specific systems programming (junction points, port-conflict auto-resolution) |
| **Documentation/knowledge management** | A self-maintained, 134-page structured knowledge base (this wiki) tracking architecture, decisions, and a full bug/incident history — used as the actual source of truth for ongoing development, not written after the fact |

---

## Notable technical challenges (situation → action → result framing, for STAR-style resume bullets)

These are the strongest individual stories in the project history — each is a real, verifiable incident.

**1. A silent data-corruption bug in the quality-judgment system.**
*Situation*: a controlled experiment comparing two candidate AI models for the moment-detection stage produced a result that looked decisive — one model appeared dramatically better than the other.
*Action*: rather than accepting a surprising result at face value, the underlying judge output was audited row-by-row, revealing that several outputs marked "keep this clip" with a positive written rationale simultaneously carried a numeric score of exactly zero — an internally contradictory record. Root-caused to a Python `dict.get(key, 0)` default silently substituting zero whenever the model's response omitted a score field, rather than leaving the item unscored.
*Result*: fixed the defaulting behavior to fail open (leave unscored) instead of fail with a fabricated value, re-ran the experiment, and got the correct (much closer) result. Also identified that the same bug, left unfixed, would have caused an unrelated production quality filter to silently discard clips the judge had actually approved — a bug that could have been shipped and never noticed, since its symptom (fewer clips than expected) looks identical to normal quality variance.

**2. Diagnosing a dormant, statistically-impossible feature.**
*Situation*: several visual editing effects (freeze-frames, cutaways, slow-motion) were configured with substantial probability weights in the system's per-category templates, and had recently been "tuned" based on comparison against reference content.
*Action*: when asked to investigate a related rendering question, ground-truth logs were checked across the full render history — 433 renders — revealing zero instances of any of these effects ever having actually fired, despite the configured probabilities implying dozens should have.
*Result*: traced to a code path where the effect-synthesis logic only ever populated two of six possible effect types, meaning the "tuning" work done on the unused knobs had been entirely inert the whole time. Documented as a bug with the broader lesson (a config value existing is not evidence a code path consults it — verify against execution logs, not configuration).

**3. A production infrastructure migration under real usage.**
*Situation*: a Docker-containerized architecture had accumulated enough operational friction (hostname-rewriting bugs, exec-bridge fragility, WSL2 overhead) that it was actively slowing feature development.
*Action*: designed and executed a full port to a native bare-metal architecture — rewriting the orchestration layer, redesigning the model-serving connection, and re-validating every pipeline stage — while keeping the old path available as a documented, working fallback rather than a one-way door.
*Result*: eliminated an entire category of recurring bugs and became the sole actively-developed path going forward, with the legacy path preserved for reference/rollback rather than deleted.

**4. Building a rate-limit incident into engineered protection.**
*Situation*: a batch social-media-publishing run triggered an undocumented anti-spam velocity lockout on a third-party platform (distinct from, and stricter than, the platform's documented rate limits) after a burst of rapid API calls.
*Action*: diagnosed the platform's actual behavior from its own error messages, then engineered a burst-aware request-pacing system (not a naive fixed-delay retry) that respects both the documented quota and the empirically-discovered velocity threshold, with automatic fallback to scheduled (delayed) posting once a burst threshold is hit.
*Result*: the system now self-limits before triggering the same lockout again, and the incident is documented for future reference rather than being a one-off manual recovery.

---

## Context the resume-evaluation agent should have (not resolved here — a judgment call)

- **This is a personal/self-directed project**, not employer-owned work — built to solve the project owner's own content-creation workflow (clipping their own or watched livestreams for social media), self-hosted on personal hardware, using free/open-source tooling. It should be framed accordingly (a personal engineering project demonstrating initiative and depth), not implied to be commercial/team work.
- **Development method**: this project was built through extensive, sustained collaboration between the project owner and an AI coding agent (Claude, via Claude Code) across many sessions over the ~4.5-month history — the owner directs requirements, architecture and product decisions, reviews and tests every output (including by ear/eye on rendered video — several of the 77 documented bugs are explicitly owner-caught or owner-spotted), sets priorities, and makes the final call on tradeoffs (e.g., explicitly overriding a data-driven recommendation to preserve a stylistic choice they liked); a large share of the code-writing, debugging legwork, and research was done by the AI agent under that direction. How to represent this honestly on a resume is a real question without one obvious answer — "designed and directed development of," "architected," and similar framings are defensible given the depth of technical decision-making documented above (the owner is consistently making the calls on architecture, model selection, methodology, and quality bars throughout this project's history), but the resume-evaluation agent should not imply 100%-by-hand authorship of 50,000+ lines of code without qualification, and should probably ask the project owner directly how they want this represented rather than assuming.
- **No production users / no uptime-under-load claims**: this serves the owner's own use, not a customer base — don't manufacture "served N users" or "handled N requests/day" framing that implies external production traffic.
- **Verifiable specifics exist**: nearly every claim above traces to a real file, commit, or wiki entry in this repository, so specific, checkable claims ("built a pipeline with an explicit quality-judgment stage," "diagnosed and fixed a silent data-corruption bug via a controlled experiment") are safer and more defensible in an interview than vague superlatives.

---

## Where to look for more depth

- [[overview]] — full architecture writeup
- [[hot]] — current state snapshot and most recent engineering activity
- [[concepts/bugs-and-fixes]] — the full 77-bug history with root causes
- [[index]] — catalog of all 134 wiki pages, organized by topic (pipeline stages, models, render features, research/engineering-plan pages)
- `git log` in the repository itself — 348 commits of granular history
