#!/usr/bin/env bash
# Stage 5 — Frame Extraction (6 JPEGs per candidate moment)
#
# Sourced by scripts/clip-pipeline.sh as part of Phase B. Inherits globals
# (TEMP_DIR, VOD_PATH, CLIP_STYLE, etc.) and the cleanup EXIT trap from the
# orchestrator. Extracted byte-for-byte — only the file boundary changed.

# ============================================================
# STAGE 5 — Frame Extraction
# ============================================================
set_stage "Stage 5/8 — Frame Extraction"
log "=== Stage 5/8 — Frame Extraction ==="

TIMESTAMPS=$(python3 -c "import json; [print(m['timestamp']) for m in json.load(open('/tmp/clipper/hype_moments.json'))]")
FRAME_COUNT=0

# Targeted frame offsets around the moment peak T.
# Per ClippingResearch.md Additional Topic 2, the payoff is at T+0..T+3.
# Previous code extracted at fps=1/5 starting from T-15 and only fed indices
# 03/04 (≈ T-5 / T+0) to the VLM — so the model was describing the setup,
# not the payoff. We now extract at 6 specific offsets and feed all of them
# to Stage 6 in a single time-ordered call.
# Format: "label:offset_seconds". Labels become part of the filename.
FRAME_OFFSETS=("tminus2:-2" "t0:0" "tplus1:1" "tplus2:2" "tplus3:3" "tplus5:5")

while IFS= read -r T; do
    [ -z "$T" ] && continue

    log "Extracting payoff-window frames for moment at T=${T}s (T-2, T+0, T+1, T+2, T+3, T+5)..."
    for pair in "${FRAME_OFFSETS[@]}"; do
        label="${pair%%:*}"
        offset="${pair##*:}"
        FRAME_T=$((T + offset))
        [ "$FRAME_T" -lt 0 ] && FRAME_T=0
        ffmpeg -nostdin -y -ss "$FRAME_T" -i "$VOD_PATH" \
            -frames:v 1 \
            -vf "scale=960:540" \
            -q:v 2 \
            "$TEMP_DIR/frames_${T}_${label}.jpg" 2>/dev/null \
            || warn "Frame extraction failed for T=$T offset=${offset}s"
    done

    FRAME_COUNT=$((FRAME_COUNT + 1))
done <<< "$TIMESTAMPS"

log "Extracted payoff-window frames for $FRAME_COUNT moments"

# ============================================================
# Tier-3 A2 — Setup-frame extraction for callback / arc moments
# ============================================================
# Moments tagged callback (Tier-2 M3) or arc (Tier-3 A1) carry a setup_time
# (the earlier transcript point that established the joke / claim being
# referenced now). For those, extract 2 additional frames at setup_time-1
# and setup_time+1 so Stage 6 can visually verify that the same person /
# scene that set up the moment is still on screen at the payoff. Falls
# through silently for moments without setup_time.
log "Tier-3 A2: extracting setup frames for callback/arc moments..."
SETUP_FRAME_COUNT=0
while IFS=$'\t' read -r T SETUP_T; do
    [ -z "$T" ] && continue
    [ -z "$SETUP_T" ] && continue
    [ "$SETUP_T" = "None" ] && continue
    for offset_label in "setupminus1:-1" "setupplus1:1"; do
        label="${offset_label%%:*}"
        off="${offset_label##*:}"
        FRAME_T=$((SETUP_T + off))
        [ "$FRAME_T" -lt 0 ] && FRAME_T=0
        ffmpeg -nostdin -y -ss "$FRAME_T" -i "$VOD_PATH" \
            -frames:v 1 \
            -vf "scale=960:540" \
            -q:v 2 \
            "$TEMP_DIR/frames_${T}_${label}.jpg" 2>/dev/null \
            || warn "A2 setup-frame extraction failed for T=$T setup=${SETUP_T}s"
    done
    SETUP_FRAME_COUNT=$((SETUP_FRAME_COUNT + 1))
done < <(python3 -c "
import json
for m in json.load(open('/tmp/clipper/hype_moments.json')):
    setup = m.get('setup_time')
    if setup is None:
        continue
    print(f\"{m['timestamp']}\t{int(setup)}\")
")
log "A2 extracted setup frames for $SETUP_FRAME_COUNT callback/arc moments"

# Phase 4.1 (chrome masking + PaddleOCR) removed 2026-05-01 after BUG 49
# (PaddleOCR wedge truncating the pipeline) and BUG 50 (MOG2 frame-spacing
# mismatch left the detector dead-code). Stage 5 frames flow directly to
# Stage 6 unmodified.
