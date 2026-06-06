#!/usr/bin/env python3
"""Group adjacent moments into stitch or narrative arcs (Wave C).

Reads ``/tmp/clipper/hype_moments.json`` (output of Pass C), produces
``/tmp/clipper/moment_groups.json``, and writes a patched copy of
``hype_moments.json`` with ``group_id`` and ``group_kind`` fields added to
each moment.

Group kinds:

- ``solo``: unchanged — a single moment rendered as one clip (default).
- ``narrative``: two or more moments in the same story arc (storytime /
  emotional / hot_take) within ~2 minutes of each other. Merged into one
  continuous long clip (up to 90 s). The merged clip window spans from the
  earliest ``clip_start`` to the latest ``clip_end``.
- ``stitch``: 3-4 short moments sharing a category that are rendered as
  sub-segments (each < 30 s) and concatenated into one post.

Which groupings are produced is controlled by CLI flags:

    --stitch true|false      enable stitch synthesis
    --narrative true|false   enable narrative merging

When both flags are false this script is a no-op (every moment stays solo)
apart from writing a moment_groups.json with only solo entries for Stage 7.
"""
import argparse
import json
import os
import re
import sys
import uuid
from pathlib import Path

TEMP_DIR = Path(os.environ.get("CLIP_TEMP_DIR", "/tmp/clipper"))

NARRATIVE_CATEGORIES = {"storytime", "emotional", "hot_take"}
NARRATIVE_MAX_GAP_SEC = 120
NARRATIVE_MAX_DURATION = 90
STITCH_MIN_MEMBERS = 3
STITCH_MAX_MEMBERS = 4
STITCH_MAX_MEMBER_DUR = 12  # short sub-segments
STITCH_TOTAL_TARGET = 28    # aim for ~28 s total (under the <30 s research bar)
STITCHABLE_CATEGORIES = {"funny", "hype", "reactive", "dancing"}

# Fix 3 (2026-06-06): arc/callback moments carry a far-earlier setup_time; render
# them as a 2-member stitch (short SETUP snippet -> PAYOFF) so the setup->payoff
# arc lands visually instead of the setup only living in the caption.
ARC_STITCH_CATEGORIES = {"arc", "callback"}
ARC_SETUP_SNIPPET = 8     # seconds of the setup to show before the payoff
ARC_PAYOFF_MAX = 30       # cap the payoff segment length
ARC_MIN_GAP = 10          # setup must be >= this many seconds before the payoff window


def new_group_id() -> str:
    return "g_" + uuid.uuid4().hex[:8]


def build_narrative_groups(moments: list[dict]) -> list[dict]:
    """Adjacent storytime/emotional/hot_take moments become one long clip."""
    groups: list[dict] = []
    used: set[int] = set()
    # Sort by timestamp so "adjacent" really means adjacent in time
    ordered = sorted(moments, key=lambda m: m.get("timestamp", 0))

    for i, m in enumerate(ordered):
        if i in used:
            continue
        if m.get("category") not in NARRATIVE_CATEGORIES:
            continue
        # Extend as long as the next moment is same-family and close in time
        members = [m]
        member_indices = [i]
        last_end = m.get("clip_end", m.get("timestamp", 0) + 30)
        for j in range(i + 1, len(ordered)):
            if j in used:
                continue
            n = ordered[j]
            if n.get("category") not in NARRATIVE_CATEGORIES:
                continue
            gap = n.get("clip_start", n["timestamp"]) - last_end
            if gap < 0:
                gap = 0
            if gap > NARRATIVE_MAX_GAP_SEC:
                break
            projected_start = members[0].get("clip_start", members[0]["timestamp"])
            projected_end = n.get("clip_end", n["timestamp"] + 30)
            if projected_end - projected_start > NARRATIVE_MAX_DURATION:
                break
            members.append(n)
            member_indices.append(j)
            last_end = projected_end

        if len(members) < 2:
            continue

        gid = new_group_id()
        start = members[0].get("clip_start", members[0]["timestamp"])
        end = members[-1].get("clip_end", members[-1]["timestamp"] + 30)
        duration = min(NARRATIVE_MAX_DURATION, max(45, end - start))
        groups.append({
            "group_id": gid,
            "kind": "narrative",
            "category": members[0].get("category"),
            "segment_type": members[0].get("segment_type"),
            "start": start,
            "end": start + duration,
            "duration": duration,
            "members": [
                {"timestamp": x["timestamp"], "start": x.get("clip_start"),
                 "end": x.get("clip_end"), "role": "beat"}
                for x in members
            ],
            "score": round(max(m.get("score", 0) for m in members), 3),
        })
        used.update(member_indices)

    return groups


