#!/usr/bin/env python3
"""audio_sense.py — semantic audio-event sensing (the shared sensing layer).

Implements the verified stack from concepts/clip-forensics-research-2026-06:
a HYBRID audio-event sensor = PANNs CNN14 (fixed AudioSet backbone, framewise
temporal localization, MIT) + CLAP zero-shot (open describable meme-SFX vocab),
plus inaSpeechSegmenter music/speech segmentation. This is the reusable
dependency behind the offline clip-forensics tool AND (later, flag-gated) the
live pipeline's audio_events upgrade.

Design rules (repo conventions):
  * Offline-first, CPU default (optional CUDA), no cloud APIs.
  * LAZY imports — importing this module pulls in nothing heavy; each backend is
    imported only when first called.
  * FAILURE-SOFT — any missing model/dep/file yields [] (+ a one-line stderr
    note), never an exception that propagates. The two audio backends are
    independent: if CLAP is absent you still get PANNs events and vice-versa.
  * Commercial-safe picks only: panns_inference (MIT), transformers ClapModel /
    laion-clap (Apache/permissive — prefer over MS-CLAP, whose license is
    unconfirmed), inaSpeechSegmenter (MIT). Essentia (AGPL/NC) is deliberately
    NOT used; "suspenseful music" is a CLAP prompt instead.

Public API:
    sense_events(media, *, window_s=1.0, hop_s=0.5, labels=None, device=None,
                 max_duration_s=240.0, cache_path=None) -> list[dict]
        -> [{"t": float, "end": float, "label": str, "score": float,
             "source": "clap"|"panns"}, ...]  (sorted by t)

    music_segments(media, *, cache_path=None) -> list[dict]
        -> [{"start": float, "end": float, "kind": "speech"|"music"|"noise"}, ...]

CLI (quick check):
    python audio_sense.py --media path.mp4 [--no-cuda] [--window 1.0 --hop 0.5]
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


def _log(msg: str) -> None:
    print(f"[audio_sense] {msg}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Config (label vocab + thresholds) — three-tier load like the rest of the repo
# ---------------------------------------------------------------------------
_LABELS_CACHE: dict | None = None

_DEFAULT_LABELS: dict[str, Any] = {
    # CLAP raw audio-text cosines run LOW + uncalibrated (verified 2026-06-21:
    # top labels ~0.26-0.32 on real clips). 0.30 is a starting default — TUNE
    # per corpus against your reference_clips/.notes.json. Higher = fewer/cleaner.
    "clap_threshold": 0.30,
    "panns_threshold": 0.30,
    "clap_labels": [
        {"label": "boom", "prompt": "a deep bass boom impact sound effect, vine boom"},
        {"label": "scratch", "prompt": "a vinyl record scratch sound effect"},
        {"label": "applause", "prompt": "applause, people clapping and cheering"},
        {"label": "whoosh", "prompt": "a fast whoosh transition swoosh"},
        {"label": "riser", "prompt": "a rising tension riser build-up sweep"},
        {"label": "suspense_music", "prompt": "suspenseful tense dramatic background music"},
        {"label": "beep_censor", "prompt": "a censorship beep tone bleeping over speech"},
        {"label": "music", "prompt": "background music playing"},
        {"label": "laughter", "prompt": "people laughing"},
        {"label": "cheering", "prompt": "a crowd cheering and shouting"},
    ],
    "panns_keep": ["music", "speech", "laughter", "applause", "cheering", "crowd",
                   "whoosh", "boom", "explosion", "beep", "quack", "sound effect", "cartoon"],
}


def load_labels(path: str | None = None) -> dict:
    """Load config/audio_sense_labels.json (env -> Linux -> repo), back-filled
    from the in-code defaults. Cached when path is None."""
    global _LABELS_CACHE
    if path is None and _LABELS_CACHE is not None:
        return _LABELS_CACHE
    for c in (path, os.environ.get("CLIP_AUDIO_SENSE_LABELS"),
              "/root/.openclaw/audio_sense_labels.json",
              str(Path(__file__).resolve().parents[2] / "config" / "audio_sense_labels.json")):
        if c and os.path.exists(c):
            try:
                cfg = json.loads(Path(c).read_text(encoding="utf-8")) or {}
                break
            except (OSError, json.JSONDecodeError):
                cfg = {}
    else:
        cfg = {}
    merged = dict(_DEFAULT_LABELS)
    merged.update(cfg or {})
    if path is None:
        _LABELS_CACHE = merged
    return merged


# ---------------------------------------------------------------------------
# Audio extraction (ffmpeg -> float32 mono ndarray; no temp file / codec dep)
# ---------------------------------------------------------------------------
def _extract_audio(media: str, sr: int):
    import numpy as np
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-i", str(media),
           "-ac", "1", "-ar", str(sr), "-f", "f32le", "pipe:1"]
    p = subprocess.run(cmd, capture_output=True)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or b"").decode("utf-8", "replace")[-300:])
    return np.frombuffer(p.stdout, dtype=np.float32).copy()


def _resolve_device(device: str | None) -> str:
    if device:
        return device
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


def _merge_events(events: list[dict], dedup_s: float = 0.3) -> list[dict]:
    """Sort by time; drop a later event that duplicates the same label within
    dedup_s of one already kept (keeps the higher score)."""
    events = sorted(events, key=lambda e: (e["t"], -e.get("score", 0.0)))
    kept: list[dict] = []
    for e in events:
        dup = next((k for k in kept if k["label"] == e["label"]
                    and abs(k["t"] - e["t"]) < dedup_s), None)
        if dup is None:
            kept.append(e)
        elif e.get("score", 0) > dup.get("score", 0):
            dup.update(e)
    return sorted(kept, key=lambda e: e["t"])


# ---------------------------------------------------------------------------
# Backend 1 — PANNs CNN14 framewise sound-event detection (MIT)
# ---------------------------------------------------------------------------
_PANNS = {"sed": None, "labels": None}


def _panns_events(media: str, labels_cfg: dict, device: str) -> list[dict]:
    # PANNs is OPT-IN (CLIP_AUDIO_SENSE_PANNS=1). panns_inference 0.1.1 +
    # torchlibrosa STALLS during SoundEventDetection init on torch >= 2.9
    # (verified 2026-06-21: both CUDA and CPU, even with OMP guards) — and a
    # stall isn't catchable by try/except, so it would hang the whole tool.
    # CLAP covers the common classes (music/laughter/applause/beep/quack) via
    # prompts, so it's the default. Re-enable PANNs on a saner torch build.
    if os.environ.get("CLIP_AUDIO_SENSE_PANNS", "").strip().lower() not in ("1", "true", "yes", "on"):
        _log("PANNs disabled by default (CLIP_AUDIO_SENSE_PANNS=1 to enable; stalls on torch>=2.9). CLAP covers common classes.")
        return []
    try:
        import numpy as np
        import panns_inference
        from panns_inference import SoundEventDetection, labels as panns_labels
    except Exception as e:
        _log(f"PANNs unavailable ({type(e).__name__}); skipping AudioSet backend")
        return []
    try:
        if _PANNS["sed"] is None:
            _PANNS["sed"] = SoundEventDetection(checkpoint_path=None, device=device)
            _PANNS["labels"] = list(panns_labels)
        audio = _extract_audio(media, 32000)  # PANNs expects 32 kHz
        if audio.size == 0:
            return []
        dur = audio.size / 32000.0
        framewise = _PANNS["sed"].inference(audio[None, :])[0]  # (frames, 527)
        keep_sub = [s.lower() for s in (labels_cfg.get("panns_keep") or [])]
        thr = float(labels_cfg.get("panns_threshold", 0.30))
        names = _PANNS["labels"]
        n_frames = framewise.shape[0]
        if n_frames == 0:
            return []
        sec_per_frame = dur / n_frames
        keep_idx = [i for i, nm in enumerate(names)
                    if any(sub in nm.lower() for sub in keep_sub)] if keep_sub else list(range(len(names)))
        out: list[dict] = []
        for ci in keep_idx:
            col = framewise[:, ci]
            active = col >= thr
            i = 0
            while i < n_frames:
                if active[i]:
                    j = i
                    peak = float(col[i])
                    while j + 1 < n_frames and active[j + 1]:
                        j += 1
                        peak = max(peak, float(col[j]))
                    out.append({"t": round(i * sec_per_frame, 3),
                                "end": round((j + 1) * sec_per_frame, 3),
                                "label": names[ci], "score": round(peak, 3),
                                "source": "panns"})
                    i = j + 1
                else:
                    i += 1
        return out
    except Exception as e:
        _log(f"PANNs inference failed ({type(e).__name__}: {e}); skipping")
        return []


# ---------------------------------------------------------------------------
# Backend 2 — CLAP zero-shot over the open describable vocab (Apache/permissive)
# Prefer transformers ClapModel (laion checkpoint); fall back to laion_clap pkg.
# ---------------------------------------------------------------------------
_CLAP = {"backend": None, "model": None, "proc": None, "text_emb": None, "labels": None}
_CLAP_CKPT = os.environ.get("CLIP_CLAP_CKPT", "laion/clap-htsat-unfused")


def _clap_init(labels_cfg: dict, device: str) -> bool:
    if _CLAP["backend"] is not None:
        return _CLAP["backend"] != "none"
    label_defs = labels_cfg.get("clap_labels") or []
    prompts = [str(d.get("prompt") or d.get("label") or "") for d in label_defs]
    names = [str(d.get("label") or "") for d in label_defs]
    if not prompts:
        _CLAP["backend"] = "none"
        return False
    # Try HF transformers first.
    try:
        import torch
        from transformers import ClapModel, ClapProcessor
        model = ClapModel.from_pretrained(_CLAP_CKPT).to(device).eval()
        proc = ClapProcessor.from_pretrained(_CLAP_CKPT)
        with torch.no_grad():
            ti = proc(text=prompts, return_tensors="pt", padding=True).to(device)
            temb = model.get_text_features(**ti)
            temb = temb / temb.norm(dim=-1, keepdim=True)
        _CLAP.update(backend="hf", model=model, proc=proc, text_emb=temb, labels=names)
        _log(f"CLAP ready (transformers/{_CLAP_CKPT}) on {device}")
        return True
    except Exception as e:
        _log(f"CLAP transformers backend unavailable ({type(e).__name__}); trying laion_clap")
    # Fall back to laion_clap package.
    try:
        import numpy as np
        import laion_clap
        model = laion_clap.CLAP_Module(enable_fusion=False)
        model.load_ckpt()
        temb = model.get_text_embedding(prompts, use_tensor=False)
        temb = temb / (np.linalg.norm(temb, axis=-1, keepdims=True) + 1e-9)
        _CLAP.update(backend="laion", model=model, proc=None, text_emb=temb, labels=names)
        _log("CLAP ready (laion_clap)")
        return True
    except Exception as e:
        _log(f"CLAP unavailable ({type(e).__name__}); skipping open-vocab backend")
        _CLAP["backend"] = "none"
        return False


def _clap_events(media: str, labels_cfg: dict, device: str,
                 window_s: float, hop_s: float, max_duration_s: float) -> list[dict]:
    # Opt-out knob: CLAP's checkpoint is a large first-run download and the
    # per-window pass is the heavy part — set CLIP_AUDIO_SENSE_NO_CLAP=1 to run
    # PANNs-only (covers music/laughter/applause/beep/quack via AudioSet).
    if os.environ.get("CLIP_AUDIO_SENSE_NO_CLAP", "").strip().lower() in ("1", "true", "yes", "on"):
        _log("CLAP disabled via CLIP_AUDIO_SENSE_NO_CLAP; PANNs-only")
        return []
    if not _clap_init(labels_cfg, device):
        return []
    try:
        import numpy as np
        sr = 48000  # CLAP expects 48 kHz
        audio = _extract_audio(media, sr)
        if audio.size == 0:
            return []
        if audio.size / sr > max_duration_s:
            audio = audio[: int(max_duration_s * sr)]
        thr = float(labels_cfg.get("clap_threshold", 0.45))
        # Per-label threshold override (a quiet background music bed under speech
        # scores ~0.20-0.27, below the 0.30 SFX floor — see config _note).
        thr_by_name = {str(d.get("label")): float(d.get("threshold", thr))
                       for d in (labels_cfg.get("clap_labels") or [])}
        names = _CLAP["labels"]
        win = max(1, int(window_s * sr))
        hop = max(1, int(hop_s * sr))
        out: list[dict] = []
        starts = list(range(0, max(1, audio.size - win + 1), hop))
        for s0 in starts:
            chunk = audio[s0:s0 + win]
            if chunk.size < win // 2:
                continue
            sims = _clap_window_sims(chunk, sr)
            if sims is None:
                continue
            t0 = round(s0 / sr, 3)
            for li, sc in enumerate(sims):
                nm = names[li]
                if sc >= thr_by_name.get(nm, thr):
                    out.append({"t": t0, "end": round(t0 + window_s, 3),
                                "label": nm, "score": round(float(sc), 3),
                                "source": "clap"})
        return out
    except Exception as e:
        _log(f"CLAP inference failed ({type(e).__name__}: {e}); skipping")
        return []


def _clap_window_sims(chunk, sr):
    """Cosine sims between one audio window and the label text embeddings."""
    try:
        if _CLAP["backend"] == "hf":
            import torch
            with torch.no_grad():
                ai = _CLAP["proc"](audio=chunk, sampling_rate=sr, return_tensors="pt")
                ai = {k: v.to(_CLAP["model"].device) for k, v in ai.items()}
                aemb = _CLAP["model"].get_audio_features(**ai)
                aemb = aemb / aemb.norm(dim=-1, keepdim=True)
                sims = (aemb @ _CLAP["text_emb"].T).squeeze(0).cpu().numpy()
            return sims
        elif _CLAP["backend"] == "laion":
            import numpy as np
            aemb = _CLAP["model"].get_audio_embedding_from_data(x=chunk[None, :], use_tensor=False)
            aemb = aemb / (np.linalg.norm(aemb, axis=-1, keepdims=True) + 1e-9)
            return (aemb @ _CLAP["text_emb"].T).squeeze(0)
    except Exception as e:
        _log(f"CLAP window failed ({type(e).__name__}); aborting CLAP for this clip")
        _CLAP["backend"] = "none"
    return None


# ---------------------------------------------------------------------------
# Music / speech segmentation — inaSpeechSegmenter (MIT)
# ---------------------------------------------------------------------------
_INA = {"seg": None}


def music_segments(media: str, *, cache_path: str | None = None) -> list[dict]:
    """Speech/music/noise zones via inaSpeechSegmenter. [] on any failure."""
    if cache_path and os.path.exists(cache_path):
        try:
            return json.loads(Path(cache_path).read_text(encoding="utf-8"))
        except Exception:
            pass
    out: list[dict] = []
    try:
        from inaSpeechSegmenter import Segmenter
        if _INA["seg"] is None:
            _INA["seg"] = Segmenter()
        for label, start, end in _INA["seg"](str(media)):
            kind = "speech" if str(label).startswith(("male", "female", "speech")) else \
                   ("music" if "music" in str(label) else "noise")
            out.append({"start": round(float(start), 3), "end": round(float(end), 3), "kind": kind})
    except Exception as e:
        _log(f"music_segments unavailable ({type(e).__name__}); []")
        return []
    if cache_path and out:
        try:
            Path(cache_path).write_text(json.dumps(out), encoding="utf-8")
        except OSError:
            pass
    return out


# ---------------------------------------------------------------------------
# Phase 2 — word-level transcription (faster-whisper) for censor detection
# ---------------------------------------------------------------------------
_WHISPER: dict = {}


def transcribe_words(media: str, *, model_size: str | None = None,
                     device: str | None = None, cache_path: str | None = None) -> list[dict]:
    """Word-level transcript via faster-whisper. [{"word","start","end"}, ...].
    Defaults to a small model (CLIP_FORENSICS_WHISPER, default 'base') — censor
    detection needs word POSITIONS, not perfect ASR. Failure-soft -> []."""
    if cache_path and os.path.exists(cache_path):
        try:
            return json.loads(Path(cache_path).read_text(encoding="utf-8"))
        except Exception:
            pass
    try:
        from faster_whisper import WhisperModel
    except Exception as e:
        _log(f"faster-whisper unavailable ({type(e).__name__}); transcribe_words=[]")
        return []
    size = model_size or os.environ.get("CLIP_FORENSICS_WHISPER", "base")
    dev = _resolve_device(device)
    ctype = "float16" if dev == "cuda" else "int8"
    key = (size, dev, ctype)
    try:
        if key not in _WHISPER:
            _WHISPER[key] = WhisperModel(size, device=dev, compute_type=ctype)
        segs, _info = _WHISPER[key].transcribe(str(media), word_timestamps=True)
        out: list[dict] = []
        for s in segs:
            for w in (getattr(s, "words", None) or []):
                txt = (getattr(w, "word", "") or "").strip()
                if txt:
                    out.append({"word": txt, "start": round(float(w.start), 3),
                                "end": round(float(w.end), 3)})
        if cache_path and out:
            try:
                Path(cache_path).write_text(json.dumps(out), encoding="utf-8")
            except OSError:
                pass
        return out
    except Exception as e:
        _log(f"transcribe_words failed ({type(e).__name__}: {e}); []")
        return []


def onset_times(media: str, *, sr: int = 22050, min_strength: float = 1.0,
                hop: int = 512, min_gap_s: float = 0.2) -> list[float]:
    """Abrupt audio onsets (seconds) — used to flag music that STARTS on a cut
    (editor-added bed) vs fades in. Pure-numpy energy-flux peak picker: frame
    RMS -> positive first-difference -> local maxima above mean + k*std. No
    librosa/numba (librosa's onset path deadlocks on this Windows/torch env, and
    a hang isn't catchable; this stays fast + dependency-light). [] on failure."""
    try:
        import numpy as np
        y = _extract_audio(media, sr)
        n = y.size // hop
        if n < 3:
            return []
        rms = np.sqrt((y[: n * hop].reshape(n, hop) ** 2).mean(axis=1) + 1e-9)
        flux = np.clip(np.diff(rms, prepend=rms[0]), 0.0, None)
        thr = float(flux.mean() + float(min_strength) * (flux.std() or 1.0))
        out: list[float] = []
        last = -10.0
        for i in range(1, n - 1):
            if flux[i] >= thr and flux[i] >= flux[i - 1] and flux[i] >= flux[i + 1]:
                t = i * hop / sr
                if t - last >= min_gap_s:
                    out.append(round(t, 3))
                    last = t
        return out
    except Exception as e:
        _log(f"onset_times failed ({type(e).__name__}); []")
        return []


# ---------------------------------------------------------------------------
# Public: sense_events (CLAP + PANNs merged)
# ---------------------------------------------------------------------------
def sense_events(media: str, *, window_s: float = 1.0, hop_s: float = 0.5,
                 labels: dict | None = None, device: str | None = None,
                 max_duration_s: float = 240.0, cache_path: str | None = None) -> list[dict]:
    """Semantic audio-event timeline. Merges PANNs (common AudioSet classes,
    temporally localized) + CLAP zero-shot (open meme-SFX vocab). Always returns
    a list (possibly empty); never raises."""
    if cache_path and os.path.exists(cache_path):
        try:
            return json.loads(Path(cache_path).read_text(encoding="utf-8"))
        except Exception:
            pass
    if not media or not os.path.exists(media):
        _log(f"media not found: {media!r}; []")
        return []
    cfg = labels if labels is not None else load_labels()
    device = _resolve_device(device)
    events: list[dict] = []
    events += _panns_events(media, cfg, device)
    events += _clap_events(media, cfg, device, window_s, hop_s, max_duration_s)
    merged = _merge_events(events)
    if cache_path and merged:
        try:
            Path(cache_path).write_text(json.dumps(merged), encoding="utf-8")
        except OSError:
            pass
    return merged


def _cli() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Semantic audio-event sensing (CLAP + PANNs)")
    ap.add_argument("--media", required=True)
    ap.add_argument("--window", type=float, default=1.0)
    ap.add_argument("--hop", type=float, default=0.5)
    ap.add_argument("--no-cuda", action="store_true")
    ap.add_argument("--music", action="store_true", help="also run music/speech segmentation")
    args = ap.parse_args()
    dev = "cpu" if args.no_cuda else None
    ev = sense_events(args.media, window_s=args.window, hop_s=args.hop, device=dev)
    out: dict = {"audio_events": ev, "n_events": len(ev)}
    if args.music:
        out["music"] = music_segments(args.media)
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
