# Model & Asset Cache

Everything in this folder lives on the host filesystem and is **mounted into the container** at runtime — this keeps the Docker image slim (~5 GB instead of ~8 GB) and lets you inspect or replace model weights without rebuilding.

## Layout

```
models/
├── whisper/              ← faster-whisper cache (mounted at /root/.cache/whisper-models)
│   └── models--Systran--faster-whisper-large-v3/
│       └── ...onnx, tokenizer, config files
│
└── piper/                ← Piper voice models (mounted at /root/.cache/piper)
    ├── en_US-amy-low.onnx
    ├── en_US-amy-low.onnx.json
    └── ...any other voices you drop in here
```

The folders can start empty. First pipeline run will populate `whisper/` automatically (downloads ~3 GB the first time — roughly 2 minutes on a good connection). Piper voices you download yourself or fetch through the **Asset Cache** panel in the dashboard.

## Why mounted, not baked in?

The previous Docker image had the Whisper large-v3 weights baked in. That meant:

- Image pull size was ~8 GB (mostly model).
- Updating the model required a rebuild.
- The contents were opaque — a "black box" that users couldn't inspect.

Now the image is build-once, content-static, and the heavyweight artifacts sit in a plain folder you can see.

## Changing the Whisper model

Delete the `whisper/models--Systran--faster-whisper-<old>` subfolder and change the model name in the dashboard **AI Models** panel (or edit `config/models.json`). The next pipeline run will pull the new model into this folder.

Alternative models recognized by faster-whisper:
- `large-v3` — best accuracy, ~3 GB (default)
- `large-v2` — previous best, ~3 GB
- `medium` — ~1.5 GB
- `small` — ~0.5 GB
- `base` — ~0.15 GB
- `tiny` — ~0.08 GB

## Adding Piper voices

Two easy ways:

1. **Dashboard**: click **Fetch voice** in the Asset Cache panel, type a voice ID (e.g., `en_US-ryan-high`), hit enter.
2. **Manually**: download the `.onnx` + `.onnx.json` from <https://huggingface.co/rhasspy/piper-voices> and drop the pair into `models/piper/`. The filename (minus extension) is the voice ID — set `PIPER_VOICE=<id>` env var or export it in `docker-compose.yml`.

Voices are small (~20 MB each). The default voice `en_US-amy-low` is fetched once on first voiceover use.

## Troubleshooting

- **"Piper voice not found"** in pipeline logs → the `piper/` folder is empty. Use the dashboard Asset Cache panel or run `docker exec stream-clipper python3 /root/scripts/lib/fetch_assets.py piper en_US-amy-low`.
- **"Whisper model download failed"** on first run → your network can't reach Hugging Face. Use a mirror or pre-download on another machine and copy the cache folder here.
- **Permission errors on Linux hosts** → Docker may write files as root. `chown -R $USER:$USER models/` after the first run.
