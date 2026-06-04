#!/usr/bin/env bash
# Stage 2 — Audio transcription (faster-whisper) + audio events scan
#
# Sourced by scripts/clip-pipeline.sh as part of Phase B. Inherits globals
# (TEMP_DIR, VOD_PATH, CLIP_STYLE, etc.) and the cleanup EXIT trap from the
# orchestrator. Extracted byte-for-byte — only the file boundary changed.

# ============================================================
# STAGE 2 — Audio Transcription (with caching)
# ============================================================
set_stage "Stage 2/8 — Audio Transcription"
log "=== Stage 2/8 — Audio Transcription ==="

# Free VRAM: unload any LM Studio models before Whisper needs the GPU.
# Phase 5.1: unload the stage-specific models too (split config may have
# any of these loaded at this point depending on what ran before Stage 2).
unload_model "$TEXT_MODEL"
unload_model "$VISION_MODEL"
if [ "$TEXT_MODEL_PASSB" != "$TEXT_MODEL" ]; then unload_model "$TEXT_MODEL_PASSB"; fi
if [ "$VISION_MODEL_STAGE6" != "$VISION_MODEL" ]; then unload_model "$VISION_MODEL_STAGE6"; fi

AUDIO_FILE="$TEMP_DIR/audio.wav"
TRANSCRIPT_CACHE_DIR="${VODS_DIR}/.transcriptions"
mkdir -p "$TRANSCRIPT_CACHE_DIR"

# Cache key: VOD filename without extension
VOD_CACHE_KEY=$(echo "$VOD_BASENAME" | sed 's/\.[^.]*$//')
CACHED_JSON="$TRANSCRIPT_CACHE_DIR/${VOD_CACHE_KEY}.transcript.json"
CACHED_SRT="$TRANSCRIPT_CACHE_DIR/${VOD_CACHE_KEY}.transcript.srt"

if [ -f "$CACHED_JSON" ] && [ -f "$CACHED_SRT" ]; then
    log "Found cached transcription for '$VOD_BASENAME'. Skipping transcription."
    cp "$CACHED_JSON" "$TEMP_DIR/transcript.json"
    cp "$CACHED_SRT" "$TEMP_DIR/transcript.srt"

    # Print stats from cached transcript
    python3 -c "
import json
with open('/tmp/clipper/transcript.json') as f:
    data = json.load(f)
if data:
    duration_min = data[-1]['end'] / 60
    word_count = sum(len(s['text'].split()) for s in data)
    print(json.dumps({'duration_min': round(duration_min, 1), 'segments': len(data), 'words': word_count, 'cached': True}))
"
else
    log "No cached transcription found. Transcribing via Phase 3 speech module..."
    # Phase 3: speech.py selects its backend (WhisperX preferred, faster-whisper
    # fallback), reads config/speech.json, and applies per-channel
    # streamer_prompts.json biasing keyed off the VOD basename. Env var
    # CLIP_WHISPER_MODEL (set from the dashboard) still overrides the file.
    export CLIP_WHISPER_MODEL="$WHISPER_MODEL"
    log "Extracting audio track..."
    ffmpeg -y -i "$VOD_PATH" -vn -acodec pcm_s16le -ar 16000 -ac 1 "$AUDIO_FILE" 2>/dev/null

    AUDIO_DURATION=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$AUDIO_FILE" 2>/dev/null | cut -d. -f1)
    AUDIO_DURATION=${AUDIO_DURATION:-0}
    log "Audio duration: ${AUDIO_DURATION}s"

    # Single call — all chunking / VAD / alignment / initial_prompt logic
    # lives in scripts/lib/speech.py. Summary JSON is printed last on stdout.
    SPEECH_SUMMARY=$(python3 "$LIB_DIR/speech.py" \
        --audio    "$AUDIO_FILE" \
        --out-json "$TEMP_DIR/transcript.json" \
        --out-srt  "$TEMP_DIR/transcript.srt" \
        --vod      "$VOD_BASENAME" \
        2> >(tee -a "$PIPELINE_LOG" >&2) \
    ) || {
        err "speech.py transcription failed"
        echo '{"status":"transcription_failed","clips":0}'
        exit 1
    }
    echo "$SPEECH_SUMMARY"

    # Cache the transcription for future re-clips
    cp "$TEMP_DIR/transcript.json" "$CACHED_JSON"
    cp "$TEMP_DIR/transcript.srt" "$CACHED_SRT"
    log "Transcription cached to $TRANSCRIPT_CACHE_DIR/"
fi

log "Transcription complete. Output: $TEMP_DIR/transcript.json"

# ============================================================
# Tier-2 M2 — Audio events scan (rhythmic / crowd / music)
# ============================================================
# Boost-only signals fed into Pass A keyword_scan. Runs on the same audio
# file Whisper used. Module loads the audio ONCE then slices in-memory
# per window (~250 ms / window, ~5 min on a 3-hour VOD). When librosa
# isn't available the script writes an empty events file and Pass A no-ops.
#
# `-u` disables Python output buffering and `2> >(tee ...)` uses process
# substitution (matches the speech.py invocation pattern) so per-100-window
# progress logs reach the operator's terminal + persistent log in real time
# instead of being held until the whole scan finishes.
AUDIO_EVENTS_JSON="$TEMP_DIR/audio_events.json"
if [ -f "$AUDIO_FILE" ]; then
    log "Tier-2 M2: scanning audio events (rhythmic / crowd / music)..."
    python3 -u "$LIB_DIR/audio_events.py" \
        --audio "$AUDIO_FILE" \
        --out   "$AUDIO_EVENTS_JSON" \
        2> >(tee -a "$PIPELINE_LOG" >&2) || {
        log "audio_events scan failed — continuing without audio-event signals"
        echo '{"windows": [], "skipped_reason": "scanner_error"}' > "$AUDIO_EVENTS_JSON"
    }
else
    # Cached transcription path: source WAV is gone. Skip M2 silently.
    echo '{"windows": [], "skipped_reason": "no_audio_source"}' > "$AUDIO_EVENTS_JSON"
fi

