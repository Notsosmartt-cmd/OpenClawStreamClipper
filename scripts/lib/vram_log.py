#!/usr/bin/env python3
"""Cross-vendor GPU/VRAM observability for the clipping pipeline.

Polls the host's GPU adapters and emits structured snapshots that the
pipeline orchestrator hooks at each stage transition. Output goes to
two places:

  * ``pipeline.log`` — one human-readable ``[VRAM]`` line per snapshot
  * ``{TEMP_DIR}/vram_log.json`` — cumulative array of snapshots that
    ``logtool vram <run>`` renders into a per-stage trajectory

Supported hardware:

  * **NVIDIA**: ``nvidia-smi`` gives full data (total, used, free,
    util%, temperature). Most reliable path when available.
  * **AMD on Windows**: ``Get-Counter '\\GPU Adapter Memory(*)\\Dedicated
    Usage'`` exposes per-adapter VRAM usage. Total VRAM comes from the
    ``HardwareInformation.qwMemorySize`` registry value under each
    adapter's driver class key (the ``Win32_VideoController.AdapterRAM``
    field caps at 4 GB, so we can't trust it for modern GPUs).
  * **Anything else on Windows**: same PowerShell path; vendor inferred
    by FriendlyName substring matching.

Failure-soft by design: every probe is wrapped in try/except, and a
``snapshot()`` call that hits errors on every backend returns an empty
``adapters`` list rather than raising. The pipeline keeps running.

The module is also runnable standalone for ad-hoc inspection:

    python scripts/lib/vram_log.py --snapshot
    python scripts/lib/vram_log.py --snapshot --json
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Probes — each returns adapters[] or None on failure
# ---------------------------------------------------------------------------

_NVIDIA_SMI_QUERY = (
    "index,name,memory.total,memory.used,memory.free,"
    "utilization.gpu,temperature.gpu"
)


def _probe_nvidia_smi(timeout_s: float = 4.0) -> Optional[List[Dict[str, Any]]]:
    """Query NVIDIA cards via ``nvidia-smi`` CSV output.

    Returns a list of adapter dicts or ``None`` if nvidia-smi is missing,
    times out, or returns garbage.
    """
    try:
        r = subprocess.run(
            [
                "nvidia-smi",
                f"--query-gpu={_NVIDIA_SMI_QUERY}",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True, text=True, timeout=timeout_s,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if r.returncode != 0:
        return None
    adapters: List[Dict[str, Any]] = []
    for raw in (r.stdout or "").splitlines():
        parts = [p.strip() for p in raw.split(",")]
        if len(parts) < 7:
            continue
        try:
            adapters.append({
                "vendor": "NVIDIA",
                "name": parts[1],
                "total_mb": int(parts[2]),
                "used_mb": int(parts[3]),
                "free_mb": int(parts[4]),
                "util_pct": int(parts[5]),
                "temp_c": int(parts[6]) if parts[6].isdigit() else None,
                "backend": "nvidia-smi",
            })
        except (ValueError, IndexError):
            continue
    return adapters or None


# PowerShell script: enumerate display adapters, look up real total VRAM
# from the registry (Enum\<InstanceId>.Driver → Control\Class\<Driver>.
# HardwareInformation.qwMemorySize), and join GPU Adapter Memory counter
# samples by descending-size pairing (LUID-to-PCI mapping isn't directly
# exposed; we sort both lists by size and pair them, which works reliably
# when each adapter is a distinct physical GPU).
#
# Emits one JSON object per real-GPU line. Virtual adapters (Meta Virtual
# Monitor, USB Mobile, etc.) are filtered out by total_mb=0.
_PS_SNAPSHOT_SCRIPT = r"""
# Step 1: enumerate adapters + total VRAM (via indirect Enum→Class registry path)
$adapters = @()
Get-PnpDevice -Class Display -Status OK | ForEach-Object {
  $pnp = $_
  $enumPath = "HKLM:\SYSTEM\CurrentControlSet\Enum\$($pnp.InstanceId)"
  $totalMb = 0
  try {
    $devKey = Get-ItemProperty -Path $enumPath -ErrorAction Stop
    $devNum = $devKey.Driver
    if ($devNum) {
      $classKey = "HKLM:\SYSTEM\CurrentControlSet\Control\Class\$devNum"
      $sz = (Get-ItemProperty -Path $classKey -ErrorAction Stop).'HardwareInformation.qwMemorySize'
      if ($sz) { $totalMb = [int]([math]::Round($sz / 1MB, 0)) }
    }
  } catch {}
  $adapters += [PSCustomObject]@{
    Name = $pnp.FriendlyName
    TotalMb = $totalMb
    InstanceId = $pnp.InstanceId
  }
}