def build_stitch_groups(moments: list[dict], enabled: bool) -> list[dict]:
    """Pick 3-4 short same-category moments and bundle them as one stitch post."""
    if not enabled:
        return []

    groups: list[dict] = []
    by_cat: dict[str, list[dict]] = {}
    for m in moments:
        if m.get("group_id"):
            continue  # already claimed by narrative
        cat = m.get("category", "")
        if cat not in STITCHABLE_CATEGORIES:
            continue
        dur = m.get("clip_duration", 30)
        if dur > STITCH_MAX_MEMBER_DUR * 2:
            continue
        by_cat.setdefault(cat, []).append(m)

    for cat, pool in by_cat.items():
        if len(pool) < STITCH_MIN_MEMBERS:
            continue
        # Rank by score descending, pick top N under the total budget
        pool.sort(key=lambda m: m.get("score", 0), reverse=True)
        chosen: list[dict] = []
        total = 0.0
        for m in pool:
            member_dur = min(STITCH_MAX_MEMBER_DUR, m.get("clip_duration", 10))
            if total + member_dur > STITCH_TOTAL_TARGET + 4:
                continue
            chosen.append(m)
            total += member_dur
            if len(chosen) >= STITCH_MAX_MEMBERS:
                break
        if len(chosen) < STITCH_MIN_MEMBERS:
            continue

        gid = new_group_id()
        groups.append({
            "group_id": gid,
            "kind": "stitch",
            "category": cat,
            "segment_type": chosen[0].get("segment_type"),
            "total_duration": round(total, 1),
            "members": [
                {
                    "timestamp": m["timestamp"],
                    "start": m.get("clip_start"),
                    "end": m.get("clip_start", m["timestamp"])
                           + min(STITCH_MAX_MEMBER_DUR, m.get("clip_duration", 10)),
                    "duration": min(STITCH_MAX_MEMBER_DUR, m.get("clip_duration", 10)),
                    "role": "beat",
                    "hook": m.get("hook") or m.get("why", "")[:60],
                }
                for m in chosen
            ],
            "score": round(sum(m.get("score", 0) for m in chosen) / len(chosen), 3),
        })

    return groups


