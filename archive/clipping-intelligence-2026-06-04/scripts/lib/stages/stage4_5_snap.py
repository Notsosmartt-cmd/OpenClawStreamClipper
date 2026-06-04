"""Stage 4.5 boundary snap (Phase 4.2 sentence + silence gap snapping).

Args: <transcript_path> <moments_path>
Mutates moments.json in place: snaps each clip's start/end to the nearest
sentence boundary and silence gap so Stage 7 doesn't render mid-word.
Graceful no-op if boundary_detect module is unavailable or disabled.
"""
import json, sys
sys.path.insert(0, "/root/scripts/lib")
try:
    import boundary_detect as bd
except Exception as e:
    print(f"[BOUNDARY] module import failed ({e}); skipping snap", file=sys.stderr)
    sys.exit(0)

transcript_path, moments_path = sys.argv[1], sys.argv[2]
try:
    with open(moments_path) as f:
        moments = json.load(f)
except Exception as e:
    print(f"[BOUNDARY] failed to load moments: {e}", file=sys.stderr)
    sys.exit(0)

cfg = bd.load_boundaries_config()
if not cfg.get("enabled", True):
    print("[BOUNDARY] disabled in config; snap skipped", file=sys.stderr)
    sys.exit(0)

moved = bd.snap_moments_in_place(moments, transcript_path, cfg)
print(f"[BOUNDARY] snapped {moved}/{len(moments)} moments to sentence+silence boundaries", file=sys.stderr)
for m in moments:
    if m.get("boundary_snapped"):
        ds, de = m.get("boundary_drift_s") or (0, 0)
        print(
            f"  T={m.get('timestamp')} {m.get('boundary_source')}: "
            f"start drift={ds:+.2f}s end drift={de:+.2f}s -> "
            f"dur={m.get('clip_duration')}s",
            file=sys.stderr,
        )

with open(moments_path, "w") as f:
    json.dump(moments, f, indent=2)