# Step 2: collect used MB per adapter LUID from perfmon counters
$counterMb = @()
try {
  $cs = (Get-Counter '\GPU Adapter Memory(*)\Dedicated Usage' -ErrorAction Stop).CounterSamples
  foreach ($c in $cs) {
    $mb = [int]([math]::Round($c.CookedValue / 1MB, 0))
    if ($mb -ge 0) {
      $counterMb += [PSCustomObject]@{Luid = $c.InstanceName; UsedMb = $mb}
    }
  }
} catch {}

# Step 3: pair counter samples to adapters by sorting both by size descending.
# This is heuristic but reliable when adapters have distinct sizes (e.g. 16 GB
# NVIDIA + 12 GB AMD): largest counter goes to largest adapter, etc. If two
# adapters have identical VRAM totals (unusual), the assignment is still
# correct in aggregate (pool-level totals) just not necessarily per-card.
$realAdapters = $adapters | Where-Object { $_.TotalMb -gt 0 } | Sort-Object TotalMb -Descending
$sortedCounters = $counterMb | Sort-Object UsedMb -Descending
for ($i = 0; $i -lt $realAdapters.Count; $i++) {
  $usedMb = 0
  if ($i -lt $sortedCounters.Count) { $usedMb = $sortedCounters[$i].UsedMb }
  $obj = @{
    name = $realAdapters[$i].Name
    total_mb = $realAdapters[$i].TotalMb
    used_mb = $usedMb
    instance_id = $realAdapters[$i].InstanceId
  }
  $obj | ConvertTo-Json -Compress
}
"""


def _probe_windows_adapters(timeout_s: float = 6.0) -> Optional[List[Dict[str, Any]]]:
    """Enumerate display adapters via PowerShell. Captures NVIDIA, AMD,
    and Intel cards. Returns ``None`` on Linux/Mac or on PowerShell error.
    """
    if os.name != "nt":
        return None
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-Command", _PS_SNAPSHOT_SCRIPT],
            capture_output=True, text=True, timeout=timeout_s,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if r.returncode != 0 and not r.stdout:
        return None
    adapters: List[Dict[str, Any]] = []
    for line in (r.stdout or "").splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        name = obj.get("name") or ""
        total_mb = int(obj.get("total_mb") or 0)
        used_mb = int(obj.get("used_mb") or 0)
        # Skip stubs / virtual adapters with zero VRAM
        if total_mb <= 0 and used_mb <= 0:
            continue
        if total_mb <= 0 and any(s in name.lower()
                                  for s in ("virtual", "remote", "mobile")):
            continue
        vendor = _vendor_of_name(name)
        adapters.append({
            "vendor": vendor,
            "name": name,
            "total_mb": total_mb,
            "used_mb": used_mb,
            "free_mb": max(0, total_mb - used_mb) if total_mb else None,
            "util_pct": None,  # not exposed by perfmon counters
            "backend": "windows-perfmon",
            "instance_id": obj.get("instance_id"),
        })
    return adapters or None


def _vendor_of_name(name: str) -> str:
    n = (name or "").lower()
    if "nvidia" in n or "geforce" in n or "quadro" in n or "tesla" in n or "rtx" in n:
        return "NVIDIA"
    if "amd" in n or "radeon" in n or "rx " in n:
        return "AMD"
    if "intel" in n or "arc " in n or "uhd" in n or "iris" in n:
        return "Intel"
    return "Unknown"


def _merge_adapters(nvidia: Optional[List[Dict[str, Any]]],
                    windows: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Prefer nvidia-smi data for NVIDIA cards (richer fields); fall back
    to perfmon for AMD/Intel/anything else. De-duplicates by name so we
    don't double-count a card that both probes saw."""
    out: List[Dict[str, Any]] = []
    seen_names: set = set()
    for a in (nvidia or []):
        out.append(a)
        seen_names.add(a["name"])
    for a in (windows or []):
        if a["name"] in seen_names:
            continue
        out.append(a)
        seen_names.add(a["name"])
    return out


# ---------------------------------------------------------------------------
# LM Studio loaded-model probe
# ---------------------------------------------------------------------------

_LMS_PATHS = [
    "lms",
    os.path.expanduser(r"~\.cache\lm-studio\bin\lms.exe"),
    os.path.expanduser("~/.cache/lm-studio/bin/lms"),
]


def _which_lms() -> Optional[str]:
    import shutil
    for p in _LMS_PATHS:
        found = shutil.which(p) if not os.path.sep in p else (p if os.path.exists(p) else None)
        if found:
            return found
    return None


