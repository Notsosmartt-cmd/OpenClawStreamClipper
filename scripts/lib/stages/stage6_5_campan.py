"""Stage 6.5 camera-pan prep (face tracking → per-clip crop path).

Reads env: VOD_PATH, LIB_DIR (defaults /root/scripts/lib).
Reads scored_moments.json, dispatches face_pan.py per non-stitch moment.
Writes <TEMP_DIR>/clip_<t>_campath.json for each successfully tracked clip.
"""
import json, os, subprocess, sys
TEMP_DIR = "/tmp/clipper"
LIB_DIR = os.environ.get("LIB_DIR", "/root/scripts/lib")
VOD_PATH = os.environ.get("VOD_PATH", "")

with open(f"{TEMP_DIR}/scored_moments.json") as f:
    moments = json.load(f)
prepped = 0
for m in moments:
    if m.get("group_kind") == "stitch":
        # Stitch members pan-prep is handled per-member inside stitch_render.py.
        continue
    t = m.get("timestamp")
    start = m.get("clip_start", max(0, t - 15))
    dur = m.get("clip_duration", 30)
    out = f"{TEMP_DIR}/clip_{t}_campath.json"
    rc = subprocess.call([
        "python3", f"{LIB_DIR}/face_pan.py", "prepare",
        "--vod", VOD_PATH,
        "--start", str(start),
        "--duration", str(dur),
        "--out", out,
    ])
    if rc == 0:
        prepped += 1
        print(f"  T={t} campath ok", file=sys.stderr)
    else:
        print(f"  T={t} campath skipped (rc={rc})", file=sys.stderr)
print(f"Camera-pan prep: {prepped}/{len(moments)} clip(s) have face tracks.")
