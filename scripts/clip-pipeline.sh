#!/bin/bash
# ============================================================
# Stream Clipper Pipeline v3 — Segment-Aware Hybrid Detection
# Four-pass system: transcription → segment detection →
# keyword + LLM analysis → diversity-aware selection.
# Supports --style flag. Scales clips with VOD duration.
# ============================================================
set -euo pipefail

VODS_DIR="${CLIP_VODS_DIR:-/root/VODs}"
CLIPS_DIR="${CLIP_CLIPS_DIR:-/root/VODs/Clips_Ready}"
TEMP_DIR="/tmp/clipper"
PROCESSED_LOG="${VODS_DIR}/processed.log"
WHISPER_CACHE="/root/.cache/whisper-models"
OLLAMA_URL="http://ollama:11434"
TEXT_MODEL="${CLIP_TEXT_MODEL:-qwen3.5:9b}"
VISION_MODEL="${CLIP_VISION_MODEL:-qwen3-vl:8b}"
WHISPER_MODEL="${CLIP_WHISPER_MODEL:-large-v3}"

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
# This ensures logs are available regardless of whether the pipeline was
# started manually or by the bot via exec. Tee duplicates all output to
# both stdout (for OpenClaw/terminal) and the log file.
PIPELINE_LOG="$TEMP_DIR/pipeline.log"
mkdir -p "$TEMP_DIR"
exec > >(tee -a "$PIPELINE_LOG") 2>&1
echo "=== Pipeline started at $(date -Iseconds) | style=$CLIP_STYLE vod=$TARGET_VOD type=$STREAM_TYPE_HINT ===" >> "$PIPELINE_LOG"

# Colors for log output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log() { echo -e "${GREEN}[PIPELINE]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
err() { echo -e "${RED}[ERROR]${NC} $1"; }
info() { echo -e "${CYAN}[INFO]${NC} $1"; }

# Stage tracking — writes current stage to a file for quick status checks
STAGE_FILE="$TEMP_DIR/pipeline_stage.txt"
set_stage() {
    echo "$1" > "$STAGE_FILE"
    echo "$(date -Iseconds) $1" >> "$TEMP_DIR/pipeline_stages.log"
    log ">>> $1"
}

# Unload an Ollama model from VRAM so Whisper (or another model) can use it.
# Uses keep_alive=0 which tells Ollama to immediately release VRAM.
unload_ollama() {
    local model="$1"
    log "Unloading $model from VRAM..."
    curl -sf "$OLLAMA_URL/api/generate" \
        -d "{\"model\": \"$model\", \"keep_alive\": 0}" > /dev/null 2>&1 || true
    sleep 1  # brief pause for VRAM release
}

cleanup() {
    # Save diagnostic data before cleanup
    DIAG_DIR="${CLIPS_DIR}/.diagnostics"
    mkdir -p "$DIAG_DIR"
    DIAG_FILE="$DIAG_DIR/last_run_$(date -u +%Y%m%d_%H%M%S).json"
    python3 -c "
import json, os, glob
diag = {}
for f in glob.glob('/tmp/clipper/*.json'):
    name = os.path.basename(f).replace('.json','')
    try:
        with open(f) as fh:
            data = json.load(fh)
            if isinstance(data, list):
                diag[name] = {'count': len(data), 'data': data[:30]}
            else:
                diag[name] = data
    except: pass
# Add clips_made.txt if exists
if os.path.exists('/tmp/clipper/clips_made.txt'):
    with open('/tmp/clipper/clips_made.txt') as fh:
        diag['clips_made'] = fh.read().strip().split('\n')
with open('$DIAG_FILE', 'w') as f:
    json.dump(diag, f, indent=2)
print(f'Diagnostics saved to $DIAG_FILE')
" 2>/dev/null || true
    log "Cleaning up temp files..."
    rm -rf "${TEMP_DIR:?}/"*
}
trap cleanup EXIT

log "Clip style: $CLIP_STYLE"
[[ -n "$STREAM_TYPE_HINT" ]] && log "Stream type hint: $STREAM_TYPE_HINT"
log "Text model: $TEXT_MODEL | Vision model: $VISION_MODEL | Whisper: $WHISPER_MODEL"

# ============================================================
# STAGE 1 — Discovery
# ============================================================
set_stage "Stage 1/8 — Discovery"
log "=== Stage 1/8 — Discovery ==="

mkdir -p "$CLIPS_DIR" "$TEMP_DIR"
touch "$PROCESSED_LOG"

# Find all video files
mapfile -t ALL_VODS < <(find "$VODS_DIR" -maxdepth 1 \( -name "*.mp4" -o -name "*.mkv" \) -type f 2>/dev/null | sort)

