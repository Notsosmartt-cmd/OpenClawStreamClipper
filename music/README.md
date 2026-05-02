# Music Library (optional)

Drop audio files here to enable the Wave D **music bed** in the originality stack. This folder is mounted into the container at `/root/music` and is referenced by the dashboard's "Music bed folder" field.

## Folder convention (Tier A)

Organizing tracks into category subfolders gives the best results with zero configuration:

```
music/
├── hype/          ← clutch plays, reactions, high-energy
├── funny/         ← comedy, awkward, banter
├── emotional/     ← heartfelt, vulnerable
├── storytime/     ← narrative arcs
├── neutral/       ← fallback when the category folder is empty
└── (any other files at the root are a second-tier fallback)
```

Tracks inside a category folder are preferred when the clip's category matches. If the folder is missing or empty, the picker falls back to `neutral/` and then to the whole library.

## Tier C (librosa scoring)

For libraries of 20+ tracks, enable the **Tier C music matching** checkbox in the dashboard and click **Scan Music**. That runs `scripts/lib/scan_music.py` across every file here and writes `music_library.json` with per-track tempo / energy / brightness / duration. The picker then selects the track whose features best match the target category profile.

Rescan after adding new tracks.

## Supported formats

`.mp3`, `.wav`, `.m4a`, `.ogg`, `.flac`. Anything else is ignored.

## Mix levels

The pipeline mixes at roughly –22 dB under streamer audio and voiceover. Short tracks are looped across the clip length automatically.
