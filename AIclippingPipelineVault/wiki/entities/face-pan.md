---
title: "face-pan (OpenCV camera tracker)"
type: entity
tags: [opencv, face-tracking, camera-pan, originality, wave-e, module, stage-6, vision]
sources: 0
updated: 2026-04-22
---

# face-pan helper

CPU-only active-speaker tracker that produces per-clip virtual-camera crop paths for the `camera_pan` framing mode in [[concepts/originality-stack]] wave E. Lives at `scripts/lib/face_pan.py`.

### Detection

OpenCV Haar cascade (`haarcascade_frontalface_default.xml` — ships with `opencv-python-headless`). Chosen over DNN / MTCNN because it's zero-config: the cascade XML is bundled, no model download, no ONNX runtime.

Samples the clip window at 2 fps; at each sample runs `detectMultiScale(scaleFactor=1.2, minNeighbors=4, minSize=(60,60))`.

### Target selection

- 1 face: track it.
- 2+ faces: pick the one closest to the previous center for continuity, **except** on a ~4 s cycle it rotates to the next-largest face. This is the reality-TV swing between speakers that breaks per-frame visual hashing.
- 0 faces: hold the previous smoothed position (or fall back if zero faces across the entire clip).

Diarization is **not required**. If you want true active-speaker detection later, replace `pick_target_face()` with a TalkNet-style lip-motion + audio-coherence check — the rest of the pipeline is unchanged.

### Camera path

- Crop target: 608×1080 (9:16 slice at source resolution), then scaled to 1080×1920 in the render.
- Smoothing: exponential moving average, α = 0.30.
- Clamped to frame bounds so the crop never leaves the source.

Output: `/tmp/clipper/clip_<T>_campath.json` with keyframes `[{t, x, y, w, h}, ...]`.

### Render filter

`face_pan.py --emit-filter <campath.json>` builds a nested-`if()` FFmpeg expression encoding up to 32 resampled anchors:

```
crop=w=608:h=1080:x='if(lt(t,0.50), 100+...)':y='...',scale=1080:1920:flags=lanczos
```

Stage 7's `camera_pan` case splices this in place of the blur-fill filter graph.

### Fallback ladder

| Condition | Behavior |
|---|---|
| Source portrait already | rc=2, skip |
| Haar cascade not found | rc=1, skip |
| 0 faces detected across clip | rc=3, skip |
| Otherwise | emit path; stage 7 uses it |

Any skip causes the Stage 7 `camera_pan` case to fall back to `blur_fill` for that clip silently.

### Cost

- Detection: 2–4 s CPU per clip.
- Render: ~1–2 s extra for the per-frame crop expression.
- Memory: minimal; OpenCV reads one frame at a time.

### Related
- [[concepts/originality-stack]] — wave E
- [[concepts/clip-rendering]] — `camera_pan` framing mode
- [[concepts/clipping-pipeline]] — Stage 6.5 (camera-pan prep)
