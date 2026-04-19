FROM nvidia/cuda:12.3.2-cudnn9-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV WHISPER_MODEL_DIR=/root/.cache/whisper-models
ENV LD_LIBRARY_PATH=/usr/local/cuda/lib64:${LD_LIBRARY_PATH}

# System packages
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
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Node.js 22 LTS via NodeSource
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# OpenClaw
RUN npm install -g openclaw@latest

# faster-whisper (uses CUDA libs from base image)
RUN pip3 install --no-cache-dir faster-whisper flask

# Pre-download Whisper large-v3 model (~3GB baked into image)
RUN python3 -c "\
from faster_whisper import WhisperModel; \
WhisperModel('large-v3', device='cpu', compute_type='int8', \
    download_root='/root/.cache/whisper-models')"

# Create working directories
RUN mkdir -p /root/VODs/Clips_Ready /root/.openclaw/workspace /tmp/clipper

# Copy scripts and fix line endings (Windows CRLF safety)
COPY scripts/entrypoint.sh /entrypoint.sh
COPY scripts/clip-pipeline.sh /root/scripts/clip-pipeline.sh
COPY dashboard/ /root/dashboard/
RUN sed -i 's/\r$//' /entrypoint.sh && chmod +x /entrypoint.sh && \
    sed -i 's/\r$//' /root/scripts/clip-pipeline.sh && chmod +x /root/scripts/clip-pipeline.sh && \
    find /root/dashboard -type f -exec sed -i 's/\r$//' {} +

WORKDIR /root
ENTRYPOINT ["/entrypoint.sh"]
