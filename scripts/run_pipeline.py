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
import subprocess
import sys
import tempfile
import threading
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
        # Default ON since 2026-07-10 (owner promotion after the 9/9-GOOD A/B run
        # 20260710_202308; the SFX + A/B lanes live in profile mode). Kill switch:
        # CLIP_STYLE_PROFILES=0 reverts to the legacy render path.
        self.style_profiles = _bool_env("CLIP_STYLE_PROFILES", True)
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
        # One stable stamp per RUN so effects_log (and any per-run artifact)
        # groups all clips under a single id — not one id per render second.
        # Cached on the instance: child_env() returns a fresh dict per stage, so a
        # setdefault here would re-stamp every stage.
        if not getattr(self, "run_stamp", None):
            self.run_stamp = os.environ.get("CLIP_RUN_STAMP") or time.strftime("%Y%m%d_%H%M%S")
        env["CLIP_RUN_STAMP"] = self.run_stamp
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


def _prefetch_stage2(next_vod_name: str, p, ctx, log):
    """C1 (Speed Wave 2, plan-serving-stack-2026-07): transcribe + audio-scan the NEXT
    batch VOD into the shared cache while the CURRENT VOD renders (Stage 7/8 use no LLM).
    Fully isolated from the shared work dir — writes only to `transcriptions_dir` and a temp
    wav — so it never clobbers the current VOD's audio.wav. Returns a started daemon thread,
    or None when there is nothing to prefetch (caches already warm / VOD missing / no work).
    Failure-soft: any error just means the next VOD transcribes inline as usual."""
    stem = Path(next_vod_name).stem
    tdir = p.transcriptions_dir
    cached_json = tdir / f"{stem}.transcript.json"
    cached_srt = tdir / f"{stem}.transcript.srt"
    cached_events = tdir / f"{stem}.audio_events.json"
    vod_path = p.vods_dir / next_vod_name

    def _valid_events(pth) -> bool:
        try:
            d = json.loads(Path(pth).read_text(encoding="utf-8"))
            return isinstance(d.get("windows"), list) and len(d["windows"]) > 0 \
                and not d.get("skipped_reason")
        except Exception:
            return False

    need_tx = not (cached_json.exists() and cached_srt.exists())
    need_ev = not (cached_events.exists() and _valid_events(cached_events))
    if not vod_path.exists() or (not need_tx and not need_ev):
        return None  # nothing to do — do NOT evict the model needlessly

    # There IS prefetch work: free the GPU so Whisper can run alongside the render
    # (Stage 7 = NVENC + CPU filters; the LLM is done after Stage 6).
    tdir.mkdir(parents=True, exist_ok=True)
    for m in dict.fromkeys([ctx.text_model, ctx.vision_model,
                            ctx.text_model_passb, ctx.vision_model_stage6]):
        common.unload_model(log, ctx.llm_url, m)

    prep_log = p.persistent_log_dir / f"prefetch_{stem}.log"

    def _work():
        try:
            p.persistent_log_dir.mkdir(parents=True, exist_ok=True)
            with open(prep_log, "w", encoding="utf-8") as lf:
                tmpwav = Path(tempfile.gettempdir()) / f"clipper_prefetch_{stem}.wav"
                subprocess.run(["ffmpeg", "-y", "-i", str(vod_path), "-vn", "-acodec",
                                "pcm_s16le", "-ar", "16000", "-ac", "1", str(tmpwav)],
                               stdout=lf, stderr=subprocess.STDOUT, timeout=1800)
                env = ctx.child_env()
                env["CLIP_WHISPER_MODEL"] = ctx.whisper_model
                env["VOD_BASENAME"] = next_vod_name
                if need_tx:
                    subprocess.run([sys.executable, str(p.lib_dir / "speech.py"),
                                    "--audio", str(tmpwav), "--out-json", str(cached_json),
                                    "--out-srt", str(cached_srt), "--vod", next_vod_name],
                                   cwd=str(p.repo_root), env=env, stdout=lf,
                                   stderr=subprocess.STDOUT, timeout=2400)
                if need_ev:
                    subprocess.run([sys.executable, str(p.lib_dir / "audio_events.py"),
                                    "--audio", str(tmpwav), "--out", str(cached_events)],
                                   cwd=str(p.repo_root), env=env, stdout=lf,
                                   stderr=subprocess.STDOUT, timeout=1800)
                tmpwav.unlink(missing_ok=True)
        except Exception as e:  # noqa: BLE001
            try:
                log.warn(f"C1 prefetch of '{stem}' failed ({e}) — next VOD transcribes inline")
            except Exception:
                pass

    t = threading.Thread(target=_work, name=f"prefetch-{stem}", daemon=True)
    t.start()
    log.log(f"C1: prefetching Stage 2 for next VOD '{next_vod_name}' during render "
            f"(isolated; prep log {prep_log.name}).")
    return t


