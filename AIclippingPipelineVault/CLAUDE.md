# OpenClaw Stream Clipper — Wiki Schema

This is the schema file for the LLM-maintained wiki inside this Obsidian vault. Read this before any wiki operation. It defines the structure, conventions, and workflows you follow when maintaining knowledge about this project.

---

## What this wiki is for

This wiki is a persistent, compounding knowledge base about the **OpenClaw Stream Clipper** — a Docker-based system that automatically clips stream highlights using local AI models (Whisper, Qwen3-VL, Qwen 3.5) and delivers them via Discord. It also serves as a research base for topics that inform the project: model performance, video processing, AI tooling, deployment patterns, and streaming workflows.

The wiki is the artifact. It accumulates knowledge across conversations. You never discard it — you maintain it.

---

## Directory layout

```
AIclippingPipelineVault/
├── CLAUDE.md                   ← this file (schema, read first)
├── raw/                        ← immutable source documents (you READ, never modify)
│   └── assets/                 ← images downloaded from articles
├── wiki/
│   ├── index.md                ← content catalog (update on every ingest)
│   ├── hot.md                  ← bounded (~100-line) current-state digest; refreshed every log prepend
│   ├── log.md                  ← append-only chronological record (rotate by quarter when >~600 lines)
│   ├── overview.md             ← high-level synthesis of the whole project
│   ├── entities/               ← pages for specific things: models, tools, services
│   ├── concepts/               ← pages for ideas, patterns, techniques, stages
│   └── sources/                ← one summary page per ingested raw source
```

> [!note] `hot.md` vs `log.md`
> `log.md` is the **append-only record** (never edit past entries). `hot.md` is a **mutable cache** of the current state — a hard ~100-line cap, rewritten freely. Any session that prepends a `log.md` entry **also refreshes `hot.md`** (add the one-liner, prune anything stale or older than ~2 weeks, update the state table if defaults/flags/models changed). See `wiki/hot.md` for the contract.

**Rule:** You own `wiki/` entirely — create, update, reorganize freely. You never modify files in `raw/`.

---

## Page conventions

Every wiki page starts with YAML frontmatter:

```yaml
---
title: "Page Title"
type: entity | concept | source | overview
tags: [tag1, tag2]
sources: 1          # number of raw sources that informed this page
updated: 2026-04-07
---
```

- **entity** — a specific named thing: a model, tool, container, service, person, or file
- **concept** — an idea, technique, pattern, stage, or category
- **source** — a summary of one raw document from `raw/`
- **overview** — the top-level synthesis page

**`status:` field (plan-type pages only).** Pages that describe planned/forward-looking work carry a lifecycle field in frontmatter so it's grep-able: `status: planned | in-progress | shipped | superseded | retired`. Mirror the value in the page's `index.md` entry (e.g. `— **shipped 2026-05-01**`). Query with `grep -rl "^status: in-progress" wiki/`.

**Naming conventions (new pages).** Dated research snapshots end `-YYYY-MM` (e.g. `vlm-comparison-2026-06`); plan pages start `plan-` or end `-plan`; case studies start `case-`; tombstones keep their original name with a `> [!warning] removed/retired` callout at top. Don't retrofit old names — convention applies going forward.

Use `[[WikiLinks]]` to link between pages. This keeps the Obsidian graph view meaningful.

Use `> [!note]` callouts for important caveats, `> [!warning]` for known bugs or gotchas, `> [!todo]` for gaps that need a source or deeper investigation.

---

## Workflows

### Ingest

When the user drops a new file in `raw/` and asks you to process it:

1. Read the source thoroughly
2. Discuss key takeaways with the user (what's new, what's surprising, what contradicts existing wiki pages)
3. Create a summary page in `wiki/sources/` named after the source file
4. Update `wiki/overview.md` if the source changes the big picture
5. Update or create relevant entity and concept pages — a single source may touch 5–15 pages
6. Update `wiki/index.md` — add the new source page, update any entity/concept entries that changed
7. Append an entry to `wiki/log.md`:
   ```
   ## [2026-04-07] ingest | Source Title
   Summary of what was learned. Pages created/updated: [[page1]], [[page2]].
   ```

