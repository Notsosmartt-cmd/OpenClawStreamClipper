#!/usr/bin/env python3
"""wiki_lint.py — health check for the OpenClaw Stream Clipper Obsidian wiki.

Stdlib-only (no pip deps). Codifies the ad-hoc audits from
AIclippingPipelineVault/WIKI-IMPROVEMENT-PLAN.md (Phase 4d) so wiki health
becomes a one-liner.

Run:
    python scripts/wiki_lint.py
    python scripts/wiki_lint.py --root path/to/wiki --today 2026-06-12

Checks (exit non-zero if any of classes 1-4 fail; classes 5-6 are warnings):
  1. Broken wikilinks   — [[target]] whose basename has no .md file under the
                          wiki (alias |... and #anchor stripped). Allowlisted
                          historical/forward-ref targets are not flagged.
  2. Orphan pages       — pages (excluding index/log/hot) with zero inbound
                          [[links]].
  3. Index coverage     — both ways: pages on disk not listed in index.md, and
                          entries in index.md pointing at no file.
  4. hot.md line cap    — hot.md (if present) must be <= HOT_LINE_CAP lines.
  5. Stale pages (warn) — frontmatter 'updated:' more than STALE_DAYS days
                          before --today.
  6. Bad anchors (warn) — [[page#heading]] whose heading text is not a heading
                          line in the target file.

Obsidian-style resolution: wikilinks resolve by basename, so [[entities/foo]]
and [[foo]] both point at the file whose stem is 'foo'.
"""

import argparse
import os
import re
import sys
from collections import defaultdict
from datetime import date

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

HOT_LINE_CAP = 100          # check 4: hot.md hard cap
STALE_DAYS = 60             # check 5: warn if 'updated:' older than this
DEFAULT_TODAY = "2026-06-12"

# Pages that legitimately have no inbound links and must not be flagged orphans.
ORPHAN_EXEMPT = {"index", "log", "hot"}

# check 1 allowlist: (source-page-basename, link-target-basename) pairs that are
# known-broken on purpose (historical log records, intentional forward-refs).
# A target is also allowed if it matches any (None, target) wildcard entry.
BROKEN_LINK_ALLOWLIST = {
    # log.md historical references to since-removed scratch files
    ("log", "moment_discovery_upgrade_plan"),
    ("log", "IMPLEMENTATION_PLAN"),
    ("log", "tiktokRes"),
    ("log", "grounding-ab"),
    # tier-4 plan page intentional forward-refs to not-yet-built modules
    ("tier-4-conversation-shape", "conversation-shape-module"),
    ("tier-4-conversation-shape", "model-profile"),
    ("tier-4-conversation-shape", "pattern-catalog"),
    ("tier-4-conversation-shape", "rubric-judge-module"),
}

# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

# [[ target | alias ]] or [[ target#anchor ]] etc. Capture the raw inside.
WIKILINK_RE = re.compile(r"\[\[([^\[\]]+?)\]\]")
HEADING_RE = re.compile(r"^#{1,6}\s+(.*?)\s*#*\s*$")
UPDATED_RE = re.compile(r"^updated:\s*([0-9]{4}-[0-9]{2}-[0-9]{2})", re.MULTILINE)


def basename(path_or_target):
    """Obsidian-style basename: strip dir + .md extension, lowercase-insensitive
    comparison is NOT applied (Obsidian is case-sensitive on disk here)."""
    stem = os.path.splitext(os.path.basename(path_or_target))[0]
    return stem.strip()


def parse_wikilink(raw):
    """Split a raw wikilink body into (target_basename, anchor_or_None).

    Examples:
      'entities/foo'            -> ('foo', None)
      'entities/foo|Foo'        -> ('foo', None)
      'concepts/bar#Heading'    -> ('bar', 'Heading')
      '#Same-File Heading'      -> ('', 'Same-File Heading')   (anchor in same file)
    """
    body = raw.strip()
    # Drop alias first (everything after the first '|').
    if "|" in body:
        body = body.split("|", 1)[0].strip()
    anchor = None
    if "#" in body:
        target_part, anchor = body.split("#", 1)
        anchor = anchor.strip()
    else:
        target_part = body
    target = basename(target_part) if target_part.strip() else ""
    return target, anchor


def load_wiki(root):
    """Return {relpath: text} for every .md file under root (recursive)."""
    files = {}
    for dirpath, _dirs, names in os.walk(root):
        for name in names:
            if name.lower().endswith(".md"):
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, root).replace(os.sep, "/")
                try:
                    with open(full, encoding="utf-8") as fh:
                        files[rel] = fh.read()
                except OSError as exc:  # pragma: no cover - defensive
                    print(f"  [warn] could not read {rel}: {exc}", file=sys.stderr)
    return files


