#!/usr/bin/env python3
"""Minimal GGUF metadata reader — KV-cache-relevant keys only.

Reads only the header KV block of a GGUF file (no tensor data), so it's
fast (~5-20 ms) even on a 22 GB model. Returns the exact architecture
hyperparameters needed to compute KV-cache size **deterministically**
instead of the heuristic per-architecture rate table that preceded it.

The numbers this exposes (block_count, head_count_kv, key_length,
value_length, sliding_window, sliding_window_pattern, context_length)
are the ground truth llama.cpp itself uses to allocate the KV cache.

GGUF spec: https://github.com/ggml-org/ggml/blob/master/docs/gguf.md
"""
from __future__ import annotations

import os
import struct
from typing import Any, Dict, List, Optional

_GGUF_MAGIC = 0x46554747  # "GGUF" little-endian

# GGUF value-type enum → reader. Only the types that appear in metadata.
_FMT = {
    0: ("<B", 1),   # uint8
    1: ("<b", 1),   # int8
    2: ("<H", 2),   # uint16
    3: ("<h", 2),   # int16
    4: ("<I", 4),   # uint32
    5: ("<i", 4),   # int32
    6: ("<f", 4),   # float32
    7: ("<?", 1),   # bool
    10: ("<Q", 8),  # uint64
    11: ("<q", 8),  # int64
    12: ("<d", 8),  # float64
}
_TYPE_STR = 8
_TYPE_ARRAY = 9


def _read_str(f) -> str:
    n = struct.unpack("<Q", f.read(8))[0]
    return f.read(n).decode("utf-8", errors="replace")


def _read_value(f, vtype: int) -> Any:
    if vtype == _TYPE_STR:
        return _read_str(f)
    if vtype == _TYPE_ARRAY:
        elem_type = struct.unpack("<I", f.read(4))[0]
        count = struct.unpack("<Q", f.read(8))[0]
        return [_read_value(f, elem_type) for _ in range(count)]
    fmt = _FMT.get(vtype)
    if fmt is None:
        raise ValueError(f"unhandled GGUF value type {vtype}")
    return struct.unpack(fmt[0], f.read(fmt[1]))[0]


# Substrings of metadata keys we care about. We keep any key containing one
# of these — the architecture prefix (e.g. "qwen35moe.") varies per model.
_INTEREST = (
    "general.architecture",
    "block_count",
    "attention.head_count_kv",
    "attention.head_count",
    "attention.key_length",
    "attention.value_length",
    "attention.key_length_swa",
    "attention.value_length_swa",
    "attention.sliding_window",
    "context_length",
)


def read_metadata(path: str, max_kv: int = 5000) -> Dict[str, Any]:
    """Parse the GGUF header KV block, returning only KV-cache-relevant keys.

    Returns ``{}`` on any parse error or non-GGUF file. ``max_kv`` caps how
    many KV entries we'll iterate (defends against a corrupt count field).
    """
    out: Dict[str, Any] = {}
    try:
        with open(path, "rb") as f:
            if struct.unpack("<I", f.read(4))[0] != _GGUF_MAGIC:
                return {}
            f.read(4)   # version
            f.read(8)   # tensor_count
            kv_count = struct.unpack("<Q", f.read(8))[0]
            for _ in range(min(kv_count, max_kv)):
                key = _read_str(f)
                vtype = struct.unpack("<I", f.read(4))[0]
                val = _read_value(f, vtype)
                if any(s in key for s in _INTEREST):
                    out[key] = val
    except (OSError, struct.error, ValueError):
        return {}
    return out


def _arch_get(meta: Dict[str, Any], suffix: str) -> Any:
    """Find ``<arch>.<suffix>`` in the metadata regardless of arch prefix."""
    for k, v in meta.items():
        if k.endswith(suffix) and "swa" not in k:
            return v
    return None


def _arch_get_swa(meta: Dict[str, Any], suffix: str) -> Any:
    for k, v in meta.items():
        if k.endswith(suffix) and "swa" in k:
            return v
    return None