### Query

When the user asks a question:

1. Read `wiki/index.md` to identify relevant pages
2. Read those pages
3. Synthesize an answer with `[[links]]` to cited pages
4. If the answer is non-trivial and worth keeping, offer to file it as a new wiki page
5. If filed, append to `wiki/log.md`:
   ```
   ## [2026-04-07] query | Question summary
   Answer filed as [[wiki/concepts/new-page]].
   ```

### Lint

When the user asks you to health-check the wiki:

1. Run `python scripts/wiki_lint.py` first — it mechanically reports broken wikilinks, orphan pages, index-coverage gaps both ways, `hot.md` over its line cap, stale `updated:` dates, and dead heading anchors. Fix what it flags.
2. Then read `wiki/index.md` and the pages linked from it for the judgement calls a script can't make:
   - Contradictions between pages
   - Stale claims (if newer sources supersede them)
   - Important concepts mentioned but lacking their own page
   - Missing cross-references between related pages
   - Data gaps worth investigating (flag with `> [!todo]`)
3. Fix what you can, flag what needs a new source
4. Append to `wiki/log.md`:
   ```
   ## [2026-04-07] lint | Health check
   Issues found/fixed: ...
   ```

### Searching this wiki (canonical lookups)

- **Current state**: read `wiki/hot.md` (then `wiki/overview.md` for architecture).
- **Recent activity**: `grep "^## \[" wiki/log.md | head -20`
- **A specific bug**: `grep -n "^## BUG 47" wiki/concepts/bugs-and-fixes.md` (or use its top quick-nav index)
- **Plan lifecycle**: `grep -rl "^status: in-progress" wiki/concepts/`
- **Topic → page**: read `wiki/index.md` section headers first — don't raw-grep the whole vault.
- **Health check**: `python scripts/wiki_lint.py`

---

## Domain-specific conventions

This wiki covers the stream clipper domain. Key categories:

- **Models**: Whisper large-v3-turbo, Qwen3-VL 8B, Qwen 3.5 9B — each gets an entity page
- **Pipeline stages**: Discovery → Transcription → Frame Extraction → Vision Analysis → Editing → Delivery → Logging — each stage gets a concept page or section
- **Infrastructure**: Docker, Ollama, OpenClaw, FFmpeg, Discord — entity pages
- **Performance data**: GPU vs CPU timings, VRAM budgets — track in entity pages and a concepts/performance.md page
- **Known issues / bugs**: Use `> [!warning]` callouts; add to entity pages where they occur
- **Research questions**: Tag pages with `research` when they represent open questions

When a source contradicts an existing claim, note it explicitly:
```
> [!warning] Contradicts [[other-page]]
> Source X says Y, but [[other-page]] claims Z. Needs resolution.
```

---

## Index format

`wiki/index.md` is organized by category. Each entry: `- [[page]] — one-line description`

Example:
```markdown
## Entities
- [[entities/ollama]] — local LLM inference server; serves Qwen3-VL and Qwen 3.5
- [[entities/openclaw]] — agent framework and Discord gateway

## Concepts
- [[concepts/clipping-pipeline]] — the 7-stage highlight extraction workflow
```

Keep descriptions under 100 characters. Update on every ingest.

---

## Log format

`wiki/log.md` is append-only. Prepend new entries (newest at top). Format:

```
## [YYYY-MM-DD] operation | Title
Brief description. Pages touched: [[page1]], [[page2]].
```

Operations: `ingest`, `query`, `lint`, `update`

The `## [` prefix makes log entries grep-able: `grep "^## \[" wiki/log.md | head -10`

**Every log prepend also refreshes `wiki/hot.md`** (see the directory-layout note above).

**Rotation.** When the active `log.md` exceeds ~600 lines, move older entries *verbatim* (append-only is sacred even in archives) to `wiki/log-YYYY-Qn.md`, keeping only the current quarter in `log.md`. Archives keep the `## [YYYY-MM-DD]` format so `grep -h "^## \[" wiki/log*.md` still scans full history. (As of 2026-06-12 all entries are 2026-Q2, so no archive exists yet despite the length.)
