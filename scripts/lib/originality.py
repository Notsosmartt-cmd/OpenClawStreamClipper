#!/usr/bin/env python3
"""Generate per-clip randomized render parameters for TikTok originality defense.

Called from Stage 7 of the pipeline, once per clip. Emits shell variable
assignments on stdout so the bash caller can `eval` them and interpolate the
values into its FFmpeg filter graph.

The goal is to make every rendered clip structurally different at the pixel
level: jittered blur, occasional horizontal flip, a randomized color/eq stack,
and rotating palettes for the hook card and subtitle style. Deterministic per
clip (seeded from the moment timestamp) so re-rendering the same clip yields
the same look, but distinct from every other clip in the batch.

Input (argv):
    1: timestamp (int seconds) — seed source
    2: originality flag (`true`/`false`)
    3: mirror_safe flag (`true`/`false`) from vision enrichment
    4: framing mode (`blur_fill`/`smart_crop`/`centered_square`/`camera_pan`)
    5: clip category (hype/funny/emotional/...)

Output: shell `KEY=VALUE` lines suitable for `eval`.
"""
import sys
import random
import hashlib


def main() -> int:
    if len(sys.argv) < 6:
        print("# usage: originality.py <ts> <orig> <mirror_safe> <framing> <category>",
              file=sys.stderr)
        return 2

    ts = sys.argv[1]
    originality = sys.argv[2].lower() == "true"
    mirror_safe = sys.argv[3].lower() == "true"
    framing = sys.argv[4]
    category = sys.argv[5]

    seed = int(hashlib.md5(f"oc:{ts}:{framing}:{category}".encode()).hexdigest()[:12], 16)
    rng = random.Random(seed)

    if originality:
        blur_radius = rng.randint(18, 32)
        blur_passes = rng.randint(3, 6)
        # Mirror roughly 45% of the time when vision says it's safe.
        # Research calls out "mirror or horizontally flip longer segments of
        # the source" as one of the strongest fingerprint-breakers.
        mirror = mirror_safe and rng.random() < 0.45
        # Subtle color/eq stack — kept well inside the natural look envelope
        # so clips still feel like the source, not a filter preset.
        eq_brightness = round(rng.uniform(-0.03, 0.05), 3)
        eq_saturation = round(rng.uniform(0.92, 1.18), 3)
        eq_contrast = round(rng.uniform(0.95, 1.15), 3)
        eq_gamma = round(rng.uniform(0.93, 1.08), 3)
        # Hue shift in degrees — small, per-clip distinct
        hue_shift = round(rng.uniform(-6.0, 6.0), 2)
        use_vignette = rng.random() < 0.30
        # Tiny rebound/shake: micro-motion that breaks per-frame match
        use_shake = rng.random() < 0.35
        shake_amp = round(rng.uniform(1.2, 2.5), 2) if use_shake else 0.0

        hook_palettes = [
            {"box": "white@0.92",     "fg": "black", "border_w": 22},
            {"box": "black@0.88",     "fg": "white", "border_w": 20},
            {"box": "0xFFE85D@0.95",  "fg": "black", "border_w": 24},
            {"box": "0xFF3B6B@0.92",  "fg": "white", "border_w": 22},
            {"box": "0x00D9A7@0.90",  "fg": "black", "border_w": 20},
            {"box": "0xFFFFFF@0.96",  "fg": "0x111111", "border_w": 26},
        ]
        hook_pal = rng.choice(hook_palettes)
        hook_y = rng.randint(45, 130)
        hook_fontsize = rng.randint(36, 46)

        sub_palettes = [
            {"primary": "&H00FFFFFF", "outline": "&H00000000", "outline_w": 2, "font_size": 11, "margin_v": 40},
            {"primary": "&H00FFFFFF", "outline": "&H00000000", "outline_w": 3, "font_size": 12, "margin_v": 55},
            {"primary": "&H0022F3FF", "outline": "&H00000000", "outline_w": 2, "font_size": 11, "margin_v": 45},
            {"primary": "&H00FFFFFF", "outline": "&H002A2A2A", "outline_w": 3, "font_size": 13, "margin_v": 52},
            {"primary": "&H005DFFFF", "outline": "&H00000000", "outline_w": 2, "font_size": 12, "margin_v": 48},
        ]
        sub_pal = rng.choice(sub_palettes)

        # Transition style (used when rendering stitch groups)
        transitions = ["fade", "wiperight", "slideup", "circlecrop", "distance"]
        transition = rng.choice(transitions)
    else:
        # Legacy deterministic look — identical to pre-originality behavior
        blur_radius = 25
        blur_passes = 5
        mirror = False
        eq_brightness = 0.0
        eq_saturation = 1.0
        eq_contrast = 1.0
        eq_gamma = 1.0
        hue_shift = 0.0
        use_vignette = False
        use_shake = False
        shake_amp = 0.0
        hook_pal = {"box": "white@0.92", "fg": "black", "border_w": 22}
        hook_y = 55
        hook_fontsize = 40
        sub_pal = {"primary": "&H00FFFFFF", "outline": "&H00000000",
                   "outline_w": 2, "font_size": 11, "margin_v": 40}
        transition = "fade"

    # Emit shell assignments — quote strings carefully. Values are all
    # numeric or ASCII-safe palette names so bash word-splitting is fine.
    out = []
    out.append(f"BLUR_RADIUS={blur_radius}")
    out.append(f"BLUR_PASSES={blur_passes}")
    out.append(f"MIRROR={'true' if mirror else 'false'}")
    out.append(f"EQ_BRIGHTNESS={eq_brightness}")
    out.append(f"EQ_SATURATION={eq_saturation}")
    out.append(f"EQ_CONTRAST={eq_contrast}")
    out.append(f"EQ_GAMMA={eq_gamma}")
    out.append(f"HUE_SHIFT={hue_shift}")
    out.append(f"USE_VIGNETTE={'true' if use_vignette else 'false'}")
    out.append(f"USE_SHAKE={'true' if use_shake else 'false'}")
    out.append(f"SHAKE_AMP={shake_amp}")
    out.append(f"HOOK_BOX_COLOR='{hook_pal['box']}'")
    out.append(f"HOOK_FG_COLOR='{hook_pal['fg']}'")
    out.append(f"HOOK_BOX_BORDER={hook_pal['border_w']}")
    out.append(f"HOOK_Y={hook_y}")
    out.append(f"HOOK_FONTSIZE={hook_fontsize}")
    out.append(f"SUB_PRIMARY='{sub_pal['primary']}'")
    out.append(f"SUB_OUTLINE_COL='{sub_pal['outline']}'")
    out.append(f"SUB_OUTLINE={sub_pal['outline_w']}")
    out.append(f"SUB_FONTSIZE={sub_pal['font_size']}")
    out.append(f"SUB_MARGIN_V={sub_pal['margin_v']}")
    out.append(f"TRANSITION={transition}")
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