def parse_iso_date(s):
    try:
        y, m, d = (int(x) for x in s.split("-"))
        return date(y, m, d)
    except (ValueError, AttributeError):
        return None


def extract_headings(text):
    """Return list of heading text strings (without leading #'s)."""
    out = []
    for line in text.splitlines():
        m = HEADING_RE.match(line)
        if m:
            out.append(m.group(1).strip())
    return out


def anchor_matches_heading(anchor, headings):
    """Obsidian-tolerant anchor match.

    Exact match, or the anchor is the leading token-run of a heading (handles
    the wiki's 'BUG 51 -> ## BUG 51 - description' convention) at a word
    boundary. Comparison ignores surrounding whitespace and is case-insensitive.
    """
    a = anchor.strip().lower()
    if not a:
        return True
    for h in headings:
        hl = h.strip().lower()
        if hl == a:
            return True
        # prefix at a boundary: heading starts with anchor followed by a
        # non-alphanumeric separator (space, em dash, colon, ...).
        if hl.startswith(a):
            rest = hl[len(a):]
            if not rest or not rest[0].isalnum():
                return True
    return False


# ---------------------------------------------------------------------------
# Index parsing
# ---------------------------------------------------------------------------

def index_listed_basenames(index_text):
    """Every wikilink target basename mentioned in index.md (anchors stripped)."""
    listed = set()
    for m in WIKILINK_RE.finditer(index_text):
        target, _anchor = parse_wikilink(m.group(1))
        if target:
            listed.add(target)
    return listed


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def run_checks(root, today):
    files = load_wiki(root)
    if not files:
        print(f"No .md files found under {root!r}", file=sys.stderr)
        return 2, {}

    have = {basename(p) for p in files}

    # inbound[target_basename] = set of source relpaths linking to it
    inbound = defaultdict(set)
    broken = []          # list of (source_rel, raw_target)
    anchored_links = []  # list of (source_rel, target_basename, anchor)

    for rel, text in files.items():
        src_base = basename(rel)
        for m in WIKILINK_RE.finditer(text):
            target, anchor = parse_wikilink(m.group(1))
            if anchor is not None and target:
                anchored_links.append((rel, target, anchor))
            if not target:
                # same-file anchor link (#Heading) — not a cross-page link.
                continue
            if target in have:
                inbound[target].add(rel)
            else:
                if (src_base, target) in BROKEN_LINK_ALLOWLIST:
                    continue
                broken.append((rel, m.group(1).strip()))

    # ---- check 2: orphans ----
    orphans = []
    for rel in sorted(files):
        b = basename(rel)
        if b in ORPHAN_EXEMPT:
            continue
        if not inbound.get(b):
            orphans.append(rel)

    # ---- check 3: index coverage ----
    index_rel = next((r for r in files if basename(r) == "index"), None)
    on_disk_not_in_index = []
    in_index_not_on_disk = []
    if index_rel is not None:
        listed = index_listed_basenames(files[index_rel])
        # 'index' / 'log' / 'hot' are hubs, not catalog entries — don't require
        # them to list themselves.
        catalog_disk = {
            basename(r) for r in files if basename(r) not in ("index", "log", "hot")
        }
        on_disk_not_in_index = sorted(catalog_disk - listed)
        in_index_not_on_disk = sorted(listed - have)
    else:
        print("  [warn] no index.md found — skipping coverage check", file=sys.stderr)

    # ---- check 4: hot.md cap ----
    hot_rel = next((r for r in files if basename(r) == "hot"), None)
    hot_lines = None
    hot_over = False
    if hot_rel is not None:
        hot_lines = len(files[hot_rel].splitlines())
        hot_over = hot_lines > HOT_LINE_CAP

    # ---- check 5: stale pages (warning) ----
    stale = []  # (rel, updated_str, age_days)
    today_d = parse_iso_date(today) or parse_iso_date(DEFAULT_TODAY)
    for rel, text in sorted(files.items()):
        if basename(rel) in ("log", "hot"):
            continue
        m = UPDATED_RE.search(text)
        if not m:
            continue
        d = parse_iso_date(m.group(1))
        if d is None:
            continue
        age = (today_d - d).days
        if age > STALE_DAYS:
            stale.append((rel, m.group(1), age))

    # ---- check 6: bad anchors (warning) ----
    headings_cache = {}
    bad_anchors = []  # (source_rel, target_basename, anchor)
    # map basename -> relpath for resolving anchor targets
    base_to_rel = {basename(r): r for r in files}
    for src_rel, target, anchor in anchored_links:
        tgt_rel = base_to_rel.get(target)
        if tgt_rel is None:
            # broken page link already reported by check 1; skip anchor check.
            continue
        if tgt_rel not in headings_cache:
            headings_cache[tgt_rel] = extract_headings(files[tgt_rel])
        if not anchor_matches_heading(anchor, headings_cache[tgt_rel]):
            bad_anchors.append((src_rel, target, anchor))

    results = {
        "files": files,
        "broken": broken,
        "orphans": orphans,
        "on_disk_not_in_index": on_disk_not_in_index,
        "in_index_not_on_disk": in_index_not_on_disk,
        "hot_rel": hot_rel,
        "hot_lines": hot_lines,
        "hot_over": hot_over,
        "stale": stale,
        "bad_anchors": bad_anchors,
    }

    # classes 1-4 are hard failures
    fail = bool(
        broken
        or orphans
        or on_disk_not_in_index
        or in_index_not_on_disk
        or hot_over
    )
    return (1 if fail else 0), results


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def section(title):
    print()
    print(title)
    print("-" * len(title))


