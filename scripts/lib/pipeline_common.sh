#!/usr/bin/env bash
# Pipeline common helpers — sourced by scripts/clip-pipeline.sh.
#
# Provides:
#   color env vars (RED/GREEN/YELLOW/CYAN/NC)
#   log/warn/err/info — colored stderr output
#   set_stage <text>  — write to $STAGE_FILE + append to pipeline_stages.log
#   unload_model <model>      — POST /api/v1/models/unload
#   load_model <model> [ctx]  — POST /api/v1/models/load
#   rescale_srt <src> <dst> <speed>  — wraps stages/helpers/srt_rescale.py
#   cleanup           — EXIT trap: dumps diagnostics, clears /tmp/clipper, writes pipeline.done
#
# All functions here read globals set in clip-pipeline.sh ($TEMP_DIR, $LLM_URL,
# $CONTEXT_LENGTH, $CLIPS_DIR, $PIPELINE_DONE_FILE, $PERSISTENT_LOG).
# Extracted from clip-pipeline.sh as part of Phase B.

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

# Unload a model from LM Studio to free VRAM before Whisper loads.
# LM Studio's JIT+TTL handles lifecycle automatically, but explicit
# unload ensures VRAM is freed before transcription stages.
unload_model() {
    # Best-effort unload via LM Studio's REST API. Bounded to 15 s so a
    # missing endpoint or wedged Docker bridge can never hang the pipeline.
    # If the call fails, LM Studio's TTL/JIT cleanup will handle VRAM eviction
    # naturally on the next model load.
    local model="$1"
    log "Requesting unload of '$model' from VRAM..."
    local http
    http=$(curl -s -o /dev/null -w '%{http_code}' -m 15 -X POST "$LLM_URL/api/v1/models/unload" \
        -H "Content-Type: application/json" \
        -d "{\"instance_id\": \"$model\"}" 2>/dev/null) || http="000"
    case "$http" in
        2*) ;;  # 2xx OK
        000) log "  unload: LM Studio unreachable or timeout — JIT will reclaim VRAM on next load" ;;
        404) log "  unload: endpoint not supported by this LM Studio version (HTTP 404) — relying on JIT" ;;
        *)   log "  unload: HTTP $http — continuing anyway" ;;
    esac
    sleep 1  # brief pause for VRAM release
}

load_model() {
    # Best-effort pre-load via LM Studio's REST API. The curl is bounded to
    # 120 s (large 26B+ models with 32K context can legitimately take 30-60 s
    # to load) and any non-2xx response is logged but does NOT abort the
    # pipeline — LM Studio's JIT will load the model on the first inference
    # call if pre-load fails or the endpoint isn't supported in this LM Studio
    # version.
    #
    # BUG 48 redux: while curl blocks waiting for the load to complete,
    # STAGE_FILE goes quiet. A backgrounded touch loop bumps it every 10 s
    # so the dashboard's BUG-31 staleness gate can't trip during the load.
    local model="$1"
    local ctx="${2:-$CONTEXT_LENGTH}"
    log "Pre-loading '$model' into LM Studio (context_length=$ctx, timeout=120s)..."

    # First check: is LM Studio even reachable? If GET /v1/models doesn't
    # respond inside 5 s, skip the load entirely and let JIT handle it on the
    # first chat completion request.
    local probe
    probe=$(curl -s -o /dev/null -w '%{http_code}' -m 5 "$LLM_URL/v1/models" 2>/dev/null) || probe="000"
    if [ "$probe" != "200" ]; then
        log "  LM Studio probe at $LLM_URL/v1/models returned $probe — skipping pre-load, JIT will handle it"
        sleep 2
        return 0
    fi

    local HEARTBEAT_PID=""
    if [ -n "${STAGE_FILE:-}" ] && [ -f "$STAGE_FILE" ]; then
        ( while sleep 10; do touch "$STAGE_FILE" 2>/dev/null || break; done ) &
        HEARTBEAT_PID=$!
    fi

    local http
    http=$(curl -s -o /dev/null -w '%{http_code}' -m 120 -X POST "$LLM_URL/api/v1/models/load" \
        -H "Content-Type: application/json" \
        -d "{\"model\": \"$model\", \"context_length\": $ctx}" 2>/dev/null) || http="000"

    if [ -n "$HEARTBEAT_PID" ]; then
        kill "$HEARTBEAT_PID" 2>/dev/null || true
        wait "$HEARTBEAT_PID" 2>/dev/null || true
    fi

    case "$http" in
        2*) log "  pre-load OK (HTTP $http)" ;;
        000) log "  pre-load: timeout or LM Studio unreachable — JIT will load on first inference" ;;
        404) log "  pre-load: endpoint not supported by this LM Studio version (HTTP 404) — JIT will load on first inference" ;;
        409|400) log "  pre-load: HTTP $http — model likely already loaded; continuing" ;;
        *)   log "  pre-load: HTTP $http — continuing (JIT will load if needed)" ;;
    esac

    sleep 2  # allow model to fully initialize before first inference request
}

