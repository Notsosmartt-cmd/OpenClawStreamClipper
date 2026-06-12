# Wiki Improvement Plan — density, references, hot.md, and search

> **STATUS: EXECUTED 2026-06-12** (see `wiki/log.md` → "Wiki maintenance overhaul"). Retained as
> the design record. Delivered via an 8-agent writer workflow + a 5-agent adversarial-verification
> workflow + orchestrator-owned edits to the shared files (index/log/both CLAUDE.mds).
>
> **Divergences from the plan as written** (recon-driven, documented in the log):
> - **3a (bugs-and-fixes split)** — done *in place* (completed the quick-nav, refreshed status) rather
>   than split into archive files: ~20 anchored deep-links (`#BUG NN`/`#REMOVAL`, several in the
>   append-only `log.md`) would have broken. All 69 `## BUG`/`## REMOVAL` headings kept byte-identical.
> - **1d (lmstudio rename)** — replaced with mutual "not to be confused with" disambiguation notes on
>   `lm-studio.md`/`lmstudio.md`. A rename would have broken 4 append-only `log.md` links for cosmetic gain.
> - **3b (log rotation)** — convention documented; no archive created (all 162 entries are 2026-Q2).
> - **4e (concepts/ folder split)** — considered and rejected (recorded), as the plan recommended.
> - **Bonus**: adversarial verification surfaced a model-swap staleness tail the plan didn't anticipate
>   (qwen35/gemma4/model-split/text-comparison still showed the pre-2026-06-12 split as live) — all fixed.

This plan is **self-contained** — it embeds all the audit data it relies on, so you do
not need the conversation that produced it. Re-verify anything that looks stale with the
commands in §Verification before acting; the wiki changes daily.

**Scope guard:** this plan touches ONLY `AIclippingPipelineVault/` (wiki pages, the vault
`CLAUDE.md`, and optionally the root project `CLAUDE.md` wiki-procedure section). It must
not change any pipeline/dashboard/config code. `raw/` is immutable — never modify it.

---

## Audit summary (measured 2026-06-12)

Overall the wiki is healthy — this is a tune-up, not a rebuild:

- 82 content pages (28 entities, 49 concepts, 4 sources, overview) + index + log
- **0 orphan pages** — every page has ≥1 inbound wikilink
- **100% index coverage** — every page on disk is in `index.md`; no ghost entries
- `log.md`: 162 entries, 1,528 lines, append-only, current through 2026-06-07
- Completed plans are properly tombstoned (e.g. `modularization-plan` carries a
  "shipped 2026-05-01" callout; `chrome-masking` is tombstoned)

The problems are concentrated, not systemic:

| # | Problem | Evidence |
|---|---|---|
| 1 | Two live pages link to the **deleted** `entities/grounding-ab` page | `concepts/moment-discovery-upgrades.md:103`, `concepts/clip-quality-remediation-2026-06.md:163` |
| 2 | `index.md:80` claims "62 bugs … latest BUG 62" | registry actually holds **BUG 64** |
| 3 | Twin slugs `entities/lm-studio` (server) vs `entities/lmstudio` (Python client) | one hyphen apart; 18 vs 5 inbound links |
| 4 | `overview.md` leads with the legacy Docker architecture | bare-metal Windows has been the default since 2026-06-04; project-files table (lines ~134-149) lists `clip-pipeline.sh`/`docker-compose.yml` as current |
| 5 | ~8 dangling forward-reference wikilinks + 1 pseudocode false-positive | itemized in Phase 1 |
| 6 | `concepts/bugs-and-fixes.md` is 1,738 lines / 24,486 words / 86 headings | single biggest density problem |
| 7 | `log.md` entries are dense multi-hundred-word paragraphs; no fast "current state" view | the whole motivation for `hot.md` |
| 8 | Several **load-bearing** pages have ~2-month-stale `updated:` dates | `entities/ffmpeg` (2026-04-07!), `concepts/segment-detection` (2026-04-07), `concepts/open-questions` (2026-04-07), `concepts/context-management` (2026-04-17), `entities/discord-bot`, `entities/openclaw` (2026-04-17) — all during a period of heavy churn |
| 9 | Under-referenced pages (few wikilinks per 1,000 words) | worst: `entities/bootstrap-twitch-clips` (716 w/link), `concepts/transition-animations` (431), `concepts/pipeline-optimizations-2026-06` (320), `concepts/vram-budget` (298), `concepts/style-profiles` (267) |
| 10 | No automated health check | every issue above was found by ad-hoc scripting; nothing prevents recurrence |

