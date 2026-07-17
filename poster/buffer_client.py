"""Minimal Buffer.io GraphQL client (https://api.buffer.com).

Enum spellings below were confirmed against the live schema by introspection
on 2026-07-16 (SchedulingType: notification|automatic; ShareMode: addToQueue|
shareNow|shareNext|customScheduled; Instagram PostType includes reel).

Buffer serves video posts from a PUBLIC HTTPS URL only — there is no upload
endpoint (developers.buffer.com/guides/hosting-media.html) — so callers pass
a media URL produced by media_host.upload_video().
"""
from __future__ import annotations

import requests

API_URL = "https://api.buffer.com"

SHARE_MODES = ("addToQueue", "shareNow", "shareNext")

_CREATE_POST = """
mutation PosterCreatePost($input: CreatePostInput!) {
  createPost(input: $input) {
    __typename
    ... on PostActionSuccess { post { id dueAt } }
    ... on MutationError { message }
  }
}
"""


class BufferAPIError(RuntimeError):
    """API-level failure. retry_after is set when Buffer rate-limited us."""

    def __init__(self, message: str, retry_after: int | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class BufferClient:
    def __init__(self, token: str, timeout: int = 60):
        self.token = token
        self.timeout = timeout

    def gql(self, query: str, variables: dict | None = None) -> dict:
        try:
            r = requests.post(
                API_URL,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.token}",
                },
                json={"query": query, "variables": variables or {}},
                timeout=self.timeout,
            )
        except requests.RequestException as e:
            raise BufferAPIError(f"network error reaching Buffer: {e}") from e
        if r.status_code == 429:
            try:
                retry_after = int(r.headers.get("Retry-After", "60"))
            except ValueError:
                retry_after = 60
            raise BufferAPIError(
                f"Buffer rate limit hit (retry in {retry_after}s)",
                retry_after=retry_after,
            )
        try:
            data = r.json()
        except ValueError:
            raise BufferAPIError(
                f"Buffer returned non-JSON (HTTP {r.status_code}): {r.text[:200]}"
            )
        if data.get("errors"):
            msgs = "; ".join(
                str(e.get("message", e)) for e in data["errors"][:3]
            )
            raise BufferAPIError(f"Buffer GraphQL error: {msgs}")
        return data.get("data") or {}

    # --- queries ---

    def account(self) -> dict:
        """{email, organizations: [{id, name}]}"""
        d = self.gql(
            "query PosterAccount { account { email organizations { id name } } }"
        )
        return d["account"]

    def channels(self, organization_id: str) -> list[dict]:
        d = self.gql(
            """
            query PosterChannels($orgId: OrganizationId!) {
              channels(input: { organizationId: $orgId }) {
                id name displayName service avatar isQueuePaused
              }
            }
            """,
            {"orgId": organization_id},
        )
        return d["channels"]

    # --- mutations ---

    def create_video_post(
        self,
        *,
        channel_id: str,
        service: str,
        text: str,
        video_url: str,
        mode: str = "addToQueue",
        thumbnail_offset_ms: int = 1000,
    ) -> dict:
        """Create ONE post (one clip on one channel). Returns {id, dueAt}.

        service drives the per-network metadata: Instagram video posts are
        published as REELS (owner requirement); TikTok video posts need no
        metadata block (the caption rides in `text`).
        """
        if mode not in SHARE_MODES:
            raise BufferAPIError(f"unsupported share mode: {mode}")
        inp: dict = {
            "channelId": channel_id,
            "text": text,
            "schedulingType": "automatic",
            "mode": mode,
            "assets": [
                {
                    "video": {
                        "url": video_url,
                        "metadata": {"thumbnailOffset": thumbnail_offset_ms},
                    }
                }
            ],
        }
        if service == "instagram":
            inp["metadata"] = {
                "instagram": {"type": "reel", "shouldShareToFeed": True}
            }
        d = self.gql(_CREATE_POST, {"input": inp})
        res = d.get("createPost") or {}
        if res.get("__typename") == "PostActionSuccess":
            return res["post"]
        raise BufferAPIError(
            res.get("message") or f"createPost failed ({res.get('__typename')})"
        )
