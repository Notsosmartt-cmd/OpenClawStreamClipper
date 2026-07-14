#!/usr/bin/env python3
"""GPU-profile detection + conditional-optimization resolution (speed-wave3 §2b).

The owner's rig is dual-vendor (NVIDIA 16 GB + AMD 12 GB): LM Studio pools both
via Vulkan for big models, while a CUDA-only lane can serve a smaller Pass-B
model ~1.8× faster than the same model on the Vulkan split. Those defaults must
NOT leak onto other installs — a CPU-only / NVIDIA-only / AMD-only machine gets
identical pipeline behavior with the lane inert.

Resolution order for the profile:
  1. ``config/hardware.json`` → ``gpu_profile`` when set and not ``"auto"``
     (manual override, exposed in the dashboard Hardware panel)
  2. live probes: ``nvidia-smi`` for NVIDIA, ``Win32_VideoController`` (Windows)
     for AMD/others → dual_vendor | nvidia_only | amd_only | cpu_only

Everything is failure-soft: any probe error degrades toward the most
conservative answer (a detection bug must never break a run). Probes are
cached per process.

The CUDA text-lane (``CLIP_PASSB_RUNTIME`` = auto | off | cuda):
  * ``off``  — never switch runtimes.
  * ``cuda`` — force the lane (the caller still needs lms + a cuda pack).
  * ``auto`` (default) — active only when ALL hold: profile == dual_vendor,
    the ``lms`` CLI exists, a ``llama.cpp-win-x86_64-nvidia-cuda*`` backend
    pack is installed, and the NVIDIA card reports ≥ ``MIN_NVIDIA_GB`` total
    VRAM. On nvidia_only the selected runtime is already CUDA-native, and on
    amd_only/cpu_only there is no CUDA — auto resolves to inactive.

Run directly for a human-readable report: ``python scripts/lib/hw_profile.py``.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

# scripts/lib/hw_profile.py → parents[2] = repo root (same convention as paths.py)
REPO_ROOT = Path(__file__).resolve().parents[2]
HARDWARE_JSON = Path(os.environ.get("OPENCLAW_CONFIG_DIR", str(REPO_ROOT / "config"))) / "hardware.json"

VALID_PROFILES = ("auto", "dual_vendor", "nvidia_only", "amd_only", "cpu_only")
MIN_NVIDIA_GB = 12.0          # smallest NVIDIA card the 9B lane makes sense on
_CACHE: dict = {}


def _nvidia_gpus() -> list[dict]:
    """[{name, vram_mb}] via nvidia-smi; [] when absent/erroring."""
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=8)
        if r.returncode != 0:
            return []
        out = []
        for line in r.stdout.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 2:
                try:
                    out.append({"name": parts[0], "vram_mb": int(float(parts[1]))})
                except ValueError:
                    out.append({"name": parts[0], "vram_mb": 0})
        return out
    except Exception:
        return []


def _amd_gpus() -> list[dict]:
    """[{name}] AMD/Radeon adapters. Windows: Win32_VideoController via
    PowerShell CIM (AdapterRAM is unreliable above 4 GB — presence is what
    matters for profile resolution). Non-Windows: best-effort lspci."""
    try:
        if os.name == "nt":
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "(Get-CimInstance Win32_VideoController | "
                 "Select-Object -ExpandProperty Name) -join '|'"],
                capture_output=True, text=True, timeout=12)
            names = [n.strip() for n in (r.stdout or "").split("|") if n.strip()]
        else:
            r = subprocess.run(["lspci"], capture_output=True, text=True, timeout=8)
            names = [ln for ln in (r.stdout or "").splitlines() if "VGA" in ln or "Display" in ln]
        return [{"name": n} for n in names
                if ("amd" in n.lower() or "radeon" in n.lower())]
    except Exception:
        return []


def _config_override() -> str:
    try:
        cfg = json.loads(HARDWARE_JSON.read_text(encoding="utf-8"))
        v = str(cfg.get("gpu_profile", "auto")).strip().lower()
        return v if v in VALID_PROFILES else "auto"
    except Exception:
        return "auto"


def detect(refresh: bool = False) -> dict:
    """Full detection result (cached): gpus, override, detected + resolved profile."""
    if _CACHE and not refresh:
        return _CACHE
    nvidia = _nvidia_gpus()
    amd = _amd_gpus()
    if nvidia and amd:
        detected = "dual_vendor"
    elif nvidia:
        detected = "nvidia_only"
    elif amd:
        detected = "amd_only"
    else:
        detected = "cpu_only"
    override = _config_override()
    _CACHE.clear()
    _CACHE.update({
        "nvidia": nvidia,
        "amd": amd,
        "detected": detected,
        "override": override,
        "profile": detected if override == "auto" else override,
    })
    return _CACHE


def profile(refresh: bool = False) -> str:
    return detect(refresh)["profile"]


# --- CUDA text-lane availability -------------------------------------------
def _lms_bin() -> str | None:
    p = shutil.which("lms")
    if p:
        return p
    home = Path(os.path.expanduser("~"))
    for c in (home / ".cache" / "lm-studio" / "bin" / "lms.exe",
              home / ".lmstudio" / "bin" / "lms.exe"):
        if c.exists():
            return str(c)
    return None


def _cuda_pack_installed() -> bool:
    base = Path(os.path.expanduser("~")) / ".cache" / "lm-studio" / "extensions" / "backends"
    try:
        return any(d.name.startswith("llama.cpp-win-x86_64-nvidia-cuda")
                   for d in base.iterdir() if d.is_dir())
    except Exception:
        return False


def cuda_lane_status() -> dict:
    """{active, mode, reason} for the Pass-B CUDA lane on THIS machine."""
    mode = os.environ.get("CLIP_PASSB_RUNTIME", "auto").strip().lower()
    if mode not in ("auto", "off", "cuda"):
        mode = "auto"
    info = detect()
    prof = info["profile"]

    def status(active: bool, reason: str) -> dict:
        return {"active": active, "mode": mode, "profile": prof, "reason": reason}

    if mode == "off":
        return status(False, "disabled (CLIP_PASSB_RUNTIME=off)")
    if mode == "cuda":
        return status(True, "forced (CLIP_PASSB_RUNTIME=cuda)")
    # auto
    if prof == "nvidia_only":
        return status(False, "not needed — single NVIDIA GPU serves CUDA natively")
    if prof in ("amd_only", "cpu_only"):
        return status(False, f"unavailable — no NVIDIA GPU ({prof})")
    if not _lms_bin():
        return status(False, "unavailable — lms CLI not found")
    if not _cuda_pack_installed():
        return status(False, "unavailable — no CUDA llama.cpp runtime pack installed")
    nv = info["nvidia"]
    if not nv or (nv[0].get("vram_mb", 0) / 1024.0) < MIN_NVIDIA_GB:
        return status(False, f"unavailable — NVIDIA VRAM below {MIN_NVIDIA_GB:.0f} GB")
    return status(True, "dual-vendor GPU setup detected — Pass-B runs on the CUDA lane")


# --- lms runtime helpers (used by the pipeline's swap) ----------------------
def lms_runtime_aliases() -> dict:
    """{'selected': alias|None, 'cuda': best-cuda-alias|None} from `lms runtime ls`."""
    bin_ = _lms_bin()
    if not bin_:
        return {"selected": None, "cuda": None}
    try:
        r = subprocess.run([bin_, "runtime", "ls"], capture_output=True, text=True, timeout=20)
    except Exception:
        return {"selected": None, "cuda": None}
    selected, cuda_aliases = None, []
    for line in (r.stdout or "").splitlines():
        s = line.strip()
        if not s or s.startswith("LLM ENGINE"):
            continue
        alias = s.split()[0]
        if "✓" in line or " ✓" in line:
            selected = alias
        if "nvidia-cuda" in alias:
            cuda_aliases.append(alias)
    # Prefer the plain nvidia-cuda family (newer naming) over nvidia-cuda12,
    # then the highest NUMERIC version within the family ("2.23.1" > "2.8.0" —
    # a lexicographic sort gets this backwards).
    def _ver(alias: str) -> tuple:
        try:
            return tuple(int(x) for x in alias.split("@")[-1].split("."))
        except ValueError:
            return (0,)

    best_cuda = None
    plain = [a for a in cuda_aliases if "cuda12" not in a]
    pool = plain or cuda_aliases
    if pool:
        best_cuda = max(pool, key=_ver)
    return {"selected": selected, "cuda": best_cuda}


def feature_matrix() -> list[dict]:
    """Plain-language per-feature status for the dashboard Hardware panel."""
    info = detect()
    prof = info["profile"]
    lane = cuda_lane_status()
    feats = [
        {"key": "whisperx", "label": "WhisperX + speaker diarization",
         "status": "active", "reason": "runs on any profile (CUDA→CPU fallback built in)"},
        {"key": "s2_overlap", "label": "Stage-2 scan/transcribe overlap",
         "status": "active", "reason": "structural — all profiles"},
        {"key": "master_captions", "label": "Master-slice clip captions",
         "status": "active", "reason": "structural — all profiles (CPU-only saves the most)"},
        {"key": "cuda_lane", "label": "Pass-B CUDA text lane",
         "status": "active" if lane["active"] else "inactive",
         "reason": lane["reason"]},
        {"key": "nvenc", "label": "NVENC clip encode",
         "status": "active" if prof in ("dual_vendor", "nvidia_only") else "fallback",
         "reason": "hardware encoder" if prof in ("dual_vendor", "nvidia_only")
                   else "no NVIDIA GPU — libx264 software fallback (existing behavior)"},
    ]
    return feats


if __name__ == "__main__":
    d = detect(refresh=True)
    print(f"profile: {d['profile']}  (detected={d['detected']}, override={d['override']})")
    print(f"nvidia: {d['nvidia']}")
    print(f"amd:    {d['amd']}")
    print(f"cuda lane: {cuda_lane_status()}")
    print(f"aliases:   {lms_runtime_aliases()}")
    for f in feature_matrix():
        print(f"  [{f['status']:8s}] {f['label']} — {f['reason']}")
