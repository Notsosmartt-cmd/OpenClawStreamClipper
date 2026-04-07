# OpenClaw Stream Clipper — Knowledge Base

This Obsidian vault is an LLM-maintained wiki for the OpenClaw Stream Clipper project.

## Start here

- [[wiki/overview]] — What the system is and how it works
- [[wiki/index]] — Full content catalog
- [[CLAUDE.md]] — Schema: how this wiki is structured and maintained

## How this works

The wiki is written and maintained by Claude Code (or any Claude agent). You source documents, ask questions, and direct the analysis. The LLM handles all the cross-referencing, summarizing, and filing.

**To add a new source**: drop a file in `raw/`, then tell Claude "ingest [filename]".

**To ask a question**: ask Claude directly — it will read relevant wiki pages and synthesize an answer. Good answers get filed back as new wiki pages.

**To health-check the wiki**: tell Claude "lint the wiki" — it will find contradictions, orphan pages, and gaps.

## Structure

```
raw/          ← your source documents (immutable — Claude reads, never modifies)
wiki/         ← Claude-maintained knowledge base
  index.md    ← content catalog
  log.md      ← history of all ingests and queries
  overview.md ← high-level synthesis
  entities/   ← models, tools, services
  concepts/   ← pipeline stages, patterns, techniques
  sources/    ← one summary per ingested document
CLAUDE.md     ← schema and workflow instructions for Claude
```
