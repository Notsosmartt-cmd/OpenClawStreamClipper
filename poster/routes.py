"""All /api/* routes for the Buffer Clip Poster."""
from __future__ import annotations

import re
import time
from pathlib import Path

from flask import Blueprint, jsonify, request, send_from_directory

from . import _state, media_host, worker
from .buffer_client import SHARE_MODES, BufferAPIError, BufferClient

bp = Blueprint("poster_api", __name__)

CHANNELS_TTL_S = 300

# Caption = the clip's auto-generated title (the filename) minus the trailing
# " (B)" / " (Short)" variant marker the pipeline adds for distinction.
_VARIANT_RE = re.compile(r"\s*\((?:B|Short)\)\s*$", re.IGNORECASE)


def derive_caption(filename: str) -> str:
    return _VARIANT_RE.sub("", Path(filename).stem).strip()


def normalize_hashtags(raw: str) -> str:
    """'fyp, streamer #funny' -> '#fyp #streamer #funny' (deduped, ordered)."""
    tags: list[str] = []
    for tok in re.split(r"[\s,]+", raw or ""):
        tok = tok.strip()
        if not tok:
            continue
        if not tok.startswith("#"):
            tok = "#" + tok
        if len(tok) > 1 and tok not in tags:
            tags.append(tok)
    return " ".join(tags)


def _cloudinary_url_for(entry: dict) -> str | None:
    """Media URL for a ledger entry — stored on new entries; derived from the
    public_id for entries recorded before media_url existed."""
    if entry.get("media_url"):
        return entry["media_url"]
    pid = entry.get("cloudinary_public_id")
    cloud = media_host.cloudinary_cfg().get("cloud_name")
    if pid and cloud:
        return f"https://res.cloudinary.com/{cloud}/video/upload/{pid}.mp4"
    return None


def _client() -> BufferClient:
    key = _state.load_api_key()
    if not key:
        raise BufferAPIError(
            f"Buffer API key file not found ({_state.API_KEY_FILE.name})"
        )
    return BufferClient(key)


def _get_channels(force: bool = False) -> list[dict]:
    now = time.time()
    if (not force and _state.channels_cache is not None
            and now - _state.channels_cache_at < CHANNELS_TTL_S):
        return _state.channels_cache
    client = _client()
    acct = client.account()
    orgs = acct.get("organizations") or []
    if not orgs:
        raise BufferAPIError("Buffer account has no organizations")
    _state.account_cache = {
        "email": acct.get("email"),
        "organization": orgs[0].get("name"),
        "organization_id": orgs[0]["id"],
    }
    _state.channels_cache = client.channels(orgs[0]["id"])
    _state.channels_cache_at = now
    return _state.channels_cache


@bp.route("/api/status")
def api_status():
    """Cheap, network-free snapshot for the UI (never calls Buffer)."""
    return jsonify({
        "key_present": _state.load_api_key() is not None,
        "hosting_configured": media_host.configured(),
        "clips_dir": str(_state.CLIPS_DIR),
        "account": _state.account_cache,
        "job": _state.current_job,
    })


@bp.route("/api/clips")
def api_clips():
    clips = []
    posted = _state.load_posted()
    if _state.CLIPS_DIR.exists():
        for f in sorted(_state.CLIPS_DIR.iterdir(),
                        key=lambda x: x.stat().st_mtime, reverse=True):
            if not f.is_file() or f.suffix.lower() not in _state.VIDEO_EXTS:
                continue
            st = f.stat()
            clips.append({
                "name": f.name,
                "size_mb": round(st.st_size / (1024 * 1024), 1),
                "modified": time.strftime("%Y-%m-%d %H:%M",
                                          time.localtime(st.st_mtime)),
                "caption": derive_caption(f.name),
                "posted": posted.get(f.name),
            })
    return jsonify(clips)


@bp.route("/api/clips/<path:filename>")
def serve_clip(filename):
    return send_from_directory(str(_state.CLIPS_DIR), filename)


@bp.route("/api/channels")
def api_channels():
    try:
        chans = _get_channels(force=request.args.get("refresh") == "1")
    except BufferAPIError as e:
        return jsonify({"error": str(e)}), 502
    return jsonify({"account": _state.account_cache, "channels": chans})


