#!/usr/bin/env python3
"""Minimal LM Studio client used by the grounding cascade's LLM judge.

Intentionally small — does ONE job (POST a prompt, parse the reply, strip
think tags / handle the reasoning_content fallback). The inlined LLM
callers in stage modules (Stage 3 classify, Pass B ``call_llm``, Stage 6
``_vision_call``) each have their own tuned retry / token-budget / parse
logic and are NOT routed through this module — too much blast radius to
unify. This wrapper exists so ``grounding.llm_judge`` doesn't have to
reimplement HTTP + JSON extraction + reasoning_content fallback.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from typing import Optional

DEFAULT_URL = os.environ.get("CLIP_LLM_URL", "http://host.docker.internal:1234")


def chat(
    prompt: str,
    model: str,
    url: str = DEFAULT_URL,
    timeout: float = 45.0,
    temperature: float = 0.0,
    max_tokens: int = 800,
    response_json: bool = False,
) -> Optional[str]:
    """POST to /v1/chat/completions and return the cleaned assistant text.

    Returns ``None`` on any network, HTTP, or protocol failure so callers
    can degrade (the grounding judge falls back to the Tier 1 verdict on a
    None reply). Strips ``<think>...</think>`` tags, and falls back to
    ``reasoning_content`` when Qwen3.5-35B stashes its answer there
    instead of ``content``.

    The ``response_json`` parameter is currently a no-op (kept for API
    compatibility). Pre-2026-04-27 we forwarded it as
    ``response_format: {type: json_object}`` — but LM Studio's llama.cpp /
    mlx backend rejects that field with HTTP 400 for several non-Qwen
    models (notably Gemma-4 26B), and the rejection floods logs while
    silently disabling the judge. Callers already have a robust freeform-
    JSON extractor (`text.find("{")` / `rfind("}")` + `json.loads`), so
    dropping the hint degrades cleanly. See [[concepts/bugs-and-fixes#BUG 33]].
    """
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    # response_format intentionally omitted — see docstring.
    _ = response_json  # kept for back-compat; documented as no-op.

    data = json.dumps(payload).encode()
    try:
        req = urllib.request.Request(
            f"{url.rstrip('/')}/v1/chat/completions",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, TimeoutError) as e:
        print(f"[LMSTUDIO] call failed: {e}", file=sys.stderr)
        return None
    except Exception as e:  # noqa: BLE001 — we genuinely want to swallow here
        print(f"[LMSTUDIO] unexpected error: {e}", file=sys.stderr)
        return None

    try:
        msg = result["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        return None

    content = msg.get("content") or ""
    if not content:
        content = str(msg.get("reasoning_content") or "")
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
    return content or None


def _cli() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="Tiny LM Studio client (smoke test)")
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--json", action="store_true", help="kept for back-compat; no longer forwarded (BUG 33)")
    ap.add_argument("--timeout", type=float, default=45.0)
    args = ap.parse_args()
    out = chat(
        args.prompt,
        model=args.model,
        url=args.url,
        timeout=args.timeout,
        response_json=args.json,
    )
    print(out if out is not None else "<no response>")


if __name__ == "__main__":
    _cli()
