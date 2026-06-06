#!/usr/bin/env python3
"""LM Studio model registry + VRAM/context prediction.

Reads the local LM Studio model list via ``lms ls`` (text output, parsed
into structured records) and exposes:

  * ``available_models()`` — list of ``{id, params, arch, size_gb, size_mb}``
  * ``recommend_context(model_id, available_pool_mb)`` — predicted
    largest safe ``context_length`` that fits the chosen model's weights
    AND its KV cache into ``available_pool_mb``
  * ``predict_vram(model_id, context_length)`` — projected total VRAM at
    a given context, broken into weights + KV cache + overhead

The KV-cache rate table below is empirically derived. Numbers are rough
(LM Studio's actual cache size depends on quant of K/V tensors, grouped-
query attention rate, and the specific GGUF metadata), but they're close
enough to drive a "this context will fit" prediction within ±15-20%.

Standalone use:

    python scripts/lib/model_registry.py
    python scripts/lib/model_registry.py --predict qwen/qwen3.6-35b-a3b 32768
    python scripts/lib/model_registry.py --recommend qwen/qwen3.6-35b-a3b 28583
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# KV cache rates (KB per token) keyed by architecture string from ``lms ls``.
# Derived from per-architecture (layers × kv_heads × head_dim × 2 K-and-V
# × 2 bytes FP16) calculations. Verified against LM Studio's reported
# "VRAM at <context>" values for a few common configurations.
# ---------------------------------------------------------------------------

# Format: arch_substring → (kb_per_token, note). Lookup is substring-match
# on the architecture field returned by `lms ls`. Defaults to 150 kb/tok
# (conservative) when arch isn't matched.
_KV_KB_PER_TOKEN: List[Tuple[str, float, str]] = [
    # Qwen 3.5/3.6 dense: 32 layers × 8 KV × 128 D × 2 KV × 2 bytes ≈ 131 KB/tok
    ("qwen35moe",   105.0, "Qwen 3.5/3.6 MoE — KV cache sized for active params (~3B)"),
    ("qwen3vlmoe", 110.0, "Qwen3-VL MoE — similar active-param KV footprint"),
    ("qwen35",     130.0, "Qwen 3.5/3.6 dense — 32L × 8 KV × 128 D"),
    ("qwen3vl",    115.0, "Qwen3-VL dense — slightly smaller KV than text-only"),
    # Gemma 4: 48L × 8 KV × 256 D × 2 KV × 2 bytes ≈ 393 KB/tok — Gemma uses
    # a larger head_dim (256) so its KV cache is roughly 3x Qwen at the same
    # context. This is the main reason Gemma 4 fits less context per MB.
    ("gemma4",     390.0, "Gemma 4 — large head_dim makes KV cache heavier per token"),
    # gpt-oss MoE (~24L, 8 KV, 128 D active) — small KV footprint
    ("gpt-oss",     95.0, "gpt-oss MoE — ~3B active params, small KV cache"),
    # Nemotron Hybrid (mix of attention + SSM) — small attention cache
    ("nemotron_h",  60.0, "Nemotron hybrid (attention + SSM) — minimal KV"),
]

# Per-inference overhead: weights + KV is only part of the VRAM math.
# LM Studio adds compute graph buffers, batched attention scratch, sampling
# state, and CUDA/Vulkan driver overhead. Empirically ~250-400 MB on the
# rakai-class run; 300 MB is the working estimate.
_INFERENCE_OVERHEAD_MB = 300

# Native max context per architecture. Used to cap recommendations so we
# don't suggest a 256K context window on a model with only 32K trained.
# These are the COMMON trained values (per-model variants may exceed via
# rope-scaling YARN extensions but quality degrades).
_NATIVE_MAX_CONTEXT: List[Tuple[str, int]] = [
    ("qwen35moe",  262_144),  # Qwen 3.5/3.6 architecture supports 256K
    ("qwen3vlmoe", 262_144),
    ("qwen35",     262_144),
    ("qwen3vl",    262_144),
    ("gemma4",     128_000),  # Gemma 4 trained at 128K
    ("gpt-oss",    131_072),
    ("nemotron_h",  32_768),
]

# Common-sense context tiers used as "snap-to" recommendations rather than
# raw arithmetic. Lets the dashboard show 32K / 64K / 128K rather than
# "47,233". Selected from {4, 8, 16, 32, 64, 128, 256} × 1024.
_CONTEXT_TIERS = [4_096, 8_192, 16_384, 32_768, 65_536, 131_072, 262_144]


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------

def _kv_kb_for(arch: str) -> Tuple[float, str]:
    """Resolve a ``(kb_per_token, note)`` for an architecture string."""
    a = (arch or "").lower()
    for key, kb, note in _KV_KB_PER_TOKEN:
        if key in a:
            return kb, note
    return 150.0, "default conservative estimate (unknown architecture)"


def _native_max_for(arch: str) -> int:
    """Look up the model family's native trained context window."""
    a = (arch or "").lower()
    for key, ctx in _NATIVE_MAX_CONTEXT:
        if key in a:
            return ctx
    return 32_768  # safe default