@bp.route("/api/hosting", methods=["GET", "POST"])
def api_hosting():
    if request.method == "GET":
        cfg = media_host.cloudinary_cfg()
        return jsonify({
            "configured": media_host.configured(cfg),
            "cloud_name": cfg.get("cloud_name", ""),
            # secrets stay server-side; the UI only needs to know they exist
            "has_api_key": bool(cfg.get("api_key")),
            "has_api_secret": bool(cfg.get("api_secret")),
        })
    body = request.get_json(silent=True) or {}
    cfg = {
        "cloud_name": (body.get("cloud_name") or "").strip(),
        "api_key": (body.get("api_key") or "").strip(),
        "api_secret": (body.get("api_secret") or "").strip(),
    }
    ok, msg = media_host.verify_credentials(cfg)
    if not ok:
        return jsonify({"ok": False, "message": msg}), 400
    stored = _state.load_config()
    stored["cloudinary"] = cfg
    _state.save_config(stored)
    return jsonify({"ok": True, "message": msg})


@bp.route("/api/post", methods=["POST"])
def api_post():
    body = request.get_json(silent=True) or {}
    mode = body.get("mode") or "addToQueue"
    if mode not in SHARE_MODES:
        return jsonify({"error": f"mode must be one of {SHARE_MODES}"}), 400
    key = _state.load_api_key()
    if not key:
        return jsonify({"error": "Buffer API key file missing"}), 400
    if not media_host.configured():
        return jsonify({"error": "media hosting not configured yet"}), 400

    try:
        all_channels = {c["id"]: c for c in _get_channels()}
    except BufferAPIError as e:
        return jsonify({"error": str(e)}), 502
    channel_ids = body.get("channel_ids") or []
    channels = [all_channels[cid] for cid in channel_ids if cid in all_channels]
    if not channels:
        return jsonify({"error": "no valid channels selected"}), 400

    hashtags = normalize_hashtags(body.get("hashtags") or "")
    clips_in = body.get("clips") or []
    clips = []
    for c in clips_in:
        name = (c.get("name") or "").strip()
        if not name or not (_state.CLIPS_DIR / name).is_file():
            return jsonify({"error": f"clip not found: {name or '(empty)'}"}), 400
        caption = (c.get("caption") or "").strip() or derive_caption(name)
        if hashtags:
            caption = f"{caption}\n\n{hashtags}"
        clips.append({"name": name, "caption": caption})
    if not clips:
        return jsonify({"error": "no clips selected"}), 400

    org_id = (_state.account_cache or {}).get("organization_id")
    try:
        job = worker.start_batch(clips, channels, mode, key, org_id)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 409
    return jsonify(job)


@bp.route("/api/retry", methods=["POST"])
def api_retry():
    """Re-post every ledger post whose verified status is 'error' (one item
    per failed clip+channel pair; the hosted Cloudinary asset is reused, so
    no re-upload)."""
    key = _state.load_api_key()
    if not key:
        return jsonify({"error": "Buffer API key file missing"}), 400
    try:
        channels = _get_channels()
    except BufferAPIError as e:
        return jsonify({"error": str(e)}), 502
    by_service = {c.get("service"): c for c in channels}

    pairs = []
    for name, entry in _state.load_posted().items():
        for p in entry.get("posts", []):
            if p.get("status") != "error":
                continue
            ch = by_service.get(p.get("service"))
            url = _cloudinary_url_for(entry)
            if not ch or not url:
                continue
            pairs.append({
                "name": name,
                "caption": entry.get("caption") or derive_caption(name),
                "mode": entry.get("mode") if entry.get("mode") in SHARE_MODES
                        else "shareNow",
                "media_url": url,
                "channel": ch,
            })
    if not pairs:
        return jsonify({"error": "no failed posts to retry"}), 400

    org_id = (_state.account_cache or {}).get("organization_id")
    try:
        job = worker.start_retry(pairs, key, org_id)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 409
    return jsonify(job)


@bp.route("/api/job")
def api_job():
    return jsonify(_state.current_job or {"state": "idle"})


@bp.route("/api/job/cancel", methods=["POST"])
def api_job_cancel():
    return jsonify({"cancelled": worker.cancel_current()})
