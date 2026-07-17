"""Background batch-post worker: one bounded, cancellable thread per batch.

Per clip: stability check (don't ship a file Stage 7 is still rendering) ->
Cloudinary upload (skipped on retries — the asset is already hosted) -> one
createPost per target channel -> ledger entry. Then a VERIFICATION phase:
createPost success only means Buffer ACCEPTED the post — publishing to the
networks is async and can fail minutes later (proven 2026-07-16: a TikTok
post errored server-side while the UI said "posted"). We poll the real post
statuses until every one is 'sent' or 'error', and surface failures for the
Retry button.

Bounded by design (owner directive: no zombie background tasks): fixed item
list, hard request timeouts, one 429 retry (Retry-After capped 15 min), and
a verification loop capped at VERIFY_ROUNDS.
"""
from __future__ import annotations

import threading
import time

from . import _state, media_host
from .buffer_client import BufferAPIError, BufferClient

THROTTLE_S = 2.0           # spacing between clips (rate-limit hygiene)
STABILITY_WAIT_S = 1.5     # re-stat gap for the still-being-written guard
VERIFY_ROUNDS = 10         # x VERIFY_INTERVAL_S ≈ 3.5 min max verification
VERIFY_INTERVAL_S = 20
TERMINAL = {"sent", "error"}


def start_batch(clips: list[dict], channels: list[dict], mode: str,
                api_key: str, organization_id: str | None) -> dict:
    """clips: [{name, caption}]; channels: [{id, service, name}] applied to
    every clip. Raises if a batch is already running."""
    items = [
        {
            "name": c["name"],
            "caption": c["caption"],
            "mode": mode,
            "channels": channels,
            "media_url": None,      # filled by upload
            "retry": False,
            "status": "pending",
            "detail": "",
            "posts": [],
        }
        for c in clips
    ]
    return _launch(items, api_key, organization_id, mode)


def start_retry(pairs: list[dict], api_key: str,
                organization_id: str | None) -> dict:
    """pairs: [{name, caption, mode, media_url, channel}] — one failed
    clip+channel post each; upload is skipped (asset already hosted)."""
    items = [
        {
            "name": p["name"],
            "caption": p["caption"],
            "mode": p.get("mode") or "shareNow",
            "channels": [p["channel"]],
            "media_url": p["media_url"],
            "retry": True,
            "status": "pending",
            "detail": f"retry → {p['channel'].get('service')}",
            "posts": [],
        }
        for p in pairs
    ]
    return _launch(items, api_key, organization_id, "retry")


def _launch(items: list[dict], api_key: str, organization_id: str | None,
            mode: str) -> dict:
    job = {
        "id": time.strftime("%Y%m%d_%H%M%S"),
        "state": "running",
        "cancel": False,
        "mode": mode,
        "started": time.time(),
        "finished": None,
        "items": items,
    }
    with _state.job_lock:
        cur = _state.current_job
        if cur and cur.get("state") in ("running", "verifying"):
            raise RuntimeError("a posting batch is already running")
        _state.current_job = job
    threading.Thread(
        target=_run, args=(job, api_key, organization_id),
        daemon=True, name="buffer-post-batch",
    ).start()
    return job


def cancel_current() -> bool:
    with _state.job_lock:
        job = _state.current_job
        if job and job.get("state") in ("running", "verifying"):
            job["cancel"] = True
            return True
    return False


def _run(job: dict, api_key: str, organization_id: str | None) -> None:
    client = BufferClient(api_key)
    for item in job["items"]:
        if job["cancel"]:
            item["status"] = "cancelled"
            continue
        try:
            if not item["media_url"]:
                path = _state.CLIPS_DIR / item["name"]
                if not path.exists():
                    raise RuntimeError("file not found in clips folder")
                if not _stable(path):
                    raise RuntimeError(
                        "file size still changing — is the pipeline still "
                        "rendering this clip? Retry once it finishes."
                    )
                item["status"] = "uploading"
                up = media_host.upload_video(path)
                item["media_url"] = up["secure_url"]
                item["cloudinary_public_id"] = up.get("public_id")
            for ch in item["channels"]:
                if job["cancel"]:
                    break
                item["status"] = f"posting to {ch.get('service', 'channel')}"
                post = _post_with_retry(
                    client,
                    channel_id=ch["id"],
                    service=ch.get("service", ""),
                    text=item["caption"],
                    video_url=item["media_url"],
                    mode=item["mode"],
                )
                item["posts"].append({
                    "service": ch.get("service"),
                    "channel": ch.get("name"),
                    "post_id": post.get("id"),
                    "due_at": post.get("dueAt"),
                    "status": "accepted",   # refined by verification
                })
            if job["cancel"] and len(item["posts"]) < len(item["channels"]):
                item["status"] = "cancelled"
                item["detail"] = f"stopped after {len(item['posts'])} post(s)"
            else:
                item["status"] = "done"
            _record(item)
        except Exception as e:  # per-clip failure never kills the batch
            item["status"] = "error"
            item["detail"] = str(e)[:300]
            _record(item)       # partial successes still belong in the ledger
        time.sleep(THROTTLE_S)
    _verify(job, client, organization_id)
    job["state"] = "cancelled" if job["cancel"] else "done"
    job["finished"] = time.time()


def _record(item: dict) -> None:
    if not item["posts"]:
        return
    if item["retry"]:
        _state.merge_posted_posts(item["name"], item["posts"])
        return
    _state.record_posted(item["name"], {
        "posted_at": time.strftime("%Y-%m-%d %H:%M"),
        "caption": item["caption"],
        "mode": item["mode"],
        "posts": item["posts"],
        "media_url": item.get("media_url"),
        "cloudinary_public_id": item.get("cloudinary_public_id"),
        "partial": item["status"] == "error",
    })


def _verify(job: dict, client: BufferClient,
            organization_id: str | None) -> None:
    """Poll Buffer until every created post reaches sent/error (or the
    round cap). One list call per round regardless of batch size."""
    tracked = [(item, p) for item in job["items"]
               for p in item["posts"] if p.get("post_id")]
    if not tracked or not organization_id:
        return
    job["state"] = "verifying"
    for _ in range(VERIFY_ROUNDS):
        if job["cancel"]:
            return
        time.sleep(VERIFY_INTERVAL_S)   # publishing typically takes 1-3 min
        pending_ids = [p["post_id"] for _, p in tracked
                       if p.get("status") not in TERMINAL]
        if not pending_ids:
            break
        try:
            statuses = client.posts_status(organization_id, pending_ids)
        except BufferAPIError:
            continue                     # transient — next round retries
        for item, p in tracked:
            st = statuses.get(p.get("post_id"))
            if not st:
                continue
            p["status"] = st["status"]
            p["sent_at"] = st["sent_at"]
            if st["error"]:
                p["error"] = st["error"]
    for item in job["items"]:
        if not item["posts"]:
            continue
        if item["retry"]:
            _state.merge_posted_posts(item["name"], item["posts"])
        else:
            _state.update_posted_posts(item["name"], item["posts"])


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
