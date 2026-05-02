#!/usr/bin/env bash
# Stage 7 — Editing & Export (framing + originality + stitch render + batch captions)
#
# Sourced by scripts/clip-pipeline.sh as part of Phase B. Inherits globals
# (TEMP_DIR, VOD_PATH, CLIP_STYLE, etc.) and the cleanup EXIT trap from the
# orchestrator. Extracted byte-for-byte — only the file boundary changed.

set_stage "Stage 7/8 — Editing and Export"
log "=== Stage 7/8 — Editing and Export ==="

# Free VRAM: unload vision model before Whisper needs the GPU for caption transcription
unload_model "$VISION_MODEL_STAGE6"

CLIPS_MADE=0
CLIP_FILES=()

# --- 7a. Generate clip manifest (now includes clip boundaries) ---
log "  Generating clip manifest..."
python3 -c "
import json
moments = json.load(open('/tmp/clipper/scored_moments.json'))
def _scrub_field(s, allow_unicode=True):
    # Manifest is pipe-delimited and read line-by-line by bash. Anything that
    # contains a literal pipe, newline, or carriage return splits the field
    # boundary and corrupts the record — the trailing fields land in the next
    # iteration's variables and the renderer sees mangled metadata. Replace
    # them defensively here so a chatty LLM description doesn't break Stage 7.
    if not isinstance(s, str):
        s = str(s or '')
    return s.replace('|', '-').replace('\r', ' ').replace('\n', ' ').strip()