def _execute_stages(ctx, log, after_stage6=None) -> int:
    """Run model verification + the 8 stages for one VOD. Returns an exit code.
    Catches PipelineExit (an intentional early stop) and returns its code.
    ``after_stage6`` (C1) fires once, right after Stage 6, while the LLM is idle."""
    import importlib
    t0 = time.time()
    try:
        if not ctx.list_mode:
            common.verify_models(log, ctx.llm_url, ctx.configured_models())
        for i in range(1, 9):
            importlib.import_module(f"pipeline.stages.stage{i}").run(ctx)
            if i == 6 and after_stage6 is not None:
                try:
                    after_stage6()
                except Exception as e:  # noqa: BLE001 - never let prefetch break the run
                    log.warn(f"C1 after-stage-6 hook failed: {e}")
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
            p.pid_file.name, p.done_file.name, p.vod_file.name}
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

    _news_stems: list[str] = []   # VODs completed this run (for CLIP_NEWS_AFTER)
    try:
        common.clear_vod()  # no stale batch marker from a previous run
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
            # C1: cross-VOD prefetch — DEFAULT ON (promoted 2026-07-09). Overlaps the next
            # VOD's Stage 2 (transcription+scan) with the current VOD's render window. Byte-safe
            # (prefetch audio-events proven byte-identical; isolated to cache + temp wav) and
            # contention-free: measured NVENC render 16.9 s alone vs 16.9 s during a concurrent
            # whisper job at 88% GPU util (+0.1%) — the NVENC encoder block and whisper's CUDA
            # compute don't contend. Saves ~5.6 min per VOD transition in a batch. Kill switch:
            # CLIP_BATCH_PREFETCH=0.
            prefetch_on = os.environ.get("CLIP_BATCH_PREFETCH", "1").strip().lower() not in (
                "0", "false", "no", "off", "")
            _pf = [None]  # holder for the in-flight prefetch thread (list -> closure-writable)
            # A3 (plan-speed-wave3): between-VOD LM Studio hygiene. The 07-13
            # batch decayed for ~6 h (S4 rate +70%) then died at hour 17.6 —
            # probe serving health before every VOD after the first, recycle the
            # server once on dead/heavy-decay, and abort CLEANLY (instead of
            # grinding 14 min of call timeouts) only if it stays dead.
            # Kill switch: CLIP_BATCH_LLM_PROBE=0.
            _probe_on = os.environ.get("CLIP_BATCH_LLM_PROBE", "1").strip().lower() in (
                "1", "true", "yes", "on")
            _probe_baseline = [None]

            def _llm_hygiene(vctx_) -> bool:
                """Probe (+ recycle once). Returns False only when the server
                stays dead after a recycle — the batch should stop cleanly."""
                common.load_model(log, vctx_.llm_url, vctx_.text_model, vctx_.context_length)
                lat = common.timed_probe(log, vctx_.llm_url, vctx_.text_model)
                if lat is None:
                    common.lms_server_restart(log)
                    common.load_model(log, vctx_.llm_url, vctx_.text_model, vctx_.context_length)
                    lat = common.timed_probe(log, vctx_.llm_url, vctx_.text_model)
                    if lat is None:
                        return False
                if _probe_baseline[0] is None:
                    _probe_baseline[0] = lat
                    log.log(f"[A3] LM Studio probe baseline: {lat:.1f}s")
                elif lat > 5.0 and lat > 3.0 * _probe_baseline[0]:
                    log.warn(f"[A3] LM Studio decay detected ({lat:.1f}s vs baseline "
                             f"{_probe_baseline[0]:.1f}s) — recycling the server...")
                    common.lms_server_restart(log)
                    common.load_model(log, vctx_.llm_url, vctx_.text_model, vctx_.context_length)
                    lat2 = common.timed_probe(log, vctx_.llm_url, vctx_.text_model)
                    log.log(f"[A3] post-recycle probe: "
                            f"{f'{lat2:.1f}s' if lat2 is not None else 'STILL DEAD'} — continuing.")
                else:
                    log.log(f"[A3] LM Studio probe: {lat:.1f}s (baseline {_probe_baseline[0]:.1f}s)")
                return True

            for i, vod_name in enumerate(targets):
                if i > 0:
                    if _pf[0] is not None:
                        # Bounded: guarantee the next VOD's cache is populated before it runs.
                        _pf[0].join(timeout=1800)
                        _pf[0] = None
                    _reset_work_artifacts(p)
                    # vctx here is the PREVIOUS iteration's (same models/url).
                    if _probe_on and not _llm_hygiene(vctx):
                        log.err(f"[A3] LM Studio still unresponsive after a server recycle — "
                                f"stopping the batch cleanly before '{vod_name}' "
                                f"({i}/{len(targets)} done; a re-run resumes from here).")
                        exit_code = exit_code or 2
                        break
                log.line(f"=== Clipping {vod_name} ({i + 1}/{len(targets)}) ===")
                # Dashboard "which VOD of how many" progress marker (batch runs).
                common.set_vod(vod_name, i + 1, len(targets))
                vctx = Ctx(argparse.Namespace(
                    style=args.style, vod=vod_name, type=args.type,
                    list=False, force=batch_force, all=False))
                vctx.log = log
                hook = None
                if prefetch_on and i + 1 < len(targets):
                    _next = targets[i + 1]
                    def hook(_nv=_next, _vc=vctx):
                        _pf[0] = _prefetch_stage2(_nv, p, _vc, log)
                rc = _execute_stages(vctx, log, after_stage6=hook)
                exit_code = rc or exit_code
                if rc == 0:
                    _news_stems.append(Path(vod_name).stem)
        else:
            exit_code = _execute_stages(ctx, log)
            if exit_code == 0 and ctx.target_vod:
                _news_stems.append(Path(ctx.target_vod).stem)

        # CLIP_NEWS_AFTER (owner 2026-07-11): the "also produce a news compilation
        # at the end of the run" toggle. Compiles ONE "Streamers Update" video from
        # the run's freshly-clipped VODs via news_compile.py (finished-clips
        # architecture — fast, no re-detection; A/B follows CLIP_AB_VARIANTS).
        # Failure-soft: a compile problem never changes the run's exit code.
        if (_news_stems and not ctx.list_mode
                and os.environ.get("CLIP_NEWS_AFTER", "").strip().lower()
                in ("1", "true", "yes", "on")):
            log.line(f"=== News compile ({len(_news_stems)} VOD"
                     f"{'s' if len(_news_stems) > 1 else ''}) ===")
            try:
                r = subprocess.run(
                    [sys.executable, str(p.repo_root / "scripts" / "news_compile.py"),
                     "--vods", ",".join(_news_stems)],
                    cwd=str(p.repo_root), timeout=1200,
                    capture_output=True, text=True)
                for ln in (r.stdout or "").splitlines()[-8:]:
                    log.log(f"  {ln}")
                if r.returncode != 0:
                    log.warn(f"news compile exited {r.returncode} (clips unaffected)")
            except Exception as ne:  # noqa: BLE001
                log.warn(f"news compile failed ({ne}) — clips unaffected")
    except Exception as e:  # noqa: BLE001
        exit_code = 1
        log.err(f"pipeline failed: {e}")
        import traceback
        log.write(traceback.format_exc())
    finally:
        common.clear_vod()   # batch complete — drop the per-VOD progress marker
        common.cleanup(log, persistent_log, exit_code, start_epoch)
        log.close()

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
