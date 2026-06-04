#!/usr/bin/env bash
# Stage 1 — Discovery + chat fetch (Phase 2)
#
# Sourced by scripts/clip-pipeline.sh as part of Phase B. Inherits globals
# (TEMP_DIR, VOD_PATH, CLIP_STYLE, etc.) and the cleanup EXIT trap from the
# orchestrator. Extracted byte-for-byte — only the file boundary changed.

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
# STAGE 1b — Chat discovery (Phase 2)
# ============================================================
# If vods/.chat/<basename>.jsonl exists we use it directly. Otherwise, if
# config/chat.json opts in (auto_fetch.enabled=true) AND the VOD filename
# matches vod_id_pattern, we try to fetch via the unofficial Twitch
# GraphQL /comments endpoint. Failure at any step collapses gracefully:
# downstream stages key off /tmp/clipper/chat_available.txt and degrade
# to the pre-Phase-2 behavior when it's "false".
CHAT_DIR="${VODS_DIR}/.chat"
mkdir -p "$CHAT_DIR"
VOD_STEM=$(echo "$VOD_BASENAME" | sed 's/\.[^.]*$//')
CHAT_FILE="$CHAT_DIR/${VOD_STEM}.jsonl"
CHAT_AVAILABLE="false"

if [ -f "$CHAT_FILE" ] && [ -s "$CHAT_FILE" ]; then
    log "Chat data found: $CHAT_FILE ($(wc -l < "$CHAT_FILE" | tr -d ' ') records)"
    CHAT_AVAILABLE="true"
else
    log "No local chat file at $CHAT_FILE — checking auto-fetch config"
    # Read auto_fetch config (graceful if missing)
    FETCH_CMD=$(python3 /root/scripts/lib/stages/stage1_fetch.py "$VOD_BASENAME" "$CHAT_FILE")
    if [[ "$FETCH_CMD" == FETCH* ]]; then
        read -r _kw VID CID DELAY <<<"$FETCH_CMD"
        log "Chat auto-fetch: Twitch VOD ID $VID via GraphQL (delay ${DELAY}ms between pages)"
        if python3 "$LIB_DIR/chat_fetch.py" fetch \
            --vod-id "$VID" --out "$CHAT_FILE" --client-id "$CID" --delay-ms "$DELAY" 2>&1 \
            | tee -a "$PIPELINE_LOG" ; then
            if [ -s "$CHAT_FILE" ]; then
                CHAT_AVAILABLE="true"
                log "Chat auto-fetch succeeded: $(wc -l < "$CHAT_FILE" | tr -d ' ') records"
            else
                warn "Chat auto-fetch returned 0 records; VOD may be too old or private"
            fi
        else
            warn "Chat auto-fetch failed; pipeline will proceed without chat data"
        fi
    else
        log "Chat auto-fetch: $FETCH_CMD"
    fi
fi

echo "$CHAT_AVAILABLE" > "$TEMP_DIR/chat_available.txt"
echo "$CHAT_FILE" > "$TEMP_DIR/chat_path.txt"

