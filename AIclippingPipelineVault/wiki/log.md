# Log

Append-only chronological record of wiki operations. Newest entries at top.

Format: `## [YYYY-MM-DD] operation | Title`
Grep recent: `grep "^## \[" wiki/log.md | head -10`

---

## [2026-05-01] update | "ClipT{T}" filename + hook caption fallback + pipeline elapsed-time report

User: "Some of the clips in the last run didn't get a title — they received a 'ClipT1805'. Make sure each clip gets a title since it also gets printed or embedded inside the video for the caption hook. Also at the end of the pipeline print how long it took to finish."

**Root cause of the "ClipT{T}" filenames** (`scripts/lib/stages/stage6_vision.py:150`): every moment got a baseline `entry["title"] = f"Clip_T{T}"` at Stage 6 start. Vision overrode that on success at `:594` (`if v_title and v_title != "": entry["title"] = v_title`). When vision failed (LM Studio outage, HTTP 400, parse error, stage timeout), the placeholder survived all the way to Stage 7. Stage 7's filename sanitizer at `scripts/stages/stage7_render.sh:33` does `''.join(c for c in title if c.isalnum() or c in ' -')` — that pattern keeps alphanumerics, space, dash but drops underscores. So `Clip_T1805` → `ClipT1805` → filename + hook caption.

The hook leak was a separate cause. `stage7_render.sh:41` had `hook = _scrub_field(m.get('hook', m.get('title', '')))` — when vision didn't produce a hook, the manifest builder fell back to the placeholder title, so the burned-in hook overlay also showed `ClipT1805`.

**Fixes**:

1. `scripts/lib/stages/stage6_vision.py` — added `_derive_baseline_title(why, category, T)` helper. New baseline derivation order: (1) first sentence of Pass B's `why` field, capped at 60 chars with a clean `...` ellipsis; (2) `<Category prettified> at MM:SS` with underscore-categories normalized (`hot_take` → `Hot take`). Replaced the `f"Clip_T{T}"` baseline with this. Vision still overrides on success — the change only affects what survives a vision failure. Verified across six representative cases (long `why`, empty `why`, underscore category, empty category, multi-sentence `why`, very long `why` triggering truncation).

2. `scripts/stages/stage7_render.sh:41` — changed `m.get('hook', m.get('title', ''))` → `m.get('hook', '')`. Hook now stays empty when vision didn't produce one. The bash check `[ -n "$HOOK" ]` is false → no overlay rendered. Cleaner than embedding a derived placeholder, since the title alone is already shown via the filename and the burned-in subtitle stream gives the user the moment's actual content.

3. **Pipeline elapsed-time report** — `scripts/clip-pipeline.sh` captures `PIPELINE_START_EPOCH=$(date +%s)` right after `mkdir -p "$TEMP_DIR"`, before the cleanup trap is registered. `scripts/lib/pipeline_common.sh::cleanup()` reads it at exit, computes the diff, and logs e.g. `Pipeline elapsed: 1h 47m 23s (6443s, exit=0)`. Format degrades gracefully under 1 hour to `Xm Ys`. The trap fires on every exit (success or failure) so the user sees timing for failed runs too. Also moved `PIPELINE_EXIT_STATUS=$?` to the very first line of `cleanup()` — it was previously after the diagnostics-dump block, which meant any side-effect command in that block could overwrite `$?` and silently corrupt the exit code reported in `pipeline.done`. The new placement is the canonical pattern for trap-captured exit status.

**Verification**: `bash -n` clean on `clip-pipeline.sh`, `pipeline_common.sh`, `stage7_render.sh`. `python3 ast.parse(..., feature_version=(3, 10))` clean on `stage6_vision.py`. Title-derivation tested against six representative inputs; `cleanup()` tested with a synthesized 125-second start offset and printed `Pipeline elapsed: 2m 6s (126s, exit=0)`.

Pages touched: this log entry. The vision-enrichment behavior that the title baseline change affects is documented at [[concepts/vision-enrichment]] (vision is non-gatekeeping; failed vision still ships the moment) — that page's "what survives a vision failure" claim is now strictly more accurate than it was when the baseline was the unhelpful `Clip_T{T}`.

---

## [2026-05-01] update | Dashboard LM Studio query: rewrite `host.docker.internal` → `localhost` when running on the Windows host

User reported the dashboard kept saying "LM Studio returned no models" even after a container restart, while the LM Studio server log clearly showed 12 models loaded and listening on port 1234. The Python log from the host-side dashboard run showed the smoking gun:

```
Failed to query LM Studio models: <urlopen error timed out>
127.0.0.1 - - [01/May/2026 18:42:52] "GET /api/models/available HTTP/1.1" 200 -
```

And the dashboard banner: `Dashboard mode: Windows host → docker exec`. The dashboard was running directly on the Windows host (`python dashboard/app.py`), not inside the container. `config/models.json::llm_url` is `http://host.docker.internal:1234`, which only resolves from inside a Docker container. From a Windows host shell, urllib's connect attempt times out (Docker Desktop registers the hostname in the host's hosts file but it points to a VM gateway IP that LM Studio doesn't bind to).

Verified directly:

```
http://host.docker.internal:1234/v1/models -> URLError: timed out
http://localhost:1234/v1/models           -> 12 models
http://127.0.0.1:1234/v1/models           -> 12 models
```

The pipeline (which always runs inside the container) is unaffected — `host.docker.internal:1234` still works there, as confirmed by the per-stage `bash`/`curl` calls returning 200 in the same session.

**Fix** (`dashboard/pipeline_runner.py`): added `_dashboard_llm_url()` helper. Reads `config/models.json::llm_url`. If `_state.INSIDE_DOCKER` is True, returns the URL unchanged. If False (host-side dashboard process), rewrites `host.docker.internal` → `localhost`. `check_lm_studio()` and `query_lm_studio_models()` both now route through this helper. Custom URLs (e.g. a remote LAN box on `10.x.y.z`) pass through unchanged — only the Docker-only hostname gets translated.

Also tightened the failure-print to include the URL the dashboard tried, so future timeouts log `Failed to query LM Studio models at http://localhost:1234: ...` instead of just `Failed to query LM Studio models: ...`. Makes the next debug pass instant.

**Verification**: `python3 ast.parse(..., feature_version=(3, 10))` clean. Three-URL probe from the host shell confirmed `localhost:1234` and `127.0.0.1:1234` both return 12 models, `host.docker.internal:1234` times out — exactly matching the user's symptom. After restarting the host-side dashboard, the Models panel populates.

Pages touched: this log entry. The [[entities/dashboard]] file inventory already noted that the dashboard runs in two modes (inside Docker vs on the Windows host); this is the LM-Studio-side adjustment that the host mode needed but lacked.

---

## [2026-05-01] update | Reverted Tier-4 model-profile picker; restored simple dropdown UX

User: "Previously there was no need for a model profile selection button — the user just needed to choose a model for the text and vision section dropdowns where the models were pulled from the LM Studio server API and the models like qwen and gemma had stars."

The Tier-4 ship had added a "model profiles" affordance: a row of buttons in the dashboard Models panel (`qwen35-9b`, `qwen35-35b`, `gemma4-26b`) that atomically swapped `text_model + vision_model + context_length` via a new `PUT /api/models/profile` endpoint. This duplicated the per-role dropdowns and pushed the user toward a fixed list of preset profiles instead of picking any model that LM Studio had loaded. It also created the BUG 52 trap — the `qwen35-35b` profile referenced `qwen/qwen3.5-35b-a3b` which wasn't in the user's downloaded models, and one click of that button bricked the next pipeline run.

**Reverted in 4 places**:

- `dashboard/static/modules/models-panel.js` — dropped `modelProfiles` / `activeProfile` module state, the `applyModelProfile()` exported function, and the `profileBar` HTML block above the role cards. Render is now `modelCards + ctxCard` (was `profileBar + modelCards + ctxCard`).
- `dashboard/static/app.js` — removed `applyModelProfile` from the `models-panel.js` import and from the `Object.assign(window, …)` exposure for inline `onclick=` handlers.
- `dashboard/routes/models_routes.py` — deleted the `PUT /api/models/profile` endpoint entirely. `GET /api/models` no longer returns `profiles` or `active_profile` keys (the JSON schema is back to `{config, roles, suggested, context_length_guide}`).
- `config/models.json` — dropped the `profiles` block, `active_profile` key, and the `_tier_4_note` paragraph. `text_model` and `vision_model` reset to `qwen/qwen3.5-9b` (the suggested baseline that's actually downloaded in the user's LM Studio per the 22:19 `/v1/models` listing) so the next pipeline run isn't blocked by BUG 52. `context_length` reset to 8192 to match the 9B baseline guidance. `_phase_5_note` (about the optional Pass B / Stage 6 split) preserved — that's about the unrelated Phase 5.1 feature, still live.

