---
title: "Asset Libraries"
type: concept
tags: [assets, sfx, music, broll, memes, twemoji, license, cc0, fetch, profiles]
sources: 0
updated: 2026-05-01
---

# Asset Libraries

Local CC0 / public-domain audio, video, and reaction-image libraries used by the upcoming AI editing-profile system (see [[concepts/style-profiles]] when filed). Seeds the data layer needed for Phase 4 of the editing-profiles plan: meme cutaways, B-roll inserts, SFX injection, music beds.

> [!note] Data layer only
> The libraries are present but not yet consumed by the pipeline. The pickers (`scripts/lib/meme_pick.py`, `scripts/lib/broll_pick.py`, `scripts/lib/sfx_inject.py`, the existing `scripts/lib/music_pick.py`) and the `chk-style-profiles` dashboard toggle land in subsequent phases.

---

## Layout

```
assets/
├── README.md
├── sfx/
│   ├── whoosh/      transitions (~0.3–1.0 s)
│   ├── impact/      cuts, booms, hits (~0.5–1.5 s)
│   ├── scratch/     record-scratches for comedy freeze-frames (sparse — see gaps)
│   ├── ding/        chimes, notifications (~0.3–0.8 s)
│   └── riser/       build-ups (~1.5–4 s)
├── music/
│   ├── hype/        ~140 BPM, drums (5 tracks)
│   ├── funny/       ~110 BPM, light (6 tracks)
│   ├── emotional/   piano/strings (6 tracks)
│   ├── storytime/   mid-tempo, lo-fi (6 tracks)
│   └── tension/     news / hot-take beds (5 tracks)
├── broll/
│   ├── travel/      9 short flight/landing/takeoff snippets (5 s each)
│   └── general/     empty — extend via Pexels API
├── memes/
│   ├── generic/     15 Twemoji reaction PNGs (canonical files live here)
│   ├── comedy/      manifest references ../generic/
│   ├── hot_take/    same pattern
│   └── reactive/    same pattern
└── caption_styles/  reserved for ASS preset templates (Phase 3)
```

Each leaf folder owns a `library.json` manifest. Picker libraries read these directly — the LLM that picks cutaways/B-roll/SFX is given the `tags` list and emits a tag-based selection that the picker resolves to a file.

### Manifest schema

```json
{
  "version": 1,
  "entries": [
    {
      "file": "brwoosh1.mp3",
      "tags": ["whoosh", "transition", "swoosh"],
      "license": "CC0",
      "source": "https://archive.org/download/various-sound-effects/brwoosh1.mp3"
    }
  ]
}
```

Optional fields: `attribution` (when license requires it, e.g. CC-BY), `duration_s`, `scene` (B-roll), `duration_hint_s` (filled in by ffprobe at first use).

### Memes manifest convention

Twemoji files live in `assets/memes/generic/<slug>.png`. Per-category manifests reference them by relative path (`../generic/<slug>.png`) so a single binary serves multiple category buckets without duplication on disk. User-supplied memes go directly under the matching category folder; rerunning the fetch script with `--scan` rebuilds every manifest from on-disk contents.

---

> [!note] Renamed 2026-05-02
> The seed/fetch script was originally `scripts/fetch_assets.py`. Renamed to `scripts/seed_libraries.py` to avoid confusion with the pre-existing `scripts/lib/fetch_assets.py` (Whisper / Piper model-cache helper used by `dashboard/routes/assets_routes.py`). API and behavior unchanged.

## scripts/seed_libraries.py

Single-file Python (stdlib only) that pulls verified CC0 / public-domain assets into the layout above and writes manifests as it downloads. Idempotent — existing files are skipped.

### Modes

```bash
python scripts/seed_libraries.py             # full seed (~195 MB)
python scripts/seed_libraries.py --dry-run   # list URLs without downloading
python scripts/seed_libraries.py --only sfx  # one category
python scripts/seed_libraries.py --only sfx,music
python scripts/seed_libraries.py --scan      # rebuild manifests from disk only
```

### What gets fetched

| Category | Source | Count | License |
|---|---|---|---|
| SFX singles | Internet Archive `various-sound-effects`, OpenGameArt, Wikimedia Commons | 11 verified files | CC0 / Public Domain |
| SFX ZIP packs | Kenney.nl + OpenGameArt rubberduck packs | 8 zips → ~755 files | CC0 |
| Music | Internet Archive `allfreepdmusicbykuronekony4n` (FreePD mirror) | 28 tracks across 5 categories | CC0 |
| B-roll | Internet Archive `PublicDomainCc0AirTravelStockVideoFootage` | 3 raw videos → 9 sliced 5 s snippets | CC0 |
| Reaction images | Twemoji 72×72 PNG set on GitHub | 15 emoji | CC-BY 4.0 (attribution preserved in every manifest entry) |

