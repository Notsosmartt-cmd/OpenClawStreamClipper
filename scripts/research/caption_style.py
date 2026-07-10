#!/usr/bin/env python3
"""Phase 7.2 — learn the competitor CAPTION LANGUAGE style and emit a reusable profile.

Answers the owner's question "can the captioning language styling be learned and
applied to clips we generate?" YES, for the *language* (casing, slang, hook
phrasing, length) — reliably; NOT the visual styling (font/colour/position), which
burned-in OCR can't recover. This distiller:

  1. collects burned-in caption text from every cached `.cache/*.timeline.json`
     (the EasyOCR `captions.samples`),
  2. fuzzy-dedups the near-identical per-frame repeats and strips OCR/watermark
     noise (@handles, platform tokens, sub-3-char garble),
  3. computes local stats (casing ratio, word-length distribution, emoji presence,
     top slang tokens) — no LLM needed,
  4. asks the local LLM (LM Studio) ONCE to distil a voice profile from the pooled
     lines (the model sees through OCR garble to the underlying style),
  5. writes `config/caption_style.json` (enabled=false — the owner reviews first).

Stage 6 consumes it as FEW-SHOT voice examples in the hook/title prompt (see
stage6_vision._caption_style_fewshot), failure-soft + flag-gated. LM Studio down =>
a stats-only profile is still written (no LLM fields). Output is advisory; it never
blocks a render."""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve()
REPO = HERE.parents[2]
CACHE = REPO / "reference_clips" / ".cache"
OUT = REPO / "config" / "caption_style.json"
sys.path.insert(0, str(HERE.parent))
import clip_forensics as cf  # noqa: E402  (sets up LIB_DIR path + _llm_config)

# OCR/watermark noise to strip before learning language: platform tokens, @handles,
# the download-outro handle, and lone punctuation fragments. Kept deliberately small.
_NOISE_TOKEN = re.compile(
    r"(?i)\b(tiktok|shorts|reels|youtube|instagram|subscribe|follow)\b|@\w+|#\w+")
_STOP = set("the a an and or but to of in on for it its is are was were be been you "
            "your my me he she they we his her their this that with just like so not "
            "no yeah bro dont don't im i'm gonna got get go u ur".split())
_EMOJI = re.compile("[\U0001F000-\U0001FAFF\U00002600-\U000027BF]")

_VOWEL = re.compile(r"[aeiouAEIOU]")


def _looks_like_handle(tok: str) -> bool:
    """P1.5 — a token that is almost certainly an OCR'd watermark HANDLE, not
    real caption language. These poisoned the v1 profile's `frequent_tokens`
    (solereports, realstableronaldo, chubbyreports, kingflacous…). Heuristic:
    long, digit-bearing, or interior-caps 'compound' handles."""
    t = tok.strip()
    if len(t) >= 13:                       # handles are long compounds
        return True
    if any(c.isdigit() for c in t):        # e.g. alotttwlr, bn3dits
        return True
    # interior capital after the first char (camel/Pascal compound handle)
    if len(t) > 2 and any(c.isupper() for c in t[1:]):
        return True
    return False


def _looks_like_garble(tok: str) -> bool:
    """P1.5 — OCR garble, not a word: a run of 3+ identical letters (pyyyyyyy),
    or a vowel-less alphabetic blob (ngflacous, tktok)."""
    low = tok.lower()
    if re.search(r"(.)\1\1", low):         # 3+ repeated chars
        return True
    if len(low) >= 4 and low.isalpha() and not _VOWEL.search(low):
        return True
    return False


def _plausible_word(tok: str) -> bool:
    """A token that plausibly is real caption language (for ranking/curation)."""
    return (2 <= len(tok) <= 15 and not _looks_like_handle(tok)
            and not _looks_like_garble(tok))


def _plausible_score(line: str) -> float:
    """Fraction of a line's alpha tokens that look like real words — used to
    rank candidate lines highest-signal-first in the curation sheet."""
    toks = [t for t in re.findall(r"[A-Za-z']+", line)]
    if not toks:
        return 0.0
    return sum(1 for t in toks if _plausible_word(t)) / len(toks)


