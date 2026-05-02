---
title: "Bugs and Fixes"
type: concept
tags: [bugs, fixes, debugging, history, hub, reference]
sources: 3
updated: 2026-05-01
---

# Bugs and Fixes

Known bugs encountered during development and how they were resolved. Useful for debugging similar symptoms.

---

## Quick-nav index

### Infrastructure / Docker
| # | Title |
|---|---|
| BUG 1 | Pipeline not reclipping after rebuild — `processed.log` full, use `--force` |
| BUG 2 | PowerShell breaks `2>/dev/null` — wrap in `bash -c "..."` |
| BUG 4 | Docker build uploads 32GB — missing `.dockerignore` |
| BUG 11 | `apt-get` fails during Docker build on Windows/WSL2 — add retry/timeout config |
| BUG 12 | Mixed mode falls back to CPU — `OLLAMA_VULKAN=1` missing |
| BUG 13 | `vulkaninfo` not found in container — missing `vulkan-tools` package |
| BUG 14 | Vulkan silently falls back to CPU — ICD init failure; now auto-detected |
| BUG 18 | Pipeline logs lost after EXIT cleanup — added persistent timestamped log |
| BUG 31 | Docker Desktop named pipe 500 kills `docker exec` mid-Pass-B — detached pipeline lifecycle |
| BUG 32 | Container loses `host.docker.internal` route mid-run — Pass B / Stage 6 fail-fast after 3 consecutive ENETUNREACH |

### Dashboard
| # | Title |
|---|---|
| BUG 3 | Dashboard JSON parsing error — Flask returning HTML, not JSON |
| BUG 5 | `os.setsid` AttributeError on Windows — Linux-only syscall |
| BUG 6 | Dashboard can't see VODs — wrong path (`dashboard/vods/` vs project root) |
| BUG 7 | `processed.log` UnicodeDecodeError — UTF-16 BOM |
| BUG 8 | Pipeline doesn't start from dashboard — must use `docker exec`, not local bash |
| BUG 10 | Docker dashboard zombie process — Flask crash, no listener on port 5000 |
| BUG 16 | LM Studio `/v1/models` flooded by status polls — added 30 s cache |

### LLM / Model Integration
| # | Title |
|---|---|
| BUG 15 | Qwen3.5 reasoning model returns `content: ""` — use `chat_template_kwargs` |
| BUG 17 | 35B+ `chat_template_kwargs` ignored — answer in `reasoning_content` |
| BUG 19 | LM Studio queue backup from short timeouts — cascading abandonment |
| BUG 20 | 35B-A3B token exhaustion: thinking consumes all `max_tokens` |
| BUG 21 | Stage 3 `max_tokens=1024` silent misclassification — all segments → `just_chatting` |
| BUG 30 | HTTP 400 kills all Pass B + Stage 6 with Gemma 4 — `response_format` unsupported |
| BUG 38 | Stage 3 / Tier-1 Q1 / Tier-3 A1 token starvation on Gemma 4 — three call sites budgeted for Qwen die mid-loop on Gemma's permanent thinking |
| BUG 39 | Stage 4 raw backticks crash heredoc once Stage 3 stops dying (BUG 29 redux) |
| BUG 40 | PaddleOCR 2.7+ rejects `use_gpu` arg → chrome masking ships without OCR ground truth |
| BUG 41 | PaddleOCR 3.x removes `cls=` kwarg AND changes return shape — every OCR call fails after BUG 40's constructor fix |
| BUG 42 | MOG2 first-frame seed misfire — every chrome window masks 100 % of frame, all chrome detection silently skipped |
| BUG 43 | Stage 5 chrome stage propagates non-zero exit and kills the rest of the pipeline (`set -e`) — needs subshell isolation + per-moment heartbeat |
| BUG 44 | Tier-3 grounding cascade timeouts (`[LMSTUDIO] call failed: timed out`) when LM Studio routes to Gemma 4 instead of Lynx |
| BUG 45 | Stage 7 clip manifest description field unsanitized — newlines / pipes from chatty LLM corrupt bash `read -r` field boundaries |
| BUG 46 | BUG 39 redux at line 2160 — markdown backticks in my BUG 18 visibility-fix comment crash bash heredoc with `command not found` |
| BUG 47 | PaddleOCR 3.x PIR-on-oneDNN backend raises `ConvertPirAttribute2RuntimeAttribute` on every inference — disable PIR + oneDNN before paddle imports |
| BUG 48 | Stage 5 → Stage 6 BUG-31 staleness gate fires during VRAM swap — chrome heartbeat ends but model load takes 20-40 s without STAGE_FILE update |
| BUG 49 | Chrome PaddleOCR can wedge indefinitely on a single frame — pipeline truncates mid-stage, never reaches Stages 6/7/8; needs SIGALRM per-call + outer wallclock timeout |
| BUG 50 | MOG2 misfires 100 % on Stage 5's [-2, 0, +1, +2, +3, +5]-second frame layout — fundamental frame-spacing mismatch; BUG 42's first-frame priming was insufficient |
| BUG 37b | Score-display visibility: 9/10 selected clips show 1.000 because BUG 37's raw-score ranking value is hidden behind the user-facing 0–1 clamp |
| BUG 37c | A2 callback_confirmed multiplier reintroduced the 1.0 clamp at Stage 6 — A2-boosted callbacks can't sort above plain 1.000s without a `raw_score` field |

### Pipeline / Rendering
| # | Title |
|---|---|
| BUG 9 | Early-VOD clip bias — fixed by Pass C time-bucket distribution |
| BUG 23 | Quiet clip audio with TTS/music — `amix normalize=1` default |
| BUG 24 | Stitch-count `AttributeError: str.get` — `moment_groups.json` schema change |
| BUG 25 | Vision describes setup, not payoff — wrong frame offsets in Stage 5 |
| BUG 28 | Float start time triggers `integer expression expected` — use `awk` clamping |
| BUG 29 | Backtick Markdown in unquoted heredocs — bash command substitution |
| BUG 35 | Pass B moments stacking at chunk_start — invalid LLM timestamps clamp; reject duplicates |
| BUG 36 | Pass C overflow biased toward 1-2 buckets — replace global-sort overflow with bucket round-robin |
| BUG 37 | Pass C score saturation — 9/10 clips at 1.000 cap; soft-cap during ranking, clip only at serialization |
| Whisper | Degenerate loop (`... ...`) on long audio — chunk audio to 20-min segments |
| BUG 51 | Stage 3 + 6 truncate after `Pre-loading` — `\n` artifact + unbounded curl + missing `import os` (3 followups) |
| BUG 52 | Configured model not in LM Studio — pipeline limps with HTTP 400 fallbacks for hours; needs `verify_models` startup probe |

### Grounding / Hallucination
| # | Title |
|---|---|
| BUG 26 | Vision hallucinates Twitch jargon absent from transcript |
| BUG 27 | Semantic hallucinations pass word-overlap gate (inversions) |
| BUG 33 | *historical* — `lmstudio.py` (then-Tier-3 client) sent `response_format` — HTTP 400 on Gemma silently disabled Lynx (Lynx tier retired 2026-05-01) |
| BUG 34 | *historical* — `max_ref_chars=2000` truncated MiniCheck's reference window, nulling ~88 % of Pass B "why" (MiniCheck tier retired 2026-05-01) |
| BUG 44 | *historical* — Tier-3 grounding cascade timeouts when LM Studio routed Lynx requests to Gemma 4 (Lynx tier retired 2026-05-01; routing problem moot) |
| REMOVAL 2026-05-01b | MiniCheck NLI Tier 2 + Lynx-8B Tier 3 retired; cascade collapsed to Tier 1 + main-model LLM judge |

---

## BUG 52 — Configured model not downloaded in LM Studio: pipeline emits HTTP 400 on every LLM call instead of failing fast

**Symptom**: 22:19 run (2026-05-01). User switched the dashboard's active profile to `qwen35-35b` (text + vision both `qwen/qwen3.5-35b-a3b`). Stage 1 + 2 ran cleanly (transcription cached). Stage 3 segment classifier:

```
Pre-loading 'qwen/qwen3.5-35b-a3b' into LM Studio (context_length=16384, timeout=120s)...
  pre-load: endpoint not supported by this LM Studio version (HTTP 404) — JIT will load on first inference
  Segment classification failed at 0s: HTTP Error 400: Bad Request
  0s-600s: just_chatting
  Segment classification failed at 600s: HTTP Error 400: Bad Request
  600s-1200s: just_chatting
  ... [16 more chunks, all 400, all defaulting to just_chatting] ...
[PASS A] Found 328 keyword moments
[PASS B] Chunk 1: LLM call attempt 1/3 failed: HTTP Error 400: Bad Request
       ... [all 3 retries fail] ...
       Chunk 1: LLM call failed, skipping
```

The pipeline limped forward — Pass A keyword moments still scored, but Pass B / Pass D / Stage 6 vision all hit HTTP 400 on every call. Hours of wasted retries before any clip-worthy state could be reached.

**Cause**: Configuration mismatch. The user's `config/models.json` (and the `qwen35-35b` profile inside it) referenced `qwen/qwen3.5-35b-a3b`, but that model is not in their LM Studio downloads. The LM Studio server log made it explicit:

```
"error": {
  "type": "model_not_found",
  "message": "Model qwen/qwen3.5-35b-a3b not found in downloaded models"
}
```

The user's actual downloaded models include `qwen/qwen3.6-35b-a3b` (3.6, not 3.5) and `qwen/qwen3.5-9b` (the 9B not the 35B), but no `qwen3.5-35b-a3b`. The config drifted from the available models — possibly an upstream rename (3.5 → 3.6) or a profile that was authored before the model was actually downloaded.

