#!/usr/bin/env python3
"""Central think / no-think control for every LM Studio call in the pipeline.

Background (BUG 67, verified 2026-07-09): thinking control is MODEL-DEPENDENT.
`chat_template_kwargs.enable_thinking` is the real llama.cpp/LM Studio lever and
takes priority over server defaults FOR MODELS WHOSE CHAT TEMPLATE READS IT:
  * qwen/qwen3.6-35b-a3b — obeys it (0 reasoning tokens when False). Reliable.
  * google/gemma-4-26b-a4b-qat — IGNORES it entirely (~200 reasoning tokens either
    way); only a per-model chat-TEMPLATE edit in the LM Studio UI stops it.

So this toggle reliably controls the OFF direction on compliant models (the pipeline's
long-standing default) and is best-effort for ON (that direction is additionally gated
by the model-level config, so enabling here may not suffice for some models). It does
NOT and CANNOT force a permanent-reasoning model (gemma) off — the fail-fast guardrail
(`common.preflight_thinking`) catches those before a run wedges.

`CLIP_ENABLE_THINKING` (default off) is set by the dashboard checkbox / CLI env and is
read fresh here. Zero imports beyond `os` so this always resolves in every stage
subprocess (scripts/lib is on PYTHONPATH via child_env)."""
from __future__ import annotations

import os

_TRUTHY = ("1", "true", "yes", "on")


def enabled() -> bool:
    """Whether the pipeline should LET models reason. Default False (no <think>)."""
    return os.environ.get("CLIP_ENABLE_THINKING", "").strip().lower() in _TRUTHY


def template_kwargs() -> dict:
    """The `chat_template_kwargs` block every LLM payload should send. When thinking
    is off (default) this is `{"enable_thinking": False}` — the exact literal every
    call site used to hardcode, so default behavior is byte-identical."""
    return {"enable_thinking": enabled()}


def no_think_prefix() -> str:
    """Optional `/no_think` sentinel for prompt text. Belt-and-suspenders for the OFF
    direction on Qwen; '' when thinking is on. (Proven no-op on the outcome for the
    tested models, so call sites may keep their literal prefix — provided for new code.)"""
    return "" if enabled() else "/no_think\n"