for m in moments:
    title = m['title'].replace('/', '-').replace('\\\\', '-').replace('|', '-').replace('\"', '')
    title = ''.join(c for c in title if c.isalnum() or c in ' -')[:50].strip()
    if not title:
        title = f'Clip T{m[\"timestamp\"]}'
    clip_start = m.get('clip_start', max(0, m['timestamp'] - 15))
    clip_end = m.get('clip_end', m['timestamp'] + 15)
    clip_duration = m.get('clip_duration', 30)
    score_str = f\"{m['score']:.3f}\" if isinstance(m['score'], float) else str(m['score'])
    description = _scrub_field(m.get('description', ''))[:500]
    # Hook only renders when vision (or another stage) explicitly produced one.
    # We deliberately do NOT fall back to the title here — that path used to
    # leak baseline titles like "ClipT1805" into the burned-in hook caption.
    # Empty hook → bash's `[ -n "$HOOK" ]` is false → no overlay rendered.
    hook = _scrub_field(m.get('hook', ''))
    category = _scrub_field(m.get('category', 'unknown'))
    segment_type = _scrub_field(m.get('segment_type', 'unknown'))
    print(f\"{m['timestamp']}|{title}|{score_str}|{category}|{description}|{hook}|{segment_type}|{clip_start}|{clip_duration}\")
" > "$TEMP_DIR/clip_manifest.txt"

MANIFEST_COUNT=$(wc -l < "$TEMP_DIR/clip_manifest.txt")
log "  Manifest: $MANIFEST_COUNT clips to process"

# --- 7b. Extract ALL clip audio segments (FFmpeg only, no GPU models) ---
# Now uses variable clip duration from manifest (fields 7=clip_start, 8=clip_duration)
log "  Extracting audio for all clips..."
while IFS='|' read -r T TITLE SCORE CATEGORY DESC HOOK SEG_TYPE CLIP_START_SEC CLIP_DUR; do
    [ -z "$T" ] && continue
    # Use manifest clip boundaries, fallback to legacy fixed window
    if [ -n "$CLIP_START_SEC" ] && [ -n "$CLIP_DUR" ]; then
        CLIP_START="$CLIP_START_SEC"
        CLIP_LENGTH="$CLIP_DUR"
    else
        CLIP_START=$((T - 22))
        CLIP_LENGTH=45
    fi
    CLIP_START=$(awk "BEGIN{v=$CLIP_START; print (v<0)?0:v}")
    CLIP_AUDIO="$TEMP_DIR/clip_audio_${T}.wav"

    ffmpeg -nostdin -y -ss "$CLIP_START" -t "$CLIP_LENGTH" -i "$VOD_PATH" \
        -vn -acodec pcm_s16le -ar 16000 -ac 1 \
        "$CLIP_AUDIO" 2>/dev/null || warn "Audio extraction failed for T=$T"
done < "$TEMP_DIR/clip_manifest.txt"

# --- 7c. Batch transcribe ALL clips with Whisper (ONE model load) ---
log "  Batch transcribing all clips (single Whisper load)..."
export CLIP_WHISPER_MODEL="$WHISPER_MODEL"
python3 /root/scripts/lib/stages/stage7_transcribe.py

# --- 7d. Render ALL clips (FFmpeg only, Whisper already unloaded) ---
# Originality-aware renderer: per-clip randomized blur/color/mirror/palette
# variation, two framing modes (blur_fill / camera_pan), stitch-group
# concat, optional Piper voiceover mix, optional music-bed mix.
# See scripts/lib/originality.py for per-clip parameter logic.
log "  Rendering all clips (framing=${CLIP_FRAMING}, originality=${CLIP_ORIGINALITY})..."
log "  Settings: speed=${CLIP_SPEED} captions=${CAPTIONS_ENABLED} hook=${HOOK_CAPTION_ENABLED} tts=${CLIP_TTS_VO} music=$( [ -n "$CLIP_MUSIC_BED" ] && echo on || echo off )"

# Speed filter prefix: shared across framing modes. `null` is FFmpeg's no-op.
if [ "$CLIP_SPEED" != "1.0" ]; then
    _SPEED_VF="setpts=PTS/${CLIP_SPEED}"
else
    _SPEED_VF="null"
fi

# Speed-aware audio filter (rubberband so pitch tracks tempo naturally).
if [ "$CLIP_SPEED" != "1.0" ]; then
    SPEED_AUDIO_FILTER="rubberband=tempo=${CLIP_SPEED}:pitch=${CLIP_SPEED}"
    log "  Audio filter: ${SPEED_AUDIO_FILTER}"
else
    SPEED_AUDIO_FILTER=""
fi

# --- Legacy blur-fill string used as fallback when every other path fails. ---
LEGACY_BG="split[bg][fg];[bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=25:5[blurred];[fg]scale=1080:-2:force_original_aspect_ratio=decrease[sharp];[blurred][sharp]overlay=(W-w)/2:(H-h)/2"

while IFS='|' read -r T TITLE SCORE CATEGORY DESC HOOK SEG_TYPE CLIP_START_SEC CLIP_DUR; do
    [ -z "$T" ] && continue

    if [ -n "$CLIP_START_SEC" ] && [ -n "$CLIP_DUR" ]; then
        CLIP_START="$CLIP_START_SEC"
        CLIP_LENGTH="$CLIP_DUR"
    else
        CLIP_START=$((T - 22))
        CLIP_LENGTH=45
    fi
    CLIP_START=$(awk "BEGIN{v=$CLIP_START; print (v<0)?0:v}")

    # --- Pull per-moment JSON fields (vision enrichment adds mirror_safe,
    # chrome_regions, voiceover, group_id / group_kind). Missing fields
    # produce safe defaults so the render still works pre-wave-B.
    MOMENT_META=$(CLIP_T="$T" python3 /root/scripts/lib/stages/stage7_meta.py)
    MIRROR_SAFE=$(echo "$MOMENT_META" | cut -d'|' -f1)
    VO_LINE=$(echo "$MOMENT_META" | cut -d'|' -f2)
    VO_PLACEMENT=$(echo "$MOMENT_META" | cut -d'|' -f3)
    GROUP_ID=$(echo "$MOMENT_META" | cut -d'|' -f4)
    KIND=$(echo "$MOMENT_META" | cut -d'|' -f5)
    : "${MIRROR_SAFE:=false}"
    : "${KIND:=solo}"

    # Stitch groups render through a dedicated code path (see 7e below).
    # Narrative/solo render inline here.
    if [ "$KIND" = "stitch" ] && [ "$CLIP_STITCH" = "true" ]; then
        # Defer — group rendered once in stage 7e; skip the solo render.
        log "  Deferring stitch group member T=${T} (group=${GROUP_ID})"
        continue
    fi

    # Generate per-clip randomized render params (deterministic from T).
    eval "$(python3 "$LIB_DIR/originality.py" "$T" "$CLIP_ORIGINALITY" "$MIRROR_SAFE" "$CLIP_FRAMING" "$CATEGORY")"

    log "  Rendering: $TITLE (T=${T}s, dur=${CLIP_LENGTH}s, framing=$CLIP_FRAMING, mirror=$MIRROR, score=$SCORE)"

    CLIP_SRT="$TEMP_DIR/clip_${T}.srt"
    CLIP_OUTPUT="$CLIPS_DIR/${TITLE}.mp4"

    if [ "$CLIP_SPEED" != "1.0" ]; then
        CLIP_SRT_RENDER="$TEMP_DIR/clip_${T}_scaled.srt"
        rescale_srt "$CLIP_SRT" "$CLIP_SRT_RENDER" "$CLIP_SPEED"
    else
        CLIP_SRT_RENDER="$CLIP_SRT"
    fi

    # --- Mirror + color + motion filter fragments shared by all framings. ---
    MIRROR_VF=""
    [ "$MIRROR" = "true" ] && MIRROR_VF=",hflip"

    COLOR_VF="eq=brightness=${EQ_BRIGHTNESS}:saturation=${EQ_SATURATION}:contrast=${EQ_CONTRAST}:gamma=${EQ_GAMMA},hue=h=${HUE_SHIFT}"
    [ "$USE_VIGNETTE" = "true" ] && COLOR_VF="${COLOR_VF},vignette=angle=PI/5"
    # A tiny pseudo-random shake on odd clips: offsets crop by sin()+cos() of time.
    # Adds per-frame motion noise without looking like a visible shake.
    if [ "$USE_SHAKE" = "true" ]; then
        SHAKE_VF=",crop=iw-${SHAKE_AMP}*2:ih-${SHAKE_AMP}*2:${SHAKE_AMP}+${SHAKE_AMP}*sin(t*2):${SHAKE_AMP}+${SHAKE_AMP}*cos(t*1.5)"
    else
        SHAKE_VF=""
    fi

    # --- Pick framing base chain. ---
    # Only two modes are supported:
    #   - blur_fill  — default. Full 16:9 foreground over a blurred-fill
    #     background. Nothing is cropped out of the original.
    #   - camera_pan — uses the Stage 6.5 face-track path. Falls back to
    #     blur_fill per clip when no path exists.
    # smart_crop and centered_square were removed (vision-sourced chrome
    # bboxes were too unreliable; the centered_square variant offered no
    # fingerprint gain over blur_fill + randomized params).
    BLUR_FILL_VF="${_SPEED_VF},split[bg][fg];[bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=${BLUR_RADIUS}:${BLUR_PASSES}[blurred];[fg]scale=1080:-2:force_original_aspect_ratio=decrease${MIRROR_VF}[sharp];[blurred][sharp]overlay=(W-w)/2:(H-h)/2,${COLOR_VF}${SHAKE_VF}"

    case "$CLIP_FRAMING" in
        camera_pan)
            PAN_PATH="$TEMP_DIR/clip_${T}_campath.json"
            if [ "$CLIP_CAMERA_PAN" = "true" ] && [ -f "$PAN_PATH" ]; then
                PAN_EXPR=$(python3 "$LIB_DIR/face_pan.py" --emit-filter "$PAN_PATH" 2>/dev/null)
                if [ -n "$PAN_EXPR" ]; then
                    FRAME_VF="${_SPEED_VF},${PAN_EXPR}${MIRROR_VF},${COLOR_VF}${SHAKE_VF}"
                    log "    camera_pan: using computed face track"
                else
                    FRAME_VF="$BLUR_FILL_VF"
                    log "    camera_pan: face_pan.py returned empty — falling back to blur_fill"
                fi
            else
                FRAME_VF="$BLUR_FILL_VF"
                log "    camera_pan: no precomputed path — falling back to blur_fill"
            fi
            ;;
        blur_fill|*)
            # Catches any legacy config value (smart_crop / centered_square)
            # and maps it to blur_fill silently.
            FRAME_VF="$BLUR_FILL_VF"
            ;;
    esac

    RENDER_VF="$FRAME_VF"

    # Hook overlay (per-clip randomized palette + position).
    if [ "$HOOK_CAPTION_ENABLED" = "true" ] && [ -n "$HOOK" ]; then
        HOOK_FILE="$TEMP_DIR/clip_${T}_hook.txt"
        HOOK_TEXT="$HOOK" python3 -c "