if [ ${#ALL_VODS[@]} -eq 0 ]; then
    log "No VOD files found in $VODS_DIR. Nothing to process."
    echo '{"status":"no_vods","clips":0}'
    exit 0
fi

# --list mode: show all available VODs and their processing status, then exit
if [ "$LIST_MODE" = true ]; then
    echo '{"status":"list","vods":['
    FIRST=true
    for vod in "${ALL_VODS[@]}"; do
        basename_vod=$(basename "$vod")
        size_bytes=$(stat -c%s "$vod" 2>/dev/null || echo 0)
        size_mb=$((size_bytes / 1048576))
        duration=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$vod" 2>/dev/null | cut -d. -f1)
        duration=${duration:-0}
        duration_min=$((duration / 60))
        processed="false"
        if grep -qF "$basename_vod" "$PROCESSED_LOG" 2>/dev/null; then
            processed="true"
        fi
        cached="false"
        cache_key=$(echo "$basename_vod" | sed 's/\.[^.]*$//')
        if [ -f "${VODS_DIR}/.transcriptions/${cache_key}.transcript.json" ]; then
            cached="true"
        fi
        [ "$FIRST" = true ] && FIRST=false || echo ','
        echo "  {\"name\":\"$basename_vod\",\"size_mb\":$size_mb,\"duration_min\":$duration_min,\"processed\":$processed,\"transcription_cached\":$cached}"
    done
    echo ']}'
    exit 0
fi

# If --vod was specified, find the matching VOD (case-insensitive partial match)
# NOTE: --vod ALWAYS processes the VOD, even if already processed (enables re-clipping)
if [ -n "$TARGET_VOD" ]; then
    MATCHED_VOD=""
    for vod in "${ALL_VODS[@]}"; do
        basename_vod=$(basename "$vod")
        # Case-insensitive partial match
        if echo "$basename_vod" | grep -qi "$TARGET_VOD"; then
            MATCHED_VOD="$vod"
            break
        fi
    done
    if [ -z "$MATCHED_VOD" ]; then
        err "No VOD matching '$TARGET_VOD' found in $VODS_DIR."
        # List available VODs to help the user
        echo -n "{\"status\":\"vod_not_found\",\"clips\":0,\"searched\":\"$TARGET_VOD\",\"available\":["
        FIRST=true
        for vod in "${ALL_VODS[@]}"; do
            [ "$FIRST" = true ] && FIRST=false || echo -n ","
            echo -n "\"$(basename "$vod")\""
        done
        echo "]}"
        exit 0
    fi
    VOD_PATH="$MATCHED_VOD"
    VOD_BASENAME=$(basename "$VOD_PATH")
    # Check if this is a re-process
    if grep -qF "$VOD_BASENAME" "$PROCESSED_LOG" 2>/dev/null; then
        log "Re-processing VOD: $VOD_BASENAME (previously processed, --vod override)"
    else
        log "Targeted VOD: $VOD_BASENAME (--vod match for '$TARGET_VOD')"
    fi
else
    # No --vod specified
    if [ "$FORCE_REPROCESS" = true ]; then
        # --force: re-process the most recently modified VOD regardless of processed.log
        VOD_PATH="${ALL_VODS[-1]}"
        VOD_BASENAME=$(basename "$VOD_PATH")
        log "Force re-processing latest VOD: $VOD_BASENAME"
    else
        # Filter out already-processed VODs
        NEW_VODS=()
        for vod in "${ALL_VODS[@]}"; do
            basename_vod=$(basename "$vod")
            if ! grep -qF "$basename_vod" "$PROCESSED_LOG" 2>/dev/null; then
                NEW_VODS+=("$vod")
            fi
        done

        if [ ${#NEW_VODS[@]} -eq 0 ]; then
            log "All ${#ALL_VODS[@]} VOD(s) already processed. Nothing new."
            # List the VODs so the agent can tell the user what's available for re-clipping
            echo -n '{"status":"all_processed","clips":0,"available":['
            FIRST=true
            for vod in "${ALL_VODS[@]}"; do
                [ "$FIRST" = true ] && FIRST=false || echo -n ","
                echo -n "\"$(basename "$vod")\""
            done
            echo ']}'
            exit 0
        fi

        # Process the first unprocessed VOD
        VOD_PATH="${NEW_VODS[0]}"
        VOD_BASENAME=$(basename "$VOD_PATH")
    fi
fi
log "Processing: $VOD_BASENAME"

# Verify file is fully transferred
SIZE1=$(stat -c%s "$VOD_PATH")
sleep 5
SIZE2=$(stat -c%s "$VOD_PATH")
if [ "$SIZE1" != "$SIZE2" ]; then
    err "File still being written ($SIZE1 != $SIZE2). Aborting."
    echo '{"status":"file_incomplete","clips":0}'
    exit 1
fi

VOD_DURATION=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$VOD_PATH" 2>/dev/null | cut -d. -f1)
log "VOD duration: $((VOD_DURATION / 60)) minutes ($VOD_DURATION seconds)"

# ============================================================
# STAGE 2 — Audio Transcription (with caching)
# ============================================================
set_stage "Stage 2/8 — Audio Transcription"
log "=== Stage 2/8 — Audio Transcription ==="

# Free VRAM: unload any Ollama models before Whisper needs the GPU
unload_ollama "$TEXT_MODEL"
unload_ollama "$VISION_MODEL"

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
    log "No cached transcription found. Transcribing with faster-whisper ($WHISPER_MODEL)..."
    export CLIP_WHISPER_MODEL="$WHISPER_MODEL"
    log "Extracting audio track..."
    ffmpeg -y -i "$VOD_PATH" -vn -acodec pcm_s16le -ar 16000 -ac 1 "$AUDIO_FILE" 2>/dev/null

    # Get audio duration for chunking
    AUDIO_DURATION=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$AUDIO_FILE" 2>/dev/null | cut -d. -f1)
    AUDIO_DURATION=${AUDIO_DURATION:-0}
    log "Audio duration: ${AUDIO_DURATION}s"

    # Split into 20-minute chunks to prevent Whisper degenerate loop on long files
    CHUNK_SECONDS=1200
    mkdir -p "$TEMP_DIR/audio_chunks"
    CHUNK_IDX=0
    OFFSET=0
    while [ "$OFFSET" -lt "$AUDIO_DURATION" ]; do
        ffmpeg -y -ss "$OFFSET" -t "$CHUNK_SECONDS" -i "$AUDIO_FILE" \
            -acodec pcm_s16le -ar 16000 -ac 1 \
            "$TEMP_DIR/audio_chunks/chunk_$(printf '%03d' $CHUNK_IDX).wav" 2>/dev/null
        CHUNK_IDX=$((CHUNK_IDX + 1))
        OFFSET=$((OFFSET + CHUNK_SECONDS))
    done
    log "Split audio into $CHUNK_IDX chunks of ${CHUNK_SECONDS}s each"

    python3 << 'PYEOF'
import json, sys, os, glob

try:
    from faster_whisper import WhisperModel
except ImportError:
    print("ERROR: faster-whisper not installed", file=sys.stderr)
    sys.exit(1)

cache_dir = "/root/.cache/whisper-models"
chunk_seconds = 1200  # must match CHUNK_SECONDS above
whisper_model = os.environ.get("CLIP_WHISPER_MODEL", "large-v3")

# Try GPU first, fall back to CPU
try:
    model = WhisperModel(whisper_model, device="cuda", compute_type="float16", download_root=cache_dir)
    print(f"[WHISPER] Using GPU (float16) with {whisper_model}", file=sys.stderr)
except Exception as e:
    print(f"[WHISPER] GPU failed ({e}), falling back to CPU", file=sys.stderr)
    model = WhisperModel(whisper_model, device="cpu", compute_type="int8", download_root=cache_dir)
    print(f"[WHISPER] Using CPU (int8) with {whisper_model}", file=sys.stderr)

# Transcribe each chunk and merge with corrected timestamps
chunk_files = sorted(glob.glob("/tmp/clipper/audio_chunks/chunk_*.wav"))
print(f"[WHISPER] Processing {len(chunk_files)} audio chunks...", file=sys.stderr)

all_results = []
all_srt_lines = []
srt_idx = 1
total_duration = 0.0

for ci, chunk_path in enumerate(chunk_files):
    chunk_offset = ci * chunk_seconds
    print(f"[WHISPER] Chunk {ci+1}/{len(chunk_files)} (offset {chunk_offset}s)...", file=sys.stderr)

    try:
        segments, info = model.transcribe(chunk_path, beam_size=5, word_timestamps=True)

        chunk_results = 0
        chunk_dots = 0
        for seg in segments:
            text = seg.text.strip()
            # Skip degenerate dot-only segments
            if text in [".", ""]:
                chunk_dots += 1
                continue

            abs_start = round(seg.start + chunk_offset, 2)
            abs_end = round(seg.end + chunk_offset, 2)

            all_results.append({
                "start": abs_start,
                "end": abs_end,
                "text": text
            })

            start_h, start_r = divmod(abs_start, 3600)
            start_m, start_s = divmod(start_r, 60)
            end_h, end_r = divmod(abs_end, 3600)
            end_m, end_s = divmod(end_r, 60)
            all_srt_lines.append(f"{srt_idx}")
            all_srt_lines.append(
                f"{int(start_h):02d}:{int(start_m):02d}:{start_s:06.3f}".replace(".", ",") +
                " --> " +
                f"{int(end_h):02d}:{int(end_m):02d}:{end_s:06.3f}".replace(".", ",")
            )
            all_srt_lines.append(text)
            all_srt_lines.append("")
            srt_idx += 1
            chunk_results += 1

        if ci == len(chunk_files) - 1:
            total_duration = chunk_offset + info.duration

        print(f"  -> {chunk_results} segments ({chunk_dots} dots filtered)", file=sys.stderr)

    except Exception as e:
        print(f"  -> chunk {ci} failed: {e}", file=sys.stderr)

with open("/tmp/clipper/transcript.json", "w") as f:
    json.dump(all_results, f, indent=2)

with open("/tmp/clipper/transcript.srt", "w") as f:
    f.write("\n".join(all_srt_lines))

duration_min = total_duration / 60.0 if total_duration > 0 else 0
word_count = sum(len(s["text"].split()) for s in all_results)
print(json.dumps({
    "duration_min": round(duration_min, 1),
    "segments": len(all_results),
    "words": word_count
}))
PYEOF

    # Cache the transcription for future re-clips
    cp "$TEMP_DIR/transcript.json" "$CACHED_JSON"
    cp "$TEMP_DIR/transcript.srt" "$CACHED_SRT"
    log "Transcription cached to $TRANSCRIPT_CACHE_DIR/"
fi

log "Transcription complete. Output: $TEMP_DIR/transcript.json"

# ============================================================
# STAGE 3 — Segment Detection (NEW)
# ============================================================
set_stage "Stage 3/8 — Segment Detection"
log "=== Stage 3/8 — Segment Detection ==="

python3 << PYEOF
import json, sys, time
try:
    import urllib.request
except:
    pass

OLLAMA_URL = "$OLLAMA_URL"
TEXT_MODEL = "$TEXT_MODEL"
TEMP_DIR = "/tmp/clipper"
STREAM_TYPE_HINT = "$STREAM_TYPE_HINT"

with open(f"{TEMP_DIR}/transcript.json") as f:
    segments = json.load(f)

if not segments:
    print("No transcript. Defaulting to single just_chatting segment.", file=sys.stderr)
    with open(f"{TEMP_DIR}/segments.json", "w") as f:
        json.dump([{"start": 0, "end": 0, "type": "just_chatting"}], f)
    sys.exit(0)

max_time = max(s["end"] for s in segments)

# If user provided a stream type hint (e.g. "irl", "gaming"), use it as a bias
# Valid hints: gaming, irl, just_chatting, reaction, debate, variety
VALID_TYPES = ["gaming", "irl", "just_chatting", "reaction", "debate"]
hint_type = None
if STREAM_TYPE_HINT:
    hint_lower = STREAM_TYPE_HINT.lower().strip()
    # Map common aliases
    aliases = {"chatting": "just_chatting", "chat": "just_chatting", "variety": None,
               "react": "reaction", "reacting": "reaction", "game": "gaming",
               "outdoor": "irl", "outside": "irl", "travel": "irl", "cooking": "irl"}
    if hint_lower in VALID_TYPES:
        hint_type = hint_lower
    elif hint_lower in aliases:
        hint_type = aliases[hint_lower]
    if hint_type:
        print(f"Stream type hint: '{hint_type}' — will bias segment classification", file=sys.stderr)

# Chunk into ~10 minute windows for classification
SEGMENT_CHUNK = 600  # 10 minutes
chunk_start = segments[0]["start"]
raw_segments = []

while chunk_start < max_time:
    chunk_end = chunk_start + SEGMENT_CHUNK
    chunk_segs = [s for s in segments if s["start"] < chunk_end and s["end"] > chunk_start]

    if not chunk_segs:
        chunk_start += SEGMENT_CHUNK
        continue

    # Build condensed text (limit to ~600 words for fast classification)
    chunk_texts = [s["text"] for s in chunk_segs]
    combined = " ".join(chunk_texts)
    words = combined.split()
    if len(words) > 600:
        combined = " ".join(words[:600])

    if len(words) < 10:
        # Too sparse to classify, default
        raw_segments.append({"start": int(chunk_start), "end": int(min(chunk_end, max_time)), "type": "just_chatting"})
        chunk_start += SEGMENT_CHUNK
        continue

    hint_note = ""
    if hint_type:
        hint_note = f"\nNote: This is likely a {hint_type} stream, but segments may vary. Classify based on actual content."

    prompt = f"""Classify this livestream transcript chunk into exactly ONE type:
- gaming (gameplay talk, strategy, callouts, wins/losses, game events)
- irl (real life, outside, daily activities, eating, traveling)
- just_chatting (casual conversation, Q&A, chat interaction, stories, chill vibes)
- reaction (watching/reacting to videos, clips, news, content)
- debate (arguments, disagreements, controversial topics, heated discussion)
{hint_note}
Transcript:
{combined}

Respond with ONLY the single type name. Nothing else."""

    payload = json.dumps({
        "model": TEXT_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "think": False,
        "stream": False,
        "options": {"temperature": 0.1, "num_predict": 10, "num_ctx": 8192}
    }).encode()

    seg_type = "just_chatting"  # default
    try:
        req = urllib.request.Request(
            f"{OLLAMA_URL}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode())
            answer = result.get("message", {}).get("content", "").strip().lower()
            # Extract the type from the response (model might add extra text)
            for t in ["gaming", "irl", "just_chatting", "reaction", "debate"]:
                if t in answer:
                    seg_type = t
                    break
    except Exception as e:
        print(f"  Segment classification failed at {int(chunk_start)}s: {e}", file=sys.stderr)

    raw_segments.append({"start": int(chunk_start), "end": int(min(chunk_end, max_time)), "type": seg_type})
    print(f"  {int(chunk_start)}s-{int(min(chunk_end, max_time))}s: {seg_type}", file=sys.stderr)
    chunk_start += SEGMENT_CHUNK

# Merge adjacent segments of the same type
merged_segments = []
for seg in raw_segments:
    if merged_segments and merged_segments[-1]["type"] == seg["type"]:
        merged_segments[-1]["end"] = seg["end"]
    else:
        merged_segments.append(dict(seg))

with open(f"{TEMP_DIR}/segments.json", "w") as f:
    json.dump(merged_segments, f, indent=2)

# Infer overall stream type from segment durations
type_durations = {}
for seg in merged_segments:
    t = seg["type"]
    dur = seg["end"] - seg["start"]
    type_durations[t] = type_durations.get(t, 0) + dur

total_dur = sum(type_durations.values()) or 1
dominant_type = max(type_durations, key=type_durations.get)
dominant_pct = type_durations[dominant_type] / total_dur * 100

# Build stream profile
stream_profile = {
    "dominant_type": dominant_type,
    "dominant_pct": round(dominant_pct, 1),
    "type_breakdown": {k: round(v / total_dur * 100, 1) for k, v in sorted(type_durations.items(), key=lambda x: -x[1])},
    "is_variety": dominant_pct < 60,
    "hint_used": hint_type or "none"
}

with open(f"{TEMP_DIR}/stream_profile.json", "w") as f:
    json.dump(stream_profile, f, indent=2)

# Print timeline
print(f"\nStream segment map ({len(merged_segments)} segments):", file=sys.stderr)
for seg in merged_segments:
    duration_min = (seg["end"] - seg["start"]) / 60
    start_min = seg["start"] / 60
    print(f"  {start_min:.0f}min - {start_min + duration_min:.0f}min: {seg['type']} ({duration_min:.0f} min)", file=sys.stderr)

print(f"\nStream profile: {dominant_type} ({dominant_pct:.0f}%)", file=sys.stderr)
if stream_profile["is_variety"]:
    print("  Variety stream detected — multiple segment types", file=sys.stderr)
for t, pct in stream_profile["type_breakdown"].items():
    print(f"  {t}: {pct}%", file=sys.stderr)

print(f"Segment detection complete: {len(merged_segments)} segments identified")
PYEOF

log "Segment detection complete"

# ============================================================
# STAGE 4 — Moment Detection (Three-Pass Hybrid, Segment-Aware)
# ============================================================
set_stage "Stage 4/8 — Moment Detection"
log "=== Stage 4/8 — Moment Detection (style: $CLIP_STYLE) ==="

python3 << PYEOF
import json, re, sys, time, os, math
try:
    import urllib.request
except:
    pass

OLLAMA_URL = "$OLLAMA_URL"
TEXT_MODEL = "$TEXT_MODEL"
CLIP_STYLE = "$CLIP_STYLE"
TEMP_DIR = "/tmp/clipper"

with open(f"{TEMP_DIR}/transcript.json") as f:
    segments = json.load(f)

with open(f"{TEMP_DIR}/segments.json") as f:
    segment_map = json.load(f)

if not segments:
    print("No transcript segments. Exiting.")
    with open(f"{TEMP_DIR}/hype_moments.json", "w") as f:
        json.dump([], f)
    sys.exit(0)

max_time = max(s["end"] for s in segments)
vod_hours = max_time / 3600.0

# Dynamic clip target: 2-4 clips per hour, min 3, max 20
TARGET_PER_HOUR = 3
MAX_CLIPS = max(3, min(int(math.ceil(vod_hours * TARGET_PER_HOUR)), 20))
# Allow more candidates through detection to feed into scoring/filtering
MAX_CANDIDATES = MAX_CLIPS * 2

print(f"VOD: {vod_hours:.1f} hours => target {MAX_CLIPS} clips (max {MAX_CANDIDATES} candidates)", file=sys.stderr)

def get_segment_type(timestamp):
    """Return the stream segment type for a given timestamp."""
    for seg in segment_map:
        if seg["start"] <= timestamp <= seg["end"]:
            return seg["type"]
    # Fallback: find closest
    if segment_map:
        closest = min(segment_map, key=lambda s: abs(s["start"] - timestamp))
        return closest["type"]
    return "just_chatting"

# ==============================================================
# PASS A — Segment-Aware Keyword Scanner (instant, no LLM)
# ==============================================================
print("[PASS A] Segment-aware keyword scan...", file=sys.stderr)

KEYWORD_SETS = {
    "hype": [
        "oh my god", "no way", "clip that", "let's go", "holy shit",
        "what the fuck", "no no no", "yes yes yes", "did you see that",
        "i can't believe", "lmao", "lmfao", "hahaha", "let's gooo",
        "insane", "unbelievable", "clutch", "oh shit", "poggers", "pog",
        "that was crazy", "oh my", "yoooo", "sheeeesh", "banger",
        "w stream", "dub", "we won", "massive", "legendary"
    ],
    "funny": [
        "i'm dead", "bruh", "that's so bad", "why would you", "bro what",
        "dude", "i can't", "stop", "help", "no he didn't", "she didn't",
        "what is that", "that's crazy", "are you serious", "you're trolling",
        "lol", "haha", "i'm crying", "that's hilarious", "comedy",
        "wait what", "bro", "nah", "ain't no way", "i'm wheezing",
        "i can't breathe", "that's so funny", "you did not", "caught in 4k",
        "sus", "down bad", "violated", "cooked", "finished"
    ],
    "emotional": [
        "i love you", "thank you so much", "that means a lot", "i appreciate",
        "i'm sorry", "it's been hard", "i just want to say", "you guys are",
        "honestly", "real talk", "from the bottom of my heart", "grateful",
        "miss you", "struggling", "mental health", "tough time",
        "i needed that", "means the world", "i can't thank you enough",
        "i'm gonna cry", "that hit different", "vulnerable", "opening up",
        "depression", "anxiety", "been through a lot", "love you guys"
    ],
    "hot_take": [
        "hot take", "i don't care what anyone says", "fight me", "unpopular opinion",
        "this is gonna be controversial", "here's the thing", "wrong",
        "that's not okay", "cancel", "problematic", "woke", "based",
        "ratio", "cope", "you're wrong", "nobody wants to hear this",
        "i said what i said", "don't @ me", "hear me out", "controversial",
        "honestly though", "people don't want to hear", "the truth is",
        "i'll say it", "no one talks about", "overrated", "underrated",
        "mid", "trash take", "delusional"
    ],
    "storytime": [
        "so basically", "let me tell you", "you won't believe", "long story short",
        "so this happened", "i was at", "the craziest thing", "true story",
        "one time", "back when", "so i was", "this one time", "i remember when",
        "what happened was", "the other day", "story time", "gather around",
        "you want to know", "let me explain", "so get this",
        "i gotta tell you", "the wildest thing", "not gonna lie",
        "you're not gonna believe this", "so picture this", "fun fact"
    ],
    "reactive": [
        "what is wrong with", "are you kidding", "i'm so done", "this is unacceptable",
        "this is ridiculous", "i'm pissed", "rage", "tilted", "so annoying",
        "sick of this", "how is this fair", "broken", "scam", "garbage",
        "worst", "terrible", "disgusting", "why does this always",
        "makes my blood boil", "actually insane", "look at this",
        "did you just see", "watch this", "hold on", "pause",
        "excuse me", "what did i just", "absolutely not", "hell no",
        "i'm shaking", "trembling", "speechless"
    ],
    "dancing": [
        "dance", "dancing", "twerk", "moves", "hit that", "do it",
        "go go go", "get it", "vibe", "vibing", "groove", "grooving",
        "bust a move", "let's dance", "song", "turn up", "body roll",
        "choreo", "choreography", "performing", "the dance"
    ],
    "controversial": [
        "drama", "beef", "called out", "exposed", "receipts", "caught",
        "tea", "spill", "shade", "throwing shade", "shots fired",
        "that's cap", "lying", "fake", "two-faced", "snake",
        "banned", "canceled", "cancelled", "suspended", "kicked",
        "he said she said", "clipped out of context", "oh hell no"
    ]
}

# Segment-specific keyword weight multipliers
# Boosts keywords that are natural for that segment type
SEGMENT_KEYWORD_WEIGHTS = {
    "gaming":       {"hype": 1.5, "funny": 1.0, "emotional": 0.8, "hot_take": 0.7, "storytime": 0.6, "reactive": 1.0, "dancing": 0.4, "controversial": 0.6},
    "irl":          {"hype": 0.8, "funny": 1.4, "emotional": 1.4, "hot_take": 1.0, "storytime": 1.3, "reactive": 0.8, "dancing": 1.5, "controversial": 1.0},
    "just_chatting": {"hype": 0.7, "funny": 1.3, "emotional": 1.3, "hot_take": 1.4, "storytime": 1.5, "reactive": 0.8, "dancing": 1.2, "controversial": 1.3},
    "reaction":     {"hype": 1.0, "funny": 1.2, "emotional": 0.8, "hot_take": 1.5, "storytime": 0.6, "reactive": 1.5, "dancing": 0.5, "controversial": 1.4},
    "debate":       {"hype": 0.7, "funny": 0.8, "emotional": 1.0, "hot_take": 1.5, "storytime": 0.8, "reactive": 1.3, "dancing": 0.3, "controversial": 1.5},
}

# Keyword thresholds — raised to reduce false positives from overused keywords
# A single "bruh" or "oh my god" is NOT enough — need multiple signals converging
SEGMENT_THRESHOLD = {
    "gaming": 3,
    "irl": 2,
    "just_chatting": 2,
    "reaction": 3,
    "debate": 2,
}

def keyword_scan(segments):
    """Segment-aware keyword scan with dynamic thresholds."""
    WINDOW_SIZE = 30
    STEP = 10
    flagged = []

    if not segments:
        return flagged

    max_time = max(s["end"] for s in segments)
    t = segments[0]["start"]

    while t < max_time:
        window_start = t
        window_end = t + WINDOW_SIZE
        window_segs = [s for s in segments if s["start"] < window_end and s["end"] > window_start]

        if window_segs:
            seg_type = get_segment_type(window_start + WINDOW_SIZE / 2)
            weights = SEGMENT_KEYWORD_WEIGHTS.get(seg_type, {})
            threshold = SEGMENT_THRESHOLD.get(seg_type, 2)

            texts = [s["text"] for s in window_segs]
            combined = " ".join(texts).lower()
            categories_found = {}
            total_signals = 0.0

            # Category-specific keyword matching with segment weights
            for cat, phrases in KEYWORD_SETS.items():
                cat_signals = 0
                for phrase in phrases:
                    if phrase in combined:
                        cat_signals += 1
                if cat_signals > 0:
                    weight = weights.get(cat, 1.0)
                    weighted = cat_signals * weight
                    categories_found[cat] = weighted
                    total_signals += weighted

            # Universal signals
            excl_count = sum(1 for t_text in texts if t_text.endswith("!") or "!!" in t_text)
            if excl_count >= 2:
                total_signals += 1
                categories_found["hype"] = categories_found.get("hype", 0) + 1

            # ALL CAPS streaks
            for t_text in texts:
                words = t_text.split()
                caps_streak = 0
                for w in words:
                    if w.isupper() and len(w) > 1:
                        caps_streak += 1
                        if caps_streak >= 3:
                            total_signals += 1
                            categories_found["hype"] = categories_found.get("hype", 0) + 1
                            break
                    else:
                        caps_streak = 0

            # Rapid fire short sentences
            short_count = sum(1 for t_text in texts if len(t_text.split()) < 5 and len(t_text) > 0)
            if short_count >= 4:
                total_signals += 1

            # Laughter markers
            if any(m in combined for m in ["[laughter]", "hahaha", "lmfao", "lmao"]):
                total_signals += 1
                categories_found["funny"] = categories_found.get("funny", 0) + 1

            # Question clusters (debate/engagement)
            question_count = sum(1 for t_text in texts if "?" in t_text)
            if question_count >= 3:
                total_signals += 1
                categories_found["controversial"] = categories_found.get("controversial", 0) + 1

            # Long pause then burst (emotional/dramatic)
            if len(window_segs) >= 3:
                gaps = []
                for i in range(1, len(window_segs)):
                    gap = window_segs[i]["start"] - window_segs[i-1]["end"]
                    gaps.append(gap)
                if any(g > 3.0 for g in gaps):
                    total_signals += 1
                    categories_found["emotional"] = categories_found.get("emotional", 0) + 1

            # Multi-category bonus
            if len(categories_found) >= 2:
                total_signals += 1

            # Use segment-specific threshold
            if total_signals >= threshold:
                center = window_start + WINDOW_SIZE / 2
                top_cat = max(categories_found, key=categories_found.get) if categories_found else "hype"
                # Normalize to 0.0-1.0: threshold is floor (0.0), 10+ signals is ceiling (1.0)
                # Use sigmoid-like curve so diminishing returns above ~6 signals
                raw = total_signals - threshold  # signals above threshold
                max_meaningful = 8.0  # signals above threshold that maps to ~1.0
                norm_score = min(raw / max_meaningful, 1.0)
                # Apply slight S-curve for better spread in the middle range
                norm_score = norm_score ** 0.8  # compress top, expand bottom
                flagged.append({
                    "timestamp": round(center),
                    "score": round(norm_score, 3),
                    "preview": " ".join(s["text"] for s in window_segs[:3])[:120],
                    "categories": list(categories_found.keys()),
                    "primary_category": top_cat,
                    "source": "keyword",
                    "segment_type": seg_type
                })

        t += STEP

    # Merge overlapping (within 20s)
    merged = []
    for moment in sorted(flagged, key=lambda x: x["timestamp"]):
        if merged and abs(moment["timestamp"] - merged[-1]["timestamp"]) < 20:
            if moment["score"] > merged[-1]["score"]:
                merged[-1] = moment
        else:
            merged.append(moment)

    return merged

keyword_moments = keyword_scan(segments)
print(f"[PASS A] Found {len(keyword_moments)} keyword moments", file=sys.stderr)
for m in keyword_moments:
    print(f"  T={m['timestamp']}s [{m['primary_category']}] score={m['score']} segment={m['segment_type']}", file=sys.stderr)
with open(f"{TEMP_DIR}/keyword_moments.json", "w") as f:
    json.dump(keyword_moments, f, indent=2)


# ==============================================================
# PASS B — Segment-Aware LLM Chunk Analysis
# ==============================================================
print("[PASS B] Segment-aware LLM transcript analysis...", file=sys.stderr)

def format_chunk(segs):
    """Format transcript segments with timestamps for the LLM."""
    lines = []
    for s in segs:
        minutes = int(s["start"] // 60)
        secs = int(s["start"] % 60)
        lines.append(f"[{minutes:02d}:{secs:02d}] {s['text']}")
    return "\\n".join(lines)

def time_str_to_seconds(time_str):
    """Convert MM:SS or H:MM:SS to seconds."""
    parts = time_str.strip().split(":")
    try:
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        elif len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    except (ValueError, IndexError):
        pass
    return None

def call_ollama(prompt, model=TEXT_MODEL, max_retries=2, timeout=120, num_predict=800, num_ctx=32768, allow_think=False):
    """Call Ollama API. Handles both thinking and non-thinking models.

    For thinking models (qwen3-vl, qwen3.5): thinking tokens count toward num_predict.
    If content comes back empty but thinking exists, we retry with a larger budget.
    """
    current_predict = num_predict

    for attempt in range(max_retries + 1):  # +1 for thinking retry
        payload = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "think": allow_think,
            "stream": False,
            "options": {"temperature": 0.3, "num_predict": current_predict, "num_ctx": num_ctx}
        }).encode()

        try:
            req = urllib.request.Request(
                f"{OLLAMA_URL}/api/chat",
                data=payload,
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                result = json.loads(resp.read().decode())
                msg = result.get("message", {})
                content = msg.get("content", "")
                thinking = msg.get("thinking", "")

                # If model returned thinking but empty content, it exhausted tokens on thinking
                if not content and thinking and current_predict < num_predict * 3:
                    think_tokens = len(thinking.split())
                    new_predict = current_predict + think_tokens + 200
                    print(f"  Thinking model used {think_tokens} think tokens, content empty. Retrying with num_predict={new_predict}", file=sys.stderr)
                    current_predict = new_predict
                    continue

                return content
        except Exception as e:
            print(f"  Ollama call attempt {attempt+1}/{max_retries} failed: {e}", file=sys.stderr)
            if attempt < max_retries - 1:
                time.sleep(5)
    return None

def parse_llm_moments(response_text, chunk_start, chunk_end):
    """Defensively parse LLM JSON response into moments."""
    if not response_text:
        return []

    clean = response_text.strip()

    if "\`\`\`" in clean:
        parts = clean.split("\`\`\`")
        if len(parts) >= 2:
            clean = parts[1]
            if clean.startswith("json"):
                clean = clean[4:]
            clean = clean.strip()

    arr_start = clean.find("[")
    arr_end = clean.rfind("]") + 1

    parsed_moments = []

    if arr_start >= 0 and arr_end > arr_start:
        try:
            arr = json.loads(clean[arr_start:arr_end])
            if isinstance(arr, list):
                parsed_moments = arr
        except json.JSONDecodeError:
            pass

    if not parsed_moments:
        for match in re.finditer(r'\{[^{}]+\}', clean):
            try:
                obj = json.loads(match.group())
                if "time" in obj or "timestamp" in obj:
                    parsed_moments.append(obj)
            except:
                pass

    results = []
    for m in parsed_moments:
        time_val = m.get("time") or m.get("timestamp", "")
        if isinstance(time_val, str):
            ts = time_str_to_seconds(time_val)
        elif isinstance(time_val, (int, float)):
            ts = int(time_val)
        else:
            continue

        if ts is None:
            continue

        ts = max(chunk_start, min(ts, chunk_end))

        score = 0
        try:
            score = int(m.get("score", 0))
        except (ValueError, TypeError):
            pass

        if score < 1:
            continue

        # Normalize LLM score from 1-10 to 0.0-1.0
        norm_score = round(max(0.0, min((score - 1) / 9.0, 1.0)), 3)

        category = str(m.get("category", "unknown")).lower().strip()
        cat_map = {
            "comedy": "funny", "humor": "funny", "humour": "funny",
            "emotion": "emotional", "sad": "emotional", "heartfelt": "emotional",
            "controversy": "hot_take", "controversial": "controversial", "debate": "hot_take",
            "hot-take": "hot_take", "hottake": "hot_take", "opinion": "hot_take",
            "rage": "reactive", "anger": "reactive", "frustration": "reactive",
            "ragebait": "reactive", "reaction": "reactive",
            "story": "storytime", "narrative": "storytime", "anecdote": "storytime",
            "excitement": "hype", "intense": "hype", "skill": "hype", "clutch": "hype",
            "dance": "dancing", "dancing": "dancing", "twerk": "dancing", "moves": "dancing"
        }
        category = cat_map.get(category, category)
        VALID_CATEGORIES = ("hype", "funny", "emotional", "hot_take", "storytime", "reactive", "dancing", "controversial")
        if category not in VALID_CATEGORIES:
            category = "hype"

        # Parse clip boundaries if LLM provided them
        clip_start_time = None
        clip_end_time = None
        raw_start = m.get("start_time") or m.get("start", "")
        raw_end = m.get("end_time") or m.get("end", "")
        if isinstance(raw_start, str) and raw_start:
            clip_start_time = time_str_to_seconds(raw_start)
        elif isinstance(raw_start, (int, float)):
            clip_start_time = int(raw_start)
        if isinstance(raw_end, str) and raw_end:
            clip_end_time = time_str_to_seconds(raw_end)
        elif isinstance(raw_end, (int, float)):
            clip_end_time = int(raw_end)

        # Validate and clamp boundaries
        if clip_start_time is not None and clip_end_time is not None:
            clip_start_time = max(chunk_start, min(clip_start_time, chunk_end))
            clip_end_time = max(chunk_start, min(clip_end_time, chunk_end))
            duration = clip_end_time - clip_start_time
            # Enforce bounds: 15s minimum, 90s maximum
            if duration < 15:
                clip_start_time = None
                clip_end_time = None
            elif duration > 90:
                # Trim to 90s centered on the peak timestamp
                clip_start_time = max(chunk_start, ts - 45)
                clip_end_time = clip_start_time + 90

        result_entry = {
            "timestamp": ts,
            "score": norm_score,
            "preview": str(m.get("why", m.get("reason", "")))[:120],
            "categories": [category],
            "primary_category": category,
            "source": "llm",
            "why": str(m.get("why", m.get("reason", "")))[:200]
        }
        if clip_start_time is not None and clip_end_time is not None:
            result_entry["clip_start"] = clip_start_time
            result_entry["clip_end"] = clip_end_time
        results.append(result_entry)

    return results

# Segment-specific LLM prompts — tailored to what each segment type produces
SEGMENT_PROMPTS = {
    "gaming": """Focus on GAMEPLAY moments:
- Clutch plays, skillful outplays, close calls, narrow escapes
- Epic wins or devastating losses, comeback moments
- Funny fails, glitches, unexpected game events
- Rage moments when losing, trash talk, celebrating wins
- Reactions to in-game events viewers would find exciting
- Moments where the streamer narrates something absurd happening in-game""",

    "irl": """Focus on IRL moments (these are NATURALLY QUIETER so lower your bar for what counts):
- Funny real-world situations, awkward encounters with strangers
- Interesting locations, unexpected events happening around streamer
- STORYTIME: Streamer telling a story while walking, traveling, or doing something — look for narrative arc
- Genuine emotional moments, real talk while walking/traveling
- Interactions with friends, strangers, or the environment
- Someone off-camera saying something unexpected that changes the situation
- Situational irony — streamer claims something then reality contradicts them
- Getting kicked out, confronted, or encountering unexpected resistance
- DANCING or physical performance — streamer vibing, dancing, doing moves
- Even small charming or relatable moments count here""",

    "just_chatting": """Focus on CONVERSATION moments (lower your bar for what counts — subtle is fine):
- STORYTIME: A story building to a punchline, reveal, or unexpected twist. The setup matters as much as the payoff
- HOT TAKES: Unpopular opinions, controversial claims, bold statements that will make viewers react
- Funny stories, witty one-liners, comedic timing
- Emotional vulnerability, real talk, genuine audience connection
- CONTROVERSIAL: Drama, tea-spilling, call-outs, gossip, beef, exposing someone
- Audience interaction moments that are entertaining
- Moments where the streamer says something quotable
- Someone (chat, friend, co-host) calling the streamer out or correcting them
- The streamer setting something up (bragging, explaining) and then getting undercut
- DANCING or vibing to music, physical comedy""",

    "reaction": """Focus on REACTION moments:
- Strong emotional reactions to content (shock, anger, laughter, disbelief)
- HOT TAKES about what they're watching — opinions viewers will argue about
- Reactive rage or disbelief — things viewers will clip and share
- Disagreeing strongly with popular opinion
- Over-the-top reactions that are entertaining to watch
- Moments where the streamer's reaction IS the content
- Streamer confidently stating something, then immediately being proven wrong
- Double-takes, jaw drops, or moments where they have to pause and process
- CONTROVERSIAL takes that would blow up on social media""",

    "debate": """Focus on DEBATE/ARGUMENT moments:
- Strongest arguments, mic-drop moments
- When someone gets heated, raises their voice, or loses composure
- Controversial claims that would generate engagement
- Funny comebacks or roasts during arguments
- When the conversation takes an unexpected turn
- Moments where someone says something the audience will quote
- Someone getting caught in a contradiction or logical trap"""
}

# Score boost for naturally quieter segments to compete fairly (0-1 scale)
SEGMENT_SCORE_BOOST = {
    "gaming": 0.0,
    "irl": 0.10,
    "just_chatting": 0.10,
    "reaction": 0.0,
    "debate": 0.0,
}

# Build style-specific prompt emphasis
style_prompts = {
    "auto": "Find the most engaging moments of ANY type. Balance variety.",
    "hype": "Prioritize exciting, intense, high-energy moments. Celebrations, clutch plays, shock reactions.",
    "funny": "Prioritize comedy. Funny stories, awkward moments, witty lines, ironic situations, fails, deadpan delivery.",
    "emotional": "Prioritize emotional depth. Vulnerable sharing, heartfelt gratitude, real talk, difficult topics, genuine moments.",
    "hot_take": "Prioritize controversial opinions, hot takes, unpopular opinions, bold claims that viewers will debate.",
    "storytime": "Prioritize narrative moments — stories with setup and payoff, anecdotes building to a punchline or reveal.",
    "reactive": "Prioritize strong reactions — rage, shock, disbelief, over-the-top responses to events or content.",
    "controversial": "Prioritize drama, call-outs, beef, tea-spilling, edgy statements, anything that would blow up on social media.",
    "dancing": "Prioritize physical performance moments — dancing, moves, vibing, physical comedy, any body-based entertainment.",
    "variety": "Find ONE moment from EACH category. Maximum diversity across all categories."
}
style_hint = style_prompts.get(CLIP_STYLE, style_prompts["auto"])

# Chunk transcript into 5-minute windows with 30s overlap
CHUNK_DURATION = 300  # 5 minutes
CHUNK_OVERLAP = 30
chunk_start = segments[0]["start"]
llm_moments = []
chunk_count = 0

while chunk_start < max_time:
    chunk_end = chunk_start + CHUNK_DURATION
    overlap_start = max(0, chunk_start - CHUNK_OVERLAP)
    chunk_segs = [s for s in segments if s["start"] < chunk_end + CHUNK_OVERLAP and s["end"] > overlap_start]

    if not chunk_segs:
        chunk_start += CHUNK_DURATION
        continue

    chunk_count += 1
    chunk_text = format_chunk(chunk_segs)
    word_count = sum(len(s["text"].split()) for s in chunk_segs)

    if word_count < 15:
        print(f"  Chunk {chunk_count} ({int(chunk_start)}s-{int(chunk_end)}s): too sparse ({word_count} words), skipping", file=sys.stderr)
        chunk_start += CHUNK_DURATION
        continue

    # Get segment type for this chunk's midpoint
    seg_type = get_segment_type(chunk_start + CHUNK_DURATION / 2)
    seg_instructions = SEGMENT_PROMPTS.get(seg_type, SEGMENT_PROMPTS["just_chatting"])

    prompt = f"""You are a stream clip scout finding moments viewers will watch, share, and clip. This is a {seg_type.upper()} segment. Find 0-3 clip-worthy moments.

{seg_instructions}

STYLE: {style_hint}

IMPORTANT — Look beyond keywords:
Streamers say "oh my god", "bruh", "no way" constantly. These words alone don't make a clip. Look at what's HAPPENING — the situation, the context, the story.

Good clips have at least one of these:
1. SETUP + PAYOFF — something established then subverted (e.g. "this is my penthouse" then someone says "that's MY penthouse")
2. STORYTELLING — a story building to a punchline or surprising reveal
3. GENUINE REACTIONS — reacting to something specific and interesting (not just routine gameplay)
4. SITUATIONAL IRONY — confidence followed by failure, bragging followed by humiliation
5. SOCIAL DYNAMICS — someone calls them out, chat catches a lie, a friend roasts them
6. QUOTABLE MOMENTS — a one-liner, hot take, or deadpan observation worth repeating

Weaker but still valid clips:
- Streamer getting visibly emotional about something real
- An unexpected topic change that's funny or interesting
- Chat interaction that leads to a good bit
- Any moment where the energy noticeably shifts

Skip these:
- "oh my god" or "bruh" about nothing interesting (routine gameplay, reading donations)
- Generic hype ("let's gooo") with no interesting context

When in doubt, include the moment with a lower score (3-5) rather than skipping it. Let the scoring system decide.

Transcript (timestamps MM:SS from stream start):
{chunk_text}

Respond with ONLY a JSON array. Each element: {{"time": "MM:SS", "start_time": "MM:SS", "end_time": "MM:SS", "score": 1-10, "category": "hype|funny|emotional|hot_take|storytime|reactive|dancing|controversial", "why": "one sentence explaining the SITUATION not just the words"}}

IMPORTANT — start_time and end_time define the CLIP BOUNDARIES:
- start_time: where the moment BEGINS (include setup/context). For storytimes, this is where the story starts.
- end_time: where the moment ENDS (after the payoff/reaction lands). Don't trail into dead air.
- Minimum clip: 15 seconds. Maximum clip: 90 seconds. Most clips should be 25-45 seconds.
- Short reactions/one-liners: 15-25 seconds
- Standard moments (funny, hype, hot takes): 25-45 seconds
- Storytime/emotional with narrative arc: 45-75 seconds
- Only exceed 60 seconds for genuinely exceptional stories with clear setup+payoff

Categories:
- hype: exciting, intense, clutch plays, celebrations
- funny: comedy, fails, awkward moments, ironic situations
- emotional: vulnerable, heartfelt, real talk, genuine moments
- hot_take: unpopular opinions, bold claims that viewers will debate
- storytime: narrative buildup with payoff, anecdotes, storytelling
- reactive: strong reactions to something, rage, shock, disbelief
- dancing: physical performance, dancing, moves, physical comedy
- controversial: drama, call-outs, edgy statements, tea-spilling, beef
If nothing stands out at all, respond: []"""

    print(f"  Chunk {chunk_count} ({int(chunk_start)}s-{int(chunk_end)}s): {seg_type}, {word_count} words...", file=sys.stderr)

    response = call_ollama(prompt, num_predict=800)
    if response:
        chunk_moments = parse_llm_moments(response, int(chunk_start), int(chunk_end))

        # Apply segment score boost for quieter segments (0-1 scale)
        boost = SEGMENT_SCORE_BOOST.get(seg_type, 0.0)
        for m in chunk_moments:
            m["score"] = min(m["score"] + boost, 1.0)
            m["segment_type"] = seg_type

        print(f"  Chunk {chunk_count}: found {len(chunk_moments)} moments", file=sys.stderr)
        for m in chunk_moments:
            print(f"    T={m['timestamp']}s [{m['primary_category']}] score={m['score']} — {m.get('why','')[:60]}", file=sys.stderr)
        llm_moments.extend(chunk_moments)
    else:
        print(f"  Chunk {chunk_count}: LLM call failed, skipping", file=sys.stderr)

    chunk_start += CHUNK_DURATION

print(f"[PASS B] LLM found {len(llm_moments)} moments across {chunk_count} chunks", file=sys.stderr)
with open(f"{TEMP_DIR}/llm_moments.json", "w") as f:
    json.dump(llm_moments, f, indent=2)


# ==============================================================
# PASS C — Merge, Deduplicate, Diversify, Select
# ==============================================================
print(f"[PASS C] Merging and selecting (target: {MAX_CLIPS} clips, max candidates: {MAX_CANDIDATES})...", file=sys.stderr)

all_moments = []

# Scores are already 0.0-1.0 from both passes.
# Keywords are useful for catching moments the LLM missed, but keyword-only
# moments should be penalized slightly since keywords lack context understanding.
KEYWORD_CEILING = 0.75  # keyword-only moments can't exceed this without cross-validation
for m in keyword_moments:
    m["normalized_score"] = min(m["score"], KEYWORD_CEILING)
    all_moments.append(m)

for m in llm_moments:
    m["normalized_score"] = m["score"]  # already 0.0-1.0
    all_moments.append(m)

all_moments.sort(key=lambda x: x["timestamp"])

# Deduplicate: merge moments within 25 seconds
deduped = []
for m in all_moments:
    merged = False
    for d in deduped:
        if abs(m["timestamp"] - d["timestamp"]) < 25:
            if m["source"] != d["source"]:
                # Cross-validated: multiplicative boost (×1.25) — much better than additive
                d["normalized_score"] = min(max(d["normalized_score"], m["normalized_score"]) * 1.25, 1.0)
                d["cross_validated"] = True
                for cat in m.get("categories", []):
                    if cat not in d.get("categories", []):
                        d["categories"].append(cat)
                # Inherit clip boundaries from LLM if keyword doesn't have them
                if "clip_start" not in d and "clip_start" in m:
                    d["clip_start"] = m["clip_start"]
                    d["clip_end"] = m["clip_end"]
                if m["normalized_score"] > d["normalized_score"] * 0.8:
                    d["preview"] = m.get("why") or m.get("preview", d["preview"])
            elif m["normalized_score"] > d["normalized_score"]:
                old_boundaries = {k: d.get(k) for k in ("clip_start", "clip_end") if k in d}
                d.update(m)
                # Preserve boundaries from earlier entry if new one lacks them
                for k, v in old_boundaries.items():
                    if k not in d and v is not None:
                        d[k] = v
            merged = True
            break
    if not merged:
        m["cross_validated"] = False
        deduped.append(m)

print(f"  After dedup: {len(deduped)} unique moments ({sum(1 for d in deduped if d.get('cross_validated'))} cross-validated)", file=sys.stderr)

# --- LENGTH PENALTY FUNCTION ---
# Prevents over-clipping: longer clips need higher base scores to survive selection.
# Short punchy clips are favored unless the content genuinely justifies length.
def length_penalty(duration_sec):
    """Returns a multiplier 0.0-1.0 based on clip duration."""
    if duration_sec <= 30:
        return 1.0       # ideal short-form length, no penalty
    elif duration_sec <= 45:
        return 0.95       # slight penalty
    elif duration_sec <= 60:
        return 0.85       # needs to be genuinely good
    elif duration_sec <= 75:
        return 0.75       # only strong storytime/emotional survives
    else:
        return 0.65       # exceptional content only

# Compute clip duration for each moment
for m in deduped:
    if "clip_start" in m and "clip_end" in m:
        m["clip_duration"] = m["clip_end"] - m["clip_start"]
    else:
        # Default duration based on category
        cat = m.get("primary_category", "hype")
        DEFAULT_DURATIONS = {
            "storytime": 45, "emotional": 40, "controversial": 35,
            "hot_take": 35, "funny": 30, "hype": 30,
            "reactive": 25, "dancing": 25
        }
        dur = DEFAULT_DURATIONS.get(cat, 30)
        m["clip_duration"] = dur
        # Set default boundaries centered on the peak timestamp
        half = dur // 2
        m["clip_start"] = max(0, m["timestamp"] - half)
        m["clip_end"] = m["clip_start"] + dur

# Apply style weighting and length penalty
for m in deduped:
    base = m["normalized_score"]
    cat = m.get("primary_category", "hype")

    weight_map = {
        "auto": {},
        "hype": {"hype": 1.3},
        "funny": {"funny": 1.3},
        "emotional": {"emotional": 1.3},
        "hot_take": {"hot_take": 1.3},
        "storytime": {"storytime": 1.3, "emotional": 1.15},
        "reactive": {"reactive": 1.3, "hot_take": 1.15},
        "controversial": {"controversial": 1.3, "hot_take": 1.2, "reactive": 1.15},
        "dancing": {"dancing": 1.3, "funny": 1.1},
        "variety": {}
    }

    weights = weight_map.get(CLIP_STYLE, {})
    multiplier = weights.get(cat, 1.0)
    styled_score = base * multiplier

    # Cross-validated moments get multiplicative boost
    if m.get("cross_validated"):
        styled_score *= 1.20

    # Apply length penalty — longer clips need higher base scores
    lp = length_penalty(m["clip_duration"])
    m["final_score"] = round(min(styled_score * lp, 1.0), 4)
    m["length_penalty"] = lp

# ---- TIME-BUCKET DISTRIBUTION ----
# Divide VOD into equal time buckets and guarantee each bucket gets representation.
# This prevents early-VOD bias where high-scoring early moments dominate selection.
NUM_BUCKETS = max(3, min(int(vod_hours * 2), 10))  # 2 buckets per hour, 3-10 range
bucket_duration = max_time / NUM_BUCKETS
clips_per_bucket = max(1, MAX_CLIPS // NUM_BUCKETS)
overflow_slots = MAX_CLIPS - (clips_per_bucket * NUM_BUCKETS)  # leftover slots for best-of

print(f"  Time distribution: {NUM_BUCKETS} buckets of {bucket_duration/60:.0f}min, {clips_per_bucket} clips/bucket + {overflow_slots} overflow", file=sys.stderr)

# Place each moment into its time bucket
buckets = [[] for _ in range(NUM_BUCKETS)]
for m in deduped:
    bucket_idx = min(int(m["timestamp"] / bucket_duration), NUM_BUCKETS - 1)
    buckets[bucket_idx].append(m)

# --- STREAM POSITION WEIGHTING ---
# Streamers warm up over time. The best content is typically 20-70% through the stream.
# Apply a mild position weight to counter early-stream and late-stream bias.
# Shape: slight penalty at start (cold open), peak at 30-60%, gentle decline at end.
def position_weight(timestamp, max_t):
    """Returns a multiplier 0.85-1.05 based on stream position."""
    if max_t <= 0:
        return 1.0
    pos = timestamp / max_t  # 0.0 = start, 1.0 = end
    if pos < 0.10:
        return 0.88  # first 10% — intros, setup, low energy
    elif pos < 0.25:
        return 0.95  # warming up
    elif pos < 0.70:
        return 1.05  # prime content zone
    elif pos < 0.90:
        return 1.0   # still good, winding down
    else:
        return 0.92  # last 10% — outros, low energy

for m in deduped:
    pw = position_weight(m["timestamp"], max_time)
    m["final_score"] = round(min(m["final_score"] * pw, 1.0), 4)
    m["position_weight"] = pw

# --- WITHIN-BUCKET NORMALIZATION ---
# Normalize scores within each bucket so moments in quiet segments compete fairly
# with moments in high-energy segments. A 0.6 in a dead bucket is as valuable as
# a 0.8 in a bucket where everything scores high.
for bucket in buckets:
    if len(bucket) < 2:
        continue
    bucket_max = max(m["final_score"] for m in bucket)
    bucket_min = min(m["final_score"] for m in bucket)
    if bucket_max - bucket_min < 0.05:
        continue  # all scores are nearly identical, skip
    for m in bucket:
        # Blend: 70% global score + 30% within-bucket normalized score
        if bucket_max > bucket_min:
            bucket_norm = (m["final_score"] - bucket_min) / (bucket_max - bucket_min)
        else:
            bucket_norm = 0.5
        m["final_score"] = round(0.70 * m["final_score"] + 0.30 * bucket_norm, 4)

# Sort each bucket by final_score
for b in buckets:
    b.sort(key=lambda x: x["final_score"], reverse=True)

# Minimum spacing based on clip duration (prevents overlapping clips)
def min_spacing(m):
    """Minimum seconds between this clip and neighbors."""
    return max(30, m.get("clip_duration", 30) + 10)

# Selection: pick top N from each bucket, then fill overflow with best remaining
selected = []

# Phase 1: Guaranteed picks from each bucket (ensures time spread)
for i, bucket in enumerate(buckets):
    picked = 0
    for m in bucket:
        if picked >= clips_per_bucket:
            break
        # Check spacing against already-selected (use clip-duration-aware spacing)
        spacing = min_spacing(m)
        too_close = any(abs(m["timestamp"] - s["timestamp"]) < spacing for s in selected)
        if not too_close:
            selected.append(m)
            picked += 1
    bucket_start_min = (i * bucket_duration) / 60
    bucket_end_min = ((i + 1) * bucket_duration) / 60
    print(f"  Bucket {i+1} ({bucket_start_min:.0f}-{bucket_end_min:.0f}min): {picked} clips from {len(bucket)} candidates", file=sys.stderr)

# Phase 2: Fill overflow slots with best remaining moments (any bucket)
remaining = []
for bucket in buckets:
    for m in bucket:
        if m not in selected:
            remaining.append(m)
remaining.sort(key=lambda x: x["final_score"], reverse=True)

for m in remaining:
    if len(selected) >= MAX_CLIPS:
        break
    spacing = min_spacing(m)
    too_close = any(abs(m["timestamp"] - s["timestamp"]) < spacing for s in selected)
    if not too_close:
        selected.append(m)

# Phase 3: If a style is specified, apply style-aware re-ranking within the selection
if CLIP_STYLE == "variety":
    # Round-robin by category from the selected pool
    by_category = {}
    for m in selected:
        cat = m.get("primary_category", "hype")
        by_category.setdefault(cat, []).append(m)
    for cat in by_category:
        by_category[cat].sort(key=lambda x: x["final_score"], reverse=True)
    final = []
    cats = list(by_category.keys())
    idx = 0
    while len(final) < MAX_CLIPS and any(by_category.values()):
        cat = cats[idx % len(cats)]
        if by_category.get(cat):
            final.append(by_category[cat].pop(0))
        idx += 1
        cats = [c for c in cats if by_category.get(c)]
        if not cats:
            break
elif CLIP_STYLE not in ("auto", ""):
    # Style-specific: re-sort selected by style-weighted score, pick top N
    selected.sort(key=lambda x: x["final_score"], reverse=True)
    final = selected[:MAX_CLIPS]
else:
    # Auto: category cap — no single category exceeds 50% of clips
    selected.sort(key=lambda x: x["final_score"], reverse=True)
    final = []
    cat_counts = {}
    max_per_cat = max(2, int(MAX_CLIPS * 0.50))
    for m in selected:
        cat = m.get("primary_category", "hype")
        if cat_counts.get(cat, 0) < max_per_cat:
            final.append(m)
            cat_counts[cat] = cat_counts.get(cat, 0) + 1
        if len(final) >= MAX_CLIPS:
            break
    # Backfill if we didn't reach MAX_CLIPS due to category cap
    if len(final) < MAX_CLIPS:
        for m in selected:
            if m not in final:
                final.append(m)
                if len(final) >= MAX_CLIPS:
                    break

final.sort(key=lambda x: x["final_score"], reverse=True)

print(f"  Final selection: {len(final)} clips across {len(set(min(int(m['timestamp']/bucket_duration), NUM_BUCKETS-1) for m in final))} of {NUM_BUCKETS} time buckets", file=sys.stderr)

# Write output with clip boundaries and 0-1 scores
output = []
for m in final:
    entry = {
        "timestamp": m["timestamp"],
        "score": round(m["final_score"], 3),
        "clip_start": m.get("clip_start", max(0, m["timestamp"] - 15)),
        "clip_end": m.get("clip_end", m["timestamp"] + 15),
        "clip_duration": m.get("clip_duration", 30),
        "preview": m.get("preview", "")[:120],
        "category": m.get("primary_category", "unknown"),
        "why": m.get("why", m.get("preview", ""))[:200],
        "source": m.get("source", "unknown"),
        "cross_validated": m.get("cross_validated", False),
        "segment_type": m.get("segment_type", get_segment_type(m["timestamp"])),
        "length_penalty": m.get("length_penalty", 1.0),
        "position_weight": m.get("position_weight", 1.0)
    }
    output.append(entry)

with open(f"{TEMP_DIR}/hype_moments.json", "w") as f:
    json.dump(output, f, indent=2)

cats_found = {}
segs_found = {}
for m in output:
    cat = m.get("category", "?")
    cats_found[cat] = cats_found.get(cat, 0) + 1
    seg = m.get("segment_type", "?")
    segs_found[seg] = segs_found.get(seg, 0) + 1

print(f"\n[PASS C] Selected {len(output)} moments:", file=sys.stderr)
for m in output:
    xv = " [CROSS-VALIDATED]" if m.get("cross_validated") else ""
    dur = m.get("clip_duration", 30)
    lp = m.get("length_penalty", 1.0)
    pw = m.get("position_weight", 1.0)
    print(f"  T={m['timestamp']}s [{m['category']}] score={m['score']:.3f} dur={dur}s lp={lp} pw={pw:.2f} segment={m.get('segment_type','')} src={m['source']}{xv} — {m.get('why','')[:60]}", file=sys.stderr)
print(f"  Category breakdown: {json.dumps(cats_found)}", file=sys.stderr)
print(f"  Segment breakdown: {json.dumps(segs_found)}", file=sys.stderr)
print(f"Detected {len(output)} clip-worthy moments")
for m in output:
    dur = m.get("clip_duration", 30)
    print(f"  T={m['timestamp']}s score={m['score']:.3f} [{m['category']}] ({m.get('segment_type','')}) dur={dur}s — {m.get('why','')[:60]}")
PYEOF

MOMENT_COUNT=$(python3 -c "import json; m=json.load(open('/tmp/clipper/hype_moments.json')); print(len(m))")
log "Found $MOMENT_COUNT clip-worthy moments"

if [ "$MOMENT_COUNT" -eq 0 ]; then
    warn "No clip-worthy moments detected. No clips to make."
    echo "$VOD_BASENAME	$(date -u +%Y-%m-%dT%H:%M:%SZ)	no_moments	$CLIP_STYLE" >> "$PROCESSED_LOG"
    echo '{"status":"no_moments","clips":0,"style":"'"$CLIP_STYLE"'"}'
    exit 0
fi

# ============================================================
# STAGE 5 — Frame Extraction
# ============================================================
set_stage "Stage 5/8 — Frame Extraction"
log "=== Stage 5/8 — Frame Extraction ==="

TIMESTAMPS=$(python3 -c "import json; [print(m['timestamp']) for m in json.load(open('/tmp/clipper/hype_moments.json'))]")
FRAME_COUNT=0

while IFS= read -r T; do
    [ -z "$T" ] && continue
    START=$((T - 15))
    [ "$START" -lt 0 ] && START=0

    log "Extracting frames for moment at T=${T}s..."
    ffmpeg -nostdin -y -ss "$START" -i "$VOD_PATH" \
        -vf "fps=1/5,scale=960:540" \
        -frames:v 6 \
        -q:v 2 \
        "$TEMP_DIR/frames_${T}_%02d.jpg" 2>/dev/null || warn "Frame extraction failed for T=$T"

    FRAME_COUNT=$((FRAME_COUNT + 1))
done <<< "$TIMESTAMPS"

log "Extracted frames for $FRAME_COUNT moments"

# Free VRAM: unload text model before loading vision model
unload_ollama "$TEXT_MODEL"

# ============================================================
# STAGE 6 — Vision Enrichment (NOT a gatekeeper)
# Vision scoring adds titles/descriptions and can BOOST moments
# but NEVER eliminates moments that transcript detection found.
# ============================================================
set_stage "Stage 6/8 — Vision Enrichment"
log "=== Stage 6/8 — Vision Enrichment ==="

python3 << PYEOF
import json, base64, os, sys, time
try:
    import urllib.request
except:
    pass

OLLAMA_URL = "$OLLAMA_URL"
VISION_MODEL = "$VISION_MODEL"
TEMP_DIR = "/tmp/clipper"

# Load stream profile for context hints
stream_profile = {"dominant_type": "unknown", "is_variety": False}
try:
    with open(f"{TEMP_DIR}/stream_profile.json") as f:
        stream_profile = json.load(f)
except:
    pass

with open(f"{TEMP_DIR}/hype_moments.json") as f:
    moments = json.load(f)

# EVERY moment that survived detection WILL be rendered.
# Vision only enriches with titles/descriptions and can boost the score.
enriched = []

# Total stage timeout: 20 minutes max for all vision calls combined
VISION_STAGE_START = time.time()
VISION_STAGE_TIMEOUT = 1200  # 20 minutes
VISION_PER_MOMENT_TIMEOUT = 90  # 90 seconds per moment (includes model load time)

for moment in moments:
    T = moment["timestamp"]
    transcript_score = moment.get("score", 5)
    transcript_category = moment.get("category", "unknown")
    transcript_why = moment.get("why", "")
    segment_type = moment.get("segment_type", "unknown")

    # Carry forward clip boundaries from detection
    clip_start = moment.get("clip_start", max(0, T - 15))
    clip_end = moment.get("clip_end", T + 15)
    clip_duration = moment.get("clip_duration", 30)

    # Start with transcript data as the baseline
    entry = {
        "timestamp": T,
        "score": transcript_score,
        "category": transcript_category,
        "title": f"Clip_T{T}",
        "description": transcript_why[:100] if transcript_why else "",
        "hype_score": transcript_score,
        "transcript_category": transcript_category,
        "segment_type": segment_type,
        "vision_score": 0,
        "vision_ok": False,
        "clip_start": clip_start,
        "clip_end": clip_end,
        "clip_duration": clip_duration
    }

    # Check stage timeout before attempting vision
    elapsed_vision = time.time() - VISION_STAGE_START
    skip_vision = elapsed_vision > VISION_STAGE_TIMEOUT
    if skip_vision:
        print(f"  T={T} SKIPPING vision — stage timeout ({int(elapsed_vision)}s > {VISION_STAGE_TIMEOUT}s)", file=sys.stderr)

    # Try to get vision enrichment (title, description, visual score)
    best_vision_score = 0
    best_vision_result = None

    for frame_idx in ["03", "04"]:
        if skip_vision:
            break
        frame_path = f"{TEMP_DIR}/frames_{T}_{frame_idx}.jpg"
        if not os.path.exists(frame_path):
            continue

        with open(frame_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()

        stream_type = stream_profile.get("dominant_type", "unknown")
        context_parts = [f"This is a {stream_type} stream"]
        if segment_type != stream_type:
            context_parts.append(f"currently in a {segment_type} segment")
        if transcript_why:
            context_parts.append(f"flagged as '{transcript_category}' because: {transcript_why}")
        context_hint = ". ".join(context_parts)

        prompt = f"""Analyze this livestream frame for viral clip potential. Score 1-10.
Context: {context_hint}

Consider what makes viewers share clips: funny moments, fails, skill, reactions, drama, IRL situations.

Respond ONLY with JSON: {{"score": N, "category": "comedy/skill/reaction/controversy/emotional/irl", "title": "short viral title", "description": "one sentence"}}"""

        payload = json.dumps({
            "model": VISION_MODEL,
            "messages": [{"role": "user", "content": prompt, "images": [img_b64]}],
            "think": True,
            "stream": False,
            "options": {"temperature": 0.3, "num_predict": 800, "num_ctx": 8192}
        }).encode()

        try:
            req = urllib.request.Request(
                f"{OLLAMA_URL}/api/chat",
                data=payload,
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=VISION_PER_MOMENT_TIMEOUT) as resp:
                result = json.loads(resp.read().decode())
                msg = result.get("message", {})
                response_text = msg.get("content", "")
                thinking_text = msg.get("thinking", "")
                if thinking_text:
                    print(f"  T={T} frame={frame_idx} thinking={len(thinking_text)}chars", file=sys.stderr)

            clean = response_text.strip()
            if "\`\`\`" in clean:
                parts = clean.split("\`\`\`")
                if len(parts) >= 2:
                    clean = parts[1]
                    if clean.startswith("json"):
                        clean = clean[4:]
                    clean = clean.strip()

            start = clean.find("{")
            end = clean.rfind("}") + 1
            if start >= 0 and end > start:
                parsed = json.loads(clean[start:end])
                v_score = int(parsed.get("score", 0))
                if v_score > best_vision_score:
                    best_vision_score = v_score
                    best_vision_result = parsed
                print(f"  T={T} frame={frame_idx} vision_score={v_score}", file=sys.stderr)
                break
            else:
                print(f"  T={T} frame={frame_idx} no JSON in response: {response_text[:80]}", file=sys.stderr)

        except Exception as e:
            print(f"  T={T} vision failed: {e}", file=sys.stderr)

    # Enrich the entry with vision data (if available)
    if best_vision_result:
        entry["vision_ok"] = True
        # Normalize vision score from 1-10 to 0-1
        vision_norm = max(0.0, min((best_vision_score - 1) / 9.0, 1.0))
        entry["vision_score"] = round(vision_norm, 3)
        # Use vision title/description (usually better than generic)
        v_title = best_vision_result.get("title", "")
        if v_title and v_title != "":
            entry["title"] = v_title
        v_desc = best_vision_result.get("description", "")
        if v_desc:
            entry["description"] = v_desc
        # Blend scores: transcript is primary, vision is a bonus (never penalizes)
        # Vision >= 0.67 (was 7/10): multiply by 1.15
        # Vision >= 0.44 (was 5/10): multiply by 1.08
        # Vision < 0.44: keep transcript score unchanged
        if vision_norm >= 0.67:
            entry["score"] = round(min(transcript_score * 1.15, 1.0), 3)
            print(f"  T={T} vision BOOST: {transcript_score:.3f} -> {entry['score']:.3f}", file=sys.stderr)
        elif vision_norm >= 0.44:
            entry["score"] = round(min(transcript_score * 1.08, 1.0), 3)
        # else: keep transcript_score as-is
    else:
        # Vision failed — that's OK, use transcript data as-is
        print(f"  T={T} vision failed/no-parse — using transcript score={transcript_score:.3f}", file=sys.stderr)

    enriched.append(entry)
    print(f"  T={T} FINAL score={entry['score']:.3f} dur={entry['clip_duration']}s title=\"{entry['title']}\" [{entry['category']}]", file=sys.stderr)

# NO FILTERING HERE — every moment goes to rendering.
# Sort by score descending for rendering priority.
enriched.sort(key=lambda x: x["score"], reverse=True)

with open(f"{TEMP_DIR}/scored_moments.json", "w") as f:
    json.dump(enriched, f, indent=2)

vision_ok_count = sum(1 for e in enriched if e.get("vision_ok"))
print(f"\\nEnriched {len(enriched)} moments ({vision_ok_count} with vision data). ALL will be rendered.")
for s in enriched:
    v_tag = "V" if s.get("vision_ok") else "T"
    print(f"  [{v_tag}] T={s['timestamp']} score={s['score']:.3f} dur={s.get('clip_duration',30)}s [{s['category']}] ({s.get('segment_type','')}) — {s['title']}")
PYEOF

SCORED_COUNT=$(python3 -c "import json; m=json.load(open('/tmp/clipper/scored_moments.json')); print(len(m))")
log "Moments to render: $SCORED_COUNT (all detected moments proceed to rendering)"

if [ "$SCORED_COUNT" -eq 0 ]; then
    warn "No moments to render (detection found nothing)."
    echo "$VOD_BASENAME	$(date -u +%Y-%m-%dT%H:%M:%SZ)	no_moments	$CLIP_STYLE" >> "$PROCESSED_LOG"
    echo '{"status":"no_moments","clips":0,"style":"'"$CLIP_STYLE"'"}'
    exit 0
fi

# ============================================================
# STAGE 7 — Editing and Export
# ============================================================
set_stage "Stage 7/8 — Editing and Export"
log "=== Stage 7/8 — Editing and Export ==="

# Free VRAM: unload vision model before Whisper needs the GPU for caption transcription
unload_ollama "$VISION_MODEL"

CLIPS_MADE=0
CLIP_FILES=()

# --- 7a. Generate clip manifest (now includes clip boundaries) ---
log "  Generating clip manifest..."
python3 -c "
import json
moments = json.load(open('/tmp/clipper/scored_moments.json'))
for m in moments:
    title = m['title'].replace(' ', '_').replace('/', '-').replace('\"', '')
    title = ''.join(c for c in title if c.isalnum() or c in '-_')[:50]
    if not title:
        title = f'Clip_T{m[\"timestamp\"]}'
    clip_start = m.get('clip_start', max(0, m['timestamp'] - 15))
    clip_end = m.get('clip_end', m['timestamp'] + 15)
    clip_duration = m.get('clip_duration', 30)
    score_str = f\"{m['score']:.3f}\" if isinstance(m['score'], float) else str(m['score'])
    print(f\"{m['timestamp']}|{title}|{score_str}|{m.get('category','unknown')}|{m.get('description','')}|{m.get('segment_type','unknown')}|{clip_start}|{clip_duration}\")
" > "$TEMP_DIR/clip_manifest.txt"

MANIFEST_COUNT=$(wc -l < "$TEMP_DIR/clip_manifest.txt")
log "  Manifest: $MANIFEST_COUNT clips to process"

# --- 7b. Extract ALL clip audio segments (FFmpeg only, no GPU models) ---
# Now uses variable clip duration from manifest (fields 7=clip_start, 8=clip_duration)
log "  Extracting audio for all clips..."
while IFS='|' read -r T TITLE SCORE CATEGORY DESC SEG_TYPE CLIP_START_SEC CLIP_DUR; do
    [ -z "$T" ] && continue
    # Use manifest clip boundaries, fallback to legacy fixed window
    if [ -n "$CLIP_START_SEC" ] && [ -n "$CLIP_DUR" ]; then
        CLIP_START="$CLIP_START_SEC"
        CLIP_LENGTH="$CLIP_DUR"
    else
        CLIP_START=$((T - 22))
        CLIP_LENGTH=45
    fi
    [ "$CLIP_START" -lt 0 ] && CLIP_START=0
    CLIP_AUDIO="$TEMP_DIR/clip_audio_${T}.wav"

    ffmpeg -nostdin -y -ss "$CLIP_START" -t "$CLIP_LENGTH" -i "$VOD_PATH" \
        -vn -acodec pcm_s16le -ar 16000 -ac 1 \
        "$CLIP_AUDIO" 2>/dev/null || warn "Audio extraction failed for T=$T"
done < "$TEMP_DIR/clip_manifest.txt"

# --- 7c. Batch transcribe ALL clips with Whisper (ONE model load) ---
log "  Batch transcribing all clips (single Whisper load)..."
export CLIP_WHISPER_MODEL="$WHISPER_MODEL"
python3 << 'PYTRANSCRIBE'
import json, sys, os, glob

from faster_whisper import WhisperModel

cache_dir = "/root/.cache/whisper-models"
temp_dir = "/tmp/clipper"
whisper_model = os.environ.get("CLIP_WHISPER_MODEL", "large-v3")

# Load Whisper ONCE for all clips
try:
    model = WhisperModel(whisper_model, device="cuda", compute_type="float16", download_root=cache_dir)
    print(f"[WHISPER] Batch caption mode: GPU (float16) with {whisper_model}", file=sys.stderr)
except Exception as e:
    print(f"[WHISPER] GPU failed ({e}), using CPU", file=sys.stderr)
    model = WhisperModel(whisper_model, device="cpu", compute_type="int8", download_root=cache_dir)

# Find all clip audio files
audio_files = sorted(glob.glob(f"{temp_dir}/clip_audio_*.wav"))
print(f"[WHISPER] Transcribing {len(audio_files)} clip segments...", file=sys.stderr)

for audio_path in audio_files:
    # Extract timestamp from filename: clip_audio_1234.wav -> 1234
    basename = os.path.basename(audio_path)
    ts = basename.replace("clip_audio_", "").replace(".wav", "")
    srt_path = f"{temp_dir}/clip_{ts}.srt"

    try:
        segments, info = model.transcribe(audio_path, beam_size=5, word_timestamps=True)

        srt_lines = []
        idx = 1
        for seg in segments:
            start_h, start_r = divmod(seg.start, 3600)
            start_m, start_s = divmod(start_r, 60)
            end_h, end_r = divmod(seg.end, 3600)
            end_m, end_s = divmod(end_r, 60)
            srt_lines.append(f"{idx}")
            srt_lines.append(
                f"{int(start_h):02d}:{int(start_m):02d}:{start_s:06.3f}".replace(".", ",") +
                " --> " +
                f"{int(end_h):02d}:{int(end_m):02d}:{end_s:06.3f}".replace(".", ",")
            )
            srt_lines.append(seg.text.strip())
            srt_lines.append("")
            idx += 1

        with open(srt_path, "w") as f:
            f.write("\n".join(srt_lines))
        print(f"  T={ts}: {idx-1} SRT segments", file=sys.stderr)
    except Exception as e:
        print(f"  T={ts}: transcription failed: {e}", file=sys.stderr)
        # Write empty SRT so rendering can fall back to no-subtitle mode
        with open(srt_path, "w") as f:
            f.write("")

print("[WHISPER] Batch transcription complete.", file=sys.stderr)
PYTRANSCRIBE

# --- 7d. Render ALL clips (FFmpeg only, Whisper already unloaded) ---
# Uses blur-fill technique: full 16:9 frame centered on 9:16 canvas with
# a blurred, zoomed copy of the same frame filling the background.
# This preserves all stream content instead of hard-cropping the sides.
log "  Rendering all clips (blur-fill 9:16)..."

# Blur-fill filter chain:
#   split into two streams → background gets scaled to fill 1080x1920,
#   cropped, and heavily blurred → foreground scales to fit width (1080)
#   while keeping aspect ratio → overlay foreground centered on blurred bg
BLUR_BG="split[bg][fg];[bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=25:5[blurred];[fg]scale=1080:-2:force_original_aspect_ratio=decrease[sharp];[blurred][sharp]overlay=(W-w)/2:(H-h)/2"

while IFS='|' read -r T TITLE SCORE CATEGORY DESC SEG_TYPE CLIP_START_SEC CLIP_DUR; do
    [ -z "$T" ] && continue

    # Use manifest clip boundaries, fallback to legacy fixed window
    if [ -n "$CLIP_START_SEC" ] && [ -n "$CLIP_DUR" ]; then
        CLIP_START="$CLIP_START_SEC"
        CLIP_LENGTH="$CLIP_DUR"
    else
        CLIP_START=$((T - 22))
        CLIP_LENGTH=45
    fi
    [ "$CLIP_START" -lt 0 ] && CLIP_START=0

    log "  Rendering: $TITLE (T=${T}s, dur=${CLIP_LENGTH}s, score=$SCORE, category=$CATEGORY, segment=$SEG_TYPE)"

    CLIP_SRT="$TEMP_DIR/clip_${T}.srt"
    CLIP_OUTPUT="$CLIPS_DIR/${TITLE}.mp4"

    # Render vertical clip: blur-fill background + burned captions (variable duration)
    ffmpeg -nostdin -y -ss "$CLIP_START" -t "$CLIP_LENGTH" -i "$VOD_PATH" \
        -vf "${BLUR_BG},subtitles='${CLIP_SRT}':force_style='FontSize=16,Bold=1,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=2,Alignment=2,MarginV=40'" \
        -c:v libx264 -crf 23 -preset medium \
        -c:a aac -b:a 128k \
        -movflags +faststart \
        "$CLIP_OUTPUT" 2>/dev/null

    if [ ! -f "$CLIP_OUTPUT" ]; then
        warn "Render failed for $TITLE. Trying without subtitles..."
        ffmpeg -nostdin -y -ss "$CLIP_START" -t "$CLIP_LENGTH" -i "$VOD_PATH" \
            -vf "${BLUR_BG}" \
            -c:v libx264 -crf 23 -preset medium \
            -c:a aac -b:a 128k \
            -movflags +faststart \
            "$CLIP_OUTPUT" 2>/dev/null || { warn "Render completely failed for T=$T"; continue; }
    fi

    if [ -f "$CLIP_OUTPUT" ]; then
        FINAL_SIZE=$(stat -c%s "$CLIP_OUTPUT" 2>/dev/null || echo 0)
        FINAL_MB=$((FINAL_SIZE / 1048576))
        log "  Done: $TITLE — ${FINAL_MB}MB (score: $SCORE, category: $CATEGORY, segment: $SEG_TYPE)"

        echo "${TITLE}|${SCORE}|${CATEGORY}|${DESC}|${FINAL_MB}MB|${SEG_TYPE}|${CLIP_LENGTH}s" >> "$TEMP_DIR/clips_made.txt"
    fi
done < "$TEMP_DIR/clip_manifest.txt"

# ============================================================
# STAGE 8 — Logging and Summary
# ============================================================
set_stage "Stage 8/8 — Summary"
log "=== Stage 8/8 — Summary ==="

TOTAL_CLIPS=0
if [ -f "$TEMP_DIR/clips_made.txt" ]; then
    TOTAL_CLIPS=$(wc -l < "$TEMP_DIR/clips_made.txt")
fi

echo -e "$VOD_BASENAME\t$(date -u +%Y-%m-%dT%H:%M:%SZ)\t${TOTAL_CLIPS}_clips\t${CLIP_STYLE}" >> "$PROCESSED_LOG"

python3 << 'PYEOF'
import json, os

clips_file = "/tmp/clipper/clips_made.txt"
clips = []
if os.path.exists(clips_file):
    with open(clips_file) as f:
        for line in f:
            parts = line.strip().split("|")
            if len(parts) >= 4:
                try:
                    score_val = float(parts[1])
                except (ValueError, IndexError):
                    score_val = 0.0
                clips.append({
                    "title": parts[0],
                    "score": round(score_val, 3),
                    "category": parts[2],
                    "description": parts[3],
                    "size": parts[4] if len(parts) > 4 else "?",
                    "segment_type": parts[5] if len(parts) > 5 else "?",
                    "duration": parts[6] if len(parts) > 6 else "30s"
                })

cats = {}
segs = {}
for c in clips:
    cat = c.get("category", "unknown")
    cats[cat] = cats.get(cat, 0) + 1
    seg = c.get("segment_type", "unknown")
    segs[seg] = segs.get(seg, 0) + 1

# Load segment map for timeline info
seg_map = []
try:
    with open("/tmp/clipper/segments.json") as f:
        seg_map = json.load(f)
except:
    pass

summary = {
    "status": "complete",
    "clips": len(clips),
    "category_breakdown": cats,
    "segment_breakdown": segs,
    "stream_segments": [{"type": s["type"], "duration_min": round((s["end"]-s["start"])/60)} for s in seg_map],
    "details": clips
}

with open("/tmp/clipper/summary.json", "w") as f:
    json.dump(summary, f, indent=2)

print(json.dumps(summary, indent=2))
PYEOF

log "Pipeline complete! ${TOTAL_CLIPS} clip(s) saved to $CLIPS_DIR (style: $CLIP_STYLE)"
