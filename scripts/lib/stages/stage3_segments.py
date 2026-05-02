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
TEMP_DIR = "/tmp/clipper"
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

# Chunk into ~10 minute windows for classification
SEGMENT_CHUNK = 600  # 10 minutes
chunk_start = segments[0]["start"]
raw_segments = []

while chunk_start < max_time:
    chunk_end = chunk_start + SEGMENT_CHUNK
    chunk_segs = [s for s in segments if s["start"] < chunk_end and s["end"] > chunk_start]

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
        raw_segments.append({"start": int(chunk_start), "end": int(min(chunk_end, max_time)), "type": "just_chatting"})
        chunk_start += SEGMENT_CHUNK
        continue

    hint_note = ""
    if hint_type:
        hint_note = f"\nNote: This is likely a {hint_type} stream, but segments may vary. Classify based on actual content."

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
        "temperature": 0.1,
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

    seg_type = "just_chatting"  # default
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
                        seg_type = t
                        break
    except Exception as e:
        print(f"  Segment classification failed at {int(chunk_start)}s: {e}", file=sys.stderr)

    raw_segments.append({"start": int(chunk_start), "end": int(min(chunk_end, max_time)), "type": seg_type})
    print(f"  {int(chunk_start)}s-{int(min(chunk_end, max_time))}s: {seg_type}", file=sys.stderr)
    chunk_start += SEGMENT_CHUNK

# Merge adjacent segments of the same type
merged_segments = []
for seg in raw_segments:
    if merged_segments and merged_segments[-1]["type"] == seg["type"]:
        merged_segments[-1]["end"] = seg["end"]
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
