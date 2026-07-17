"""Background batch-post worker: one bounded, cancellable thread per batch.

Per clip: stability check (don't ship a file Stage 7 is still rendering) ->
Cloudinary upload -> one createPost per selected channel -> ledger entry.
Statuses stream to the UI via the job dict polled at /api/job.

Bounded by design (owner directive: no zombie background tasks): the thread
walks a fixed item list and exits; every network step has a hard timeout; a
single 429 retry honors Retry-After (capped at 15 min) and then gives up.
"""
from __future__ import annotations

import threading
import time

from . import _state, media_host
from .buffer_client import BufferAPIError, BufferClient

THROTTLE_S = 2.0          # spacing between clips (rate-limit hygiene)
STABILITY_WAIT_S = 1.5    # re-stat gap for the still-being-written guard


def start_batch(clips: list[dict], channels: list[dict], mode: str,
                api_key: str) -> dict:
    """clips: [{name, caption}]; channels: [{id, service, name}]. Raises if a
    batch is already running. Returns the live job dict."""
    job = {
        "id": time.strftime("%Y%m%d_%H%M%S"),
        "state": "running",
        "cancel": False,
        "mode": mode,
        "started": time.time(),
        "finished": None,
        "channels": [
            {"id": c["id"], "service": c.get("service"), "name": c.get("name")}
            for c in channels
        ],
        "items": [
            {
                "name": c["name"],
                "caption": c["caption"],
                "status": "pending",
                "detail": "",
                "posts": [],
            }
            for c in clips
        ],
    }
    with _state.job_lock:
        if _state.current_job and _state.current_job.get("state") == "running":
            raise RuntimeError("a posting batch is already running")
        _state.current_job = job
    threading.Thread(
        target=_run, args=(job, channels, mode, api_key),
        daemon=True, name="buffer-post-batch",
    ).start()
    return job


def cancel_current() -> bool:
    with _state.job_lock:
        job = _state.current_job
        if job and job.get("state") == "running":
            job["cancel"] = True
            return True
    return False


def _run(job: dict, channels: list[dict], mode: str, api_key: str) -> None:
    client = BufferClient(api_key)
    for item in job["items"]:
        if job["cancel"]:
            item["status"] = "cancelled"
            continue
        try:
            self_path = _state.CLIPS_DIR / item["name"]
            if not self_path.exists():
                raise RuntimeError("file not found in clips folder")
            if not _stable(self_path):
                raise RuntimeError(
                    "file size still changing — is the pipeline still "
                    "rendering this clip? Retry once it finishes."
                )
            item["status"] = "uploading"
            up = media_host.upload_video(self_path)
            item["detail"] = f"hosted ({up['bytes'] / 1024 / 1024:.1f} MB)"
            for ch in channels:
                if job["cancel"]:
                    break
                item["status"] = f"posting to {ch.get('service', 'channel')}"
                post = _post_with_retry(
                    client,
                    channel_id=ch["id"],
                    service=ch.get("service", ""),
                    text=item["caption"],
                    video_url=up["secure_url"],
                    mode=mode,
                )
                item["posts"].append({
                    "service": ch.get("service"),
                    "channel": ch.get("name"),
                    "post_id": post.get("id"),
                    "due_at": post.get("dueAt"),
                })
            if job["cancel"] and len(item["posts"]) < len(channels):
                item["status"] = "cancelled"
                item["detail"] = f"stopped after {len(item['posts'])} post(s)"
            else:
                item["status"] = "done"
                item["detail"] = ""
            if item["posts"]:
                _state.record_posted(item["name"], {
                    "posted_at": time.strftime("%Y-%m-%d %H:%M"),
                    "caption": item["caption"],
                    "mode": mode,
                    "posts": item["posts"],
                    "cloudinary_public_id": up.get("public_id"),
                })
        except Exception as e:  # per-clip failure never kills the batch
            item["status"] = "error"
            item["detail"] = str(e)[:300]
            # partial success (e.g. TikTok posted, Instagram failed) still
            # belongs in the ledger so the owner sees what went out
            if item["posts"]:
                _state.record_posted(item["name"], {
                    "posted_at": time.strftime("%Y-%m-%d %H:%M"),
                    "caption": item["caption"],
                    "mode": mode,
                    "posts": item["posts"],
                    "partial": True,
                })
        time.sleep(THROTTLE_S)
    job["state"] = "cancelled" if job["cancel"] else "done"
    job["finished"] = time.time()


def _stable(path) -> bool:
    """True when the file size holds still across a short window (guards
    against uploading a clip the pipeline is mid-render on)."""
    s1 = path.stat().st_size
    time.sleep(STABILITY_WAIT_S)
    return path.stat().st_size == s1 and s1 > 0


def _post_with_retry(client: BufferClient, **kw) -> dict:
    try:
        return client.create_video_post(**kw)
    except BufferAPIError as e:
        if e.retry_after:
            time.sleep(min(e.retry_after, 900))
            return client.create_video_post(**kw)
        raise
