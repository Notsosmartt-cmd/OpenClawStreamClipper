"""Background batch-post worker: one bounded, cancellable thread per batch.

Rate-limit protection (2026-07-16, after the 112-clip TikTok lockout):
- Per-channel DAILY caps are enforced pre-call from Buffer's live
  dailyPostingLimits (network caps: TikTok 25, Instagram 50 per rolling 24 h —
  identical on every Buffer plan). Immediate posts beyond a channel's
  remaining quota are recorded as ``skipped_cap`` without an API call.
- TikTok velocity guard: TikTok's OWN anti-spam locks a channel for ~24 h
  after a burst of rapid API posts (measured: 6 accepted at ~25 s spacing,
  then 100 straight refusals). Immediate modes therefore allow only a small
  per-batch burst per channel; overflow is auto-converted to spaced
  ``customScheduled`` posts Buffer publishes on time — no local pacing thread.
- Drip mode schedules EVERY post at a fixed spacing (safe bulk).
- Creation-call throttle stretches for big batches so post creation itself
  stays under Buffer's 100-requests/15-min API window.

Per clip: stability check (don't ship a file Stage 7 is still rendering) ->
Cloudinary upload (skipped on retries — the asset is already hosted) -> one
createPost per target channel -> ledger entry. Then a VERIFICATION phase:
createPost success only means Buffer ACCEPTED the post — immediate publishes
happen async and can fail minutes later, so we poll until each is
``sent``/``error``. Scheduled posts are not polled (use Refresh statuses).

Bounded by design: fixed item list, hard request timeouts, one 429 retry
(Retry-After capped 15 min), verification capped at VERIFY_ROUNDS.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

from . import _state, media_host
from .buffer_client import BufferAPIError, BufferClient

STABILITY_WAIT_S = 1.5     # re-stat gap for the still-being-written guard
VERIFY_ROUNDS = 10         # x VERIFY_INTERVAL_S ≈ 3.5 min max verification
VERIFY_INTERVAL_S = 20
TERMINAL = {"sent", "error"}

# Velocity guard defaults (override via config/buffer_poster.json "rate_guard":
# {"tiktok_burst": N, "default_burst": N, "auto_spacing_min": N}).
TIKTOK_BURST_MAX = 3       # immediate posts per batch before auto-scheduling
DEFAULT_BURST_MAX = 10     # non-TikTok networks tolerated bursts fine (IG 6/6)
AUTO_SPACING_MIN = 90      # spacing for auto-converted overflow (16/day < caps)
FIRST_DUE_DELAY_S = 120    # first scheduled slot: now + 2 min


def _guard_cfg() -> dict:
    cfg = _state.load_config().get("rate_guard") or {}
    return {
        "tiktok_burst": int(cfg.get("tiktok_burst", TIKTOK_BURST_MAX)),
        "default_burst": int(cfg.get("default_burst", DEFAULT_BURST_MAX)),
        "auto_spacing_s": int(cfg.get("auto_spacing_min", AUTO_SPACING_MIN)) * 60,
    }


def start_batch(clips: list[dict], channels: list[dict], mode: str,
                api_key: str, organization_id: str | None,
                limits: dict | None = None,
                drip_spacing_s: int | None = None) -> dict:
    """clips: [{name, caption}]; channels applied to every clip. limits is
    {channel_id: {remaining, ...}} from dailyPostingLimits (None = no gate).
    mode "drip" schedules everything drip_spacing_s apart."""
    items = [
        {
            "name": c["name"],
            "path": c.get("path"),   # resolved by routes (clips/ or posted_clips/)
            "caption": c["caption"],
            "mode": mode,
            "channels": channels,
            "media_url": None,
            "retry": False,
            "status": "pending",
            "detail": "",
            "posts": [],
        }
        for c in clips
    ]
    return _launch(items, api_key, organization_id, mode, limits,
                   drip_spacing_s)


def start_retry(pairs: list[dict], api_key: str,
                organization_id: str | None,
                limits: dict | None = None) -> dict:
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
    return _launch(items, api_key, organization_id, "retry", limits, None)


def _launch(items: list[dict], api_key: str, organization_id: str | None,
            mode: str, limits: dict | None,
            drip_spacing_s: int | None) -> dict:
    total_posts = sum(len(i["channels"]) for i in items)
    job = {
        "id": time.strftime("%Y%m%d_%H%M%S"),
        "state": "running",
        "cancel": False,
        "mode": mode,
        "started": time.time(),
        "finished": None,
        "items": items,
        # stretch creation calls for big batches: Buffer allows 100 req/15 min
        "throttle_s": 2.0 if total_posts <= 80 else 12.0,
    }
    with _state.job_lock:
        cur = _state.current_job
        if cur and cur.get("state") in ("running", "verifying"):
            raise RuntimeError("a posting batch is already running")
        _state.current_job = job
    threading.Thread(
        target=_run, args=(job, api_key, organization_id, limits,
                           drip_spacing_s),
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


class _ChannelGate:
    """Per-channel posting plan: daily-cap countdown, burst budget, and the
    scheduled-slot chain for overflow/drip posts."""

    def __init__(self, channel: dict, limits: dict | None, guard: dict,
                 drip_spacing_s: int | None):
        self.service = channel.get("service") or ""
        lim = (limits or {}).get(channel["id"]) or {}
        self.remaining = lim.get("remaining")          # None = unknown/no gate
        burst_key = "tiktok_burst" if self.service == "tiktok" else "default_burst"
        self.burst_left = guard[burst_key]
        self.spacing_s = drip_spacing_s or guard["auto_spacing_s"]
        self.next_due = time.time() + FIRST_DUE_DELAY_S
        self.drip_all = drip_spacing_s is not None

    def plan(self, mode: str) -> tuple[str, str | None]:
        """-> ("immediate", None) | ("scheduled", due_iso) | ("skip_cap", None)"""
        if self.drip_all:
            return "scheduled", self._take_slot()
        if self.remaining is not None and self.remaining <= 0:
            return "skip_cap", None
        if self.burst_left <= 0:
            return "scheduled", self._take_slot()
        return "immediate", None

    def consumed(self, action: str) -> None:
        if action == "immediate":
            self.burst_left -= 1
            if self.remaining is not None:
                self.remaining -= 1

    def _take_slot(self) -> str:
        due = self.next_due
        self.next_due += self.spacing_s
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(due))


def _run(job: dict, api_key: str, organization_id: str | None,
         limits: dict | None, drip_spacing_s: int | None) -> None:
    client = BufferClient(api_key)
    guard = _guard_cfg()
    gates: dict[str, _ChannelGate] = {}
    for item in job["items"]:
        for ch in item["channels"]:
            if ch["id"] not in gates:
                gates[ch["id"]] = _ChannelGate(ch, limits, guard,
                                               drip_spacing_s)
    for item in job["items"]:
        if job["cancel"]:
            item["status"] = "cancelled"
            continue
        try:
            if not item["media_url"]:
                path = (Path(item["path"]) if item.get("path")
                        else _state.resolve_clip_path(item["name"]))
                if path is None or not path.exists():
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
                gate = gates[ch["id"]]
                action, due_iso = gate.plan(item["mode"])
                svc = ch.get("service", "channel")
                if action == "skip_cap":
                    item["posts"].append({
                        "service": ch.get("service"),
                        "channel": ch.get("name"),
                        "post_id": None,
                        "status": "skipped_cap",
                        "error": f"{svc} daily posting cap reached — "
                                 "retry tomorrow (Retry failed includes these)",
                    })
                    continue
                item["status"] = (f"posting to {svc}" if action == "immediate"
                                  else f"scheduling on {svc}")
                post = _post_with_retry(
                    client,
                    channel_id=ch["id"],
                    service=ch.get("service", ""),
                    text=item["caption"],
                    video_url=item["media_url"],
                    mode=("customScheduled" if action == "scheduled"
                          else item["mode"]),
                    due_at=due_iso,
                )
                gate.consumed(action)
                item["posts"].append({
                    "service": ch.get("service"),
                    "channel": ch.get("name"),
                    "post_id": post.get("id"),
                    "due_at": post.get("dueAt") or due_iso,
                    "status": ("accepted" if action == "immediate"
                               else "scheduled"),
                })
            if job["cancel"] and len(item["posts"]) < len(item["channels"]):
                item["status"] = "cancelled"
                item["detail"] = f"stopped after {len(item['posts'])} post(s)"
            else:
                item["status"] = "done"
                skips = [p for p in item["posts"]
                         if p["status"] == "skipped_cap"]
                if skips:
                    item["detail"] = (f"{len(skips)} post(s) skipped — "
                                      "daily cap")
            _record(item)
        except Exception as e:  # per-clip failure never kills the batch
            item["status"] = "error"
            item["detail"] = str(e)[:300]
            _record(item)       # partial successes still belong in the ledger
        time.sleep(job["throttle_s"])
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
    """Poll Buffer until every IMMEDIATE post reaches sent/error (or the
    round cap). Scheduled posts publish later — the Refresh-statuses button
    re-checks them on demand. One list call per round regardless of size."""
    tracked = [(item, p) for item in job["items"] for p in item["posts"]
               if p.get("post_id") and p.get("status") == "accepted"]
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
    # fully-sent clips graduate to posted_clips/ (strict: every post 'sent')
    job["moved"] = _state.sweep_posted_clips()


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
