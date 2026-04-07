# Log

Append-only chronological record of wiki operations. Newest entries at top.

Format: `## [YYYY-MM-DD] operation | Title`
Grep recent: `grep "^## \[" wiki/log.md | head -10`

---

## [2026-04-07] update | Full wiki rebuild — external summaries integrated and removed

Ingested `DEVELOPMENT_SUMMARY.txt` and `fix.txt`. Corrected all inaccuracies from initial bootstrap (7→8 stages, missing models, wrong rendering technique, wrong Whisper hardware). External summary files deleted.

Pages rewritten: [[overview]], [[entities/faster-whisper]], [[entities/qwen3-vl]], [[entities/qwen35]], [[entities/ollama]], [[entities/openclaw]], [[entities/ffmpeg]], [[entities/discord-bot]], [[concepts/clipping-pipeline]], [[concepts/highlight-detection]], [[concepts/vram-budget]], [[concepts/deployment]].

Pages created: [[entities/qwen25]], [[entities/dashboard]], [[concepts/segment-detection]], [[concepts/vision-enrichment]], [[concepts/clip-rendering]], [[concepts/context-management]], [[concepts/bugs-and-fixes]], [[concepts/open-questions]], [[sources/development-summary]], [[sources/fix-txt]].

Root `CLAUDE.md` created with vault-update prompt injection for agents working on the project.

## [2026-04-07] ingest | OpenClaw Stream Clipper — Detailed System Summary

Processed `OpenClaw_Stream_Clipper_Summary.md` (project root). Initial wiki bootstrap.

Pages created: [[overview]], [[sources/openclaw-stream-clipper-summary]], [[entities/openclaw]], [[entities/ollama]], [[entities/qwen3-vl]], [[entities/qwen35]], [[entities/faster-whisper]], [[entities/ffmpeg]], [[entities/discord-bot]], [[concepts/clipping-pipeline]], [[concepts/highlight-detection]], [[concepts/vram-budget]], [[concepts/deployment]].
