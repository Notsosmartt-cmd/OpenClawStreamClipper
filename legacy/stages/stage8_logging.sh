#!/usr/bin/env bash
# Stage 8 — Logging & Summary
#
# Sourced by scripts/clip-pipeline.sh as part of Phase B. Inherits globals
# (TEMP_DIR, VOD_PATH, CLIP_STYLE, etc.) and the cleanup EXIT trap from the
# orchestrator. Extracted byte-for-byte — only the file boundary changed.

# STAGE 8 — Logging and Summary
# ============================================================
set_stage "Stage 8/8 — Summary"
log "=== Stage 8/8 — Summary ==="

TOTAL_CLIPS=0
if [ -f "$TEMP_DIR/clips_made.txt" ]; then
    TOTAL_CLIPS=$(wc -l < "$TEMP_DIR/clips_made.txt")
fi

echo -e "$VOD_BASENAME\t$(date -u +%Y-%m-%dT%H:%M:%SZ)\t${TOTAL_CLIPS}_clips\t${CLIP_STYLE}" >> "$PROCESSED_LOG"

# Stage 8 summary extracted to scripts/lib/stages/stage8_summary.py (Phase A5).
python3 /root/scripts/lib/stages/stage8_summary.py

log "Pipeline complete! ${TOTAL_CLIPS} clip(s) saved to $CLIPS_DIR (style: $CLIP_STYLE)"
