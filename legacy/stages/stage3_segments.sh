#!/usr/bin/env bash
# Stage 3 — Segment Detection (5-type stream window classification)
#
# Sourced by scripts/clip-pipeline.sh as part of Phase B. Inherits globals
# (TEMP_DIR, VOD_PATH, CLIP_STYLE, etc.) and the cleanup EXIT trap from the
# orchestrator. Extracted byte-for-byte — only the file boundary changed.

# ============================================================
# STAGE 3 — Segment Detection (NEW)
# ============================================================
set_stage "Stage 3/8 — Segment Detection"
log "=== Stage 3/8 — Segment Detection ==="

# Ensure the text model is loaded with the right context before first LLM call.
# After Stage 2 (Whisper), all models are unloaded, so we load fresh here.
load_model "$TEXT_MODEL"

# Stage 3 segments extracted to scripts/lib/stages/stage3_segments.py (Phase A3).
LLM_URL="$LLM_URL" TEXT_MODEL="$TEXT_MODEL" STREAM_TYPE_HINT="$STREAM_TYPE_HINT" python3 /root/scripts/lib/stages/stage3_segments.py

log "Segment detection complete"