verify_models() {
    # Fail-fast model availability check (BUG 52). Hits LM Studio's /v1/models
    # once at pipeline startup and confirms every configured model ID is in the
    # downloaded list. Aborts with a clear error + the available list when a
    # model is missing — saves the user from waiting hours for Stage 3 / Pass B
    # / Stage 6 to limp through with HTTP 400 fallbacks on every call.
    #
    # When LM Studio is unreachable, log a warning and continue — a cached
    # transcription run can still complete without LM Studio (Stages 3-6 will
    # gracefully degrade to fallbacks; the user will see the connectivity
    # warning).
    log "Verifying configured models are loaded in LM Studio..."
    local probe_body
    probe_body=$(curl -s -m 5 "$LLM_URL/v1/models" 2>/dev/null) || probe_body=""
    if [ -z "$probe_body" ]; then
        warn "  LM Studio unreachable at $LLM_URL/v1/models — skipping model verification."
        warn "  Stages that need the LLM will fail individually if LM Studio doesn't recover."
        return 0
    fi

    # Parse the JSON list of model IDs. python3 is guaranteed in the container.
    local available
    available=$(printf '%s' "$probe_body" | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
    for m in (data.get("data") or []):
        mid = m.get("id")
        if mid:
            print(mid)
except Exception:
    sys.exit(0)
' 2>/dev/null)

    if [ -z "$available" ]; then
        warn "  /v1/models returned no parseable model list — skipping verification."
        return 0
    fi

    # Build the set of unique configured models we actually need.
    local configured=()
    [ -n "${TEXT_MODEL:-}" ] && configured+=("$TEXT_MODEL")
    [ -n "${VISION_MODEL:-}" ] && [ "$VISION_MODEL" != "$TEXT_MODEL" ] && configured+=("$VISION_MODEL")
    if [ -n "${TEXT_MODEL_PASSB:-}" ] && [ "$TEXT_MODEL_PASSB" != "$TEXT_MODEL" ] && [ "$TEXT_MODEL_PASSB" != "$VISION_MODEL" ]; then
        configured+=("$TEXT_MODEL_PASSB")
    fi
    if [ -n "${VISION_MODEL_STAGE6:-}" ] && [ "$VISION_MODEL_STAGE6" != "$TEXT_MODEL" ] && [ "$VISION_MODEL_STAGE6" != "$VISION_MODEL" ] && [ "$VISION_MODEL_STAGE6" != "$TEXT_MODEL_PASSB" ]; then
        configured+=("$VISION_MODEL_STAGE6")
    fi

    local missing=()
    for m in "${configured[@]}"; do
        if ! grep -Fxq "$m" <<< "$available"; then
            missing+=("$m")
        fi
    done

    if [ ${#missing[@]} -gt 0 ]; then
        err "Configured model(s) NOT loaded in LM Studio:"
        for m in "${missing[@]}"; do
            err "    - $m"
        done
        err ""
        err "  Available in LM Studio right now:"
        printf '%s\n' "$available" | sed 's/^/    - /' >&2
        err ""
        err "  Fix one of:"
        err "    1. Open LM Studio → Discover/Models → download '$m' (or your intended ID)."
        err "    2. Edit config/models.json (or use the dashboard Models panel) to point at a model from the available list above."
        err "    3. Switch the active profile in config/models.json::profiles to one whose IDs are in the available list."
        err ""
        err "  Aborting before Stage 3 to save you ~hours of HTTP 400 retries."
        exit 2
    fi

    log "  All ${#configured[@]} configured model(s) present in LM Studio."
}

rescale_srt() {
    # Divide all SRT timestamps by speed factor so subtitles stay in sync with
    # sped-up video. Called once per clip when CLIP_SPEED != 1.0.
    # Args: <src_srt> <dst_srt> <speed_factor>
    local src="$1" dst="$2" speed="$3"
    python3 /root/scripts/lib/stages/helpers/srt_rescale.py "$src" "$dst" "$speed"
}

cleanup() {
    # Capture exit status FIRST — before any subsequent command can clobber $?.
    PIPELINE_EXIT_STATUS=$?

    # Wallclock elapsed time. PIPELINE_START_EPOCH is set in clip-pipeline.sh
    # before this trap is registered. If for some reason it's missing (e.g.
    # `set -u` race during early init), default to 0 so the report still emits.
    local _now _elapsed _h _m _s
    _now=$(date +%s)
    _elapsed=$(( _now - ${PIPELINE_START_EPOCH:-$_now} ))
    _h=$(( _elapsed / 3600 ))
    _m=$(( (_elapsed % 3600) / 60 ))
    _s=$(( _elapsed % 60 ))
    if [ "$_h" -gt 0 ]; then
        log "Pipeline elapsed: ${_h}h ${_m}m ${_s}s (${_elapsed}s, exit=${PIPELINE_EXIT_STATUS})"
    else
        log "Pipeline elapsed: ${_m}m ${_s}s (${_elapsed}s, exit=${PIPELINE_EXIT_STATUS})"
    fi

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
    # PIPELINE_EXIT_STATUS captured at the top of cleanup() (before any later
    # commands could overwrite $?). The marker file written below reports it
    # back to the dashboard.
    rm -rf "${TEMP_DIR:?}/"*
    # Re-create the lifecycle markers AFTER cleanup. The dashboard polls
    # these files (via `docker exec cat`) so it knows whether the pipeline
    # finished cleanly even when its own `docker exec` session has died.
    {
        echo "exit_code=$PIPELINE_EXIT_STATUS"
        echo "finished=$(date -Iseconds)"
        echo "persistent_log=$PERSISTENT_LOG"
    } > "$PIPELINE_DONE_FILE" 2>/dev/null || true
}
trap cleanup EXIT