def _probe_lms_loaded(timeout_s: float = 5.0) -> List[Dict[str, Any]]:
    """Read currently loaded LM Studio models via ``lms ps``. Returns a
    list (possibly empty); never raises."""
    lms = _which_lms()
    if not lms:
        return []
    try:
        r = subprocess.run([lms, "ps"], capture_output=True, text=True, timeout=timeout_s)
    except (subprocess.TimeoutExpired, OSError):
        return []
    if r.returncode != 0:
        return []
    loaded: List[Dict[str, Any]] = []
    for line in (r.stdout or "").splitlines():
        line = line.rstrip()
        if not line or "loaded" in line.lower() or "MODEL" in line or "---" in line:
            continue
        # ``lms ps`` text format varies by version. Extract model IDs that
        # look like "vendor/model-name" or include a colon for tag.
        m = re.search(r"([\w\-\.]+\/[\w\-\.]+)", line)
        if m:
            loaded.append({"id": m.group(1), "raw_line": line.strip()})
    return loaded


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def snapshot(stage_label: Optional[str] = None) -> Dict[str, Any]:
    """Return a complete VRAM snapshot. Combines NVIDIA + Windows probes
    + LM Studio loaded models. Never raises — empty fields on probe failure.

    ``stage_label`` is stored in the snapshot for the pipeline orchestrator's
    per-stage tagging.
    """
    t0 = time.time()
    nvidia = _probe_nvidia_smi()
    windows = _probe_windows_adapters() if os.name == "nt" else None
    adapters = _merge_adapters(nvidia, windows)
    loaded = _probe_lms_loaded()
    total = sum((a.get("total_mb") or 0) for a in adapters)
    used = sum((a.get("used_mb") or 0) for a in adapters)
    snap: Dict[str, Any] = {
        "timestamp": time.time(),
        "probe_duration_ms": int((time.time() - t0) * 1000),
        "stage": stage_label,
        "adapters": adapters,
        "lm_studio_loaded": loaded,
        "pool_total_mb": total,
        "pool_used_mb": used,
        "pool_free_mb": max(0, total - used),
    }
    return snap


def format_snapshot_line(snap: Dict[str, Any]) -> str:
    """One-line summary suitable for ``pipeline.log``."""
    parts: List[str] = []
    if snap.get("stage"):
        parts.append(f"stage={snap['stage']}")
    for a in snap.get("adapters") or []:
        total = a.get("total_mb") or 0
        used = a.get("used_mb") or 0
        free = a.get("free_mb")
        if free is None:
            free = max(0, total - used) if total else 0
        util = a.get("util_pct")
        util_str = f", {util}% util" if util is not None else ""
        parts.append(
            f"{a.get('vendor','?')}({a.get('name','?')[:24]}): "
            f"{used}/{total} MB used, {free} free{util_str}"
        )
    loaded = snap.get("lm_studio_loaded") or []
    if loaded:
        parts.append("loaded: " + ", ".join(m.get("id", "?") for m in loaded))
    pool = snap.get("pool_total_mb") or 0
    pool_used = snap.get("pool_used_mb") or 0
    if pool:
        parts.append(f"pool: {pool_used}/{pool} MB ({100*pool_used//pool}%)")
    return " | ".join(parts) or "(no adapters detected)"


def stage_snapshot(stage_label: str, work_dir: str, log_fn=None) -> Dict[str, Any]:
    """Take a snapshot, log it to stderr via ``log_fn`` (or print), and
    append to ``{work_dir}/vram_log.json``. Returns the snapshot dict.

    Failure-soft: any error during probe or append is caught and logged
    but never raises — the caller's pipeline keeps running.
    """
    try:
        snap = snapshot(stage_label=stage_label)
    except Exception as e:  # noqa: BLE001
        msg = f"[VRAM] snapshot failed ({type(e).__name__}: {e})"
        if log_fn:
            log_fn(msg)
        else:
            print(msg, file=sys.stderr)
        return {"timestamp": time.time(), "stage": stage_label, "error": str(e),
                "adapters": [], "lm_studio_loaded": []}

    # Log a one-line summary
    line = "[VRAM] " + format_snapshot_line(snap)
    if log_fn:
        log_fn(line)
    else:
        print(line, file=sys.stderr)

    # Append to the per-run cumulative log
    try:
        log_path = Path(work_dir) / "vram_log.json"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        if log_path.exists():
            existing = json.loads(log_path.read_text(encoding="utf-8") or "[]")
            if not isinstance(existing, list):
                existing = []
        else:
            existing = []
        existing.append(snap)
        log_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    except OSError as e:
        # Non-fatal — operator may have read-only work dir
        if log_fn:
            log_fn(f"[VRAM] failed to persist log ({e})")
    return snap


def _cli() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="GPU/VRAM snapshot probe")
    ap.add_argument("--snapshot", action="store_true",
                    help="emit a single snapshot and exit")
    ap.add_argument("--json", action="store_true",
                    help="raw JSON output (default: human-readable line)")
    args = ap.parse_args()
    snap = snapshot()
    if args.json:
        print(json.dumps(snap, indent=2))
    else:
        print(format_snapshot_line(snap))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