### B-roll slicing

The original Internet Archive air-travel videos are 30 s – 10 min long (~1.5 GB total). After download the script's caller is expected to slice them into 5 s snippets at distributed timestamps and delete the originals. The current seeded library already contains nine pre-sliced snippets (`flight_*_a/b/c.mp4`, `landing_miami_a/b/c.mp4`, `liftoff_charleston_a/b/c.mp4`) at 720p H.264, totalling ~10 MB. Future versions of the fetch script should automate this.

### Known fetch gaps (2026-05-01)

- `assets/sfx/scratch/inn_room_scratch.mp3` — Internet Archive returned HTTP 500. Fall back: the Kenney `interface-sounds` and `ui-audio` packs include scratch-shaped clicks; the `rubberduck_100_v2` pack has squeak-like cues. The `scratch/` folder is otherwise empty until a replacement source is wired in.
- `kenney_digital_audio.zip` — URL hash segment rotated on Kenney's CDN (HTTP 404). The other four Kenney ZIPs still resolve. `riser/` is still well-populated via `kenney_scifi` + `rubberduck_retro_50`.

Both are recoverable by editing the URL list in `scripts/seed_libraries.py`. Neither blocks Phase 4.

---

## License inventory

The script intentionally avoids several popular-but-restricted sources:

| Source | Status | Reason |
|---|---|---|
| Pixabay | **Avoided** | Current TOS bars ML training and bulk extraction |
| Pexels | **Optional via key** | Same TOS caveat; finished-clip output is permitted, set `PEXELS_API_KEY` env var to enable |
| Mixkit | **Avoided** | Proprietary license restricts redistribution |
| Coverr | **Avoided** | Switched away from CC0 to a restrictive Coverr License |
| BBC Sound Effects | **Avoided** | Non-commercial only — unsafe for monetized clips |
| Freesound.org | **Optional via key** | Even CC0 originals require OAuth login; set `FREESOUND_API_KEY` to extend |
| Internet Archive `various-sound-effects` | **Used** | CC0 1.0, direct downloads (302 → CDN) |
| Internet Archive `allfreepdmusicbykuronekony4n` | **Used** | FreePD mirror, CC0 1.0 |
| Internet Archive `PublicDomainCc0AirTravelStockVideoFootage` | **Used** | CC0 1.0 |
| OpenGameArt.org | **Used** | CC0 single files + 4 rubberduck packs |
| Kenney.nl | **Used** | CC0 SFX packs (impact, interface, UI, sci-fi) |
| Wikimedia Commons | **Used** | Per-file PD verification |
| Twemoji (Twitter) | **Used** | CC-BY 4.0; attribution preserved in manifests |
| KnowYourMeme image hosts | **User opt-in** | Copyrighted reaction memes (Pepe, Spider-Man pointing, Hide-the-Pain Harold). User accepted IP risk and supplies them manually under `assets/memes/<category>/` — script does not hotlink |

---

## Disk budget

| Category | Size |
|---|---|
| SFX | ~23 MB |
| Music | ~162 MB |
| B-roll (sliced) | ~11 MB |
| Memes | ~77 KB |
| **Total** | **~195 MB** |

`.gitignore` excludes the binary contents (`*.wav`, `*.mp3`, `*.mp4`, `*.png`, etc. under `assets/`) but keeps `library.json` manifests, `README.md`, `caption_styles/`, and `scripts/seed_libraries.py` in git so the recipe is reproducible.

---

## Extending

### Add user content

1. Drop files into the matching category folder (`assets/sfx/whoosh/my_clip.mp3`, `assets/memes/comedy/pepe_laugh.png`, etc.).
2. Run `python scripts/seed_libraries.py --scan`.
3. The script auto-tags by filename; edit the `library.json` `tags` list afterwards for richer LLM matching.

### Add API-keyed sources

`scripts/seed_libraries.py` reads `PEXELS_API_KEY` and `FREESOUND_API_KEY` env vars. The current implementation prints a hint when each is set; extending the URL pools to call those APIs is straightforward (search → result list → per-result download).

### Add new categories

Edit `CATEGORIES` in `scripts/seed_libraries.py` and add a corresponding fetcher function; add the new folder to the layout in `assets/README.md`. The picker libraries (Phase 4) just walk every leaf folder under `assets/<top>/`, so new categories are discovered automatically.

---

## Related

- [[concepts/originality-stack]] — Wave-D music bed and the wider originality scheme
- [[entities/librosa]] — feeds Tier-C music selection from the new music library
- [[concepts/captions]] — Phase 3 ASS preset templates land in `assets/caption_styles/`
- [[concepts/clip-rendering]] — Stage 7 will consume sfx/broll/meme libraries when Phase 4 ships
