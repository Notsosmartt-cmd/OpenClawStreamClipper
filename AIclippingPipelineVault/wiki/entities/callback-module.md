---
title: "callbacks.py — Tier-2 M3 long-range callback detector"
type: entity
tags: [callbacks, sentence-transformers, faiss, embeddings, tier-2, m3, pass-b, module, stage-4, text]
sources: 1
updated: 2026-04-27
---

# `scripts/lib/callbacks.py`

Long-range setup-payoff detector using sentence-embedding cosine search + a small Pass-B' LLM judgment. Catches arcs that span minutes to hours — the canonical Lacy penthouse moment where a claim made early is contradicted later. Pass B's chunk-local view can't see those connections; this module surfaces them.

Introduced 2026-04-27 as Tier-2 M3 of the [[concepts/moment-discovery-upgrades]]. See [[concepts/callback-detection]] for the architectural picture.

---

## Pipeline

1. **Aggregate** transcript segments into ~30 s overlapping windows (single Whisper segments are too noisy for cosine).
2. **Embed** each window with `sentence-transformers/all-MiniLM-L6-v2` (90 MB, CPU, ~10 ms/segment), L2-normalized so inner-product = cosine.
3. **Index** with `faiss-cpu` (`IndexFlatIP`); falls back to numpy brute-force when FAISS isn't installed.
4. **For each top-K Pass-B candidate** (K = 20 by default), embed the ±15 s payoff window and search for setups that occurred ≥ 5 min earlier with cosine ≥ 0.6.
5. **Judge** each surviving pair with a small Pass-B' LLM call: "is this a real callback?" — gates false-positive semantic similarity (related topic ≠ ironic callback).
6. **Emit** callback moments with `category="callback"`, `cross_validated=True`, `1.5× score boost`.

---

## Wire point

Called once after Pass B writes `llm_moments.json`:

```python
import callbacks
callback_moments = callbacks.detect_callbacks(
    segments=segments,
    llm_moments=llm_moments,
    call_llm_fn=call_llm,
    cache_dir="/root/.cache/sentence-transformers",
)
llm_moments.extend(callback_moments)
```

Skipped when Pass B produced zero moments (nothing to anchor a payoff to) or when LM Studio is in network outage.

---

## Output shape

A callback moment looks like:

```json
{
  "timestamp": 4530,
  "score": 0.95,
  "preview": "earlier they bragged this was their penthouse, now off-screen voice exposes it isn't",
  "categories": ["callback", "controversial"],
  "primary_category": "callback",
  "source": "callback",
  "why": "Streamer pitched their course while bragging about 'their' penthouse 12 minutes ago — friend's voice now exposes that it's not theirs.",
  "clip_start": 4520,
  "clip_end": 4570,
  "callback_kind": "contradiction",
  "setup_time": 3795,
  "setup_text": "yeah this is my penthouse, this is what you get when you take my course...",
  "callback_cosine": 0.71,
  "cross_validated": true
}
```

`callback_kind` is one of `irony` / `contradiction` / `fulfillment` / `theme_return` (whatever the judge LLM returned).

---

## Tunable defaults

| Parameter | Default | Notes |
|---|---|---|
| `model_name` | `sentence-transformers/all-MiniLM-L6-v2` | tradeoff between size (90 MB) and quality |
| `top_k` | 20 | how many top Pass-B candidates to evaluate |
| `cosine_threshold` | 0.6 | minimum semantic similarity to even consider a setup |
| `min_gap` | 300 s | setup must precede payoff by ≥ 5 min (ignore same-chunk arcs) |
| `max_callbacks` | 5 | cap callbacks added per VOD |

---

## Graceful degradation

| Missing | Behavior |
|---|---|
| `sentence-transformers` not installed | `detect_callbacks()` returns `[]`, single stderr line |
| `faiss-cpu` not installed | falls back to numpy brute-force similarity |
| Embedding model fails to download/load | returns `[]` |
| LLM judge call fails per candidate | skips that pair, continues |

The pipeline catches ImportError + Exception around the call so a missing module is logged and never fatal.

---

## Cost

- Embedding ~360 windows for a 3-hour VOD: ~10-30 s on CPU
- FAISS build: <1 s
- Per-candidate payoff embed + search + judge: ~3-5 s × 20 candidates = ~1-2 min
- VRAM: 0 (everything CPU)

Total wall-time impact: ~1-2 min per VOD when active.

---

## CLI (inspector)

```bash
python3 scripts/lib/callbacks.py --transcript /tmp/clipper/transcript.json --top 10
```

Prints the top setup→payoff cosine pairs without involving the LLM judge — useful for tuning thresholds.

---

## Related

- [[concepts/callback-detection]] — architectural concept
- [[concepts/highlight-detection]] — Pass B/C context
- [[entities/grounding]] — separate post-Pass-B gate (still runs on callback moments' `why`)
- [[concepts/moment-discovery-upgrades]] — original spec (Tier 2 M3)
- [[entities/audio-events]] — sibling Tier-2 module (M2, librosa signals)
- [[entities/diarization]] — sibling Tier-2 module (M1, speaker labels)
- [[concepts/two-stage-passb]] — Tier-3 A1 sibling that also closes the long-range arc gap
