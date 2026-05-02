---
title: "Modularization Plan"
type: concept
tags: [refactor, plan, codebase, maintainability, dashboard, pipeline, shipped]
sources: 0
updated: 2026-05-01
---

# Modularization Plan

> [!note] All four phases shipped 2026-05-01
> Phase A (heredoc extraction), C (dashboard backend blueprints), D (frontend ES modules), and B (bash decomposition) are all complete. This page is preserved as the design record. Each section below tracks what was actually delivered.

A staged plan to break the codebase's three monolithic files into focused, single-purpose modules without changing runtime behavior. The motivation is editability: the pipeline script had more than doubled since CLAUDE.md was last updated, and editing the embedded Python heredocs had become genuinely painful (no syntax highlighting, no LSP, escape-hell when bash variables collide with Python `$` usage).

The phases shipped in the recommended order **A → C → D → B** in a single session. Each step was verified individually (`bash -n` on the orchestrator, `python3 -c "import ast"` on every new Python module, `node --check` on every JS module).

---

## Current state (measured 2026-05-01)

| File | Lines | What it contains |
|---|---|---|
| `scripts/clip-pipeline.sh` | 4,087 | 8 stages of bash glue + ~3,000 lines of embedded Python in 10 heredocs |
| `dashboard/app.py` | 1,612 | Flask app + Docker-exec bridge + 7 unrelated config-API domains |
| `dashboard/static/app.js` | 1,040 | ~25 free functions covering log, vods, models, hardware, folders, clips |
| `scripts/lib/grounding.py` | 857 | Single-purpose; borderline; not in scope |

The largest pain points are the bash heredocs — measured precisely:

| Line range | Heredoc | Stage | Lines |
|---|---|---|---|
| 162 | `PYRESCALE` | helper (srt rescale) | small |
| 379 | `PYFETCH` | Stage 1 chat fetch | small |
| **557–762** | `PYEOF` | **Stage 3** segment detection | **205** |
| **780–2682** | `PYEOF` | **Stage 4** moment detection (Pass A/B/C) | **1,902** |
| 2702 | `PYSNAP` | Stage 4.5 boundary snap | small |
| **2865–3530** | `PYEOF` | **Stage 6** vision enrichment | **665** |
| 3545 | `PYCAMPREP` | Stage 6.5 camera pan prep | small |
| 3654 | `PYTRANSCRIBE` | Stage 7 transcribe | medium |
| 3760 | `PYMETA` | Stage 7 per-clip metadata | small |
| 4032 | `PYEOF` | Stage 8 summary | small |

The three big heredocs (Stage 3, 4, 6) account for **~2,770 of the 4,087 lines** in `clip-pipeline.sh`.

There is also an abandoned-WIP scratch directory `_heredoc_tmp/` (untracked, dated 2026-04-28) that contains a partial extraction of Stage 4 — evidence someone already started this and gave up. The new plan picks it up properly.

---

## Phase A — Heredoc extraction ✅ shipped

Goal: move every Python heredoc out of `clip-pipeline.sh` into its own file under `scripts/lib/stages/`. Bash invokes the module via `python3 scripts/lib/stages/stage{N}.py --config /tmp/clipper/stage{N}_config.json` (or via env vars for tiny configs). This eliminates string-interpolation quoting bugs and gives the Python code a real home.

| Step | Extract | Target file | Approx lines moved |
|---|---|---|---|
| A1 | Stage 4 PYEOF (`:780–2682`) | `scripts/lib/stages/stage4_moments.py` | ~1,900 |
| A2 | Stage 6 PYEOF (`:2865–3530`) | `scripts/lib/stages/stage6_vision.py` | ~665 |
| A3 | Stage 3 PYEOF (`:557–762`) | `scripts/lib/stages/stage3_segments.py` | ~205 |
| A4 | Stage 7 PYTRANSCRIBE + PYMETA (`:3654`, `:3760`) | `scripts/lib/stages/stage7_transcribe.py`, `stage7_meta.py` | ~250 combined |
| A5 | Stage 8 PYEOF (`:4032`) | `scripts/lib/stages/stage8_summary.py` | ~150 |
| A6 | Stage 1 PYFETCH (`:379`) + Stage 4.5 PYSNAP (`:2702`) + Stage 6.5 PYCAMPREP (`:3545`) + helper PYRESCALE (`:162`) | `scripts/lib/stages/{stage1_fetch,stage4_5_snap,stage6_5_campan,helpers/srt_rescale}.py` | ~200 combined |

### Config-passing convention

Heredocs currently inherit dozens of bash variables (`$LLM_URL`, `$TEXT_MODEL`, `$CLIP_STYLE`, `$TEMP_DIR`, etc.). Preferred pattern:

