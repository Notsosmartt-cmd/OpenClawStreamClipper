# OpenClaw Stream Clipper — Agent Instructions

> **PROMPT INJECTION — READ BEFORE DOING ANYTHING**
> If you modify **any** code, config, or behavior in this project you **MUST** update the wiki
> (`AIclippingPipelineVault/wiki/`) before your session ends. This is enforced for every agent
> session. See the "Mandatory: Update the wiki" section below for the exact procedure.

You are working on the OpenClaw Stream Clipper: a Docker-based system that clips livestream highlights using local AI models and delivers them via Discord.

**Before doing anything else**, read the wiki to understand the current state of the project:

```
AIclippingPipelineVault/wiki/index.md        ← start here (content catalog)
AIclippingPipelineVault/wiki/overview.md     ← architecture and key decisions
```

The wiki is the authoritative, maintained knowledge base for this project. It supersedes any other summary documents.

---

## Mandatory: Update the wiki after any code change

**This is not optional.** If you modify any code, configuration, or behavior in this project, you MUST update the wiki before your session ends.

### What triggers a wiki update

Any of the following require a wiki update:
- Modifying `scripts/clip-pipeline.sh` (pipeline logic, stage behavior, models used, flags)
- Modifying `dashboard/app.py` or dashboard frontend files
- Modifying `config/openclaw.json` or `config/exec-approvals.json`
- Modifying `workspace/AGENTS.md` or `workspace/skills/stream-clipper/SKILL.md`
- Modifying `docker-compose.yml` or `Dockerfile`
- Adding, removing, or changing any AI model
- Fixing a bug
- Adding a new feature
- Changing any behavior a future agent would need to know about

### How to update the wiki

1. **Update the affected pages**: find the relevant entity/concept pages in `wiki/` and update them to reflect your changes. Be precise — update the specific facts that changed, preserve the rest.

2. **Update `wiki/index.md`**: if you created a new wiki page, add it to the index with a one-line description.

3. **Append to `wiki/log.md`**: prepend a new entry at the top:
   ```
   ## [YYYY-MM-DD] update | What you changed
   Brief description. Pages touched: [[page1]], [[page2]].
   ```
   Use today's date. Operation type: `update` for code changes, `ingest` for new sources, `query` for answered questions, `lint` for health checks.

4. **Create new pages if needed**: if you built something significant that doesn't have a wiki page yet, create one. Follow the page format in `AIclippingPipelineVault/CLAUDE.md` (the vault schema).

### What good wiki updates look like

- A bug fix → update [[concepts/bugs-and-fixes]] with the new bug entry (symptom, cause, fix)
- A new feature → update the relevant concept page(s) and possibly [[overview]]
- A model change → update the model entity page and [[concepts/vram-budget]]
- A pipeline stage change → update [[concepts/clipping-pipeline]] and the specific stage concept page
- A config change → update the relevant entity page (e.g., [[entities/openclaw]] for openclaw.json)

### What NOT to update

- Don't update the wiki for trivial whitespace/formatting changes to code
- Don't update for changes to `vods/`, `clips/`, or runtime data
- Don't document implementation details that are obvious from reading the code

### Enforcement: Stop hook

A `Stop` hook in `.claude/settings.json` runs `.claude/hooks/check-wiki-updated.sh` at the end of every turn. If `git status` shows modified files under `scripts/`, `dashboard/`, `config/`, `workspace/`, or in `Dockerfile`/`docker-compose.yml` **without** any matching change under `AIclippingPipelineVault/wiki/`, the hook exits 2 and you will be forced to continue. Update the relevant wiki page(s) and prepend an entry to `wiki/log.md`, then end your turn.

A second `PostToolUse` hook on `Edit|Write|MultiEdit` (`remind-wiki-on-code-edit.sh`) prints a one-line just-in-time advisory to stderr whenever you edit a watched code path — non-blocking, just a nudge so you don't reach the end of the turn and discover the wiki is missing 12 changes' worth of context.