def _distinct_lines(samples: list[dict]) -> list[str]:
    """Collapse the per-frame OCR repeats into distinct caption lines, stripping
    watermark/handle noise and OCR fragments."""
    out: list[str] = []
    for s in samples:
        raw = (s.get("text") or "").strip()
        if not raw:
            continue
        cleaned = _NOISE_TOKEN.sub(" ", raw)
        cleaned = re.sub(r'["\']', " ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        # drop lines that are mostly OCR garble (too few real word chars)
        if len(re.sub(r"[^a-zA-Z ]", "", cleaned).replace(" ", "")) < 6:
            continue
        # fuzzy-dedup vs the previous kept line (per-frame drift is tiny)
        if out and _similar(cleaned.lower(), out[-1].lower()):
            continue
        out.append(cleaned)
    return out


def _similar(a: str, b: str) -> bool:
    """Cheap near-equality: token-set Jaccard >= 0.8 (per-frame OCR drift)."""
    sa, sb = set(a.split()), set(b.split())
    if not sa or not sb:
        return a == b
    return len(sa & sb) / len(sa | sb) >= 0.8


def collect() -> tuple[list[str], int]:
    """(distinct cleaned caption lines across the corpus, #clips that had captions)."""
    pool: list[str] = []
    clips = 0
    for tj in sorted(CACHE.glob("*.timeline.json")):
        try:
            tl = json.loads(tj.read_text(encoding="utf-8"))
        except Exception:
            continue
        caps = tl.get("captions")
        if not isinstance(caps, dict) or not caps.get("available"):
            continue
        lines = _distinct_lines(caps.get("samples") or [])
        if lines:
            clips += 1
            pool.extend(lines)
    # global fuzzy-dedup (same caption can recur across clips)
    uniq: list[str] = []
    for ln in pool:
        if not any(_similar(ln.lower(), u.lower()) for u in uniq):
            uniq.append(ln)
    return uniq, clips


def local_stats(lines: list[str]) -> dict:
    words = [w for ln in lines for w in ln.split()]
    lens = [len(ln.split()) for ln in lines]
    lens.sort()
    alpha = [w for w in words if any(c.isalpha() for c in w)]
    lower = sum(1 for w in alpha if w == w.lower())
    upper = sum(1 for w in alpha if w.isupper() and len(w) > 1)
    ratio = round(lower / len(alpha), 3) if alpha else None
    casing = ("lowercase-dominant" if ratio and ratio >= 0.85 else
              "all-caps-heavy" if upper and upper / max(1, len(alpha)) >= 0.3 else
              "mixed")
    toks = Counter(re.sub(r"[^a-z']", "", w.lower()) for w in words)
    # P1.5: exclude OCR'd watermark handles + garble so the learned "slang"
    # isn't the v1 profile's solereports/pyyyyyyy pollution.
    slang = [w for w, _ in toks.most_common(60)
             if w and len(w) >= 2 and w not in _STOP
             and not _looks_like_handle(w) and not _looks_like_garble(w)][:15]
    return {
        "casing": casing,
        "lowercase_ratio": ratio,
        "median_words": lens[len(lens) // 2] if lens else None,
        "p90_words": lens[int(len(lens) * 0.9)] if lens else None,
        "emoji_seen": any(_EMOJI.search(ln) for ln in lines),
        "frequent_tokens": slang,
    }


_PROMPT = """You are a short-form (TikTok/Shorts) caption-writing analyst. Below are
burned-in caption/overlay lines OCR'd from competitor clips in ONE creator's niche.
The text is NOISY (OCR errors, merged overlays, watermark scraps) — infer the
underlying WRITING VOICE, do not quote errors verbatim.

Caption lines:
{lines}

Local stats already computed: {stats}

Output ONLY JSON (no prose, no fences) with:
- "voice_summary": one sentence describing the caption writing voice
- "casing_rule": e.g. "all lowercase", "SCREAMING CAPS on the punchline", "sentence case"
- "slang_lexicon": array of slang/filler words this niche actually uses (from the lines)
- "hook_phrasings": array of 3-6 short hook/opening phrasings in this voice (<= 8 words each)
- "punctuation": short note (emoji use, ellipses, ALL CAPS, none)
- "per_category_tone": object mapping any of funny/hype/reactive/storytime/hot_take to a one-word tone, only for tones you can infer
Base everything on the lines. If a field can't be grounded, use an empty value."""


def synthesize(lines: list[str], stats: dict, *, timeout: float = 90.0) -> dict | None:
    try:
        import lmstudio  # noqa: E402  (LIB_DIR on path via clip_forensics)
    except Exception as e:
        print(f"[caption_style] lmstudio client unavailable ({type(e).__name__}); stats-only")
        return None
    model, url = cf._llm_config()
    sample = lines[:40]  # token-light: 40 distinct lines is plenty of voice signal
    prompt = _PROMPT.format(lines="\n".join(f"- {l}" for l in sample),
                            stats=json.dumps(stats, default=str))
    reply = lmstudio.chat(prompt, model=model, url=url, timeout=timeout, max_tokens=700)
    if not reply:
        print(f"[caption_style] LLM unreachable/empty (url={url}); stats-only")
        return None
    obj = lmstudio.loads_lenient(reply)  # tolerant of the qwen unterminated-string glitch
    if obj is None:
        print("[caption_style] reply not parseable even leniently; stats-only")
    return obj


def _arg(flag: str) -> str | None:
    """Value after `flag` in argv, or None."""
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return None


def write_review_sheet(path: str) -> int:
    """P1.5 — emit the highest-signal candidate caption lines for the owner to
    curate. Because OCR mangles even good hooks, the reliable path to a usable
    voice bank is a human keep/drop pass (not more auto-cleaning). Owner marks
    KEEP lines [x], then `--ingest-sheet` folds them into examples/hook_phrasings."""
    lines, n_clips = collect()
    if not lines:
        print("[caption_style] no OCR'd captions in cache — run clip_forensics --ocr first.")
        return 1
    ranked = sorted(lines, key=_plausible_score, reverse=True)
    top = [ln for ln in ranked if _plausible_score(ln) >= 0.5][:40] or ranked[:40]
    with open(path, "w", encoding="utf-8") as f:
        f.write("# Caption-voice curation sheet (P1.5) — from "
                f"{n_clips} clip(s), {len(lines)} distinct lines.\n")
        f.write("# Mark lines that read like a REAL creator's caption with [x]; leave [ ] to drop.\n")
        f.write("# Fix OCR typos inline if you like. Then:\n")
        f.write("#   python scripts/research/caption_style.py --ingest-sheet "
                f"{path} [--enable]\n\n")
        for ln in top:
            f.write(f"[ ] {ln}\n")
    print(f"[caption_style] wrote curation sheet -> {path} ({len(top)} candidates). "
          f"Mark [x] the good ones, then --ingest-sheet.")
    return 0


def ingest_sheet(path: str, enable: bool) -> int:
    """P1.5 — read owner-kept lines ([x]) back into the profile's curated
    examples/hook_phrasings. `--enable` flips enabled=true (the v1 blocker was
    that the profile was never good enough to enable; a curated bank is)."""
    kept: list[str] = []
    try:
        for raw in Path(path).read_text(encoding="utf-8").splitlines():
            s = raw.strip()
            if s.lower().startswith("[x]"):
                kept.append(s[3:].strip())
    except OSError as e:
        print(f"[caption_style] cannot read sheet {path}: {e}")
        return 1
    if not kept:
        print(f"[caption_style] no [x]-marked lines in {path}; nothing ingested.")
        return 1
    try:
        prof = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    except Exception:
        prof = {}
    prof["examples"] = kept[:12]
    prof["hook_phrasings"] = kept[:8]
    prof["_curated"] = True
    prof["_curated_count"] = len(kept)
    if enable:
        prof["enabled"] = True
    OUT.write_text(json.dumps(prof, indent=2), encoding="utf-8")
    print(f"[caption_style] ingested {len(kept)} curated line(s) -> {OUT} "
          f"(enabled={str(prof.get('enabled', False)).lower()}). "
          f"Stage 6 will use these as the voice few-shot when enabled.")
    return 0


def main() -> int:
    _sheet = _arg("--review-sheet")
    if _sheet is not None:
        return write_review_sheet(_sheet)
    _ingest = _arg("--ingest-sheet")
    if _ingest is not None:
        return ingest_sheet(_ingest, enable=("--enable" in sys.argv))

    lines, n_clips = collect()
    if not lines:
        print("[caption_style] no OCR'd captions in cache. Decompose clips with --ocr first "
              "(clip_forensics --clip X --ocr).")
        return 0
    # Generalization guard (owner 2026-07-05): preserve the existing profile's
    # `applies_to` channel scoping (and enabled flag) across regenerations, so a
    # corpus refresh never silently widens a niche voice to all channels.
    prev_applies, prev_enabled = [], False
    try:
        _prev = json.loads(OUT.read_text(encoding="utf-8"))
        prev_applies = _prev.get("applies_to") or []
        prev_enabled = bool(_prev.get("enabled"))
    except Exception:
        pass
    stats = local_stats(lines)
    print(f"[caption_style] {len(lines)} distinct caption lines from {n_clips} clip(s); "
          f"casing={stats['casing']} median_words={stats['median_words']}")
    voice = synthesize(lines, stats) if "--no-llm" not in sys.argv else None
    profile = {
        "version": 1,
        "_note": ("Learned caption-LANGUAGE voice (Phase 7.2). Consumed by Stage 6 as "
                  "FEW-SHOT examples in the hook/title prompt when enabled=true. "
                  "OCR-derived: language style is reliable; visual styling "
                  "(font/colour/position) is NOT captured. Review + set enabled=true to use."),
        "_applies_note": ("GENERALIZATION: `applies_to` scopes this voice to the channels/"
                          "niche it was learned FROM (substring match vs the VOD basename, "
                          "case-insensitive). Unknown channels get the neutral prompt. "
                          "Empty list = applies everywhere (explicit choice). Preserved "
                          "across regenerations."),
        "applies_to": prev_applies,
        "enabled": prev_enabled,
        "generated_from_clips": n_clips,
        "distinct_caption_lines": len(lines),
        "stats": stats,
        "examples": lines[:8],
        "caveats": "OCR-derived from burned-in overlays; may mix editor captions with "
                   "streamer/chat overlay text. Language reliable, visual styling not.",
    }
    if voice:
        profile.update({k: v for k, v in voice.items() if k in (
            "voice_summary", "casing_rule", "slang_lexicon", "hook_phrasings",
            "punctuation", "per_category_tone")})
        print(f"[caption_style] voice: {voice.get('voice_summary', '(none)')}")
    OUT.write_text(json.dumps(profile, indent=2), encoding="utf-8")
    print(f"[caption_style] wrote {OUT} (enabled={str(profile['enabled']).lower()}, "
          f"applies_to={profile['applies_to'] or 'ALL'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