def _snap_to_tier(value: int) -> int:
    """Snap a raw context size DOWN to the nearest sane tier."""
    last = _CONTEXT_TIERS[0]
    for t in _CONTEXT_TIERS:
        if t > value:
            return last
        last = t
    return last


# ---------------------------------------------------------------------------
# lms ls parsing
# ---------------------------------------------------------------------------

def _which_lms() -> Optional[str]:
    candidates = [
        "lms",
        os.path.expanduser(r"~\.cache\lm-studio\bin\lms.exe"),
        os.path.expanduser("~/.cache/lm-studio/bin/lms"),
    ]
    for p in candidates:
        if os.sep in p:
            if os.path.exists(p):
                return p
        else:
            found = shutil.which(p)
            if found:
                return found
    return None


_LMS_ROW_RE = re.compile(
    r"""
    ^                                       # start of line
    (?P<id>[\w\-./@]+)\s+                  # model id with optional @quant tag
    (?:\(\d+\s+variants?\)\s+)?            # optional "(1 variant)"
    (?P<params>[\w\.\-]+)\s+               # param count (e.g. 35B-A3B, 4.0B)
    (?P<arch>[\w\-.]+)\s+                  # architecture (qwen35moe, gemma4, …)
    (?P<size>\d+\.\d+)\s+(?P<unit>GB|MB)   # size + unit
    \s+\w+                                 # DEVICE column (Local)
    \s*$
    """,
    re.VERBOSE,
)