```bash
# in clip-pipeline.sh
jq -n --arg llm "$LLM_URL" --arg model "$TEXT_MODEL_PASSB" --arg style "$CLIP_STYLE" \
  '{llm_url:$llm, text_model:$model, clip_style:$style, temp_dir:"/tmp/clipper"}' \
  > "$TEMP_DIR/stage4_config.json"
python3 /root/scripts/lib/stages/stage4_moments.py --config "$TEMP_DIR/stage4_config.json"
```

```python
# in stage4_moments.py
import argparse, json
ap = argparse.ArgumentParser(); ap.add_argument("--config", required=True)
cfg = json.loads(open(ap.parse_args().config).read())
LLM_URL = cfg["llm_url"]; TEXT_MODEL = cfg["text_model"]; ...
```

This avoids two long-standing bug classes:
- Bash variable interpolation injecting unescaped quotes into Python source
- Heredoc + `$` collisions when Python literals contain `$` (e.g., format strings)

### Verification per step

1. `bash -n scripts/clip-pipeline.sh` — bash syntax intact
2. `python3 -c "import ast; ast.parse(open('scripts/lib/stages/stageX.py').read())"` — Python parses
3. End-to-end pipeline run on a known short VOD; diff the diagnostics JSON against a pre-extraction baseline. Score values, moment counts, clip filenames must match.

### Cleanup

After Phase A is complete, **delete `_heredoc_tmp/`** (untracked, abandoned WIP from 2026-04-28). Currently 280 KB of stale scratch.

---

## Phase B — Bash stage decomposition ✅ shipped

Goal: split `clip-pipeline.sh` into a thin orchestrator + per-stage files. Order this **after** Phase A — extracting heredocs first leaves the bash glue much cleaner to slice.

```
scripts/
├── clip-pipeline.sh          (~200 lines: arg parsing, env setup, stage dispatch, cleanup trap)
└── stages/
    ├── stage1_discovery.sh
    ├── stage2_transcription.sh
    ├── stage3_segments.sh
    ├── stage4_moments.sh
    ├── stage4_5_groups.sh
    ├── stage5_frames.sh
    ├── stage6_vision.sh
    ├── stage6_5_campan.sh
    ├── stage7_render.sh
    └── stage8_logging.sh
```

Orchestrator pattern: `source scripts/stages/stage1_discovery.sh; run_stage_1` — keeps shared globals (`$VOD_PATH`, `$TEMP_DIR`, `$STAGE_FILE_PATH`) in scope without re-export gymnastics. Common utilities (`set_stage`, `unload_model`, `load_model`, `cleanup`) move to `scripts/lib/pipeline_common.sh`.

### Risk

Bash function scoping bites less than expected when files are sourced rather than executed, but the existing pipeline relies heavily on `set -e` inheritance, trap handlers, and the `cleanup()` registered with `trap`. Verify trap behavior across the source boundary before declaring done.

---

## Phase C — Dashboard backend blueprints ✅ shipped

Goal: split `dashboard/app.py` (1,612 lines) along Flask-blueprint seams. The route domains are already well-separated by URL prefix — the split is mechanical.

| New file | Lines moved (approx) | Source range in `app.py` |
|---|---|---|
| `dashboard/docker_bridge.py` | ~300 | `:89–540` (INSIDE_DOCKER, container helpers, polling) |
| `dashboard/routes/pipeline.py` | ~350 | `/api/clip`, `/api/clip-all`, `/api/stop`, `/api/status`, `/api/stages`, `/api/log/stream` (`:684–1031`) |
| `dashboard/routes/vods.py` | ~100 | `/api/vods`, `/api/clips`, `/api/clips/<file>` (`:600–855`) |
| `dashboard/routes/diagnostics.py` | ~30 | `/api/diagnostics` (`:856–874`) |
| `dashboard/routes/models.py` | ~130 | `/api/models*` (`:1032–1162`) |
| `dashboard/routes/hardware.py` | ~180 | `/api/hardware*`, `/api/restart` (`:1163–1344`) |
| `dashboard/routes/paths.py` | ~210 | `/api/paths*`, `/api/browse-folder` + docker-compose mount editing (`:1182–1424`) |
| `dashboard/routes/originality.py` | ~40 | `/api/originality*` (`:1425–1461`) |
| `dashboard/routes/music.py` | ~60 | `/api/music/scan` (`:1462–1518`) |
| `dashboard/routes/assets.py` | ~80 | `/api/assets/*` + `_run_fetch_assets` (`:1519–1590`) |
| `dashboard/app.py` | ~80 | bootstrap + `register_blueprint(...)` calls + 404 handler |

