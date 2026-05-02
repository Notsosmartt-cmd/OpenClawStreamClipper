#!/bin/bash
# ============================================================
# Stream Clipper Pipeline — orchestrator
# Sources scripts/lib/pipeline_common.sh + scripts/stages/stageN.sh.
# Per-stage logic, helper functions, and embedded Python modules
# all live outside this file. See:
#   AIclippingPipelineVault/wiki/concepts/modularization-plan.md
# ============================================================
set -euo pipefail

VODS_DIR="${CLIP_VODS_DIR:-/root/VODs}"
CLIPS_DIR="${CLIP_CLIPS_DIR:-/root/VODs/Clips_Ready}"
TEMP_DIR="/tmp/clipper"
PROCESSED_LOG="${VODS_DIR}/processed.log"
WHISPER_CACHE="/root/.cache/whisper-models"
LLM_URL="${CLIP_LLM_URL:-http://host.docker.internal:1234}"
TEXT_MODEL="${CLIP_TEXT_MODEL:-qwen/qwen3.5-9b}"
VISION_MODEL="${CLIP_VISION_MODEL:-qwen/qwen3.5-9b}"
# Phase 5.1 split: stage-specific model overrides with fallback to the
# unified model. Stage 3 segment classify always uses TEXT_MODEL. Pass B
# uses TEXT_MODEL_PASSB (recommended: a text-only, non-thinking model).
# Stage 6 vision uses VISION_MODEL_STAGE6 (recommended: a vision-specialist
# smaller than the unified LLM). Unset → fall back to TEXT_MODEL/VISION_MODEL.
TEXT_MODEL_PASSB="${CLIP_TEXT_MODEL_PASSB:-$TEXT_MODEL}"
VISION_MODEL_STAGE6="${CLIP_VISION_MODEL_STAGE6:-$VISION_MODEL}"
WHISPER_MODEL="${CLIP_WHISPER_MODEL:-large-v3}"
# Context length passed to LM Studio when loading models.
# Tune based on VRAM: 4096 (~2 GB KV), 8192 (~4 GB), 16384 (~8 GB), 32768 (~16 GB).
CONTEXT_LENGTH="${CLIP_CONTEXT_LENGTH:-8192}"
# Caption rendering: set CLIP_CAPTIONS=false to skip subtitle burn-in
CAPTIONS_ENABLED="${CLIP_CAPTIONS:-true}"
# Hook caption: AI-generated punchy title at the top of the video (toggle via CLIP_HOOK_CAPTION=false)
HOOK_CAPTION_ENABLED="${CLIP_HOOK_CAPTION:-true}"
# Speed-up: CLIP_SPEED=1.25 speeds video and audio together.
# Pitch tracks speed proportionally (rubberband=tempo=N:pitch=N) so the voice
# naturally sounds like someone talking faster — no chipmunk and no separate control.
CLIP_SPEED="${CLIP_SPEED:-1.0}"

# --- Originality controls (TikTok 2025 unoriginal-content defense) ---
# ORIGINALITY: per-clip randomization of blur, filters, mirror, hook/subtitle styles
# FRAMING: blur_fill | camera_pan  (smart_crop and centered_square removed)
# STITCH: enable multi-segment stitch groups (3+ sub-clips under 30s each)
# NARRATIVE: enable long-form narrative groups (60-90s storytime/emotional)
# TTS_VO: enable Piper voiceover layer on clips that carry a voiceover_line
# MUSIC_BED: path to a folder of background-music tracks (empty = disabled)
# MUSIC_TIER_C: when true, run librosa feature analysis for advanced matching
# CAMERA_PAN: when framing is camera_pan, enable face-tracking crop paths
CLIP_ORIGINALITY="${CLIP_ORIGINALITY:-true}"
# CLIP_FRAMING accepts only two modes now:
#   blur_fill  — legacy 9:16 blur-fill (safe default)
#   camera_pan — OpenCV face-tracked virtual camera (requires CLIP_CAMERA_PAN=true)
CLIP_FRAMING="${CLIP_FRAMING:-blur_fill}"
CLIP_STITCH="${CLIP_STITCH:-false}"
CLIP_NARRATIVE="${CLIP_NARRATIVE:-true}"
CLIP_TTS_VO="${CLIP_TTS_VO:-false}"
CLIP_MUSIC_BED="${CLIP_MUSIC_BED:-}"
CLIP_MUSIC_TIER_C="${CLIP_MUSIC_TIER_C:-false}"
CLIP_CAMERA_PAN="${CLIP_CAMERA_PAN:-false}"
LIB_DIR="/root/scripts/lib"
SCRIPTS_DIR="/root/scripts"
STAGES_DIR="$SCRIPTS_DIR/stages"

# Parse arguments
CLIP_STYLE="auto"
TARGET_VOD=""
LIST_MODE=false
FORCE_REPROCESS=false
STREAM_TYPE_HINT=""
while [[ $# -gt 0 ]]; do
    case $1 in
        --style) CLIP_STYLE="$2"; shift 2;;
        --vod) TARGET_VOD="$2"; shift 2;;
        --list) LIST_MODE=true; shift;;
        --force) FORCE_REPROCESS=true; shift;;
        --type) STREAM_TYPE_HINT="$2"; shift 2;;
        *) shift;;
    esac
done

