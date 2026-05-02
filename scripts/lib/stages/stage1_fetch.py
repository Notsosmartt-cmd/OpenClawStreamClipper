"""Stage 1 chat auto-fetch config reader.

Reads /root/.openclaw/chat.json (or $CLIP_CHAT_CONFIG) to decide whether
auto-fetch is enabled and extract the Twitch VOD ID from the filename.
Args: <vod_basename> <chat_out_path>
Prints either "SKIP <reason>" or "FETCH <vod_id> <client_id> <delay_ms>".
"""
import json, os, re, sys
from pathlib import Path

vod_name = sys.argv[1]
out_path = sys.argv[2]
cfg_path = Path(os.environ.get("CLIP_CHAT_CONFIG", "/root/.openclaw/chat.json"))
if not cfg_path.exists():
    print("SKIP no-config")
    sys.exit(0)
try:
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
except Exception as e:
    print(f"SKIP config-parse-error:{e}")
    sys.exit(0)

af = cfg.get("auto_fetch") or {}
if not af.get("enabled"):
    print("SKIP auto_fetch-disabled")
    sys.exit(0)
pattern = af.get("vod_id_pattern") or ""
try:
    rx = re.compile(pattern) if pattern else None
except re.error:
    print("SKIP bad-pattern")
    sys.exit(0)
vid = None
if rx:
    m = rx.search(vod_name)
    if m:
        for g in m.groups():
            if g and g.isdigit():
                vid = g
                break
if not vid:
    print("SKIP no-vod-id-in-filename")
    sys.exit(0)

client_id = af.get("twitch_client_id") or "kimne78kx3ncx6brgo4mv6wki5h1ko"
delay = int(af.get("request_delay_ms") or 200)
print(f"FETCH {vid} {client_id} {delay}")
