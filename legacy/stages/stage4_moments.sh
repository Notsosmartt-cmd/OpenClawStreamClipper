#!/usr/bin/env bash
# Stage 4 — Moment Detection (Pass A keywords + Pass B LLM + Pass C select); 4.5 groups
#
# Sourced by scripts/clip-pipeline.sh as part of Phase B. Inherits globals
# (TEMP_DIR, VOD_PATH, CLIP_STYLE, etc.) and the cleanup EXIT trap from the
# orchestrator. Extracted byte-for-byte — only the file boundary changed.

# ============================================================
# STAGE 4 — Moment Detection (Three-Pass Hybrid, Segment-Aware)
# ============================================================
set_stage "Stage 4/8 — Moment Detection"
log "=== Stage 4/8 — Moment Detection (style: $CLIP_STYLE) ==="

# Phase 5.1: if Pass B's model differs from Stage 3's (TEXT_MODEL → TEXT_MODEL_PASSB),
# swap now so Pass A/B/C use the right model. No-op in the unified config.
if [ "$TEXT_MODEL_PASSB" != "$TEXT_MODEL" ]; then
    log "Phase 5.1: swapping from Stage-3 text model ($TEXT_MODEL) to Pass-B model ($TEXT_MODEL_PASSB)"
    unload_model "$TEXT_MODEL"
    load_model "$TEXT_MODEL_PASSB"
fi

# Stage 4 Pass A/B/C extracted to scripts/lib/stages/stage4_moments.py (Phase A1).
LLM_URL="$LLM_URL" TEXT_MODEL="$TEXT_MODEL" TEXT_MODEL_PASSB="$TEXT_MODEL_PASSB" CLIP_STYLE="$CLIP_STYLE" python3 /root/scripts/lib/stages/stage4_moments.py

MOMENT_COUNT=$(python3 -c "import json; m=json.load(open('/tmp/clipper/hype_moments.json')); print(len(m))")
log "Found $MOMENT_COUNT clip-worthy moments"

if [ "$MOMENT_COUNT" -eq 0 ]; then
    warn "No clip-worthy moments detected. No clips to make."
    echo "$VOD_BASENAME	$(date -u +%Y-%m-%dT%H:%M:%SZ)	no_moments	$CLIP_STYLE" >> "$PROCESSED_LOG"
    echo '{"status":"no_moments","clips":0,"style":"'"$CLIP_STYLE"'"}'
    exit 0
fi

# ============================================================
# Pass D — structured rubric judge (Tier-4 Phase 4.4)
# ============================================================
# Per-moment rubric scoring on the same multimodal model used by Pass B.
# Failure-soft: per-moment errors keep the Pass C score; 3 consecutive
# network errors abort the whole pass and surviving moments are unchanged.
# Tier-4 Phase 4.6 MMR diversity rank runs at the tail of this same script.
log "Applying Tier-4 Pass D rubric judge..."
LLM_URL="$LLM_URL" TEXT_MODEL="$TEXT_MODEL" TEXT_MODEL_PASSB="$TEXT_MODEL_PASSB" python3 /root/scripts/lib/stages/stage4_rubric.py "$TEMP_DIR/hype_moments.json" || warn "Pass D rubric judge exited non-zero; continuing with Pass C scores"

# Tier-4 Phase 4.6 — MMR diversity ranker over Pass B `why` embeddings.
# Demotes near-duplicate moments. Reuses M3 callback module's loaded
# sentence-transformer. Failure-soft — falls through unchanged when the
# embedding model isn't available.
log "Applying Tier-4 Phase 4.6 MMR diversity rank (style=$CLIP_STYLE)..."
CLIP_STYLE="$CLIP_STYLE" python3 /root/scripts/lib/stages/stage4_diversity.py "$TEMP_DIR/hype_moments.json" || warn "MMR diversity rank exited non-zero; continuing with Pass D ordering"

# ============================================================
# Phase 4.2 — boundary snap on selected moments
# ============================================================
# Snap each moment's (clip_start, clip_end) to nearby Whisper word
# boundaries + silence gaps so Stage 7 doesn't render clips that start
# mid-word or end mid-reaction. Graceful no-op when boundaries.json is
# disabled or the transcript lacks word-level timestamps.
log "Applying Phase 4.2 boundary snap..."
python3 /root/scripts/lib/stages/stage4_5_snap.py "$TEMP_DIR/transcript.json" "$TEMP_DIR/hype_moments.json"

# ============================================================
# STAGE 4.5 — Moment Groups (narrative arcs and stitch bundles)
# Only runs when stitching or narrative merging is enabled. When both are
# disabled every moment stays solo (no behavior change from v3 baseline).
# ============================================================
if [ "$CLIP_STITCH" = "true" ] || [ "$CLIP_NARRATIVE" = "true" ]; then
    set_stage "Stage 4.5/8 — Moment Groups"
    log "=== Stage 4.5/8 — Moment Groups (stitch=$CLIP_STITCH narrative=$CLIP_NARRATIVE) ==="
    GROUP_SUMMARY=$(python3 "$LIB_DIR/moment_groups.py" \
        --stitch "$CLIP_STITCH" \
        --narrative "$CLIP_NARRATIVE" \
        --moments "$TEMP_DIR/hype_moments.json" \
        --out "$TEMP_DIR/moment_groups.json" 2>&1 | tail -1)
    log "  Groups: $GROUP_SUMMARY"
fi
