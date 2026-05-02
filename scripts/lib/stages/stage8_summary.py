"""Stage 8 — Summary (clips_made.txt → summary.json + stdout report).

Extracted from scripts/clip-pipeline.sh as part of the modularization plan
(Phase A5). No env-var dependencies — reads everything from /tmp/clipper/.
"""
import json, os

clips_file = "/tmp/clipper/clips_made.txt"
clips = []
if os.path.exists(clips_file):
    with open(clips_file) as f:
        for line in f:
            parts = line.strip().split("|")
            if len(parts) >= 4:
                try:
                    score_val = float(parts[1])
                except (ValueError, IndexError):
                    score_val = 0.0
                clips.append({
                    "title": parts[0],
                    "score": round(score_val, 3),
                    "category": parts[2],
                    "description": parts[3],
                    "size": parts[4] if len(parts) > 4 else "?",
                    "segment_type": parts[5] if len(parts) > 5 else "?",
                    "duration": parts[6] if len(parts) > 6 else "30s"
                })

cats = {}
segs = {}
for c in clips:
    cat = c.get("category", "unknown")
    cats[cat] = cats.get(cat, 0) + 1
    seg = c.get("segment_type", "unknown")
    segs[seg] = segs.get(seg, 0) + 1

# Load segment map for timeline info
seg_map = []
try:
    with open("/tmp/clipper/segments.json") as f:
        seg_map = json.load(f)
except:
    pass

summary = {
    "status": "complete",
    "clips": len(clips),
    "category_breakdown": cats,
    "segment_breakdown": segs,
    "stream_segments": [{"type": s["type"], "duration_min": round((s["end"]-s["start"])/60)} for s in seg_map],
    "details": clips
}

with open("/tmp/clipper/summary.json", "w") as f:
    json.dump(summary, f, indent=2)

print(json.dumps(summary, indent=2))
