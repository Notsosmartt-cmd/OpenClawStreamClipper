#!/usr/bin/env python3
"""Central path resolution for the bare-metal Windows pipeline.

Single source of truth that replaces the hardcoded Linux paths the Docker
image baked in:

    /tmp/clipper                     → work dir (ephemeral run artifacts)
    /root/VODs                       → vods dir (input)
    /root/VODs/Clips_Ready           → clips dir (output)
    /root/.cache/whisper-models      → whisper model cache
    /root/.openclaw                  → config dir (speech.json, models.json, …)
    /root/scripts, /root/scripts/lib → repo script dirs

Every location is overridable by env var so the three independent entry
points — the orchestrator (``run_pipeline.py``), the Flask dashboard, and
the reused ``scripts/lib/**`` modules invoked as subprocesses — all resolve
to the *same* real Windows directories. The Docker container relied on the
filesystem layout being identical everywhere; on bare metal we make that
explicit instead.

Import either as a package module (``from lib.paths import PATHS``) or as a
sibling (``import paths``); both work because the orchestrator puts
``scripts`` and ``scripts/lib`` on ``sys.path``.

Run directly to print the resolved layout (the Phase 1 verification step):

    python scripts/lib/paths.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

# scripts/lib/paths.py → parents[0]=lib, [1]=scripts, [2]=repo root
REPO_ROOT = Path(__file__).resolve().parents[2]


def nvidia_bin_dirs() -> list[str]:
    """The pip-installed CUDA lib dirs (cuDNN/cuBLAS/nvrtc) inside this venv.

    CTranslate2 (faster-whisper) loads these DLLs at runtime; on Windows they
    must be on the DLL search path. Returns the existing ``nvidia/*/bin`` dirs
    under the active interpreter's site-packages.
    """
    site = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
    dirs: list[str] = []
    if site.is_dir():
        for sub in site.iterdir():
            binp = sub / "bin"
            if binp.is_dir():
                dirs.append(str(binp))
    return dirs


def load_dotenv(path=None) -> int:
    """Load KEY=VALUE lines from .env into os.environ (already-set vars win).

    Keeps secrets (HF_TOKEN, DISCORD_BOT_TOKEN) in the gitignored .env rather
    than committed config, and lets a cloner *without* a .env run normally —
    missing keys just stay unset. No-op if the file is absent. Returns the
    number of keys set.
    """
    f = Path(path) if path else (REPO_ROOT / ".env")
    if not f.is_file():
        return 0
    n = 0
    for raw in f.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val
            n += 1
    return n


def _env_dir(var: str, default: Path) -> Path:
    """Resolve a directory from ``var`` (expanding ~ and env refs) or default."""
    val = os.environ.get(var)
    if val:
        return Path(os.path.expandvars(os.path.expanduser(val)))
    return default


def _default_work_dir() -> Path:
    """Per-user ephemeral work dir. Replaces /tmp/clipper.

    Prefers %LOCALAPPDATA% on Windows so the dir is stable across runs and
    the dashboard (separate process) resolves the same location. Falls back
    to the system temp dir on platforms without LOCALAPPDATA.
    """
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "OpenClawClipper" / "work"
    return Path(tempfile.gettempdir()) / "clipper"


@dataclass(frozen=True)
class Paths:
    """Resolved filesystem layout for one pipeline run / dashboard session."""

    repo_root: Path
    work_dir: Path
    vods_dir: Path
    clips_dir: Path
    whisper_cache: Path
    config_dir: Path
    scripts_dir: Path
    lib_dir: Path

    # --- derived run-artifact files (live under work_dir) -----------------
    @property
    def pipeline_log(self) -> Path:
        return self.work_dir / "pipeline.log"

    @property
    def stage_file(self) -> Path:
        return self.work_dir / "pipeline_stage.txt"

    @property
    def stages_log(self) -> Path:
        return self.work_dir / "pipeline_stages.log"

    @property
    def pid_file(self) -> Path:
        return self.work_dir / "pipeline.pid"

    @property
    def done_file(self) -> Path:
        return self.work_dir / "pipeline.done"

    @property
    def processed_log(self) -> Path:
        return self.vods_dir / "processed.log"

    @property
    def transcript_json(self) -> Path:
        return self.work_dir / "transcript.json"

    @property
    def transcript_srt(self) -> Path:
        return self.work_dir / "transcript.srt"

    @property
    def hype_moments(self) -> Path:
        return self.work_dir / "hype_moments.json"

    @property
    def scored_moments(self) -> Path:
        return self.work_dir / "scored_moments.json"

    @property
    def clips_made(self) -> Path:
        return self.work_dir / "clips_made.txt"

    @property
    def persistent_log_dir(self) -> Path:
        return self.clips_dir / ".pipeline_logs"

    @property
    def transcriptions_dir(self) -> Path:
        return self.vods_dir / ".transcriptions"

    @property
    def diagnostics_dir(self) -> Path:
        return self.clips_dir / ".diagnostics"

    # --- helpers ----------------------------------------------------------
    def work(self, name: str) -> Path:
        """Path to a file inside the work dir (replaces /tmp/clipper/<name>)."""
        return self.work_dir / name

    def config(self, name: str) -> Path:
        """Path to a config file (replaces /root/.openclaw/<name>)."""
        return self.config_dir / name

    def ensure_dirs(self) -> None:
        """Create the directories the pipeline writes to."""
        for d in (
            self.work_dir,
            self.clips_dir,
            self.vods_dir,
            self.whisper_cache,
            self.persistent_log_dir,
            self.transcriptions_dir,
            self.diagnostics_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)

    def child_env(self, base: dict | None = None) -> dict:
        """Env dict for child ``scripts/lib/**`` modules run as subprocesses.

        Sets every variable the reused modules read (each currently defaults
        to a Linux path) so they resolve to the Windows layout without code
        changes to the module defaults. Extend as the Phase 1 audit finds
        more config env vars.
        """
        env = dict(base if base is not None else os.environ)
        env["CLIP_WORK_DIR"] = str(self.work_dir)
        env["CLIP_TEMP_DIR"] = str(self.work_dir)   # alias (moment_groups.py)
        env["TEMP_DIR"] = str(self.work_dir)         # alias (profile_render.py)
        env["CLIP_VODS_DIR"] = str(self.vods_dir)
        env["CLIP_CLIPS_DIR"] = str(self.clips_dir)
        env["WHISPER_MODEL_DIR"] = str(self.whisper_cache)
        env["OPENCLAW_CONFIG_DIR"] = str(self.config_dir)
        # Per-feature config files (each module reads its own env var, else a
        # Linux default). Point them all at the repo config/ dir.
        env["CLIP_SPEECH_CONFIG"] = str(self.config("speech.json"))
        env["CLIP_STREAMER_PROMPTS"] = str(self.config("streamer_prompts.json"))
        env["CLIP_EMOTES_PATH"] = str(self.config("emotes.json"))
        env["CLIP_CHAT_CONFIG"] = str(self.config("chat.json"))
        env["CLIP_BOUNDARIES_CONFIG"] = str(self.config("boundaries.json"))
        env["CLIP_DENYLIST_PATH"] = str(self.config("denylist.json"))
        env["CLIP_GROUNDING_CONFIG"] = str(self.config("grounding.json"))
        env["CLIP_SELF_CONSISTENCY_CONFIG"] = str(self.config("self_consistency.json"))
        env["CLIP_DISCOURSE_MARKERS"] = str(self.config("discourse_markers.json"))
        env["CLIP_RUBRIC_CONFIG"] = str(self.config("rubric.json"))
        env["CLIP_PATTERNS_CONFIG"] = str(self.config("patterns.json"))
        env["CLIP_STYLE_PATTERN_WEIGHTS"] = str(self.config("style_pattern_weights.json"))
        # Asset dirs + module dir.
        env["LIB_DIR"] = str(self.lib_dir)
        env["PIPER_VOICE_DIR"] = str(self.repo_root / "models" / "piper")
        env["CALLBACKS_CACHE_DIR"] = str(self.repo_root / "models" / "sentence-transformers")
        # Quieter HF cache on Windows (no symlink support without dev mode).
        env.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        # Put the venv's CUDA DLLs on PATH so CTranslate2 (faster-whisper)
        # finds cuDNN/cuBLAS in child processes (Stage 2 / Stage 7 captions).
        nv = nvidia_bin_dirs()
        if nv:
            env["PATH"] = os.pathsep.join(nv + [env.get("PATH", "")])
        # Let reused lib modules import their siblings (lib/ and lib/stages/)
        # regardless of the dir the subprocess is launched from.
        pp = [str(self.lib_dir), str(self.lib_dir / "stages")]
        if env.get("PYTHONPATH"):
            pp.append(env["PYTHONPATH"])
        env["PYTHONPATH"] = os.pathsep.join(pp)
        return env


def resolve() -> Paths:
    """Build the :class:`Paths` for the current environment."""
    return Paths(
        repo_root=REPO_ROOT,
        work_dir=_env_dir("CLIP_WORK_DIR", _default_work_dir()),
        vods_dir=_env_dir("CLIP_VODS_DIR", REPO_ROOT / "vods"),
        clips_dir=_env_dir("CLIP_CLIPS_DIR", REPO_ROOT / "clips"),
        whisper_cache=_env_dir("WHISPER_MODEL_DIR", REPO_ROOT / "models" / "whisper"),
        config_dir=_env_dir("OPENCLAW_CONFIG_DIR", REPO_ROOT / "config"),
        scripts_dir=REPO_ROOT / "scripts",
        lib_dir=REPO_ROOT / "scripts" / "lib",
    )


# Module-level singleton for convenient `from lib.paths import PATHS`.
PATHS = resolve()


if __name__ == "__main__":
    p = resolve()
    print("Resolved bare-metal paths:")
    for field in (
        "repo_root", "work_dir", "vods_dir", "clips_dir",
        "whisper_cache", "config_dir", "scripts_dir", "lib_dir",
    ):
        print(f"  {field:14s} = {getattr(p, field)}")
    print("Derived:")
    for field in ("pipeline_log", "stage_file", "pid_file", "done_file",
                  "transcript_json", "hype_moments", "scored_moments"):
        print(f"  {field:14s} = {getattr(p, field)}")
