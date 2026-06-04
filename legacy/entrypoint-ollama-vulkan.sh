#!/bin/bash
set -e

echo "=== Ollama (Vulkan) ==="

CONFIG="/config/hardware.json"

if [ -f "$CONFIG" ]; then
    GPU_BACKEND=$(python3 -c "import json; d=json.load(open('$CONFIG')); print(d.get('gpu_backend','vulkan'))" 2>/dev/null || echo "vulkan")
    GPU_COUNT=$(python3 -c "import json; d=json.load(open('$CONFIG')); print(d.get('gpu_count','1'))" 2>/dev/null || echo "1")
    GPU_PAIR=$(python3 -c "import json; d=json.load(open('$CONFIG')); print(d.get('gpu_pair','amd_amd'))" 2>/dev/null || echo "amd_amd")

    if [ "$GPU_BACKEND" = "mixed" ]; then
        # Mixed NVIDIA + AMD: both visible as Vulkan devices (NVIDIA via toolkit ICD, AMD via /dev/dri)
        # Device indices are Vulkan enumeration order — user may need to verify with vulkaninfo
        export GGML_VK_VISIBLE_DEVICES="0,1"
        case "$GPU_PAIR" in
            "amd_primary")
                echo "Mixed Vulkan: AMD primary (device 0), NVIDIA secondary (device 1)"
                ;;
            *)
                echo "Mixed Vulkan: NVIDIA primary (device 0), AMD secondary (device 1)"
                ;;
        esac
        echo "NOTE: Verify device order with 'vulkaninfo --summary' if inference is slow"
    else
        # Pure Vulkan mode
        case "${GPU_COUNT}" in
            "2")
                export GGML_VK_VISIBLE_DEVICES="0,1"
                echo "Vulkan: using 2 GPUs (devices 0,1)"
                ;;
            "all")
                unset GGML_VK_VISIBLE_DEVICES
                echo "Vulkan: using all available GPUs"
                ;;
            *)
                export GGML_VK_VISIBLE_DEVICES="0"
                echo "Vulkan: using 1 GPU (device 0)"
                ;;
        esac
    fi
else
    export GGML_VK_VISIBLE_DEVICES="0"
    echo "Vulkan: no hardware config found, defaulting to 1 GPU"
fi

exec ollama serve
