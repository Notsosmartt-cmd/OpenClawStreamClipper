"""SRT timestamp rescaler (helper for Stage 7 caption speed-matching).

Extracted from scripts/clip-pipeline.sh as part of Phase A6.
Args: <src_srt> <dst_srt> <speed_factor>
Divides every SRT timestamp by speed_factor so subtitles stay in sync
when the clip is sped up via setpts in the render.
"""
import sys, re
src, dst, speed = sys.argv[1], sys.argv[2], float(sys.argv[3])
def scale_ts(m):
    h, mn, s, ms = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
    total_ms = (h * 3600 + mn * 60 + s) * 1000 + ms
    new_ms = int(round(total_ms / speed))
    h2, r = divmod(new_ms, 3600000)
    mn2, r = divmod(r, 60000)
    s2, ms2 = divmod(r, 1000)
    return f"{h2:02d}:{mn2:02d}:{s2:02d},{ms2:03d}"
pat = re.compile(r'(\d{2}):(\d{2}):(\d{2}),(\d{3})')
with open(src) as f:
    content = f.read()
with open(dst, "w") as f:
    f.write(pat.sub(scale_ts, content))