Phases are independent unless noted — they can be executed in separate sessions.
Recommended order: **1 → 2 → 3 → 4** (cheap hygiene first, then the hot.md deliverable,
then density, then conventions/tooling that lock the gains in).

---

## Phase 1 — Reference hygiene (~20 min, zero risk)

### 1a. Remove the two stale `grounding-ab` links
`entities/grounding-ab.md` was deliberately deleted 2026-05-01 when the grounding cascade
collapsed to two tiers (see log.md entry "REMOVAL 2026-05-01b"). Fix the two live references:

- `concepts/moment-discovery-upgrades.md:103` — line reads `- [[entities/grounding-ab]] — A3 validation harness`. Replace with plain text: `- ~~grounding-ab~~ — A3 validation harness, **removed 2026-05-01** with the MiniCheck/Lynx retirement (see [[concepts/bugs-and-fixes]] REMOVAL 2026-05-01b)`.
- `concepts/clip-quality-remediation-2026-06.md:163` — reads `[[entities/grounding]] / [[entities/grounding-ab]] — …`. Drop the second link, keep `[[entities/grounding]]`.

Do NOT touch the mentions in `bugs-and-fixes.md` and `log.md` — those are historical
records of the deletion and are correct as-is.

### 1b. Fix the stale index line
`index.md:80`: update "62 bugs documented (latest: BUG 62 …, BUG 61 …)" to the actual
current count/latest (BUG 64 white-flash regression, BUG 63 stitch never fired — re-check
the registry head before writing; more bugs may exist by execution time). Better: make the
line count-free so it can't go stale again — e.g. `Bug registry + removals, quick-nav by
category; check §Status summary for the latest`.

### 1c. Resolve the remaining dangling links (decide per link)
Audit found these wikilink targets with no file. Dispositions:

| Link | Found in | Disposition |
|---|---|---|
| `[[entities/pipeline-orchestrator]]` | `concepts/observability.md` | **Create the page.** `scripts/run_pipeline.py` is the post-bare-metal-port orchestrator and genuinely deserves an entity page (stages, flags, `--vods`/`--all`/`--force` semantics, persistent-log slugs). This is the one dangling link that is a real gap, not noise. |
| `[[entities/conversation-shape-module]]`, `[[entities/model-profile]]`, `[[entities/pattern-catalog]]`, `[[entities/rubric-judge-module]]` | `concepts/tier-4-conversation-shape.md` | Forward references to not-yet-built modules in a plan page. **Keep but annotate** at first occurrence: `*(page to be created when the module ships)*` so readers know they're intentional. |
| `[[entities/dockerfile]]` | `concepts/originality-stack.md`, `entities/librosa.md` | **De-link** (plain text "the Dockerfile"). The Dockerfile is legacy since the bare-metal port; it will never get a page. |
| `[[sources/tiktok-originality-2026]]` | `concepts/originality-stack.md` | **De-link.** The raw source was never ingested (`raw/` is empty save .gitkeep) and no source page exists. Plain-text the citation. |
| `[[[box, (text, conf)]]` | `concepts/bugs-and-fixes.md` | False positive: pseudocode being parsed as a wikilink. Wrap the snippet in backticks so Obsidian stops linking it. |
| `[[../moment_discovery_upgrade_plan]]`, `[[IMPLEMENTATION_PLAN]]`, `[[raw/tiktokRes]]` | `log.md` | **Leave.** Historical log entries referencing since-removed scratch files; rewriting the append-only log is forbidden. |

### 1d. Disambiguate the lm-studio twins
`entities/lm-studio.md` = the LM Studio **server** (318 lines, 18 inbound links — leave it).
`entities/lmstudio.md` = `scripts/lib/lmstudio.py`, the minimal **HTTP client module**
(70 lines, 5 inbound links). Rename the client page to `entities/lmstudio-client.md` and
update its 5 inbound linkers: `concepts/bugs-and-fixes.md`, `entities/grounding.md`,
`index.md`, `log.md`, `sources/implementation-plan.md`. Exception: in `log.md`, only
update if the link is plain (historical text may keep the old name — use judgment; a
`[[entities/lmstudio-client|lmstudio]]` alias preserves the historical reading).
Also add a one-line "not to be confused with" note at the top of each page pointing at
the other.

**Acceptance (1a-1d):** the link checker in §Verification reports 0 broken links outside
`log.md` and `tier-4-conversation-shape.md` (whose remaining ones are annotated
forward-refs), and 0 orphans.

---

## Phase 2 — Create `wiki/hot.md` (the explicit user request)

### Why
`log.md` is append-only history — correct, but 1,528 lines of dense paragraphs. An agent
starting a session needs "what is true *right now* and what changed *lately*" without
scanning it. `index.md` is a catalog, not a state snapshot. `overview.md` is architecture,
updated rarely. Nothing currently answers: *what's in flight, what shipped this week,
what's awaiting validation, what will bite me.* `hot.md` is that page.

### Contract (this is the important part — write these rules INTO the file header)
1. **Bounded:** hard cap **~100 lines**. When adding, remove. It never grows monotonically.
   It is a *cache*, not a record — `log.md` remains the record.
2. **Mutable:** unlike `log.md`, entries are rewritten/dropped freely. No append-only rule.
3. **Updated in the same breath as `log.md`:** any session that prepends a `log.md` entry
   also refreshes `hot.md` (add the one-liner, prune anything stale/older than ~2 weeks,
   update the state table if defaults/flags/models changed).
4. **Everything links:** every line should carry a `[[wikilink]]` to the page with depth.
   hot.md holds pointers + one-line claims, never the full story.

### Template (create `wiki/hot.md` with exactly this skeleton, filled from the latest log entries at execution time)

```markdown
---
title: "Hot — current state & recent activity"
type: overview
tags: [hot, hub, status]
updated: <today>
---

# Hot

Bounded current-state digest (cap ~100 lines — when you add, prune).
Rewritten freely; [[log]] is the append-only record, this is the cache.
Update this whenever you prepend a log entry.

## State snapshot
| Fact | Value | Since | Detail |
|---|---|---|---|
| Default architecture | bare-metal Windows (Python orchestrator) | 2026-06-04 | [[concepts/bare-metal-windows]] |
| text_model | qwen/qwen3.5-9b | … | [[entities/qwen35]] |
| vision_model | google/gemma-4-12b | … | [[entities/gemma4]] |
| Latest bug | BUG 64 (white-flash regression, fixed) | 2026-06-07 | [[concepts/bugs-and-fixes]] |
| … keep this table ≤ ~12 rows of genuinely load-bearing facts … |

## In flight / awaiting validation
- <feature shipped but not yet validated on a real run> — [[page]]
  (seed: transition animations shipped 2026-06-06 flag-gated, BUG 64 fixed 06-07 —
   needs a clean validation run; arc-stitch + stitch-short reworked under BUG 63)

## Recent changes (last ~10, one line each, newest first)
- [2026-06-07] BUG 64 white-flash painted clips white — fixed (drawbox transient) — [[concepts/transition-animations]]
- [2026-06-07] grouping `--explain` dry-run + narrative logging — [[concepts/originality-stack]]
- … mirror the log.md headline, ~15 words, with the date and one link …

## Landmines (top gotchas for the next agent)
- <max ~6 bullets of currently-active traps, each linking to the bug/page>
  (seed candidates: FFmpeg `fade` holds colour outside its window (BUG 64);
   stitch needs 3 same-category eligibles ≤28s (BUG 63 qualification);
   `config/originality.json` is untracked runtime state)
```

Populate "Recent changes" from the newest 10 `log.md` headers (`grep "^## \[" wiki/log.md | head -10`),
the state table from `overview.md` + `config/models.json`, and "In flight" by scanning the
last ~15 log entries for "needs a validation run" / "awaiting" / "deferred" phrasing —
that phrase appears constantly in recent entries and is exactly the signal hot.md exists
to surface.

### Wire it into the conventions (required, or it will rot)
1. `wiki/index.md` — add `[[hot]]` at the very top of the Overview section: *"start here
   for current state, then [[overview]] for architecture"*.
2. `AIclippingPipelineVault/CLAUDE.md` (vault schema) — add `hot.md` to the directory-layout
   diagram and add one workflow rule: **"Every log.md prepend is accompanied by a hot.md
   refresh (add one-liner, prune >2-week-old lines, keep ≤100 lines)."**
3. Root `G:\OpenClawStreamClipper\CLAUDE.md` — in "How to update the wiki", extend step 3
   (the log.md step) with the hot.md refresh. Keep it one sentence.
4. `wiki/log.md` — log the creation as an `update` entry.

**Acceptance:** `hot.md` exists, ≤100 lines, every section populated with real current
data, both CLAUDE.mds mention the refresh rule, index links it first.

---

## Phase 3 — Density remediation

### 3a. Split `concepts/bugs-and-fixes.md` (1,738 lines → hub + archive)
The page already has the right skeleton: a `## Status summary`, a `## Quick-nav index`
(by category: Infrastructure/Docker, Dashboard, LLM/Model Integration, Pipeline/Rendering,
Grounding/Hallucination), then 64 full bug entries + 2 REMOVAL records, newest-first.

Restructure:
- Keep `concepts/bugs-and-fixes.md` as the **hub**: status summary + quick-nav + a
  complete one-line-per-bug catalog (`BUG NN — symptom (fixed YYYY-MM-DD) → [[archive-page]]`)
  + **full entries for only the ~10 most recent bugs** (the ones agents actually hit).
- Move older full entries to numbered archive pages: `concepts/bugs-archive-01-25.md`,
  `concepts/bugs-archive-26-50.md`, `concepts/bugs-archive-51-up.md` (numeric ranges, not
  categories — bug numbers are stable, categories drift; the quick-nav already covers
  topical lookup). Newest bugs graduate into the archive as new ones arrive.
- **Anchor hazard:** other pages deep-link with heading anchors, e.g.
  `[[concepts/bugs-and-fixes#REMOVAL 2026-05-01b]]` (`entities/grounding.md`) and many
  plain `[[concepts/bugs-and-fixes]]` + "BUG NN" textual references. Before moving any
  entry: `grep -rn "bugs-and-fixes#" wiki/ --include=*.md` and retarget every anchored
  link to the archive page that now holds the heading. Plain page links can stay (the
  hub's catalog redirects the reader).
- Update `index.md` with the archive pages; log the operation.

**Acceptance:** hub ≤ ~500 lines; every `BUG NN` and `REMOVAL` reachable in ≤2 hops from
the hub; `grep -rn "bugs-and-fixes#" wiki/` shows no anchor pointing at a heading that no
longer exists in that file.

### 3b. Rotate `log.md` by quarter (optional but recommended)
Keep `log.md` = current quarter only; move older entries verbatim (no edits — append-only
is sacred *within* an archive too) to `wiki/log-2026-Q1.md`, `wiki/log-2026-Q2.md` as
needed. Add a header line in `log.md` pointing at the archives. Preserve the grep
contract: archives keep the `## [YYYY-MM-DD]` format so
`grep -h "^## \[" wiki/log*.md` still scans full history. Update the vault `CLAUDE.md`
log-format section with the rotation rule (rotate when the active log exceeds ~600 lines).
With hot.md in place (Phase 2), the cost of a shorter active log is near zero.

### 3c. Refresh `overview.md` to lead with the current architecture
Restructure so bare-metal Windows is the headline (it has been the default since
2026-06-04) and Docker is the explicitly-labeled legacy section. Update the project-files
table: `scripts/run_pipeline.py` + `scripts/pipeline/stages/` + `dashboard/app.py` native
mode are current; `clip-pipeline.sh` / `docker-compose.yml` / `Dockerfile` move to a
"legacy (pre-2026-06-04)" sub-table. Also re-verify the model table and the
"Two text models" design-decision bullet against `config/models.json` — the qwen2.5
Discord-model claim looked dated even at audit time (index.md already notes "current
setup uses same LM Studio model for agent and pipeline").

### 3d. Refresh the stale load-bearing pages
These have `updated:` dates from early April during a period when the system changed
radically (bare-metal port, WhisperX, NVENC, CapCut captions, model swaps). Re-read each
against current code and either refresh or stamp them with a "still accurate as of
<date>" note (silence is indistinguishable from staleness — the stamp matters even when
nothing changed):
- `entities/ffmpeg.md` (2026-04-07) — predates NVENC/`venc.py`, blur-fill changes, transitions
- `concepts/segment-detection.md` (2026-04-07) — predates the `CLIP_SEGMENT_CHUNK` knob (Fix 1)
- `concepts/open-questions.md` (2026-04-07) — many questions likely answered since; prune/refile
- `concepts/context-management.md`, `entities/discord-bot.md`, `entities/openclaw.md` (2026-04-17)
- Leave retired/tombstoned pages alone (`entities/ollama.md`, `concepts/deployment.md`,
  `concepts/chrome-masking.md`, `entities/chrome-mask-module.md`) — they're correct as graves.

### 3e. Raise reference density on the worst offenders (cheap, do alongside 3d)
Pages with the fewest wikilinks per word (audit top 5): `entities/bootstrap-twitch-clips`
(716 words, 1 link), `concepts/transition-animations` (862 w, 2 links — should link
BUG 63/64, [[concepts/clip-rendering]], [[entities/dashboard]]),
`concepts/pipeline-optimizations-2026-06`, `concepts/vram-budget`,
`concepts/style-profiles`. Add the obvious links where related pages are *named in prose
but not linked*. Don't force it — a link per claimed relationship, not per noun.

---

## Phase 4 — Indexing & search upgrades for future agents

### 4a. `status:` frontmatter on plan-type pages
The wiki holds ~12 plan/roadmap pages in various lifecycle states, distinguishable today
only by reading body callouts. Add a frontmatter field to *plan-type pages only*:
`status: planned | in-progress | shipped | superseded | retired`. Seed values:
`modularization-plan: shipped`, `detection-improvements-plan: shipped`,
`chrome-masking: retired`, `tier-4-conversation-shape: planned`, the five
`plan-*` axis pages: check each body, `clipping-quality-overhaul: in-progress`, etc.
This makes lifecycle grep-able: `grep -rl "^status: planned" wiki/concepts/`.
Document the field in the vault `CLAUDE.md` page-conventions section. Mirror the status
in `index.md` as a suffix on those entries (e.g. `— **shipped 2026-05-01**`), which the
index already does informally for some.

### 4b. Document the grep contracts
Add a short "Searching this wiki" section to the vault `CLAUDE.md` (and a 3-line
condensed version at the top of `index.md`) listing the canonical lookups:
- Recent activity: `grep "^## \[" wiki/log*.md | head -20`
- A specific bug: `grep -rn "^## BUG 47" wiki/concepts/bugs*.md`
- Lifecycle: `grep -rl "^status: in-progress" wiki/`
- Current state: read `wiki/hot.md` (after Phase 2)
- Topic → page: read `wiki/index.md` section headers first, never raw-grep the whole vault first
This is cheap and directly improves how future agents query the wiki.

### 4c. Naming conventions (document, don't retrofit)
Write into the vault `CLAUDE.md`: dated research snapshots end `-YYYY-MM`
(existing: `vlm-comparison-2026-06` etc.); plan pages start `plan-` or end `-plan`;
case studies start `case-`; archives start `bugs-archive-` / `log-`. Retrofitting old
names is not worth the link churn — convention applies to new pages.

### 4d. `scripts/wiki_lint.py` — automate the audit
Everything in this plan's audit was found by ad-hoc scripts; codify them so health checks
become a one-liner. A stdlib-only script (no deps) that reports:
1. Broken wikilinks (target basename has no file) — with an allowlist for `log.md`
   historical refs and annotated forward-refs
2. Orphan pages (no inbound links, excluding index/log/hot)
3. Index coverage both ways (on disk ∉ index; in index ∉ disk)
4. `hot.md` over the 100-line cap
5. Pages whose `updated:` is >60 days old (warning, not error)
6. Anchored links (`[[page#heading]]`) whose heading no longer exists in the target
Exit non-zero on classes 1-4. Run it manually or via the existing `lint` wiki workflow
(the vault CLAUDE.md already defines a lint operation — reference the script from there).
Place it in `scripts/` (it's project tooling, which makes it a *code* change — so that
session must follow the full wiki-update + commit mandate, and the script itself gets a
mention in [[concepts/observability]] or a small entity page).

### 4e. Folder split for `concepts/` — **recommend AGAINST, record the decision**
49 files in one folder looks dense, but the index already groups them (Pipeline / System
/ Reference), all links are path-qualified (`[[concepts/x]]`), and a split would rewrite
hundreds of links across log.md-untouchable history for purely cosmetic gain. With 4a's
`status:` field + the index groupings, the flat folder is fine. Record this as a
considered-and-rejected decision in the log entry so the question stops being re-asked.

---

## Verification

Re-run after each phase. Link/orphan/coverage checker (stdlib only, run from `wiki/`):

```bash
python - <<'EOF'
import os, re, glob
from collections import defaultdict
files = {p: open(p, encoding='utf-8').read() for p in glob.glob('**/*.md', recursive=True)}
base = lambda p: os.path.splitext(os.path.basename(p))[0]
have = {base(p) for p in files}
inbound, broken = defaultdict(set), []
for p, t in files.items():
    for m in re.finditer(r'\[\[([^\]|#]+)', t):
        tb = base(m.group(1).strip())
        (inbound[tb].add(p) if tb in have else broken.append((p, m.group(1))))
print("ORPHANS:", [p for p in files if base(p) not in ('index','log','hot') and not inbound[base(p)]])
print("BROKEN:", *broken or ['none'], sep='\n  ')
EOF
```

Quick checks: index↔disk coverage (compare `ls`-derived basenames against `[[...]]` in
index.md); bug-count freshness (`grep -oE "BUG [0-9]+" concepts/bugs-and-fixes.md | sort
-Vu | tail -1` vs the index line); hot.md cap (`wc -l wiki/hot.md` ≤ ~100).

## Ground rules for the executing agent

- `log.md` content is append-only: never edit existing entries (rotation moves them verbatim).
- Tombstoned/retired pages are correct as graves — refresh their *links*, not their verdicts.
- Every phase ends with: affected pages' `updated:` bumped, `index.md` reconciled,
  a `wiki/log.md` entry prepended (and from Phase 2 on: `hot.md` refreshed), and a git
  commit per the project mandate.
- If execution reveals this plan is stale (e.g. someone already created hot.md, or
  bugs-and-fixes was already split), trust the wiki over the plan, do the still-valid
  remainder, and note the divergence in the log entry.
