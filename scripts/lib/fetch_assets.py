#!/usr/bin/env python3
"""User-runnable helper to pre-download Whisper + Piper assets.

Since the Docker image no longer bakes model weights in (they sit on the
host under ``./models/``), this script lets the user or the dashboard
pre-populate those caches without waiting for the pipeline's first run.

Usage (inside the container):

    python3 /root/scripts/lib/fetch_assets.py status          # show cache state
    python3 /root/scripts/lib/fetch_assets.py whisper large-v3
    python3 /root/scripts/lib/fetch_assets.py piper en_US-amy-low
    python3 /root/scripts/lib/fetch_assets.py piper en_US-ryan-high

The dashboard ``POST /api/assets/fetch`` endpoint wraps this.

Output is JSON-serializable plain text so the dashboard can parse results.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

WHISPER_DIR = Path(os.environ.get("WHISPER_MODEL_DIR", "/root/.cache/whisper-models"))
PIPER_DIR = Path(os.environ.get("PIPER_VOICE_DIR", "/root/.cache/piper"))


def dir_size_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            continue
    return total


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def list_whisper_models() -> list[dict]:
    if not WHISPER_DIR.exists():
        return []
    out = []
    for entry in sorted(WHISPER_DIR.iterdir()):
        if not entry.is_dir():
            continue
        name = entry.name
        # faster-whisper caches models under "models--<org>--<name>"
        if name.startswith("models--"):
            friendly = name.split("--")[-1]
            out.append({
                "name": friendly,
                "path": str(entry),
                "size_bytes": dir_size_bytes(entry),
            })
    return out


def list_piper_voices() -> list[dict]:
    if not PIPER_DIR.exists():
        return []
    seen: dict[str, dict] = {}
    for p in sorted(PIPER_DIR.rglob("*.onnx")):
        voice = p.stem
        meta_json = p.with_suffix(".onnx.json")
        size = p.stat().st_size + (meta_json.stat().st_size if meta_json.is_file() else 0)
        seen[voice] = {
            "name": voice,
            "path": str(p),
            "size_bytes": size,
            "has_meta": meta_json.is_file(),
        }
    return list(seen.values())


def status() -> dict:
    whisper = list_whisper_models()
    piper = list_piper_voices()
    return {
        "whisper": {
            "dir": str(WHISPER_DIR),
            "models": whisper,
            "total_size_bytes": sum(m["size_bytes"] for m in whisper),
        },
        "piper": {
            "dir": str(PIPER_DIR),
            "voices": piper,
            "total_size_bytes": sum(v["size_bytes"] for v in piper),
        },
    }


def fetch_whisper(model: str) -> dict:
    WHISPER_DIR.mkdir(parents=True, exist_ok=True)
    try:
        # faster-whisper downloads to download_root if missing.
        # CPU int8 loads fast and uses the same cache layout as CUDA.
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError:
        return {"ok": False, "error": "faster-whisper not installed in this image"}

    try:
        WhisperModel(model, device="cpu", compute_type="int8",
                     download_root=str(WHISPER_DIR))
    except Exception as e:
        return {"ok": False, "error": f"download failed: {e}"}

    # Size check
    size = sum(
        m["size_bytes"] for m in list_whisper_models() if model in m["name"]
    )
    return {"ok": True, "model": model, "size_bytes": size,
            "size_human": human(size)}


PIPER_HF_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0"


def fetch_piper(voice: str) -> dict:
    """Pull a Piper voice ONNX + metadata JSON into PIPER_DIR.

    Tries the Python package's downloader first (most reliable); falls back
    to wget against the Hugging Face v1.0.0 tag. Names follow the
    ``<lang>_<REGION>-<speaker>-<quality>`` convention used by piper-voices.
    """
    PIPER_DIR.mkdir(parents=True, exist_ok=True)

    # Attempt 1 — piper.download_voices CLI
    try:
        proc = subprocess.run(
            ["python3", "-m", "piper.download_voices", voice,
             "--data-dir", str(PIPER_DIR)],
            capture_output=True, text=True, timeout=180,
        )
        if proc.returncode == 0:
            return {"ok": True, "voice": voice, "method": "piper.download_voices"}
    except FileNotFoundError:
        pass
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "piper.download_voices timed out after 180 s"}

    # Attempt 2 — direct HF URL. Parse the voice id to build the path.
    try:
        lang_region, speaker, quality = voice.split("-", 2)
        lang = lang_region.split("_")[0]
    except ValueError:
        return {"ok": False, "error":
                f"voice '{voice}' doesn't look like <lang>_<REGION>-<speaker>-<quality>"}

    urls = [
        f"{PIPER_HF_BASE}/{lang}/{lang_region}/{speaker}/{quality}/{voice}.onnx",
        f"{PIPER_HF_BASE}/{lang}/{lang_region}/{speaker}/{quality}/{voice}.onnx.json",
    ]
    for url in urls:
        target = PIPER_DIR / url.rsplit("/", 1)[-1]
        rc = subprocess.call(["wget", "-q", "-O", str(target), url])
        if rc != 0 or target.stat().st_size < 512:
            target.unlink(missing_ok=True)
            return {"ok": False, "error": f"wget failed for {url}"}

    return {"ok": True, "voice": voice, "method": "huggingface fallback"}


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status")

    p_w = sub.add_parser("whisper")
    p_w.add_argument("model", default="large-v3", nargs="?")

    p_p = sub.add_parser("piper")
    p_p.add_argument("voice", default="en_US-amy-low", nargs="?")

    args = parser.parse_args()

    if args.cmd == "status":
        print(json.dumps(status(), indent=2))
        return 0
    if args.cmd == "whisper":
        result = fetch_whisper(args.model)
        print(json.dumps(result))
        return 0 if result.get("ok") else 1
    if args.cmd == "piper":
        result = fetch_piper(args.voice)
        print(json.dumps(result))
        return 0 if result.get("ok") else 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
