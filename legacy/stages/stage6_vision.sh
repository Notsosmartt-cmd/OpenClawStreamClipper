#!/usr/bin/env bash
# Stage 6 — Vision Enrichment (non-gatekeeping); 6.5 camera-pan prep
#
# Sourced by scripts/clip-pipeline.sh as part of Phase B. Inherits globals
# (TEMP_DIR, VOD_PATH, CLIP_STYLE, etc.) and the cleanup EXIT trap from the
# orchestrator. Extracted byte-for-byte — only the file boundary changed.


# Bump STAGE_FILE before the Pass B → Stage 6 VRAM swap. The unload+load
# round can take 20-40 s on a 26B-class model; without an early stage-file
# bump the dashboard's BUG-31 staleness gate trips during a Docker Desktop
# hiccup and prematurely emits "Pipeline finished".
set_stage "Stage 6/8 — Vision Enrichment (loading model)"

# Free VRAM before vision stage.
# Phase 5.1: compare the stage-specific models (Pass B text model and
# Stage 6 vision model). When they're the same (default / unified config),
# skip the unload/reload cycle. When they differ (split config), unload
# the Pass B text model and load the Stage 6 vision model.
if [ "$TEXT_MODEL_PASSB" != "$VISION_MODEL_STAGE6" ]; then
    unload_model "$TEXT_MODEL_PASSB"
    load_model "$VISION_MODEL_STAGE6"
else
    log "Pass B text and Stage 6 vision models are the same ('$TEXT_MODEL_PASSB') — skipping VRAM swap"
fi

# ============================================================
# STAGE 6 — Vision Enrichment (NOT a gatekeeper)
# Vision scoring adds titles/descriptions and can BOOST moments
# but NEVER eliminates moments that transcript detection found.
# ============================================================
set_stage "Stage 6/8 — Vision Enrichment"
log "=== Stage 6/8 — Vision Enrichment ==="

# Stage 6 vision extracted to scripts/lib/stages/stage6_vision.py (Phase A2).
LLM_URL="$LLM_URL" VISION_MODEL_STAGE6="$VISION_MODEL_STAGE6" python3 /root/scripts/lib/stages/stage6_vision.py

SCORED_COUNT=$(python3 -c "import json; m=json.load(open('/tmp/clipper/scored_moments.json')); print(len(m))")
log "Moments to render: $SCORED_COUNT (all detected moments proceed to rendering)"

# ============================================================
# STAGE 6.5 — Camera Pan Prep (optional, wave E)
# For each moment, sample frames, detect faces, and produce a per-clip
# camera-path JSON. Skipped entirely when CLIP_CAMERA_PAN=false or when
# the framing mode isn't camera_pan. Falls back silently per clip if no
# faces are detected — the Stage 7 render then reverts to blur_fill.
# ============================================================
if [ "$CLIP_CAMERA_PAN" = "true" ] && [ "$CLIP_FRAMING" = "camera_pan" ]; then
    set_stage "Stage 6.5/8 — Camera Pan Prep"
    log "=== Stage 6.5/8 — Camera Pan Prep (face tracking) ==="
    VOD_PATH="$VOD_PATH" LIB_DIR="$LIB_DIR" python3 /root/scripts/lib/stages/stage6_5_campan.py
fi

if [ "$SCORED_COUNT" -eq 0 ]; then
    warn "No moments to render (detection found nothing)."
    echo "$VOD_BASENAME	$(date -u +%Y-%m-%dT%H:%M:%SZ)	no_moments	$CLIP_STYLE" >> "$PROCESSED_LOG"
    echo '{"status":"no_moments","clips":0,"style":"'"$CLIP_STYLE"'"}'
    exit 0
fi

# ============================================================
# STAGE 7 — Editing and Export
# ============================================================