import os, textwrap
hook = os.environ.get('HOOK_TEXT', '').strip()
lines = textwrap.wrap(hook, 22)[:3]
print('\n'.join(lines) if lines else hook[:60])
" > "$HOOK_FILE"
        RENDER_VF="${RENDER_VF},drawtext=textfile='${HOOK_FILE}':fontsize=${HOOK_FONTSIZE}:fontcolor=${HOOK_FG_COLOR}:fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:box=1:boxcolor=${HOOK_BOX_COLOR}:boxborderw=${HOOK_BOX_BORDER}:x=(w-text_w)/2:y=${HOOK_Y}:line_spacing=8"
    fi

    if [ "$CAPTIONS_ENABLED" != "false" ]; then
        RENDER_VF="${RENDER_VF},subtitles='${CLIP_SRT_RENDER}':force_style='FontSize=${SUB_FONTSIZE},Bold=1,PrimaryColour=${SUB_PRIMARY},OutlineColour=${SUB_OUTLINE_COL},Outline=${SUB_OUTLINE},Alignment=2,MarginV=${SUB_MARGIN_V}'"
    fi

    # --- Voiceover + music-bed audio layers ---
    VO_WAV=""
    MUSIC_WAV=""
    if [ "$CLIP_TTS_VO" = "true" ] && [ -n "$VO_LINE" ]; then
        VO_WAV="$TEMP_DIR/clip_${T}_vo.wav"
        python3 "$LIB_DIR/piper_vo.py" \
            --text "$VO_LINE" \
            --out "$VO_WAV" \
            --placement "$VO_PLACEMENT" \
            --clip-duration "$CLIP_LENGTH" \
            --speed "$CLIP_SPEED" \
            --tone "$CATEGORY" 2>&1 | while IFS= read -r line; do info "    [TTS] $line"; done
        [ ! -f "$VO_WAV" ] && VO_WAV=""
    fi
    if [ -n "$CLIP_MUSIC_BED" ] && [ -d "$CLIP_MUSIC_BED" ]; then
        MUSIC_WAV=$(python3 "$LIB_DIR/music_pick.py" \
            --library "$CLIP_MUSIC_BED" \
            --category "$CATEGORY" \
            --segment "$SEG_TYPE" \
            --duration "$CLIP_LENGTH" \
            --tier-c "$CLIP_MUSIC_TIER_C" \
            --seed "$T" 2>/dev/null || true)
        [ -n "$MUSIC_WAV" ] && log "    music-bed: $(basename "$MUSIC_WAV")"
    fi

    render_ok=false
    if [ -n "$VO_WAV" ] || [ -n "$MUSIC_WAV" ]; then
        # Build a mix filter graph. Key details:
        #   - amix defaults to normalize=1, which divides every input by the
        #     number of sources and produces painfully quiet clips. We set
        #     normalize=0 so per-input volumes control the final mix directly.
        #   - Source stays at full volume to match the pre-TTS behavior (the
        #     user reported the old simple path was correctly loud). No
        #     automatic duck; VO gets a gain boost instead so it rides over.
        #   - Music bed sits at -22 dB. Only added when MUSIC_WAV is set.
        MIX_ARGS=(-i "$VOD_PATH")
        MIX_INDEX=1
        SRC_AUDIO_FILTER="${SPEED_AUDIO_FILTER:+${SPEED_AUDIO_FILTER},}volume=1.0"
        AUDIO_DEFS="[0:a]${SRC_AUDIO_FILTER}[src_audio]"
        MIX_INS="[src_audio]"
        if [ -n "$VO_WAV" ]; then
            MIX_ARGS+=(-i "$VO_WAV")
            # VO gain tuned so it's clearly audible but doesn't clip when
            # layered on loud source audio. apad keeps the track at clip
            # length so amix doesn't finish early.
            AUDIO_DEFS="${AUDIO_DEFS};[${MIX_INDEX}:a]volume=1.6,apad=whole_dur=${CLIP_LENGTH}[vo_audio]"
            MIX_INS="${MIX_INS}[vo_audio]"
            MIX_INDEX=$((MIX_INDEX+1))
        fi
        if [ -n "$MUSIC_WAV" ]; then
            MIX_ARGS+=(-stream_loop -1 -i "$MUSIC_WAV")
            AUDIO_DEFS="${AUDIO_DEFS};[${MIX_INDEX}:a]atrim=0:${CLIP_LENGTH},volume=0.08[music_audio]"
            MIX_INS="${MIX_INS}[music_audio]"
            MIX_INDEX=$((MIX_INDEX+1))
        fi
        # normalize=0 keeps the source at 1.0 regardless of how many layers are
        # stacked on top. A final `volume=0.85` gives a small headroom cushion
        # to avoid inter-sample peaks when VO + music overlap with loud source.
        FILTER_COMPLEX="[0:v]${RENDER_VF}[vout];${AUDIO_DEFS};${MIX_INS}amix=inputs=${MIX_INDEX}:duration=first:dropout_transition=0:normalize=0[amixed];[amixed]volume=0.95[aout]"

        if ffmpeg -nostdin -y -ss "$CLIP_START" -t "$CLIP_LENGTH" "${MIX_ARGS[@]}" \
            -filter_complex "$FILTER_COMPLEX" \
            -map "[vout]" -map "[aout]" \
            -c:v libx264 -crf 20 -preset slow -profile:v high -level 4.2 -pix_fmt yuv420p -r 30 \
            -b:v 18M -maxrate 20M -bufsize 40M \
            -c:a aac -b:a 192k \
            -movflags +faststart \
            "$CLIP_OUTPUT" 2>/dev/null; then
            render_ok=true
        fi
    else
        AUDIO_FLAG=()
        [ -n "$SPEED_AUDIO_FILTER" ] && AUDIO_FLAG=(-af "$SPEED_AUDIO_FILTER")
        if ffmpeg -nostdin -y -ss "$CLIP_START" -t "$CLIP_LENGTH" -i "$VOD_PATH" \
            -vf "$RENDER_VF" \
            "${AUDIO_FLAG[@]}" \
            -c:v libx264 -crf 20 -preset slow -profile:v high -level 4.2 -pix_fmt yuv420p -r 30 \
            -b:v 18M -maxrate 20M -bufsize 40M \
            -c:a aac -b:a 192k \
            -movflags +faststart \
            "$CLIP_OUTPUT" 2>/dev/null; then
            render_ok=true
        fi
    fi

    # Fallback ladder: try without subtitles, then legacy blur-fill at standard quality.
    if [ "$render_ok" != "true" ]; then
        warn "Render failed for $TITLE. Retrying without originality layers..."
        AUDIO_FLAG=()
        [ -n "$SPEED_AUDIO_FILTER" ] && AUDIO_FLAG=(-af "$SPEED_AUDIO_FILTER")
        ffmpeg -nostdin -y -ss "$CLIP_START" -t "$CLIP_LENGTH" -i "$VOD_PATH" \
            -vf "${_SPEED_VF},${LEGACY_BG}" \
            "${AUDIO_FLAG[@]}" \
            -c:v libx264 -crf 23 -preset medium \
            -c:a aac -b:a 128k \
            -movflags +faststart \
            "$CLIP_OUTPUT" 2>/dev/null || { warn "Render completely failed for T=$T"; continue; }
    fi

    if [ -f "$CLIP_OUTPUT" ]; then
        FINAL_SIZE=$(stat -c%s "$CLIP_OUTPUT" 2>/dev/null || echo 0)
        FINAL_MB=$((FINAL_SIZE / 1048576))
        log "  Done: $TITLE — ${FINAL_MB}MB (framing=$CLIP_FRAMING mirror=$MIRROR category=$CATEGORY segment=$SEG_TYPE)"
        echo "${TITLE}|${SCORE}|${CATEGORY}|${DESC}|${FINAL_MB}MB|${SEG_TYPE}|${CLIP_LENGTH}s" >> "$TEMP_DIR/clips_made.txt"
    fi