def build_arc_stitch_groups(moments: list[dict], enabled: bool) -> list[dict]:
    """Fix 3: each A1 arc / M3 callback moment becomes a 2-member stitch — a
    short SETUP snippet (around its far-earlier ``setup_time``) jump-cut to the
    PAYOFF (its normal clip window) — so the viewer sees both halves of the arc.

    Both members carry the PAYOFF moment's ``timestamp`` so ``stitch_render``
    resolves them to the same moment in ``scored_moments.json``; each member's
    ``start``/``duration`` override the moment's own window (the renderer prefers
    ``member["start"]``). Skips moments whose setup is too close to (or inside)
    the payoff window — those already show the setup in a single clip."""
    if not enabled:
        return []
    groups: list[dict] = []
    for m in moments:
        if m.get("group_id"):
            continue  # already claimed by narrative/stitch
        if m.get("category") not in ARC_STITCH_CATEGORIES:
            continue
        setup_t = m.get("setup_time")
        payoff_t = m.get("timestamp")
        if setup_t is None or payoff_t is None:
            continue
        setup_t, payoff_t = int(setup_t), int(payoff_t)
        payoff_start = int(m.get("clip_start", payoff_t - 12))
        # Setup must be genuinely earlier than the payoff window, else a single
        # clip already contains it.
        if setup_t >= payoff_start - ARC_MIN_GAP:
            continue
        payoff_dur = min(ARC_PAYOFF_MAX, int(m.get("clip_duration", 25)) or 25)
        setup_start = max(0, setup_t - 2)
        setup_text = (m.get("setup_text") or m.get("why") or "").strip()
        # Strip the Pass-B "Pattern <id>:" debug prefix so the on-screen caption
        # is clean (arcs fall back to `why`, which carries that prefix).
        setup_text = re.sub(r"^\s*Pattern\s+[A-Za-z0-9_]+\s*:\s*", "", setup_text, flags=re.IGNORECASE)
        setup_hook = ("Earlier: " + setup_text[:48]) if setup_text else "Earlier..."
        gid = new_group_id()
        groups.append({
            "group_id": gid,
            "kind": "stitch",
            "category": m.get("category"),
            "segment_type": m.get("segment_type"),
            "arc_kind": m.get("arc_kind"),
            "total_duration": ARC_SETUP_SNIPPET + payoff_dur,
            "members": [
                {
                    "timestamp": payoff_t,  # resolves to this moment in scored_moments
                    "start": setup_start,
                    "end": setup_start + ARC_SETUP_SNIPPET,
                    "duration": ARC_SETUP_SNIPPET,
                    "role": "setup",
                    "hook": setup_hook,
                },
                {
                    "timestamp": payoff_t,
                    "start": payoff_start,
                    "end": payoff_start + payoff_dur,
                    "duration": payoff_dur,
                    "role": "payoff",
                    "hook": m.get("hook") or m.get("why", "")[:60],
                },
            ],
            "score": round(m.get("score", 0), 3),
        })
    return groups


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stitch", default="false")
    parser.add_argument("--narrative", default="true")
    parser.add_argument("--arc-stitch", dest="arc_stitch", default="false")
    parser.add_argument("--moments", default=str(TEMP_DIR / "hype_moments.json"))
    parser.add_argument("--out", default=str(TEMP_DIR / "moment_groups.json"))
    args = parser.parse_args()

    stitch_enabled = args.stitch.lower() == "true"
    narrative_enabled = args.narrative.lower() == "true"
    arc_stitch_enabled = args.arc_stitch.lower() == "true"

    src = Path(args.moments)
    if not src.is_file():
        print(f"moments file missing: {src}", file=sys.stderr)
        return 1
    moments = json.loads(src.read_text(encoding="utf-8"))

    # Narrative first (claims whole arcs), then stitch on the remainder.
    narrative = build_narrative_groups(moments) if narrative_enabled else []
    for g in narrative:
        for mem in g["members"]:
            for m in moments:
                if m.get("timestamp") == mem["timestamp"]:
                    m["group_id"] = g["group_id"]
                    m["group_kind"] = "narrative"
                    # For narrative, stretch the primary member's clip window
                    if m is moments[0] or m["timestamp"] == g["members"][0]["timestamp"]:
                        m["clip_start"] = g["start"]
                        m["clip_end"] = g["end"]
                        m["clip_duration"] = g["duration"]

    stitch = build_stitch_groups(moments, stitch_enabled)
    for g in stitch:
        for mem in g["members"]:
            for m in moments:
                if m.get("timestamp") == mem["timestamp"]:
                    m["group_id"] = g["group_id"]
                    m["group_kind"] = "stitch"

    # Fix 3: arc/callback setup->payoff stitches, on the remainder.
    arc_stitch = build_arc_stitch_groups(moments, arc_stitch_enabled)
    for g in arc_stitch:
        for m in moments:
            if m.get("timestamp") == g["members"][0]["timestamp"] and not m.get("group_id"):
                m["group_id"] = g["group_id"]
                m["group_kind"] = "stitch"

    # Any unclassified moment stays solo.
    for m in moments:
        m.setdefault("group_id", "")
        m.setdefault("group_kind", "solo")

    groups = narrative + stitch + arc_stitch
    # Emit every moment as well so stitch_render can look up members.
    out = {
        "groups": groups,
        "moments": moments,
        "summary": {
            "total_moments": len(moments),
            "narrative_groups": len(narrative),
            "stitch_groups": len(stitch),
            "arc_stitch_groups": len(arc_stitch),
            "solo_moments": sum(1 for m in moments if m.get("group_kind") == "solo"),
        },
    }

    Path(args.out).write_text(json.dumps(out, indent=2), encoding="utf-8")
    # Rewrite hype_moments.json with group fields merged in
    src.write_text(json.dumps(moments, indent=2), encoding="utf-8")

    # For stitch groups, only the first (highest-scored) member stays in the
    # rendering manifest so Stage 7 doesn't emit a solo render for every
    # member. Stage 7's stitch pass then renders the whole group once.
    print(json.dumps(out["summary"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
