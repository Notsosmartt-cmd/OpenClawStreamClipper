#!/usr/bin/env python3
"""Stage 6 — Vision Enrichment (non-gatekeeping) + 6.5 camera-pan prep.
Port of stage6_vision.sh.

D6 (plan-speed-wave3, 2026-07-15): while ``stage6_vision.py`` enriches moments
on the GPU (vision LLM), a consumer thread watches for the per-moment
``enriched_<T>.json`` sidecars it emits and renders each finished clip
immediately — NVENC + CPU filters don't contend with the LLM (measured +0.1%),
and Stage 7's Whisper claim is gone since the A2′ master-slice captions.
Stage 7 then renders only whatever the consumer didn't get to. Failure-soft at
every step: any consumer error leaves that clip for Stage 7's normal path.
``CLIP_S6_S7_OVERLAP=0`` restores the strict sequential order.
"""
from __future__ import annotations

import json
import os
import threading

from pipeline import common


def _start_overlap_consumer(ctx, log):
    """Start the D6 render-as-enriched consumer. Returns (thread, stop_event,
    rendered_set) — join the thread AFTER stage6_vision exits, then hand
    ``rendered_set`` to Stage 7 via ``ctx.early_rendered``."""
    from pipeline.stages import stage7 as _s7  # lazy — sibling stage import

    p = ctx.paths

    # Pre-S6 A/B eligibility: rank by the judge-set raw_score over the same
    # input stage6_vision reads. Stage 6's A2 callback boosts can reorder the
    # final top-N in rare runs — accepted divergence (B variants are additive).
    try:
        _top_n = int(os.environ.get("CLIP_AB_VARIANTS_TOP_N", "5") or "5")
    except ValueError:
        _top_n = 5
    try:
        _hm = json.loads(p.hype_moments.read_text(encoding="utf-8"))
        _order = sorted(_hm, key=lambda m: m.get("raw_score", m.get("score", 0.0)) or 0.0,
                        reverse=True)
        ab_ok = {int(float(m.get("timestamp", -1))) for m in _order[:_top_n]}
    except Exception:
        ab_ok = set()

    # Resolve the encoder once (stage7 normally does this after its model
    # unload; NVENC is a separate ASIC — running it with the model loaded was
    # measured at +0.1% contention in the C1 A/B).
    _s7._ACTIVE_VENC = _s7._resolve_encoder(log)
    speed_vf = f"setpts=PTS/{ctx.clip_speed}" if ctx.clip_speed != "1.0" else "null"
    speed_af = (f"rubberband=tempo={ctx.clip_speed}:pitch={ctx.clip_speed}"
                if ctx.clip_speed != "1.0" else "")

    rendered: set = set()
    stop = threading.Event()

    def _consume_one(sc_path) -> None:
        m = json.loads(sc_path.read_text(encoding="utf-8"))
        row = _s7._row_from_moment(m)
        row["hook_variants"] = m.get("hook_variants") or []
        row["ab_eligible"] = int(float(m.get("timestamp", -1))) in ab_ok
        T = row["t"]
        # Pre-stage moment_<T>.json for the profile renderer (scored_moments.json
        # doesn't exist yet — _extract_moment honors the pre-staged file).
        p.work(f"moment_{T}.json").write_text(json.dumps(m, indent=2), encoding="utf-8")
        # Per-clip caption slice from the master transcript (single-window file).
        wf = p.work(f"clip_windows_{T}.json")
        wf.write_text(json.dumps({str(T): {"start": row["clip_start"],
                                           "duration": row["clip_duration"]}}),
                      encoding="utf-8")
        cap_env = ctx.child_env()
        cap_env["CLIP_WHISPER_MODEL"] = ctx.whisper_model
        cap_env["CLIP_WINDOWS_FILE"] = str(wf)
        common.run_module(log, "stages/stage7_transcribe.py", [], env=cap_env, check=False)
        if not p.work(f"clip_{T}.srt").exists():
            raise RuntimeError("caption slice missing — leaving for Stage 7")
        _s7._render_clip(ctx, row, speed_vf, speed_af)
        rendered.add(T)
        log.log(f"  [D6] early-rendered T={T} while Stage 6 continues")

    def _watch() -> None:
        seen: set = set()

        def _sweep() -> None:
            for f in sorted(p.work_dir.glob("enriched_*.json")):
                if f.name in seen:
                    continue
                seen.add(f.name)
                try:
                    _consume_one(f)
                except Exception as e:  # noqa: BLE001 — stage 7 is the safety net
                    log.warn(f"  [D6] early render skipped for {f.name}: {e}")

        while not stop.is_set():
            _sweep()
            stop.wait(2.0)
        _sweep()   # final drain after stage6_vision exits

    t = threading.Thread(target=_watch, name="d6-overlap-consumer", daemon=True)
    t.start()
    return t, stop, rendered


