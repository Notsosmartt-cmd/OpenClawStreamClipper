#!/usr/bin/env python3
"""
Seed the asset libraries used by the AI editing profiles (Phase 4 plan).

Pulls verified CC0 / public-domain assets into:
  assets/sfx/{whoosh,impact,scratch,ding,riser}/
  assets/music/{hype,funny,emotional,storytime,tension}/
  assets/broll/{travel,general}/
  assets/memes/{generic,comedy,hot_take,reactive}/   (Twemoji reactions only by default)

Also writes / updates a library.json manifest in each category folder so the
pipeline's pickers (meme_pick.py, music_pick.py, broll_pick.py) can read tags
without re-deriving them at runtime.

Run from repo root:
    python scripts/fetch_assets.py              # full seed (~120 MB)
    python scripts/fetch_assets.py --dry-run    # list what would download
    python scripts/fetch_assets.py --only sfx   # one category
    python scripts/fetch_assets.py --only sfx,music

Re-running is idempotent: existing files are skipped, manifests are merged.

External keys (optional, off by default):
    PEXELS_API_KEY    — enables b-roll fetch from Pexels (TOS bars ML training,
                        finished-clip output is fine).
    FREESOUND_API_KEY — enables SFX fetch from Freesound CC0 filter.

Stdlib only. No third-party deps.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ASSETS = REPO_ROOT / "assets"
USER_AGENT = "OpenClawStreamClipper-Seed/1.0 (+local; CC0 fetch)"


# ─────────────────────────────────────────────────────────────────────────────
# Curated URL pool. Every entry was verified reachable by web research; the
# license column is the ONLY contract — if a CDN moves a file, the script
# logs a 404 and continues.
# ─────────────────────────────────────────────────────────────────────────────

SFX_SINGLES = [
    # whoosh
    {"url": "https://archive.org/download/various-sound-effects/brwoosh1.mp3",
     "dest": "sfx/whoosh/brwoosh1.mp3",
     "license": "CC0", "tags": ["whoosh", "transition", "swoosh"]},
    {"url": "https://archive.org/download/various-sound-effects/brwoosh2.mp3",
     "dest": "sfx/whoosh/brwoosh2.mp3",
     "license": "CC0", "tags": ["whoosh", "transition"]},
    {"url": "https://archive.org/download/various-sound-effects/flyaway.mp3",
     "dest": "sfx/whoosh/flyaway.mp3",
     "license": "CC0", "tags": ["whoosh", "fly", "exit"]},
    {"url": "https://opengameart.org/sites/default/files/wind%20woosh%20loop.ogg",
     "dest": "sfx/whoosh/wind_woosh_loop.ogg",
     "license": "CC0", "tags": ["whoosh", "wind", "loop"]},

    # impact / boom
    {"url": "https://upload.wikimedia.org/wikipedia/commons/b/b9/Explosion-LS100155.ogg",
     "dest": "sfx/impact/explosion_ls100155.ogg",
     "license": "Public Domain", "tags": ["impact", "explosion", "boom"]},
    {"url": "https://opengameart.org/sites/default/files/NenadSimic%20-%20Muffled%20Distant%20Explosion.wav",
     "dest": "sfx/impact/muffled_distant_explosion.wav",
     "license": "CC0", "tags": ["impact", "explosion", "distant"]},
    {"url": "https://archive.org/download/various-sound-effects/cannon.mp3",
     "dest": "sfx/impact/cannon.mp3",
     "license": "CC0", "tags": ["impact", "boom", "cannon"]},

    # scratch
    {"url": "https://archive.org/download/various-sound-effects/inn-room-scratch.mp3",
     "dest": "sfx/scratch/inn_room_scratch.mp3",
     "license": "CC0", "tags": ["scratch", "comedy", "freeze"]},

    # ding / chime
    {"url": "https://archive.org/download/various-sound-effects/chimes1.mp3",
     "dest": "sfx/ding/chimes1.mp3", "license": "CC0", "tags": ["ding", "chime"]},
    {"url": "https://archive.org/download/various-sound-effects/chimes2.mp3",
     "dest": "sfx/ding/chimes2.mp3", "license": "CC0", "tags": ["ding", "chime"]},
    {"url": "https://archive.org/download/various-sound-effects/chimes3.mp3",
     "dest": "sfx/ding/chimes3.mp3", "license": "CC0", "tags": ["ding", "chime"]},
    {"url": "https://archive.org/download/various-sound-effects/bellworm1.mp3",
     "dest": "sfx/ding/bellworm1.mp3", "license": "CC0", "tags": ["ding", "bell"]},
]

# OpenGameArt + Kenney ZIP packs — extracted into the matching category folder.
# Each pack contributes 50–130 individual SFX files.
SFX_ZIPS = [
    {"url": "https://kenney.nl/media/pages/assets/impact-sounds/8aa7b545c9-1677589768/kenney_impact-sounds.zip",
     "name": "kenney_impact", "extract_to": "sfx/impact",
     "license": "CC0", "tags": ["impact", "hit", "thud"]},
    {"url": "https://kenney.nl/media/pages/assets/interface-sounds/d23a84242e-1677589452/kenney_interface-sounds.zip",
     "name": "kenney_interface", "extract_to": "sfx/ding",
     "license": "CC0", "tags": ["ding", "ui", "click"]},
    {"url": "https://kenney.nl/media/pages/assets/ui-audio/e19c9b1814-1677590494/kenney_ui-audio.zip",
     "name": "kenney_ui_audio", "extract_to": "sfx/ding",
     "license": "CC0", "tags": ["ding", "ui", "notification"]},
    {"url": "https://kenney.nl/media/pages/assets/sci-fi-sounds/e3af5f7ed7-1677589334/kenney_sci-fi-sounds.zip",
     "name": "kenney_scifi", "extract_to": "sfx/riser",
     "license": "CC0", "tags": ["riser", "sweep", "sci-fi"]},
    {"url": "https://kenney.nl/media/pages/assets/digital-audio/7492b26e77-1677590265/kenney_digital_audio.zip",
     "name": "kenney_digital", "extract_to": "sfx/riser",
     "license": "CC0", "tags": ["riser", "digital", "sweep"]},
    {"url": "https://opengameart.org/sites/default/files/50-CC0-retro-synth-SFX.zip",
     "name": "rubberduck_retro_50", "extract_to": "sfx/riser",
     "license": "CC0", "tags": ["riser", "synth", "retro"]},
    {"url": "https://opengameart.org/sites/default/files/100-CC0-SFX_0.zip",
     "name": "rubberduck_100_v1", "extract_to": "sfx/impact",
     "license": "CC0", "tags": ["impact", "hit", "general"]},
    {"url": "https://opengameart.org/sites/default/files/100-CC0-wood-metal-SFX.zip",
     "name": "rubberduck_100_woodmetal", "extract_to": "sfx/impact",
     "license": "CC0", "tags": ["impact", "wood", "metal"]},
    {"url": "https://opengameart.org/sites/default/files/sfx_100_v2.zip",
     "name": "rubberduck_100_v2", "extract_to": "sfx/whoosh",
     "license": "CC0", "tags": ["whoosh", "general"]},
]

# FreePD music mirror on archive.org. All CC0. Direct .mp3 URLs.
MUSIC_TRACKS = [
    # hype
    ("Epic Boss Battle.mp3",          "music/hype",      ["hype", "epic", "battle"]),
    ("Behind Enemy Lines.mp3",        "music/hype",      ["hype", "tense", "action"]),
    ("Strength of the Titans.mp3",    "music/hype",      ["hype", "epic", "drums"]),
    ("New Hero in Town.mp3",          "music/hype",      ["hype", "uplifting"]),
    ("The Enemy.mp3",                 "music/hype",      ["hype", "intense"]),
    # funny
    ("Happy Whistling Ukulele.mp3",   "music/funny",     ["funny", "playful", "ukulele"]),
    ("Funshine.mp3",                  "music/funny",     ["funny", "happy"]),
    ("Spring Chicken.mp3",            "music/funny",     ["funny", "bouncy"]),
    ("Wakka Wakka.mp3",               "music/funny",     ["funny", "comedy"]),
    ("Pickled Pink.mp3",              "music/funny",     ["funny", "playful"]),
    ("Ukulele Song.mp3",              "music/funny",     ["funny", "ukulele"]),
    # emotional
    ("Emotional Blockbuster 2.mp3",   "music/emotional", ["emotional", "cinematic"]),
    ("Cornfield Chase.mp3",           "music/emotional", ["emotional", "piano", "strings"]),
    ("Epic Blockbuster 2.mp3",        "music/emotional", ["emotional", "cinematic", "epic"]),
    ("Night Vigil.mp3",               "music/emotional", ["emotional", "slow"]),
    ("Relaxing Ballad.mp3",           "music/emotional", ["emotional", "ballad"]),
    ("The Celebrated Minuet.mp3",     "music/emotional", ["emotional", "classical"]),
    # storytime
    ("Adventure.mp3",                 "music/storytime", ["storytime", "adventure"]),
    ("Be Chillin.mp3",                "music/storytime", ["storytime", "chill", "lofi"]),
    ("Study and Relax.mp3",           "music/storytime", ["storytime", "chill"]),
    ("City Sunshine.mp3",             "music/storytime", ["storytime", "uplifting"]),
    ("Motions.mp3",                   "music/storytime", ["storytime", "mid-tempo"]),
    ("Still Pickin.mp3",              "music/storytime", ["storytime", "bluegrass"]),
    # tension / news
    ("Stereotype News.mp3",           "music/tension",   ["tension", "news", "bed"]),
    ("Evil Incoming.mp3",             "music/tension",   ["tension", "dark"]),
    ("The Ice Giants.mp3",            "music/tension",   ["tension", "epic"]),
    ("Black Knight.mp3",              "music/tension",   ["tension", "dark"]),
    ("Assassin.mp3",                  "music/tension",   ["tension", "stealth"]),
]

FREEPD_BASE = (
    "https://archive.org/download/allfreepdmusicbykuronekony4n/"
    "content/drive/My%20Drive/Download/all%20freepd%20music%20%28by%20kuronekony4n%29/"
)

BROLL_SINGLES = [
    {"url": "https://archive.org/download/PublicDomainCc0AirTravelStockVideoFootage/FlightBetweenCharlestonAndMiamiSummer2013.mp4",
     "dest": "broll/travel/flight_charleston_miami.mp4",
     "license": "CC0", "tags": ["travel", "flight", "airplane", "window"]},
    {"url": "https://archive.org/download/PublicDomainCc0AirTravelStockVideoFootage/LandingAtMiamiInternationalAirportSummer2013.mp4",
     "dest": "broll/travel/landing_miami.mp4",
     "license": "CC0", "tags": ["travel", "landing", "airport", "miami"]},
    {"url": "https://archive.org/download/PublicDomainCc0AirTravelStockVideoFootage/LiftoffAtCharlestonAirportSummer2013.mp4",
     "dest": "broll/travel/liftoff_charleston.mp4",
     "license": "CC0", "tags": ["travel", "liftoff", "takeoff", "airport"]},
]

# Twemoji set for reaction overlays. CC-BY 4.0 — manifest records the
# attribution string so the pipeline can render a one-frame credit if you
# ever want to be strict, or just treat it as a best-effort acknowledgement.
TWEMOJI_BASE = "https://raw.githubusercontent.com/twitter/twemoji/master/assets/72x72/"
TWEMOJI = [
    ("1f632.png", "surprised",       ["surprise", "shock", "wow"],          "reactive"),
    ("1f923.png", "rofl",            ["laugh", "funny", "lol"],             "comedy"),
    ("1f602.png", "joy",             ["laugh", "tears", "funny"],           "comedy"),
    ("1f914.png", "thinking",        ["thinking", "skeptical", "hmm"],      "hot_take"),
    ("1f926.png", "facepalm",        ["facepalm", "regret", "ugh"],         "reactive"),
    ("1f44f.png", "clap",            ["clap", "applause", "respect"],       "reactive"),
    ("1f9d0.png", "monocle",         ["scrutiny", "skeptical"],             "hot_take"),
    ("1f928.png", "raised_brow",     ["doubt", "really", "skeptical"],      "hot_take"),
    ("1f480.png", "skull",           ["dead", "lol", "rip"],                "comedy"),
    ("1f525.png", "fire",            ["fire", "hype", "lit"],               "reactive"),
    ("1f4af.png", "100",             ["100", "fact", "respect"],            "reactive"),
    ("1f644.png", "eyeroll",         ["eyeroll", "annoyed", "really"],      "hot_take"),
    ("1f97a.png", "pleading",        ["sad", "please", "puppy"],            "comedy"),
    ("1f60d.png", "heart_eyes",      ["love", "wow", "beautiful"],          "reactive"),
    ("1f624.png", "angry",           ["angry", "rage", "mad"],              "hot_take"),
]
TWEMOJI_ATTRIBUTION = (
    "Reaction emoji from Twemoji (https://github.com/twitter/twemoji), "
    "Copyright 2020 Twitter, Inc and other contributors, CC-BY 4.0."
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _http_get(url: str, dest: Path, *, timeout: int = 60) -> int:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        dest.parent.mkdir(parents=True, exist_ok=True)
        size = 0
        with open(dest, "wb") as f:
            while True:
                chunk = r.read(64 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                size += len(chunk)
        return size


def _download(url: str, dest: Path, *, label: str, dry: bool) -> bool:
    if dest.exists() and dest.stat().st_size > 0:
        print(f"  skip  {label} — already exists ({dest.stat().st_size // 1024} KB)")
        return True
    if dry:
        print(f"  DRY   {label} <- {url}")
        return True
    try:
        size = _http_get(url, dest)
        print(f"  ok    {label} — {size // 1024} KB")
        return True
    except urllib.error.HTTPError as e:
        print(f"  FAIL  {label} — HTTP {e.code} ({url})", file=sys.stderr)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print(f"  FAIL  {label} — {type(e).__name__}: {e}", file=sys.stderr)
    return False


def _load_manifest(folder: Path) -> dict:
    p = folder / "library.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"version": 1, "entries": []}


def _save_manifest(folder: Path, manifest: dict) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "library.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _merge_entry(manifest: dict, entry: dict) -> None:
    """Add or replace by `file` key, preserving entry order."""
    for i, e in enumerate(manifest["entries"]):
        if e.get("file") == entry["file"]:
            manifest["entries"][i] = entry
            return
    manifest["entries"].append(entry)


# ─────────────────────────────────────────────────────────────────────────────
# Per-category fetchers
# ─────────────────────────────────────────────────────────────────────────────

def fetch_sfx_singles(dry: bool) -> int:
    print("[sfx-singles]")
    n = 0
    by_folder: dict[Path, dict] = {}
    for item in SFX_SINGLES:
        dest = ASSETS / item["dest"]
        ok = _download(item["url"], dest, label=item["dest"], dry=dry)
        if ok and not dry:
            folder = dest.parent
            manifest = by_folder.setdefault(folder, _load_manifest(folder))
            _merge_entry(manifest, {
                "file": dest.name,
                "tags": item["tags"],
                "license": item["license"],
                "source": item["url"],
            })
            n += 1
    if not dry:
        for folder, m in by_folder.items():
            _save_manifest(folder, m)
    return n


def fetch_sfx_zips(dry: bool) -> int:
    print("[sfx-zips]")
    n = 0
    tmp_root = ASSETS / "_tmp_zips"
    for pack in SFX_ZIPS:
        zip_path = tmp_root / f"{pack['name']}.zip"
        ok = _download(pack["url"], zip_path, label=pack["name"], dry=dry)
        if not ok or dry:
            continue
        out_dir = ASSETS / pack["extract_to"] / pack["name"]
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(zip_path) as zf:
                audio = [m for m in zf.namelist()
                         if m.lower().endswith((".wav", ".mp3", ".ogg", ".flac"))
                         and not m.endswith("/")]
                for member in audio:
                    target = out_dir / Path(member).name
                    if target.exists() and target.stat().st_size > 0:
                        continue
                    with zf.open(member) as src, open(target, "wb") as dst:
                        dst.write(src.read())
                print(f"  ok    extracted {len(audio)} files -> {pack['extract_to']}/{pack['name']}/")
        except zipfile.BadZipFile as e:
            print(f"  FAIL  {pack['name']} — bad zip: {e}", file=sys.stderr)
            continue

        manifest_folder = ASSETS / pack["extract_to"]
        manifest = _load_manifest(manifest_folder)
        for f in sorted(out_dir.iterdir()):
            if not f.is_file():
                continue
            _merge_entry(manifest, {
                "file": f"{pack['name']}/{f.name}",
                "tags": pack["tags"] + [Path(f.name).stem.replace("_", "-").lower()],
                "license": pack["license"],
                "source": pack["url"],
            })
        _save_manifest(manifest_folder, manifest)
        n += 1
    # clean up zip cache
    if tmp_root.exists() and not dry:
        for z in tmp_root.glob("*.zip"):
            try:
                z.unlink()
            except OSError:
                pass
        try:
            tmp_root.rmdir()
        except OSError:
            pass
    return n


def fetch_music(dry: bool) -> int:
    print("[music]")
    n = 0
    by_folder: dict[Path, dict] = {}
    for filename, dest_folder, tags in MUSIC_TRACKS:
        url = FREEPD_BASE + urllib.parse.quote(filename)
        # Some FreePD titles contain unicode/encoding subtleties; quote the
        # filename component but not the path we already URL-encoded above.
        dest = ASSETS / dest_folder / filename
        ok = _download(url, dest, label=f"{dest_folder}/{filename}", dry=dry)
        if ok and not dry:
            folder = dest.parent
            manifest = by_folder.setdefault(folder, _load_manifest(folder))
            _merge_entry(manifest, {
                "file": filename,
                "tags": tags,
                "license": "CC0",
                "source": url,
            })
            n += 1
    if not dry:
        for folder, m in by_folder.items():
            _save_manifest(folder, m)
    return n


def fetch_broll(dry: bool) -> int:
    print("[broll]")
    n = 0
    by_folder: dict[Path, dict] = {}
    for item in BROLL_SINGLES:
        dest = ASSETS / item["dest"]
        ok = _download(item["url"], dest, label=item["dest"], dry=dry)
        if ok and not dry:
            folder = dest.parent
            manifest = by_folder.setdefault(folder, _load_manifest(folder))
            _merge_entry(manifest, {
                "file": dest.name,
                "tags": item["tags"],
                "license": item["license"],
                "source": item["url"],
                "duration_hint_s": None,  # filled in by ffprobe at first use
            })
            n += 1
    if not dry:
        for folder, m in by_folder.items():
            _save_manifest(folder, m)
    if os.environ.get("PEXELS_API_KEY"):
        print("  note: PEXELS_API_KEY set — extend BROLL_SINGLES to call the Pexels API "
              "for richer common-noun coverage. Pexels TOS forbids ML training; finished-clip output is fine.")
    else:
        print("  note: set PEXELS_API_KEY to extend coverage (food, money, kitchen, sports, etc).")
    return n


def fetch_memes(dry: bool) -> int:
    """Pull the Twemoji reaction set into assets/memes/generic/.

    Copyrighted reaction memes (Pepe, Spider-Man pointing, Hide-the-Pain
    Harold, etc.) are NOT auto-downloaded — the user has accepted the IP
    risk and can drop those into assets/memes/<category>/ manually. The
    fetch script will pick them up on the next manifest scan.
    """
    print("[memes/twemoji]")
    n = 0
    by_folder: dict[Path, dict] = {}
    for emoji_file, slug, tags, default_cat in TWEMOJI:
        # Generic copy + per-category copy so the LLM sees them in any
        # category bucket without us duplicating the file twice on disk.
        # We download once into generic/ and reference the same path from
        # the per-category manifests.
        dest = ASSETS / "memes" / "generic" / f"{slug}.png"
        url = TWEMOJI_BASE + emoji_file
        ok = _download(url, dest, label=f"twemoji/{slug}", dry=dry)
        if ok and not dry:
            entry = {
                "file": f"../generic/{slug}.png",
                "tags": tags,
                "license": "CC-BY 4.0",
                "attribution": TWEMOJI_ATTRIBUTION,
                "source": url,
            }
            cat_folder = ASSETS / "memes" / default_cat
            cat_folder.mkdir(parents=True, exist_ok=True)
            cat_manifest = by_folder.setdefault(cat_folder, _load_manifest(cat_folder))
            _merge_entry(cat_manifest, entry)
            # Also list it in generic/ so style_profiles can find it without category bias
            gen_manifest = by_folder.setdefault(dest.parent, _load_manifest(dest.parent))
            _merge_entry(gen_manifest, {**entry, "file": f"{slug}.png"})
            n += 1
    if not dry:
        for folder, m in by_folder.items():
            _save_manifest(folder, m)
    return n


def scan_only() -> int:
    """Re-scan every assets/ subfolder and rewrite library.json from the files
    on disk. Useful after the user drops in their own memes / b-roll / music
    without going through the fetch script."""
    print("[scan-only]")
    audio_ext = {".wav", ".mp3", ".ogg", ".flac"}
    image_ext = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
    video_ext = {".mp4", ".webm", ".mov", ".mkv"}
    n = 0
    for top in ("sfx", "music", "broll", "memes"):
        root = ASSETS / top
        if not root.exists():
            continue
        for cat in [p for p in root.iterdir() if p.is_dir()]:
            manifest = _load_manifest(cat)
            existing = {e["file"]: e for e in manifest["entries"]}
            files = []
            for f in cat.rglob("*"):
                if not f.is_file():
                    continue
                ext = f.suffix.lower()
                if ext not in (audio_ext | image_ext | video_ext):
                    continue
                rel = str(f.relative_to(cat)).replace("\\", "/")
                files.append(rel)
            new_entries = []
            for rel in sorted(files):
                if rel in existing:
                    new_entries.append(existing[rel])
                else:
                    new_entries.append({
                        "file": rel,
                        "tags": [Path(rel).stem.replace("_", " ").replace("-", " ").lower()],
                        "license": "user-supplied",
                        "source": "local",
                    })
                    n += 1
            manifest["entries"] = new_entries
            _save_manifest(cat, manifest)
        print(f"  ok    rebuilt manifests under assets/{top}/")
    return n


# ─────────────────────────────────────────────────────────────────────────────

CATEGORIES = {
    "sfx": [fetch_sfx_singles, fetch_sfx_zips],
    "music": [fetch_music],
    "broll": [fetch_broll],
    "memes": [fetch_memes],
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="list URLs without downloading")
    ap.add_argument("--only", default="",
                    help="comma-separated subset: sfx,music,broll,memes")
    ap.add_argument("--scan", action="store_true",
                    help="rewrite library.json files from on-disk contents (no download)")
    args = ap.parse_args()

    if args.scan:
        n = scan_only()
        print(f"\nscan complete — {n} new entry/entries added")
        return 0

    selected = [s.strip() for s in args.only.split(",") if s.strip()] if args.only else list(CATEGORIES)
    invalid = [s for s in selected if s not in CATEGORIES]
    if invalid:
        print(f"unknown category: {', '.join(invalid)} (valid: {', '.join(CATEGORIES)})", file=sys.stderr)
        return 2

    t0 = time.time()
    total = 0
    for cat in selected:
        for fn in CATEGORIES[cat]:
            total += fn(args.dry_run)
    dt = time.time() - t0
    mode = "dry-run" if args.dry_run else "fetched"
    print(f"\n{mode} {total} item(s) in {dt:.1f}s")
    if not args.dry_run:
        print("Re-run with --scan after adding your own files to update manifests.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
