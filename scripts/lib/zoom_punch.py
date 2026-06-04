#!/usr/bin/env python3
"""Build an FFmpeg filter graph fragment that overlays a zoomed copy at
specific timestamps.

Approach: split the video, scale the second copy up by `scale`, crop it back
to the original WxH, and overlay it on the original ONLY during the zoom
windows via FFmpeg's `enable=` expression. This avoids the famously fiddly
`zoompan` filter and keeps the effect cheap (one extra scale + crop).

Caller passes a list of zoom punches (each {t, scale, hold}) and the
expected output WxH. Returns:
    - filter graph fragment (str, or "" if no punches)
    - a label name for the output (so the caller can chain)

Usage from the renderer:
    pre = "[base]"            # whatever the prior fragment outputs to
    fragment, out_label = build_zoom_fragment(in_label="base", zoom_punches=...)
    full_chain = pre + fragment + ...   # downstream filters use [out_label]
"""
from __future__ import annotations


def _enable_expr(windows: list[tuple[float, float]]) -> str:
    """Build a + chain of between(t,a,b) terms, suitable for `enable=`."""
    parts = [f"between(t,{a:.3f},{b:.3f})" for a, b in windows if b > a]
    return "+".join(parts) if parts else "0"


def build_zoom_fragment(in_label: str, out_label: str, zoom_punches: list[dict],
                        out_w: int = 1080, out_h: int = 1920) -> tuple[str, str]:
    """Return (filter_fragment, out_label). Empty string when no punches.

    Note: The fragment is meant to be appended to a `filter_complex` graph
    where the input is already labelled `[in_label]`. The output is labelled
    `[out_label]`.
    """
    if not zoom_punches:
        return "", in_label

    # Coalesce into (start, end) windows, drop near-zero holds.
    windows: list[tuple[float, float]] = []
    for p in zoom_punches:
        t = float(p.get("t", 0.0))
        hold = float(p.get("hold", 0.30))
        if hold < 0.05:
            continue
        windows.append((t, t + hold))
    if not windows:
        return "", in_label

    # Single-scale path: pick the first scale as the visual punch amount;
    # zoom punches are short and roughly uniform so this is fine.
    scale = float(zoom_punches[0].get("scale", 1.15))
    scale = max(1.02, min(scale, 1.40))

    enable = _enable_expr(windows)
    # crop=W:H:(iw-W)/2:(ih-H)/2 keeps the zoomed copy centered on the
    # original output canvas — overlay then sits at 0,0.
    fragment = (
        f"[{in_label}]split=2[zb_base][zb_zsrc];"
        f"[zb_zsrc]scale=iw*{scale:.4f}:ih*{scale:.4f},"
        f"crop={out_w}:{out_h}:(iw-{out_w})/2:(ih-{out_h})/2[zb_zoomed];"
        f"[zb_base][zb_zoomed]overlay=x=0:y=0:enable='{enable}'[{out_label}]"
    )
    return fragment, out_label


def _cli() -> int:
    import argparse, json, sys
    ap = argparse.ArgumentParser()
    ap.add_argument("--punches", required=True,
                    help="JSON list of zoom punches")
    ap.add_argument("--in-label", default="base")
    ap.add_argument("--out-label", default="zoomed")
    ap.add_argument("--w", type=int, default=1080)
    ap.add_argument("--h", type=int, default=1920)
    args = ap.parse_args()
    punches = json.loads(args.punches)
    frag, lab = build_zoom_fragment(args.in_label, args.out_label, punches, args.w, args.h)
    print(json.dumps({"fragment": frag, "out_label": lab}))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_cli())