def run(ctx) -> None:
    log = ctx.log
    p = ctx.paths
    env = ctx.child_env()

    # Bump the stage marker before the (possibly slow) VRAM swap.
    common.set_stage(log, "Stage 6/8 — Vision Enrichment (loading model)")

    # Phase 5.1: swap Pass-B text model -> Stage-6 vision model only if different.
    # S4.5 (plan-s45-text-judge): when the text judge ran in stage 5 it already
    # performed this exact swap — don't unload/reload the 22 GB model again.
    if getattr(ctx, "s45_swapped", False):
        log.log("S4.5 text judge already swapped to the vision model — skipping VRAM swap")
    elif ctx.text_model_passb != ctx.vision_model_stage6:
        common.unload_model(log, ctx.llm_url, ctx.text_model_passb)
        common.load_model(log, ctx.llm_url, ctx.vision_model_stage6, ctx.context_length)
    else:
        log.log(f"Pass B text and Stage 6 vision models are the same "
                f"('{ctx.text_model_passb}') — skipping VRAM swap")

    # Stage 5.5 — Vision Judge (Plan 1.a): tournament re-rank of the Pass C
    # shortlist using the multimodal model just loaded above. Failure-soft
    # (check=False): on outage / too-few comparisons it leaves hype_moments.json
    # in Pass C order and Stage 6 proceeds unchanged.
    common.set_stage(log, "Stage 5.5/8 — Vision Judge (tournament re-rank)")
    log.log("=== Stage 5.5/8 — Vision Judge ===")
    common.run_module(log, "stages/stage5_5_judge.py", [], env=env, check=False)

    common.set_stage(log, "Stage 6/8 — Vision Enrichment")
    log.log("=== Stage 6/8 — Vision Enrichment ===")

    # Mirror of the Stage-4 fix (owner-observed both times): grounding's judge
    # tier falls back to CLIP_TEXT_MODEL — since B3 that's the 9B, so Stage 6's
    # cascade_check judge calls JIT-summoned a CPU-placed 9B alongside the 35B
    # (GPU offload 0 — no VRAM left). Pin every Stage-6 grounding judge to the
    # phase-resident vision model, same as the caption judge already is.
    env["CLIP_GROUNDING_JUDGE_MODEL"] = ctx.vision_model_stage6
    # BUG-74 audit closure (2026-07-15): cut_inference (jump-cuts lane) is the
    # last CLIP_TEXT_MODEL faller-backer, and D6 renders — where it fires when
    # enabled — run DURING this vision phase. Pin it process-wide (os.environ:
    # the D6 consumer's _render_clip children AND stage7 residual renders both
    # build their env from it) so enabling CLIP_JUMP_CUTS can't summon a ghost.
    os.environ["CLIP_CUT_MODEL"] = ctx.vision_model_stage6

    # D6 — render-as-enriched overlap (see module docstring).
    _overlap = os.environ.get("CLIP_S6_S7_OVERLAP", "1").strip().lower() in (
        "1", "true", "yes", "on")
    consumer = None
    if _overlap:
        try:
            # Purge sidecars a CRASHED earlier run may have left in the work
            # dir — at this point none can be legitimate (stage6_vision hasn't
            # started), and a stale one would render a ghost clip.
            for _stale in p.work_dir.glob("enriched_*.json"):
                _stale.unlink(missing_ok=True)
            env["CLIP_S6_SIDECARS"] = "1"
            consumer = _start_overlap_consumer(ctx, log)
            log.log("[D6] S6∥S7 overlap ACTIVE — clips render as their enrichment completes")
        except Exception as e:  # noqa: BLE001
            log.warn(f"[D6] overlap consumer unavailable ({e}) — sequential render")
            consumer = None

    try:
        common.run_module(log, "stages/stage6_vision.py", [], env=env, check=True)
    finally:
        if consumer is not None:
            _t, _stop, _rendered = consumer
            _stop.set()
            _t.join(timeout=1800)   # bounded: renders are minutes, not hours
            ctx.early_rendered = set(_rendered)
            if _rendered:
                log.log(f"[D6] {len(_rendered)} clip(s) rendered during Stage 6")

    scored = json.loads(p.scored_moments.read_text(encoding="utf-8")) if p.scored_moments.exists() else []
    log.log(f"Moments to render: {len(scored)} (all detected moments proceed to rendering)")

    # Stage 6.5 — Camera Pan Prep (optional).
    if ctx.camera_pan and ctx.framing == "camera_pan":
        common.set_stage(log, "Stage 6.5/8 — Camera Pan Prep")
        log.log("=== Stage 6.5/8 — Camera Pan Prep (face tracking) ===")
        common.run_module(log, "stages/stage6_5_campan.py", [], env=env, check=False)

    if not scored:
        log.warn("No moments to render (detection found nothing).")
        common.append_processed(p.processed_log, ctx.vod_basename, "no_moments", ctx.style)
        raise common.PipelineExit(0, json.dumps({"status": "no_moments", "clips": 0, "style": ctx.style}))
