#!/bin/bash
set -e

echo "=== Ollama ==="

CONFIG="/config/hardware.json"

# ── Read hardware config ────────────────────────────────────────────────────
if [ -f "$CONFIG" ]; then
    GPU_BACKEND=$(python3 -c "import json; d=json.load(open('$CONFIG')); print(d.get('gpu_backend','cuda'))" 2>/dev/null || echo "cuda")
    GPU_COUNT=$(python3 -c "import json; d=json.load(open('$CONFIG')); print(d.get('gpu_count','1'))" 2>/dev/null || echo "1")
    GPU_PAIR=$(python3 -c "import json; d=json.load(open('$CONFIG')); print(d.get('gpu_pair','nvidia_nvidia'))" 2>/dev/null || echo "nvidia_nvidia")
else
    GPU_BACKEND="cuda"
    GPU_COUNT="1"
    GPU_PAIR="nvidia_nvidia"
    echo "No hardware config found — defaulting to CUDA"
fi

echo "Config: backend=$GPU_BACKEND  count=$GPU_COUNT  pair=$GPU_PAIR"

# ── Vulkan GPU detection ────────────────────────────────────────────────────
# Returns the count of real discrete/integrated Vulkan GPU devices.
# Excludes llvmpipe (Mesa software CPU renderer) which is always present.
# Returns 0 if vulkaninfo is unavailable or only CPU devices found.
#
# WHY THIS MATTERS: When Vulkan mode is requested but no real GPU hardware
# is accessible (ICD init fails, missing /dev/dxg, missing Windows driver
# bridge), Ollama silently falls back to CPU inference — causing all LLM
# stages to run on CPU with high CPU usage and zero GPU utilization.
# This check catches that condition before starting Ollama.
count_real_vulkan_gpus() {
    if ! command -v vulkaninfo >/dev/null 2>&1; then
        echo "0"
        return
    fi
    local count
    count=$(vulkaninfo --summary 2>/dev/null \
        | grep "deviceType" \
        | grep -cv "PHYSICAL_DEVICE_TYPE_CPU" \
        || true)
    echo "${count:-0}"
}