def report(root, today, code, r):
    print("=" * 64)
    print("  wiki_lint — health check")
    print(f"  root:  {root}")
    print(f"  today: {today}")
    print(f"  pages: {len(r.get('files', {}))}")
    print("=" * 64)

    # --- 1. broken wikilinks ---
    broken = r["broken"]
    section(f"1. Broken wikilinks  [{len(broken)}]  (FAIL)")
    if broken:
        for src, raw in broken:
            print(f"  {src}: [[{raw}]]")
    else:
        print("  none")

    # --- 2. orphans ---
    orphans = r["orphans"]
    section(f"2. Orphan pages  [{len(orphans)}]  (FAIL)")
    if orphans:
        for o in orphans:
            print(f"  {o}")
    else:
        print("  none")

    # --- 3. index coverage ---
    odni = r["on_disk_not_in_index"]
    iind = r["in_index_not_on_disk"]
    section(f"3. Index coverage  [disk-not-in-index={len(odni)} index-not-on-disk={len(iind)}]  (FAIL)")
    if odni:
        print("  on disk but missing from index.md:")
        for x in odni:
            print(f"    {x}")
    if iind:
        print("  listed in index.md but no file on disk:")
        for x in iind:
            print(f"    {x}")
    if not odni and not iind:
        print("  none")

    # --- 4. hot.md cap ---
    section(f"4. hot.md line cap (<= {HOT_LINE_CAP})  (FAIL)")
    if r["hot_rel"] is None:
        print("  hot.md not present — skipped")
    elif r["hot_over"]:
        print(f"  OVER CAP: {r['hot_rel']} has {r['hot_lines']} lines (> {HOT_LINE_CAP})")
    else:
        print(f"  ok: {r['hot_rel']} has {r['hot_lines']} lines")

    # --- 5. stale pages (warning) ---
    stale = r["stale"]
    section(f"5. Stale pages (updated > {STALE_DAYS}d before {today})  [{len(stale)}]  (warning)")
    if stale:
        for rel, upd, age in sorted(stale, key=lambda t: -t[2]):
            print(f"  {rel}  (updated {upd}, {age}d old)")
    else:
        print("  none")

    # --- 6. bad anchors (warning) ---
    bad = r["bad_anchors"]
    section(f"6. Anchored links to missing headings  [{len(bad)}]  (warning)")
    if bad:
        for src, tgt, anc in bad:
            print(f"  {src}: [[{tgt}#{anc}]]")
    else:
        print("  none")

    # --- summary ---
    section("Summary")
    print(f"  broken links            : {len(broken)}")
    print(f"  orphan pages            : {len(orphans)}")
    print(f"  disk-not-in-index       : {len(odni)}")
    print(f"  index-not-on-disk       : {len(iind)}")
    hot_state = (
        "n/a" if r["hot_rel"] is None
        else ("OVER" if r["hot_over"] else f"{r['hot_lines']}/{HOT_LINE_CAP}")
    )
    print(f"  hot.md cap              : {hot_state}")
    print(f"  stale pages (warn)      : {len(stale)}")
    print(f"  bad anchors (warn)      : {len(bad)}")
    print()
    if code == 0:
        print("  RESULT: PASS (classes 1-4 clean)")
    else:
        print("  RESULT: FAIL (one or more of classes 1-4 has issues)")
    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def default_root():
    # scripts/wiki_lint.py -> ../AIclippingPipelineVault/wiki
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(here, "..", "AIclippingPipelineVault", "wiki"))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Health-check the wiki.")
    ap.add_argument("--root", default=default_root(),
                    help="wiki root directory (default: ../AIclippingPipelineVault/wiki)")
    ap.add_argument("--today", default=DEFAULT_TODAY,
                    help=f"reference date YYYY-MM-DD for staleness (default {DEFAULT_TODAY})")
    args = ap.parse_args(argv)

    root = os.path.abspath(args.root)
    if not os.path.isdir(root):
        print(f"error: wiki root not found: {root}", file=sys.stderr)
        return 2

    code, results = run_checks(root, args.today)
    report(root, args.today, code, results)
    return code


if __name__ == "__main__":
    sys.exit(main())