def kv_cache_bytes(meta: Dict[str, Any], context_length: int,
                   bytes_per_elem: int = 2) -> Optional[int]:
    """Compute exact KV-cache bytes for ``context_length`` from GGUF metadata.

    Handles three cases:

      1. **Simple GQA** (Qwen, gpt-oss): scalar ``head_count_kv``, single
         ``key_length`` / ``value_length``. KV = layers × kv × (kl+vl) ×
         bytes × ctx.

      2. **Sliding-window attention** (Gemma): ``head_count_kv`` is a
         per-layer array, ``sliding_window_pattern`` marks which layers are
         SWA (cache only ``sliding_window`` tokens) vs full-attention (cache
         the whole context). SWA layers use ``key_length_swa`` /
         ``value_length_swa``. This makes the cache MUCH smaller at large
         contexts than the naive full-attention upper bound.

      3. **Fallback**: missing fields → return None so the caller can use
         the heuristic table.

    ``bytes_per_elem`` defaults to 2 (FP16 KV cache, llama.cpp + LM Studio
    default). Pass 1 for Q8 KV-cache quantization, 0.5 isn't valid here so
    callers wanting Q4 should scale the result.
    """
    block_count = _arch_get(meta, "block_count")
    if not block_count:
        return None
    kv_heads = _arch_get(meta, "attention.head_count_kv")
    key_len = _arch_get(meta, "attention.key_length")
    val_len = _arch_get(meta, "attention.value_length")
    if kv_heads is None or key_len is None or val_len is None:
        return None

    # Case 2: SWA model (Gemma) — head_count_kv is a per-layer array
    pattern = _arch_get(meta, "attention.sliding_window_pattern")
    window = _arch_get(meta, "attention.sliding_window")
    if isinstance(kv_heads, list) and pattern and window:
        kl_swa = _arch_get_swa(meta, "attention.key_length") or key_len
        vl_swa = _arch_get_swa(meta, "attention.value_length") or val_len
        total = 0
        for i in range(block_count):
            heads = kv_heads[i] if i < len(kv_heads) else kv_heads[-1]
            is_swa = bool(pattern[i]) if i < len(pattern) else True
            if is_swa:
                cache_tokens = min(context_length, int(window))
                per = heads * (int(kl_swa) + int(vl_swa)) * bytes_per_elem
            else:
                cache_tokens = context_length
                per = heads * (int(key_len) + int(val_len)) * bytes_per_elem
            total += per * cache_tokens
        return total

    # Case 1: simple GQA — scalar (or single-element array) head_count_kv
    if isinstance(kv_heads, list):
        kv_heads = kv_heads[0] if kv_heads else 0
    per_token = int(block_count) * int(kv_heads) * (int(key_len) + int(val_len)) * bytes_per_elem
    return per_token * context_length


def native_context(meta: Dict[str, Any]) -> Optional[int]:
    """Trained context window from GGUF (``<arch>.context_length``)."""
    v = _arch_get(meta, "context_length")
    return int(v) if v else None


def architecture(meta: Dict[str, Any]) -> Optional[str]:
    return meta.get("general.architecture")


def summarize(meta: Dict[str, Any]) -> Dict[str, Any]:
    """Human-friendly summary of the KV-relevant params for diagnostics."""
    kv_heads = _arch_get(meta, "attention.head_count_kv")
    pattern = _arch_get(meta, "attention.sliding_window_pattern")
    swa_layers = sum(1 for p in pattern if p) if isinstance(pattern, list) else 0
    full_layers = (len(pattern) - swa_layers) if isinstance(pattern, list) else None
    return {
        "arch": architecture(meta),
        "block_count": _arch_get(meta, "block_count"),
        "head_count_kv": (f"per-layer {sorted(set(kv_heads))}"
                          if isinstance(kv_heads, list) else kv_heads),
        "key_length": _arch_get(meta, "attention.key_length"),
        "value_length": _arch_get(meta, "attention.value_length"),
        "sliding_window": _arch_get(meta, "attention.sliding_window"),
        "swa_layers": swa_layers or None,
        "full_attn_layers": full_layers,
        "native_context": native_context(meta),
    }


def _cli() -> int:
    import argparse
    import json
    ap = argparse.ArgumentParser(description="GGUF metadata + KV-cache calc")
    ap.add_argument("gguf_path")
    ap.add_argument("--context", type=int, default=32768)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    if not os.path.exists(args.gguf_path):
        print(f"not found: {args.gguf_path}")
        return 1
    meta = read_metadata(args.gguf_path)
    kv = kv_cache_bytes(meta, args.context)
    summary = summarize(meta)
    summary["kv_cache_mb_at_ctx"] = round(kv / (1024 * 1024), 1) if kv else None
    summary["context"] = args.context
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        for k, v in summary.items():
            print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