def available_models(timeout_s: float = 8.0) -> List[Dict[str, Any]]:
    """Parse ``lms ls`` (text mode) into one record per installed model.

    Returns ``[]`` if lms is missing, the call fails, or the output is
    unparseable. Each record has keys: ``id``, ``params``, ``arch``,
    ``size_mb``, ``size_gb``.
    """
    lms = _which_lms()
    if not lms:
        return []
    try:
        r = subprocess.run([lms, "ls"], capture_output=True, text=True, timeout=timeout_s)
    except (subprocess.TimeoutExpired, OSError):
        return []
    if r.returncode != 0:
        return []

    out: List[Dict[str, Any]] = []
    for raw_line in (r.stdout or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        # Skip headers / footers / embeddings section
        if line.startswith(("You have", "LLM", "EMBEDDING", "------")):
            continue
        m = _LMS_ROW_RE.match(line)
        if not m:
            continue
        size_val = float(m.group("size"))
        unit = m.group("unit")
        size_mb = size_val * 1024.0 if unit == "GB" else size_val
        out.append({
            "id": m.group("id"),
            "params": m.group("params"),
            "arch": m.group("arch"),
            "size_mb": int(round(size_mb)),
            "size_gb": round(size_mb / 1024.0, 2),
        })
    return out


def model_by_id(model_id: str) -> Optional[Dict[str, Any]]:
    """Return the registry record for ``model_id`` or None if not found."""
    for m in available_models():
        if m["id"] == model_id:
            return m
    return None


# ---------------------------------------------------------------------------
# Prediction
# ---------------------------------------------------------------------------

def predict_vram(model_id: str, context_length: int) -> Dict[str, Any]:
    """Project total VRAM usage for ``model_id`` at ``context_length``.

    Returns a breakdown ``{model_id, arch, weights_mb, kv_cache_mb,
    overhead_mb, total_mb, kv_kb_per_token, note}``. Returns
    ``{"error": ...}`` if the model isn't found.
    """
    m = model_by_id(model_id)
    if not m:
        return {"error": f"model not found in lms ls: {model_id}"}
    kb_per_tok, note = _kv_kb_for(m["arch"])
    kv_mb = int(round(context_length * kb_per_tok / 1024.0))
    return {
        "model_id": model_id,
        "arch": m["arch"],
        "context_length": context_length,
        "weights_mb": m["size_mb"],
        "kv_cache_mb": kv_mb,
        "overhead_mb": _INFERENCE_OVERHEAD_MB,
        "total_mb": m["size_mb"] + kv_mb + _INFERENCE_OVERHEAD_MB,
        "kv_kb_per_token": kb_per_tok,
        "note": note,
    }


def recommend_context(
    model_id: str,
    available_pool_mb: int,
    safety_margin_mb: int = 500,
) -> Dict[str, Any]:
    """Predict the largest safe ``context_length`` that fits the model
    + its KV cache + overhead in ``available_pool_mb``.

    ``safety_margin_mb`` is reserved on top of the projected total to
    absorb measurement error in the KV-rate table (~15-20%) and to leave
    headroom for the OS + other processes.

    Returns ``{model_id, available_pool_mb, max_raw_tokens, recommended,
    weights_mb, overhead_mb, headroom_mb, native_max, fit_class}`` where
    ``fit_class`` is one of:

      * ``fits_easily``  — recommended context ≤ 50% of native max
      * ``fits_tight``   — between 50% and 100% of native max
      * ``no_kv_room``   — weights + overhead alone exceeds the pool;
                           model won't load at any context (or will spill)
    """
    m = model_by_id(model_id)
    if not m:
        return {"error": f"model not found in lms ls: {model_id}"}
    kb_per_tok, note = _kv_kb_for(m["arch"])
    native_max = _native_max_for(m["arch"])
    weights = m["size_mb"]
    available_kv = available_pool_mb - weights - _INFERENCE_OVERHEAD_MB - safety_margin_mb
    if available_kv <= 0:
        return {
            "model_id": model_id,
            "arch": m["arch"],
            "available_pool_mb": available_pool_mb,
            "weights_mb": weights,
            "overhead_mb": _INFERENCE_OVERHEAD_MB,
            "safety_margin_mb": safety_margin_mb,
            "max_raw_tokens": 0,
            "recommended": 0,
            "native_max": native_max,
            "kv_kb_per_token": kb_per_tok,
            "fit_class": "no_kv_room",
            "headroom_mb": available_kv,
            "note": (f"Weights ({weights} MB) + overhead ({_INFERENCE_OVERHEAD_MB} MB) "
                     f"+ safety margin ({safety_margin_mb} MB) already exceeds pool "
                     f"({available_pool_mb} MB). Model will spill to CPU if loaded."),
        }
    max_raw = int(available_kv * 1024.0 / kb_per_tok)
    capped = min(max_raw, native_max)
    recommended = _snap_to_tier(capped)
    pct_of_native = recommended / native_max if native_max else 0
    if pct_of_native >= 1.0:
        fit_class = "fits_native_max"
    elif pct_of_native >= 0.5:
        fit_class = "fits_tight"
    else:
        fit_class = "fits_easily"
    return {
        "model_id": model_id,
        "arch": m["arch"],
        "available_pool_mb": available_pool_mb,
        "weights_mb": weights,
        "overhead_mb": _INFERENCE_OVERHEAD_MB,
        "safety_margin_mb": safety_margin_mb,
        "kv_kb_per_token": kb_per_tok,
        "max_raw_tokens": max_raw,
        "recommended": recommended,
        "native_max": native_max,
        "fit_class": fit_class,
        "headroom_mb": available_kv,
        "note": note,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="LM Studio model registry + VRAM/context prediction")
    sub = ap.add_subparsers(dest="cmd")

    pl = sub.add_parser("list", help="list installed models with arch + size")
    pl.add_argument("--json", action="store_true")

    pp = sub.add_parser("predict",
        help="predict VRAM at a given context for a model")
    pp.add_argument("model_id")
    pp.add_argument("context_length", type=int)
    pp.add_argument("--json", action="store_true")

    pr = sub.add_parser("recommend",
        help="recommend max context for a model + available pool")
    pr.add_argument("model_id")
    pr.add_argument("available_pool_mb", type=int)
    pr.add_argument("--safety-mb", type=int, default=500)
    pr.add_argument("--json", action="store_true")

    args = ap.parse_args()

    if args.cmd == "list" or args.cmd is None:
        models = available_models()
        if args.cmd == "list" and getattr(args, "json", False):
            print(json.dumps(models, indent=2))
            return 0
        print(f"# {len(models)} installed model(s)")
        print(f"{'id':<32}  {'params':<10}  {'arch':<14}  {'size':>7}  {'KV kb/tok':>11}  {'native max':>11}")
        print("-" * 96)
        for m in models:
            kb, _ = _kv_kb_for(m["arch"])
            native = _native_max_for(m["arch"])
            print(f"{m['id']:<32}  {m['params']:<10}  {m['arch']:<14}  "
                  f"{m['size_gb']:>5.2f}GB  {kb:>11.1f}  {native:>11}")
        return 0

    if args.cmd == "predict":
        result = predict_vram(args.model_id, args.context_length)
        if args.json:
            print(json.dumps(result, indent=2))
            return 0
        if "error" in result:
            print(result["error"])
            return 1
        print(f"# {result['model_id']} @ {result['context_length']} tokens")
        print(f"  weights    : {result['weights_mb']:>6} MB")
        print(f"  KV cache   : {result['kv_cache_mb']:>6} MB ({result['kv_kb_per_token']:.1f} KB/tok)")
        print(f"  overhead   : {result['overhead_mb']:>6} MB")
        print(f"  PROJECTED  : {result['total_mb']:>6} MB")
        print(f"  ({result['note']})")
        return 0

    if args.cmd == "recommend":
        result = recommend_context(args.model_id, args.available_pool_mb,
                                    safety_margin_mb=args.safety_mb)
        if args.json:
            print(json.dumps(result, indent=2))
            return 0
        if "error" in result:
            print(result["error"])
            return 1
        print(f"# {result['model_id']} on {result['available_pool_mb']} MB pool")
        print(f"  arch          : {result['arch']}")
        print(f"  weights       : {result['weights_mb']:>6} MB")
        print(f"  overhead      : {result['overhead_mb']:>6} MB")
        print(f"  safety margin : {result['safety_margin_mb']:>6} MB")
        print(f"  headroom for KV : {result['headroom_mb']:>6} MB")
        print(f"  KV rate       : {result['kv_kb_per_token']:.1f} KB/tok")
        print(f"  raw max ctx   : {result['max_raw_tokens']:>7} tokens")
        print(f"  native max    : {result['native_max']:>7} tokens")
        print(f"  RECOMMENDED   : {result['recommended']:>7} tokens   [{result['fit_class']}]")
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