# ── Configure GPU backend ───────────────────────────────────────────────────
case "$GPU_BACKEND" in

    "cuda")
        # NVIDIA CUDA — Ollama auto-detects via Container Toolkit.
        # No overrides needed; clear any stale vars from previous runs.
        unset CUDA_VISIBLE_DEVICES    2>/dev/null || true
        unset GGML_VK_VISIBLE_DEVICES 2>/dev/null || true
        echo "Backend: CUDA — all NVIDIA GPUs (automatic)"
        ;;

    "mixed")
        # NVIDIA + AMD together via Vulkan.
        # OLLAMA_VULKAN=1 enables the experimental Vulkan backend (off by default).
        # CUDA_VISIBLE_DEVICES="" disables the CUDA backend so llama.cpp is forced
        # onto the Vulkan path (CUDA is preferred over Vulkan when both are available).
        #
        # Requires both ICDs to be accessible inside the container:
        #   - NVIDIA: Vulkan ICD injected by Container Toolkit
        #   - AMD:    Mesa DZN (D3D12) ICD via /usr/lib/wsl + /dev/dxg on WSL2
        #             or Mesa RADV on native Linux
        #
        # SAFETY FALLBACK: if vulkaninfo finds no real GPU hardware (only llvmpipe),
        # fall back to CUDA rather than silently running all inference on CPU.
        echo "Checking Vulkan GPU availability..."
        REAL_VK_GPUS=$(count_real_vulkan_gpus)
        echo "  Real Vulkan GPU(s) detected: $REAL_VK_GPUS"

        if [ "$REAL_VK_GPUS" -ge 1 ]; then
            export OLLAMA_VULKAN=1
            export CUDA_VISIBLE_DEVICES=""

            case "$GPU_PAIR" in
                "amd_primary")
                    export GGML_VK_VISIBLE_DEVICES="1,0"
                    echo "Backend: Mixed Vulkan — AMD primary (device 1), NVIDIA secondary (device 0)"
                    ;;
                *)
                    export GGML_VK_VISIBLE_DEVICES="0,1"
                    echo "Backend: Mixed Vulkan — NVIDIA primary (device 0), AMD secondary (device 1)"
                    ;;
            esac

            if [ "$REAL_VK_GPUS" -lt 2 ]; then
                echo "WARNING: Only $REAL_VK_GPUS real Vulkan GPU found — expected 2 for mixed mode."
                echo "  One GPU may not be exposing a Vulkan ICD inside the container."
                echo "  Run: docker exec ollama vulkaninfo --summary"
            fi
            echo "Tip: verify device indices with 'docker exec ollama vulkaninfo --summary'"
        else
            echo ""
            echo "╔══════════════════════════════════════════════════════════════╗"
            echo "║  WARNING: Mixed Vulkan mode requested but no real GPU        ║"
            echo "║  hardware found (vulkaninfo shows only llvmpipe / CPU).      ║"
            echo "║                                                              ║"
            echo "║  Falling back to CUDA so inference runs on NVIDIA GPU.      ║"
            echo "║                                                              ║"
            echo "║  To debug AMD Vulkan on WSL2:                                ║"
            echo "║    docker exec ollama vulkaninfo --summary                   ║"
            echo "║    - NVIDIA ICD: needs Container Toolkit working correctly   ║"
            echo "║    - AMD ICD:    needs /dev/dxg + /usr/lib/wsl mounts +     ║"
            echo "║                 AMD Adrenalin WSL2 driver on Windows host   ║"
            echo "╚══════════════════════════════════════════════════════════════╝"
            echo ""
            unset CUDA_VISIBLE_DEVICES    2>/dev/null || true
            unset GGML_VK_VISIBLE_DEVICES 2>/dev/null || true
            echo "Backend: CUDA fallback — all NVIDIA GPUs (Vulkan unavailable)"
        fi
        ;;

    "vulkan")
        # Vulkan only — enable Vulkan backend, disable CUDA.
        echo "Checking Vulkan GPU availability..."
        REAL_VK_GPUS=$(count_real_vulkan_gpus)
        echo "  Real Vulkan GPU(s) detected: $REAL_VK_GPUS"

        if [ "$REAL_VK_GPUS" -ge 1 ]; then
            export OLLAMA_VULKAN=1
            export CUDA_VISIBLE_DEVICES=""

            case "$GPU_COUNT" in
                "2")
                    export GGML_VK_VISIBLE_DEVICES="0,1"
                    echo "Backend: Vulkan — 2 GPUs (devices 0, 1)"
                    ;;
                "all")
                    unset GGML_VK_VISIBLE_DEVICES 2>/dev/null || true
                    echo "Backend: Vulkan — all available Vulkan GPUs"
                    ;;
                *)
                    export GGML_VK_VISIBLE_DEVICES="0"
                    echo "Backend: Vulkan — single GPU (device 0)"
                    ;;
            esac
        else
            echo ""
            echo "WARNING: Vulkan mode requested but no real GPU hardware found."
            echo "  Falling back to CUDA. Run: docker exec ollama vulkaninfo --summary"
            echo ""
            unset CUDA_VISIBLE_DEVICES    2>/dev/null || true
            unset GGML_VK_VISIBLE_DEVICES 2>/dev/null || true
            echo "Backend: CUDA fallback (Vulkan unavailable)"
        fi
        ;;

    "cpu")
        # CPU only — disable all GPU backends explicitly.
        export CUDA_VISIBLE_DEVICES=""
        export GGML_VK_VISIBLE_DEVICES=""
        echo "Backend: CPU — GPU inference disabled"
        ;;

    *)
        echo "Unknown backend '$GPU_BACKEND' — defaulting to CUDA"
        unset CUDA_VISIBLE_DEVICES    2>/dev/null || true
        unset GGML_VK_VISIBLE_DEVICES 2>/dev/null || true
        ;;
esac

exec ollama serve
