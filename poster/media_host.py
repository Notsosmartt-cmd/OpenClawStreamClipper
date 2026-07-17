"""Media hosting for Buffer posts — Cloudinary signed uploads.

Buffer's API cannot accept file uploads; assets must sit at a public, direct,
stable HTTPS URL until the post publishes (their docs explicitly warn against
expiring/pre-signed links and recommend Cloudinary/R2). Cloudinary's free
tier fits this project's short-form clips, and the delivery URL
(res.cloudinary.com/...) is permanent — safe even for addToQueue posts that
publish hours later. Uploaded assets are therefore NOT auto-deleted.

Uses the plain REST upload API (signed) — no SDK dependency. Credentials live
in config/buffer_poster.json (gitignored), entered once via the Setup panel.

Free-plan limit worth knowing: single video files cap at 100 MB.
"""
from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path

import requests

from . import _state

MAX_VIDEO_BYTES = 98 * 1024 * 1024  # stay under Cloudinary's 100 MB free cap
UPLOAD_FOLDER = "openclaw_poster"


def cloudinary_cfg() -> dict:
    return _state.load_config().get("cloudinary") or {}


def configured(cfg: dict | None = None) -> bool:
    cfg = cfg if cfg is not None else cloudinary_cfg()
    return all(cfg.get(k) for k in ("cloud_name", "api_key", "api_secret"))


def verify_credentials(cfg: dict | None = None) -> tuple[bool, str]:
    """Read-only credential check against the Admin API /usage endpoint."""
    cfg = cfg if cfg is not None else cloudinary_cfg()
    if not configured(cfg):
        return False, "cloud name, API key and API secret are all required"
    try:
        r = requests.get(
            f"https://api.cloudinary.com/v1_1/{cfg['cloud_name']}/usage",
            auth=(cfg["api_key"], cfg["api_secret"]),
            timeout=20,
        )
    except requests.RequestException as e:
        return False, f"network error reaching Cloudinary: {e}"
    if r.status_code == 200:
        try:
            plan = r.json().get("plan", "?")
        except ValueError:
            plan = "?"
        return True, f"credentials OK (plan: {plan})"
    if r.status_code == 401:
        return False, "Cloudinary rejected the credentials (401) — check key/secret"
    if r.status_code == 404:
        return False, f"cloud name '{cfg['cloud_name']}' not found (404)"
    return False, f"Cloudinary returned HTTP {r.status_code}"


def _signature(params: dict, secret: str) -> str:
    """Cloudinary signed-upload signature: sha1 of the sorted params + secret."""
    to_sign = "&".join(f"{k}={params[k]}" for k in sorted(params))
    return hashlib.sha1((to_sign + secret).encode("utf-8")).hexdigest()


def upload_video(path: Path) -> dict:
    """Upload one clip; returns {secure_url, public_id, bytes}."""
    cfg = cloudinary_cfg()
    if not configured(cfg):
        raise RuntimeError("media hosting not configured (Setup panel)")
    size = path.stat().st_size
    if size > MAX_VIDEO_BYTES:
        raise RuntimeError(
            f"{path.name} is {size / 1024 / 1024:.0f} MB — over Cloudinary's "
            "100 MB free-plan cap for a single video"
        )
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", path.stem).strip("_")[:80] or "clip"
    params = {
        "folder": UPLOAD_FOLDER,
        # timestamp suffix keeps re-posts of the same file from overwriting
        "public_id": f"{stem}_{int(time.time())}",
        "timestamp": str(int(time.time())),
    }
    data = dict(params)
    data["api_key"] = cfg["api_key"]
    data["signature"] = _signature(params, cfg["api_secret"])
    with open(path, "rb") as f:
        try:
            r = requests.post(
                f"https://api.cloudinary.com/v1_1/{cfg['cloud_name']}/video/upload",
                data=data,
                files={"file": (path.name, f)},
                timeout=(30, 900),
            )
        except requests.RequestException as e:
            raise RuntimeError(f"upload failed (network): {e}") from e
    try:
        body = r.json()
    except ValueError:
        raise RuntimeError(f"Cloudinary returned non-JSON (HTTP {r.status_code})")
    if r.status_code != 200 or "secure_url" not in body:
        msg = (body.get("error") or {}).get("message") or f"HTTP {r.status_code}"
        raise RuntimeError(f"Cloudinary upload failed: {msg}")
    return {
        "secure_url": body["secure_url"],
        "public_id": body.get("public_id"),
        "bytes": body.get("bytes", size),
    }
