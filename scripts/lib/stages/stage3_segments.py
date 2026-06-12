"""Stage 3 — Segment Detection (5-type stream-window classification + profile build).

Extracted from scripts/clip-pipeline.sh as part of the modularization plan
(see AIclippingPipelineVault/wiki/concepts/modularization-plan.md, Phase A3).

Reads bash-interpolated config from environment variables:
    LLM_URL, TEXT_MODEL, STREAM_TYPE_HINT
"""
import json, os, re, sys, time
try:
    import urllib.request
except:
    pass

LLM_URL = os.environ["LLM_URL"]
TEXT_MODEL = os.environ["TEXT_MODEL"]
TEMP_DIR = os.environ.get("CLIP_WORK_DIR", "/tmp/clipper")
STREAM_TYPE_HINT = os.environ["STREAM_TYPE_HINT"]

with open(f"{TEMP_DIR}/transcript.json") as f:
    segments = json.load(f)

if not segments:
    print("No transcript. Defaulting to single just_chatting segment.", file=sys.stderr)
    with open(f"{TEMP_DIR}/segments.json", "w") as f:
        json.dump([{"start": 0, "end": 0, "type": "just_chatting"}], f)
    sys.exit(0)

max_time = max(s["end"] for s in segments)

# If user provided a stream type hint (e.g. "irl", "gaming"), use it as a bias
# Valid hints: gaming, irl, just_chatting, reaction, debate, variety
VALID_TYPES = ["gaming", "irl", "just_chatting", "reaction", "debate"]
hint_type = None
if STREAM_TYPE_HINT:
    hint_lower = STREAM_TYPE_HINT.lower().strip()
    # Map common aliases
    aliases = {"chatting": "just_chatting", "chat": "just_chatting", "variety": None,
               "react": "reaction", "reacting": "reaction", "game": "gaming",
               "outdoor": "irl", "outside": "irl", "travel": "irl", "cooking": "irl"}
    if hint_lower in VALID_TYPES:
        hint_type = hint_lower
    elif hint_lower in aliases:
        hint_type = aliases[hint_lower]
    if hint_type:
        print(f"Stream type hint: '{hint_type}' — will bias segment classification", file=sys.stderr)

# Chunk into windows for classification. Default 10 min (600s). Fix 1
# (2026-06-06): the window size is now a knob — CLIP_SEGMENT_CHUNK=300 gives
# finer granularity so a short off-type pocket (e.g. a 2-min debate inside a
# gaming stream) gets its own label instead of being absorbed, at ~2x the
# (cheap) classification calls. Default left at 600 on purpose (measure-first:
# moment detection is already type-agnostic, so this mainly sharpens chunk-
# sizing + Pass A thresholds — A/B 300 vs 600 via the env to decide).
# CLIP_SEGMENT_OVERLAP adds read-context to each window without overlapping the
# recorded (nominal, non-overlapping) segments. See detection-improvements-plan Fix 1.
SEGMENT_CHUNK = int(os.environ.get("CLIP_SEGMENT_CHUNK", "600") or "600")
SEGMENT_OVERLAP = int(os.environ.get("CLIP_SEGMENT_OVERLAP", "0") or "0")

# Segment-classification confidence (2026-06-12 — clipping-intelligence
# weakness #5; the rap-battle case's unguarded single point of failure). One
# word from the model routes ALL downstream Pass A weights/thresholds and
# Pass B prompts, with no confidence and no smoothing beyond same-type merge.
# CLIP_SEGMENT_VOTES>=2 classifies each window N times at temperature 0.7
# (the single-vote path keeps the deterministic 0.1 call), takes the majority,
# and records confidence = top_votes/N on the window. Low-confidence windows
# (< CLIP_SEGMENT_SMOOTH_BELOW, default 0.67) sandwiched between two
# neighbors that agree with each other get smoothed to the neighbor type —
# confidently-typed off-type pockets (Fix 1's whole point) are never touched.
# Default VOTES=1 = exactly the old behavior: no extra calls, no smoothing
# (a single deterministic vote gives no basis to overrule the model).
# Cost at N=3 on a 3-h VOD: ~36 extra 1-word classifications (~1 min).
try:
    SEGMENT_VOTES = max(1, int(os.environ.get("CLIP_SEGMENT_VOTES", "1") or "1"))
except ValueError:
    SEGMENT_VOTES = 1
SEGMENT_SMOOTH = os.environ.get("CLIP_SEGMENT_SMOOTH", "1").strip().lower() not in (
    "0", "false", "no", "off",
)
try:
    SEGMENT_SMOOTH_BELOW = float(os.environ.get("CLIP_SEGMENT_SMOOTH_BELOW", "0.67") or "0.67")
except ValueError:
    SEGMENT_SMOOTH_BELOW = 0.67
if SEGMENT_VOTES > 1:
    print(f"Segment classification: {SEGMENT_VOTES}-vote majority + confidence "
          f"(smooth below {SEGMENT_SMOOTH_BELOW}: {'on' if SEGMENT_SMOOTH else 'off'})",
          file=sys.stderr)