# --- Always write a persistent log file ---
# PIPELINE_LOG: ephemeral per-run log in /tmp — cleared by the EXIT trap.
# PERSISTENT_LOG: timestamped file in Clips_Ready/.pipeline_logs/ that
# survives the cleanup trap and is always available for post-run review.
PIPELINE_LOG="$TEMP_DIR/pipeline.log"
mkdir -p "$TEMP_DIR"

# Wallclock start — used by cleanup() to report elapsed time at exit.
PIPELINE_START_EPOCH=$(date +%s)

LOG_TIMESTAMP=$(date -u +%Y%m%d_%H%M%S)
LOG_VOD_SLUG=$(basename "${TARGET_VOD:-unknown}" | sed 's/\.[^.]*$//' | tr ' ' '_' | tr -cd '[:alnum:]_-' | cut -c1-40)
PERSISTENT_LOG_DIR="${CLIPS_DIR}/.pipeline_logs"
mkdir -p "$PERSISTENT_LOG_DIR"
PERSISTENT_LOG="${PERSISTENT_LOG_DIR}/${LOG_TIMESTAMP}_${LOG_VOD_SLUG}.log"

exec > >(tee -a "$PIPELINE_LOG" "$PERSISTENT_LOG") 2>&1
echo "=== Pipeline started at $(date -Iseconds) | style=$CLIP_STYLE vod=$TARGET_VOD type=$STREAM_TYPE_HINT speed=$CLIP_SPEED ==="
echo "=== Persistent log: $PERSISTENT_LOG ==="

# --- Detached-lifecycle markers (BUG 31) ---
# Written so the dashboard (running on the Windows host) can observe pipeline
# liveness even if its `docker exec` session dies — e.g. when Docker
# Desktop's named-pipe returns 500 mid-run. The dashboard reads:
#   - PIPELINE_PID_FILE (pid of THIS bash + persistent log path) on startup
#   - PIPELINE_DONE_FILE (exit code) on completion (written by EXIT trap)
# As long as PIPELINE_DONE_FILE is absent and the pid is alive, the dashboard
# treats the pipeline as still running and keeps streaming the in-container log.
PIPELINE_PID_FILE="$TEMP_DIR/pipeline.pid"
PIPELINE_DONE_FILE="$TEMP_DIR/pipeline.done"
rm -f "$PIPELINE_DONE_FILE"
{
    echo "pid=$$"
    echo "started=$(date -Iseconds)"
    echo "persistent_log=$PERSISTENT_LOG"
} > "$PIPELINE_PID_FILE"

# Source shared helpers (log/warn/err, set_stage, model load/unload, cleanup).
# This also installs `trap cleanup EXIT` so subsequent stage failures still
# emit pipeline.done with the correct exit_code for the dashboard.
# shellcheck source=lib/pipeline_common.sh
source "$LIB_DIR/pipeline_common.sh"

log "Clip style: $CLIP_STYLE"
[[ -n "$STREAM_TYPE_HINT" ]] && log "Stream type hint: $STREAM_TYPE_HINT"
log "Text model: $TEXT_MODEL | Vision model: $VISION_MODEL | Whisper: $WHISPER_MODEL"
if [ "$TEXT_MODEL_PASSB" != "$TEXT_MODEL" ] || [ "$VISION_MODEL_STAGE6" != "$VISION_MODEL" ]; then
    log "Phase 5.1 split active: Pass B=$TEXT_MODEL_PASSB | Stage 6=$VISION_MODEL_STAGE6"
fi
log "Originality: orig=${CLIP_ORIGINALITY} framing=${CLIP_FRAMING} stitch=${CLIP_STITCH} narrative=${CLIP_NARRATIVE} pan=${CLIP_CAMERA_PAN} tts=${CLIP_TTS_VO} music=$( [ -n "$CLIP_MUSIC_BED" ] && echo "$CLIP_MUSIC_BED tier_c=${CLIP_MUSIC_TIER_C}" || echo off )"

# Fail-fast: confirm all configured LM Studio models are actually downloaded
# (BUG 52). Without this, a typo or missing model produces HTTP 400 on every
# Stage 3 / Pass B / Stage 6 call and the pipeline limps through fallbacks for
# hours before noticing.
verify_models

# --- Stage dispatch ---
# Each stage file is sourced (not executed) so it inherits all globals,
# the cleanup trap, and `set -euo pipefail`. Stages are byte-identical to
# the pre-Phase-B inline blocks; only the file boundary changed.
# shellcheck source=stages/stage1_discovery.sh
source "$STAGES_DIR/stage1_discovery.sh"
# shellcheck source=stages/stage2_transcription.sh
source "$STAGES_DIR/stage2_transcription.sh"
# shellcheck source=stages/stage3_segments.sh
source "$STAGES_DIR/stage3_segments.sh"
# shellcheck source=stages/stage4_moments.sh
source "$STAGES_DIR/stage4_moments.sh"
# shellcheck source=stages/stage5_frames.sh
source "$STAGES_DIR/stage5_frames.sh"
# shellcheck source=stages/stage6_vision.sh
source "$STAGES_DIR/stage6_vision.sh"
# shellcheck source=stages/stage7_render.sh
source "$STAGES_DIR/stage7_render.sh"
# shellcheck source=stages/stage8_logging.sh
source "$STAGES_DIR/stage8_logging.sh"
