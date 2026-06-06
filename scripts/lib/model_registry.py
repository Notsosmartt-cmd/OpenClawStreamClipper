#!/usr/bin/env python3
"""LM Studio model registry + deterministic VRAM/context prediction.

Combines three data sources:

  1. ``lms ls`` — the list of installed models (id, params, arch, on-disk
     size). The size approximates the weight VRAM footprint at the file's
     quant.
  2. **GGUF metadata** (via :mod:`gguf_meta`) — the EXACT KV-cache
     hyperparameters (layers, kv heads, head dim, sliding-window pattern)
     read straight from each model file's header. This makes the
     VRAM/context math deterministic rather than a per-architecture
     guess. Gemma's sliding-window attention in particular makes its KV
     cache ~10x smaller at 32K than a naive flat-rate estimate.
  3. A heuristic per-architecture KV-rate table — used ONLY as a fallback
     when the GGUF file can't be located or parsed (e.g. the models dir
     moved and discovery missed it).

Public API (stable — ``logtool vram`` depends on it):
  * ``available_models()`` -> list of model records
  * ``model_by_id(id)`` -> single record or None
  * ``predict_vram(id, ctx)`` -> {weights_mb, kv_cache_mb, total_mb, ...}
  * ``recommend_context(id, pool_mb)`` -> {recommended, fit_class, ...}

CLI:
    python scripts/lib/model_registry.py list
    python scripts/lib/model_registry.py predict qwen/qwen3.6-35b-a3b 32768
    python scripts/lib/model_registry.py recommend qwen/qwen3.6-35b-a3b 28583
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Local import (same dir on path when run via the pipeline's run_module).
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import gguf_meta as _gguf
except ImportError:
    _gguf = None


# ---------------------------------------------------------------------------
# Heuristic KV rates (FALLBACK ONLY — used when GGUF can't be read).
# These are the OLD estimates; the GGUF path supersedes them when available.
# Corrected 2026-06-05 against actual GGUF metadata where it was way off.
# ---------------------------------------------------------------------------
_KV_KB_PER_TOKEN_FALLBACK: List[Tuple[str, float]] = [
    ("qwen35moe",   80.0),   # 40L × 2kv × 512 × 2 (was est 105)
    ("qwen3vlmoe",  80.0),
    ("qwen35",     128.0),   # 32L × 4kv × 512 × 2 (est 130 — was right)
    ("qwen3vl",    100.0),
    ("gemma4",      40.0),   # SWA-dominated; effective ~36 at 32K (est 390 — 10x off!)
    ("gpt-oss",     48.0),   # 24L × 8kv × 128 × 2 (was est 95)
    ("nemotron_h",  40.0),
]

_INFERENCE_OVERHEAD_MB = 300
_DEFAULT_SAFETY_MB = 500

_NATIVE_MAX_FALLBACK: List[Tuple[str, int]] = [
    ("qwen35moe",  262_144), ("qwen3vlmoe", 262_144),
    ("qwen35",     262_144), ("qwen3vl",    262_144),
    ("gemma4",     128_000), ("gpt-oss",    131_072),
    ("nemotron_h",  32_768),
]

_CONTEXT_TIERS = [4_096, 8_192, 16_384, 32_768, 65_536, 131_072, 262_144]


# ---------------------------------------------------------------------------
# Models-dir discovery (portable across machines)
# ---------------------------------------------------------------------------

def _candidate_model_roots() -> List[str]:
    """Ordered list of plausible LM Studio model roots. First match that
    contains GGUFs wins. ``CLIP_LMSTUDIO_MODELS_DIR`` env overrides all."""
    roots: List[str] = []
    env = os.environ.get("CLIP_LMSTUDIO_MODELS_DIR", "").strip()
    if env:
        roots.append(env)
    roots += [
        os.path.expanduser(r"~\.cache\lm-studio\models"),
        os.path.expanduser(r"~\.lmstudio\models"),
        os.path.expanduser("~/.cache/lm-studio/models"),
        os.path.expanduser("~/.lmstudio/models"),
    ]
    # Power-user relocations: scan drive roots for a top-level lm-studio dir.
    if os.name == "nt":
        for drive in "CDEFGHIJ":
            roots.append(rf"{drive}:\lm-studio")
            roots.append(rf"{drive}:\lm-studio\models")
    return roots


_MODELS_ROOT_CACHE: Optional[str] = None


def _models_root() -> Optional[str]:
    global _MODELS_ROOT_CACHE
    if _MODELS_ROOT_CACHE is not None:
        return _MODELS_ROOT_CACHE or None
    import glob
    for root in _candidate_model_roots():
        if not root or not os.path.isdir(root):
            continue
        # Validate: at least one .gguf somewhere under it
        try:
            hit = next(iter(glob.iglob(os.path.join(root, "**", "*.gguf"),
                                       recursive=True)), None)
        except OSError:
            hit = None
        if hit:
            _MODELS_ROOT_CACHE = root
            return root
    _MODELS_ROOT_CACHE = ""
    return None


def _normalize(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


_GGUF_PATH_CACHE: Dict[str, Optional[str]] = {}


def _gguf_path_for(model_id: str) -> Optional[str]:
    """Map an lms model ID (e.g. ``qwen/qwen3.6-35b-a3b``) to its main GGUF
    file. Matches by normalized name substring against the model dir names,
    then picks the largest non-mmproj shard's first part. Cached per ID.
    """
    if model_id in _GGUF_PATH_CACHE:
        return _GGUF_PATH_CACHE[model_id]
    root = _models_root()
    if not root:
        _GGUF_PATH_CACHE[model_id] = None
        return None
    import glob
    name = model_id.split("/")[-1]
    target = _normalize(name)
    best: Optional[str] = None
    best_size = -1
    for gguf in glob.iglob(os.path.join(root, "**", "*.gguf"), recursive=True):
        low = gguf.lower()
        if "mmproj" in low:
            continue
        # Match on the containing directory name OR the file name
        parent = _normalize(os.path.basename(os.path.dirname(gguf)))
        fnorm = _normalize(os.path.basename(gguf))
        if target in parent or target in fnorm:
            # Prefer the first shard of a multi-part model; among candidates,
            # pick the largest single file (the main weights).
            try:
                sz = os.path.getsize(gguf)
            except OSError:
                sz = 0
            # Skip later shards (00002-of-..., etc.) — pick part 1 / single.
            if re.search(r"0000[2-9]-of", low):
                continue
            if sz > best_size:
                best_size = sz
                best = gguf
    _GGUF_PATH_CACHE[model_id] = best
    return best


_META_CACHE: Dict[str, Dict[str, Any]] = {}


def _meta_for(model_id: str) -> Dict[str, Any]:
    """Parsed GGUF metadata for a model ID (cached). ``{}`` if unavailable."""
    if _gguf is None:
        return {}
    path = _gguf_path_for(model_id)
    if not path:
        return {}
    key = f"{path}:{os.path.getmtime(path) if os.path.exists(path) else 0}"
    if key in _META_CACHE:
        return _META_CACHE[key]
    meta = _gguf.read_metadata(path)
    _META_CACHE[key] = meta
    return meta


# ---------------------------------------------------------------------------
# Fallback lookups
# ---------------------------------------------------------------------------

def _kv_kb_fallback(arch: str) -> float:
    a = (arch or "").lower()
    for key, kb in _KV_KB_PER_TOKEN_FALLBACK:
        if key in a:
            return kb
    return 150.0


def _native_max_fallback(arch: str) -> int:
    a = (arch or "").lower()
    for key, ctx in _NATIVE_MAX_FALLBACK:
        if key in a:
            return ctx
    return 32_768


def _snap_to_tier(value: int) -> int:
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
    for p in ["lms",
              os.path.expanduser(r"~\.cache\lm-studio\bin\lms.exe"),
              os.path.expanduser("~/.cache/lm-studio/bin/lms")]:
        if os.sep in p:
            if os.path.exists(p):
                return p
        else:
            found = shutil.which(p)
            if found:
                return found
    return None


_LMS_ROW_RE = re.compile(
    r"^(?P<id>[\w\-./@]+)\s+(?:\(\d+\s+variants?\)\s+)?"
    r"(?P<params>[\w\.\-]+)\s+(?P<arch>[\w\-.]+)\s+"
    r"(?P<size>\d+\.\d+)\s+(?P<unit>GB|MB)\s+\w+\s*$"
)


def available_models(timeout_s: float = 8.0) -> List[Dict[str, Any]]:
    """One record per installed LLM (skips embeddings)."""
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
    in_embed = False
    for raw in (r.stdout or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("EMBEDDING"):
            in_embed = True
            continue
        if in_embed:
            continue
        if line.startswith(("You have", "LLM", "------")):
            continue
        m = _LMS_ROW_RE.match(line)
        if not m:
            continue
        size_mb = float(m.group("size")) * (1024.0 if m.group("unit") == "GB" else 1.0)
        out.append({
            "id": m.group("id"),
            "params": m.group("params"),
            "arch": m.group("arch"),
            "size_mb": int(round(size_mb)),
            "size_gb": round(size_mb / 1024.0, 2),
        })
    return out


def model_by_id(model_id: str) -> Optional[Dict[str, Any]]:
    for m in available_models():
        if m["id"] == model_id:
            return m
    return None


# ---------------------------------------------------------------------------
# Prediction (GGUF-exact when available, heuristic fallback otherwise)
# ---------------------------------------------------------------------------

def _kv_cache_mb(model_id: str, arch: str, context_length: int) -> Tuple[int, str]:
    """Return ``(kv_cache_mb, source)`` where source is 'gguf' or 'heuristic'."""
    meta = _meta_for(model_id)
    if meta and _gguf is not None:
        kv_bytes = _gguf.kv_cache_bytes(meta, context_length)
        if kv_bytes:
            return int(round(kv_bytes / (1024 * 1024))), "gguf"
    # Fallback to the per-architecture rate table
    kb = _kv_kb_fallback(arch)
    return int(round(context_length * kb / 1024.0)), "heuristic"


def _native_max(model_id: str, arch: str) -> int:
    meta = _meta_for(model_id)
    if meta and _gguf is not None:
        nc = _gguf.native_context(meta)
        if nc:
            return nc
    return _native_max_fallback(arch)


def predict_vram(model_id: str, context_length: int) -> Dict[str, Any]:
    """Project total VRAM for ``model_id`` at ``context_length``."""
    m = model_by_id(model_id)
    if not m:
        return {"error": f"model not found in lms ls: {model_id}"}
    kv_mb, source = _kv_cache_mb(model_id, m["arch"], context_length)
    return {
        "model_id": model_id,
        "arch": m["arch"],
        "context_length": context_length,
        "weights_mb": m["size_mb"],
        "kv_cache_mb": kv_mb,
        "kv_source": source,
        "overhead_mb": _INFERENCE_OVERHEAD_MB,
        "total_mb": m["size_mb"] + kv_mb + _INFERENCE_OVERHEAD_MB,
    }


def recommend_context(
    model_id: str,
    available_pool_mb: int,
    safety_margin_mb: int = _DEFAULT_SAFETY_MB,
) -> Dict[str, Any]:
    """Largest safe ``context_length`` fitting weights + KV + overhead in
    ``available_pool_mb``. Iterates the context tiers from largest down and
    picks the first that fits — robust for any KV shape including Gemma's
    piecewise sliding-window cache."""
    m = model_by_id(model_id)
    if not m:
        return {"error": f"model not found in lms ls: {model_id}"}
    native = _native_max(model_id, m["arch"])
    budget = available_pool_mb - m["size_mb"] - _INFERENCE_OVERHEAD_MB - safety_margin_mb
    if budget <= 0:
        return {
            "model_id": model_id, "arch": m["arch"],
            "available_pool_mb": available_pool_mb, "weights_mb": m["size_mb"],
            "overhead_mb": _INFERENCE_OVERHEAD_MB, "safety_margin_mb": safety_margin_mb,
            "recommended": 0, "native_max": native, "fit_class": "no_kv_room",
            "kv_source": _kv_cache_mb(model_id, m["arch"], 4096)[1],
            "headroom_mb": budget,
            "note": (f"Weights ({m['size_mb']} MB) + overhead + safety exceeds pool "
                     f"({available_pool_mb} MB). Will spill to CPU if loaded."),
        }
    # Walk tiers from largest ≤ native down to smallest; pick first that fits.
    recommended = 0
    kv_at_rec = 0
    source = "heuristic"
    for tier in sorted(_CONTEXT_TIERS, reverse=True):
        if tier > native:
            continue
        kv_mb, source = _kv_cache_mb(model_id, m["arch"], tier)
        if kv_mb <= budget:
            recommended = tier
            kv_at_rec = kv_mb
            break
    if recommended == 0:
        # Even the smallest tier doesn't fit the KV budget
        kv_mb, source = _kv_cache_mb(model_id, m["arch"], _CONTEXT_TIERS[0])
        return {
            "model_id": model_id, "arch": m["arch"],
            "available_pool_mb": available_pool_mb, "weights_mb": m["size_mb"],
            "overhead_mb": _INFERENCE_OVERHEAD_MB, "safety_margin_mb": safety_margin_mb,
            "recommended": 0, "native_max": native, "fit_class": "no_kv_room",
            "kv_source": source, "headroom_mb": budget,
            "note": f"Even {_CONTEXT_TIERS[0]} ctx needs {kv_mb} MB KV > {budget} MB budget.",
        }
    pct = recommended / native if native else 0
    fit_class = ("fits_native_max" if recommended >= native else
                 "fits_tight" if pct >= 0.5 else "fits_easily")
    return {
        "model_id": model_id, "arch": m["arch"],
        "available_pool_mb": available_pool_mb, "weights_mb": m["size_mb"],
        "overhead_mb": _INFERENCE_OVERHEAD_MB, "safety_margin_mb": safety_margin_mb,
        "kv_cache_mb_at_recommended": kv_at_rec,
        "kv_source": source,
        "recommended": recommended, "native_max": native,
        "fit_class": fit_class, "headroom_mb": budget,
    }


def recommend_context_combo(
    text_model_id: str,
    vision_model_id: Optional[str],
    available_pool_mb: int,
    safety_margin_mb: int = _DEFAULT_SAFETY_MB,
) -> Dict[str, Any]:
    """Recommended SHARED context_length for a text+vision config.

    The pipeline swaps models per stage (only one resident at a time), but
    ``context_length`` in config/models.json is a single shared value, so
    the safe recommendation is the MORE CONSTRAINED of the two models. When
    text == vision (consolidation), it's just that one model.
    """
    text_rec = recommend_context(text_model_id, available_pool_mb, safety_margin_mb)
    if "error" in text_rec:
        return text_rec
    if not vision_model_id or vision_model_id == text_model_id:
        return {
            "text_model": text_model_id,
            "vision_model": vision_model_id or text_model_id,
            "consolidated": True,
            "recommended": text_rec["recommended"],
            "constrained_by": text_model_id,
            "text": text_rec,
            "vision": text_rec,
        }
    vision_rec = recommend_context(vision_model_id, available_pool_mb, safety_margin_mb)
    if "error" in vision_rec:
        return vision_rec
    rec = min(text_rec["recommended"], vision_rec["recommended"])
    constrained = (text_model_id if text_rec["recommended"] <= vision_rec["recommended"]
                   else vision_model_id)
    return {
        "text_model": text_model_id,
        "vision_model": vision_model_id,
        "consolidated": False,
        "recommended": rec,
        "constrained_by": constrained,
        "text": text_rec,
        "vision": vision_rec,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="LM Studio model registry + VRAM/context prediction")
    sub = ap.add_subparsers(dest="cmd")

    pl = sub.add_parser("list"); pl.add_argument("--json", action="store_true")
    pp = sub.add_parser("predict")
    pp.add_argument("model_id"); pp.add_argument("context_length", type=int)
    pp.add_argument("--json", action="store_true")
    pr = sub.add_parser("recommend")
    pr.add_argument("model_id"); pr.add_argument("available_pool_mb", type=int)
    pr.add_argument("--safety-mb", type=int, default=_DEFAULT_SAFETY_MB)
    pr.add_argument("--json", action="store_true")
    pc = sub.add_parser("combo")
    pc.add_argument("text_model_id"); pc.add_argument("vision_model_id")
    pc.add_argument("available_pool_mb", type=int)
    pc.add_argument("--json", action="store_true")

    args = ap.parse_args()

    if args.cmd in (None, "list"):
        models = available_models()
        if args.cmd == "list" and getattr(args, "json", False):
            print(json.dumps(models, indent=2)); return 0
        root = _models_root()
        print(f"# {len(models)} installed LLM(s)  (GGUF root: {root or 'NOT FOUND — heuristic mode'})")
        print(f"{'id':<30}  {'arch':<12}  {'size':>7}  {'KV src':<9}  {'native max':>11}")
        print("-" * 80)
        for m in models:
            src = _kv_cache_mb(m["id"], m["arch"], 32768)[1]
            native = _native_max(m["id"], m["arch"])
            print(f"{m['id']:<30}  {m['arch']:<12}  {m['size_gb']:>5.2f}GB  {src:<9}  {native:>11}")
        return 0

    if args.cmd == "predict":
        r = predict_vram(args.model_id, args.context_length)
        if args.json:
            print(json.dumps(r, indent=2)); return 0
        if "error" in r:
            print(r["error"]); return 1
        print(f"# {r['model_id']} @ {r['context_length']} tokens  [KV source: {r['kv_source']}]")
        print(f"  weights   : {r['weights_mb']:>6} MB")
        print(f"  KV cache  : {r['kv_cache_mb']:>6} MB")
        print(f"  overhead  : {r['overhead_mb']:>6} MB")
        print(f"  PROJECTED : {r['total_mb']:>6} MB")
        return 0

    if args.cmd == "recommend":
        r = recommend_context(args.model_id, args.available_pool_mb, safety_margin_mb=args.safety_mb)
        if args.json:
            print(json.dumps(r, indent=2)); return 0
        if "error" in r:
            print(r["error"]); return 1
        print(f"# {r['model_id']} on {r['available_pool_mb']} MB pool  [KV source: {r['kv_source']}]")
        print(f"  weights       : {r['weights_mb']:>6} MB")
        print(f"  KV budget     : {r['headroom_mb']:>6} MB (after overhead + safety)")
        print(f"  native max    : {r['native_max']:>7} tokens")
        print(f"  RECOMMENDED   : {r['recommended']:>7} tokens   [{r['fit_class']}]")
        if r.get("kv_cache_mb_at_recommended"):
            print(f"  KV @ rec      : {r['kv_cache_mb_at_recommended']:>6} MB")
        return 0

    if args.cmd == "combo":
        r = recommend_context_combo(args.text_model_id, args.vision_model_id, args.available_pool_mb)
        if args.json:
            print(json.dumps(r, indent=2)); return 0
        if "error" in r:
            print(r["error"]); return 1
        print(f"# text={r['text_model']} vision={r['vision_model']} on {args.available_pool_mb} MB")
        print(f"  consolidated  : {r['consolidated']}")
        print(f"  RECOMMENDED   : {r['recommended']} tokens (constrained by {r['constrained_by']})")
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