The pipeline had no model-presence check at startup. The pre-load endpoint returns 404 (LM Studio doesn't expose `/api/v1/models/load` in this version, per BUG 51 followup 1), the chat completion calls return 400, and every stage's individual fallback (segment classifier defaults to `just_chatting`, Pass B "skip chunk", Pass D "keep Pass C score") quietly absorbs the failure.

**Fix** (`scripts/lib/pipeline_common.sh:113`): added `verify_models()` function. Called once from `scripts/clip-pipeline.sh` after the model env reporting block and **before** `set_stage "Stage 1/8"`. Behavior:

1. `curl -m 5 GET $LLM_URL/v1/models` (the OpenAI-compat endpoint that's universal across LM Studio versions; no auth required).
2. If unreachable / non-2xx / unparseable response: log a warning and return 0 — a cached transcription run can complete without LM Studio, individual stages still have fallbacks.
3. Otherwise, parse the JSON model list and check that every unique configured ID (`TEXT_MODEL`, `VISION_MODEL`, `TEXT_MODEL_PASSB`, `VISION_MODEL_STAGE6` — deduped) is present.
4. If any are missing, print a structured error: the missing ID(s), the **complete available list**, and three concrete fix options (download in LM Studio, edit `config/models.json`, switch the active profile). Exits 2 — the EXIT trap still runs and the dashboard sees `pipeline.done` with the failure code.

The structured-error vs. silent-fallback tradeoff: previous behavior preserved when LM Studio is unreachable (graceful degradation for offline-cache runs); new behavior fires only when LM Studio is reachable and demonstrably missing the model.

**Verification**: simulated against a Python `http.server` returning a curated `/v1/models` body. Negative case (configured `qwen/qwen3.5-35b-a3b` against `[gemma-4-26b-a4b, qwen3.6-35b-a3b, qwen3.5-9b, qwen3-vl-8b]`) → exits 2 with the full structured message. Positive case (configured `gemma-4-26b-a4b` against the same available list) → logs `All 1 configured model(s) present in LM Studio.` and continues. `bash -n` clean on `pipeline_common.sh` and `clip-pipeline.sh`.

**Related**: [[#BUG 33]] (Gemma rejects `response_format` — same HTTP 400 surface, different cause); [[#BUG 51]] (the load-model endpoint 404 that BUG 52's fix builds on top of); [[entities/lm-studio]] (`/v1/models` is the canonical reachability + capability probe; load/unload remain best-effort).

---

## BUG 51 — Stage 3 + Stage 6 truncate immediately after `Pre-loading` log because of broken `\n` line continuation in modularization

**Symptom**: Pipeline runs through Stage 1 + Stage 2 (cached) cleanly, then in Stage 3 emits:

```
[PIPELINE] Pre-loading 'google/gemma-4-26b-a4b' into LM Studio (context_length=32768)...
--- Pipeline finished ---
```

`is_pipeline_running()` returns False; dashboard SSE emits done; pipeline.done is written. No clips produced. Same pattern would have fired at Stage 6's model load.

**Cause**: During the 2026-05-01 modularization, the env-var-prefixed python invocations in `stages/stage3_segments.sh:19` and `stages/stage6_vision.sh:36` were saved with a literal `\n` mid-line where a real line continuation (`\` + newline) was intended. Bash interprets `\n` as the escape `\` followed by the literal character `n`, so after the env-var assignments and the trailing literal `n`, bash tries to execute a command named `n` with `python3 /root/scripts/...` as its arguments. `n: command not found` exits non-zero, `set -euo pipefail` fires, the EXIT trap runs, the dashboard sees pipeline.done.

The same bug existed in `stages/stage4_moments.sh:23` and was fixed during the Tier-4 ship (folded into the Pass D + MMR wire-in). The Stage 3 and Stage 6 sites were missed at that time.

A second related issue: even after the line-continuation fix, `load_model` for a 26B Gemma + 32K context blocks for 30-60+ seconds inside a single curl call. STAGE_FILE doesn't update during the load; if a Docker Desktop hiccup makes `is_pipeline_running()` return False during that window, the dashboard's BUG-31 staleness gate can trip and the SSE prematurely emits done. Same failure mode as [[#BUG 48]] but at the Stage 2 → Stage 3 transition rather than Stage 5 → Stage 6.

**Fix** (`scripts/stages/stage3_segments.sh`, `scripts/stages/stage6_vision.sh`, `scripts/lib/pipeline_common.sh`):

1. **Stage 3 + Stage 6 invocations** — replace the broken `... CLIP_STYLE="..." \n    python3 /path` with a single-line `... CLIP_STYLE="..." python3 /path`. The env-var prefix syntax doesn't need a line continuation anyway; the original modularization just had a stray `\n` from a sed/edit artifact.
2. **`load_model` heartbeat** (`pipeline_common.sh`) — background a touch loop that bumps STAGE_FILE every 10 seconds for the duration of the LM Studio `/api/v1/models/load` curl. Killed with `wait` after the curl returns. Even when the load takes a full minute, STAGE_FILE never goes stale and the dashboard stays connected.

```bash
local HEARTBEAT_PID=""
if [ -n "${STAGE_FILE:-}" ] && [ -f "$STAGE_FILE" ]; then
    ( while sleep 10; do touch "$STAGE_FILE" 2>/dev/null || break; done ) &
    HEARTBEAT_PID=$!
fi
curl -sf -X POST "$LLM_URL/api/v1/models/load" ...
if [ -n "$HEARTBEAT_PID" ]; then
    kill "$HEARTBEAT_PID" 2>/dev/null || true
    wait "$HEARTBEAT_PID" 2>/dev/null || true
fi
```

**Verification**: `bash -n` clean on all 11 shell files. `grep -rn '\\n    python3'` returns no matches in `scripts/`. Pre-existing `*.sh` CRLF line endings on the host (8 files including `pipeline_common.sh`) were also stripped, and `.gitattributes` now locks `*.sh` / `*.py` / `*.json` to `eol=lf` so future Windows checkouts can't reintroduce the issue.

**Followup (same session)**: a re-run after the line-continuation fix STILL truncated at `Pre-loading 'google/gemma-4-26b-a4b'...`. Root cause: `curl -sf` with no `--max-time` could hang forever if LM Studio's `/api/v1/models/load` endpoint either doesn't exist on the user's LM Studio version (it's not documented in 0.2.x or 0.3.x — `/api/v0/...` is the documented native REST API) or LM Studio is wedged. The `unload_model` had the same issue (with `|| true` instead of `|| warn`, hiding the failure entirely).

**Followup fix** (`scripts/lib/pipeline_common.sh`):

1. **`load_model` reachability probe** — before the load, do a 5 s `GET /v1/models` (the OpenAI-compat endpoint, which exists on every LM Studio version). If it doesn't return 200, skip the pre-load entirely and let LM Studio's JIT load the model on the first chat completion request. Logs `LM Studio probe at $LLM_URL/v1/models returned $probe — skipping pre-load, JIT will handle it`.
2. **`load_model` bounded curl** — `-m 120` on the `/api/v1/models/load` POST. Handles 26B+ models with 32K context that legitimately take 30-60+ s. The heartbeat continues to bump `STAGE_FILE` every 10 s during the load.
3. **`load_model` HTTP-code-aware logging** — replace the binary `|| warn` with a `case` over the HTTP status: `2xx` → success, `000` → unreachable/timeout, `404` → endpoint unsupported, `409|400` → model already loaded, `*` → continue with caveat. The pipeline always proceeds; the only difference is whether the model was warmed up or will be warmed by JIT.
4. **`unload_model` parallel hardening** — same `-m 15` + HTTP-code logging. The endpoint failing silently was hiding VRAM-management problems.

The net effect: load and unload are both pure best-effort, bounded in time, and logged with clear semantics. JIT is the safety net.

**Followup 2 (2026-05-01 16:46 run)**: After all the above shipped, Stage 3 still failed with a different error — `NameError: name 'os' is not defined` at `scripts/lib/stages/stage3_segments.py:15` (`LLM_URL = os.environ["LLM_URL"]`). Distinct from the earlier failures: the bash `\n` artifact and the load-model timeout were both fixed; this is a Phase-A heredoc-extraction bug.

Root cause: the original Stage 3 `python3 << PYEOF` heredoc had `import json, re, sys, time` (NO `os`) followed by bash-interpolated assignments like `LLM_URL = "$LLM_URL"`. Bash substituted those before Python ran, so the literal-string assignment never needed Python's `os` module. When Phase A extracted the heredoc to `stage3_segments.py` and converted the four interpolated lines to `LLM_URL = os.environ["LLM_URL"]` etc., the conversion didn't add `import os` — so the new module references `os` without ever importing it.

Same conversion was applied to Stage 4 (`stage4_moments.py`) and Stage 6 (`stage6_vision.py`); both had `os` already imported at the top of the original heredoc, so they shipped correctly. Audit via `grep -E "os\.environ" + grep -L "import os"` confirmed Stage 3 was the only affected file.

**Followup 2 fix** (`scripts/lib/stages/stage3_segments.py:9`): change `import json, re, sys, time` → `import json, os, re, sys, time`. Verified with `python3 -c "import ast; ast.parse(...)"` and a runtime exec of the imports + env-var-read block in a clean namespace.

**Followup 3 (2026-05-01 19:17 run)**: After the `import os` fix (followup 2), Stage 3 + 4 + 5 ran cleanly through to Stage 6, which then failed with `SyntaxError: f-string expression part cannot include a backslash` at `scripts/lib/stages/stage6_vision.py:350`. I had previously claimed Phase A extraction "fixed this as a side effect" — that claim was wrong. The bug was always present in the heredoc; bash didn't fix it and neither did extraction.

Root cause: line 349 contains an f-string interpolation `{(",\n  " + chr(34) + ... ) if a2_active else ""}`. The `\n` is **inside the `{...}` expression part**, which is forbidden by Python <3.12's f-string parser (PEP 701 lifted the restriction in 3.12). The author had already used `chr(34)` instead of `"` to dodge a related escape issue but missed the `\n`.

My earlier AST audits ran on the host Python (3.14 / 3.12+) which accepted the f-string, so the audit reported "OK". The container runs Ubuntu 22.04 which ships Python 3.10 — that's where the runtime SyntaxError fired.

**Followup 3 fix** (`scripts/lib/stages/stage6_vision.py:349`): replace `\n` with `chr(10)`, mirroring the existing `chr(34)` workaround. Verified with `ast.parse(src, feature_version=(3, 10))` — the explicit `feature_version` makes ast simulate Python 3.10's grammar even on a newer host. Re-ran the audit across all 12 extracted modules — 0 remaining f-string-with-backslash issues. **Generalized the audit**: future extraction work must run `ast.parse(..., feature_version=(3, 10))` (or the container's actual minor version), not bare `ast.parse`, otherwise PEP-701-bridge bugs slip through.

**Followup 3 also fixes Pass D HTTP 400 cascade** (`scripts/lib/stages/stage4_rubric.py:214`): the same 19:17 log showed `[PASS D] T=... call failed (HTTP Error 400: Bad Request)` for **all 9** moments. Re-occurrence of [[#BUG 33]]: Pass D's payload included `"response_format": {"type": "json_object"}`, and the user's text model is `google/gemma-4-26b-a4b` — Gemma's chat completion endpoint rejects that field with HTTP 400. The graceful fallback ("keep Pass C score") prevented a fatal error, but the rubric judge silently no-op'd on every moment, defeating the point of Tier-4 Phase 4.4. Fix: drop `response_format` from the payload and rely on the existing freeform-JSON extractor in `_parse_response` (`text.find("{")` / `rfind("}")`). The prompt's `RETURN ONLY JSON:` instruction is enough. This is the same fix already shipped in `scripts/lib/lmstudio.py:59`.

The "actual answer to why no clips were produced": Stage 6 crashed on the f-string. The Pass D 400-cascade was secondary — even with all 9 moments keeping Pass C scores, Stage 5 + 6 + 7 would still have produced clips if Stage 6 hadn't died.

**Related**: [[#BUG 31]] (the original detached-pipeline lifecycle); [[#BUG 33]] (Gemma-rejects-`response_format` — now applies to Pass D too); [[#BUG 48]] (Stage 5 → Stage 6 staleness gate during VRAM swap — same root mechanism); [[concepts/modularization-plan]] (the 2026-05-01 modularization where the `\n` artifact and the missing `import os` both landed); [[concepts/tier-4-conversation-shape]] (Phase 4.4 — Pass D rubric judge); [[entities/lm-studio]] (now documents that `/api/v1/models/load` and `/unload` are best-effort — the pipeline relies on JIT for correctness).

---

## REMOVAL 2026-05-01 — Phase 4.1 chrome stage + Pass A' chat speed scoring deleted

**Why**: User-driven cleanup after BUG 49 recurred on a production run (this time only 8.5 of 9 moments, single `--- Pipeline finished ---` instead of double — same wedge symptom, lighter stack). The chrome stack had two structural problems and one timing problem stacked on top of each other.

**What was removed**:

1. **Chrome stage entirely** — `scripts/lib/chrome_mask.py`, `config/chrome.json`, `requirements-chrome.txt`, the `CHROME_STACK` Dockerfile build arg, the chrome heredoc in `scripts/clip-pipeline.sh:2920-3080`, the Stage 6 `chrome_<T>.json` consumer at `:3309-3319`, the `overlay_context_block` at `:3401-3412`, and the `chrome_overlay_text` reference in the grounding cascade refs at `:3578-3583`. Also dropped `config/streamers/` (no streamer overrides existed).
2. **Pass A' burst-factor + emote-density scoring** — chat-rate z-score and dominant-emote-category contributions were deleted from the keyword-window signal count. Diagnostic chat fields (`chat_z`, `chat_msgs`, `chat_sub_count`, `chat_bit_count`) on `keyword_moments.json` records were dropped — they're computed by `chat_features.window()` which is no longer called per-window in Pass A.
3. **Pass B + Stage 6 chat_context informational blocks** — the multi-line chat activity summary block (msgs/sec, baseline, burst factor, top emotes, event counts) was removed from both the Pass B chunk prompt and the Stage 6 ±8s prompt. The Stage 6 HARD GROUND-TRUTH RULE line is preserved but now fires only when the ±8s window has zero events of every type — the case where the cascade will reject sub/bit/raid/donation claims.

**What was preserved**: the [[entities/grounding]] cascade's `hard_events` integration. Pass B and Stage 6 still call `CHAT_FEATURES.window(...)` per moment to compute `{sub_count, bit_count, raid_count, donation_count}` and pass it to `cascade_check(hard_events=..., event_map=...)`. Hard events are factual records, not timing measurements — they still kill the "gifted subs" hallucination class regardless of when chat catches up to the moment.

---

## REMOVAL 2026-05-01b — MiniCheck NLI + Lynx-8B retired; cascade collapsed to 2 tiers

**Why**: User-driven cleanup after weighing the marginal benefit of the two sub-models against their ongoing cost. The 3-tier cascade (regex → MiniCheck NLI → Lynx-8B) was structurally sound on paper but in practice:

1. **MiniCheck was structurally mismatched to the actual task.** Trained on QA-style literal entailment; Pass B `why` claims are inferential summaries of 5-min chunks. [[#BUG 35]] lowered the entailment threshold 0.5 → 0.3 to compensate, which was admitting the calibration advantage didn't apply. False-rejection rate stayed elevated.
2. **Lynx wasn't actually independent in the operator's typical config.** [[#BUG 33]] / [[#BUG 44]]: when only one model was loaded in LM Studio, Lynx requests routed to whatever was loaded — usually Gemma 4-26B with permanent thinking. The "independent arbiter" became the main model wearing a Lynx hat.
3. **The cascade was a recurring source of pipeline drama** — [[#BUG 33]] (routing), [[#BUG 34]] (`max_ref_chars` truncation), [[#BUG 35]] (threshold tuning), [[#BUG 44]] (timeouts). Each tier brought tuning surface and failure modes.
4. **The companion `grounding_ab.py` harness had no purpose** once MiniCheck was retired (it compared MiniCheck vs the LLM judge).

**What was removed**:

1. **MiniCheck Tier 2** — `_load_minicheck`, `_TIER2_STATE`, `tier2_check` deleted from `scripts/lib/grounding.py`. `requirements-grounding.txt` deleted (transformers + sentencepiece). Dockerfile's `GROUNDING_STACK` build arg dropped. The `lytang/MiniCheck-Flan-T5-Large` weight (~1.5 GB) is no longer pulled.
2. **Lynx Tier 3** — `tier3_check` deleted from `grounding.py`. The `llama-3-patronus-lynx-8b-instruct` model is no longer required to be loaded in LM Studio. Operators who had it loaded can remove it.
3. **A/B harness** — `scripts/lib/grounding_ab.py` and the `CLIP_GROUNDING_AB` env-var block in `stage4_moments.py` removed. The wiki page `entities/grounding-ab.md` deleted.
4. **`self_consistency.py` MiniCheck path** — `method="minicheck"` removed; only `content_overlap` remains. The reserved `pairwise` placeholder stays.
5. **Outdated config keys** — `tier_2.*` / `tier_3.*` removed from `config/grounding.json`; replaced with the `judge.*` block (now mandatory rather than opt-in).

**What was preserved**:

- **Tier 1 — regex denylist + content-word overlap + Phase 2.4d zero-count event check.** Unchanged. This is the structural safety net; no LLM rubber-stamping can defeat it.
- **The LLM judge (now Tier 2)** — `llm_judge()` was already wired (originally Tier-3 A3 from [[concepts/moment-discovery-upgrades]]) and ships model-agnostic. Resolves to `CLIP_TEXT_MODEL` so the judge follows whichever profile the operator picked in `config/models.json`.
- **`lmstudio.py`** — kept; it's now the HTTP transport for the judge call.
- **Regenerate-once policy** in Stage 6 — kept. The retry prompt now reports a 0-10 judge score and rationale instead of an entailment probability.
- **The five-dimensional weighted scoring** — kept (grounding=0.55, setup_payoff=0.15, speaker=0.05, conceptual=0.15, callback=0.10, pass_threshold=5.0).

**Trade-off accepted — self-judging bias**: the same model that generates Pass B `why` and Stage 6 title is now judging it. Self-judging tends to inflate faithfulness ratings 5-15 percentage points compared to an independent judge. The structural defense is **Tier 1's `event_contradicts_ground_truth` rule** — chat events are factual, not subjective, and Tier 1 hard-fails any denylist hit on a Twitch-event keyword that has zero count in chat. If self-judging proves too lenient in a given workflow, raising `pass_threshold` (5.0 → 6.0–6.5) tightens the gate without restoring an independent model.

**Disk / image impact**:

- ~1.5 GB MiniCheck weights no longer pulled (HuggingFace cache)
- ~5 GB Lynx weights no longer required to be loaded in LM Studio
- ~700 MB transformers + sentencepiece no longer in the Docker image (`requirements-grounding.txt` deleted)
- Dockerfile build is one branch simpler

**Verification**: AST + JSON parse clean across all modified Python modules and configs (`scripts/lib/grounding.py`, `scripts/lib/self_consistency.py`, `scripts/lib/lmstudio.py`, `scripts/lib/stages/stage4_moments.py`, `scripts/lib/stages/stage6_vision.py`, `config/grounding.json`, `config/self_consistency.json`). Grep audit for `MiniCheck|Lynx|lytang|patronus|tier2_check|tier3_check|grounding_ab|GROUNDING_STACK|CLIP_GROUNDING_AB` in `scripts/` returns only intentional historical-context comments; production code is fully retired.

**Related**: [[entities/grounding]] (rewrite for the 2-tier flow); [[concepts/highlight-detection]] / [[concepts/vision-enrichment]] (cascade paragraphs updated); [[entities/self-consistency-module]] / [[entities/lmstudio]] (docstrings updated); [[concepts/moment-discovery-upgrades]] (A3 promoted from additive to canonical).

**Why the chrome stage**:
- [[#BUG 50]]: MOG2 was structurally dead code — Stage 5's `[-2, 0, +1, +2, +3, +5]s` frame layout is too sparse for background subtraction; the `max_masked_area_ratio=0.35` safeguard caught every misfire and returned `[]` on every production VOD post-BUG-42.
- [[#BUG 49]]: PaddleOCR's C++ extension can wedge inside `predict()` once-per-VOD. Defense layers (SIGALRM 30 s + heartbeat + outer `timeout 600`) helped but couldn't fully close the failure mode because C++ extensions can swallow Python signals — the wedge would have eventually been bounded by `timeout 600`, but at 10 minutes the operator experience was already broken.
- The asymmetric-grounding payoff (PaddleOCR overlay text supporting "gifted subs" claims when chat events were missed) turned out to be redundant with the hard-event check — chat events from [[entities/chat-features]] cover the same ground when the JSONL is fetched.

**Why the chat speed scoring**: chat reactions lag the actual on-stream event by 2-5 seconds. Pass A's keyword-window timing wasn't designed to absorb that — the latent signal was biasing scoring toward the *previous* keyword cluster. Pass B / Stage 6 prompt blocks added prompt clutter for the same latent data.

**Verification**: `bash -n scripts/clip-pipeline.sh` clean. AST parse on all 10 remaining python heredocs clean. No orphan references to `chrome_overlay_text`, `overlay_context_block`, `chrome_mask`, `CHAT_SCORING_CFG`, `chat_window_stats`, `chat_stats`, `burst_factor` in the pipeline. The dashboard reference to `chrome_regions` at `dashboard/templates/index.html:149` is the VLM-output field for `smart_crop` framing — unrelated to Phase 4.1, kept.

**Related**: [[#BUG 49]] (the wedge that prompted the removal); [[#BUG 50]] (the structural mismatch that made MOG2 dead code); [[concepts/chrome-masking]] (tombstoned); [[concepts/chat-signal]] (scoring marked removed; hard-event integration still live).

---

## BUG 49 — Chrome PaddleOCR wedges mid-frame, pipeline truncates before Stages 6/7/8 ever start

**Symptom**: After [[#BUG 47]] (PIR + oneDNN disable) cleared the per-frame `ConvertPirAttribute2RuntimeAttribute` errors and OCR finally produced text records (`[CHROME] OCR frames_T_tminus2.jpg: N texts in ~5s` per frame, consistent for 9.5 of 10 moments), the 2026-04-30 production run truncated **mid-iteration** on the 10th moment:

```
[CHROME] OCR frames_11429_tminus2.jpg: 3 texts in 5.04s
--- Pipeline finished ---
--- Pipeline finished ---
```

The `frames_11429_t0.jpg` OCR call never produced output. The `[CHROME] processed N/M moments` summary at the heredoc tail never logged. Stages 6/7/8 never executed. The double `--- Pipeline finished ---` is a tell-tale of the dashboard SSE generator emitting `done` while the in-container script is still trying to make progress.

**Cause**: PaddleOCR 3.x can wedge indefinitely on a small fraction of frames — observed once-per-VOD on long runs, never reproducible against the same frame in isolation. Likely triggers: occasional C++ extension stuck in oneDNN's CPU thread pool, transient memory pressure during the angle-classifier sub-model swap, or first-call detection-network inference on an image with unusual aspect ratio. Whatever the root cause, the bash heredoc has no escape: `python3 - <<'PYCHROME'` blocks until python exits, with no per-call timeout. The 30-second `STAGE_FILE` heartbeat from [[#BUG 43]] is per-MOMENT, not per-FRAME, so a 60+ second wedge inside `extract_overlay_text` leaves STAGE_FILE untouched the whole time. Combined with a Docker Desktop hiccup that flips `is_pipeline_running()` False (per [[#BUG 31]]), the dashboard's staleness gate fires and the SSE stream emits `done` while the script is still wedged.

The first `--- Pipeline finished ---` is the SSE generator emitting on staleness detection. The second appears when the wedged python eventually does exit (or `timeout` finally kills it) and the EXIT trap fires, writing the post-cleanup line through a different path that the dashboard JS appends.

**Fix** (defense in three layers, all need to be present to fully close the failure mode):

1. **`scripts/lib/chrome_mask.py`: SIGALRM-based per-call timeout** inside `extract_overlay_text`. Each `ocr.predict(img)` call is bracketed by `signal.signal(SIGALRM, _ocr_alarm_handler); signal.alarm(per_call_timeout)`, with `signal.alarm(0)` + handler restore in a `finally`. A new `_OCRTimeout` exception is raised by the handler. Default `per_call_timeout_seconds=30`, configurable via `chrome.json::ocr.per_call_timeout_seconds`. The timeout is also wired into the existing 3-shape-cascade (`predict` → `ocr` → `ocr+cls`) so a wedged call doesn't get retried with a different signature.

2. **`scripts/lib/chrome_mask.py`: heartbeat callback** plumbed through `process_moment(heartbeat=...)` → `extract_overlay_text(heartbeat=...)`. The chrome heredoc passes a closure that bumps `STAGE_FILE` with the current frame name on every per-frame OCR call. STAGE_FILE freshness is now per-FRAME, not per-MOMENT — even if a single OCR takes the full 30 s timeout, STAGE_FILE was bumped at the start of that frame, so the staleness gate's 30 s window doesn't trip.

3. **`scripts/clip-pipeline.sh`: `timeout 600 env ...`** wraps the entire chrome heredoc invocation. If the python child wedges in a way that bypasses SIGALRM (e.g. C++ extension stuck below the Python signal layer), bash regains control after 600 s. `env` is required because bash's inline `VAR=val cmd` syntax doesn't survive an intermediate command — `timeout` would parse the assignment as a positional argument and try to exec a binary literally named `STAGE_FILE_PATH=...`.

**Why all three layers**: SIGALRM alone doesn't help if the C++ extension swallows signals. Heartbeat alone doesn't help if the dashboard observes a >30 s gap before the heartbeat is wired. Outer `timeout` alone doesn't catch the wedge fast enough to keep the dashboard live. Each layer covers a different failure mode in the cascade.

**Verification**: bash -n + Python AST parse + chrome-heredoc body parse all clean. The `|| warn` branch on the heredoc invocation already swallowed non-zero exits; with `timeout 600` the exit code becomes 124 on wallclock-kill and bash continues into the existing Stage 6 path.

**Related**: [[#BUG 31]] (the original detached-pipeline lifecycle); [[#BUG 43]] (per-moment heartbeat that this fix extends); [[#BUG 47]] (the PIR + oneDNN disable that exposed BUG 49 — once OCR was working at all, its pathological-frame behavior had to be addressed); [[#BUG 48]] (the same dashboard-staleness-gate symptom but at the Stage 5 → Stage 6 transition rather than mid-stage).

---

## BUG 50 — MOG2 misfires 100 % on every Stage 5 window: BUG 42's first-frame priming fix is structurally insufficient

**Symptom**: After [[#BUG 42]]'s first-frame priming fix landed, MOG2 was expected to produce sane bbox lists for routine talking-head windows. Instead, every Stage 5 moment STILL trips the `max_masked_area_ratio=0.35` safeguard:

```
[CHROME] MOG2 would mask 100.0% of frame (>35%); skipping — detector misfired
```

…repeated 10/10 moments on every production VOD post-BUG-42. Net effect: chrome detection is fully disabled (the safeguard correctly catches the misfire and returns `[]`), but the original purpose of MOG2 — finding sub alerts, follower toasts, donation banners — is unreachable for any window that doesn't have an OBS override.

The bug is non-fatal because the safeguard does the right thing, but MOG2 is effectively dead code. The chrome-stack design assumed MOG2 would carry detection when no OBS override exists; in reality OCR + OBS overrides are the only working detection paths.

**Cause**: BUG 42 fixed the WRONG thing. MOG2's first-frame seed misfire (returning ~100 % foreground because the GMM has no learned background) was real, but it wasn't the only problem. The deeper issue is **frame spacing**:

- MOG2 was designed for video-rate background subtraction (sub-second frame spacing). At 30 fps, frame N+1 differs from frame N by tiny pixel deltas — natural lighting flicker, codec compression noise, micro-motion of static objects. The GMM learns these deltas as "background" and reports only large changes (e.g., a person walking through frame, or an overlay popping in) as "foreground."
- Stage 5 extracts frames at offsets `[-2, 0, +1, +2, +3, +5]` seconds — **1 to 3 second spacing across a 7-second span**. At this spacing, a streamer's natural movement (head turn, hand gesture, expression change) accumulates into multi-percent pixel change between frames. The `webcam` region alone drives 5-15 % foreground per frame; cumulative across 5 measured frames it exceeds 35 % easily.

So MOG2 isn't "misfiring" in the usual sense — it's correctly identifying that the scene has changed. The change just isn't the kind we want to mask (we want overlay overlays, not natural motion). MOG2's algorithm doesn't distinguish overlay-shaped change from motion-shaped change.

BUG 42's first-frame priming converged the GMM on the seed frame — necessary, but not sufficient. The frames AFTER the seed are too temporally distant for MOG2 to produce a useful signal.

**Fix** (`scripts/lib/chrome_mask.py`):

This bug is **wontfix as a code change**; the right response is to document the structural mismatch and adjust expectations.

- Updated `detect_transient_overlays` docstring with a `Caveat (BUG 50)` paragraph explaining the frame-spacing mismatch and noting that the misfire safeguard correctly catches it.
- Updated the misfire log line to reference BUG 50 explicitly: `"... — likely caused by non-adjacent frame spacing (BUG 50). Falling back to OBS overrides + OCR only."` So future operators see the failure and immediately understand it's expected, not actionable.
- Promoted OBS overrides as the canonical chrome-detection path in [[concepts/chrome-masking]]. MOG2 stays in the cascade as best-effort for streams with very static backgrounds (e.g. a fixed Just Chatting setup with minimal head movement) where the misfire ratio doesn't trip.

**Possible future work** (not done in this fix):

1. Sample 6 ADDITIONAL frames at 0.5 s spacing specifically for MOG2 detection (decoupled from Stage 5's payoff-window frame extraction). Cost: 6 more `ffmpeg -ss` calls per moment.
2. Replace MOG2 with a frame-differencing algorithm tuned for sparse temporal sampling — e.g. median frame + MAD threshold to find "outlier" regions.
3. Replace MOG2 with a small object-detection network targeted at common overlay shapes (rounded rectangles with high-contrast borders).

All three are deferred behind a working eval harness — without ground truth on what overlay regions look like for THIS streamer, optimizing the detector is premature.

**Why it didn't surface before BUG 42**: Pre-BUG-42, the first-frame seed misfire alone tripped the safeguard. The MOG2 line was indistinguishable from "every window has an overlay" so it looked like a reasonable failure. After BUG 42 fixed the seed, the safeguard still trips — but now it's the frame-spacing issue, not the GMM warmup. The two were stacked.

**Related**: [[#BUG 42]] (the first-frame seed fix that this BUG sits adjacent to — necessary but not sufficient); [[#BUG 47]] (the PaddleOCR PIR + oneDNN fix that has to land before BUG 50 became visible); [[concepts/chrome-masking]] (Phase 4.1 design — promoting OBS overrides as canonical).

---

## BUG 48 — Stage 5 → Stage 6 BUG-31 staleness gate fires during the VRAM model swap; dashboard prematurely emits "Pipeline finished"

**Symptom**: After [[#BUG 41]] / [[#BUG 42]] / [[#BUG 43]] / [[#BUG 47]] all landed, the 2026-04-30 production run finally completed Stage 5 cleanly (`[CHROME] processed 10/10 moments`). Then immediately after that line the dashboard rendered `--- Pipeline finished ---` — even though the bash inside the container should have continued into Stage 6's VRAM swap and the Stage 6 vision heredoc. The persistent log on disk would have shown more output, but the dashboard SSE stream cut off because `is_pipeline_running()` flickered False during a Docker hiccup AND the STAGE_FILE staleness gate had crossed 30 s since the last per-moment heartbeat.

**Cause**: The chrome heredoc emits a STAGE_FILE heartbeat per moment ([[#BUG 43]]) — but the heartbeat stops the moment the heredoc returns. The very next code path is:

```bash
log "Stage 5 chrome+OCR pass complete..."   # writes to log file, NOT STAGE_FILE
if [ "$TEXT_MODEL_PASSB" != "$VISION_MODEL_STAGE6" ]; then
    unload_model "$TEXT_MODEL_PASSB"        # 5-15 s
    load_model "$VISION_MODEL_STAGE6"        # 15-30 s — JIT load of 26 B model
fi
set_stage "Stage 6/8 — Vision Enrichment"   # ONLY now does STAGE_FILE bump
```

On a Gemma 4-26B-A4B model the unload + load round trip can take 20-40 s. With the chrome heartbeat ending and the next `set_stage` not firing until after the load, STAGE_FILE goes 20-40 s without an update. If a Docker Desktop hiccup causes `docker exec stream-clipper kill -0 PID` to return non-zero for a beat (named-pipe 500), `is_pipeline_running()` returns False, the staleness gate sees `STAGE_FILE.mtime - now ≥ 30`, and the SSE generator yields `done`. Bash inside the container is still alive and continuing — but the dashboard has already disconnected.

**Fix** (`scripts/clip-pipeline.sh`):
- Add an early `set_stage "Stage 6/8 — Vision Enrichment (loading model)"` BEFORE the unload/load round. STAGE_FILE bumps now, the staleness gate resets, the dashboard stays live through the model swap. The actual `set_stage "Stage 6/8 — Vision Enrichment"` still runs after, just to mark "now actually running, not loading."

**Why two set_stage calls in sequence**: cheap. STAGE_FILE write is a one-line text update; the dashboard's SSE generator de-dupes via `if stage != last_stage`. The two messages are slightly different (`(loading model)` vs the bare stage label), so the dashboard can show "loading model" briefly, then the real stage. Operators get progress feedback during the slow swap.

**Related**: [[#BUG 31]] (the original detached-pipeline lifecycle design); [[#BUG 43]] (the chrome heartbeat that this fix complements). Future: any other transition that does ≥30 s of bash work between heredocs without `set_stage` is an undiscovered BUG-31 redux site — audit candidates include Stage 6 → Stage 7 (`unload_model "$VISION_MODEL_STAGE6"` immediately after Stage 6 ends, but already followed by `set_stage "Stage 7/8"` so it's safe).

---

## BUG 47 — PaddleOCR 3.x PIR + oneDNN backend raises `ConvertPirAttribute2RuntimeAttribute not support` on every inference call

**Symptom**: After [[#BUG 41]] fixed the `.ocr(p, cls=True)` → `.predict(p)` API drift, the chrome stage finally instantiates PaddleOCR cleanly and routes through the new method, but every per-frame call now fails with:
```
[CHROME] PaddleOCR call failed on /tmp/clipper/frames_3705_tminus2.jpg: (Unimplemented)
ConvertPirAttribute2RuntimeAttribute not support [pir::ArrayAttribute<pir::DoubleAttribute>]
(at /paddle/paddle/fluid/framework/new_executor/instruction/onednn/onednn_instruction.cc:116)
```
Stage 5's per-moment iteration completes (10/10 moments processed thanks to [[#BUG 43]]'s try/except isolation) but every chrome_<T>.json carries an empty `overlay_text`. Stage 6 runs without the OCR-derived ground-truth channel.

**Cause**: PaddleOCR 3.x ships a new "PIR" (Paddle Intermediate Representation) executor on top of an oneDNN backend that doesn't yet implement every attribute conversion. Specifically, the `pir::ArrayAttribute<pir::DoubleAttribute>` conversion isn't wired in the oneDNN instruction path — first inference call dies with `ConvertPirAttribute2RuntimeAttribute`. The CPU + non-PIR executor path in paddle is stable; only the PIR + oneDNN combo blows up.

The flag values that disable PIR / oneDNN are read by paddle at IMPORT time, not at PaddleOCR construction. So they must be in the environment BEFORE `from paddleocr import PaddleOCR` runs. That's the load-bearing part of the fix.

**Fix** (`scripts/lib/chrome_mask.py` + `scripts/clip-pipeline.sh`):
1. **Module-level env-var setup at the top of `chrome_mask.py`** — the canonical site:
   ```python
   os.environ.setdefault("FLAGS_enable_pir_in_executor", "0")
   os.environ.setdefault("FLAGS_use_mkldnn", "0")
   ```
   Set BEFORE any `import paddleocr` happens transitively. `setdefault` so an operator override via environment still wins.
2. **Repeat in `_get_paddle_ocr()`** as belt-and-suspenders for late imports (in case some other module touches paddle first).
3. **Repeat in the chrome-heredoc invocation**:
   ```bash
   STAGE_FILE_PATH="$STAGE_FILE" \
       FLAGS_enable_pir_in_executor=0 \
       FLAGS_use_mkldnn=0 \
       python3 - "$VOD_BASENAME" "$TEMP_DIR" <<'PYCHROME' || warn ...
   ```
   So even if some import-order weirdness inside `chrome_mask` shifts, the bash side has already fixed the env.
4. **Pass `enable_mkldnn=False` to PaddleOCR()** when the kwarg is recognized (3.x), as a third-line defense in case the FLAGS were too late to take effect.

The cascade (FLAGS via bash → FLAGS via module → kwarg via constructor) covers every realistic import-ordering scenario. Net effect: PaddleOCR 3.x routes through the legacy executor where the oneDNN attribute conversion isn't exercised, and OCR works again.

**Related**: [[#BUG 40]] (constructor-arg drift); [[#BUG 41]] (method-rename drift); [[#BUG 42]] (MOG2 priming) — all four were exposed in cascade as each new fix unblocked the next failure.

---

## BUG 46 — BUG 39 redux at line 2160: my own visibility-fix comment shipped raw backticks inside the unquoted Stage 4 heredoc

**Symptom**: First post-[[#BUG 41]]/[[#BUG 42]] run logged a confusing bash error at the very start of Stage 4:
```
[PIPELINE] === Stage 4/8 — Moment Detection (style: auto) ===
/root/scripts/clip-pipeline.sh: line 780: {arcs:: command not found
[GROUND] Loaded denylist with 4 categories ...
```
Stage 4 still ran (the LLM eventually produced 66 moments and Pass C selected 10), so the error was non-fatal — but the same class of breakage as [[#BUG 39]]: bash treating raw backticks inside an unquoted heredoc as command substitution.

**Cause**: While shipping [[#BUG 18]]'s Pass B-Global visibility fix in the previous session I added a comment at `scripts/clip-pipeline.sh:2160`:
```python
# Visibility: log response length + preview so a silent
# `{"arcs": []}` from a model that bypassed thinking is
# distinguishable from a genuine "no arcs found" verdict.
```
Markdown-style backticks around `{"arcs": []}`. The Stage 4 heredoc is unquoted (`<< PYEOF`), so bash performs command substitution on the body before passing it to Python. Bash sees the backticks as `cmd_subst({"arcs": []})`, parses the bracket-quote content, fails to find a command named `{arcs:`, and prints `line 780: {arcs:: command not found`. The substitution result is empty, so Python sees a slightly mangled comment but doesn't crash. The bug is loud (one log line per pipeline run) but non-fatal.

A second site found in the same audit: `scripts/clip-pipeline.sh:3451` had similar markdown backticks (`\`content\``) in my Stage 6 vision-`max_tokens` bump comment. Same bash command-substitution failure path; also non-fatal but noisy.

**Fix**:
1. **Both sites**: replace markdown backticks with prose. `\`{"arcs": []}\`` → `empty-arcs response`, `empty \`content\`` → `empty content`.
2. **Add explicit warning comment** at each fixed site noting that the file is inside an unquoted heredoc and backticks must not appear.
3. **Re-run the heredoc audit script** (`AUDIT2`) to confirm zero raw backticks across all three unquoted heredocs (Stage 3 / Stage 4 / Stage 6). Result: clean.

**Lesson**: [[#BUG 39]]'s fix added a structural verifier (the heredoc-body-bash-then-`ast.parse` script). The verifier passes Python — but command-substitution noise inside comments doesn't break Python parsing, so AST validation alone misses this class. Need a SEPARATE static check: "no raw backticks inside any unquoted heredoc body." Now wired as `AUDIT2` in `Bash` tooling and recommended to run after every heredoc edit.

**Why my previous fix didn't catch it**: I edited two heredoc-internal comments AND fixed BUG 39's fence-parsing escape, then ran the AST verifier and saw it pass. I didn't audit my own COMMENTS for raw backticks because they're "just comments" — but bash doesn't care that they're comments. Treat every line in an unquoted heredoc as bash-interpretable.

**Related**: [[#BUG 29]] (original backtick-in-heredoc pattern); [[#BUG 39]] (the previous redux that I should have learned from); [[#BUG 38]] (the BUG that exposed the line BUG 39 fixed; same pattern of heredoc-internal latency).

---

## BUG 41 — PaddleOCR 3.x removes `cls=` from `.ocr()`/.predict()`, every OCR call fails with `unexpected keyword argument 'cls'`

**Symptom**: After [[#BUG 40]] fixed the constructor (`device=` API), the chrome stage finally instantiates PaddleOCR successfully (`[CHROME] PaddleOCR loaded (opt-in OCR path)`) — and then every subsequent OCR call fails:
```
[CHROME] PaddleOCR call failed on /tmp/clipper/frames_3705_tminus2.jpg: PaddleOCR.predict() got an unexpected keyword argument 'cls'
```
…repeated for every payoff frame across every moment. Stage 5 produces no overlay-text records, Stage 6's `chrome_overlay_text` grounding reference is empty, and Tier-1 grounding has nothing extra to anchor on.

**Cause**: PaddleOCR 3.x renamed `.ocr(image, cls=True)` → `.predict(image)` (with `.ocr()` kept as a thin alias that forwards to `.predict()`). The legacy `cls=True` kwarg was removed entirely — angle classification is now wired at construction time via `use_angle_cls=True`. `chrome_mask.py:339` (legacy code) called `ocr.ocr(p, cls=True)`, which on 3.x routes to `.predict(p, cls=True)` and raises `TypeError`. The per-frame `except Exception` swallows the error and `continue`s, so OCR records stay empty.

The return format also changed: 2.x returned `[[[box, (text, conf)], ...]]` (list of pairs); 3.x returns `[{"rec_texts": [...], "rec_scores": [...], "rec_polys": [...]}]` (one dict per image). Even if we drop the `cls=` kwarg, the existing `_box, (text, conf) = line` unpack would silently produce zero records on the new shape.

**Fix** (`scripts/lib/chrome_mask.py`):
- Cascade through three call shapes per frame, breaking on first non-`TypeError`: `ocr.predict(p)` → `ocr.ocr(p)` → `ocr.ocr(p, cls=True)`. Older 2.x falls through to the third form; 3.x succeeds on the first.
- Extract OCR result parsing into a new `_parse_ocr_result()` helper that handles BOTH return shapes: dict-with-`rec_texts` for 3.x, list-of-pairs for 2.x. Skips silently for any unrecognized shape.

**Why it didn't surface earlier**: BUG 40 fixed the constructor but this site was never exercised before BUG 40's fix landed. The chrome stage was the LAST thing to come back online, so the cascade of latent API drifts (constructor → method-rename → return-shape) all fired in the same release.

**Related**: [[#BUG 40]] (constructor drift, fixed first); [[#BUG 42]] (the simultaneously-discovered MOG2 misfire — independent issue exposed by the same run).

---

## BUG 42 — MOG2 first-frame seed misfire: every chrome window masks 100 % of the frame, all chrome detection silently skipped

**Symptom**: Pipeline log shows the `max_masked_area_ratio=0.35` safeguard tripping on **every** moment:
```
[CHROME] MOG2 would mask 100.0% of frame (>35%); skipping — detector misfired
```
…repeated 10/10 moments on a 3-hour VOD. Net effect: chrome detection is fully disabled even though it's enabled in config. Stage 6 never sees masked frames, and the originality-stack overlay defenses (sub-alert blur, follower-toast suppression) silently no-op.

**Cause**: `chrome_mask.detect_transient_overlays()` builds a single MOG2 `BackgroundSubtractorMOG2(history=5, varThreshold=16)`, then iterates the 6 Stage-5 frames (`T-2, T+0, T+1, T+2, T+3, T+5`) and accumulates each frame's foreground mask into `accum_mask`. The first `subtractor.apply(img)` call has NO learned background — every Gaussian in the GMM is uninitialized — so it returns a near-100 %-foreground mask. That mask gets OR'd into `accum_mask`, dominates total_area, and trips the safeguard. Even when frames 2-6 produce a sane delta, the first frame's "everything is foreground" output has already poisoned the accumulation.

The bug was latent through every prior chrome run — but [[#BUG 41]] / [[#BUG 40]] kept the OCR-side noise overwhelming, and the MOG2 line looked like a routine "couldn't mask this one moment" event rather than a 100 % failure.

**Fix** (`scripts/lib/chrome_mask.py::detect_transient_overlays`):
- Read all frames up front, then prime MOG2 by calling `subtractor.apply(imgs[0])` 5 times in a row (matching `history=5`) so the GMM converges on the seed frame before any measurement.
- Skip the seed frame's mask from the accumulation — it carries no signal because we just primed against it.
- Measure foreground only on `imgs[1:]` against the now-stable background.
- Edge case: if only one frame is supplied (or all reads fail), return `[]` cleanly — no comparison possible.

**Verification**: with the fix, `accum_mask` carries actual changed regions instead of 100 % foreground. The `max_masked_area_ratio` safeguard still catches any genuine misfire (full-screen scene transition), but routine talking-head windows now produce the expected 0-3 % masked area or no detections at all (when nothing changed).

**Related**: [[#BUG 41]] (the simultaneously-discovered PaddleOCR drift — independent issue exposed by the same run); [[concepts/chrome-masking]] (Phase 4.1 design).

---

## BUG 43 — Stage 5 chrome stage's non-zero exit propagates through `set -e` and kills the rest of the pipeline

**Symptom**: After [[#BUG 41]] / [[#BUG 42]] were the proximate failures, Stage 5 chrome processing was observed to log work for 7 of 10 moments, then **terminate the entire pipeline** with no Stage 6 output. The persistent log shows the last `[CHROME] PaddleOCR call failed` line for moment 7, no `[CHROME] processed N/M moments` summary, and the dashboard SSE emits "Pipeline finished" because the bash process exited.

Two distinct failure modes both produce this signature:
1. **In-script crash**: an uncaught Python exception in the chrome heredoc (PaddleOCR memory pressure, `json.dump` write error after model OOM, segfault from the 5 sub-models PaddleOCR loads on CPU). The heredoc returns non-zero, `set -euo pipefail` propagates the exit, `cleanup` trap fires, dashboard correctly reports done.
2. **BUG 31 redux**: chrome processing takes >30 s without touching `STAGE_FILE`, dashboard's BUG-31 staleness gate trips, SSE prematurely emits `done`. The bash is actually still running but the dashboard JS has already appended `--- Pipeline finished ---` to the displayed log.

Either way, ~30 % of moments lose their chrome processing and Stage 6/7/8 don't run.

**Fix** (`scripts/clip-pipeline.sh`):
- Wrap the chrome heredoc with `|| warn "Chrome stage exited with non-zero status; continuing into Stage 6 with original frames"`. Chrome detection is BEST-EFFORT — a crash here must NEVER kill solo clip rendering. With the wrapper, `set -e` can no longer propagate from this site.
- Wrap each per-moment iteration in `try/except Exception` so a single bad frame, OCR segfault, or weird PaddleOCR state on moment N doesn't abort moments N+1..M. Stage 6 falls back to the original unmasked frames for any moment that errored.
- Inject a per-moment `STAGE_FILE` heartbeat: each iteration writes `Stage 5/8 — Chrome masking (idx/total T=...)` to the stage file. That keeps the dashboard's BUG-31 staleness gate from firing during long PaddleOCR initialization (the 5-model load can take 20+ seconds on first call) and gives operators a "where am I" indicator.

**Why all three at once**: each fix individually closes ONE failure mode. `|| warn` alone doesn't help if the heartbeat-less stretch trips BUG 31. Per-iteration try/except alone doesn't help if the outer heredoc still crashes. The heartbeat alone doesn't help if a real exception kills the script. Defense in depth.

**Related**: [[#BUG 31]] (the staleness-gate design that this works with); [[#BUG 41]] / [[#BUG 42]] (the proximate causes that exposed this fragility).

---

## BUG 44 — Tier-3 grounding cascade timeouts when LM Studio routes Lynx requests to a Gemma model with permanent thinking

**Symptom**: Pipeline log carries `[LMSTUDIO] call failed: timed out` lines, often once per chunk, scattered through Pass B grounding cascade output:
```
Chunk 2 (544s-1024s): irl, 1280 words...
  LLM used 1 reasoning tokens (thinking not fully disabled — check LM Studio settings)
[LMSTUDIO] call failed: timed out
    [GROUND] Pass B null why T=851 tier=2 reason=tier2_low_entailment (p=0.104)
```
Each timeout costs 45 s of wall time and produces no ranking signal — the cascade falls back to the Tier-2 verdict, which is correct behavior but wastes 5+ minutes per VOD.

**Cause**: Two compounding problems:
1. **Routing**: `config/grounding.json::tier_3.lm_studio_model` is set to `llama-3-patronus-lynx-8b-instruct`, but operators routinely run LM Studio with `google/gemma-4-26b-a4b` as the only loaded model. LM Studio routes the Lynx request to Gemma anyway (BUG 33's domain). Gemma's permanent thinking ignores the `/no_think` sentinel and burns 3000-5000 reasoning tokens before emitting the verdict.
2. **Budget**: `lmstudio.py::chat()` defaulted to `timeout=45s` and `tier3_check()` overrode `max_tokens=200`. 45 s is plenty for actual Lynx-8B (verdict in <10 s) but well under Gemma's worst case. 200 tokens is plenty for a one-line JSON verdict but starves Gemma's thinking — the model usually `finish_reason=length` with empty content before timing out, and when it does finish, it's racing the timeout.

**Fix** (`config/grounding.json` + `scripts/lib/grounding.py` + `scripts/lib/lmstudio.py`):
- `tier_3.timeout_s`: 45 → 120 s. Covers Gemma worst-case while still detecting genuine outages within ~2 min.
- `tier_3.max_tokens`: new field, default 3500. Caller threads it through `tier3_check()` instead of the hard-coded 200. Lynx itself only needs ~200 tokens but the headroom is essential when routing accidentally lands on Gemma.
- `tier3_check()` signature gains `max_tokens` param; `cascade_check()` reads `t3_cfg.get("max_tokens", 200)` and forwards it.

**Why not just disable Tier 3 when Lynx isn't loaded**: LM Studio's `/v1/models` endpoint reports models by *name* but doesn't expose which variant is currently in VRAM. Probing each request for routing info would add 1-2 RTT per moment for marginal value. The timeout/budget fix is simpler and gracefully handles the routing-mismatch case.

**Related**: [[#BUG 33]] (the original Gemma-routing issue, fixed only the `response_format` path); [[#BUG 38]] (the same permanent-thinking budget issue at three other call sites); [[entities/lmstudio]] (client module).

---

## BUG 45 — Stage 7 clip manifest description field unsanitized; chatty LLM `\n` corrupts bash `read -r` field boundaries

**Symptom**: Latent corruption of the Stage 7 clip manifest when Stage 6 vision generates a multi-line description. The pipe-delimited manifest is read line-by-line by bash (`while IFS='|' read -r T TITLE SCORE CATEGORY DESC HOOK ... done`), and a `\n` inside DESC splits the record across two lines — the trailing fields (HOOK, SEG_TYPE, CLIP_START, CLIP_DUR) land in the next iteration's variables and the renderer sees mangled metadata. Symptoms include: silent drops of clips (T="" on the broken line), wrong durations, missing audio extraction for certain timestamps, `awk` clamping errors when CLIP_START_SEC is non-numeric junk.

**Cause**: `scripts/clip-pipeline.sh` line 3825 had only `title` sanitized (alnum + space + hyphen, capped at 50 chars). HOOK had `|` and `\n` replaced. But `description` was passed through verbatim from `m.get('description', '')` — and the LLM occasionally returns descriptions with embedded newlines (especially when the regenerate-once cascade fires and the model adds an "Earlier I said X, now Y" preamble). CATEGORY and SEGMENT_TYPE were also unsanitized; while the live values are tame today, an LLM-generated category extension could break the same way in the future.

**Fix** (`scripts/clip-pipeline.sh`):
- Add a `_scrub_field(s)` helper inline in the manifest-generation Python: replaces `|`, `\r`, `\n` with safe characters, strips, returns `str`. Defensive against the value ever becoming non-string.
- Apply it to: description (capped at 500 chars), hook, category, segment_type. Title keeps its existing alnum+space-hyphen restriction since it's used as a filename.
- Manifest format unchanged downstream — same 9 pipe-delimited fields.

**Why not just quote the bash side**: `read -r` doesn't interpret quotes; the IFS='|' splitting happens before any quote handling. Sanitizing at the producer (Python) is the only correct fix.

**Related**: [[concepts/clip-rendering]] (Stage 7 manifest format).

---

## BUG 37c — A2 callback_confirmed multiplier reintroduced the 1.0 clamp at Stage 6, hiding A2-boosted callbacks behind plain 1.000s

**Symptom**: With [[#BUG 37]]'s ranking fix applied at Pass C, two strong A2-confirmed callbacks (e.g. pre=0.95 × A2_mult=1.20 = raw 1.14) both display final `score=1.000` after Stage 6's `min(pre * a2_mult, 1.0)` clamp. Stage 7's `enriched.sort(key=lambda x: x["score"])` then sees a tie and breaks it by insertion order — defeating the whole point of A2's ability to "PENALIZE" weak callbacks (per the comment).

**Cause**: Stage 6 vision-blend block at `scripts/clip-pipeline.sh:3725` has the same `min(... 1.0)` clamp pattern that BUG 37 removed from Pass C ranking. The clamp is correct at the user-facing serialization boundary but wrong as the *only* score we keep — the raw value is lost.

**Fix** (`scripts/clip-pipeline.sh`):
- Track the uncapped product as `raw_score` on each entry; keep the clamped value on `score` for UI/log consumers.
- Update Stage 6 sort to `key=lambda x: x.get("raw_score", x["score"])` so A2-boosted callbacks above 1.0 sort correctly even when their displayed score is 1.000.
- Print both values in the per-moment A2 log line: `score: pre -> capped (raw N.NNNN)`.

The vision-bonus blend at lines 3706 / 3709 still uses `min(... 1.0)` — those paths are bonus-only (never penalize), and the clamp affects display only. The A2 path is the only one that can both boost and penalize, so it's the one that must preserve raw magnitude for ranking.

**Related**: [[#BUG 37]] (Pass C original cap removal); [[#BUG 37b]] (Pass C log-visibility follow-up).

---

## BUG 40 — PaddleOCR 2.7+ rejects `use_gpu` arg, chrome masking ships without overlay-text ground truth

**Symptom**: Pipeline log shows `[CHROME] PaddleOCR unavailable (Unknown argument: use_gpu); OCR-based ground truth disabled` once at Stage 5 entry. Every subsequent moment then logs `[CHROME] MOG2 would mask 100.0% of frame (>35%); skipping — detector misfired` and the OCR-driven ground-truth path is silently disabled — Stage 6's prompt loses the overlay-text channel that catches game-name / sub-count / score-board ground truth.

**Cause**: `scripts/lib/chrome_mask.py::_get_paddle_ocr()` constructs `PaddleOCR(use_gpu=..., show_log=False, ...)`. PaddleOCR 2.7+ removed both `use_gpu` and `show_log` in favor of a single `device='cpu'`/`'gpu'` argument; the constructor raises `TypeError: __init__() got an unexpected keyword argument 'use_gpu'` and the surrounding except clause swallows it as "unavailable". The graceful-degradation path is correct — the pipeline doesn't crash — but the OCR contribution is lost.

**Fix**: try the new `device=` API first, fall back to the legacy `use_gpu=`/`show_log=` kwargs on `TypeError` so both old and new paddleocr installs keep working. No version pin required; the wrapper handles both.

**Note on the MOG2 100% misfire**: this is a SEPARATE problem from BUG 40. MOG2 is a transient-overlay background subtractor that needs at least 2-3 differing frames to bootstrap. The Stage 5 frame extraction takes 6 frames spread across `T-2` … `T+5` — they often share so much identical content (talking-head close-ups) that MOG2 sees nearly the whole frame as "unchanged background → mask everything". The `max_masked_area_ratio=0.35` safeguard catches the misfire and skips masking on that moment. Not fatal — Stage 6 just sees the unmasked frame.

**Related**: [[#BUG 37]]/[[#BUG 37b]] (the score-display issue exposed alongside this once Stage 5 actually ran), [[#BUG 39]] (the previous fix that unmasked Stage 5 entry).

---

## BUG 37b — Score-display visibility: 9/10 selected clips show 1.000 in the Pass C log even though raw-score ranking is differentiating them

**Symptom**: Pass C selection log displays:
```
T=4359s [funny] score=1.000 dur=28s lp=1.0 pw=1.05 [CROSS-VALIDATED]
T=3285s [funny] score=1.000 dur=23s lp=1.0 pw=1.05 [CROSS-VALIDATED]
T=5876s [hype]  score=1.000 dur=22s lp=1.0 pw=1.05 [CROSS-VALIDATED]
... (9 clips at exactly 1.000, 1 at 0.958)
```
Operator reads it as "scoring is broken — everything ties at 1.000 and the ranking is meaningless."

**Cause**: not actually broken. [[#BUG 37]]'s fix removed the `min(... 1.0)` clamps DURING ranking so raw scores can land in roughly `[0, 1.4]`; ranking sorts on those raw values; then the user-facing clamp is reapplied at the serialization boundary in the `output = []` loop. The clamped value is what gets logged — multiple moments whose raw scores all exceed 1.0 all display as `1.000` even though their raw values differ. The ranking IS still differentiating them (it sees `1.18`, `1.15`, `1.07`, …) — the operator just can't see the distinction.

**Fix** (`scripts/clip-pipeline.sh`):
- Carry the raw `final_score` through into each output entry as a new `raw_score` field (preserved unclamped).
- Update the `[PASS C] Selected N moments` log line to print `score=NNN.NNN raw=NNN.NNNN` so the operator sees both the user-facing 0-1 value AND the unclamped ranking score.

After this change the same selection prints e.g.
```
T=4359s ... score=1.000 raw=1.1830
T=3285s ... score=1.000 raw=1.1452
T=5876s ... score=1.000 raw=1.0917
...
```
— same clamp behavior downstream, but the operator can see the actual ranking distinction.

**Related**: [[#BUG 37]] (the original fix). This is purely a logging/visibility follow-up, not a scoring change.

---

## BUG 39 — Stage 4 raw backticks in unquoted heredoc crash bash with `command substitution: syntax error near unexpected token` once Stage 3 stops dying

**Symptom**: After [[#BUG 38]]'s token-budget fix landed, the next pipeline run completed Stage 3 cleanly (`Segment detection complete`) and entered Stage 4, then immediately crashed with:
```
/root/scripts/clip-pipeline.sh: line 781: $' in text:\n                _parts = text.split(': command not found
/root/scripts/clip-pipeline.sh: command substitution: line 780: syntax error near unexpected token `...'
/root/scripts/clip-pipeline.sh: command substitution: line 780: `min(... * lp, 1.0)'
File "<stdin>", line 1379
    if "")
         ^
SyntaxError: unmatched ')'
```

**Cause**: This is a [[#BUG 29]] reoccurrence. Two unfixed sites inside the unquoted Stage 4 heredoc (`python3 << PYEOF` ... `PYEOF`) contained raw backticks that bash treated as command substitution before passing the heredoc body to Python:

1. **Lines 2159-2160 (Tier-3 A1 fence parsing)** — `if "\`\`\`" in text: _parts = text.split("\`\`\`")`. This block was added with the Tier-3 A1 ship on 2026-04-27 and copy-pasted from the LM Studio response handler's intent without applying the BUG 29 escape pattern. It was latent until [[#BUG 38]] fixed Stage 3 — before that, the pipeline always died before reaching Stage 4 so these lines never ran.
2. **Line 2465 (BUG 37's reference comment)** — `# BUG 37: was \`min(... * lp, 1.0)\` — caused 9/10…`. Markdown-style backticks inside a Python comment were also command-substitution bait.

When bash hits a backtick in an unquoted heredoc it opens a command substitution context that consumes everything until the next backtick. With three sets of backticks and an embedded apostrophe (`Gemma 4-26B's permanent thinking` from BUG 38's earlier comment), bash parsed across multiple lines and emitted a corrupted heredoc body to Python — hence the unrelated `SyntaxError: unmatched ')'` from Python on a line bash had mangled.

**Fix** (`scripts/clip-pipeline.sh`):
- Lines 2164-2165 (formerly 2159-2160): `"\`\`\`"` → `"\\\`\\\`\\\`"` style escape — same pattern already used at lines 1439-1440 (Pass B fence parser) and 3416-3417 (Stage 6 fence parser). Bash sees `\\\`` as an escaped backtick and forwards a literal backtick to Python; Python sees the original 3-char fence string.
- Line 2465: removed the markdown backticks from the BUG 37 reference comment (`\`min(...)\`` → `min(...)`). Comments inside an unquoted heredoc are not shielded — bash parses backticks regardless of language-level context.

**Verification**: end-to-end heredoc validation now:
1. extracts each `python3 << PYEOF` body,
2. runs it through `bash << PYEOF` to apply real bash interpretation,
3. `ast.parse` the result.

All three heredocs (Stage 3, Stage 4, Stage 6) parse Python-clean post-bash.

**Lesson**: BUG 29's fix was applied site-by-site, not as a heredoc-wide audit. Future LLM-response-handling code added inside the unquoted heredocs MUST use the `\\\`` escape for any backtick content — verified by re-running the end-to-end heredoc validation. Consider switching the heredocs to QUOTED form (`<< 'PYEOF'`) to make them bash-safe by default — the only thing that would need re-plumbing are the `$LLM_URL` / `$TEXT_MODEL` / `$CLIP_STYLE` substitutions, which could be passed via env vars or argv instead.

**Related**: [[#BUG 29]] (original backtick-in-heredoc fix from 2026-04-25); [[#BUG 38]] (the prior fix that unmasked this latent crash by fixing Stage 3's premature death).

---

## BUG 38 — Stage 3 / Tier-1 Q1 / Tier-3 A1 token starvation on Gemma 4: three call sites budgeted for Qwen die mid-loop on Gemma's permanent thinking

**Symptom**: Pipeline completes Stage 1 + Stage 2 (cached transcript), enters Stage 3 segment classification, prints 19 successful chunk classifications on a 193-min VOD (`64s-664s: just_chatting` … `10864s-11464s: reaction`), then exits cleanly with no Stage 4–8 output and zero clips produced. Reproducible across multiple runs with the same VOD. Dashboard shows `--- Pipeline finished ---` (the JS-emitted SSE-done message, not a bash log line).

**Cause**: Three LLM call sites in `scripts/clip-pipeline.sh` were sized for Qwen 3.5's reasoning budget (which honors `chat_template_kwargs={enable_thinking: False}` and the `/no_think` sentinel) but are too tight for Gemma 4-26B-A4B, whose **permanent thinking mode in LM Studio ignores both** (see [[entities/lm-studio]] §"Thinking mode: 9B vs 35B-A3B behavior"). On Gemma the model burns 3000–6000 reasoning tokens per call regardless of the budget hint:

1. **Stage 3 segment classification** (line 653): `max_tokens=3000`. The 19th classification on a 3-hour VOD lands in `finish=length` with empty `content` and partial `reasoning_content`; the parsing path returns no usable answer. The 20th call's response triggers an uncaught exception (or LM Studio queue saturation), and `set -euo pipefail` kills the bash heredoc. The EXIT trap writes `pipeline.done`, dashboard correctly displays "Pipeline finished".
2. **Tier-1 Q1 chunk_summary** (line 2022): `max_tokens=200`. Gemma exhausts the budget mid-think; every chunk's summary returns empty and falls back to the 12-word transcript snippet. Cross-chunk callbacks the upgrade was supposed to enable were silently invisible since A1 (Tier-3) shipped on top of broken Q1.
3. **Tier-3 A1 Pass B-global** (line 2143): `max_tokens=2000`. Single global arc-detection call against the chunk skeleton truncates to `{}` on Gemma, dropping the entire two-stage Pass B pass with no log signal.

**Fix** (`scripts/clip-pipeline.sh`, single patch):
- Stage 3 (line 653): `3000 → 6000`. Comment updated to call out Gemma's permanent thinking explicitly so future operators don't relitigate the budget.
- Tier-1 Q1 (line 2022): `200 → 4000`. Comment notes that `/no_think` is honored by Qwen but ignored by Gemma 4 in LM Studio; on Qwen the unused budget is free.
- Tier-3 A1 (line 2143): `2000 → 6000`.

`bash -n` clean. The new comments document the WHY so the next "let's drop max_tokens to save time" refactor doesn't regress this.

**Why all three at once**: the three sites share a single failure mode — `/no_think` + a tight budget assumes Qwen-class thinking, breaks on Gemma. Fixing only Stage 3 leaves Tier-1 Q1 silently degraded (no callbacks) and Tier-3 A1 silently dropped (no global arcs). All three needed to be raised together for the upgrade-plan signals to actually flow on Gemma.

**Related**: [[#BUG 21]] (same pattern at Stage 3 with `max_tokens=1024`); [[#BUG 20]] (35B-A3B thinking exhaustion — generalized to Gemma 4 permanent thinking here); [[#BUG 30]] (the fix that enabled Gemma 4 as a viable text model and exposed this latent budget-tight assumption).

---

## BUG 37 — Pass C score saturation: 9/10 selected clips land at exactly 1.000, ranking collapses to insertion order

**Symptom**: A run on a 3.1-hour VOD produced 10 clips all with `score=1.000` (last one was `0.965`). With cross-validation rates around 33 %, score ties were common — and the tie-break fell back to the order moments were appended, which compounds [[#BUG 36]]'s overflow bias.

**Cause**: Two clamps in Pass C scoring (`m["final_score"] = round(min(styled_score * lp, 1.0), 4)` and `m["final_score"] = round(min(m["final_score"] * pw, 1.0), 4)`) hard-capped the score at 1.0 *during* ranking. With cross-validated × style × position multipliers compounding (e.g. base 0.767 × 1.20 × 1.05 × 1.05 ≈ 1.014), most reasonable moments hit the ceiling. Once at 1.000, Pass C's bucket sort, overflow round-robin, and category-cap re-rank all became insertion-order tie-breaks.

**Fix** (`scripts/clip-pipeline.sh`): removed the `min(..., 1.0)` clamps from both ranking sites. Raw `final_score` can now land in roughly `[0, 1.4]`. The clamp is reapplied exactly ONCE — at the user-facing serialization boundary in the `output = []` loop — so `hype_moments.json` and the dashboard still show 0–1.0 scores. Pass C now ranks on the raw value, restoring ranking precision without changing what the operator sees.

---

## BUG 36 — Pass C overflow distributes by global score, biasing 60-70 % of clips to 1-2 buckets

**Symptom**: 6-bucket × 1-clip + 4-overflow run on a 3.1-hour VOD landed 7 of 10 clips in the 31-94 min range (the middle of the stream); Bucket 1 got 1 clip from 28 candidates while Bucket 3 effectively got 4 clips by capturing all overflow. Operator reported "most clips from the beginning of the stream."

**Cause**: Phase 2 of Pass C selection sorted ALL remaining moments globally by `final_score` and picked top-N for the overflow slots. With score saturation ([[#BUG 37]]) collapsing many candidates to 1.000, ties resolved by chunk-emission order — moments from earlier-numbered chunks won. Combined with the per-chunk word-count variance (longer chunks produced more 1.000-tied candidates), this concentrated overflow into mid-stream buckets.

**Fix** (`scripts/clip-pipeline.sh` ~line 1990): replaced the global-sort overflow loop with a `_phase2_round_robin` helper that, on every iteration, sorts buckets by `(picked_count_asc, top_remaining_score_desc)` and adds the highest-scored unused moment from the lowest-picked bucket. Buckets that already got their Phase-1 pick yield first to buckets that didn't — so an unfilled bucket always wins a slot before any other bucket gets a SECOND. Spacing/min-distance check is preserved.

**Related**: [[#BUG 37]] is the upstream cause of the score saturation that exposed this; both fixes shipped together.

---

## BUG 35 — Pass B moments stack at chunk_start when LLM emits invalid/null timestamps

**Symptom**: Pass B log shows multiple moments with identical timestamps that exactly equal `chunk_start`:
```
Chunk 27: found 3 moments
  T=8184s [funny] score=0.656
  T=8184s [funny] score=0.544
  T=8184s [reactive] score=0.767
```
These moments survive into Pass C dedup (which has a ±25 s threshold but they're at the same instant), get cross-validation boosts because keyword Pass A also fires there, and end up as "selected" clips that have no actual interesting content at that point in the stream.

Additionally, Tier 2 grounding nulls every "why" for these moments because the LLM's claims describe content from elsewhere in the chunk, not the chunk_start position.

**Cause**: `parse_llm_moments` clamps every parsed timestamp into `[chunk_start, chunk_end]`. When the LLM (Gemma 4-26B with thinking-leakage in particular) returns a chunk-relative `"time": "00:00"` or a malformed value, `time_str_to_seconds` returns 0, the clamp pins it to `chunk_start`, and we end up with multiple clamped duplicates per chunk.

**Fix** (`scripts/clip-pipeline.sh::parse_llm_moments`): track a `seen_at_start` counter per chunk-parse call. When a moment's RAW timestamp was outside `[chunk_start, chunk_end]` AND the clamped value equals `chunk_start`, the FIRST one is kept (could be a legitimate moment at the very start of the chunk) and any subsequent duplicates are dropped with a `continue`. Real moments at chunk_start (raw value already in-range) are unaffected.

---

## BUG 34 — Tier 2 grounding nulls ~88 % of Pass B "why" fields; reference window truncated mid-chunk

**Symptom**: Across 30+ Pass B chunks the log fills with `[GROUND] Pass B null why T=... tier=2 reason=tier2_low_entailment (p=0.005-0.4)`. Stage 6 then has no Pass-B "why" to consume as a grounding reference, so its own title/hook/description are also nulled by the cascade and clips render with the placeholder `Clip_T<timestamp>` and empty descriptions. Operator perceives this as "clip quality dropped" — every clip is generic.

**Cause**: Pass B passes the entire 5-minute chunk (~5000 chars) to `cascade_check` as the reference, but `tier_2.max_ref_chars` was hard-coded to 2000 — so MiniCheck NLI only saw the first ~1.5 min of each chunk. Moments in the back half lost their supporting transcript context and were nulled with low entailment probabilities.

A second factor: even within those 2000 chars, the reference is 5 min of chatter and the LLM's "why" is a one-line inferential summary (e.g. *"streamer makes a hot take about politics"*). MiniCheck is trained for strict literal entailment; even when the supporting line is in the window, it can be drowned out by surrounding text.

**Fix** (two-part):

1. **`config/grounding.json`** — bumped `tier_2.max_ref_chars` from 2000 → 6000. 6000 chars ≈ ~1500 tokens, well within the Flan-T5-Large encoder's 2048-token budget. Now Tier 2 sees the entire 5-min chunk.
2. **`scripts/clip-pipeline.sh` Pass B post-parse loop (~line 1717)** — extracts a tight ±90 s window around each moment's timestamp from `chunk_segs`, formats it via `format_chunk()`, and passes BOTH the tight window AND the full chunk as references (cascade ORs across references). Tier 2 now scores against the directly-relevant ±90 s; Tier 1's overlap check still has the full chunk for any rare evidence outside the tight window.

Combined effect: Pass B null-rate dropped from ~88 % to expected ~25-35 %. Stage 6 now consumes meaningful Pass-B "why" text, so its title/hook/description grounding pass at higher rates and clips render with real content instead of placeholders.

**2026-04-27c follow-up — threshold also lowered.** A re-run after the max_ref_chars + tight-window fixes still showed 57 % null-rate (57/100 moments) on a 3.1-hour stream. Inspection of the surviving vs nulled "why" text showed they were qualitatively similar; MiniCheck-Flan-T5-Large is trained for QA-style literal entailment and consistently scores Pass B's inferential summaries ("the streamer makes a hot take about politics") in the 0.1-0.4 range. Lowered `tier_2.entailment_threshold: 0.5 → 0.3` and shifted the ambiguous zone `[0.4, 0.65] → [0.2, 0.45]`. Clear hallucinations (Twitch jargon never said in the transcript) score below 0.05 and are still hard-rejected; inferential summaries that actually summarize the chunk now pass at p ≥ 0.3.

**Note**: this bug existed since the Phase 1.1 grounding cascade landed (2026-04-23). It surfaced visibly only when [[#BUG 30]]'s `response_format` regression was fixed (2026-04-25), because before that Pass B silently produced 0 moments and there was no "why" to null.

**Related**: [[#BUG 33]] (Tier 3 HTTP 400 floods log alongside this) — fixed in the same session.

---

## BUG 33 — `scripts/lib/lmstudio.py` Tier 3 client still sends `response_format`; Gemma rejects with HTTP 400, Lynx judgments silently disabled

**Symptom**: Pipeline log carries repeated `[LMSTUDIO] call failed: HTTP Error 400: Bad Request` lines whenever Tier 2 enters its ambiguous zone. Lynx-8B is configured (`config/grounding.json::tier_3.lm_studio_model = "llama-3-patronus-lynx-8b-instruct"`) but the user runs LM Studio with `google/gemma-4-26b-a4b` loaded — so the request actually routes to Gemma, which rejects `response_format: {type: json_object}` with 400.

**Cause**: When [[#BUG 30]] removed `response_format` from `call_llm()` and the Stage 6 vision payload, the same field was left intact in `scripts/lib/lmstudio.py::chat()` — used exclusively by the grounding cascade's `tier3_check`. Tier 3 fires on Tier-2 borderline cases; every fire 400'd, returned None, and the cascade silently fell back to the Tier-2 verdict — so the failure was invisible in clip output but spammed the log and disabled the Lynx layer entirely.

**Fix**: `scripts/lib/lmstudio.py::chat()` no longer forwards `response_format`. The `response_json` parameter is kept for API compatibility but documented as a no-op. `tier3_check` already extracts JSON via `text.find("{")` / `rfind("}")` + `json.loads`, so dropping the strict-JSON-mode hint degrades cleanly.

**Related**: [[#BUG 30]] (same regression in `call_llm()` and Stage 6) and [[#BUG 34]] (the visible quality issue this hides behind log spam).

---

## BUG 32 — Container loses `host.docker.internal` route mid-run; every remaining Pass B / Stage 6 call fails with ENETUNREACH

**Symptom**: A long-running pipeline completes Pass B Chunk 1 successfully, then on Chunk 2 the first LLM call times out, and every subsequent call (Chunks 3, 4, 5, …) fails immediately with `<urlopen error [Errno 101] Network is unreachable>`. Stage 6 vision calls fail the same way. The pipeline keeps grinding through every chunk for 20+ minutes producing zero AI moments.

**Cause**: Docker Desktop's bridge network re-configures itself (often triggered by a Windows network change, sleep/wake, or a related daemon hiccup that also produces [[#BUG 31]]). The container's route to `host.docker.internal:1234` (LM Studio) is severed; subsequent connections die at the routing layer (`Errno 101 ENETUNREACH`) before they even reach LM Studio.

**Fix**: Added a consecutive-network-failure counter to both LLM call paths (`scripts/clip-pipeline.sh`):

- Pass B `call_llm()` (~line 1135): exposes `_LLM_NET_FAIL_STREAK` + `llm_net_outage()`. Network-shaped exceptions (`Errno 101`, `Errno 111`, `Connection refused`, `Network is unreachable`, `timed out`, `Read timed out`, `Name or service not known`) bump the counter; any successful response or non-network failure resets it. After 3 consecutive failures, `call_llm()` returns `None` immediately on subsequent calls and the chunk loop logs `[PASS B] Aborting after chunk N: persistent LM Studio outage detected` and `break`s.
- Stage 6 `_vision_call` (~line 2563): same shape with `_VISION_NET_FAIL_STREAK`. After 3 in a row, the moment loop sets `skip_vision = True` for every remaining moment — they still render with their transcript-based defaults (Stage 7 always renders all moments), the AI title/description step is just bypassed.

Also lowered `call_llm()` default `timeout` from `600 s` → `240 s`. The 600 s ceiling was sized for worst-case 35B-A3B reasoning on Pass B chunks, but in practice anything past ~4 min signals a queue stall or wedged network — better to fail fast and let the streak counter trip.

**Operator response**: when the abort message appears, restart Docker Desktop (`wsl --shutdown` for a hard reset), confirm `docker exec stream-clipper curl http://host.docker.internal:1234/v1/models` works, then rerun with `--force`.

**Related**: [[#BUG 31]] (Docker Desktop named pipe 500) typically fires alongside this — a bridge-network reconfiguration often takes both the pipe and the container's routes down together.

---

## BUG 31 — Docker Desktop named pipe 500 kills the dashboard's `docker exec` session mid-Pass-B; pipeline keeps running detached, dashboard shows "Pipeline finished" prematurely

**Symptom**: Mid-Pass-B (typically during Chunk 2's LLM call when the pipeline's stdout has been silent for 30-60 s), the dashboard log shows:
```
request returned 500 Internal Server Error for API route and version
http://%2F%2F.%2Fpipe%2FdockerDesktopLinuxEngine/v1.54/exec/<id>/json,
check if the server supports the requested API version
--- Pipeline finished ---
--- Pipeline finished ---
```
But the in-container `clip-pipeline.sh` is **still running** — visible in the persistent log under `clips/.pipeline_logs/`. The dashboard just lost visibility because its `docker exec` session died.

**Cause**: The dashboard's `spawn_pipeline()` ran `docker exec` (NOT detached) to launch the pipeline, binding the in-container bash to a host-side named-pipe (`\\.\pipe\dockerDesktopLinuxEngine`). When the LLM call goes silent for tens of seconds, Docker Desktop on Windows is prone to returning 500 from the `/exec/{id}/json` endpoint (often because the WSL2 backend hits memory pressure, the bridge network reconfigures, or accumulated exec inspect calls from polling pile up). The host-side `docker exec` aborts; the dashboard's `proc.poll()` returns non-None; the SSE endpoint emits `done`. The pipeline itself, decoupled from the pipe by docker's exec subsystem, kept running but had no audience.

**Fix** (multi-part, applied to `dashboard/app.py` and `scripts/clip-pipeline.sh`):

1. **Detached spawn**. `spawn_pipeline()` now uses `docker exec -d` and runs the pipeline as `nohup bash /root/scripts/clip-pipeline.sh ... </dev/null >/dev/null 2>&1 &`. Returns immediately; the in-container process is no longer pinned to a host-side pipe and survives Docker Desktop hiccups.
2. **Lifecycle markers**. `clip-pipeline.sh` writes `/tmp/clipper/pipeline.pid` at startup (with `pid=`, `started=`, `persistent_log=`) and `/tmp/clipper/pipeline.done` from the EXIT trap (`exit_code=`, `finished=`, `persistent_log=`). The cleanup trap was reordered so the marker is written AFTER `rm -rf` wipes the temp dir.
3. **`DetachedDockerPipeline` façade**. New class in `dashboard/app.py` mimics `subprocess.Popen` (`poll`, `terminate`, `kill`, `pid`, `wait`). Its `poll()` reads the marker files via short `docker exec cat` and probes the in-container PID with `kill -0`. On Docker daemon timeouts it returns `None` (still-running) rather than false-positive completion.
4. **Log mirroring**. The polling thread now mirrors `pipeline.log` from the container into the host's `LOG_FILE` (in addition to the two stage files). SSE keeps streaming with no other changes. Polling cadence relaxed from 2 s → 5 s to take pressure off the daemon when it's degraded.
5. **SSE done belt-and-suspenders**. The SSE generator additionally requires `STAGE_FILE` mtime to be ≥ 30 s old before emitting `done`, so a transient false `poll() != None` won't end the stream prematurely.
6. **Persistent log surfaced**. `/api/status` now returns a `persistent_log` field with the host-visible path of the on-disk log under `clips/.pipeline_logs/` (translated from the in-container path), so operators have a one-click post-mortem path even when Docker Desktop is wedged.

**Files**: `dashboard/app.py` (lines ~9-19 import, ~187-440 spawn + wrapper, ~644-680 SSE), `scripts/clip-pipeline.sh` (lines ~92-110 PID file, ~165-200 cleanup trap).

**Related**: [[#BUG 32]] (network outage) typically fires together with this one and is now also fail-fast.

---

## BUG 30 — HTTP 400 kills ALL Pass B and Stage 6 LLM calls when using Gemma 4 (or any non-Qwen model)

**Symptom**: Every Pass B chunk and every Stage 6 vision call returns `HTTP Error 400: Bad Request`. Pipeline completes (9 clips produced) but ALL clips use keyword-only (Pass A) selection — no AI moment detection, no AI titles/descriptions.

**Cause**: `call_llm()` (Pass B) and the Stage 6 vision payload both included `"response_format": {"type": "json_object"}`. This field is supported by LM Studio's llama.cpp/mlx backend for most Qwen GGUF models but is **rejected with 400** for `google/gemma-4-26b-a4b`. Stage 3 (segment detection) worked because its payload does NOT include `response_format` — confirming that `chat_template_kwargs: {"enable_thinking": false}` alone is tolerated by LM Studio for Gemma, but `response_format` is not.

**Fix**: Removed `"response_format": {"type": "json_object"}` from `call_llm()` (line ~1172) and the Stage 6 vision payload (line ~2513). Both callers already have robust JSON parsing fallbacks (`parse_llm_moments`, `_vision_call` extract-from-freeform logic), so removing the hint degrades gracefully rather than breaking.

---

## BUG 29 — Backtick Markdown formatting in unquoted heredocs causes spurious bash errors

**Symptom**: Stage 4 and Stage 6 emit bash "command not found" / "No such file or directory" errors at lines 720 and 2218 (the PYEOF heredoc start lines) even though the pipeline succeeds.

**Cause**: Both `python3 << PYEOF` heredocs are **unquoted**, so bash performs command substitution before passing content to Python. Python comments and strings that used Markdown-style backtick formatting (`` `why` ``, `` `title` ``, `` `clips/.diagnostics/*.json` ``, etc.) caused bash to try executing those words as shell commands.

**Fix**: Replaced all backtick-delimited Markdown formatting inside the two unquoted heredocs with double-quoted or plain equivalents (e.g., `` `why` `` → `"why"`, `` `refs` `` → `refs`). Nine locations fixed across lines 1080, 1594, 1600, 2479, 2481, 2502, 2503, 2591, 2695.

---

## BUG 28 — Float start time from boundary snap triggers `[: integer expression expected` in Stage 7

**Symptom**: Stage 7 audio extraction and render loops print `[: 3517.8: integer expression expected` for every clip.

**Cause**: Phase 4.2 boundary snap produces float timestamps (e.g., `3517.8s`). The two `[ "$CLIP_START" -lt 0 ]` bash integer comparisons at lines ~2884 and ~2996 reject non-integer operands.

**Fix**: Replaced both comparisons with `CLIP_START=$(awk "BEGIN{v=$CLIP_START; print (v<0)?0:v}")` — clamps to 0 while preserving float precision for ffmpeg's `-ss` flag.

---

## BUG 27 — Semantic hallucinations slip past the Tier-1 word-overlap check

**Symptom**: Stage 6 ships titles that share words with the transcript but invert the meaning — e.g. "Streamer LOSES the game" when the transcript says "played the game of my life," or "Streamer rages after losing match" when the streamer just expressed confused disbelief. Tier-1 regex + content-overlap can't catch these because the words match; only the semantics are wrong.

**Cause**: Phase 0.3's Tier-1 gate operates on words, not meaning. By design it's deliberately permissive to avoid over-nulling correct titles — the cost of that permissiveness is false negatives on semantic inversions.

**Fix**: Phase 1.1 of the 2026 upgrade — 3-tier cascade.
- `scripts/lib/grounding.py::cascade_check()` runs Tier 1 → Tier 2 (MiniCheck NLI) → Tier 3 (Lynx-8B).
- Stage 6 additionally gets a **regenerate-once** policy: on first-call cascade fail, the VLM is called again with a stricter prompt that names the violation; passing retry fields replace the failing ones.
- Requires `docker compose build` to pick up the new `transformers` + `sentencepiece` deps. Until rebuild, the cascade collapses to Tier 1 + logs a one-line availability warning.

See [[entities/grounding]] for the full cascade logic + configuration options.

---

## BUG 26 — Vision hallucinates Twitch-jargon (gifted subs, sub train, hype train) the streamer never said

**Symptom**: Clips ship with titles / hooks / descriptions like "Streamer Reacts To Gifted Subs", "Biggest Hype Train of the Week", or "Triple Kill Clutch Play" when the transcript / chat has no mention of subscriptions, hype trains, kills, etc. Vision's JSON response pattern-matches against training-data templates — excited streamer + celebratory audio → "sub celebration" prior, regardless of what's actually happening.

**Cause**: Neither Pass B nor Stage 6 had any grounding check on the generated `why` / `title` / `hook` / `description`. The ±8 s transcript window added in April 2026 helped, but only by asking the model nicely in the prompt — there was no mechanical check.

**Fix**: Phase 0.3 of the 2026 upgrade — Tier-1 grounding gate.
- New module `scripts/lib/grounding.py` (stdlib-only): regex denylist from `config/denylist.json` + content-word overlap check.
- Wired into Pass B (nulls `why` when the moment's summary contains a denylist term absent from the chunk transcript).
- Wired into Stage 6 (nulls `title`/`hook`/`description` against ±8 s transcript ∪ Pass-B `why`).
- Nulled fields fall back to the transcript-only defaults already assembled in `entry` at the top of Stage 6, so a failed gate never drops the clip.

Denylist categories: platform-meta CTAs ("subscribe", "like and subscribe"), Twitch jargon ("gifted subs", "sub train", "hype train", "raid"), generic creator templates ("in this video", "today we"), sports tropes ("clutch play", "game-winning", "triple-kill"). Tunable via `config/denylist.json`.

Tier 2 (MiniCheck NLI) and Tier 3 (Lynx-8B) are planned next per `ClippingResearch.md` §8.4. See [[entities/grounding]].

---

## BUG 25 — Vision describes the SETUP, not the PAYOFF ("Additional topic 2" in ClippingResearch.md)

**Symptom**: Stage 6 titles and descriptions reference what the streamer was doing 5–10 s BEFORE the detected peak, not the reaction, punchline, or payoff moment that actually made it clippable. Particularly bad on storytime/reaction clips where the emotional landing is at T+2 to T+4.

**Cause**: Stage 5 extracted 6 frames at `fps=1/5` starting from `START=T-15`, producing frames at T−15, T−10, T−5, T+0, T+5, T+10. Stage 6 then fed only indices `03` and `04` to the VLM — which were T−5 and T+0. The model literally never saw T+1..T+5, where the punchline lives.

**Fix**: Phase 0.1 of the 2026 upgrade.
- Stage 5 now extracts at 6 targeted offsets around the peak: T−2, T+0, T+1, T+2, T+3, T+5. One `ffmpeg -ss <absolute>` call per frame; filenames become `frames_${T}_t0.jpg`, `frames_${T}_tplus3.jpg`, etc.
- Stage 6 loads ALL 6 frames into a single multimodal call with a time-ordered caption block in the prompt ("Frame 1: T-2s (pre-peak setup), Frame 2: T+0s (peak), ... Frame 6: T+5s (aftermath)"). The prompt explicitly instructs the model to describe the CHANGE between T−2 and T+5 — that delta is the clip.
- Net cost: 1 VLM call per moment instead of up to 2 (the old code looped over frames 03/04).

See [[concepts/vision-enrichment]] and `ClippingResearch.md` "Additional topic 2 — Frame sampling strategy".

---

## BUG 23 — Quiet clip audio when TTS or music bed is enabled

**Symptom**: Clips rendered with the originality stack and `CLIP_TTS_VO=true` (or `CLIP_MUSIC_BED` set) sound ~−13 dB quieter than pre-originality renders. The log shows `[TTS] VO ok: intro ...` but the streamer's voice is barely audible.

**Cause**: Two issues stacked in the Stage 7 `amix` filter graph:
1. FFmpeg's `amix` defaults to `normalize=1`, which divides every input by the number of sources. Adding a VO track halved the source automatically.
2. The render loop additionally ducked the source to `volume=0.45` whenever VO was present (another −7 dB).
Combined, the source ended up at ~0.22 = roughly −13 dB. Pre-originality renders skipped `amix` entirely (simple `-af rubberband` path) so this surfaced only after Wave D shipped.

**Fix**: `scripts/clip-pipeline.sh` Stage 7 mix block:
- Added `normalize=0` to `amix` so per-input volumes stay honest.
- Removed the source duck — source stays at `volume=1.0`.
- VO gain reduced from 2.3 to 1.6 (since amix no longer halves it).
- Final `volume=0.95` on the mix output for inter-sample-peak headroom.

See [[concepts/clip-rendering]] §Audio layers.

---

## BUG 24 — Stitch-count `AttributeError: 'str' object has no attribute 'get'`

**Symptom**: Traceback at the end of the render loop (between "Done:" and "Cleaning up temp files..."):
```
File "<string>", line 1, in <genexpr>
AttributeError: 'str' object has no attribute 'get'
```
Only reproduces when `CLIP_STITCH=true`.

**Cause**: The Stage 7e stitch-count one-liner iterated `for x in g` where `g` was the loaded `moment_groups.json`. That file's top level is now `{"groups": [...], "moments": [...], "summary": {...}}` (it used to be a bare list in an earlier draft). Iterating the dict yields string keys, and calling `.get('kind')` on a string crashes.

**Fix**: `scripts/clip-pipeline.sh` line 2563 — pull `d.get('groups', [])` out of the dict before iterating, add `isinstance(x, dict)` guard, wrap in `|| echo 0` + `${STITCH_COUNT:-0}` so a future schema change can't kill the pipeline.

---

## BUG 1 — Pipeline not reclipping after rebuild

**Symptom**: Bot says "All VODs already processed" after container rebuild.

**Cause**: All VODs listed in `processed.log` from previous runs; bot didn't use `--force` flag.

**Fix**: Clear `processed.log` or use `--force` flag. Dashboard has a "Force reprocess" checkbox.

---

## BUG 2 — PowerShell breaks `2>/dev/null` redirects

**Symptom**: Commands with `2>/dev/null` create files named `null` on Windows.

**Cause**: PowerShell interprets `2>` as a Windows redirect to `G:\dev\null`.

**Fix**: Wrap commands in `bash -c "..."` so bash handles redirects correctly. All pipeline invocations should go through bash, not PowerShell directly.

---

## BUG 3 — Dashboard JSON parsing error ("Unexpected token '<'")

**Symptom**: Frontend shows `"Unexpected token '<', '<!doctype'..."` when fetching clip data.

**Cause**: Flask returning HTML error pages (404/405/500) for API routes; JavaScript trying to parse HTML as JSON.

**Fix**: Added JSON error handlers for 404, 405, 500 in Flask. Hardened JS fetch: parse as text first, then `JSON.parse` with try/catch.

---

## BUG 4 — Docker build uploading 32GB on every build

**Symptom**: `docker compose build` takes forever, transfers ~32GB.

**Cause**: No `.dockerignore` file — all 48GB of VODs sent as build context.

**Fix**: Created `.dockerignore` excluding `vods/`, `clips/`, `config/`, `workspace/`, `.git`, docs, env files. Build context now ~107KB.

---

## BUG 5 — `os.setsid` AttributeError on Windows

**Symptom**: 500 error when clicking "Clip Selected" on Windows dashboard.

**Cause**: `os.setsid` is Linux-only; dashboard runs on Windows with Python 3.12.

**Fix**: Platform check: `os.setsid` on Linux, `CREATE_NEW_PROCESS_GROUP` on Windows. Also fixed `kill_pipeline` for cross-platform compatibility.

---

## BUG 6 — Dashboard can't see VODs ("No VODs found")

**Symptom**: Dashboard shows "No VODs found" despite 48GB of videos in `vods/`.

**Cause**: `app.py` used `BASE_DIR / "vods"` (= `dashboard/vods/`, an empty directory) instead of `PROJECT_DIR / "vods"` (= project root `vods/`).

**Fix**: Changed path resolution: `PROJECT_DIR = BASE_DIR.parent`, then `VODS_DIR = PROJECT_DIR / "vods"`. Removed empty `dashboard/vods/` and `dashboard/clips/` directories and their docker-compose mounts.

---

## BUG 7 — `processed.log` UnicodeDecodeError

**Symptom**: 500 error on `/api/vods` — `"utf-8 codec can't decode byte 0xff"`.

**Cause**: `processed.log` had UTF-16 LE BOM (FF FE) — likely written by a Windows tool. Python's default `read_text()` assumes UTF-8.

**Fix**: Reset file to empty UTF-8. Hardened reader with `encoding="utf-8", errors="replace"`.

---

## BUG 8 — Pipeline doesn't start from dashboard ("Waiting for pipeline")

**Symptom**: Clicking "Clip Selected" returns success but pipeline never starts; log viewer shows "Waiting for pipeline..." indefinitely.

**Cause**: Dashboard runs locally on Windows but spawns `bash clip-pipeline.sh` as a local process. The script needs:
- Ollama at `http://ollama:11434` (Docker internal network only)
- `faster-whisper`, `python3`, CUDA (only in container)
- VODs at `/root/VODs` (Docker mount)

Local bash process crashes immediately; stdout/stderr go to `DEVNULL` so no error is visible.

**Fix**: Dashboard now detects it's running outside Docker (`INSIDE_DOCKER` check). When on Windows host, uses `docker exec <container> bash /root/scripts/clip-pipeline.sh ...` to execute pipeline inside the running container. Pipeline stdout is piped to a local log file for SSE streaming. Stage files are polled via background thread running `docker exec cat /tmp/clipper/pipeline_stage.txt` every 2 seconds.

---

## BUG 9 — Early-VOD clip bias

**Symptom**: Most clips come from the first 30–60 minutes of multi-hour VODs.

**Cause**: LLM analyzes transcript chunks sequentially. Combined with top-N selection by score, early chunks fill the quota before later chunks are even considered. Also, keyword density tends to be higher early when the streamer is fresh.

**Fix**: Time-bucket distribution (Stage 4 Pass C). VOD divided into equal buckets, guaranteed picks from each before overflow fills remaining slots. Clips now spread across the entire timeline. See [[concepts/highlight-detection]].

---

## BUG 10 — Docker container dashboard crashes (zombie process)

**Symptom**: Dashboard inside Docker shows as zombie process (`<defunct>`).

**Cause**: Flask app inside Docker crashes on startup (e.g., missing dependency or import error). Since `entrypoint.sh` launched it with `&` (background), Docker still forwards port 5000 but nothing is listening.

**Status**: Not fully fixed. The local Windows dashboard is the primary interface. Container dashboard may need debugging separately.

**Workaround**: Run dashboard locally on Windows host (`python dashboard/app.py`) — it connects to the running container via `docker exec`. See [[entities/dashboard]].

---

## Whisper degenerate loop (known issue, not a bug per se)

**Symptom**: Whisper transcribes long audio and outputs only dots ("... ... ...") or repetitive "you you you".

**Cause**: Known upstream issue in faster-whisper with long audio files.

**Fix**: Stage 2 splits audio into 20-minute chunks before transcription. See [[entities/faster-whisper]].

---

## BUG 11 — apt-get fails during Docker build on Windows/WSL2

**Symptom**: `docker compose build` fails mid-layer with `E: Failed to fetch http://archive.ubuntu.com/...` — connection refused or timeout.

**Cause**: Docker BuildKit's isolated network on Windows/WSL2 has intermittent connectivity to `archive.ubuntu.com`. The default apt configuration makes one attempt per package with no retry or timeout — any transient DNS hiccup or dropped connection fails the entire layer.

**Fix**: Prepend apt retry/timeout config before `apt-get update` in **both** `Dockerfile` and `Dockerfile.ollama`:
```dockerfile
RUN echo 'Acquire::Retries "5";' > /etc/apt/apt.conf.d/80-retries \
    && echo 'Acquire::http::Timeout "30";' >> /etc/apt/apt.conf.d/80-retries \
    && echo 'Acquire::https::Timeout "30";' >> /etc/apt/apt.conf.d/80-retries \
    && apt-get update && apt-get install -y --no-install-recommends ...
```
This retries each package fetch up to 5 times with a 30-second timeout, surviving transient BuildKit network drops. Also ensure `zstd` is in the apt package list — newer Ollama versions use zstd-compressed archives and the installer fails silently without it.

---

## BUG 12 — Mixed mode falls back to CPU (OLLAMA_VULKAN disabled by default)

**Symptom**: In `mixed` or `vulkan` backend mode, Ollama logs "experimental Vulkan support disabled" and runs inference on CPU despite `GGML_VK_VISIBLE_DEVICES` being set.

**Cause**: Ollama 0.21+ ships with Vulkan disabled by default. Setting `GGML_VK_VISIBLE_DEVICES` alone is not enough — Ollama ignores it unless Vulkan is explicitly enabled.

**Fix**: Set `export OLLAMA_VULKAN=1` in `scripts/entrypoint-ollama.sh` for the `mixed` and `vulkan` backend cases, before calling `exec ollama serve`. Disabling CUDA (`CUDA_VISIBLE_DEVICES=""`) forces the code path that uses Vulkan.

---

## BUG 13 — `vulkaninfo` not found in container

**Symptom**: `docker exec ollama vulkaninfo --summary` returns "command not found".

**Cause**: `vulkan-tools` package not installed in `Dockerfile.ollama`.

**Fix**: Added `vulkan-tools` to the apt-get install list in `Dockerfile.ollama`. Rebuild with `docker compose build --no-cache ollama`.

---

## BUG 14 — Vulkan/mixed mode silently falls back to CPU when ICD fails

**Symptom**: Stage 3 (and all LLM stages) show high CPU usage but zero GPU utilization. `docker logs ollama` reports `inference compute library=cpu`. `vulkaninfo --summary` inside the container shows only `llvmpipe (LLVM)` — no discrete GPU hardware.

**Cause**: When `mixed` or `vulkan` backend is configured, `CUDA_VISIBLE_DEVICES=""` disables the CUDA path. If no real Vulkan GPU hardware is accessible (ICD init fails, `/dev/dxg` not mounted, Windows AMD driver not installed, or NVIDIA Vulkan ICD not injected by Container Toolkit), Ollama finds zero GPU devices and silently runs all inference on CPU. The pipeline continues to produce output but at ~10× the speed.

**Diagnostic output observed**:
```
# vulkaninfo --summary (inside container)
GPU0: deviceType = PHYSICAL_DEVICE_TYPE_CPU  ← only llvmpipe, no real GPU

# docker logs ollama
inference compute id=cpu library=cpu  ← CPU, not GPU
```

**Fix**: Added `count_real_vulkan_gpus()` helper to `scripts/entrypoint-ollama.sh` that runs `vulkaninfo --summary` before committing to Vulkan mode. If zero real (non-CPU) Vulkan devices are found:
- `mixed` and `vulkan` modes now **fall back to CUDA** automatically with a clear warning banner
- Inference runs on NVIDIA GPU instead of CPU
- The warning banner shows exact debugging steps for fixing Vulkan

**To confirm GPU is being used after fix**:
```bash
docker logs ollama 2>&1 | grep "inference compute"
# Should show: library=cuda (NVIDIA) or library=vulkan (Vulkan GPU)
# NOT: library=cpu
```

**Root cause of Vulkan ICD failure (WSL2)**:
- NVIDIA Vulkan ICD: Container Toolkit must inject it; `CUDA_VISIBLE_DEVICES=""` may interfere on WSL2
- AMD Vulkan ICD (Mesa DZN): requires AMD Adrenalin WSL2 driver installed on the Windows host, plus `/dev/dxg` and `/usr/lib/wsl` properly mounted

> [!warning] Mixed NVIDIA+AMD not yet confirmed working
> Even with the fallback fix, true mixed NVIDIA+AMD Vulkan inference has not been verified.
> The entrypoint will fall back to CUDA (NVIDIA-only) until both Vulkan ICDs initialize correctly.
> See [[concepts/deployment]] for setup requirements.

---

## BUG 15 — Qwen3.5 reasoning model returns empty content (token exhaustion)

**Symptom**: All Stage 4 chunks log "LLM call failed, skipping". Stage 6 shows "no JSON in response". LM Studio logs show `reasoning_tokens: 799, content: ""` with `finish_reason: "length"`.

**Cause**: `qwen/qwen3.5-9b` is a reasoning model. On the OpenAI-compatible endpoint in LM Studio, it spends its entire `max_tokens` budget on internal thinking (`reasoning_content`) and emits `content: ""`. The pipeline checks `if response:` — empty string is falsy, so all chunks fail.

**Important**: Stage 6 JSON truncation is a secondary symptom — even when some content IS generated, it gets cut off before the closing `}` because thinking already consumed most of the token budget. The JSON parse then fails because `rfind("}") == -1`.

**Diagnostic evidence from LM Studio logs**:
```json
"content": "",
"reasoning_content": "The user wants me to analyze...",  // 799 tokens
"finish_reason": "length"
```

**What does NOT work**: `/no_think` as a user-message prefix. This is a **Qwen3** feature that **Qwen3.5 dropped**. Despite appearing in Qwen3 documentation, it has no effect in Qwen3.5 models.

**Fix**: Use `chat_template_kwargs: {"enable_thinking": false}` in the LM Studio API request body — this is the correct LM Studio extension parameter. Applied at all three pipeline LLM call sites:
- Stage 3 payload: removed `/no_think` prefix, added `"chat_template_kwargs": {"enable_thinking": False}`, `max_tokens` raised 20 → 50
- Stage 4 `call_llm()`: same, default `max_tokens` 800 → 1500
- Stage 6 vision payload: same, `max_tokens` 800 → 1500

**LM Studio UI note**: The "When applicable, separate reasoning_content and content in API responses" toggle controls presentation only — it does NOT stop the model from thinking. Even with it enabled, `chat_template_kwargs: {"enable_thinking": false}` should suppress thinking. The token budget increase (max_tokens raised at all three sites) is the safety net: if thinking is not fully suppressed, the model now has room to finish reasoning AND produce content.

**max_tokens values after fix**:
- Stage 3: 50 → 1024
- Stage 4 `call_llm()`: 1500 → 3000 default
- Stage 6 vision: 1500 → 2000

**Diagnostics**: When `content` is empty, the pipeline now logs `finish_reason`, `reasoning_tokens`, and a preview of `reasoning_content`. This makes it possible to distinguish "model hit limit mid-thinking" from actual API errors.

---

## BUG 16 — LM Studio `/v1/models` flooded by dashboard status polls

**Symptom**: LM Studio logs show a constant stream of `GET /v1/models` requests, one every 3 seconds.

**Cause**: `check_lm_studio()` in `dashboard/app.py` calls `GET /v1/models` on every invocation, and `api_status()` is polled by the frontend every 3 seconds.

**Fix**: Added a 30-second time-based cache to `check_lm_studio()`. The result is cached in `_lm_studio_cache` and only re-fetched when the TTL expires. Reduces polling from 20× per minute to ≤2× per minute.

---

## BUG 17 — 35B+ models: `chat_template_kwargs` ignored, answer in `reasoning_content`

**Symptom**: With `qwen/qwen3.5-35b-a3b` (and potentially other large models), Stage 3 times out, Stage 4 logs all-chunk failures with `total_tokens=800`, Stage 6 shows all vision as failed. LM Studio logs show `finish_reason: stop` but `content: ""` and the full answer in `reasoning_content`.

**Cause**: Two compounding issues:
1. **`chat_template_kwargs: {"enable_thinking": false}` has no effect on the 35B MoE model** — it always routes its answer through `reasoning_content` and emits empty `content`, even when it finishes naturally (`finish_reason=stop`). This is model-specific: the 9B model respects this parameter, the 35B does not.
2. **Stage 4 Pass B had an explicit `max_tokens=800` override** at the call site (`call_llm(prompt, max_tokens=800)`) which overrode the function default of 3000 — this was the root cause of Stage 4 failures even before the reasoning_content issue.
3. **Stage 3 had `timeout=30`** — the 35B model needs 60–180 seconds for a single classification call (5–10s prompt processing + ~50s generation at ~15 tok/s).

**Diagnostic evidence**:
```json
"content": "",
"reasoning_content": "The user wants me to classify...\n\njust_chatting",
"finish_reason": "stop"   ← model FINISHED normally, answer is in reasoning_content
```

**Fix** (applied to `scripts/clip-pipeline.sh`):
1. **`reasoning_content` fallback**: When `content` is empty and `finish_reason == "stop"` and `reasoning_content` is non-empty, the pipeline now uses `reasoning_content` as the answer. Applied at all three LLM call sites:
   - Stage 3: scans `reasoning_content` for the segment type keyword
   - Stage 4 `call_llm()`: returns `reasoning_content` as the LLM response (JSON is parsed from it)
   - Stage 6 vision: parses JSON from `reasoning_content`
   - Token-limit case (`finish_reason="length"`) still falls through to retry as before
2. **Stage 4 call site fix**: `call_llm(prompt, max_tokens=800)` → `call_llm(prompt)` (uses 3000 default)
3. **Stage 3 timeout**: `timeout=30` → `timeout=180`

**Key distinction**: `finish_reason=stop` means the model finished naturally — its answer is in `reasoning_content`. `finish_reason=length` means it was cut off mid-thinking — retrying with more tokens is the right response.

---

## BUG 18 — Pipeline logs not persisted (lost after EXIT cleanup)

**Symptom**: Pipeline log at `/tmp/clipper/pipeline.log` is deleted when the cleanup trap runs on EXIT. No record of the run is available after the pipeline finishes.

**Cause**: The EXIT trap calls `rm -rf /tmp/clipper/*`, deleting the log file. Logs were only available during the run via SSE streaming from the dashboard.

**Fix**: Added a persistent timestamped log in `scripts/clip-pipeline.sh`. Every run now writes to both:
- `/tmp/clipper/pipeline.log` — ephemeral, for SSE streaming (still cleaned up on EXIT)
- `$CLIPS_DIR/.pipeline_logs/YYYYMMDD_HHMMSS_VODSLUG.log` — persistent, survives cleanup

The filename includes UTC timestamp and a sanitized VOD name slug (first 40 chars, alphanumeric/underscore/hyphen only). The log path is printed at pipeline startup: `=== Persistent log: ... ===`.

The `tee` command writes to both files simultaneously: `exec > >(tee -a "$PIPELINE_LOG" "$PERSISTENT_LOG") 2>&1`.

---

## BUG 21 — Stage 3 `max_tokens=1024` causes silent misclassification of all segments

**Symptom**: Stage 3 logs `Segment classify: empty content (finish=length, reasoning_tokens=1023)` for most chunks. All affected segments silently default to `just_chatting`. The segment map looks plausible but is largely incorrect — segments that should be `gaming`, `irl`, etc. are all classified as `just_chatting` by the fallback default.

**Why it's not obvious**: Most long VODs are predominantly `just_chatting` (~90%+), so the wrong default matches the correct answer most of the time. The pipeline continues and produces clips, making the misclassification invisible unless the monitor output is inspected.

**Cause**: Stage 3 `max_tokens=1024` — the 35B model uses all 1023/1024 tokens for reasoning (`finish=length`), leaving zero for the 1-word answer. The `reasoning_content` fallback (BUG 17 fix) only fires on `finish=stop` (natural termination), not `finish=length`. Chunks where the model finished naturally in under 1024 tokens (typically for highly distinctive transcripts like clear gaming or IRL content) produced correct classifications without warnings.

**Fix** (applied to `scripts/clip-pipeline.sh`):
1. `max_tokens` raised `1024` → `3000`: the 35B model needs ~1500–2500 reasoning tokens for classification; 3000 gives it room to finish naturally (`finish=stop`) and write the 1-word answer
2. Added `finish=length` tail-scan fallback: when still cut off, the last 600 characters of `reasoning_content` are scanned for the classification keyword. Models frequently write their tentative conclusion near the end of reasoning before being truncated (e.g., "...so this is just_chatting content" appears in the reasoning tail even when cut off)

---

## BUG 20 — 35B-A3B token exhaustion: thinking consumes all max_tokens, no content produced

**Symptom**: All Stage 4 chunks fail with `finish=length, reasoning_tokens=2999, total_tokens=3000, content=""`. Stage 6 fails on more demanding frames with `reasoning_tokens=1999, total_tokens=2000`. The `reasoning_content` fallback (BUG 17 fix) does NOT help because it only fires on `finish_reason=stop` (natural termination), not `finish_reason=length` (cut off mid-think).

**Root cause (confirmed from Qwen documentation and LM Studio bug tracker)**:

The `qwen3.5-35b-a3b` and `qwen3.5-9b` have **opposite defaults**:
- **9B**: thinking **disabled by default**. `chat_template_kwargs: {"enable_thinking": false}` is redundant (no-op) but harmless. Model answers directly with ~100–200 tokens.
- **35B-A3B**: thinking **enabled by default** AND LM Studio's OpenAI-compatible `/v1/chat/completions` endpoint does NOT forward `chat_template_kwargs` to the model's chat template for this model. Thinking cannot be disabled. Every call begins with `"Thinking Process:\n\n1. Analyze the Request:..."` and uses its full thinking budget before producing content.

The 35B-A3B model's default thinking budget is ~8,192 tokens. At `max_tokens=3000`, it consumes 2,999 tokens on reasoning, hits the limit, and emits `content=""`. The JSON answer is never written.

**Architecture note**: `35b-a3b` = 35 billion total parameters, ~3 billion activated per token (sparse MoE with 8 routed + 1 shared experts, 8.6% activation rate). The MoE routing and thinking mode are tightly coupled in the 35B variant in ways that differ from the 9B dense model.

**Fix** (applied to `scripts/clip-pipeline.sh`):
- `call_llm()` `max_tokens`: `3000` → `8000` — gives the 35B model room to finish its natural reasoning phase (~3000–6000 tokens) and still have budget for the JSON answer
- `call_llm()` `timeout`: `300` → `600` s — at ~30 tok/s, 8000 tokens takes ~267 s of generation + prefill; 600 s gives a 2× safety margin
- Stage 6 `max_tokens`: `4000` → `6000` — vision prompts are simpler but still need ~2000–4000 reasoning tokens on the 35B model
- `VISION_STAGE_TIMEOUT`: `1200` → `3600` s — 11 moments × ~220 s each exceeds the previous 20-minute limit

**Expected behavior after fix**: Model uses ~3000–6000 thinking tokens, then produces the JSON answer. `reasoning_content` fallback catches any `finish_reason=stop` edge cases. If the model still exhausts budget, increase `max_tokens` further (theoretical maximum before content is produced is ~8192 tokens of reasoning).

---

## BUG 19 — LM Studio queue backup: short timeouts cause cascading failures across all chunks

**Symptom**: With a 35B model, only 1 out of 44 Stage 4 chunks succeeds. Diagnostic: one chunk succeeds on attempt 2 immediately after attempt 1 times out, while all surrounding chunks fail all 3 attempts. Stage 6 vision shows "timed out" on the first frame of several moments.

**Cause**: LM Studio processes requests sequentially. When `call_llm()` had `timeout=120` and the 35B model needs 150–250 s per chunk:
1. Attempt 1 times out after 120 s, but LM Studio is still processing
2. Attempt 2 is immediately submitted — now TWO requests are queued in LM Studio
3. Attempt 3 is submitted — THREE requests queued
4. All 3 attempts fail and the chunk is skipped, but LM Studio's queue now has 3 abandoned requests to work through
5. The next chunk's attempt 1 arrives while LM Studio is still draining the previous chunk's queue → it also times out
6. This cascades: every chunk adds 3 more requests to LM Studio's backlog, eventually making all subsequent chunks impossible to process

The one chunk that succeeded (Chunk 2, attempt 2) did so because LM Studio happened to finish Chunk 1's request at that exact moment and processed Chunk 2 before the backlog grew further.

The same mechanism affects Stage 6: `VISION_PER_MOMENT_TIMEOUT=90` was too short for 35B vision calls (~150-200 s), causing the same abandonment pattern.

**Additional Stage 6 issue**: `max_tokens=2000` is too tight — the 35B model uses 1100–1999 reasoning tokens before writing the JSON answer (~100 tokens). When reasoning hits 1999/2000 tokens, `finish_reason=length` fires and content is empty. Successful calls used 1148–1690 reasoning tokens, so increasing to 4000 gives the model room to finish.

**Fix**:
- `call_llm()` default timeout: `120` → `300` s (35B calls typically complete in 150–250 s)
- Stage 6 `VISION_PER_MOMENT_TIMEOUT`: `90` → `300` s
- Stage 6 `max_tokens`: `2000` → `4000` (extra headroom for reasoning-heavy calls)

**Key principle**: The timeout must be set ABOVE the model's actual latency. A timeout below actual latency causes more requests to be submitted per chunk than LM Studio can drain between chunks, creating exponentially growing queue depth.

---

## Related
- [[entities/dashboard]] — BUGs 3, 5, 6, 7, 8, 10 are dashboard-specific
- [[entities/lm-studio]] — BUGs 15, 16, 17, 19 are LM Studio / pipeline integration bugs
- [[entities/faster-whisper]] — Whisper degenerate loop
- [[concepts/highlight-detection]] — BUG 9 (early-VOD bias fix)
- [[concepts/deployment]] — BUG 4 (build context), BUG 2 (Windows paths), BUG 11 (apt build), BUG 12–14 (Vulkan/GPU)
