#!/usr/bin/env python3
"""Native pipeline orchestrator — Python port of ``scripts/clip-pipeline.sh``.

Bare-metal Windows entry point (no Docker, no bash). Usage mirrors the old
shell flags exactly so the dashboard and the Discord/OpenClaw skill can call
it the same way:

    python scripts/run_pipeline.py --style auto --vod lacy
    python scripts/run_pipeline.py --list
    python scripts/run_pipeline.py --style funny --force

Config precedence for each setting: ``CLIP_*`` env var  >  config/models.json
>  built-in default. (The dashboard sets the env vars; a bare CLI run falls
back to models.json so it still works.)
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import sys
import time
from pathlib import Path

# --- import wiring: make `import paths` and `from pipeline import ...` work ---
HERE = Path(__file__).resolve().parent           # scripts/
sys.path.insert(0, str(HERE / "lib"))            # scripts/lib  → `import paths`
sys.path.insert(0, str(HERE))                    # scripts      → `import pipeline`

import paths  # noqa: E402
from pipeline import common  # noqa: E402
from pipeline.common import PipelineExit  # noqa: E402


def _bool_env(name: str, default: bool) -> bool:
    # Accept the standard truthy set — NOT just "true". Before 2026-07-04 this
    # was `== "true"`, so any flag set to "1"/"yes"/"on" silently read as False.
    # That disabled CLIP_SFX_ANCHOR=1 and CLIP_COLD_OPEN=1 in every run that set
    # them numerically (the harness did), turning the acoustic SFX anchor + the
    # cold-open teaser OFF while looking ON.
    return os.environ.get(name, str(default).lower()).strip().lower() in (
        "true", "1", "yes", "on")


class Ctx:
    """Run configuration + mutable runtime state shared across stages
    (the Python equivalent of clip-pipeline.sh's globals)."""

    def __init__(self, args: argparse.Namespace):
        self.paths = paths.PATHS
        self.style = args.style
        self.target_vod = args.vod or ""
        self.list_mode = bool(args.list)
        self.force = bool(args.force)
        self.type_hint = args.type or ""

        models = {}
        cfg_file = self.paths.config("models.json")
        if cfg_file.exists():
            try:
                models = json.loads(cfg_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                models = {}

        def pick(env_key: str, json_key: str, default):
            v = os.environ.get(env_key)
            if v is not None and v != "":
                return v
            if models.get(json_key) not in (None, ""):
                return models[json_key]
            return default

        url = pick("CLIP_LLM_URL", "llm_url", "http://localhost:1234")
        self.llm_url = str(url).replace("host.docker.internal", "localhost").rstrip("/")
        self.text_model = pick("CLIP_TEXT_MODEL", "text_model", "qwen/qwen3.5-9b")
        self.vision_model = pick("CLIP_VISION_MODEL", "vision_model", self.text_model)
        self.text_model_passb = os.environ.get("CLIP_TEXT_MODEL_PASSB") or models.get("text_model_passb") or self.text_model
        # Phase 4 B5 — decorrelation model for the Stage 4 rubric. Falls back to
        # passb -> text_model (null default => no decorrelation, no behavior change).
        self.text_model_passd = os.environ.get("CLIP_TEXT_MODEL_PASSD") or models.get("text_model_passd") or self.text_model_passb
        self.vision_model_stage6 = os.environ.get("CLIP_VISION_MODEL_STAGE6") or models.get("vision_model_stage6") or self.vision_model
        self.whisper_model = pick("CLIP_WHISPER_MODEL", "whisper_model", "large-v3-turbo")
        self.context_length = int(pick("CLIP_CONTEXT_LENGTH", "context_length", 8192))

        self.captions_enabled = _bool_env("CLIP_CAPTIONS", True)
        self.hook_caption_enabled = _bool_env("CLIP_HOOK_CAPTION", True)
        self.clip_speed = os.environ.get("CLIP_SPEED", "1.0")

        # Originality controls
        self.originality = _bool_env("CLIP_ORIGINALITY", True)
        self.framing = os.environ.get("CLIP_FRAMING", "blur_fill")
        self.stitch = _bool_env("CLIP_STITCH", False)
        self.narrative = _bool_env("CLIP_NARRATIVE", True)
        # Fix 3 (2026-06-06): render A1 arc / M3 callback moments as a 2-segment
        # stitch (short setup snippet -> payoff) so the setup->payoff arc lands
        # visually instead of the setup only living in the caption. Opt-in.
        self.arc_stitch = _bool_env("CLIP_ARC_STITCH", False)
        self.tts_vo = _bool_env("CLIP_TTS_VO", False)
        self.music_bed = os.environ.get("CLIP_MUSIC_BED", "")
        self.music_tier_c = _bool_env("CLIP_MUSIC_TIER_C", False)
        self.camera_pan = _bool_env("CLIP_CAMERA_PAN", False)
        self.style_profiles = _bool_env("CLIP_STYLE_PROFILES", False)
        # Cold-open teaser: prepend a ~1-2s tease of the run-up to the payoff +
        # whoosh/flash into the clip (concepts/hook-engineering-2026-06). Opt-in;
        # a Stage 7 post-step (cold_open.py), failure-soft.
        self.cold_open = _bool_env("CLIP_COLD_OPEN", False)
        # Acoustic-anchor SFX placement inside profile-mode (consumed by
        # profile_render.py / sfx_cues.py; concepts/sfx-cue-taxonomy-2026-06).
        # Default ON (deliberate, unlike most new flags): profile-mode already
        # emits SFX by default, so this only improves WHERE they land (beat
        # anchors vs zoom-punch timing) and has a clean kill switch. Wired here
        # for discoverability/auditability; CLIP_SFX_ANCHOR=0 reverts to the
        # legacy zoom-tied synthesis.
        self.sfx_anchor = _bool_env("CLIP_SFX_ANCHOR", True)

        # Runtime state populated by stages
        self.vod_path: Path | None = None
        self.vod_basename: str = ""
        self.vod_duration: int = 0
        self.chat_available: bool = False
        self.chat_path: str = ""

        # Logger is attached in main() once log paths are known.
        self.log: common.Logger | None = None

    def child_env(self) -> dict:
        """Env for subprocessed lib modules: base path/config vars plus the
        per-run model + originality settings the modules read."""
        env = self.paths.child_env()
        env["CLIP_LLM_URL"] = self.llm_url
        env["LLM_URL"] = self.llm_url  # some modules read LLM_URL directly
        env["CLIP_TEXT_MODEL"] = env["TEXT_MODEL"] = self.text_model
        env["CLIP_VISION_MODEL"] = env["VISION_MODEL"] = self.vision_model
        env["CLIP_TEXT_MODEL_PASSB"] = env["TEXT_MODEL_PASSB"] = self.text_model_passb
        env["CLIP_TEXT_MODEL_PASSD"] = env["TEXT_MODEL_PASSD"] = self.text_model_passd
        env["CLIP_VISION_MODEL_STAGE6"] = env["VISION_MODEL_STAGE6"] = self.vision_model_stage6
        env["CLIP_WHISPER_MODEL"] = self.whisper_model
        env["CLIP_CONTEXT_LENGTH"] = str(self.context_length)
        env["CLIP_STYLE"] = self.style
        env["STREAM_TYPE_HINT"] = self.type_hint
        env["CLIP_STYLE_PROFILES"] = "true" if self.style_profiles else "false"
        env["CLIP_COLD_OPEN"] = "true" if self.cold_open else "false"
        env["CLIP_SFX_ANCHOR"] = "true" if self.sfx_anchor else "false"
        if self.vod_path:
            env["VOD_PATH"] = str(self.vod_path)
            env["VOD_BASENAME"] = self.vod_basename
        return env

    def configured_models(self) -> list[str]:
        return [self.text_model, self.vision_model,
                self.text_model_passb, self.text_model_passd, self.vision_model_stage6]


def parse_args(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="OpenClaw Stream Clipper pipeline")
    ap.add_argument("--style", default="auto")
    ap.add_argument("--vod", default="")
    ap.add_argument("--vods", default="",
                    help="comma-separated VOD names/stems to process sequentially (dashboard multi-select)")
    ap.add_argument("--type", default="")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--all", action="store_true", help="process every (unprocessed) VOD")
    # tolerate unknown args the way the bash `*) shift ;;` did
    args, _unknown = ap.parse_known_args(argv)
    return args


def _execute_stages(ctx, log) -> int:
    """Run model verification + the 8 stages for one VOD. Returns an exit code.
    Catches PipelineExit (an intentional early stop) and returns its code."""
    import importlib
    t0 = time.time()
    try:
        if not ctx.list_mode:
            common.verify_models(log, ctx.llm_url, ctx.configured_models())
        for i in range(1, 9):
            importlib.import_module(f"pipeline.stages.stage{i}").run(ctx)
        return 0
    except PipelineExit as pe:
        if pe.summary:
            log.line(pe.summary)
        return pe.code
    finally:
        if not ctx.list_mode:
            el = int(time.time() - t0)
            log.log(f"VOD session time [{ctx.vod_basename or ctx.target_vod or 'unknown'}]: "
                    f"{el // 60}m {el % 60}s ({el}s)")


def _discover_all(ctx) -> list[str]:
    """VOD basenames for --all: every VOD, minus already-processed (unless --force)."""
    from pipeline.stages import stage1
    p = ctx.paths
    vods = stage1._find_vods(p.vods_dir)
    if not ctx.force:
        vods = [v for v in vods if not stage1._is_processed(p.processed_log, v.name)]
    return [v.name for v in vods]


def _reset_work_artifacts(p) -> None:
    """Clear per-VOD work files between --all iterations, keeping the streamed
    log + lifecycle markers intact."""
    keep = {p.pipeline_log.name, p.stage_file.name, p.stages_log.name,
            p.pid_file.name, p.done_file.name}
    for f in p.work_dir.glob("*"):
        if f.name in keep:
            continue
        try:
            if f.is_dir():
                shutil.rmtree(f, ignore_errors=True)
            else:
                f.unlink(missing_ok=True)
        except Exception:
            pass


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    paths.load_dotenv()  # pull HF_TOKEN / other secrets from .env into the env
    ctx = Ctx(args)
    p = ctx.paths
    p.ensure_dirs()

    # Persistent timestamped log (survives work-dir cleanup).
    stamp = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
    _slug_base = (ctx.target_vod
                  or (args.vods.split(",")[0].strip() if args.vods else "")
                  or ("all" if args.all else "")
                  or "unknown")
    slug = "".join(c for c in Path(_slug_base).stem
                   if c.isalnum() or c in "_-")[:40] or "unknown"
    persistent_log = p.persistent_log_dir / f"{stamp}_{slug}.log"

    # Truncate the ephemeral log so the dashboard SSE starts fresh.
    try:
        p.pipeline_log.write_text("", encoding="utf-8")
    except OSError:
        pass

    log = common.Logger(p.pipeline_log, persistent_log)
    ctx.log = log
    start_epoch = time.time()
    common.write_pid_marker(persistent_log)

    log.line(f"=== Pipeline started {common._utc_stamp()} | style={ctx.style} "
             f"vod={ctx.target_vod} type={ctx.type_hint} speed={ctx.clip_speed} ===")
    log.line(f"=== Persistent log: {persistent_log} ===")
    log.log(f"Text model: {ctx.text_model} | Vision: {ctx.vision_model} | Whisper: {ctx.whisper_model}")
    log.log(f"LM Studio: {ctx.llm_url}")
    log.log(f"Originality: orig={ctx.originality} framing={ctx.framing} stitch={ctx.stitch} "
            f"narrative={ctx.narrative} pan={ctx.camera_pan} tts={ctx.tts_vo}")

    exit_code = 0

    def _handle_signal(signum, _frame):
        log.warn(f"received signal {signum} — aborting")
        raise PipelineExit(130, '{"status":"aborted","clips":0}')

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handle_signal)
        except (ValueError, OSError):
            pass

    try:
        if (args.all or args.vods) and not ctx.list_mode:
            if args.vods:
                # Explicit multi-select (dashboard): process exactly these,
                # respecting the caller's --force (re-transcribe) choice.
                targets = [s.strip() for s in args.vods.split(",") if s.strip()]
                batch_force = ctx.force
            else:
                # --all: every (unprocessed) VOD, each reprocessed fresh.
                targets = _discover_all(ctx)
                batch_force = True
            if not targets:
                log.log("No VODs to process.")
            for i, vod_name in enumerate(targets):
                if i > 0:
                    _reset_work_artifacts(p)
                log.line(f"=== Clipping {vod_name} ({i + 1}/{len(targets)}) ===")
                vctx = Ctx(argparse.Namespace(
                    style=args.style, vod=vod_name, type=args.type,
                    list=False, force=batch_force, all=False))
                vctx.log = log
                rc = _execute_stages(vctx, log)
                exit_code = rc or exit_code
        else:
            exit_code = _execute_stages(ctx, log)
    except Exception as e:  # noqa: BLE001
        exit_code = 1
        log.err(f"pipeline failed: {e}")
        import traceback
        log.write(traceback.format_exc())
    finally:
        common.cleanup(log, persistent_log, exit_code, start_epoch)
        log.close()

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