def classify_window(combined, hint_note, temperature):
    """One classification call for a window's condensed text. Returns the
    parsed type, or None on network/parse failure (the caller treats a None
    as a missing vote, and an all-None window falls back to just_chatting)."""
    # /no_think belt-and-suspenders: Qwen3.5-35B-A3B ignores chat_template_kwargs
    # in LM Studio, but honors the /no_think sentinel inside the message itself.
    # For pure classification there is zero upside to reasoning, so we force it
    # off both ways (no-op on 9B + Gemma where thinking is already disabled).
    prompt = f"""/no_think
Classify this livestream transcript chunk into exactly ONE type:
- gaming (gameplay talk, strategy, callouts, wins/losses, game events)
- irl (real life, outside, daily activities, eating, traveling)
- just_chatting (casual conversation, Q&A, chat interaction, stories, chill vibes)
- reaction (watching/reacting to videos, clips, news, content)
- debate (arguments, disagreements, controversial topics, heated discussion)
{hint_note}
Transcript:
{combined}

Respond with ONLY the single type name. Nothing else."""

    payload = json.dumps({
        "model": TEXT_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "temperature": temperature,
        # 6000 tokens: covers both Qwen3.5-35B-A3B (~1500–2500 reasoning) and
        # Gemma 4-26B-A4B (~3000–6000 reasoning, permanent thinking — both
        # /no_think and chat_template_kwargs are ignored, so we MUST budget
        # for the full reasoning + answer). 3000 was tight enough on Gemma
        # that the 19th-or-20th classification on a 3-hr VOD landed in
        # finish=length with no usable answer, raising mid-loop and killing
        # the heredoc under set -e. 1024 → defaults everything to
        # just_chatting on Qwen, which is the original symptom of this
        # token-starvation pattern.
        "max_tokens": 6000,
        "chat_template_kwargs": {"enable_thinking": False},
    }).encode()

    try:
        req = urllib.request.Request(
            f"{LLM_URL}/v1/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=180) as resp:
            result = json.loads(resp.read().decode())
            msg = result["choices"][0]["message"]
            raw = msg.get("content") or ""
            finish_reason = result["choices"][0].get("finish_reason", "?")
            reasoning_tokens = result.get("usage", {}).get(
                "completion_tokens_details", {}).get("reasoning_tokens", 0)
            if not raw:
                reasoning_content = str(msg.get("reasoning_content", ""))
                if finish_reason == "stop" and reasoning_content:
                    # Natural termination: model finished thinking, answer is in
                    # reasoning_content (35B ignores enable_thinking=False).
                    print(f"  Segment classify: reasoning_content fallback "
                          f"(finish=stop, reasoning_tokens={reasoning_tokens})",
                          file=sys.stderr)
                    raw = reasoning_content
                elif finish_reason == "length" and reasoning_content:
                    # Cut off mid-think: scan the last 600 chars of reasoning for
                    # a conclusion. Models often write their tentative answer
                    # ("this is gaming", "just_chatting content") near the end of
                    # reasoning before being truncated.
                    tail = reasoning_content[-600:]
                    print(f"  Segment classify: scanning tail of truncated reasoning "
                          f"(finish=length, reasoning_tokens={reasoning_tokens})",
                          file=sys.stderr)
                    raw = tail
                else:
                    r_preview = reasoning_content[:80]
                    print(f"  Segment classify: empty content (finish={finish_reason}, "
                          f"reasoning_tokens={reasoning_tokens}, preview={r_preview!r})",
                          file=sys.stderr)
            if raw:
                answer = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip().lower()
                # Extract the type from the response (model might add extra text)
                for t in ["gaming", "irl", "just_chatting", "reaction", "debate"]:
                    if t in answer:
                        return t
    except Exception as e:
        print(f"  Segment classification call failed: {e}", file=sys.stderr)
    return None


chunk_start = segments[0]["start"]
raw_segments = []

while chunk_start < max_time:
    chunk_end = chunk_start + SEGMENT_CHUNK
    chunk_segs = [s for s in segments
                  if s["start"] < chunk_end + SEGMENT_OVERLAP
                  and s["end"] > chunk_start - SEGMENT_OVERLAP]

    if not chunk_segs:
        chunk_start += SEGMENT_CHUNK
        continue

    # Build condensed text (limit to ~600 words for fast classification)
    chunk_texts = [s["text"] for s in chunk_segs]
    combined = " ".join(chunk_texts)
    words = combined.split()
    if len(words) > 600:
        combined = " ".join(words[:600])

    if len(words) < 10:
        # Too sparse to classify, default
        raw_segments.append({"start": int(chunk_start), "end": int(min(chunk_end, max_time)), "type": "just_chatting", "confidence": None})
        chunk_start += SEGMENT_CHUNK
        continue

    hint_note = ""
    if hint_type:
        hint_note = f"\nNote: This is likely a {hint_type} stream, but segments may vary. Classify based on actual content."

    # Single-vote path keeps the deterministic temperature 0.1 call (old
    # behavior); multi-vote uses 0.7 so the votes actually vary and the
    # majority carries signal.
    votes = []
    for _v in range(SEGMENT_VOTES):
        _vt = classify_window(combined, hint_note, 0.1 if SEGMENT_VOTES == 1 else 0.7)
        if _vt:
            votes.append(_vt)

    if votes:
        _counts = {}
        for _vt in votes:
            _counts[_vt] = _counts.get(_vt, 0) + 1
        # Majority; ties break toward the earliest vote (stable).
        seg_type = max(_counts, key=lambda k: (_counts[k], -votes.index(k)))
        confidence = round(_counts[seg_type] / len(votes), 2) if SEGMENT_VOTES > 1 else None
    else:
        print(f"  Segment classification failed at {int(chunk_start)}s (no usable votes); defaulting", file=sys.stderr)
        seg_type = "just_chatting"
        confidence = 0.0 if SEGMENT_VOTES > 1 else None

    raw_segments.append({"start": int(chunk_start), "end": int(min(chunk_end, max_time)), "type": seg_type, "confidence": confidence})
    _conf_str = f" (conf {confidence})" if confidence is not None else ""
    print(f"  {int(chunk_start)}s-{int(min(chunk_end, max_time))}s: {seg_type}{_conf_str}", file=sys.stderr)
    chunk_start += SEGMENT_CHUNK

# Boundary smoothing: flip a LOW-CONFIDENCE window sandwiched between two
# neighbors that agree with each other. Only fires when voting produced a
# confidence (VOTES>=2) — a single deterministic vote gives no basis to
# overrule the model, and confidently-typed off-type pockets (the entire
# point of Fix 1's finer windows) must never be flattened by their context.
if SEGMENT_SMOOTH and SEGMENT_VOTES > 1 and len(raw_segments) >= 3:
    for _i in range(1, len(raw_segments) - 1):
        _cur = raw_segments[_i]
        _prev_t = raw_segments[_i - 1]["type"]
        _next_t = raw_segments[_i + 1]["type"]
        _conf = _cur.get("confidence")
        if (_prev_t == _next_t and _cur["type"] != _prev_t
                and _conf is not None and _conf < SEGMENT_SMOOTH_BELOW):
            print(f"  Smoothing {_cur['start']}s-{_cur['end']}s: {_cur['type']} "
                  f"(conf {_conf}) -> {_prev_t} (agreeing neighbors)", file=sys.stderr)
            _cur["type"] = _prev_t
            _cur["smoothed"] = True

# Merge adjacent segments of the same type (a merged segment keeps the LOWEST
# member confidence — pessimistic, so downstream consumers can spot shaky spans)
merged_segments = []
for seg in raw_segments:
    if merged_segments and merged_segments[-1]["type"] == seg["type"]:
        merged_segments[-1]["end"] = seg["end"]
        _pc = merged_segments[-1].get("confidence")
        _sc = seg.get("confidence")
        if _pc is not None and _sc is not None:
            merged_segments[-1]["confidence"] = min(_pc, _sc)
        elif _pc is None:
            merged_segments[-1]["confidence"] = _sc
    else:
        merged_segments.append(dict(seg))

with open(f"{TEMP_DIR}/segments.json", "w") as f:
    json.dump(merged_segments, f, indent=2)

# Infer overall stream type from segment durations
type_durations = {}
for seg in merged_segments:
    t = seg["type"]
    dur = seg["end"] - seg["start"]
    type_durations[t] = type_durations.get(t, 0) + dur

total_dur = sum(type_durations.values()) or 1
dominant_type = max(type_durations, key=type_durations.get)
dominant_pct = type_durations[dominant_type] / total_dur * 100

# Build stream profile
stream_profile = {
    "dominant_type": dominant_type,
    "dominant_pct": round(dominant_pct, 1),
    "type_breakdown": {k: round(v / total_dur * 100, 1) for k, v in sorted(type_durations.items(), key=lambda x: -x[1])},
    "is_variety": dominant_pct < 60,
    "hint_used": hint_type or "none"
}

with open(f"{TEMP_DIR}/stream_profile.json", "w") as f:
    json.dump(stream_profile, f, indent=2)

# Print timeline
print(f"\nStream segment map ({len(merged_segments)} segments):", file=sys.stderr)
for seg in merged_segments:
    duration_min = (seg["end"] - seg["start"]) / 60
    start_min = seg["start"] / 60
    print(f"  {start_min:.0f}min - {start_min + duration_min:.0f}min: {seg['type']} ({duration_min:.0f} min)", file=sys.stderr)

print(f"\nStream profile: {dominant_type} ({dominant_pct:.0f}%)", file=sys.stderr)
if stream_profile["is_variety"]:
    print("  Variety stream detected — multiple segment types", file=sys.stderr)
for t, pct in stream_profile["type_breakdown"].items():
    print(f"  {t}: {pct}%", file=sys.stderr)

print(f"Segment detection complete: {len(merged_segments)} segments identified")