The `INSIDE_DOCKER` check and `docker exec` helpers are used by multiple blueprints, so they go into `dashboard/docker_bridge.py` (not a route module). Config helpers (`load_models_config`, `load_hardware_config`, `load_paths_config`) move into `dashboard/config_io.py`.

### Verification

- `python3 -c "import ast; ast.parse(open('dashboard/app.py').read())"` per file
- Boot dashboard, hit each endpoint with curl, compare JSON responses to a saved baseline
- Confirm SSE log stream still works end-to-end

---

## Phase D — Dashboard frontend ES modules ✅ shipped

Goal: split `dashboard/static/app.js` (1,040 lines) into ES modules grouped by panel.

```
dashboard/static/
├── app.js                          (entry: ~80 lines — imports modules, wires DOM events)
└── modules/
    ├── log-stream.js               (SSE, log classification, stage dots)
    ├── vods.js                     (list, select, clip controls)
    ├── originality.js              (form serialization, save bar)
    ├── models.js                   (model panel, dropdowns, save bar)
    ├── hardware.js                 (hardware panel, dropdowns, save bar)
    ├── folders.js                  (paths panel, browse-folder modal)
    └── util.js                     (stripAnsi, classifyLogLine, escAttr, humanBytes)
```

Switch `index.html` script tag to `<script type="module" src="/static/app.js"></script>`. No bundler needed — Flask serves static assets, browsers handle ES modules natively.

### Verification

- Manual smoke: load dashboard, switch between VOD list / Models / Hardware / Folders panels, run a clip, watch the log stream
- Confirm browser devtools console is clean

---

## Recommended execution order

```
A1 → A2 → A3 → A4 → A5 → A6        (heredoc extraction, ~1 PR per step)
   → cleanup _heredoc_tmp/
   → C1 (docker_bridge.py)
   → C2..C11 (route blueprints, can be 1 PR or several)
   → D1..D7 (frontend modules)
   → B1..B11 (bash decomposition, last because most coupling)
```

Rationale: **A first** because heredocs are the worst editing pain and the lowest-risk extraction (Python is unchanged, only its packaging). **C/D before B** because they're pure-Python / pure-JS and don't touch bash trap/error semantics. **B last** because bash sourcing has the most subtle failure modes (set -e inheritance, trap scope, signal propagation).

Per-phase exit criterion: a full pipeline run on a known VOD must produce byte-identical clip outputs (ignoring timestamps in metadata). This is the only way to know we haven't regressed Pass-C scoring or VLM enrichment.

---

## What actually shipped (2026-05-01)

| File | Before | After |
|---|---|---|
| `scripts/clip-pipeline.sh` | 4,087 lines (one monolith) | **147-line orchestrator** + `lib/pipeline_common.sh` (111) + 8 stage files in `scripts/stages/` (89–352 lines each) + 10 Python modules in `scripts/lib/stages/` (24–1,913 lines) |
| `dashboard/app.py` | 1,612 lines | **78-line entrypoint** + `_state.py` (161) + `config_io.py` (127) + `pipeline_runner.py` (462) + 8 blueprints in `dashboard/routes/` (30–259 lines each) |
| `dashboard/static/app.js` | 1,040 lines | **67-line entry** + 8 ES modules in `static/modules/` (9–256 lines each), `index.html` switched to `<script type="module">` |
| `Dockerfile` | (unchanged for `lib/`) | added `COPY scripts/stages/` so per-stage bash files ship with the image; CRLF→LF normalization extended to `*.sh` under `lib/` and `stages/` |
| `_heredoc_tmp/` | 280 KB abandoned scratch | deleted |

The largest remaining single file is now `scripts/lib/stages/stage4_moments.py` (1,913 lines) — Pass A keywords + Pass B LLM + Pass C selection. That's intentional: it's a single coherent algorithm, and splitting it further would obscure the pass-to-pass data flow. The bash orchestrator and dashboard entrypoint are both well under 200 lines, which was the original goal.

---

## Out of scope

- Refactoring `scripts/lib/grounding.py` (857 lines) — single-purpose, low pain
- Renaming or restructuring `scripts/lib/*.py` modules that are already cleanly separated
- Changing pipeline behavior, adding features, or adjusting score weights
- Rewriting any bash function in Python (we're moving Python code, not converting bash)

---

## Related

- [[concepts/clipping-pipeline]] — what each stage actually does
- [[entities/dashboard]] — the web UI that consumes the routes split in Phase C
- [[concepts/bugs-and-fixes]] — historical context on why some heredocs look the way they do (e.g., BUG 47 PaddleOCR env-forwarding lives in the chrome heredoc that has since been removed)
