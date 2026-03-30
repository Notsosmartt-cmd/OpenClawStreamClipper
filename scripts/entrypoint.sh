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

# --- Wait for Ollama ---
echo "Waiting for Ollama to become reachable..."
RETRIES=0
MAX_RETRIES=60
until curl -sf http://ollama:11434/api/tags > /dev/null 2>&1; do
  RETRIES=$((RETRIES + 1))
  if [ "$RETRIES" -ge "$MAX_RETRIES" ]; then
    echo "ERROR: Ollama not reachable after $MAX_RETRIES attempts. Exiting."
    exit 1
  fi
  echo "  Ollama not ready, retrying in 5s... ($RETRIES/$MAX_RETRIES)"
  sleep 5
done
echo "Ollama is reachable."

# --- Pull models if missing ---
pull_if_missing() {
  local model="$1"
  if ! curl -sf http://ollama:11434/api/tags | grep -q "\"$model\""; then
    echo "Pulling model: $model (this may take a while on first run)..."
    curl -sf http://ollama:11434/api/pull -d "{\"name\": \"$model\"}" | \
      while IFS= read -r line; do
        STATUS=$(echo "$line" | grep -o '"status":"[^"]*"' | head -1 | cut -d'"' -f4)
        if [ -n "$STATUS" ]; then
          echo "  [$model] $STATUS"
        fi
      done
    echo "Model $model ready."
  else
    echo "Model $model already present."
  fi
}

pull_if_missing "qwen3-vl:8b"
pull_if_missing "qwen2.5:7b"
pull_if_missing "qwen3.5:9b"

# --- Create workspace dirs if missing ---
mkdir -p /root/VODs/Clips_Ready /tmp/clipper /root/VODs/.transcriptions

# --- Ensure scripts are executable ---
chmod +x /root/scripts/*.sh 2>/dev/null || true

# --- Start OpenClaw Gateway ---
echo "Starting OpenClaw gateway..."
exec openclaw gateway