done < "$TEMP_DIR/clip_manifest.txt"

# --- 7e. Render stitch groups (wave C) ---
# A stitch group is 2-4 sub-moments concatenated into one post. Each member
# is rendered through the same framing pipeline as solo clips (with its own
# per-segment mirror/color variation), then xfade-concatenated.
if [ "$CLIP_STITCH" = "true" ] && [ -f "$TEMP_DIR/moment_groups.json" ]; then
    # moment_groups.json shape: {"groups": [...], "moments": [...], "summary": {...}}
    # iterate the "groups" list, not the top-level dict keys.
    STITCH_COUNT=$(python3 -c "import json; d=json.load(open('$TEMP_DIR/moment_groups.json')); g=d.get('groups',[]) if isinstance(d,dict) else d; print(sum(1 for x in g if isinstance(x,dict) and x.get('kind')=='stitch'))" 2>/dev/null || echo 0)
    STITCH_COUNT="${STITCH_COUNT:-0}"
    if [ "$STITCH_COUNT" -gt 0 ]; then
        log "  Rendering $STITCH_COUNT stitch group(s)..."
        # Stitch render is OPTIONAL post-processing — its failure must not
        # abort Stage 7 / Stage 8. With `set -o pipefail` active, a non-zero
        # exit from stitch_render would bubble up through the pipe and
        # terminate the whole run via `set -e`, throwing away every solo
        # clip we already rendered. Wrap the whole pipeline in a
        # subshell + `|| warn` so the failure is logged and we continue.
        if ! (
            CLIPS_DIR_ENV="$CLIPS_DIR" TEMP_DIR_ENV="$TEMP_DIR" VOD_PATH_ENV="$VOD_PATH" \
                CLIP_FRAMING_ENV="$CLIP_FRAMING" CLIP_ORIGINALITY_ENV="$CLIP_ORIGINALITY" \
                CLIP_SPEED_ENV="$CLIP_SPEED" CLIP_CAPTIONS_ENV="$CAPTIONS_ENABLED" \
                CLIP_HOOK_ENV="$HOOK_CAPTION_ENABLED" \
                python3 "$LIB_DIR/stitch_render.py" 2>&1 | while IFS= read -r line; do log "    [STITCH] $line"; done
        ); then
            warn "Stitch render exited non-zero; solo clips remain valid, continuing"
        fi
    fi
fi

# ============================================================