The Models panel now behaves exactly as before Tier-4: three dropdowns (Text / Vision / Whisper) populated from `GET /api/models/available` (which calls LM Studio's `/v1/models`), with stars (⭐) marking the suggested model from `_state.SUGGESTED_MODELS` (qwen3.5-9b for text + vision, large-v3 for whisper), a yellow "custom" badge that doubles as a one-click reset to the suggested model, a green "recommended" badge when the active model matches the suggestion, and a context-length card. No profile bar.

**BUG 52's `verify_models` startup probe stays in place** — the simpler UX makes it easier for the user to pick a loaded model, but the safety net is independent of which UI the user is using. If they edit `config/models.json` directly to a missing ID, or LM Studio unloads a model between runs, the pipeline still aborts at startup with the structured error rather than wasting hours on HTTP 400 fallbacks.

**Verification**: `node --check` clean on `app.js` + `models-panel.js`. `python3 ast.parse(..., feature_version=(3, 10))` clean on `models_routes.py`. `json.load` clean on `config/models.json`. `grep` for any remaining profile references in `dashboard/` and `config/` returns only stale `.pyc` files (rebuilt at next container start). The `applyModelProfile` symbol is fully removed from JS — verified with a final `grep -rn applyModelProfile dashboard/` returning nothing.

Pages touched: this log entry. The previous Tier-4 wiki sections that mention the profile picker (e.g. [[concepts/tier-4-conversation-shape]]) are left as-is for historical record — Tier-4 itself is still alive; only the UI affordance was reverted, the underlying `text_model`/`vision_model` writes go through the same dropdown-driven `PUT /api/models` endpoint they always did.

---

## [2026-05-01] update | BUG 52 — fail-fast model availability check (`verify_models`)

User reported "new errors when trying to load the qwen 3.5 models — this was not an issue before". A 22:19 run with `qwen/qwen3.5-35b-a3b` configured for both text + vision produced HTTP 400 on every LM Studio call (Stage 3 segment classifier failed for all 17 chunks, Pass B failed all 3 retries on chunk 1, etc.). The pipeline limped forward with each stage's individual fallback (segment classifier defaults to `just_chatting`, Pass B "skip chunk", Pass D "keep Pass C score"), wasting hours.

Root cause was visible in the LM Studio server log:

```
"error": {
  "type": "model_not_found",
  "message": "Model qwen/qwen3.5-35b-a3b not found in downloaded models"
}
```

The `/v1/models` listing in the same log shows the user has `qwen/qwen3.6-35b-a3b` (3.6, not 3.5) and `qwen/qwen3.5-9b` (the 9B not the 35B), but no `qwen/qwen3.5-35b-a3b`. Likely an upstream rename (3.5 → 3.6) that the config didn't follow, or a profile authored before the model was actually downloaded. Either way, configuration ≠ availability.

**Fix** (`scripts/lib/pipeline_common.sh:113`): added `verify_models()` function. Called once from `scripts/clip-pipeline.sh` after the model env reporting block and **before** `set_stage "Stage 1/8"`. Probes `GET $LLM_URL/v1/models` (5 s timeout, universal across LM Studio versions, no auth), parses the JSON model list, and verifies every unique configured ID (`TEXT_MODEL`, `VISION_MODEL`, `TEXT_MODEL_PASSB`, `VISION_MODEL_STAGE6` deduped) is present. On miss, exits 2 with a structured error: missing IDs, the **complete available list**, and three concrete fix paths (download, edit `config/models.json`, switch active profile). On unreachable LM Studio, logs a warning and returns 0 — preserves graceful-degradation for cached-transcription runs.

**Verified** via simulated `/v1/models` server: negative case (configured `qwen/qwen3.5-35b-a3b` against the user's actual available list) exits 2 with the full structured message. Positive case (configured `gemma-4-26b-a4b` against same list) logs `All 1 configured model(s) present` and continues. `bash -n` clean.

The user's specific config currently has `text_model` and `vision_model` set to `qwen/qwen3.5-35b-a3b` in `config/models.json`, plus a profile `qwen35-35b` referencing the same. They should either: (1) download `qwen/qwen3.5-35b-a3b` in LM Studio, (2) switch to the `qwen35-9b` or `gemma4-26b` profile in the dashboard, or (3) edit the `qwen35-35b` profile to point at `qwen/qwen3.6-35b-a3b` if that's the upgrade they intended. Did NOT silently change their config — let them choose.

Pages touched: [[concepts/bugs-and-fixes]] (new BUG 52 entry + index row + a BUG 51 index row backfilled while in the area).

---

## [2026-05-01] update | BUG 51 followup 3 — Stage 6 f-string (Python 3.10) + Pass D HTTP 400 (Gemma)

User asked why a 19:17 run finished prematurely with no clips, after BUG 51 followup 2 had landed. Pasted dashboard log was truncated mid-stream by SSE; the persistent log under `clips/.pipeline_logs/` shows the real ending: Stage 5 completed all 9 moments cleanly, Stage 6 model load was skipped (Pass B model = Stage 6 model), then Stage 6 crashed instantly with:

```
File "/root/scripts/lib/stages/stage6_vision.py", line 350
    }}"""
         ^
SyntaxError: f-string expression part cannot include a backslash
```

Two distinct bugs found in this investigation:

**1. Stage 6 f-string — `\n` inside `{...}` expression** (`stage6_vision.py:349`). The line builds a conditional JSON-template fragment:

```python
... |down"{(",\n  " + chr(34) + "callback_confirmed" + chr(34) + ": ...") if a2_active else ""}
```

The `\n` is inside the f-string `{...}` expression part. Forbidden in Python <3.12 (PEP 701 lifted the restriction in 3.12). Author had already used `chr(34)` instead of `"` to dodge a related escape issue — they just missed the `\n`. My earlier "Phase A fixed it as a side effect" claim was wrong: the AST audits ran on the host's Python 3.14, which accepted the f-string under PEP 701 grammar; the container runs Python 3.10 (Ubuntu 22.04 default) where it's a SyntaxError. **Generalized lesson**: future audits MUST use `ast.parse(src, feature_version=(3, 10))` to simulate the container's grammar, not bare `ast.parse`. Fix: replace `\n` with `chr(10)` to mirror the existing `chr(34)` workaround. Verified with `feature_version=(3, 10)` audit across all 12 extracted modules — 0 remaining issues.

**2. Pass D rubric judge HTTP 400 cascade — re-occurrence of BUG 33** (`stage4_rubric.py:214`). The same log shows all 9 Pass D LLM calls failed with `HTTP Error 400: Bad Request`. Cause: Pass D's payload included `"response_format": {"type": "json_object"}`, and the user's text model is `google/gemma-4-26b-a4b` — Gemma's chat completion endpoint rejects that field with 400. The graceful "keep Pass C score" fallback prevented a fatal error, but Pass D silently no-op'd on every moment, defeating the rubric judge entirely. Same fix as `lmstudio.py:59` (already in the codebase per BUG 33): drop `response_format` from the payload; rely on the existing freeform-JSON extractor + the `RETURN ONLY JSON:` prompt instruction.

**Why no clips**: Stage 6's SyntaxError was the actual blocker. Pass D's 400 cascade was secondary — with all 9 moments keeping Pass C scores, Stage 5 + 6 + 7 would have produced clips if Stage 6 hadn't crashed. So fixing Pass D doesn't unblock the run on its own; it just restores Tier-4 scoring quality. Both fixes are needed.

Pages touched: [[concepts/bugs-and-fixes]] (BUG 51 expanded with Followup 3 paragraph covering both fixes; the earlier "Phase A fixed the f-string side effect" claim removed since it was wrong).

---

## [2026-05-01] update | Retired MiniCheck Tier 2 + Lynx-8B Tier 3; cascade collapsed to 2 tiers

User asked whether the two sub-models could be replaced with a judge instance using the main model. Yes — the `llm_judge` path (originally Tier-3 A3 from [[concepts/moment-discovery-upgrades]]) was already wired, just opt-in. Promoted it to the canonical Tier 2 and removed the dead code.

**Removed**: `_TIER2_STATE` / `_load_minicheck` / `tier2_check` and `tier3_check` from `scripts/lib/grounding.py`; `scripts/lib/grounding_ab.py` (whole file); `requirements-grounding.txt` (whole file); `GROUNDING_STACK` build arg from Dockerfile; the `CLIP_GROUNDING_AB` block in `stage4_moments.py`; the `method="minicheck"` branch in `self_consistency.py`. The MiniCheck-Flan-T5-Large weight (~1.5 GB) and Lynx-8B weight (~5 GB) are no longer pulled.

**Kept and now load-bearing**: Tier 1 (regex denylist + content overlap + Phase 2.4d zero-count event check) — the structural safety net no LLM can defeat. `llm_judge` (5-dim weighted score, model-agnostic via `_resolve_judge_model`) — uses whichever model `CLIP_TEXT_MODEL` resolves to. `lmstudio.py` — HTTP transport for the judge.

**Trade-off**: self-judging inflates faithfulness ~5-15 pp vs independent judging. Defense is Tier 1's hard-event check (chat-event ground truth). `pass_threshold` (default 5.0) is the tuning knob — raise to 6.0+ if leniency proves a problem.

**Touched**: [[concepts/bugs-and-fixes]] (`REMOVAL 2026-05-01b` block + `BUG 33`/`BUG 34`/`BUG 44` flagged historical), [[entities/grounding]] (full rewrite), [[entities/grounding-ab]] (deleted), [[entities/self-consistency-module]], [[entities/lmstudio]], [[concepts/highlight-detection]], [[concepts/vision-enrichment]], [[concepts/self-consistency]], [[concepts/moment-discovery-upgrades]], [[concepts/chat-signal]], [[index]]. Source files: `scripts/lib/grounding.py`, `scripts/lib/self_consistency.py`, `scripts/lib/lmstudio.py`, `scripts/lib/stages/stage4_moments.py`, `scripts/lib/stages/stage6_vision.py`, `config/grounding.json`, `config/self_consistency.json`, `Dockerfile`.

---

## [2026-05-01] update | BUG 51 followup 2 — stage3_segments.py missing `import os` (Phase A defect)

User asked to investigate why the pipeline kept stopping prematurely. Reviewed the four most-recent persistent logs:

- 16:23:12 → `pipeline_common.sh: line 23: $'\r': command not found` (CRLF — pre-existing BUG 51 root cause)
- 16:26:44 → `stage3_segments.sh: line 19: n: command not found` (the `\n` literal — fixed by BUG 51 followup 1)
- 16:32:52 → `NameError: name 'os' is not defined` at `stage3_segments.py:15` ← **new bug, not covered by BUG 51 followups 1 or 2 (now this entry)**
- 16:46:41 → same `NameError` (the `\n` fix didn't help; this is a different defect)

Root cause: when Phase A extracted Stage 3's `python3 << PYEOF` heredoc to `scripts/lib/stages/stage3_segments.py`, the conversion translated four bash-interpolated lines (`LLM_URL = "$LLM_URL"`, etc.) into `os.environ` reads — but the original heredoc's first import line was `import json, re, sys, time` (no `os`), because the original bash-substituted form never needed it. Stage 4 (`stage4_moments.py`) and Stage 6 (`stage6_vision.py`) had `os` already in their original heredoc imports, so they shipped correctly. Stage 3 didn't.

**Verified scope** with an AST audit (`ast.walk` collecting every `Attribute` node + comparing against `Import`/`ImportFrom` results): 0 remaining missing-import issues across all 10 extracted Python modules in `scripts/lib/stages/` and `scripts/lib/stages/helpers/`. Stage 3 was the only affected file.

**Side benefit observed**: the 2026-05-01 01:52 pre-modularization run (44 KB log) had failed in Stage 6 with `SyntaxError: f-string expression part cannot include a backslash` at `<stdin>` line 356. That was a pre-existing bug in the unquoted heredoc form — bash mangling backslashes inside the f-string template before Python parsed it. Phase A extraction silently fixed it by moving the Python into a real file with no shell layer in between.

**LM Studio audit** (per user request): the load/unload calls are working correctly post-BUG-51-followup-1 — `unload` returns 404 (gracefully handled as "not supported, relying on JIT"), `load` returns 200, both bounded by `-m N` curl timeouts and wrapped in the heartbeat loop that bumps STAGE_FILE every 10 s. None of the recent failures are LM-Studio-side; all are downstream Python errors after a successful pre-load.

**Fix** (`scripts/lib/stages/stage3_segments.py:9`): `import json, re, sys, time` → `import json, os, re, sys, time`. AST parse + simulated runtime exec (clean namespace, env vars set) both pass.

Pages touched: [[concepts/bugs-and-fixes]] (BUG 51 expanded with Followup 2 paragraph + the f-string side-benefit note; `updated:` bumped to 2026-05-01).

---

## [2026-05-01] update | BUG 51 followup — load_model bounded + reachability probe + JIT fallback

User re-ran after the line-continuation fix and the pipeline STILL truncated at `[PIPELINE] Pre-loading 'google/gemma-4-26b-a4b'...`. Root cause: `curl -sf` had no `--max-time` and could hang indefinitely if `/api/v1/models/load` either didn't exist on the user's LM Studio version (it's undocumented — `/api/v0/...` is the native REST API; `/v1/...` is the OpenAI-compat layer) or LM Studio was wedged.

**Fix** (`scripts/lib/pipeline_common.sh`):
- `load_model` now probes `GET /v1/models` first (5 s timeout — the OpenAI-compat endpoint exists on every LM Studio version). If unreachable, skips the pre-load and relies on JIT.
- The `/api/v1/models/load` POST is bounded to 120 s (`-m 120`).
- HTTP status is captured and logged via a `case` over `%{http_code}`: 2xx success, 000 unreachable/timeout, 404 endpoint unsupported, 409/400 already loaded, `*` other. The pipeline always proceeds — the only difference is whether the model was warmed by pre-load or by JIT.
- `unload_model` got the same treatment (`-m 15`, HTTP-aware logging) — its previous `|| true` was hiding VRAM-management failures silently.

Net effect: pre-load and unload are pure best-effort. JIT is the correctness safety net. The dashboard's BUG-31 staleness gate stays inert because the heartbeat keeps STAGE_FILE fresh during whatever load time actually occurs.

Documented in [[concepts/bugs-and-fixes#BUG 51]] (Followup section).

---

## [2026-05-01] update | BUG 51 — fixed `\n` line-continuation breaking Stage 3 + Stage 6, added load_model heartbeat

User ran the pipeline post-Tier-4-ship and saw it truncate at:

```
[PIPELINE] Pre-loading 'google/gemma-4-26b-a4b' into LM Studio (context_length=32768)...
--- Pipeline finished ---
```

Two problems, fixed both:

1. **`stages/stage3_segments.sh:19` and `stages/stage6_vision.sh:36`** had the same broken `\n` line continuation as `stages/stage4_moments.sh:23` (which I fixed during the Tier-4 wire-in). After `load_model` returned, bash interpreted `... ENV="..." \n    python3 /path` as `... ENV="..." n python3 /path` — tried to exec a command `n`, failed with `n: command not found`, `set -euo pipefail` killed the pipeline, EXIT trap wrote `pipeline.done`, dashboard emitted "Pipeline finished". Fixed by removing the stray `\n` so the env-var-prefixed python invocation runs on a single line.
2. **`load_model` heartbeat** (`scripts/lib/pipeline_common.sh`) — even with the line-continuation fixed, a 26B Gemma + 32K context load blocks the curl call for 30-60+ seconds. STAGE_FILE goes quiet during that window; if a Docker Desktop hiccup flips `is_pipeline_running()` False, the dashboard's BUG-31 staleness gate trips. Same failure mode as BUG 48 but at the Stage 2 → 3 transition. Fix: background a touch loop (`( while sleep 10; do touch "$STAGE_FILE"; done ) &`) for the duration of the curl, killed with `wait` after the load returns.

Bonus housekeeping: stripped Windows-checkout CRLF line endings from 8 bash files (`pipeline_common.sh` + 7 stage files) — pre-existing issue that surfaced as `$'\r': command not found` on Stage 1. Updated `.gitattributes` with `*.sh`/`*.py`/`*.json` `eol=lf` so future Windows checkouts can't reintroduce CRLF.

Documented as [[concepts/bugs-and-fixes#BUG 51]].

---

## [2026-05-01] update | Tier-4 SHIPPED — all 8 phases landed in one session

Filed the per-phase Tier-4 plan earlier in the session, then user said "Ship all phases now". All 8 phases delivered.

**What ships:**

- **Phase 4.1** (model profiles) — `config/models.json` grew a `profiles` block (qwen35-9b / qwen35-35b / gemma4-26b — swappable, no enforced default). New `PUT /api/models/profile` endpoint atomically applies a profile. Dashboard models panel grows a profile bar above the existing role cards.
- **Phase 4.2** (conversation analytics) — new `scripts/lib/conversation_shape.py` extracts turn graph, off-screen voice intrusions (the Lacy-penthouse signal), discourse markers (story_opener / claim_stake / pushback / concession / topic_pivot / info_ramble_marker / agreement), monologue runs, topic boundaries via TextTiling cosine drop. Stdlib only — degrades cleanly when M1 diarization isn't on. Wired into Pass A as boost-only signals AND into Pass B's per-chunk prompt as a serialized shape block.
- **Phase 4.3** (Pattern Catalog) — new `config/patterns.json` with 10 closed-taxonomy interaction patterns. Pass B prompt rewritten to evaluate against named catalog patterns; LLM returns `primary_pattern` + `secondary_patterns` IDs that are validated against the catalog and propagated through Pass C.
- **Phase 4.4** (Pass D rubric judge — NEW phase) — new `scripts/lib/stages/stage4_rubric.py` runs after Pass C, before Phase 4.2 boundary snap. Per-moment LLM call scores 7 dimensions (setup_strength / payoff_strength / originality / broad_appeal / replay_value / audio_quality / self_contained); aggregates with weights from `config/rubric.json` into `rubric_score`; blends `final_score = 0.6 × pass_c_score + 0.4 × rubric_score`. Failure-soft (per-moment errors keep Pass C score; 3 consecutive network errors abort the pass). 10-20 LLM calls per VOD; ~3 min added to a 2-hr VOD.
- **Phase 4.5** (vision-as-shape-detector) — Stage 6 prompt extended with 4 new fields: `interaction_shape` (monologue / reading-chat / dialog-with-on-screen-guest / dialog-with-off-screen-voice / gameplay-with-commentary / silent-gameplay / multi-speaker-stage), `pattern_match`, `pattern_match_strength`, `gaze_direction`. Cross-validation across three channels (Pass B `primary_pattern` == Pass D `pattern_confirmed` == Stage 6 `vision_pattern_match`, all ≥0.6 strength) stamps `cross_validated_full` and `+0.1` to score (capped).
- **Phase 4.6** (MMR diversity) — new `scripts/lib/stages/stage4_diversity.py` re-ranks Pass D survivors via Maximal Marginal Relevance over sentence-transformer embeddings of each moment's `audit_one_liner` / `why`. Reuses M3 callback module's loaded model. Lambda 0.7 (configurable). Fails through to score-greedy ordering when sentence-transformers isn't available.
- **Phase 4.7** (style presets) — new `config/style_pattern_weights.json` with 5 new styles (`conversational`, `informational`, `freestyle`, `chatlive`, `spicy`). Each style maps to per-pattern boost/demote multipliers applied before MMR. `workspace/AGENTS.md` Discord agent aliases updated.
- **Phase 4.8** (eval runner) — new `scripts/lib/eval_tier4.py` compares pipeline-selected moments against user-curated reference labels and reports precision / recall + per-pattern recall breakdown.

**Stray bug fix bundled in:** `scripts/stages/stage4_moments.sh:23` had a broken `\n` line continuation in the python3 invocation (would have made bash exec a non-existent `n` command). Fixed alongside the Pass D / MMR wire-in.

**Verification:** `bash -n` clean on `clip-pipeline.sh` + `stages/stage4_moments.sh`. AST parse clean on `conversation_shape.py`, `stage4_moments.py`, `stage4_rubric.py`, `stage4_diversity.py`, `stage6_vision.py`, `eval_tier4.py`. All 5 new/modified JSON configs parse (`models.json`, `patterns.json`, `rubric.json`, `discourse_markers.json`, `style_pattern_weights.json`). End-to-end smoke test confirms `conversation_shape.analyze_chunk` correctly tags the Lacy-penthouse signature (claim_stake → off_screen_intrusion → concession) and `eval_tier4.evaluate` produces correct precision/recall on a synthetic test set.

Pages touched: [[concepts/tier-4-conversation-shape]] (added SHIPPED banner with file-by-file wire-in record), [[index]] (already had the Tier-4 entry).

---

## [2026-05-01] update | Tier-4 plan filed — conversation-shape detection + Pass D rubric judge

Filed the per-phase Tier-4 upgrade plan extending the existing Tier-1/2/3 work. The plan shifts moment detection from lexical (keywords + transcript reading) to structural+semantic (turn graphs, discourse moves, named interaction patterns) and introduces a dedicated LLM-as-judge phase (Pass D) that scores every Pass C survivor against a 7-dimension rubric using the same multimodal model.

**Targets seven nuanced clip classes** the current pipeline catches inconsistently: streamer reading-and-reacting-to-chat, controversial moments, storytelling arcs, informational rambles (financial / news / backstory / motivational / social-dynamics), Lacy-penthouse-class self-claim contradictions, rap battles / freestyles, and challenge-and-fold conversation patterns (the Neon/6ix9ine archetype).

**Architectural pillars:**
- **One model, multiple phases** — Stage 3 / Pass B / Pass D / Stage 6 all hit the same loaded multimodal model. No VRAM swap between phases.
- **Model swappable, no default** — `models_profile` block in `config/models.json` ships with Qwen 3.5 9B / 35B-A3B / Gemma 4 26B-A4B presets; future models slot in via config.
- **Pattern Catalog** in `config/patterns.json` (user-editable, hot-reloadable) replaces hard-coded keyword lists. 10 patterns ship initially.
- **Conversation shape signals** (turn graph, discourse markers, off-screen voice intrusions, monologue runs, topic boundaries) extracted stdlib from existing M1 diarization output.
- **Diversity-aware ranking via MMR** over Pass B `why` embeddings (reuses M3 callback module's loaded sentence-transformer).
- **5 new style presets** (`conversational`, `informational`, `freestyle`, `chatlive`, `spicy`) layered on top of existing emotion-coded styles.

**Phase breakdown:** 4.1 model profile → 4.2 conversation analytics → 4.3 Pattern Catalog + Pass B rewrite → 4.4 Pass D rubric judge (NEW phase) → 4.5 vision-as-shape-detector → 4.6 MMR diversity rank → 4.7 style preset extension → 4.8 eval and validation. ~24 hours total engineering work; ~3.5 min added to a 2-hour VOD's wall time.

Findings draw on conversational-analytics literature (Sacks/Schegloff/Jefferson turn-taking, DAMSL/SwDA dialog acts, TextTiling topic segmentation, Schiffrin discourse markers), highlight-detection literature (QVHighlights / Moment-DETR / CG-DETR query-conditioned retrieval; TVSum / YouTube-Highlights diversity-aware ranking; G-Eval / MT-Bench / Prometheus sub-score aggregation), and the multimodal capabilities of Gemma 4 + Qwen 3.5 (multi-image input, structured JSON output, long context, no need for tool-calling).

Pages touched: [[concepts/tier-4-conversation-shape]] (new — the plan itself), [[index]] (registered under Overview).

---

## [2026-05-01] update | Shipped all 4 modularization phases (A → C → D → B)

User said "ship all phases" after the modularization plan + wiki-update Stop hook had been filed earlier in the session. All four phases delivered in one session.

**Phase A — Heredoc extraction** (10 Python heredocs → `scripts/lib/stages/`):
- `stage4_moments.py` (1,913 lines, was the 1,902-line PYEOF at L780–2682) — Pass A keywords + Pass B LLM + Pass C select
- `stage6_vision.py` (676 lines) — vision enrichment
- `stage3_segments.py` (212) — segment classification
- `stage8_summary.py` (57) — clips_made.txt → summary.json
- `stage7_transcribe.py`, `stage7_meta.py`, `stage4_5_snap.py`, `stage6_5_campan.py`, `stage1_fetch.py`, `helpers/srt_rescale.py`
- Conversion pattern: bash-interpolated `"$VAR"` lines became `os.environ["VAR"]`; bash invokes the module via env-prefixed `python3 /root/scripts/lib/stages/<file>.py`. Verified via `python3 -c "import ast; ast.parse(...)"` per file. Removed orphan `)` lines left over from `MOMENT_META=$(... <<'PYMETA' ... PYMETA )` and the equivalent PYFETCH command-substitution. After phase A the bash file went 4,087 → 1,055 lines.
- Cleanup: deleted abandoned `_heredoc_tmp/` (untracked scratch from 2026-04-28).

**Phase C — Dashboard backend blueprints** (`dashboard/app.py` 1,612 → 78):
- `dashboard/_state.py` (161) — module-attribute pattern for cross-route mutable state (path globals get rebound by `_reload_path_globals`, so route modules read `_state.VAR` not `from _state import VAR`).
- `dashboard/config_io.py` (127) — load/save helpers for `config/*.json`.
- `dashboard/pipeline_runner.py` (462) — DetachedDockerPipeline class + spawn / poll / kill + LM Studio reachability. The BUG 31 detached-exec mechanic is preserved byte-for-byte; only the home moved.
- 8 Flask blueprints in `dashboard/routes/` — one per URL domain (pipeline, vods, models, hardware, paths, originality, music, assets); each is 30–259 lines.
- `app.py` reduced to bootstrap + `register_blueprint` calls. `__init__.py` makes `dashboard` a proper package; sys.path hack in `app.py` lets it run as either `python3 dashboard/app.py` (entrypoint.sh's invocation) or `python3 -m dashboard.app`.

**Phase D — Dashboard frontend ES modules** (`dashboard/static/app.js` 1,040 → 67):
- 8 modules under `dashboard/static/modules/`: `util`, `state`, `pipeline-ui`, `vods-panel`, `models-panel`, `hardware-panel`, `folders-panel`, `assets-panel`. Largest is `models-panel.js` at 256 lines.
- Switched `index.html` script tag to `<script type="module">`.
- Inline `onclick=` handlers in HTML reach functions through `window.*`, so `app.js` does `Object.assign(window, {...})` for the handlers it knows about (selectVod, onModelChange, resetModel, onHardwareDropdown, onFoldersChange, browseFolderFor, saveFolders, onOriginalityChange, browseMusicFolder, scanMusicLibrary, fetchAsset). All modules verified with `node --check`.

**Phase B — Bash decomposition** (`clip-pipeline.sh` 1,055 → 147):
- `scripts/lib/pipeline_common.sh` (111) — color vars + log/warn/err/info, set_stage, unload_model, load_model, rescale_srt wrapper, cleanup function, `trap cleanup EXIT`.
- 8 stage files under `scripts/stages/`: stage1_discovery (179), stage2_transcription (109), stage3_segments (22), stage4_moments (60 — Pass A/B/C orchestration shell that calls stage4_moments.py), stage5_frames (89), stage6_vision (63), stage7_render (352), stage8_logging (23). Sourced from the orchestrator (not executed) so they share globals + the EXIT trap.
- Updated `Dockerfile` with `COPY scripts/stages/ /root/scripts/stages/` and CRLF→LF normalization for both `scripts/lib/*.sh` and `scripts/stages/*.sh`. The runtime mount `./scripts:/root/scripts` in docker-compose.yml ensures live edits work without rebuild.

**Verification**: `bash -n` clean on orchestrator + every stage file + pipeline_common.sh. `python3 -c "import ast; ast.parse(...)"` clean on every new Python module (the `\`` SyntaxWarnings in stage4_moments and stage6_vision are pre-existing in the original heredocs — warnings, not errors). `node --check` clean on every JS file. Static structure verified; runtime end-to-end verification (a full pipeline run on a known VOD with byte-identical clip output diff against pre-extraction) was deferred since this is a refactor session and the user has the working pre-refactor state in git.

**Surface stats**: largest file in the codebase is now `scripts/lib/stages/stage4_moments.py` (1,913 lines) — single coherent algorithm, intentionally not split further. Bash orchestrator and dashboard entrypoint are both <150 lines, the original goal.

Pages touched: [[concepts/modularization-plan]] (every phase marked ✅ shipped + new "What actually shipped" table at the bottom), [[overview]] (clip-pipeline.sh description), [[concepts/clipping-pipeline]] (file layout description), [[entities/dashboard]] (file inventory + 5001 vs 5000 mismatch noted). CLAUDE.md updated: "Modularization in progress" subsection rewritten as "Modularization complete (2026-05-01)" with the new module map.

---

## [2026-05-01] update | Modularization plan + Stop-hook wiki-update enforcement

User asked for an audit of lengthy/monolithic files in the codebase and a plan to modularize them, plus a mechanism to ensure other agents update the wiki when they modify code.

**Audit findings (measured 2026-05-01)**:
- `scripts/clip-pipeline.sh` is **4,087 lines** (CLAUDE.md and overview.md both claimed ~1,700; clipping-pipeline.md claimed ~2,400 — all stale, all corrected). About 3,000 of those lines are embedded Python in 10 heredocs; the three biggest are Stage 4 Pass-B/C (1,902 lines), Stage 6 vision (665), Stage 3 segments (205).
- `dashboard/app.py` is **1,612 lines**, mixing Docker bridge + 7 unrelated config-API domains.
- `dashboard/static/app.js` is **1,040 lines** of free functions across 5 panels.
- `_heredoc_tmp/` (untracked, dated 2026-04-28) is leftover scratch from a previous abandoned Stage-4 extraction attempt.

**Plan filed**: [[concepts/modularization-plan]] — 4 phases (A heredoc extraction → C dashboard backend blueprints → D frontend ES modules → B bash decomposition last). Includes per-step file targets, line counts, a JSON-config passing convention to replace the current bash-variable interpolation, and per-phase verification criteria (`bash -n`, `python3 -c "import ast"`, end-to-end clip diff against a pre-extraction baseline).

**Enforcement mechanism**:
- `.claude/settings.json` (new, project-shared) registers two hooks.
- `Stop` hook → `.claude/hooks/check-wiki-updated.sh`: greps `git status --porcelain --untracked-files=all` for changes under `scripts/`, `dashboard/`, `config/`, `workspace/`, `Dockerfile`, `docker-compose.yml`. If any are present without a matching change under `AIclippingPipelineVault/wiki/`, exits 2 with a clear "BLOCKED" message + the list of unaccompanied code files. Agent is forced to continue and update the wiki before stopping.
- `PostToolUse` hook (matcher `Edit|Write|MultiEdit`) → `.claude/hooks/remind-wiki-on-code-edit.sh`: parses the tool input JSON for the edited path and prints a one-line stderr advisory if the path is in a watched dir. Non-blocking — just a JIT nudge.
- Both hooks tested with positive (code+wiki) and negative (code-only) cases on a synthetic git repo. Stop hook exits 0 / 2 correctly; PostToolUse fires on `scripts/*` and stays silent on `AIclippingPipelineVault/wiki/*`.
- Bypass for trivial whitespace/format changes: append a one-line entry to `wiki/log.md` explaining why no other update was needed. Hook only checks for *any* wiki touch, not content.

**Stale-claim cleanup**: bumped `updated:` on overview.md and clipping-pipeline.md to 2026-05-01; corrected the line-count claims; removed the obsolete "two Docker containers (ollama + stream-clipper)" mention in CLAUDE.md (project moved to LM Studio + single container per the 2026-04-18 retirement of the ollama entity).

Pages touched: [[concepts/modularization-plan]] (new), [[index]] (added under Concepts → System), [[overview]] (line count + updated date), [[concepts/clipping-pipeline]] (line count + modularization pointer + updated date). Project files touched: `CLAUDE.md` (Enforcement subsection + Modularization subsection + corrected overview), `.claude/settings.json` (new), `.claude/hooks/check-wiki-updated.sh` (new), `.claude/hooks/remind-wiki-on-code-edit.sh` (new).

---

## [2026-05-01] update | Removed Phase 4.1 chrome stage + Pass A' chat speed scoring

User asked to fix the pipeline by removing broken / latent subsystems after BUG 49 recurred (PaddleOCR wedging the pipeline mid-OCR on the last moment, this time only 8.5/9 moments and a single `--- Pipeline finished ---`).

**Removed**:
- Chrome stage entirely (Phase 4.1): `scripts/lib/chrome_mask.py`, `config/chrome.json`, `requirements-chrome.txt`, `config/streamers/`, the `CHROME_STACK` Dockerfile build arg, the chrome heredoc + Stage 6 consumer + `overlay_context_block` + `chrome_overlay_text` ref in the grounding cascade. Reasons: BUG 50 (MOG2 frame-spacing mismatch made the detector dead code) and BUG 49 (PaddleOCR C++-extension wedge truncated the pipeline; defense-in-depth couldn't fully bound it).
- Pass A' burst-factor + emote-density scoring contributions to the keyword-window signal count. Reason: chat is latent vs. the moment by 2-5 s; Pass A's window timing wasn't designed to absorb that.
- Pass B + Stage 6 multi-line chat_context informational blocks. Reason: same latency, plus prompt clutter.

**Preserved** (the actually-load-bearing piece): the grounding cascade's `hard_events` integration (`sub_count` / `bit_count` / `raid_count` / `donation_count`). Pass B and Stage 6 still call `CHAT_FEATURES.window(...)` per moment for hard-event ground truth and the cascade still hard-rejects "gifted subs" / "hype train" / "bit rain" claims when the ±8 s window shows zero events. Hard events are factual records, not timing measurements.

**Verification**: `bash -n` clean. AST parse on all 10 remaining python heredocs clean. No orphan references to chrome_overlay_text / overlay_context_block / chrome_mask / CHAT_SCORING_CFG / chat_window_stats / chat_stats / burst_factor in the pipeline. The dashboard's `chrome_regions` mention is the VLM-output field for `smart_crop` framing (unrelated).

Pages touched: [[concepts/chrome-masking]] (tombstoned with REMOVED banner; historical content preserved), [[entities/chrome-mask-module]] (tombstoned), [[concepts/clipping-pipeline]] (Phase 4.1 section dropped), [[concepts/chat-signal]] (scoring marked removed; hard-event integration kept), [[entities/chat-features]] (consumer-scope-narrowed note added), [[concepts/bugs-and-fixes]] (REMOVAL entry above BUG 49).

---

## [2026-04-30] update | BUG 49-50 — chrome OCR hang protection + MOG2 frame-spacing root cause

The 2026-04-30 third production run validated all eight previous chrome-stack fixes (BUG 40-48) — Pass B/Pass B-Global/Pass C selected 10 cross-validated moments cleanly, Stage 4.5 grouped them, Stage 5 extracted frames, the chrome heredoc spawned without errors, OCR loaded with PIR + oneDNN disabled, and 9.5/10 moments processed with the expected ~5 s per OCR call. Then the run truncated mid-iteration on moment 10 (`frames_11429_t0.jpg` OCR never produced output) and the dashboard rendered `--- Pipeline finished ---` twice. Stages 6/7/8 never executed.

**BUG 49 — Chrome PaddleOCR can wedge indefinitely on a single frame.** Once-per-VOD on long runs, never reproducible against the same frame in isolation. Likely triggers: PaddleOCR's C++ extension stuck in oneDNN's CPU thread pool, transient memory pressure during the angle-classifier sub-model swap, or first-call detection-network inference on an image with unusual aspect ratio. The `python3 - <<'PYCHROME'` heredoc has no escape — bash blocks until python exits. The 30-second STAGE_FILE heartbeat from [[concepts/bugs-and-fixes#BUG 43]] is per-MOMENT, not per-FRAME, so a 60+ second wedge inside `extract_overlay_text` leaves STAGE_FILE untouched the whole time. Combined with a Docker Desktop hiccup that flips `is_pipeline_running()` False, the dashboard's staleness gate fires and SSE emits `done` while the script is still wedged. **Fix:** three layers of containment. (1) `scripts/lib/chrome_mask.py` adds a SIGALRM-based per-call timeout in `extract_overlay_text` (default 30 s, configurable via `chrome.json::ocr.per_call_timeout_seconds`); a wedged call now raises `_OCRTimeout` and the per-moment try/except resumes with the next moment. (2) New `heartbeat` callback parameter on `process_moment` and `extract_overlay_text` — the chrome heredoc passes a closure that bumps STAGE_FILE on every per-frame OCR call, so even a full-timeout 30 s hang doesn't stale STAGE_FILE. (3) `scripts/clip-pipeline.sh` wraps the entire chrome heredoc in `timeout 600 env STAGE_FILE_PATH=... FLAGS_...=0 python3 -` — outer wallclock ceiling for the case where SIGALRM is swallowed by the C++ extension. `env` is required to forward env vars through `timeout`.

**BUG 50 — MOG2 misfires 100 % on Stage 5's frame layout; BUG 42's first-frame priming was structurally insufficient.** After [[concepts/bugs-and-fixes#BUG 42]] primed the GMM correctly, every Stage 5 moment STILL trips the `max_masked_area_ratio=0.35` safeguard (10/10 misfires logged in the 2026-04-30 run). The root cause isn't the GMM warmup — it's frame spacing. MOG2 is designed for sub-second video-rate background subtraction; Stage 5 extracts at offsets `[-2, 0, +1, +2, +3, +5]` seconds (1-3 s spacing across a 7-second span). Natural streamer movement between samples — head turn, hand gesture, expression change — accumulates to >35 % "foreground" easily. MOG2 isn't wrong; it's just being asked the wrong question. **Fix:** wontfix as a code change; document the structural mismatch and adjust expectations. Updated `detect_transient_overlays` docstring with a `Caveat (BUG 50)` paragraph; updated the misfire log line to reference BUG 50 explicitly so future operators see the failure and immediately understand it's expected, not actionable; promoted OBS overrides as the canonical chrome-detection path. Possible future work: sample 6 ADDITIONAL frames at 0.5 s spacing specifically for MOG2; replace MOG2 with frame-differencing or a small object-detection net targeted at common overlay shapes. Deferred behind a working eval harness.

**Verification:** `bash -n` clean. `python3 -c "import ast; ast.parse(open(...).read())"` on chrome_mask.py clean. Chrome heredoc body parsed via regex extract + AST clean (4758 chars, 121 lines). The `env` placement preserves the existing FLAGS_enable_pir_in_executor=0 / FLAGS_use_mkldnn=0 forwarding from BUG 47.

Pages touched: [[concepts/bugs-and-fixes]] (BUG 49/50 entries, index updates), [[concepts/chrome-masking]] (BUG 50 caveat callout, BUG 49 timeout reference).

---

## [2026-04-30] update | BUG 46-48 — chrome stage end-to-end repair pt.2 + BUG-31 redux at VRAM swap

The 2026-04-30 second production run validated five of the six previous-session fixes (Pass B chunks completed, scoring transparent with raw_score, Stage 5 reached 10/10 chrome moments) but exposed three NEW failures:

**BUG 46 — My own visibility-fix comment was the bash error that previously blamed my own code's PASS B-Global parser.** Line 2160's comment had markdown backticks around `{"arcs": []}` inside the unquoted Stage 4 heredoc; bash command-substituted them on every run. Non-fatal but noisy (`line 780: {arcs:: command not found`). Found a second site at line 3451 (Stage 6 vision `max_tokens` comment) — same pattern. **Fix:** strip markdown backticks; add explicit warning comments at each site; ran new `AUDIT2` script to verify zero raw backticks across all three unquoted heredocs (Stage 3, Stage 4, Stage 6). Lesson: [[concepts/bugs-and-fixes#BUG 39]]'s AST verifier passes Python, but command-substitution noise inside comments doesn't break Python parsing — need a separate static check, now wired.

**BUG 47 — PaddleOCR 3.x PIR + oneDNN backend.** Every `.predict(p)` call after [[concepts/bugs-and-fixes#BUG 41]]'s fix raised `(Unimplemented) ConvertPirAttribute2RuntimeAttribute not support [pir::ArrayAttribute<pir::DoubleAttribute>]`. The PIR executor's oneDNN attribute conversion isn't fully wired in PaddleOCR 3.x. **Fix:** disable PIR + oneDNN BEFORE paddle imports, three layers deep — `os.environ.setdefault("FLAGS_enable_pir_in_executor", "0")` and `"FLAGS_use_mkldnn", "0"` at the top of `chrome_mask.py` (canonical site), repeated in `_get_paddle_ocr()` (belt), and exported in the bash invocation env (suspenders). Also pass `enable_mkldnn=False` to the constructor when accepted. The cascade covers every realistic import-ordering scenario.

**BUG 48 — Stage 5 → Stage 6 BUG-31 staleness gate fires during VRAM swap.** With chrome processing now reaching `processed 10/10 moments` cleanly, the dashboard immediately rendered "Pipeline finished" because the per-moment heartbeat ([[concepts/bugs-and-fixes#BUG 43]]) ends with the heredoc and the VRAM unload+load cycle (20-40 s on Gemma 26B) goes that whole time without bumping STAGE_FILE. A Docker Desktop hiccup during the swap flips `is_pipeline_running()` False, the staleness gate sees ≥30 s of STAGE_FILE quiet, and SSE prematurely emits done. **Fix:** add an early `set_stage "Stage 6/8 — Vision Enrichment (loading model)"` BEFORE the unload/load round so STAGE_FILE bumps before the long swap. The existing `set_stage "Stage 6/8 — Vision Enrichment"` after the swap remains; operators briefly see "loading model" then the steady stage label.

**Verification:** `bash -n` clean. New `AUDIT2` raw-backtick scan reports zero sites. Heredoc end-to-end verifier passes all 11. The PaddleOCR FLAGS chain is set in 3 independent places per file → import-ordering should not regress.

Pages touched: [[concepts/bugs-and-fixes]] (BUG 46/47/48 entries, index updates).

---

## [2026-04-30] update | BUG 41-45 + 37c — Stage 5 chrome end-to-end repair, Tier 3 timeout, manifest sanitization

The 2026-04-30 production run revealed a six-bug compound failure mode in Stage 5 + Stage 7. After [[concepts/bugs-and-fixes#BUG 40]] unmasked the chrome stage, every dependent code path that hadn't actually run since the upgrade landed surfaced its own latent breakage. Fixed all six in one session.

**BUG 41 — PaddleOCR 3.x `cls=` kwarg removal + return-shape change.** `chrome_mask.extract_overlay_text` calls `ocr.ocr(p, cls=True)`. PaddleOCR 3.x renamed `.ocr()` → `.predict()` (with `.ocr()` kept as a thin alias) and removed `cls=` entirely. Every OCR call raised `TypeError: PaddleOCR.predict() got an unexpected keyword argument 'cls'`. **Fix:** cascade through `.predict(p)` → `.ocr(p)` → `.ocr(p, cls=True)` per frame; extract a new `_parse_ocr_result()` helper that handles both 3.x dict format (`rec_texts`/`rec_scores`) and legacy 2.x list-of-pairs format.

**BUG 42 — MOG2 first-frame seed misfire.** `detect_transient_overlays` was applying every Stage-5 frame to a fresh `BackgroundSubtractorMOG2` and OR'ing the masks into `accum_mask`. The first `apply()` call has no learned background so MOG2 returns a 100 %-foreground mask, which dominates `accum_mask` and trips the `max_masked_area_ratio=0.35` safeguard. **Fix:** prime MOG2 by feeding the seed frame 5× before measurement, then accumulate only `imgs[1:]`. Sane talking-head windows now produce 0-3 % masked area instead of 100 %.

**BUG 43 — Stage 5 chrome stage's non-zero exit propagates and kills the pipeline.** With `set -euo pipefail`, an in-script crash inside the chrome heredoc (or BUG 31 staleness during a long PaddleOCR init) terminated the whole run before Stage 6/7/8. **Fix:** wrap the heredoc with `|| warn`, wrap each per-moment iteration in `try/except`, and inject a per-moment `STAGE_FILE` heartbeat so the dashboard's BUG-31 staleness gate doesn't fire during chrome processing. Defense in depth — each fix closes one failure mode.

**BUG 44 — Tier-3 grounding cascade timeouts on Gemma routing.** When LM Studio has Gemma 4-26B loaded but `tier_3.lm_studio_model="llama-3-patronus-lynx-8b-instruct"`, requests route to Gemma anyway. Gemma's permanent thinking burns 3000-5000 reasoning tokens before emitting the verdict and routinely overruns 45 s as `[LMSTUDIO] call failed: timed out` per ambiguous-zone moment. **Fix:** `config/grounding.json::tier_3.timeout_s 45 → 120`; new `tier_3.max_tokens` field defaulting to 3500 (was hard-coded 200 in `tier3_check`); thread the value through `cascade_check` and `lmstudio.chat`.

**BUG 45 — Stage 7 manifest description newline corruption.** Stage 7's pipe-delimited manifest is read line-by-line by `while IFS='|' read -r ... done`, but `description` was passed verbatim from the LLM. A multi-line description splits the record across two lines and shifts trailing fields into the next iteration. **Fix:** new `_scrub_field()` helper replaces `|` / `\r` / `\n` in description (capped 500 chars), hook, category, segment_type. Title keeps its existing alnum+space-hyphen restriction.

**BUG 37c — A2 callback_confirmed multiplier reintroduced the 1.0 clamp at Stage 6.** Stage 6's `entry["score"] = round(min(pre * a2_mult, 1.0), 3)` undoes BUG 37's ranking-on-raw-scores fix at this site — two strong A2-boosted callbacks both display `score=1.000` and Stage 7's sort breaks ties by insertion order. **Fix:** track uncapped product as `raw_score` on the entry (BUG 37 pattern); update Stage 6 sort to prefer `raw_score`; print both values in the A2 log line. Clamped `score` still serializes for UI consumers.

**Pre-emptive Stage 6 fix.** Bumped Stage 6 vision `max_tokens 6000 → 8000` to cover Gemma vision worst case (BUG 38 family — Gemma can burn 4000-6000 reasoning tokens on the multi-frame prompt with the A2 verification block).

**Verification:** `bash -n clip-pipeline.sh` clean. `ast.parse(chrome_mask.py | grounding.py)` clean. End-to-end heredoc verifier (extract → bash-substitute → `ast.parse`) confirms all 11 heredocs Python-clean post-bash interpretation.

Pages touched: [[concepts/bugs-and-fixes]] (BUG 41/42/43/44/45/37c new entries, index updates).

---

## [2026-04-28] fix | BUG 40 + BUG 37b — Stage 5 chrome OCR + Pass C score visibility

After [[concepts/bugs-and-fixes#BUG 39]] unmasked Stage 5 the next run completed Stage 5's chrome masking but logged two visible problems plus a possible silent termination before Stage 6.

**BUG 40 — PaddleOCR 2.7+ removed `use_gpu`.** `scripts/lib/chrome_mask.py::_get_paddle_ocr()` was constructing `PaddleOCR(use_gpu=..., show_log=False, ...)`; new versions reject those kwargs with `TypeError` and the except branch logged "unavailable", silently disabling overlay-text ground truth for Stage 6. **Fix:** try the new `device='cpu'`/`'gpu'` API first; fall back to legacy kwargs on `TypeError`.

**BUG 37b — Pass C log clamp hides ranking distinction.** [[concepts/bugs-and-fixes#BUG 37]]'s fix kept ranking on unclamped raw scores but logged only the clamped 0-1 display value, so 9 of 10 selected clips appeared to tie at exactly 1.000 even though the ranking was differentiating them. **Fix:** carry the raw `final_score` through into each `output` entry as a new `raw_score` field; the `[PASS C] Selected N moments` line now prints `score=N.NNN raw=N.NNNN` side by side.

**Stage 5 → Stage 6 transition diagnostic.** The user's run also showed `--- Pipeline finished ---` (the dashboard SSE marker) right after `[CHROME] processed 10/10 moments`, with no Stage 6 log. Likely [[concepts/bugs-and-fixes#BUG 31]] redux — the dashboard's SSE done-emission fires when STAGE_FILE mtime is ≥ 30 s old AND `is_pipeline_running()` returns False; Stage 5 hasn't called `set_stage()` since 2804 so STAGE_FILE was 30+ s stale by the time chrome finished. To narrow the next investigation, added a `[PIPELINE] Stage 5 chrome+OCR pass complete; preparing Stage 6 model state.` log line right after `PYCHROME` ends — if the persistent log shows this line then the regression is between here and Stage 6's heredoc; if not, the chrome heredoc itself died after its last print.

`bash -n` clean, all three Python heredocs (Stage 3/4/6) parse Python-clean post-bash interpretation.

Pages touched: [[concepts/bugs-and-fixes]] (BUG 40 entry, BUG 37b follow-up entry, index updates).

---

## [2026-04-28] fix | BUG 39 — Stage 4 raw backticks in unquoted heredoc (BUG 29 redux)

After [[concepts/bugs-and-fixes#BUG 38]] fixed Stage 3's token-starvation crash, the next run completed Stage 3 and crashed at the start of Stage 4 with `bash: command substitution: syntax error near unexpected token` and a Python `SyntaxError: unmatched ')'` from a heredoc body that bash had mangled. Two unfixed BUG 29 sites:

- `scripts/clip-pipeline.sh:2159-2160` — Tier-3 A1 markdown-fence parsing used raw `"\`\`\`"`. Shipped with A1 on 2026-04-27 and never exercised because Stage 3 always died first.
- `scripts/clip-pipeline.sh:2465` — BUG 37's reference comment had markdown-style `\`min(... * lp, 1.0)\``.

**Fix:** escaped both via the `\\\`` pattern already used at lines 1439-1440 and 3416-3417. Built an end-to-end heredoc verifier (`bash << PYEOF` expansion → `ast.parse`) that catches the failure mode regardless of which line introduced it; ran it on Stage 3, 4, and 6 — all clean.

Pages touched: [[concepts/bugs-and-fixes]] (BUG 39 entry + index).

---

## [2026-04-28] fix | BUG 38 — Stage 3 / Tier-1 Q1 / Tier-3 A1 token starvation on Gemma 4

Pipeline ran Stage 1+2 then died after Stage 3's 19th segment classification on a 193-min VOD with `google/gemma-4-26b-a4b`, with no Stage 4–8 output and zero clips produced. Three LLM call sites in `scripts/clip-pipeline.sh` were budgeted for Qwen 3.5's reasoning behavior (which honors `chat_template_kwargs={enable_thinking: False}` and `/no_think`) but Gemma 4-26B-A4B's permanent thinking mode in LM Studio ignores both — burning 3000–6000 reasoning tokens regardless and leaving no headroom for the answer.

**Fix in `scripts/clip-pipeline.sh`:**
- Stage 3 segment classification (line 653): `max_tokens=3000 → 6000`.
- Tier-1 Q1 chunk_summary (line 2022): `max_tokens=200 → 4000`. Q1 had been silently degraded since shipping — every chunk's summary fell back to the 12-word transcript snippet on Gemma, hollowing out the cross-chunk callback signal that motivates Q1.
- Tier-3 A1 Pass B-global (line 2143): `max_tokens=2000 → 6000`. A1 had been silently dropping its global arc-detection JSON to `{}` on Gemma.

All three sites share the same failure pattern: `/no_think` + tight budget assumes Qwen-class thinking, breaks on Gemma 4. Fixing only Stage 3 would have left Tier-1 Q1 and Tier-3 A1 silently broken on Gemma — the moment-discovery-upgrades plan's cross-chunk signals only flow when all three are sized correctly.

`bash -n` clean. Pages touched: [[concepts/bugs-and-fixes]] (BUG 38 entry + index), [[concepts/moment-discovery-upgrades]] (status snapshot + Gemma warning callout).

---

## [2026-04-28] lint | Wiki gardening — graph color + broken-link sweep

Wiki-only pass to make the Obsidian graph more colorful and remove orphan stubs. No code or behavior changes.

**Hub pages added:**
- [[concepts/moment-discovery-upgrades]] — central node for the Tier-1/2/3 plan; backlinked from every Q1–Q5 / M1–M3 / A1–A3 page (resolves 16 broken `[[../moment_discovery_upgrade_plan]]` references that were rendering as orphan stubs)
- [[sources/implementation-plan]] — Phase 0–5 hub for the IMPLEMENTATION_PLAN.md roadmap (resolves 7 broken `[[IMPLEMENTATION_PLAN]]` references)

**Tag taxonomy normalized across ~50 pages.** Added consistent dimensional tags so graph color groups light up cleanly:
- `stage-1` … `stage-8` — pipeline stage
- `pass-a` / `pass-b` / `pass-c` — Stage 4 detection passes
- `tier-1` / `tier-2` / `tier-3` — moment-discovery upgrade tier
- `audio` / `vision` / `text` / `chat` / `video` — modality
- `model` / `module` / `infrastructure` / `interface` / `signals` — role
- `hub` — pages that act as semantic anchors
- `retired` / `historical` — lifecycle

**`graph.json` color groups extended.** Added 13 tag-based color groups (hub, retired, tier-3, tier-2, pass-a, pass-b, stage-2, stage-7, originality, signals, module, infrastructure, interface) on top of the existing six folder-based groups. First-match-wins; tag groups are more specific so they take precedence.

**Cross-link strengthening.** Tier-2 sibling links added between [[entities/audio-events]], [[entities/diarization]], and [[entities/callback-module]] so the M1/M2/M3 cluster is fully meshed in the graph.

Pages touched: index, log, all 50 wiki pages (frontmatter only for tag normalization), `.obsidian/graph.json`. New: [[concepts/moment-discovery-upgrades]], [[sources/implementation-plan]].

---

## [2026-04-28] fix | Tier-2 M2 hang on long VODs

Pipeline hung at "Tier-2 M2: scanning audio events (rhythmic / crowd / music)..." on a 193-min VOD with no further log output. Root cause: `audio_events.py::detect_window` called `librosa.load(audio_path, offset=t, duration=window_size)` once per Pass A window (~1160 times for a 3-hour VOD), re-opening the WAV file each iteration. Combined with stderr being block-buffered through `2>&1 | tee` in the bash invocation, the operator saw zero progress.

**Fix in `scripts/lib/audio_events.py`:**
- Refactored `scan_audio_events()` to load the audio ONCE up-front and slice the in-memory waveform per window. Per-window cost now reflects only the actual feature work (~200-300 ms HPSS dominated). 3-hour VOD: ~5 min instead of "appears hung."
- Added `_run_detectors(y, sr, librosa, np)` helper so the loop calls feature ops directly instead of going through file I/O on every iteration.
- Added per-100-window progress logs with explicit `sys.stderr.flush()` so callers see updates in real time even when stderr is block-buffered.
- Catch `MemoryError` on the up-front load and write an empty events file so VODs > ~6 hours degrade cleanly instead of crashing.

**Fix in `scripts/clip-pipeline.sh`:**
- Switched M2 invocation from `2>&1 | tee -a "$PIPELINE_LOG" >&2` to `python3 -u "$LIB_DIR/audio_events.py" ... 2> >(tee -a "$PIPELINE_LOG" >&2)` — matches the `speech.py` invocation pattern. The `-u` flag disables Python output buffering and process substitution avoids the pipe-buffering issue, so progress lines reach the operator's terminal as they're emitted.

`detect_window()` (the load-per-call CLI helper) is preserved for ad-hoc inspection but documented as DO-NOT-USE-IN-A-LOOP.

Verified: `bash -n` clean; `py_compile` clean; graceful-degradation smoke test still emits the same `librosa_missing` / `load_failed` skip reasons.

Pages touched: [[entities/audio-events]] (cost table updated; the BUG callout names the prior implementation's symptom).

---

## [2026-04-27g] update | Judge rename + A/B harness (Tier-3 A3 follow-up)

Two changes building on Tier-3 A3 ([[#2026-04-27f]]):

- **Renamed `gemma_judge` → `llm_judge`** in `scripts/lib/grounding.py` to drop the model-family assumption from the API. New `_resolve_judge_model()` helper picks the model in order: explicit `cfg["model"]` → `CLIP_GROUNDING_JUDGE_MODEL` env → `CLIP_TEXT_MODEL` env (which `clip-pipeline.sh` already sets from `config/models.json`) → fallback `qwen/qwen3.5-9b`. So switching from Gemma to Qwen, Llama, or any other LM Studio chat model requires no code change. **Back-compat** preserved: the old `gemma_judge` function alias, `_gemma_weighted_score` alias, and `gemma_judge` config key all keep working. Default fallback model changed from gemma-specific to qwen-neutral.

- **Built `scripts/lib/grounding_ab.py`** — A/B harness that runs both MiniCheck and the LLM judge over the same Pass B output and emits a 2×2 confusion matrix (`mc_pass × judge_pass`) plus the `P(judge pass | mc fail)` lenience indicator. Two modes:
  1. **CLI**: `python3 scripts/lib/grounding_ab.py --diagnostics path.json --out comparison.jsonl` — ad-hoc one-off
  2. **Pipeline toggle**: env var `CLIP_GROUNDING_AB=true` (default off) runs the comparison automatically right after Pass B / Pass B-global writes `llm_moments.json`, producing `/tmp/clipper/grounding_ab.json` + `/tmp/clipper/grounding_ab.jsonl`

  Output answers the §A3 risk-register question ("is the judge rubber-stamping its own claims?") with data instead of the plan's manual-rating gut check. Per-disagreement details stream to JSONL so an operator can read only the ~10-20 disagreement rows instead of every moment.

Verified: `bash -n` clean; `py_compile` clean on both modules; smoke test confirms (a) the rename + back-compat aliases work, (b) the model-resolution chain picks `cfg → CLIP_GROUNDING_JUDGE_MODEL → CLIP_TEXT_MODEL → qwen-default` in order, (c) both `judge` and legacy `gemma_judge` config keys are accepted, (d) the A/B harness runs end-to-end on synthetic moments with no LM Studio (everything falls to Tier 1 — agreement = 1.0).

Pages touched: [[entities/grounding]] (rename + model-resolution + validation pointer), [[index]]. New: [[entities/grounding-ab]].

---

## [2026-04-27f] update | Tier-3 moment-discovery upgrades (A1, A2, A3)

Shipped Tier 3 of [[concepts/moment-discovery-upgrades]] — three architectural changes that build on Tier 1 + Tier 2's foundations.

- **A1 — Two-stage Pass B (local + global)** — extended the Pass B heredoc in `clip-pipeline.sh` (~line 2055-2225). After Pass B-local finishes, the pipeline reuses Tier-1 Q1's `chunk_summaries` to build a stream skeleton (one line per chunk: `[MM:SS-MM:SS] (i/N) <summary>`) and makes ONE Gemma call asking for cross-chunk SETUP-PAYOFF arcs. Validated arcs appended to `llm_moments` with `category="arc"`, `cross_validated=True`, 1.4× boost, plus `setup_time` / `setup_chunk` / `payoff_chunk` / `arc_kind`. Skipped when chunk_summaries is empty (<3) or LM Studio is in outage. Pages: [[concepts/two-stage-passb]].

- **A2 — Visual setup-payoff verification** — Stage 5 (`clip-pipeline.sh` ~line 2602-2628) extracts 2 additional `setupminus1` / `setupplus1` frames for moments carrying `setup_time` (callback/arc). Stage 6's frame loader (~line 3149-3185) prepends them when present and adds an A2-aware prompt addendum naming the arc kind and the setup transcript. The prompt JSON spec gains a `callback_confirmed` 0-10 dimension; parser applies it as a multiplicative `[0.85, 1.20]` adjustment to `final_score` (the ONLY Stage 6 path that can penalize — vision_score remains bonus-only). Pass C entry now preserves `setup_time` / `setup_text` / `arc_kind` / `callback_cosine` / `dominant_speaker*` so Stage 5/6 can read them. Pages: [[concepts/clipping-pipeline]], [[concepts/vision-enrichment]] *(implicit via clipping-pipeline)*.

- **A3 — Gemma judge for grounding (additive)** — extended `scripts/lib/grounding.py` with `gemma_judge()` (single LLM call returning 5-dimensional scores: `grounding`, `setup_payoff`, `speaker`, `conceptual`, `callback`) and `_gemma_weighted_score()` (weighted mean → 0-10 → pass threshold). Wired into `cascade_check` as an opt-in path that runs in place of MiniCheck when `config/grounding.json::gemma_judge.enabled=true`; MiniCheck remains the fallback when the Gemma call fails. **Deviation from plan §A3:** the original spec said "retire MiniCheck/Lynx" — this implementation keeps them as fallbacks per CLAUDE.md §6.2's no-hard-fail rule. Documented explicitly in [[entities/grounding]]. New optional `optional_setup` / `optional_speaker_info` kwargs on `cascade_check()`.

All paths preserve graceful degradation. `bash -n` clean; all 10 Python heredocs `ast.parse` clean; A3 fallback chain (no LM Studio + no transformers → Tier 1) verified in a smoke test.

Pages touched: [[concepts/highlight-detection]], [[concepts/clipping-pipeline]], [[entities/grounding]], [[index]]. New: [[concepts/two-stage-passb]].

---

## [2026-04-27e] update | Tier-2 moment-discovery upgrades (M1, M2, M3)

Shipped Tier 2 of [[concepts/moment-discovery-upgrades]] — three signal-adding modules that close the canonical Lacy-class gaps from a different angle than Tier 1. Per the plan §9, M4 (self-consistency) is intentionally skipped as costly and subsumed by Tier 3 A1.

- **M2 — audio events** — new `scripts/lib/audio_events.py` (~300 lines). Three CPU librosa detectors per 30 s window: `rhythmic_speech` (onset regularity → freestyle/song), `crowd_response` (RMS spike + ZCR → laughter/cheer), `music_dominance` (HPSS percussive ratio). Pipeline invokes scanner between Stage 2 and Stage 3 (`clip-pipeline.sh` ~520-540), writing `/tmp/clipper/audio_events.json`. Pass A `keyword_scan` loads via `_audio_events_mod.lookup_window()` and adds boost-only signals: `rhythmic≥0.7` → +1 dancing+hype, `crowd≥0.5` → +1 funny+hype, `music≥0.6` → +1 dancing. Pages: [[entities/audio-events]].
- **M1 — speaker diarization** — extended `scripts/lib/speech.py` with `_maybe_diarize()` helper (WhisperX `DiarizationPipeline` + pyannote 3.1, gated on `HF_TOKEN`). Each `transcript.json` segment may now carry a `speaker` field. Pass A `keyword_scan` computes `speaker_count` / `dominant_speaker_share` per window and fires +1 funny+controversial when ≥2 speakers AND no speaker dominates. Pass B post-parse annotates LLM moments from their ±15 s window. Pass C applies multiplicative ×1.15 boost (smaller than the ×1.20 cross-validated boost) at `clip-pipeline.sh` ~2178-2185. Pages: [[entities/diarization]], [[entities/speech-module]].
- **M3 — long-range callback detector** — new `scripts/lib/callbacks.py` (~370 lines). Aggregates transcript into ~30 s windows, embeds via `sentence-transformers/all-MiniLM-L6-v2` (CPU, L2-normalized), indexes with FAISS `IndexFlatIP` (numpy fallback). For each top-20 Pass B candidate, FAISS-searches for setups ≥ 5 min earlier with cosine ≥ 0.6, then gates with a small Pass-B' LLM judgment. Survivors emitted as `category="callback"`, `cross_validated=True`, ×1.5 score boost, with `setup_time` / `setup_text` / `callback_kind` / `callback_cosine` for downstream consumers. Wired in `clip-pipeline.sh` right after Pass B's `llm_moments.json` write (~line 2055). Pages: [[entities/callback-module]], [[concepts/callback-detection]].

All new dependencies optional; module-level try/except + feature gates (`HF_TOKEN`, ImportError handling, missing-file fallthrough). Per CLAUDE.md §6.2, no hard-fail path introduced.

Verified: `bash -n` clean; all 10 Python heredocs `ast.parse` clean; M2/M3 graceful-degradation paths exercised in a smoke test (missing librosa → empty events file; missing sentence-transformers → empty callback list).

Pages touched: [[concepts/highlight-detection]], [[concepts/clipping-pipeline]], [[entities/speech-module]], [[index]]. New: [[entities/audio-events]], [[entities/diarization]], [[entities/callback-module]], [[concepts/callback-detection]].

---

## [2026-04-27d] update | Tier-1 moment-discovery upgrades (Q1-Q5)

Shipped Tier 1 of [[concepts/moment-discovery-upgrades]] — five low-risk Pass B / Pass C changes targeting the canonical Lacy-penthouse miss class (long-range narrative, narrative-rare keyword phrases, storytime length).

- **Q5** — Pass B prompt + `parse_llm_moments` duration clamp now allow 150 s clips for `storytime`/`emotional` (was hard 90 s for all). `clip-pipeline.sh` ~1456-1463 + ~1655-1662.
- **Q3** — `KEYWORD_CEILING` in Pass C went from constant `0.75` to per-category dict: `storytime` 0.90, `hot_take`/`emotional`/`controversial` 0.85, `hype`/`reactive` 0.75, `funny`/`dancing` 0.70. Lookup via `m.get("primary_category", "hype")` — Pass A keyword_scan already sets `primary_category` so the change works for both passes' moments. `clip-pipeline.sh` ~1830-1850.
- **Q2** — Three explicit transcript→JSON few-shot examples added to the Pass B prompt right before the live transcript: setup-payoff w/ off-screen voice (Lacy archetype), long-form storytime w/ payoff, hot take w/ audience pushback. Each example's `why` describes the SITUATION, not the words. `clip-pipeline.sh` ~1648-1685.
- **Q1** — Pre-compute `total_chunks` once by walking the timeline with the per-segment window logic. After each chunk's parse + grounding, ask LLM for a one-line summary (`max_tokens=200`, `/no_think`, neutral fallback to first ~12 transcript words on failure). Inject the last 2 summaries into the next chunk's prompt as `Earlier in this stream:` with `(idx/total)` indices, instructing the model to name cross-chunk callbacks in `why`. `clip-pipeline.sh` ~1583-1610 + 1622-1645 + 1810-1850.
- **Q4** — Replaced `CHUNK_DURATION = 300` constant with `CHUNK_DURATION_BY_SEGMENT` dict (`just_chatting`/`irl` 480, `debate` 360, `reaction`/`gaming` 300) and `CHUNK_OVERLAP_BY_SEGMENT` dict. New `_chunk_window_for(start_ts)` helper does the +150 s segment peek and returns `(duration, overlap, seg_type)`; loop body uses `cur_chunk_dur`/`cur_chunk_overlap` for the rest of the iteration. `total_chunks` walker uses the same logic so indices stay accurate. `clip-pipeline.sh` ~1561-1607 + ~1614-1626 + ~1923 (loop tail).

Bash + Python heredoc syntax verified clean (`bash -n` + `ast.parse` on all 10 heredocs).

Skipped from the plan in this batch: M4 (self-consistency, plan recommends skip). Tier 2 (M1/M2/M3) and Tier 3 (A1/A2/A3) are not started — they need Tier-1 effect measurement first.

Pages touched: [[concepts/highlight-detection]], [[concepts/clipping-pipeline]].

---

## [2026-04-27c] update | BUG 35 + BUG 36 + BUG 37 + BUG 34 follow-up: clip distribution + description quality

Diagnosed from the 18:47 UTC re-run after the BUG 34 fix. User reported clips still clustered at the start of the stream and several clips had descriptions that didn't match the video — "as if the clipping pipeline clipped too early or too late given the context." Tally on the run: 57 / 100 Pass B "why" fields nulled (down from 88 % pre-fix but still high); 9 / 10 selected clips at score=1.000; 7 / 10 clips in the 31-94 min range of a 187-min stream; 1 clip ("Clip_T4037") with all Stage 6 fields nulled and only 2 Whisper SRT segments (mostly silence).

**BUG 34 follow-up** — Lowered `tier_2.entailment_threshold: 0.5 → 0.3` (and shifted ambiguous zone `[0.4, 0.65] → [0.2, 0.45]`) in `config/grounding.json`. Surviving vs nulled "why" text was qualitatively similar; MiniCheck-Flan-T5-Large is too literal for inferential summaries. Clear hallucinations score under 0.05 and are still rejected. Pages touched: [[concepts/bugs-and-fixes]].

**BUG 35** — Pass B moments stacking at `chunk_start` when LLM emits invalid timestamps. `parse_llm_moments` clamped every parsed timestamp into `[chunk_start, chunk_end]`; when Gemma 4 returned chunk-relative `"00:00"` or null values, multiple moments per chunk pinned to the same `chunk_start`, surviving Pass C dedup (because they had identical timestamps, not just close ones) and getting cross-validation boosts because Pass A keyword scan also fired around chunk boundaries. Fix: `parse_llm_moments` now tracks a `seen_at_start` counter — when a raw timestamp was outside the chunk window AND the clamp lands at `chunk_start`, only the first such moment per chunk is kept. Pages touched: [[concepts/bugs-and-fixes]], [[concepts/highlight-detection]].

**BUG 36** — Pass C overflow biased toward 1-2 buckets. The Phase 2 overflow loop sorted ALL remaining candidates globally by `final_score` and picked top-N, so when score saturation collapsed many candidates to 1.000 in mid-stream chunks, every overflow slot landed there. Replaced with `_phase2_round_robin` helper: on every iteration, sorts buckets by `(picked_count_asc, top_remaining_score_desc)` and adds the highest-scored unused moment from the lowest-picked bucket. Buckets with no Phase-1 pick always win a slot before any other bucket gets a SECOND. Pages touched: [[concepts/bugs-and-fixes]].

**BUG 37** — Score saturation. `m["final_score"] = round(min(styled_score * lp, 1.0), 4)` and the equivalent at the position-weight site hard-capped scores at 1.0 *during* ranking. With cross-validated × style × position multipliers compounding (e.g. base 0.767 × 1.20 × 1.05 × 1.05 ≈ 1.014), most reasonable moments hit the ceiling and Pass C's tie-break collapsed to insertion order. Removed both `min(..., 1.0)` clamps from ranking; final values can land in `[0, ~1.4]`. Reapplied the clamp exactly once at the user-facing serialization boundary so `hype_moments.json` and the dashboard still show 0–1.0 scores. Pages touched: [[concepts/bugs-and-fixes]].

**Files**: `config/grounding.json` (threshold 0.5→0.3, ambiguous zone shift). `scripts/clip-pipeline.sh` (parse_llm_moments seen_at_start counter ~line 1395; Pass C `_phase2_round_robin` ~line 1990; cross-val/position-weight clamps removed ~line 1916 and ~line 1958; `display_score` clamp at output serialization ~line 2113).

Bugs-and-fixes count bumped 34 → 37. Pages touched: [[concepts/bugs-and-fixes]], [[concepts/highlight-detection]], [[wiki/index]].

---

## [2026-04-27b] update | BUG 33 + BUG 34: clip quality regression — Tier 3 HTTP 400 + Tier 2 reference truncation

Diagnosed from the 2026-04-27 17:24 UTC pipeline run on `20260407_Thetylilshow_2742682361.mp4`. User reported "clipping quality went down after the latest pipeline rework." Investigation showed the BUG 31 + BUG 32 fixes from earlier in the day did not touch grounding or clip metadata — but they fixed [[#BUG 30]]'s `response_format` regression in `call_llm()` and the Stage 6 vision payload, which had been silently masking BUG 34 by causing every Pass B call to 400 (so there was no "why" to null in the first place). Once Pass B started succeeding, BUG 34's pre-existing reference-window truncation became visible: ~88 % of Pass B "why" fields and ~40 % of Stage 6 fields were nulled by Tier 2 grounding, leaving clips with placeholder titles (`Clip_T<timestamp>`) and empty descriptions.

**BUG 33 (medium)** — `scripts/lib/lmstudio.py::chat()` (Tier 3 transport, untouched by the earlier BUG 30 fix) still forwarded `response_format: {type: json_object}` when `response_json=True`. With `tier_3.lm_studio_model = "llama-3-patronus-lynx-8b-instruct"` configured but the operator running Gemma 4 in LM Studio, every Tier 3 fire returned HTTP 400 — silently disabling Lynx and flooding the log. Removed the field entirely; the `response_json` parameter is now a documented no-op kept for API compat. Caller-side JSON extraction (`text.find("{")` + `rfind("}")` + `json.loads`) is robust enough on its own. Pages touched: [[concepts/bugs-and-fixes]], [[entities/lmstudio]], [[entities/grounding]].

**BUG 34 (critical, root cause of quality regression)** — Pass B post-parse loop passed the entire 5-min chunk (~5000 chars) as the grounding reference, but `tier_2.max_ref_chars` was hard-coded at 2000 — so MiniCheck NLI only saw the first ~1.5 min of each chunk. Moments in the back half lost their supporting transcript and were nulled with low entailment probabilities (typical p=0.005-0.4 vs threshold 0.5). Fix has two parts: (a) bumped `tier_2.max_ref_chars` 2000 → 6000 in `config/grounding.json` (still well within Flan-T5-Large's 2048-token encoder budget); (b) Pass B post-parse loop now extracts a tight ±90 s window around each moment's timestamp from `chunk_segs` via `format_chunk()` and passes BOTH the tight window AND the full chunk as references, so Tier 2 scores against the directly-relevant ±90 s while Tier 1's overlap check still has the full chunk for any rare evidence outside the tight window. Expected null-rate drop ~88 % → ~25-35 %. Pages touched: [[concepts/bugs-and-fixes]], [[entities/grounding]].

**Files**: `scripts/lib/lmstudio.py` (~line 28: docstring rewrite, response_format omitted, response_json now no-op). `config/grounding.json` (`tier_2.max_ref_chars: 2000 → 6000` plus `_max_ref_chars_note`). `scripts/clip-pipeline.sh` (Pass B post-parse loop ~line 1717: tight-window extraction + dual-reference cascade call).

Bugs-and-fixes count bumped 32 → 34. Pages touched: [[concepts/bugs-and-fixes]], [[entities/grounding]], [[entities/lmstudio]], [[wiki/index]].

---

## [2026-04-27] update | BUG 31 + BUG 32: detached-exec pipeline lifecycle + LM Studio outage fail-fast

Diagnosed from a 2026-04-27 pipeline log run with `google/gemma-4-26b-a4b`. Two related production failures hit during Pass B Chunk 2: the dashboard's host-side `docker exec` session died with a Docker Desktop named-pipe 500 (`/exec/<id>/json` returned 500), and inside the container every subsequent LLM call failed with `Errno 101 Network is unreachable` because the bridge route to `host.docker.internal` was severed. The pipeline kept running for 10+ minutes producing zero AI moments; the dashboard incorrectly emitted "Pipeline finished" twice.

**BUG 31 (critical, structural)** — Dashboard now spawns the pipeline detached via `docker exec -d` with `nohup ... </dev/null >/dev/null 2>&1 &`. Lifecycle is tracked through marker files written by `clip-pipeline.sh` itself (`/tmp/clipper/pipeline.pid` at startup, `/tmp/clipper/pipeline.done` from the EXIT trap, both containing `persistent_log=`). New `DetachedDockerPipeline` class mimics `subprocess.Popen` (`poll`, `terminate`, `kill`, `pid`, `wait`); `poll()` reads markers via short `docker exec cat` and probes the in-container pid with `kill -0`, returning `None` on docker-daemon timeouts so a transient pipe failure can't false-positive completion. Polling thread now mirrors `pipeline.log` from the container into the host `LOG_FILE` (in addition to the two stage files); cadence relaxed 2 s → 5 s. SSE generator additionally requires stage-file mtime ≥ 30 s old before emitting `done`. `/api/status` exposes a `persistent_log` field with the host-visible path under `clips/.pipeline_logs/`. Pages touched: [[concepts/bugs-and-fixes]], [[entities/dashboard]].

**BUG 32 (critical)** — Both LLM call paths now fail-fast on persistent network outage. `call_llm()` (Pass B) tracks `_LLM_NET_FAIL_STREAK`; network-shaped exceptions (`Errno 101`, `Errno 111`, `Connection refused`, `Network is unreachable`, `timed out`, `Read timed out`, `Name or service not known`) bump it; non-network failures or any successful response reset it. After 3 consecutive failures, `call_llm()` short-circuits to `None` and the chunk loop logs `[PASS B] Aborting after chunk N: persistent LM Studio outage detected` and `break`s. Stage 6 `_vision_call` mirrors with `_VISION_NET_FAIL_STREAK`; after 3 failures the moment loop sets `skip_vision = True` for every remaining moment (Stage 7 always renders all moments — only the AI title/description step is bypassed). Also lowered `call_llm()` default `timeout` 600 s → 240 s — the old ceiling absorbed worst-case 35B-A3B reasoning, but in practice anything past 4 min signals a queue stall or wedged network. Pages touched: [[concepts/bugs-and-fixes]], [[concepts/clipping-pipeline]].

**Files**: `scripts/clip-pipeline.sh` (PID file at startup ~line 92, EXIT trap rewrite ~line 165, `call_llm()` + helpers ~line 1135, Pass B chunk-loop break ~line 1745, Stage 6 `_vision_call` outage flag ~line 2563, moment-loop early skip ~line 2387). `dashboard/app.py` (new `DetachedDockerPipeline` class ~line 195, `spawn_pipeline()` rewrite ~line 290, `_poll_container_stages` log mirror ~line 415, `_read_remote_files` mode flag ~line 425, SSE staleness check ~line 700, `_read_persistent_log_path` helper + `/api/status` field ~line 670, `shlex` import ~line 12).

Bugs-and-fixes count bumped 30 → 32. Pages touched: [[concepts/bugs-and-fixes]], [[concepts/clipping-pipeline]], [[entities/dashboard]], [[wiki/index]].

---

## [2026-04-25] update | Wiki modularization: captions + speed-control pages, vram-budget refresh, bugs nav table, graph colors

Modularized the wiki to improve page manageability and enrich the Obsidian graph.

**New pages**: [[concepts/captions]] (extracted from `clip-rendering.md` — subtitle style, hook card, per-clip palette/position randomization) and [[concepts/speed-control]] (extracted — setpts + rubberband, SRT rescaling, dashboard dropdown).

**clip-rendering.md**: Removed the "Subtitle/caption style", "Hook caption", and "Speed control" sections; replaced each with a 1–2 sentence summary + WikiLink. Added `[[concepts/captions]]` and `[[concepts/speed-control]]` to Related section.

**vram-budget.md**: Replaced stale Ollama-era content — updated model table to reflect configurable LM Studio models with Gemma-4 as current default; rewrote stage-by-stage VRAM flow for both unified (single multimodal model, no swap) and split (separate text + vision) configurations; fixed "Whisper vs Ollama" section heading and copy to reference LM Studio.

**bugs-and-fixes.md**: Added categorized quick-nav table at top (Infrastructure, Dashboard, LLM/Model Integration, Pipeline/Rendering, Grounding/Hallucination). Updated bug count to 30 in index.

**index.md**: Added entries for `[[concepts/captions]]` and `[[concepts/speed-control]]` in Pipeline section; updated bugs-and-fixes description.

**graph.json**: Populated `colorGroups` — entities (blue), concepts (amber), sources (purple), overview (green), index (teal), log (gray).

Pages created: [[concepts/captions]], [[concepts/speed-control]].
Pages updated: [[concepts/clip-rendering]], [[concepts/vram-budget]], [[concepts/bugs-and-fixes]], [[wiki/index]].

---

## [2026-04-25] update | Fix three pipeline bugs: HTTP 400 on Gemma, heredoc backtick bash errors, float comparison in Stage 7

Three bugs diagnosed from a 2026-04-24 pipeline log run with `google/gemma-4-26b-a4b`.

**BUG 30 (critical)** — `response_format: json_object` caused HTTP 400 on every Pass B and Stage 6 call. Removed from `call_llm()` and Stage 6 vision payload; JSON parsing fallbacks already handle free-form output. Pages touched: [[concepts/bugs-and-fixes]], [[concepts/clipping-pipeline]].

**BUG 29 (non-fatal)** — Backtick Markdown formatting in the two unquoted `python3 << PYEOF` heredocs was being interpreted as bash command substitution, producing spurious "command not found" errors. Fixed 9 comment/string locations. Pages touched: [[concepts/bugs-and-fixes]].

**BUG 28 (non-fatal)** — Phase 4.2 boundary-snap float timestamps triggered `[: integer expression expected` in Stage 7's bash `-lt 0` guards. Replaced both with `awk`-based float-safe clamp. Pages touched: [[concepts/bugs-and-fixes]].

## [2026-04-24] update | Phase 5 of the 2026 upgrade — per-stage model split, self-consistency ranker, Twitch clip dataset bootstrap

Implemented Phase 5 of `IMPLEMENTATION_PLAN.md` — scoped to 5.1 + 5.2 + 5.3. Explicitly deferred 5.4 (HITL + DPO loop — separate product) and 5.5 (Temporal Contrastive Decoding — research evaluation, not production).

**5.1 — Per-stage model split (config + pipeline)**: `config/models.json` now has optional `text_model_passb` and `vision_model_stage6` fields. When set, they override `text_model` / `vision_model` ONLY for Pass B and Stage 6 respectively; Stage 3 segment classification always uses `text_model`. When `null` (default), every stage uses the unified config exactly as before — zero regression risk. `dashboard/app.py` forwards the overrides as `CLIP_TEXT_MODEL_PASSB` / `CLIP_VISION_MODEL_STAGE6` env vars when set. The pipeline's VRAM choreography (lines 117–126, 426–429, 500, Stage 5→6 swap, Stage 6→7 unload) compares the stage-specific model names with the unified ones and skips swaps when they match. Recommended 48 GB split: `qwen/qwen3-32b` text-only for Pass B (~28 GB) + `qwen/qwen3-vl-8b` FP8 for Stage 6 (~10 GB), co-resident.

**5.2 — Self-consistency ranker** (`scripts/lib/self_consistency.py`, stdlib-default): implements Universal Self-Consistency (Chen et al., arXiv:2311.17311) + SelfCheckGPT-style divergence (Manakul et al., arXiv:2303.08896). Given N candidate strings + a reference, returns them ranked by a combined score: `(1 - agreement_weight) × ref_grounding + agreement_weight × pairwise_agreement`. Default method is `content_overlap` (reuses Phase 1's `grounding.content_overlap_ratio`); `minicheck` method reuses Phase 1's Tier-2 NLI; `pairwise` is reserved for sentence-transformers embeddings. Also ships `rank_field_dict(candidate_dicts, field, reference)` — convenience for parsed VLM outputs where you want to rank by one field but keep the full dict structure of the winner. Opt-in via `config/self_consistency.json::enabled = true` (default false). Stage 6 integration documented but NOT wired by default — enabling would mean `n_candidates × vision_cost` per clip; availability-first.

**5.3 — Twitch clip dataset bootstrap** (`scripts/research/bootstrap_twitch_clips.py`): standalone research tool, NOT wired into the pipeline. Three subcommands: `fetch-clips` (top-N clips per broadcaster via Helix API when creds are set, else unofficial GraphQL with TwitchDownloader's public web client_id), `pair` (positives from `vod_offset + duration ± margin`, sampled negatives ≥ `min_gap_sec` away from any positive in the same VOD), `summary` (broadcaster/duration/view stats). Produces JSONL datasets directly usable by a future CG-DETR eval harness (Phase 4.2 deferred) or a DPO training loop (5.4 deferred). Offline pair-mode smoke test: 3 synthetic clips → 3 positive + 6 negative triples.

**Scope-deferred for Phase 5:**
- **5.1 Stage 6a text classifier** — a text-only classification pass BEFORE Stage 6 vision that emits `{what_happened, category, confidence}` as a hard constraint. Needs a real Stage 6 rewrite. Tracked in `IMPLEMENTATION_PLAN.md`.
- **5.1 Top-5 % vision escalation** — hot-swap to Qwen3-VL-32B for highest-scoring candidates. Needs a cost/quality eval harness before landing.
- **5.2b Stage 6 self-consistency wire-in** — module is ready; plumbing Stage 6's `_vision_call()` closure into a sampling loop is a targeted follow-up once someone flips `self_consistency.enabled = true` and validates the ~3× cost is worth it.
- **5.4 HITL + DPO retraining loop** — Argilla / Label Studio UI + weekly DPO fine-tune. Separate product scope.
- **5.5 Temporal Contrastive Decoding (EventHallusion)** — research evaluation against the Phase 5.3 bootstrap dataset; worth doing but explicitly not production shipping.
- **5.6 End-to-end architectures** — held per plan; revisit Q4 2026 when Dispider / InternVideo2.5 successors land.

**Graceful degradation everywhere**: unified-config users see zero behavior change; self-consistency module falls through to stdlib Jaccard when grounding/transformers is missing; bootstrap tool falls through to GraphQL when Helix creds aren't set.

**Files changed**: `scripts/clip-pipeline.sh` (TEXT_MODEL_PASSB / VISION_MODEL_STAGE6 env wiring + VRAM swap updates at lines 18–20, 192–194, 426–429, 714–716, 1123, 2202, 2189–2197, 2844); `dashboard/app.py` (forward new env vars in both direct-subprocess and docker-exec paths). **New**: `scripts/lib/self_consistency.py`, `scripts/research/bootstrap_twitch_clips.py`, `config/self_consistency.json`. **Extended**: `config/models.json`.

Pages touched: [[concepts/model-split]] (new), [[concepts/self-consistency]] (new), [[entities/self-consistency-module]] (new), [[entities/bootstrap-twitch-clips]] (new), [[index]], `IMPLEMENTATION_PLAN.md` status tracker.

---

## [2026-04-24] update | Phase 4 of the 2026 upgrade — UI chrome masking, overlay-text OCR, variable-length boundary snap

Implemented Phase 4 of `IMPLEMENTATION_PLAN.md` — scoped pragmatically: ship the lighter alternatives that cover the 80 % case; defer the heavyweight Florence-2 auto-calibration and CG-DETR pieces that need proper eval harnesses to justify.

**4.1a — OpenCV MOG2 transient-overlay detection** (`scripts/lib/chrome_mask.py`): between Stage 5 and Stage 6, run MOG2 background subtraction across the 6 payoff-window frames to find overlays that pop in mid-clip (sub alerts, follower toasts, bit rain). No new deps — opencv is already installed via the originality stack. Safety clamps: `min_contour_area=2500` drops noise; `max_masked_area_ratio=0.35` rejects misfires that would mask most of the frame.

**4.1b — OBS scene overrides**: when a user drops a `config/streamers/{channel}_chrome.json` file with the exact persistent-region bboxes (webcam crop, chat panel, logo), those overrides WIN over MOG2. Filename-substring matching picks the right file per VOD. Bboxes auto-scale from the file's native resolution to the Stage 5 frame resolution. This is the "OBS scene JSON gives you perfect overlay bboxes for free" path from the research doc.

**4.1c — PaddleOCR overlay text** (opt-in, `CHROME_STACK=full`): PaddleOCR PP-OCRv5 on up to 2 unmasked frames per moment. Extracted text (`"USER gifted 5 subs"`) is concatenated into an `overlay_text` string, written to `/tmp/clipper/chrome_<T>.json`, and Stage 6 uses it two ways: (a) dedicated "Overlay text visible on the frames — treat as hard evidence" block in the VLM prompt; (b) appended to the grounding cascade's `refs` list so overlay pixels can LEGITIMATELY support claims (e.g. a "gifted subs" title is supported by OCR pixels even when chat events weren't captured).

**4.2 — Sentence + silence boundary snap** (`scripts/lib/boundary_detect.py`): between Pass C and Stage 4.5, snap each moment's tentative `(clip_start, clip_end)` to the nearest Whisper word-boundary using Phase 3's word-level transcripts. Start drifts back up to 3 s; end drifts forward up to 8 s — asymmetric budget so storytime payoffs that land just past the tentative window are caught. After sentence snap, a second pass nudges into the nearest silence gap (> 250 ms inter-word pause) for clean audio cut points. Safety clamps keep snapped duration in `[15, 90]` s; any snap outside that reverts to tentative values. CG-DETR / SG-DETR / Lighthouse explicitly deferred to Phase 5.

**Chrome masking swap-in-place**: chrome_mask writes masked frames to a scratch dir then atomically swaps them over the Stage 5 originals. Stage 6's VLM call path doesn't need to know about two sets of frame paths — it picks up whatever is at `/tmp/clipper/frames_<T>_<label>.jpg`.

**Graceful degradation everywhere**: opencv missing → chrome stage is a no-op; paddleocr missing → `overlay_text=""`; boundaries.json disabled → every moment decorated with `source="disabled"`; transcript lacks word-level timestamps → `source="no_timeline"`; snap would exceed duration bounds → reverts to tentative. The only scenario that hard-fails is a broken chrome_mask import, which Phase 4's PYCHROME block catches with a try/except and exits 0.

**Files changed**: `scripts/clip-pipeline.sh` — one PYSNAP heredoc after Pass C, one PYCHROME heredoc after Stage 5, Stage 6 prompt gets an `overlay_context_block` and the cascade `refs` now include `chrome_overlay_text`. `Dockerfile` — new `CHROME_STACK=full|slim` build arg. **New**: `scripts/lib/chrome_mask.py`, `scripts/lib/boundary_detect.py`, `config/chrome.json`, `config/boundaries.json`, `config/streamers/README.md`, `requirements-chrome.txt`.

**Not shipped (deferred per scope decisions)**:
- **Florence-2 persistent-overlay auto-calibration** — needs per-channel calibration flow + heavy model dep. Users who know their OBS layout use the manual override path instead.
- **SigLIP 2 image-text cosine** — extra grounding signal alongside OCR. Out of scope.
- **CG-DETR / SG-DETR variable-length windows** — needs QVHighlights-trained weights + SlowFast/CLIP feature extraction per window. Revisit after Phase 5 bootstraps a Twitch-clip eval dataset.
- **TransNet V2 shot-cut snap** — coded but disabled by default in `boundaries.json`. Enable for edit-heavy content.

Pages touched: [[concepts/chrome-masking]] (new), [[concepts/boundary-snap]] (new), [[entities/chrome-mask-module]] (new), [[entities/boundary-detect-module]] (new), [[index]], `IMPLEMENTATION_PLAN.md` status tracker.

---

## [2026-04-23] update | Phase 3 of the 2026 upgrade — WhisperX transcription, large-v3-turbo, streamer prompts, opt-in vocal separation

Implemented Phase 3 of `IMPLEMENTATION_PLAN.md` — scoped to the "80 % of gains for an afternoon" core (3.1 WhisperX + 3.2 large-v3-turbo + 3.5 streamer prompts + 3.3 opt-in vocal sep). **TalkNet-ASD (3.4) explicitly deferred** — face-tracking + webcam-crop localization + VAD AND-gating is a separate product scope.

**3.1 + 3.2 — WhisperX + `large-v3-turbo`**: Stage 2's 100-line inline heredoc was replaced with a single subprocess call into the new [[entities/speech-module]] (`scripts/lib/speech.py`). Primary backend is now WhisperX — VAD-based chunking, batched faster-whisper inference, and wav2vec2 forced alignment. The old 20-minute ffmpeg-chunking scaffolding is gone; WhisperX's internal VAD drops silence gaps and prevents the degenerate-loop bug that motivated the chunking in the first place. Default model bumped from `large-v3` to `large-v3-turbo` per the research doc's "free 2.5× speedup with < 1 % WER loss." Expected wall time on a 2-hour VOD: ~12-18 min on RTX 5060 Ti (was ~35-45 min). The legacy faster-whisper 20-minute-chunk path is preserved as the fallback backend — fires automatically when WhisperX isn't importable (image built `SPEECH_STACK=slim`) or when WhisperX hits a runtime error.

**3.5 — Streamer-slang biasing**: new `config/streamer_prompts.json` holds per-channel `initial_prompt` strings. `speech.pick_initial_prompt(vod_basename)` does case-insensitive filename-substring matching; first-match wins, falls back to a global `default_prompt` when nothing matches. Ships with generic Valorant and League prompts (activate on filenames containing `valorant`/`val_`/`vct` or `league`/`lol_`) plus a Twitch-common-vocab default covering pog/poggers/W/L/KEKW/LULW/POGGERS/Sadge. Users add channels by editing the JSON — no rebuild needed.

**3.3 — Vocal separation (opt-in)**: new [[entities/vocal-sep-module]] (`scripts/lib/vocal_sep.py`) wraps Demucs v4 `htdemucs_ft`. Disabled by default — flip `vocal_separation.enabled` in `config/speech.json` to turn on. When enabled, pre-processes the audio to a vocals-only stem before transcription; adds ~60-120 s per hour of audio on a 4090 (much longer on CPU). Intended for DJ sets, music-game streams, IRL-with-car-music content. Graceful no-op when demucs isn't installed.

**Env-var compat**: `CLIP_WHISPER_MODEL` / `CLIP_WHISPER_DEVICE` / `CLIP_WHISPER_COMPUTE` env vars still work — they now win over `config/speech.json` when set. The dashboard Models panel continues to control Whisper without any changes.

**Deferred from Phase 3 (per scope decisions)**:
- **3.4 TalkNet-ASD active-speaker detection** — needs face tracking + webcam-crop localization + AND-gating with audio VAD. Complex; handle later.
- **Parakeet-TDT-0.6B-v3** — NeMo-specific; only worth integrating if WhisperX + turbo becomes throughput-bound.
- **Mel-Band RoFormer** — Demucs v4 baseline covers Phase 3's needs.

Files changed: `scripts/clip-pipeline.sh` (Stage 2 heredoc → subprocess call, ~130 lines deleted), `Dockerfile` (new `SPEECH_STACK=full|slim` build arg). New: `scripts/lib/speech.py`, `scripts/lib/vocal_sep.py`, `config/speech.json`, `config/streamer_prompts.json`, `requirements-speech.txt`.

Pages touched: [[concepts/speech-pipeline]] (new), [[entities/speech-module]] (new), [[entities/vocal-sep-module]] (new), [[concepts/clipping-pipeline]] (Stage 2 section rewritten), [[index]], `IMPLEMENTATION_PLAN.md` status tracker.

---

## [2026-04-23] update | Phase 2 of the 2026 upgrade — chat-signal ingestion (Pass A'), hard event ground truth

Implemented Phase 2 of `IMPLEMENTATION_PLAN.md` — VOD path only (live EventSub daemon deferred). Chat is now the **first-class moment-detection signal** the research doc positioned it as: 0.75 F1 on epic-moment detection alone, additive with vision. More importantly, EventSub/TwitchDownloader events (`sub_count`, `bit_count`, `raid_count`, `donation_count`) become **hard ground truth** that kills the "gifted subs" hallucination class deterministically.

**2.1 — VOD chat acquisition** (`scripts/lib/chat_fetch.py`): two-mode fetcher — (a) anonymous Twitch GraphQL `/comments` streaming via the public web client ID pattern community tools use, or (b) import from a pre-downloaded TwitchDownloader JSON. Mode (b) is preferred for hard event fidelity because it captures dedicated `message_type: subscription | raid` events that GraphQL omits.

**2.3 — Feature extractor** (`scripts/lib/chat_features.py`, stdlib only): loads the canonical JSONL and exposes a `ChatFeatures.window(start, end)` method returning `msgs_per_sec`, rolling z-score, `emote_density[category]`, `top_emotes`, `phrase_hits`, and the four hard event counts. Smoke-tested with a 336-message synthetic chat — z-score of 11.6 for a KEKW burst, sub_count correctly aggregated.

**2.4a — Stage 1b chat discovery**: after VOD discovery, the pipeline checks `vods/.chat/<basename>.jsonl`. When missing AND `config/chat.json::auto_fetch.enabled` AND the filename matches `vod_id_pattern`, Stage 1b auto-fetches via the GraphQL path. Marker files `/tmp/clipper/chat_available.txt` + `chat_path.txt` gate every downstream consumer. No chat → pipeline behaves exactly as post-Phase 1.

**2.4b — Pass A chat burst + emote density**: inside the 30 s keyword-scan loop, when chat is loaded, a `log1p(z_score)` bonus (capped at +2.0) and an emote-density bonus (capped at +1.5) add to the raw signal count. The dominant emote category is mapped into a keyword category (laugh→funny, hype→hype, tense→emotional...) so chat evidence reinforces the right category. Diagnostic fields (`chat_z`, `chat_msgs`, `chat_sub_count`, `chat_bit_count`) land on each `keyword_moments.json` entry.

**2.4c — Pass B + Stage 6 `chat_context` block**: both prompts get a 4-line (Pass B) / 5-line (Stage 6) structured chat summary — msgs count, baseline, burst factor, z-score, top emotes, and the four event counts. Stage 6 additionally gets an explicit "HARD GROUND-TRUTH RULE: if sub_count/bit_count/raid_count/donation_count are all 0, you may NOT say 'gifted subs'/'sub train'/'hype train'/..." directive.

**2.4d — Grounding cascade hard-event check**: `grounding.cascade_check()` now accepts `hard_events` + `event_map` kwargs. When a denylist hit in `twitch_jargon_overclaims` (e.g. "gifted subs") maps to a zero-count event in the window, Tier 1 hard-fails with `reason="event_contradicts_ground_truth"` — overriding token overlap, NLI probability, and Lynx judgment. This is the cleanest anti-hallucination signal the pipeline can give: a literal `sub_count=0` contradicts a "gifted subs" title regardless of what the frame overlay shows.

Self-tests confirm: (1) "Gifted Subs" title with sub_count=1 in window → pass, (2) same title with sub_count=0 → hard-fail with event_contradicts_ground_truth, (3) no hard_events supplied → cascade behaves as Phase 1. Graceful degradation: transformers / Lynx / chat data all independently optional.

Files changed: `scripts/clip-pipeline.sh` (Stage 1b new, Pass A scoring + diagnostics, Pass B chat_context, Pass B hard-event wire, Stage 6 chat_context + hard_events, Stage 6 hard-event wire on retry); `scripts/lib/grounding.py` (`_event_contradicts` helper, `check_claim`/`cascade_check` kwargs). New: `scripts/lib/chat_fetch.py`, `scripts/lib/chat_features.py`, `config/chat.json`, `config/emotes.json`.

Pages touched: [[concepts/chat-signal]] (new concept page), [[entities/chat-fetch]] (new), [[entities/chat-features]] (new), [[entities/grounding]] (hard-event check documented), [[concepts/highlight-detection]] + [[concepts/vision-enrichment]] (prompt chat_context + HARD rule), [[index]], `IMPLEMENTATION_PLAN.md` status tracker.

**Not shipped (deferred)**: live EventSub daemon path (§2.1 of plan). Requires Twitch Developer Console app + webhook URL + persistent process; separate product scope. Current VOD path via TwitchDownloader covers the offline-processing use case fully.

---

## [2026-04-23] update | Phase 1 of the 2026 upgrade — 3-tier grounding cascade, regenerate-once, JSON mode

Implemented Phase 1 of `IMPLEMENTATION_PLAN.md`. Builds on the Phase 0 foundations shipped earlier today — where Phase 0 caught **word-level** hallucinations (regex denylist + content overlap), Phase 1 catches **meaning-level** hallucinations (semantic entailment via MiniCheck NLI, LLM-as-judge via Lynx-8B) and stops silently dropping candidates to malformed JSON.

**1.1 — Tier 2 + Tier 3 grounding, with regenerate-once policy**: extended `scripts/lib/grounding.py` with two new tiers. Tier 2 is MiniCheck NLI (`lytang/MiniCheck-Flan-T5-Large`, CPU, ~150 ms/claim) loaded lazily via the `minicheck` package when available or raw `transformers` as fallback. Tier 3 is Lynx-8B (`PatronusAI/Llama-3-Patronus-Lynx-8B-Instruct`) hosted in LM Studio, fired only on Tier-2 borderline cases (entailment prob in [0.4, 0.65]). New `cascade_check()` function orchestrates: Tier 1 always runs; escalate to Tier 2 only when Tier 1 is neither a clear pass nor a hard denylist fail; escalate to Tier 3 only when Tier 2 is in the ambiguous zone AND enabled in config. Tiers that fail to load / aren't enabled collapse to the previous tier's verdict with a one-line log.

Stage 6 also gained a **regenerate-once** policy: when the first VLM call produces a field that fails the cascade, the pipeline makes ONE more VLM call with the violation named explicitly in the retry prompt ("your previous response contained 'gifted subs' but the transcript never mentions subscriptions — rewrite using ONLY what's in the transcript"). If the retry passes, its field replaces the failing one; if it also fails, the field is nulled per the Phase 0 fall-back-to-transcript-defaults policy. This converts ~40 % of Tier-1 false positives back into shipped clips.

**1.2 — LM Studio JSON mode at every JSON call site**: added `response_format: {type: json_object}` to `call_llm()` (Pass B) and the new `_vision_call()` closure (Stage 6). This constrains the model's decode to produce a valid top-level JSON object. Stage 3 classify returns a single word so JSON mode would hurt — intentionally left off. Since OpenAI-style JSON mode requires a top-level OBJECT (not array), the Pass B prompt was migrated from `[{...}]` to `{"moments": [{...}]}`; `parse_llm_moments` was extended to handle both shapes plus common alias keys (`clips`, `highlights`, `items`, `results`). Verified backwards compatibility via a 6-case parser smoke test.

**Plumbing**: new `config/grounding.json` with tier flags and thresholds; new `scripts/lib/lmstudio.py` minimal HTTP wrapper used by Tier 3; new `requirements-grounding.txt` with `transformers` + `sentencepiece`; new `GROUNDING_STACK=full|slim` Docker build arg (mirrors the existing `ORIGINALITY_STACK` pattern — install transformers for Tier 2 NLI, or skip for a smaller image with Tier 1 still fully working).

The pipeline is designed to degrade gracefully at every layer: no transformers installed → Tier 2 unavailable, cascade collapses to Tier 1; Lynx-8B not loaded in LM Studio → Tier 3 returns None, cascade uses Tier 2 verdict; regeneration fails → null-and-default. A fresh rebuild with `docker compose build` picks up the new deps; until rebuild, Phase 0 Tier 1 continues to work as before.

Files changed: `scripts/clip-pipeline.sh` (Pass B grounding wiring → `cascade_check`; Stage 6 vision call factored into `_vision_call()` closure + cascade + regenerate-once; `parse_llm_moments` accepts wrapped shapes; `call_llm` + Stage 6 request JSON mode; Pass B prompt emits `{"moments": [...]}`). New: `scripts/lib/lmstudio.py`, `config/grounding.json`, `requirements-grounding.txt`. Updated: `Dockerfile` (new build arg + install step), `IMPLEMENTATION_PLAN.md` (status tracker).

Pages touched: [[concepts/vision-enrichment]] (Phase 1 cascade + regenerate-once section), [[concepts/highlight-detection]] (Pass B cascade), [[entities/grounding]] (full Tier 2 + Tier 3 docs, cascade logic). New: [[entities/lmstudio]] (Tier 3 HTTP helper). [[concepts/bugs-and-fixes]] BUG 26 updated with Phase 1 note.

---

## [2026-04-23] update | Phase 0 of the 2026 upgrade — payoff-window frame sampling, /no_think sentinels, Tier-1 grounding gate

Implemented Phase 0 of `IMPLEMENTATION_PLAN.md` (synthesized from `ClippingResearch.md`). Three small, reversible changes that together address the single highest-ROI failure mode in the pipeline: Stage 6 describing the setup instead of the payoff, plus Pass B/Stage 6 emitting hallucinated Twitch-jargon.

**0.1 — payoff-window frame sampling** (biggest single fix): Stage 5 previously extracted 6 frames at `fps=1/5` starting from T−15, and Stage 6 only fed indices `03` / `04` to the VLM — i.e. T−5 and T+0. Per `ClippingResearch.md` "Additional topic 2", the payoff is at T+0..T+3, so the model was describing the setup frame, not the punchline. Now Stage 5 extracts at 6 targeted offsets (T−2, T+0, T+1, T+2, T+3, T+5) with one ffmpeg invocation per frame (named `frames_${T}_t0.jpg` etc.), and Stage 6 sends ALL six frames in ONE time-ordered VLM call. The prompt explicitly asks about the delta between T−2 and T+5. Net: 1 call per moment instead of up to 2, richer temporal context, and the model reasons about the whole arc.

**0.2 — /no_think sentinel on classification calls**: Qwen3.5-35B-A3B ignores `chat_template_kwargs={enable_thinking: False}` in LM Studio and burns 2–4 k reasoning tokens on each classification call (Stage 3 segment-type classify, Pass B moment scouting). The `/no_think` sentinel is a Qwen chat-template convention honored by the model itself. Prepended to the prompts in both places — no-op on 9B/Gemma, big throughput win on 35B.

**0.3 — Tier-1 grounding gate**: new module `scripts/lib/grounding.py` (stdlib only) plus `config/denylist.json`. Implements regex denylist + content-word overlap per `ClippingResearch.md` §8.4. Wired at two points: Pass B (after `parse_llm_moments`) nulls a moment's `why` if it mentions e.g. "gifted subs" / "sub train" / "hype train" and the transcript chunk doesn't support those tokens; Stage 6 (after vision JSON parse) nulls `title`/`hook`/`description` under the same rule against the ±8 s transcript window ∪ Pass-B `why`. Nulled fields fall back to transcript-only defaults in the existing Stage 7 path — the gate never drops a moment, only strips unsupported text. Tier 2 (MiniCheck NLI) and Tier 3 (Lynx-8B) are planned for Phase 1.

Files changed: `scripts/clip-pipeline.sh` (Stage 5 extract loop, Stage 6 heredoc fully rewrites per-frame loop → single multi-frame call, `/no_think` on Stage 3/Pass B prompts, grounding wire-in on Pass B + Stage 6). New: `scripts/lib/grounding.py`, `config/denylist.json`, `IMPLEMENTATION_PLAN.md` (project root). Doc: `README.md` frame-filename reference updated.

Pages touched: [[concepts/bugs-and-fixes]] (new BUG 25 — setup-window vision sampling; new BUG 26 — unsupported Twitch-jargon in generated metadata), [[concepts/vision-enrichment]] (Stage 6 now single-call over 6 frames + Tier-1 grounding gate), [[concepts/clipping-pipeline]] (Stage 5 frame-file names + Stage 6 semantics), [[concepts/highlight-detection]] (Pass B grounding on `why`). New: [[entities/grounding]] (module entity).

---

## [2026-04-23] update | Pruned framing modes; transcript grounding in Stage 6; detection diagnostic

Three related changes after a real-world detection miss (a clip whose vision output said "streamer reacting to gifted subs" when the streamer was actually talking about reaching Ranked 3.0):

**Framing modes pruned** — removed `smart_crop` and `centered_square` from the supported framing set. The remaining options are `blur_fill` (default) and `camera_pan`. `smart_crop` was a net negative: the vision-returned chrome bboxes were unreliable enough to cause render artifacts, and the extra prompt asks contributed to overall vision-output drift. `centered_square` offered no measurable fingerprint gain over `blur_fill + per-clip randomization`. Legacy configs still saving `framing=smart_crop` or `centered_square` map to `blur_fill` via the case-statement default.

Files changed: `scripts/clip-pipeline.sh` (Stage 7 render loop simplified; `chrome_regions` removed from the vision prompt + parser), `dashboard/templates/index.html` (dropdown trimmed to 2 options with `blur_fill` as the default), `dashboard/app.py` (`DEFAULT_ORIGINALITY["framing"] = "blur_fill"`), `scripts/lib/stitch_render.py` (centered_square branch dropped).

**Transcript grounding in Stage 6** — the vision prompt previously received only the upstream Pass-B LLM's `why` summary as narrative context. When Pass B itself hallucinated (pattern-matching excited streamer audio to "sub celebration", a very common training-data template), vision inherited and amplified that error into the title/hook/description/voiceover with no ground-truth check. Now:
- Stage 6 pulls ±8 s of verbatim transcript around the peak timestamp and injects it into the prompt.
- Prompt explicitly tells the model: `title/description/hook MUST describe what the transcript literally says`.
- New `grounded_in_transcript: true|false` field in the vision JSON; `false` values are logged so downstream review can flag questionable clips.

**Detection diagnostic** — wrote `CLIPPING_DIAGNOSTIC.md` at the project root for research review. Covers: how Pass A/B/C coordinate, how vision is fed context, where hallucinations propagate, a mechanical reconstruction of the Ranked-3.0 failure, and 10 industry-practice recommendations for future work (separate text-grounded from vision calls, self-consistency sampling, cross-field contradiction checks, feedback-loop eval sets, dataset references).

Pages touched: [[concepts/bugs-and-fixes]] (two new bug entries carried over from the previous day), [[concepts/originality-stack]] (framing modes updated), [[concepts/clip-rendering]] (framing section updated), [[concepts/vision-enrichment]] (grounding section added).

Files changed: `scripts/clip-pipeline.sh`, `scripts/lib/stitch_render.py`, `dashboard/app.py`, `dashboard/templates/index.html`, `CLIPPING_DIAGNOSTIC.md` (new).

---

## [2026-04-23] update | Fix: quiet clip audio + stitch-count AttributeError

Two bugs discovered after the first Wave D production run:

**Quiet clip audio** — When TTS voiceover or music-bed was enabled, clips came back roughly −13 dB quieter than pre-originality renders. Two causes stacked:
1. `amix` defaults to `normalize=1`, which divides every input by the number of sources. With 2 inputs the source dropped to 0.5×.
2. On top of that I was ducking the source to `volume=0.45` whenever VO was present — another −7 dB.

Combined: source ended up at ~0.22 = −13 dB. The old pre-TTS render path didn't go through `amix` at all, so volume matched the source exactly.

**Fix** (`scripts/clip-pipeline.sh` Stage 7 mix block):
- Added `normalize=0` to `amix` so per-input volumes control the final level directly.
- Dropped the source duck; source stays at `volume=1.0` to match the pre-TTS behavior.
- VO gain reduced from 2.3 to 1.6 (amix no longer halves it).
- Final `volume=0.95` on the mix output for inter-sample-peak headroom.

**Stitch-count AttributeError** — Pipeline traceback during the Stage 7e stitch guard:
```
File "<string>", line 1, in <genexpr>
AttributeError: 'str' object has no attribute 'get'
```
The Python one-liner that counted stitch groups iterated `for x in g` where `g` was the loaded `moment_groups.json` — but that file's top level is now `{groups:[...], moments:[...], summary:{...}}`. Iterating the dict yielded its string keys, and calling `.get('kind')` on a string crashed.

**Fix** (`scripts/clip-pipeline.sh` line 2563): pull the list out of the dict first — `d.get('groups', [])` — and wrap the whole command in `|| echo 0` plus `${STITCH_COUNT:-0}` so a future schema shift can't terminate the pipeline.

Pages touched: [[concepts/bugs-and-fixes]] (to be updated).
Files changed: `scripts/clip-pipeline.sh`.

---

## [2026-04-22] update | Docker image slimming and asset externalization

Removed ~3 GB of baked-in weights from the `stream-clipper` image and made the remaining artifacts user-visible, in response to the "black box" concern. See [[concepts/image-slimming]] for the full design.

**Image changes:**
- Whisper `large-v3` weights no longer baked in. Host folder `./models/whisper/` is mounted at `/root/.cache/whisper-models`; faster-whisper lazy-downloads on first pipeline run.
- Piper voice no longer baked in. Host folder `./models/piper/` is mounted at `/root/.cache/piper`; dashboard fetches voices on demand.
- New `requirements.txt` + `requirements-originality.txt` at project root — users can see (and pin) every Python dependency without reading the Dockerfile.
- New `ORIGINALITY_STACK` build arg (`full` default / `slim`). Slim build omits piper-tts / librosa / opencv-python-headless (~350 MB). Originality helpers fail gracefully when their packages are missing.
- `.dockerignore` now excludes `models/`, `music/`, `AIclippingPipelineVault/`, pycache, node_modules — much smaller build context.

**New assets:**
- `models/README.md` — explains the cache layout, how to change Whisper models, how to add Piper voices.
- `music/README.md` — explains the wave-D music library conventions (folder-based tier A, tier C via scan button).
- `scripts/lib/fetch_assets.py` — user-runnable CLI: `status` / `whisper <model>` / `piper <voice>`. JSON output for dashboard consumption.

**Dashboard additions:**
- **Asset Cache** panel under Folder Settings. Shows Whisper + Piper disk usage live. Includes fields to fetch new models / voices with status feedback.
- New endpoints: `GET /api/assets/status` and `POST /api/assets/fetch` both wrap `fetch_assets.py` and run it inside the container in docker-exec mode.

**Net effect:** full image ~5.5 GB (was ~8.5 GB); slim image ~5.2 GB. Pipeline performance unchanged — first run is slightly slower only if the Whisper cache is empty.

Pages touched: [[concepts/image-slimming]] (new), [[index]], [[entities/dashboard]] (asset panel), [[concepts/deployment]] (build-arg + mounts).

Files changed: `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `requirements.txt` (new), `requirements-originality.txt` (new), `models/README.md` (new), `models/whisper/.gitkeep`, `models/piper/.gitkeep`, `music/README.md` (new), `scripts/lib/fetch_assets.py` (new), `dashboard/app.py`, `dashboard/static/app.js`, `dashboard/templates/index.html`.

---

## [2026-04-22] update | Originality stack: waves A/B/C/D/E + multimodal model swap

Implemented the full [[concepts/originality-stack]] in response to the TikTok 2025 unoriginal-content research ([[raw/tiktokRes]]). Five coordinated waves, all independently toggleable from the dashboard **Originality & Render** panel:

- **Wave A** — per-clip randomized blur (`18–32`, passes `3–6`), optional horizontal flip (when vision says it's mirror-safe), eq + hue + vignette + micro-shake color/motion stack, rotating 6-palette hook card, rotating 5-variant subtitle style. Export spec bumped to CRF 20, preset slow, High@4.2, 18–20 Mbps, AAC 192 k.
- **Wave B** — four framing modes replacing the single blur-fill: `blur_fill` (legacy), `smart_crop` (crops chat/logo/cam bboxes returned by vision — default), `centered_square` (1080×810 foreground padded top/bottom), `camera_pan` (uses wave-E face track). Stage 6 prompt extended with `mirror_safe` / `chrome_regions` / `voiceover` fields; parser updated to harvest them safely with defaults.
- **Wave C** — MomentGroup data model. New Stage 4.5 (`scripts/lib/moment_groups.py`) detects narrative arcs (2+ adjacent storytime/emotional/hot_take within 120 s → one 45–90 s long clip) and stitch bundles (3–4 short funny/hype/reactive moments → one ≈28 s composite). New Stage 7e (`scripts/lib/stitch_render.py`) concatenates stitch members with `xfade` transitions.
- **Wave D** — [[entities/piper]] local TTS voiceover layer (ducks source audio to 0.45, boosts VO to 2.3, zero dropout transitions) and music-bed layer (tier A folder convention, tier C [[entities/librosa]] feature scoring behind a dashboard toggle). One-shot music tagger `scripts/lib/scan_music.py` available via **Scan Music** button (`POST /api/music/scan`).
- **Wave E** — new Stage 6.5 runs [[entities/face-pan]] (OpenCV Haar cascade) on each clip window when `CLIP_CAMERA_PAN=true`. Produces a keyframe JSON; Stage 7's `camera_pan` case interpolates it into an FFmpeg piecewise-linear `crop` expression. Reality-TV swing between detected speakers every ~4 s. Fallback ladder drops to blur_fill on zero faces / portrait source / missing cascade.

**Also**: retired [[entities/qwen3-vl]] from the default config — vision runs on the same multimodal model as text (Gemma 4 / Qwen 3.5). Stage 5→6 VRAM swap already short-circuits when text and vision model IDs match.

**Dashboard**: new **Originality & Render** panel. Settings persist to `config/originality.json` and are forwarded as `CLIP_*` env vars through `spawn_pipeline` (both direct and docker-exec paths).

**Dockerfile**: added `piper-tts`, `librosa`, `soundfile`, `opencv-python-headless`. Pre-downloads `en_US-amy-low` Piper voice to `/root/.cache/piper/`. Copies `scripts/lib/` into the image.

Pages created: [[concepts/originality-stack]], [[entities/piper]], [[entities/librosa]], [[entities/face-pan]].
Pages updated: [[overview]], [[concepts/clipping-pipeline]], [[concepts/clip-rendering]], [[concepts/vision-enrichment]], [[entities/dashboard]], [[entities/qwen3-vl]] (marked retired), [[index]].

Files changed: `scripts/clip-pipeline.sh`, `scripts/lib/*.py` (7 new files: originality.py, piper_vo.py, music_pick.py, scan_music.py, face_pan.py, moment_groups.py, stitch_render.py), `Dockerfile`, `dashboard/app.py`, `dashboard/static/app.js`, `dashboard/templates/index.html`.

---

## [2026-04-20] update | Fix: folder path changes now update docker-compose.yml mounts

**Bug**: configured folder paths had no effect on where the pipeline actually wrote clips (or read VODs from). The `spawn_pipeline()` docker exec path never passed `CLIP_VODS_DIR`/`CLIP_CLIPS_DIR` to the container, so the pipeline always used its hardcoded container defaults (`/root/VODs`, `/root/VODs/Clips_Ready`), which map to the original `./vods`/`./clips` host directories.

**Fix**: `api_paths_update()` now calls `update_docker_compose_mounts()`, which uses regex to rewrite the host-side volume bindings in `docker-compose.yml` while leaving the container paths unchanged. Returns `restart_required: true` when the file is modified. Dashboard shows a "Restart required" notice with a **Restart Container** button. After restart, the container mounts the new host folders at the same container paths and the pipeline writes to the right place.

Regex replaces `/root/VODs/Clips_Ready` binding first (more specific), then `/root/VODs` with a negative lookahead `(?!/)` to avoid double-matching. Reverts to `./vods`/`./clips` notation when the selected path resolves to the project defaults (keeps the file portable).

Pages touched: [[entities/dashboard]].

Files changed: `dashboard/app.py`, `dashboard/static/app.js`.

---

## [2026-04-20] update | Native OS folder picker for folder settings

Added a **Browse…** button to each folder input in the Folder Settings panel. Clicking it opens a native OS folder-picker dialog (Windows Explorer style) via a tkinter subprocess spawned from the Flask backend. Selected path is returned to the frontend and fills the input. User can cancel the dialog without any change.

Implementation: `POST /api/browse-folder` endpoint in `app.py`. Spawns `python -c "import tkinter..."` as a subprocess with `-topmost` so the dialog appears above the browser. Falls back gracefully (button just logs a warning) when running inside Docker (no display). `browseFolderFor(inputId)` JS function. CSS `.folder-row` + `.folder-browse-btn` classes.

Pages touched: [[entities/dashboard]].

Files changed: `dashboard/app.py`, `dashboard/static/app.js`, `dashboard/static/style.css`.

---

## [2026-04-20] update | Configurable VOD and clips folder paths

Added a **Folder Settings** panel to the dashboard allowing users to configure the VOD source folder and clips output folder without editing code. Paths are persisted to `config/paths.json` and take effect immediately (no restart needed).

Implementation: `PATHS_CONFIG` constant + `_reload_path_globals()` function in `app.py` that updates module-level `VODS_DIR`/`CLIPS_DIR` globals and derived paths at startup and on save. `GET/PUT /api/paths` endpoints. Dashboard panel with two text inputs and a styled save bar. Defaults remain the project-relative `./vods` and `./clips` directories.

Note: in Windows host + docker exec mode, the configured paths affect dashboard file browsing only. The pipeline inside the container continues using its volume-mounted paths (`/root/VODs`, `/root/VODs/Clips_Ready`). In inside-Docker mode, the configured paths are passed directly to the pipeline via `pipeline_env()`.

Pages touched: [[entities/dashboard]].

Files changed: `dashboard/app.py`, `dashboard/templates/index.html`, `dashboard/static/app.js`, `dashboard/static/style.css`.

---

## [2026-04-20] update | Hook caption, title spaces, 2× speed, fonts

Multiple Stage 7 rendering additions:
- **Hook caption**: AI-generated punchy top-of-video title in the style/voice of the stream niche. New `hook` field added to Stage 6 LLM prompt and manifest. `CLIP_HOOK_CAPTION` env var (default `true`); dashboard "Hook caption" checkbox toggle. Rendered via FFmpeg `drawtext` filter (DejaVuSans-Bold, white box, black text, top-center, y=55). `fonts-dejavu-core` added to Dockerfile.
- **Title spaces**: Clip filenames now use spaces instead of underscores (e.g. `Epic Clutch Play.mp4`). Title sanitization updated in manifest generation Python block.
- **2× speed**: Added `2.0` option to the dashboard Speed dropdown.
- Hook text wraps at 22 chars/line (max 3 lines) via Python `textwrap`, written to per-clip `clip_{T}_hook.txt` temp file to avoid shell quoting issues.

Pages touched: [[concepts/clip-rendering]].

Files changed: `scripts/clip-pipeline.sh`, `dashboard/app.py`, `dashboard/templates/index.html`, `dashboard/static/app.js`, `Dockerfile`.

---

## [2026-04-19] update | Simplified pitch: proportional to speed, no separate control

Removed independent voice pitch control. Pitch now always equals speed (`rubberband=tempo=N:pitch=N`) so voice sounds like a natural fast-talker. Removed `CLIP_PITCH` env var, Voice pitch dropdown from dashboard, and all `pitch` parameters from `app.py` and `app.js`. Pages touched: [[concepts/clip-rendering]].

---

## [2026-04-19] update | Speed-up + pitch shift for clip rendering

Added video speed-up and voice pitch controls to Stage 7 rendering. `CLIP_SPEED` (1.0/1.1/1.25/1.5) prepends `setpts=PTS/N` to the blur-fill filter chain and drives `rubberband=tempo=N:pitch=P` on the audio stream. `CLIP_PITCH` (1.0/1.059/1.122/1.189) sets the voice pitch ratio independently of tempo (no chipmunk effect at default 1.0). SRT timestamps are rescaled by `1/speed` via `rescale_srt()` when speed ≠ 1.0. Dashboard gains Speed and Voice pitch dropdowns in Clip Controls. Pages touched: [[concepts/clip-rendering]].

Files changed: `scripts/clip-pipeline.sh`, `dashboard/app.py`, `dashboard/templates/index.html`, `dashboard/static/app.js`.

---

## [2026-04-19] update | Caption size reduced + dashboard caption toggle

Reduced subtitle font size from 16 → 11 in Stage 7 FFmpeg render. Added `CLIP_CAPTIONS` env var (default `true`) to pipeline — when `false`, renders without subtitle filter. Dashboard Clip Controls panel gains a **Captions** checkbox (checked by default) that controls the toggle via the `/api/clip` and `/api/clip-all` POST bodies. Pages touched: [[concepts/clip-rendering]].

Files changed: `scripts/clip-pipeline.sh`, `dashboard/app.py`, `dashboard/templates/index.html`, `dashboard/static/app.js`.

---

## [2026-04-19] update | README rewrite + wiki deployment update; classification system documented

Complete `README.md` rewrite to reflect current architecture (LM Studio, no Ollama). Major additions:
- **Setup Guide** (8 steps, Discord bot intentionally last): prerequisites → clone → configs → LM Studio setup → build container → configure dashboard models → test pipeline → Discord bot
- **Classification System** section: every file that participates in deciding what gets clipped, data flow diagram, keyword category tables, segment-type weight multipliers table
- Updated Models section: LM Studio model IDs, 35B vs 9B tradeoffs, thinking mode note
- Updated Troubleshooting: Stage 3/4/6 failures, LM Studio unreachable, token budget issues
- Updated Project Structure: reflects current single-container + LM Studio architecture

`AIclippingPipelineVault/wiki/concepts/deployment.md` fully rewritten: LM Studio-centric, step-by-step setup, volume mounts, config file reference, persistent log docs.

`AIclippingPipelineVault/wiki/overview.md`: Updated model table to current LM Studio IDs; added 35B vs 9B note.

`AIclippingPipelineVault/wiki/index.md`: Fixed stale model descriptions; updated bugs-and-fixes count (21); updated deployment description.

Pages touched: [[overview]], [[concepts/deployment]], [[index]].

## [2026-04-19] update | Fix Stage 3 silent misclassification: max_tokens 1024→3000, add tail-scan fallback

Stage 3 `max_tokens=1024` caused the 35B model to use 1023/1024 tokens on reasoning (finish=length) for almost every chunk, silently defaulting all segments to `just_chatting`. Fix (BUG 21): `max_tokens` raised to 3000 so the model has room to finish thinking naturally; added `finish=length` tail-scan fallback (last 600 chars of `reasoning_content`) as a safety net if still cut off.

Pages touched: [[concepts/bugs-and-fixes]] (BUG 21).

## [2026-04-19] update | Fix 35B token exhaustion: raise max_tokens; research 9B vs 35B thinking behavior

Root cause of all remaining Stage 4/6 failures with `qwen/qwen3.5-35b-a3b` confirmed (BUG 20): LM Studio's endpoint does not forward `chat_template_kwargs` to the 35B model's chat template. Thinking cannot be disabled. The 35B has thinking ENABLED by default (~8192 token budget); the 9B has it DISABLED by default. At `max_tokens=3000`, the model used 2999 reasoning tokens and hit the limit before writing any content.

Changes to `scripts/clip-pipeline.sh`:
- `call_llm()` `max_tokens` `3000` → `8000`
- `call_llm()` `timeout` `300` → `600` s (8000 tokens at ~30 tok/s = ~267 s generation)
- Stage 6 `max_tokens` `4000` → `6000`
- `VISION_STAGE_TIMEOUT` `1200` → `3600` s (11 moments × ~220 s/moment with 35B)

Pages touched: [[concepts/bugs-and-fixes]] (BUG 20), [[entities/lm-studio]] (9B vs 35B thinking table, confirmed failure modes).

## [2026-04-19] update | Fix LM Studio queue backup; increase Stage 4/6 timeouts and Stage 6 max_tokens

Three fixes to `scripts/clip-pipeline.sh` for large (35B MoE) model support (BUG 19):

1. **`call_llm()` timeout** `120` → `300` s: The 35B model takes 150–250 s per Stage 4 chunk. At 120 s, every attempt timed out and submitted another request to LM Studio while it was still processing. Queue depth grew by 3 per chunk, causing all subsequent chunks to time out — except one that fluked through while LM Studio was between requests.

2. **Stage 6 `VISION_PER_MOMENT_TIMEOUT`** `90` → `300` s: Same logic — 35B vision calls need ~150–200 s. 90 s caused per-frame timeouts which fed the LM Studio queue.

3. **Stage 6 `max_tokens`** `2000` → `4000`: The 35B model uses 1148–1999 reasoning tokens before writing the JSON answer (~100 tokens). Calls that hit 1999/2000 tokens got `finish_reason=length` and empty content. 4000 tokens gives room to finish.

Pages touched: [[concepts/bugs-and-fixes]] (BUG 19).

## [2026-04-19] update | reasoning_content fallback for 35B models; persistent timestamped logs; Stage 3/4 fixes

Four fixes applied to `scripts/clip-pipeline.sh` to handle large models (35B MoE) and improve observability:

1. **`reasoning_content` fallback** (BUG 17): When `content` is empty and `finish_reason == "stop"`, all three LLM call sites (Stages 3, 4, 6) now extract the answer from `reasoning_content`. Models like `qwen/qwen3.5-35b-a3b` ignore `chat_template_kwargs` and always put their answer there. `finish_reason=length` (mid-think cutoff) still retries as before. Applied in the Stage 3 inline block, `call_llm()`, and the Stage 6 vision loop.

2. **Stage 3 timeout** (BUG 17): `timeout=30` → `timeout=180`. The 35B model needs 60–180 s per classification call; 30 s caused every chunk to time out.

3. **Stage 4 call site fix** (BUG 17): `call_llm(prompt, max_tokens=800)` → `call_llm(prompt)`. This explicit override was causing `total_tokens=800` in all Stage 4 diagnostics despite the function default being 3000.

4. **Persistent timestamped log** (BUG 18): Every pipeline run now writes to both the ephemeral `/tmp/clipper/pipeline.log` (for SSE streaming, cleaned up on EXIT) and a new `$CLIPS_DIR/.pipeline_logs/YYYYMMDD_HHMMSS_VODSLUG.log` that survives the cleanup trap. Path printed at startup.

Pages touched: [[concepts/bugs-and-fixes]] (BUG 17, BUG 18), [[entities/lm-studio]] (request format, reasoning_content fallback note).

## [2026-04-19] update | Increase LLM token budgets; rich diagnostics for empty-content case

Follow-up to the Qwen3.5 thinking fix. Clarified from LM Studio docs: the "separate reasoning_content and content" toggle controls **presentation** only, not whether the model thinks. `chat_template_kwargs: {"enable_thinking": false}` may suppress thinking, but the generous token budget is the safety net if it doesn't — the model can finish reasoning AND still produce content before hitting the limit.

Changes to `scripts/clip-pipeline.sh`:
- Stage 3 `max_tokens` 50 → 1024 (50 was never sufficient even without thinking)
- Stage 4 `call_llm()` default `max_tokens` 1500 → 3000; completely rewritten response handling: detects empty content, logs `finish_reason` + `reasoning_tokens` + `reasoning_content` preview, separates "still thinking" from actual errors, only counts as failure if content is empty after all retries
- Stage 6 vision `max_tokens` 1500 → 2000; same diagnostic pattern applied; JSON parse errors now caught separately from empty-content cases

Pages touched: [[concepts/bugs-and-fixes]], [[entities/lm-studio]].

## [2026-04-18] update | Fix Qwen3.5 thinking via chat_template_kwargs; unified model; context length API

Root cause of all Stage 4/6 LLM failures confirmed: `/no_think` user-message prefix has no effect on Qwen3.5 (it was removed from Qwen3 → Qwen3.5). Correct LM Studio parameter is `chat_template_kwargs: {"enable_thinking": false}` in the request body.

Changes:
- `scripts/clip-pipeline.sh`: Replaced `/no_think` prefix with `chat_template_kwargs` at Stages 3, 4, 6. Added `load_model()` bash function that calls `/api/v1/models/load` with `context_length` before Stage 3 (and conditionally before Stage 6). Stage 5→6 model swap now skipped when `TEXT_MODEL == VISION_MODEL`. Stage 6 `max_tokens` raised 800 → 1500. Default `TEXT_MODEL` and `VISION_MODEL` both set to `qwen/qwen3.5-9b`. Added `CONTEXT_LENGTH` env var (default 8192).
- `config/models.json`: `vision_model` → `qwen/qwen3.5-9b`, added `context_length: 8192`.
- `dashboard/app.py`: `DEFAULT_MODELS` and `SUGGESTED_MODELS` updated for unified model; added `CONTEXT_LENGTH_GUIDE`; `/api/models` now returns `context_length_guide`; PUT handler accepts `context_length`; both `pipeline_env()` and `spawn_pipeline()` inject `CLIP_CONTEXT_LENGTH`.
- `dashboard/static/app.js`: Added context window picker card in Models panel with VRAM guidance; `updateSaveBar()` tracks context changes.

Pages touched: [[concepts/bugs-and-fixes]] (BUG 15 revised), [[entities/lm-studio]].

## [2026-04-18] update | Fix Qwen3 reasoning token exhaustion; correct model IDs; cache LM Studio poll

Fixed three root causes of pipeline LLM call failures:

1. **`/no_think` prefix on all pipeline LLM calls** (`scripts/clip-pipeline.sh`): `qwen/qwen3.5-9b` is a reasoning model — without this switch it spends all `max_tokens` on thinking (`reasoning_content`) and returns `content: ""`, silently failing every call. Added `/no_think\n\n` prefix to Stage 3 payload, Stage 4 `call_llm()`, and Stage 6 vision text part. Also raised `max_tokens`: Stage 3: 20→50, Stage 4 default: 800→1500.

2. **Correct model IDs** (`config/models.json` already correct; `dashboard/app.py` `DEFAULT_MODELS` and `SUGGESTED_MODELS` updated; `config/openclaw.json` model entries updated): LM Studio uses `org/model` format — `qwen/qwen3.5-9b`, `qwen/qwen3-vl-8b`, `qwen/qwen2.5-vl-7b`. Old stale IDs (`qwen3.5-9b-instruct`, `qwen2.5-vl-7b-instruct`) replaced throughout dashboard code and OpenClaw config.

3. **LM Studio poll cache** (`dashboard/app.py`): `check_lm_studio()` was calling `GET /v1/models` every 3 seconds (every status poll). Added 30-second TTL cache via `_lm_studio_cache` module global — reduces poll rate from 20×/min to ≤2×/min.

Pages touched: [[concepts/bugs-and-fixes]] (BUG 15, BUG 16), [[entities/lm-studio]].

## [2026-04-18] update | Fix openclaw.json api field; add LM Studio model picker with recommendations

Fixed `config/openclaw.json` and `config/openclaw.example.json`: `api: "openai"` → `api: "openai-completions"` (OpenClaw only accepts specific API type strings — this was causing the container restart loop). Added `SUGGESTED_MODELS` dict to `dashboard/app.py` returned via `/api/models`; updated `dashboard/static/app.js` to fix stale `availableOllama` → `availableLmStudio`, use `suggested` data to show ⭐ markers and tip/warning messages in model dropdowns, `resetModel()` now switches to the suggested model ID (with alert if not loaded in LM Studio), simplified Hardware panel to only show `whisper_device` (GPU backend now managed in LM Studio), fixed `restartServices()` to check `lm_studio` not `ollama` field. Added `.model-status-warn` / `.model-status-tip` CSS. Pages touched: [[entities/lm-studio]].

## [2026-04-18] update | Migrate LLM backend from Ollama-in-Docker to LM Studio (native Windows)

Replaced the `ollama` Docker container with LM Studio running natively on Windows. LM Studio serves an OpenAI-compatible API on port 1234, accessible from the container via `http://host.docker.internal:1234`. Motivation: native NVIDIA+AMD multi-GPU support without WSL2 Vulkan driver hacks (which caused silent CPU fallback — see BUG 14 in [[concepts/bugs-and-fixes]]).

Code changes:
- `docker-compose.yml`: removed `ollama` service and `ollama_data` volume; added `extra_hosts: ["host.docker.internal:host-gateway"]` to `stream-clipper`
- `scripts/clip-pipeline.sh`: `OLLAMA_URL` → `LLM_URL`; `unload_ollama()` → `unload_model()` (uses `/api/v1/models/unload`); `call_ollama()` → `call_llm()` (OpenAI API format); all three Python heredocs (Stages 3, 4, 6) updated to `/v1/chat/completions`, OpenAI payload structure, response via `choices[0].message.content`, vision via `image_url` content part; think-tag stripping added
- `scripts/entrypoint.sh`: removed Ollama wait + model pull; added LM Studio readiness poll (`GET /v1/models`)
- `config/hardware.json`: removed `gpu_backend`/`gpu_count`/`gpu_pair`; only `whisper_device` remains
- `config/models.json`: `ollama_url` → `llm_url`; model IDs updated to LM Studio format (`qwen3.5-9b-instruct`, `qwen2.5-vl-7b-instruct`)
- `config/openclaw.json` + `openclaw.example.json`: provider changed from `ollama` to `lmstudio` with `baseUrl` pointing to port 1234
- `dashboard/app.py`: removed `get_ollama_container()`; replaced `query_ollama_models()` with `query_lm_studio_models()` (calls `/v1/models`); added `check_lm_studio()`; `api_status()` now reports `lm_studio` not `ollama`; simplified `DEFAULT_HARDWARE` and `api_hardware_update()`

Pages touched: [[overview]], [[entities/lm-studio]] (created), [[entities/ollama]] (marked retired), [[concepts/vram-budget]], [[index]].

## [2026-04-17] update | Fix Vulkan CPU fallback; add GPU detection to entrypoint; strengthen CLAUDE.md

Diagnosed Stage 3+ high CPU usage: Vulkan ICDs not initializing inside container, Ollama silently using CPU (confirmed via `inference compute library=cpu` in docker logs and `vulkaninfo` showing only llvmpipe). Fixed `scripts/entrypoint-ollama.sh`: added `count_real_vulkan_gpus()` helper that runs `vulkaninfo --summary` before committing to Vulkan mode; if no real GPU hardware found, auto-falls back to CUDA with a warning banner instead of silently using CPU. Added prompt injection banner to `CLAUDE.md`. Fixed stale container names in `CLAUDE.md` (`ollama-gpu` → `ollama`, `stream-clipper-gpu` → `stream-clipper`). Pages touched: [[entities/ollama]], [[concepts/bugs-and-fixes]] (BUG 14), [[concepts/deployment]].

## [2026-04-17] lint | Post-refactor wiki audit and fixes

Audited all wiki pages against actual codebase after profile-collapse refactor. Fixed stale container names (`ollama-gpu` → `ollama`, `stream-clipper-gpu` → `stream-clipper`), removed old profile commands, documented `OLLAMA_VULKAN=1` requirement (BUG 12), documented `vulkan-tools` fix (BUG 13), updated dashboard feature list (model switcher + hardware panel now implemented), expanded REST API table with 6 new endpoints, added hardware.json schema table to dashboard page, added deprecated-files notice to deployment page, fixed `spawn_pipeline()` error message in `dashboard/app.py` (still referenced old `--profile` flags). Pages touched: [[overview]], [[entities/ollama]], [[entities/dashboard]], [[concepts/deployment]], [[concepts/bugs-and-fixes]].

## [2026-04-17] update | Collapse multi-profile architecture to single service pair

Removed all Docker Compose profiles (cuda/vulkan/mixed/cpu). Single `ollama` + `stream-clipper` service pair. New `Dockerfile.ollama` (unified CUDA+Vulkan image) and `scripts/entrypoint-ollama.sh` (reads hardware.json, sets CUDA_VISIBLE_DEVICES / GGML_VK_VISIBLE_DEVICES). WSL2 AMD Vulkan enabled via /dev/dxg + /usr/lib/wsl mounts in compose. Dashboard Hardware panel gains "Restart Services" button (calls new /api/restart endpoint). Pages touched: [[concepts/deployment]].

## [2026-04-17] update | Fix apt-get network failures in Dockerfile.ollama-vulkan

Added apt retry and timeout configuration to `Dockerfile.ollama-vulkan` to handle intermittent Docker BuildKit network issues on Windows/WSL2. The `apt-get` layer now retries each package fetch up to 5 times with a 30-second timeout before failing. Pages touched: [[concepts/bugs-and-fixes]].

## [2026-04-16] update | Multi-backend GPU support — CUDA, Vulkan (AMD), CPU profiles

Added Vulkan (AMD/Intel) and explicit CUDA/CPU backend selection. New docker-compose profiles: `cuda` (NVIDIA, also aliased `gpu`), `vulkan` (AMD/Intel), `cpu`. New files: `Dockerfile.ollama-vulkan`, `scripts/entrypoint-ollama-vulkan.sh`, `config/hardware.json`. Whisper device (`cuda`/`cpu`) now controlled via `CLIP_WHISPER_DEVICE` env var read from hardware config; Vulkan and CPU modes force Whisper to CPU. Dashboard Hardware panel added for backend, GPU count, and Whisper device selection. Pages touched: [[concepts/deployment]], [[concepts/vram-budget]].

## [2026-04-07] update | Full wiki rebuild — external summaries integrated and removed

Ingested `DEVELOPMENT_SUMMARY.txt` and `fix.txt`. Corrected all inaccuracies from initial bootstrap (7→8 stages, missing models, wrong rendering technique, wrong Whisper hardware). External summary files deleted.

Pages rewritten: [[overview]], [[entities/faster-whisper]], [[entities/qwen3-vl]], [[entities/qwen35]], [[entities/ollama]], [[entities/openclaw]], [[entities/ffmpeg]], [[entities/discord-bot]], [[concepts/clipping-pipeline]], [[concepts/highlight-detection]], [[concepts/vram-budget]], [[concepts/deployment]].

Pages created: [[entities/qwen25]], [[entities/dashboard]], [[concepts/segment-detection]], [[concepts/vision-enrichment]], [[concepts/clip-rendering]], [[concepts/context-management]], [[concepts/bugs-and-fixes]], [[concepts/open-questions]], [[sources/development-summary]], [[sources/fix-txt]].

Root `CLAUDE.md` created with vault-update prompt injection for agents working on the project.

## [2026-04-07] ingest | OpenClaw Stream Clipper — Detailed System Summary

Processed `OpenClaw_Stream_Clipper_Summary.md` (project root). Initial wiki bootstrap.

Pages created: [[overview]], [[sources/openclaw-stream-clipper-summary]], [[entities/openclaw]], [[entities/ollama]], [[entities/qwen3-vl]], [[entities/qwen35]], [[entities/faster-whisper]], [[entities/ffmpeg]], [[entities/discord-bot]], [[concepts/clipping-pipeline]], [[concepts/highlight-detection]], [[concepts/vram-budget]], [[concepts/deployment]].
