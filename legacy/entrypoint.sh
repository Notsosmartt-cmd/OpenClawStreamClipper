#!/bin/bash
set -e

echo "=== OpenClaw Stream Clipper ==="

# --- Discord token injection ---
if [ -n "$DISCORD_BOT_TOKEN" ]; then
  echo "Injecting Discord bot token into config..."
  sed -i "s|__DISCORD_BOT_TOKEN__|$DISCORD_BOT_TOKEN|g" /root/.openclaw/openclaw.json
else
  echo "WARNING: DISCORD_BOT_TOKEN not set. Bot will not connect to Discord."
fi

# --- Read hardware config and export Whisper device vars ---
# LLM backend is now managed by LM Studio on the host.
# Only Whisper's device (cuda / cpu) is configured here.
HARDWARE_CONFIG="/root/.openclaw/hardware.json"
if [ -f "$HARDWARE_CONFIG" ]; then
  WHISPER_DEVICE=$(python3 -c "import json; d=json.load(open('$HARDWARE_CONFIG')); print(d.get('whisper_device','cuda'))" 2>/dev/null || echo "cuda")
  export CLIP_WHISPER_DEVICE="$WHISPER_DEVICE"
  if [ "$WHISPER_DEVICE" = "cuda" ]; then
    export CLIP_WHISPER_COMPUTE="float16"
  else
    export CLIP_WHISPER_COMPUTE="int8"
  fi
  echo "Hardware: whisper=$WHISPER_DEVICE ($CLIP_WHISPER_COMPUTE)"
else
  export CLIP_WHISPER_DEVICE="cuda"
  export CLIP_WHISPER_COMPUTE="float16"
  echo "Hardware: no config found — defaulting Whisper to CUDA"
fi

# --- Wait for LM Studio ---
# LM Studio must be running on the Windows host with "Serve on Local Network" enabled.
# Default port is 1234. Accessible from this container at host.docker.internal:1234.
LM_STUDIO_URL="http://host.docker.internal:1234"
echo "Waiting for LM Studio server at ${LM_STUDIO_URL}..."
RETRIES=0
MAX_RETRIES=30
until curl -sf "${LM_STUDIO_URL}/v1/models" > /dev/null 2>&1; do
  RETRIES=$((RETRIES + 1))
  if [ "$RETRIES" -ge "$MAX_RETRIES" ]; then
    echo "WARNING: LM Studio not reachable after $MAX_RETRIES attempts (${MAX_RETRIES}x 5s)."
    echo "  ► Make sure LM Studio is running with 'Serve on Local Network' enabled."
    echo "  ► The pipeline will fail when it first calls the LLM if LM Studio is not up."
    break
  fi
  echo "  LM Studio not ready, retrying in 5s... ($RETRIES/$MAX_RETRIES)"
  sleep 5
done
if curl -sf "${LM_STUDIO_URL}/v1/models" > /dev/null 2>&1; then
  echo "LM Studio server is reachable."
fi

# Models are managed in LM Studio's GUI — no auto-pull needed.
# Set model names in the dashboard Model Settings panel to match your
# LM Studio model IDs (check GET http://host.docker.internal:1234/v1/models).

# --- Create workspace dirs if missing ---
mkdir -p /root/VODs/Clips_Ready /tmp/clipper /root/VODs/.transcriptions

# --- Ensure scripts are executable ---
chmod +x /root/scripts/*.sh 2>/dev/null || true

# --- Best-effort default-asset fetch ---
# Keeps the "zero user intervention" UX even though assets are mounted from
# the host. Whisper is NOT fetched here — faster-whisper lazy-downloads on
# first pipeline call, and the Whisper weights are large (~3 GB) so we
# prefer not to block container startup on a network round-trip.
# Piper voice (~20 MB) is fetched eagerly so voiceover works the moment the
# user toggles TTS. Failure is non-fatal — piper_vo.py will retry on demand.
PIPER_DIR="/root/.cache/piper"
DEFAULT_PIPER_VOICE="${PIPER_VOICE:-en_US-amy-low}"
if [ -z "$(ls -A "$PIPER_DIR" 2>/dev/null)" ]; then
  echo "Piper cache at $PIPER_DIR is empty — fetching default voice $DEFAULT_PIPER_VOICE..."
  if python3 /root/scripts/lib/fetch_assets.py piper "$DEFAULT_PIPER_VOICE" >/dev/null 2>&1; then
    echo "  ✓ Piper voice cached."
  else
    echo "  ✗ Piper voice fetch failed (network?). Voiceover will retry on demand."
  fi
else
  echo "Piper cache at $PIPER_DIR is already populated."
fi

# Whisper cache status message (informational only — no fetch here).
WHISPER_DIR="/root/.cache/whisper-models"
if [ -z "$(ls -A "$WHISPER_DIR" 2>/dev/null)" ]; then
  echo "Whisper cache at $WHISPER_DIR is empty — the first pipeline run will download ~3 GB."
else
  echo "Whisper cache at $WHISPER_DIR is already populated."
fi

# --- Start Dashboard ---
echo "Starting web dashboard on port 5000..."
python3 /root/dashboard/app.py &

# --- Start OpenClaw Gateway ---
echo "Starting OpenClaw gateway..."
exec openclaw gateway
