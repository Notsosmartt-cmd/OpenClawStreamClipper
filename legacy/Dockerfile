FROM nvidia/cuda:12.3.2-cudnn9-runtime-ubuntu22.04

# ─────────────────────────────────────────────────────────────────────────────
# Build arguments
# ─────────────────────────────────────────────────────────────────────────────
#
# ORIGINALITY_STACK=full  (default) → install voiceover / music-tier-C /
#                                     face-pan extras (adds ~350 MB to the image)
# ORIGINALITY_STACK=slim            → skip them; the originality helpers will
#                                     log-and-skip gracefully when imports fail
#
#   docker compose build --build-arg ORIGINALITY_STACK=slim
#
# ─────────────────────────────────────────────────────────────────────────────
ARG ORIGINALITY_STACK=full

# SPEECH_STACK=full (default) installs whisperx + demucs for Phase 3's
# VAD-chunked transcription, forced alignment, and optional vocal-stem
# separation (~500 MB whisperx + ~80 MB demucs code; demucs model weights
# are downloaded lazily on first use). slim → faster-whisper-only fallback.
ARG SPEECH_STACK=full

ENV DEBIAN_FRONTEND=noninteractive
# Whisper + Piper caches are MOUNTED FROM THE HOST at runtime via
# ./models/{whisper,piper} — see docker-compose.yml. The image no longer
# bakes in the ~3 GB large-v3 weights; first pipeline run downloads them
# to the host mount and every subsequent run uses the cached copy.
ENV WHISPER_MODEL_DIR=/root/.cache/whisper-models
ENV PIPER_VOICE_DIR=/root/.cache/piper
ENV LD_LIBRARY_PATH=/usr/local/cuda/lib64:${LD_LIBRARY_PATH}
ENV ORIGINALITY_STACK=${ORIGINALITY_STACK}
ENV SPEECH_STACK=${SPEECH_STACK}

# ─── System packages ─────────────────────────────────────────────────────────
RUN echo 'Acquire::Retries "5";' > /etc/apt/apt.conf.d/80-retries \
    && echo 'Acquire::http::Timeout "30";' >> /etc/apt/apt.conf.d/80-retries \
    && echo 'Acquire::https::Timeout "30";' >> /etc/apt/apt.conf.d/80-retries \
    && apt-get update && apt-get install -y --no-install-recommends \
    curl \
    wget \
    git \
    ffmpeg \
    python3 \
    python3-pip \
    ca-certificates \
    gnupg \
    fonts-dejavu-core \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Node.js 22 LTS via NodeSource
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# OpenClaw
RUN npm install -g openclaw@latest

# ─── Python dependencies ─────────────────────────────────────────────────────
# Requirements are kept in visible files at the project root so users can
# inspect and pin versions without touching the Dockerfile.
COPY requirements.txt requirements-originality.txt requirements-speech.txt /tmp/
RUN pip3 install --no-cache-dir -r /tmp/requirements.txt && \
    if [ "$ORIGINALITY_STACK" = "full" ]; then \
        pip3 install --no-cache-dir -r /tmp/requirements-originality.txt; \
    else \
        echo "ORIGINALITY_STACK=slim — skipping piper / librosa / opencv"; \
    fi && \
    if [ "$SPEECH_STACK" = "full" ]; then \
        pip3 install --no-cache-dir -r /tmp/requirements-speech.txt; \
    else \
        echo "SPEECH_STACK=slim — skipping whisperx/demucs; faster-whisper fallback only"; \
    fi && \
    rm -f /tmp/requirements.txt /tmp/requirements-originality.txt /tmp/requirements-speech.txt && \
    pip3 cache purge || true

# ─── Create working directories ──────────────────────────────────────────────
# Model caches are MOUNTED, so we just ensure the mount points exist.
RUN mkdir -p /root/VODs/Clips_Ready \
             /root/.openclaw/workspace \
             /root/.cache/whisper-models \
             /root/.cache/piper \
             /root/music \
             /tmp/clipper \
             /root/scripts/lib

# ─── Copy scripts + dashboard ────────────────────────────────────────────────
# These are ALSO mounted at runtime (./scripts, ./dashboard) so live edits
# don't require a rebuild. The COPY below is a fallback for users who run
# the image with `docker run` without mounts.
COPY scripts/entrypoint.sh /entrypoint.sh
COPY scripts/clip-pipeline.sh /root/scripts/clip-pipeline.sh
COPY scripts/lib/ /root/scripts/lib/
COPY scripts/stages/ /root/scripts/stages/
COPY dashboard/ /root/dashboard/
RUN sed -i 's/\r$//' /entrypoint.sh && chmod +x /entrypoint.sh && \
    sed -i 's/\r$//' /root/scripts/clip-pipeline.sh && chmod +x /root/scripts/clip-pipeline.sh && \
    find /root/scripts/lib -type f -name '*.py' -exec sed -i 's/\r$//' {} + && \
    find /root/scripts/lib -type f -name '*.sh' -exec sed -i 's/\r$//' {} + && \
    find /root/scripts/stages -type f -name '*.sh' -exec sed -i 's/\r$//' {} + && \
    find /root/dashboard -type f -exec sed -i 's/\r$//' {} +

WORKDIR /root
ENTRYPOINT ["/entrypoint.sh"]
