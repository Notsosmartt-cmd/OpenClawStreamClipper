# reference_clips/ — curated competitor/source reference clips

Drop hand-picked short clips here that you want the **clip-forensics** tool to
study and replicate (see `AIclippingPipelineVault/wiki/concepts/plan-clip-forensics.md`).

This is **NOT** `vods/` (raw streams the pipeline clips) and **NOT** `assets/`
(SFX/music/B-roll injected into renders). It's a corpus of *finished, edited
clips that already get reach* — the forensics tool decomposes their editing
"essence" (SFX, music beds, censor sounds, cuts, captions) into a replicable
style profile.

## Layout

```
reference_clips/
├── README.md                      ← this file (tracked)
├── <name>.mp4                     ← a curated clip (gitignored — binary/large)
├── <name>.notes.json              ← OPTIONAL human annotations (tracked)
└── example.notes.json             ← sidecar template (tracked)
```

- Put the video files here (`.mp4`/`.mov`/`.webm`). They are **gitignored** (large binaries).
- For each clip you can add a `<name>.notes.json` sidecar with your own notes on
  what works — this becomes the **ground truth** the forensics output is checked
  against (e.g. "music swell in at 0:04, quack censor at 0:09, vine boom on the
  punchline at 0:12"). Sidecars **are tracked** so the annotations persist.

## Sidecar schema (`<name>.notes.json`)

See `example.notes.json`. Fields are all optional; fill what you noticed:

```json
{
  "clip": "reemknocks_bus.mp4",
  "source": "tiktok / competitor account / etc",
  "why_it_works": "free-text: what makes this clip land",
  "events": [
    {"t": 0.0,  "kind": "cold_open_teaser", "note": "payoff flashed before setup"},
    {"t": 4.0,  "kind": "music_in",        "note": "suspenseful bed fades up"},
    {"t": 9.0,  "kind": "censor",          "note": "quack over a curse"},
    {"t": 12.0, "kind": "sfx",             "note": "vine boom on the punchline"}
  ]
}
```

`kind` is free-text for now (suggested vocab: `cold_open_teaser`, `music_in`,
`music_out`, `sfx`, `censor`, `cut`, `zoom`, `caption`, `freeze`, `voiceover`).
The forensics tool will try to recover these automatically; your notes let it
score how well it did.

> Naming: keep filenames short and slug-like (`reemknocks_bus.mp4`), no spaces.
