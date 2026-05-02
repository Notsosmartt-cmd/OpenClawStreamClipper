---
title: "Docker Image Slimming & Asset Externalization"
type: concept
tags: [docker, image-size, caching, volumes, deployment, transparency, infrastructure]
sources: 0
updated: 2026-04-22
---

# Docker Image Slimming

Design decisions that keep the `stream-clipper` image lean and transparent. Changed April 2026 in response to user feedback that the previous image felt like a black box: users couldn't see what was baked in, couldn't easily swap models, and couldn't tell why image pulls were ~8 GB.

---

## What moved OUT of the image

| Artifact | Old location | New location | Size saved |
|---|---|---|---|
| Whisper large-v3 weights | baked at `/root/.cache/whisper-models` | **mounted** from host `./models/whisper/` | ~3 GB |
| Piper voice `en_US-amy-low` | baked at `/root/.cache/piper/` | **mounted** from host `./models/piper/` | ~20 MB |
| Python requirements list | hardcoded in `Dockerfile` | **`requirements.txt` + `requirements-originality.txt`** | 0 (visibility gain) |
| Originality extras (librosa, opencv, piper-tts) | always installed | **conditional** on `ORIGINALITY_STACK` build arg | ~350 MB when slim |

Net effect: **full build dropped from ~8.5 GB to ~5.5 GB** (~35% reduction). The `slim` build drops another ~350 MB.

The image still contains everything it needs to run — it just no longer carries artifacts that are better kept on the host where they can be inspected, replaced, or backed up.

---

## Host folder layout

```
./models/
├── README.md              ← explains the cache to users
├── whisper/               → mounted at /root/.cache/whisper-models
│   └── models--Systran--faster-whisper-large-v3/…
└── piper/                 → mounted at /root/.cache/piper
    ├── en_US-amy-low.onnx
    └── en_US-amy-low.onnx.json

./music/                   → mounted at /root/music (wave-D music bed)
└── README.md
```

Both `models/whisper/` and `models/piper/` start empty. First pipeline run populates `whisper/` automatically (faster-whisper lazy-downloads when the model isn't cached). Piper voices are fetched on demand through the dashboard or `scripts/lib/fetch_assets.py`.

---

## Build arguments

```bash
# Default — full originality stack
docker compose build

# Slim build — no Piper/librosa/opencv
docker compose build --build-arg ORIGINALITY_STACK=slim
```

When the stack is `slim`, the originality helpers fail gracefully at runtime:

- `piper_vo.py` — returns rc=1 with "piper CLI not found and piper-tts package not installed"; voiceover layer is skipped
- `scan_music.py` / tier-C `music_pick.py` — libroса import errors; falls back to tier-A folder convention
- `face_pan.py` — OpenCV import check at startup; `camera_pan` framing falls back to `blur_fill`

This means users can run the lightest possible image and still have Waves A + B + C fully functional.

---

## Asset Cache panel (dashboard)

New panel under **Folder Settings** shows what's currently cached on disk and lets the user fetch Whisper models or Piper voices without shelling into the container.

- `GET /api/assets/status` → wraps `scripts/lib/fetch_assets.py status`. Returns per-model and per-voice sizes + total.
- `POST /api/assets/fetch` → wraps `fetch_assets.py whisper <model>` or `fetch_assets.py piper <voice>`. Timeouts: 30 min for Whisper (large models over slow links), 5 min for Piper (~20 MB).

`fetch_assets.py` handles Piper downloads two ways: `python -m piper.download_voices` first, falling back to direct Hugging Face `wget` against the `v1.0.0` tag when the packaged downloader is unavailable.

---

## .dockerignore additions

Build context now excludes:

- `models/`, `music/` — big, mounted at runtime
- `AIclippingPipelineVault/` — the wiki (large, not needed in the image)
- `Dockerfile.ollama*` — legacy
- `__pycache__/`, `*.pyc`, `.pytest_cache/` — Python build artifacts
- `node_modules/` — OpenClaw is installed globally

Before: `docker build` transferred the entire repo (including wiki + any local models) to the daemon. After: only the source tree the image actually needs.

---

## Runtime mounts (docker-compose.yml)

```yaml
volumes:
  - ./config:/root/.openclaw
  - ./workspace:/root/.openclaw/workspace
  - ./vods:/root/VODs
  - ./clips:/root/VODs/Clips_Ready
  - ./scripts:/root/scripts          # live edits to pipeline + helpers
  - ./dashboard:/root/dashboard      # live edits to UI
  - ./models/whisper:/root/.cache/whisper-models
  - ./models/piper:/root/.cache/piper
  - ./music:/root/music
```

Because `./scripts` is mounted, editing any of the `scripts/lib/*.py` helpers or `scripts/clip-pipeline.sh` takes effect on the next pipeline invocation **without rebuilding**. The Dockerfile still `COPY`s them for users who invoke the image with plain `docker run` without mounts.

---

## When to rebuild vs. restart

| Change | Action |
|---|---|
| Edit `scripts/*.sh` or `scripts/lib/*.py` | *nothing* — mounted, next run picks it up |
| Edit `dashboard/*` | *nothing* — mounted, refresh browser |
| Edit `requirements*.txt` | `docker compose build` |
| Change `ORIGINALITY_STACK` | `docker compose build --build-arg ORIGINALITY_STACK=slim` |
| Add Whisper / Piper weights | *nothing* — dashboard Asset Cache panel or `fetch_assets.py` |
| Change volume mounts in `docker-compose.yml` | `docker compose up -d` (no rebuild) |
| Change base image or system packages | `docker compose build --no-cache` |

---

## Related
- [[entities/dashboard]] — Asset Cache panel
- [[concepts/deployment]] — overall build/run procedure
- [[concepts/originality-stack]] — which originality waves need the extras
- [[entities/piper]], [[entities/librosa]], [[entities/face-pan]] — packages that move to optional
