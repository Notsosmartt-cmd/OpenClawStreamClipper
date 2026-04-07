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
│   ├── log.md                  ← append-only chronological record
│   ├── overview.md             ← high-level synthesis of the whole project
│   ├── entities/               ← pages for specific things: models, tools, services
│   ├── concepts/               ← pages for ideas, patterns, techniques, stages
│   └── sources/                ← one summary page per ingested raw source
```

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

1. Read `wiki/index.md` and all pages linked from it
2. Report:
   - Contradictions between pages
   - Stale claims (if newer sources supersede them)
   - Orphan pages (no inbound links)
   - Important concepts mentioned but lacking their own page
   - Missing cross-references between related pages
   - Data gaps worth investigating (flag with `> [!todo]`)
3. Fix what you can, flag what needs a new source
4. Append to `wiki/log.md`:
   ```
   ## [2026-04-07] lint | Health check
   Issues found/fixed: ...
   ```

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
