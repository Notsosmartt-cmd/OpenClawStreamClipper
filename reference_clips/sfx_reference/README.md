# SFX Reference Library — matching/analysis use ONLY

Seed soundboard for **clip-forensics Phase 4a exact-SFX matching** (audfprint /
cross-correlation): "is *this specific* vine boom at t=7.2s in the competitor
clip?" See `AIclippingPipelineVault/wiki/concepts/plan-clip-forensics.md` (E2 in
the master proposal).

> **LICENSE WARNING — do NOT use these in rendered clips.**
> These are canonical meme sounds fetched from myinstants.com direct media URLs
> (user-uploaded, provenance murky, mostly copyrighted snippets). They are kept
> ONLY as an offline *matching reference* — the same "analysis-only, never
> shipped" lane as the Demucs weights. Sounds that go INTO rendered clips live
> in `assets/sfx/<kind>/` and must be CC0/royalty-free (see the CC0 source list
> in wiki/concepts/sfx-cue-taxonomy-2026-06.md §4).

## Format guidance (for adding your own)
- **Any format FFmpeg decodes works**: `.mp3`, `.wav`, `.flac`, `.ogg`, `.m4a`.
  WAV is ideal (lossless) but mp3 is fine for matching.
- **One sound per file**, named after the sound kind (matches the
  `config/audio_sense_labels.json` vocabulary where possible).
- Short is better (~0.3–3 s); longer files still match but slower.
- Drop new files in this folder — media is gitignored, README is tracked.

## Contents (downloaded + ffprobe-validated 2026-07-02)

| File | Dur | Source slug (myinstants /media/sounds/) |
|---|---|---|
| vine_boom.mp3 | 1.25s | vine-boom.mp3 |
| bruh.mp3 | 4.13s | bruh-sound-effect.mp3 |
| quack.mp3 | 10.66s | duck-quack-sound-effect.mp3 |
| airhorn.mp3 | 2.98s | dj-air-horn.mp3 |
| record_scratch.mp3 | 0.94s | record-scratch.mp3 |
| sad_trombone.mp3 | 3.55s | sad-trombone.mp3 |
| crickets.mp3 | 5.28s | crickets.mp3 |
| applause.mp3 | 6.84s | applause.mp3 |
| boing.mp3 | 0.91s | boing.mp3 |
| whoosh.mp3 | 4.08s | whoosh.mp3 |
| censor_beep.mp3 | 2.72s | bleep.mp3 |
| metal_pipe.mp3 | 2.79s | metal-pipe-clang.mp3 |
| anime_wow.mp3 | 4.18s | anime-wow-sound-effect.mp3 |
| oof.mp3 | 7.11s | roblox-death-sound.mp3 |
