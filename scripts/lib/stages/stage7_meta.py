"""Stage 7 per-clip moment-metadata extractor.

Reads env CLIP_T (clip timestamp). Looks up the matching entry in
scored_moments.json and prints a pipe-separated string used by bash to
populate render flags: mirror_safe|vo_text|vo_placement|group_id|group_kind.
"""
import json, os, sys
T = int(os.environ["CLIP_T"])
try:
    with open("/tmp/clipper/scored_moments.json") as f:
        moments = json.load(f)
except Exception:
    moments = []
target = {}
for m in moments:
    if m.get("timestamp") == T:
        target = m
        break
vo = target.get("voiceover") or {}
kind = target.get("group_kind") or "solo"
gid = target.get("group_id") or ""
print("|".join([
    str(target.get("mirror_safe", False)).lower(),
    (vo.get("text") or "").replace("|", "-").replace("\n", " ").strip(),
    (vo.get("placement") or "intro").strip() or "intro",
    gid,
    kind,
]))