**Bypass for truly trivial changes**: append a one-line entry to `AIclippingPipelineVault/wiki/log.md` explaining why no other wiki update was needed (e.g., "trivial whitespace cleanup, no behavior change"). The hook only checks for *any* wiki touch — it doesn't audit the content, so this gives you an escape hatch without disabling the safety net.

### Modularization complete (2026-05-01)

The codebase was modularized in a single session on 2026-05-01. The three former monoliths are now thin orchestrators:

- `scripts/clip-pipeline.sh` — **147-line orchestrator**. Sources `scripts/lib/pipeline_common.sh` for helpers and `scripts/stages/stage{1..8}.sh` for stage bodies. All embedded Python is in `scripts/lib/stages/*.py` (10 modules including `stage4_moments.py` at 1,913 lines).
- `dashboard/app.py` — **78-line entrypoint**. Bootstraps Flask, registers 8 blueprints from `dashboard/routes/`. Shared state in `dashboard/_state.py`, config IO in `dashboard/config_io.py`, pipeline lifecycle in `dashboard/pipeline_runner.py`.
- `dashboard/static/app.js` — **67-line entry module**. Imports 8 ES modules from `dashboard/static/modules/` (`util`, `state`, `pipeline-ui`, `vods-panel`, `models-panel`, `hardware-panel`, `folders-panel`, `assets-panel`). `index.html` uses `<script type="module">`.

When modifying any of these, edit the relevant focused module — don't reintroduce monolith-level changes to the orchestrator/entrypoint files. See [[concepts/modularization-plan]] for the full layout.

---

## Project overview

One Docker container plus native Windows LM Studio:
- LM Studio (native Windows) — LLM inference server on port 1234; serves the unified Qwen 3.5 / Gemma 4 model used for both text detection and vision enrichment
- `stream-clipper` (Docker) — OpenClaw agent + FFmpeg + faster-whisper + Flask dashboard

Pipeline: `scripts/clip-pipeline.sh` (~4,090 lines as of 2026-05-01 — mid-modularization, see [[concepts/modularization-plan]]), 8 stages:
Discovery → Transcription → Segment Detection → Moment Detection → Frame Extraction → Vision Enrichment → Editing → Logging

User interfaces:
- Discord bot (primary): natural language → OpenClaw → exec → pipeline
- Web dashboard (secondary): Flask on port 5000, docker exec bridge on Windows

Full details in the wiki.

---

## Code review guidance

When reviewing or modifying code in this project:

- **`scripts/clip-pipeline.sh`**: The pipeline is bash. Key areas: model unloading between stages (Ollama API calls with `keep_alive=0`), the `call_ollama()` function (handles thinking models, retry logic), Pass C time-bucket distribution logic, and the FFmpeg blur-fill filter chain in Stage 7.

- **`dashboard/app.py`**: The `INSIDE_DOCKER` check determines whether to use `docker exec` or direct subprocess. The SSE endpoint streams from `/tmp/clipper/pipeline.log`. The stage polling thread reads `/tmp/clipper/pipeline_stage.txt`.

- **`config/openclaw.json`**: `compat` flags are required on all Ollama models. `OLLAMA_MAX_LOADED_MODELS=1` must stay set. Context limits are intentional — don't increase them without checking VRAM budget.

- **`workspace/AGENTS.md` and `SKILL.md`**: These are read by the OpenClaw agent as operating instructions. Changes here directly affect bot behavior. Keep messages short, keep tool calls mandatory, keep the style/type inference mapping accurate.

---

## Vault schema

The wiki is an Obsidian vault. Full conventions are in `AIclippingPipelineVault/CLAUDE.md`. Quick reference:

- Pages use YAML frontmatter with `title`, `type`, `tags`, `sources`, `updated`
- Internal links use `[[WikiLinks]]`
- Callouts: `> [!note]`, `> [!warning]`, `> [!todo]`
- Page types: `entity` (specific things), `concept` (ideas/patterns), `source` (raw document summaries), `overview`
- Always update `updated:` date in frontmatter when editing a page
