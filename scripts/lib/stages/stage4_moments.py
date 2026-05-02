"""Stage 4 — Moment Detection (Pass A keywords + Pass B LLM + Pass C merge/select).

Extracted from scripts/clip-pipeline.sh as part of the modularization plan
(see AIclippingPipelineVault/wiki/concepts/modularization-plan.md, Phase A1).

Reads bash-interpolated config from environment variables:
    LLM_URL, TEXT_MODEL, TEXT_MODEL_PASSB, CLIP_STYLE

Writes outputs to /tmp/clipper/ (TEMP_DIR), same as the original heredoc.
Behavior is byte-identical to the pre-extraction heredoc — only the
config-passing mechanism changed (bash interpolation → os.environ).
"""
import json, re, sys, time, os, math
try:
    import urllib.request
except:
    pass

LLM_URL = os.environ["LLM_URL"]
TEXT_MODEL = os.environ["TEXT_MODEL"]
TEXT_MODEL_PASSB = os.environ["TEXT_MODEL_PASSB"]
CLIP_STYLE = os.environ["CLIP_STYLE"]
TEMP_DIR = "/tmp/clipper"

# Two-tier grounding cascade (regex denylist + content overlap → main-model
# LLM judge). MiniCheck NLI and Lynx-8B were retired 2026-05-01; the same
# multimodal model the pipeline already loaded does the judge call now.
# Module lives in /root/scripts/lib (Dockerfile copies scripts/lib/ there).
# On import failure we fall back to NO gating — better to ship unfiltered
# than to fail the pipeline because of a lint layer.
sys.path.insert(0, "/root/scripts/lib")
try:
    import grounding as _grounding
    GROUNDING_DENYLIST = _grounding.load_denylist()
    GROUNDING_CONFIG = _grounding.load_grounding_config()
    _t2 = GROUNDING_CONFIG.get("tier_2", {}).get("enabled", False)
    _t3 = GROUNDING_CONFIG.get("tier_3", {}).get("enabled", False)
    print(
        f"[GROUND] Loaded denylist with {len(GROUNDING_DENYLIST)} categories "
        f"(Tier 2 enabled={_t2}, Tier 3 enabled={_t3})",
        file=sys.stderr,
    )
except Exception as _e:
    _grounding = None
    GROUNDING_DENYLIST = {}
    GROUNDING_CONFIG = {}
    print(f"[GROUND] grounding module unavailable ({_e}); proceeding without cascade", file=sys.stderr)

# Phase 2: load chat features for hard-event ground truth (sub_count,
# bit_count, raid_count, donation_count) consumed by the grounding cascade
# in Pass B + Stage 6. Burst/emote scoring was removed 2026-05-01 — chat
# is latent vs. the moment, so timing-based scoring biased Pass A toward
# the previous keyword cluster. Hard event counts stay because they're
# factual, not timing-based, and they kill the "gifted subs" hallucination
# class.
CHAT_FEATURES = None
try:
    with open(f"{TEMP_DIR}/chat_available.txt") as _cf:
        _chat_available = _cf.read().strip() == "true"
except Exception:
    _chat_available = False
if _chat_available:
    try:
        import chat_features as _chat_feat
        with open(f"{TEMP_DIR}/chat_path.txt") as _cp:
            _chat_path = _cp.read().strip()
        CHAT_FEATURES = _chat_feat.load(_chat_path)
        print(
            f"[CHAT] loaded {CHAT_FEATURES.message_count} msgs across "
            f"{CHAT_FEATURES.duration_sec}s; hard-event ground truth ENABLED",
            file=sys.stderr,
        )
        if CHAT_FEATURES.is_empty():
            CHAT_FEATURES = None
    except Exception as _ce:
        CHAT_FEATURES = None
        print(f"[CHAT] chat_features unavailable ({_ce}); grounding running without hard-event check", file=sys.stderr)
else:
    print("[CHAT] no chat data available for this VOD — grounding cascade runs without hard-event check", file=sys.stderr)

# Tier-2 M2: load audio-event windows (rhythmic / crowd / music). Empty dict
# when the scanner skipped (no librosa, cached re-clip, etc.) — Pass A
# keyword_scan handles that with a 0.0 fallback.
AUDIO_EVENTS = {}
try:
    import audio_events as _audio_events_mod
    AUDIO_EVENTS = _audio_events_mod.load_events(f"{TEMP_DIR}/audio_events.json")
    if AUDIO_EVENTS:
        print(
            f"[AUDIO_EVENTS] loaded {len(AUDIO_EVENTS)} window samples; "
            f"Pass A audio-signal boost ENABLED",
            file=sys.stderr,
        )
    else:
        print("[AUDIO_EVENTS] no event data available — Pass A runs without audio signals", file=sys.stderr)
except Exception as _ae:
    print(f"[AUDIO_EVENTS] loader unavailable ({_ae}); proceeding without audio signals", file=sys.stderr)

# Tier-4 Phase 4.2 — conversation_shape (turn graph + discourse markers +
# off-screen intrusions + monologue runs + topic boundaries). Computed per
# Pass B chunk and serialized into the prompt. Stdlib only — degrades cleanly
# when M1 diarization isn't loaded.
CONVO_SHAPE = None
CONVO_MARKERS = None
try:
    import conversation_shape as _conv_shape
    CONVO_MARKERS = _conv_shape.load_discourse_markers()
    CONVO_SHAPE = _conv_shape
    print(
        f"[CONVO] conversation_shape loaded ({sum(len(v) for v in CONVO_MARKERS.values())} discourse-marker patterns)",
        file=sys.stderr,
    )
except Exception as _ce:
    print(f"[CONVO] conversation_shape unavailable ({_ce}); Pass B running without shape signals", file=sys.stderr)

# Tier-4 Phase 4.3 — Pattern Catalog. Loaded once, serialized into Pass B
# prompt. User-editable in config/patterns.json (mounted at /root/.openclaw).
PATTERN_CATALOG = []
try:
    _pat_path = "/root/.openclaw/patterns.json"
    if not os.path.exists(_pat_path):
        _pat_path = "/root/scripts/lib/../../config/patterns.json"
    with open(_pat_path) as _pf:
        _pat_raw = json.load(_pf)
    PATTERN_CATALOG = list(_pat_raw.get("patterns") or [])
    print(f"[PATTERNS] loaded {len(PATTERN_CATALOG)} interaction patterns", file=sys.stderr)
except Exception as _pe:
    print(f"[PATTERNS] catalog unavailable ({_pe}); Pass B will fall back to legacy 6-rule prompt", file=sys.stderr)

PATTERN_IDS = {p["id"] for p in PATTERN_CATALOG if isinstance(p, dict) and p.get("id")}


def _serialize_pattern_catalog_for_prompt(catalog):
    """Render the Pattern Catalog as a numbered list for the Pass B prompt.
    Each entry: id + label + signature. Examples are omitted from the prompt
    body to keep token budget tight; the LLM gets the structural definition
    and is asked to match against it."""
    if not catalog:
        return ""
    lines = []
    for i, p in enumerate(catalog, start=1):
        if not isinstance(p, dict):
            continue
        pid = p.get("id", "")
        label = p.get("label", pid)
        sig = (p.get("signature") or "").strip()
        signals = p.get("structural_signals") or []
        sig_str = ", ".join(signals) if signals else ""
        line = f"{i}. {pid} — {label}\n   Signature: {sig}"
        if sig_str:
            line += f"\n   Structural signals: {sig_str}"
        lines.append(line)
    return "\n".join(lines)


PATTERN_CATALOG_PROMPT = _serialize_pattern_catalog_for_prompt(PATTERN_CATALOG)

with open(f"{TEMP_DIR}/transcript.json") as f:
    segments = json.load(f)

with open(f"{TEMP_DIR}/segments.json") as f:
    segment_map = json.load(f)

if not segments:
    print("No transcript segments. Exiting.")
    with open(f"{TEMP_DIR}/hype_moments.json", "w") as f:
        json.dump([], f)
    sys.exit(0)

max_time = max(s["end"] for s in segments)
vod_hours = max_time / 3600.0

# Dynamic clip target: 2-4 clips per hour, min 3, max 20
TARGET_PER_HOUR = 3
MAX_CLIPS = max(3, min(int(math.ceil(vod_hours * TARGET_PER_HOUR)), 20))
# Allow more candidates through detection to feed into scoring/filtering
MAX_CANDIDATES = MAX_CLIPS * 2

print(f"VOD: {vod_hours:.1f} hours => target {MAX_CLIPS} clips (max {MAX_CANDIDATES} candidates)", file=sys.stderr)

def get_segment_type(timestamp):
    """Return the stream segment type for a given timestamp."""
    for seg in segment_map:
        if seg["start"] <= timestamp <= seg["end"]:
            return seg["type"]
    # Fallback: find closest
    if segment_map:
        closest = min(segment_map, key=lambda s: abs(s["start"] - timestamp))
        return closest["type"]
    return "just_chatting"

# ==============================================================
# PASS A — Segment-Aware Keyword Scanner (instant, no LLM)
# ==============================================================
print("[PASS A] Segment-aware keyword scan...", file=sys.stderr)

KEYWORD_SETS = {
    "hype": [
        "oh my god", "no way", "clip that", "let's go", "holy shit",
        "what the fuck", "no no no", "yes yes yes", "did you see that",
        "i can't believe", "lmao", "lmfao", "hahaha", "let's gooo",
        "insane", "unbelievable", "clutch", "oh shit", "poggers", "pog",
        "that was crazy", "oh my", "yoooo", "sheeeesh", "banger",
        "w stream", "dub", "we won", "massive", "legendary"
    ],
    "funny": [
        "i'm dead", "bruh", "that's so bad", "why would you", "bro what",
        "dude", "i can't", "stop", "help", "no he didn't", "she didn't",
        "what is that", "that's crazy", "are you serious", "you're trolling",
        "lol", "haha", "i'm crying", "that's hilarious", "comedy",
        "wait what", "bro", "nah", "ain't no way", "i'm wheezing",
        "i can't breathe", "that's so funny", "you did not", "caught in 4k",
        "sus", "down bad", "violated", "cooked", "finished"
    ],
    "emotional": [
        "i love you", "thank you so much", "that means a lot", "i appreciate",
        "i'm sorry", "it's been hard", "i just want to say", "you guys are",
        "honestly", "real talk", "from the bottom of my heart", "grateful",
        "miss you", "struggling", "mental health", "tough time",
        "i needed that", "means the world", "i can't thank you enough",
        "i'm gonna cry", "that hit different", "vulnerable", "opening up",
        "depression", "anxiety", "been through a lot", "love you guys"
    ],
    "hot_take": [
        "hot take", "i don't care what anyone says", "fight me", "unpopular opinion",
        "this is gonna be controversial", "here's the thing", "wrong",
        "that's not okay", "cancel", "problematic", "woke", "based",
        "ratio", "cope", "you're wrong", "nobody wants to hear this",
        "i said what i said", "don't @ me", "hear me out", "controversial",
        "honestly though", "people don't want to hear", "the truth is",
        "i'll say it", "no one talks about", "overrated", "underrated",
        "mid", "trash take", "delusional"
    ],
    "storytime": [
        "so basically", "let me tell you", "you won't believe", "long story short",
        "so this happened", "i was at", "the craziest thing", "true story",
        "one time", "back when", "so i was", "this one time", "i remember when",
        "what happened was", "the other day", "story time", "gather around",
        "you want to know", "let me explain", "so get this",
        "i gotta tell you", "the wildest thing", "not gonna lie",
        "you're not gonna believe this", "so picture this", "fun fact"
    ],
    "reactive": [
        "what is wrong with", "are you kidding", "i'm so done", "this is unacceptable",
        "this is ridiculous", "i'm pissed", "rage", "tilted", "so annoying",
        "sick of this", "how is this fair", "broken", "scam", "garbage",
        "worst", "terrible", "disgusting", "why does this always",
        "makes my blood boil", "actually insane", "look at this",
        "did you just see", "watch this", "hold on", "pause",
        "excuse me", "what did i just", "absolutely not", "hell no",
        "i'm shaking", "trembling", "speechless"
    ],
    "dancing": [
        "dance", "dancing", "twerk", "moves", "hit that", "do it",
        "go go go", "get it", "vibe", "vibing", "groove", "grooving",
        "bust a move", "let's dance", "song", "turn up", "body roll",
        "choreo", "choreography", "performing", "the dance"
    ],
    "controversial": [
        "drama", "beef", "called out", "exposed", "receipts", "caught",
        "tea", "spill", "shade", "throwing shade", "shots fired",
        "that's cap", "lying", "fake", "two-faced", "snake",
        "banned", "canceled", "cancelled", "suspended", "kicked",
        "he said she said", "clipped out of context", "oh hell no"
    ]
}

# Segment-specific keyword weight multipliers
# Boosts keywords that are natural for that segment type
SEGMENT_KEYWORD_WEIGHTS = {
    "gaming":       {"hype": 1.5, "funny": 1.0, "emotional": 0.8, "hot_take": 0.7, "storytime": 0.6, "reactive": 1.0, "dancing": 0.4, "controversial": 0.6},
    "irl":          {"hype": 0.8, "funny": 1.4, "emotional": 1.4, "hot_take": 1.0, "storytime": 1.3, "reactive": 0.8, "dancing": 1.5, "controversial": 1.0},
    "just_chatting": {"hype": 0.7, "funny": 1.3, "emotional": 1.3, "hot_take": 1.4, "storytime": 1.5, "reactive": 0.8, "dancing": 1.2, "controversial": 1.3},
    "reaction":     {"hype": 1.0, "funny": 1.2, "emotional": 0.8, "hot_take": 1.5, "storytime": 0.6, "reactive": 1.5, "dancing": 0.5, "controversial": 1.4},
    "debate":       {"hype": 0.7, "funny": 0.8, "emotional": 1.0, "hot_take": 1.5, "storytime": 0.8, "reactive": 1.3, "dancing": 0.3, "controversial": 1.5},
}

# Keyword thresholds — raised to reduce false positives from overused keywords
# A single "bruh" or "oh my god" is NOT enough — need multiple signals converging
SEGMENT_THRESHOLD = {
    "gaming": 3,
    "irl": 2,
    "just_chatting": 2,
    "reaction": 3,
    "debate": 2,
}

def keyword_scan(segments):
    """Segment-aware keyword scan with dynamic thresholds."""
    WINDOW_SIZE = 30
    STEP = 10
    flagged = []

    if not segments:
        return flagged

    max_time = max(s["end"] for s in segments)
    t = segments[0]["start"]

    while t < max_time:
        window_start = t
        window_end = t + WINDOW_SIZE
        window_segs = [s for s in segments if s["start"] < window_end and s["end"] > window_start]

        if window_segs:
            seg_type = get_segment_type(window_start + WINDOW_SIZE / 2)
            weights = SEGMENT_KEYWORD_WEIGHTS.get(seg_type, {})
            threshold = SEGMENT_THRESHOLD.get(seg_type, 2)

            texts = [s["text"] for s in window_segs]
            combined = " ".join(texts).lower()
            categories_found = {}
            total_signals = 0.0

            # Category-specific keyword matching with segment weights
            for cat, phrases in KEYWORD_SETS.items():
                cat_signals = 0
                for phrase in phrases:
                    if phrase in combined:
                        cat_signals += 1
                if cat_signals > 0:
                    weight = weights.get(cat, 1.0)
                    weighted = cat_signals * weight
                    categories_found[cat] = weighted
                    total_signals += weighted

            # Universal signals
            excl_count = sum(1 for t_text in texts if t_text.endswith("!") or "!!" in t_text)
            if excl_count >= 2:
                total_signals += 1
                categories_found["hype"] = categories_found.get("hype", 0) + 1

            # ALL CAPS streaks
            for t_text in texts:
                words = t_text.split()
                caps_streak = 0
                for w in words:
                    if w.isupper() and len(w) > 1:
                        caps_streak += 1
                        if caps_streak >= 3:
                            total_signals += 1
                            categories_found["hype"] = categories_found.get("hype", 0) + 1
                            break
                    else:
                        caps_streak = 0

            # Rapid fire short sentences
            short_count = sum(1 for t_text in texts if len(t_text.split()) < 5 and len(t_text) > 0)
            if short_count >= 4:
                total_signals += 1

            # Laughter markers
            if any(m in combined for m in ["[laughter]", "hahaha", "lmfao", "lmao"]):
                total_signals += 1
                categories_found["funny"] = categories_found.get("funny", 0) + 1

            # Question clusters (debate/engagement)
            question_count = sum(1 for t_text in texts if "?" in t_text)
            if question_count >= 3:
                total_signals += 1
                categories_found["controversial"] = categories_found.get("controversial", 0) + 1

            # Long pause then burst (emotional/dramatic)
            if len(window_segs) >= 3:
                gaps = []
                for i in range(1, len(window_segs)):
                    gap = window_segs[i]["start"] - window_segs[i-1]["end"]
                    gaps.append(gap)
                if any(g > 3.0 for g in gaps):
                    total_signals += 1
                    categories_found["emotional"] = categories_found.get("emotional", 0) + 1

            # Multi-category bonus
            if len(categories_found) >= 2:
                total_signals += 1

            # Tier-2 M1: speaker-change boost. When diarization is on AND the
            # window contains 2+ speakers AND no single speaker dominates
            # (>70% of audio), it's a multi-voice event — friend interjection,
            # off-screen voice, banter — exactly the pattern that catches
            # Lacy-style "caught lying" moments. Records dominant_speaker /
            # speaker_count on the moment so Pass C and Stage 6 can use it.
            window_dom_speaker = None
            window_speaker_count = 0
            window_dom_share = 1.0
            speakers_present = [s.get("speaker") for s in window_segs if s.get("speaker")]
            if speakers_present:
                from collections import Counter as _Counter
                speaker_durations = {}
                for _s in window_segs:
                    sp = _s.get("speaker")
                    if not sp:
                        continue
                    speaker_durations[sp] = speaker_durations.get(sp, 0.0) + max(
                        0.0, float(_s.get("end", 0)) - float(_s.get("start", 0))
                    )
                if speaker_durations:
                    total_speaker_dur = sum(speaker_durations.values()) or 1.0
                    window_dom_speaker, dom_dur = max(
                        speaker_durations.items(), key=lambda kv: kv[1]
                    )
                    window_dom_share = dom_dur / total_speaker_dur
                    window_speaker_count = len(speaker_durations)
                    if window_speaker_count >= 2 and window_dom_share < 0.7:
                        total_signals += 1
                        categories_found["funny"] = categories_found.get("funny", 0) + 1
                        categories_found["controversial"] = categories_found.get("controversial", 0) + 1

            # Tier-4 Phase 4.2: conversation-shape boost-only signals. Cheap
            # regex-based discourse markers + off-screen voice intrusions. All
            # additive caps so the worst case is "the shape extractor missed
            # something" — never gates a moment.
            if CONVO_SHAPE is not None:
                try:
                    _shape = CONVO_SHAPE.analyze_chunk(
                        window_segs, float(window_start), float(window_end),
                        markers=CONVO_MARKERS,
                    )
                except Exception:
                    _shape = None
                if _shape:
                    _intrusions = _shape.get("off_screen_intrusions") or []
                    if _intrusions:
                        # Lacy-penthouse signal — strong cap.
                        total_signals += min(0.5 * len(_intrusions), 1.0)
                        categories_found["controversial"] = categories_found.get("controversial", 0) + 0.5
                    by_class = {}
                    for _m in _shape.get("discourse_markers") or []:
                        by_class[_m.get("class")] = by_class.get(_m.get("class"), 0) + 1
                    if by_class.get("pushback", 0):
                        total_signals += 0.3
                        categories_found["controversial"] = categories_found.get("controversial", 0) + 0.3
                    if by_class.get("story_opener", 0):
                        # Only credit if a long-enough monologue follows in the window.
                        _runs = _shape.get("monologue_runs") or []
                        if any(r.get("duration_s", 0) >= 15 for r in _runs):
                            total_signals += 0.5
                            categories_found["storytime"] = categories_found.get("storytime", 0) + 0.5
                    if by_class.get("claim_stake", 0) and window_speaker_count >= 2:
                        total_signals += 0.3

            # Tier-2 M2: audio-event boost (rhythmic speech / crowd response /
            # music dominance). Boost-only — every signal is purely additive,
            # so the worst case is "the audio scanner missed something" and
            # current behavior is preserved. Thresholds match the plan: 0.7
            # for rhythmic (tight regularity), 0.5 for crowd (clear spike +
            # laughter spectrum), 0.6 for music (HPSS percussive ratio).
            if AUDIO_EVENTS:
                _ae = _audio_events_mod.lookup_window(
                    AUDIO_EVENTS, window_start, WINDOW_SIZE,
                )
                if _ae["rhythmic_speech"] >= 0.7:
                    total_signals += 1
                    categories_found["dancing"] = categories_found.get("dancing", 0) + 1
                    categories_found["hype"] = categories_found.get("hype", 0) + 1
                if _ae["crowd_response"] >= 0.5:
                    total_signals += 1
                    categories_found["funny"] = categories_found.get("funny", 0) + 1
                    categories_found["hype"] = categories_found.get("hype", 0) + 1
                if _ae["music_dominance"] >= 0.6:
                    total_signals += 1
                    categories_found["dancing"] = categories_found.get("dancing", 0) + 1

            # Use segment-specific threshold
            if total_signals >= threshold:
                center = window_start + WINDOW_SIZE / 2
                top_cat = max(categories_found, key=categories_found.get) if categories_found else "hype"
                # Normalize to 0.0-1.0: threshold is floor (0.0), 10+ signals is ceiling (1.0)
                # Use sigmoid-like curve so diminishing returns above ~6 signals
                raw = total_signals - threshold  # signals above threshold
                max_meaningful = 8.0  # signals above threshold that maps to ~1.0
                norm_score = min(raw / max_meaningful, 1.0)
                # Apply slight S-curve for better spread in the middle range
                norm_score = norm_score ** 0.8  # compress top, expand bottom
                flagged.append({
                    "timestamp": round(center),
                    "score": round(norm_score, 3),
                    "preview": " ".join(s["text"] for s in window_segs[:3])[:120],
                    "categories": list(categories_found.keys()),
                    "primary_category": top_cat,
                    "source": "keyword",
                    "segment_type": seg_type,
                    # Tier-2 M1: speaker context propagated to Pass C.
                    "dominant_speaker": window_dom_speaker,
                    "speaker_count": window_speaker_count,
                    "dominant_speaker_share": round(window_dom_share, 3) if speakers_present else None,
                })

        t += STEP

    # Merge overlapping (within 20s)
    merged = []
    for moment in sorted(flagged, key=lambda x: x["timestamp"]):
        if merged and abs(moment["timestamp"] - merged[-1]["timestamp"]) < 20:
            if moment["score"] > merged[-1]["score"]:
                merged[-1] = moment
        else:
            merged.append(moment)

    return merged

keyword_moments = keyword_scan(segments)
print(f"[PASS A] Found {len(keyword_moments)} keyword moments", file=sys.stderr)
for m in keyword_moments:
    print(f"  T={m['timestamp']}s [{m['primary_category']}] score={m['score']} segment={m['segment_type']}", file=sys.stderr)
with open(f"{TEMP_DIR}/keyword_moments.json", "w") as f:
    json.dump(keyword_moments, f, indent=2)


# ==============================================================
# PASS B — Segment-Aware LLM Chunk Analysis
# ==============================================================
print("[PASS B] Segment-aware LLM transcript analysis...", file=sys.stderr)

def format_chunk(segs):
    """Format transcript segments with timestamps for the LLM."""
    lines = []
    for s in segs:
        minutes = int(s["start"] // 60)
        secs = int(s["start"] % 60)
        lines.append(f"[{minutes:02d}:{secs:02d}] {s['text']}")
    return "\\n".join(lines)

def time_str_to_seconds(time_str):
    """Convert MM:SS or H:MM:SS to seconds."""
    parts = time_str.strip().split(":")
    try:
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        elif len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    except (ValueError, IndexError):
        pass
    return None

# --- Network outage fail-fast (BUG 31) -----------------------------------
# Track consecutive Pass B / call_llm failures whose root cause is a network
# outage (timeout, ENETUNREACH, ECONNREFUSED). When a Docker Desktop hiccup
# severs the bridge to host.docker.internal:1234, every retry afterwards
# returns the same Errno-101 error within milliseconds — without this guard
# the pipeline would burn through all remaining Pass B chunks producing no
# moments. Once 3 consecutive call_llm() invocations bail out with the
# network signature, sys.exit(2) ends Pass B early so the operator can
# restart Docker Desktop and rerun.
_LLM_NET_FAIL_STREAK = 0
_LLM_NET_FAIL_LIMIT = 3
_NET_ERR_PATTERNS = (
    "Network is unreachable",
    "Errno 101",
    "Connection refused",
    "Errno 111",
    "Name or service not known",
    "timed out",
    "Read timed out",
)

def _looks_like_network_outage(err_msg):
    s = str(err_msg)
    return any(pat in s for pat in _NET_ERR_PATTERNS)


def call_llm(prompt, model=TEXT_MODEL_PASSB, max_retries=2, timeout=240, max_tokens=8000):
    """Call LM Studio (OpenAI-compatible) API.

    === Token budget ===
    max_tokens=8000: The Qwen3.5-35B-A3B model has a default thinking budget
    of ~8192 tokens and CANNOT have thinking disabled via chat_template_kwargs
    in LM Studio (confirmed: LM Studio's OpenAI endpoint does not forward this
    to the model's chat template for the 35B variant). The model WILL use
    ~3000–6000 tokens on reasoning before producing its answer. Setting
    max_tokens=8000 ensures it can finish thinking AND write the JSON output
    without hitting the limit (finish_reason=length with empty content).

    Smaller models (9B) have thinking disabled by default, so their token
    use is much lower (~100–200 tokens). 8000 is still correct for them —
    they just finish early.

    === Timeout (BUG 31 update) ===
    timeout=240: Pass B chunks emit short JSON ({moments: [...]}). Even with
    Gemma-style reasoning leakage (~2 k reasoning tokens) the call should
    finish in ~90-120 s on a 35B-class model. 240 s is a 2× safety margin.
    The old 600 s ceiling existed to absorb worst-case 35B-A3B reasoning,
    but in practice a >4 min Pass B call signals LM Studio is queued or
    Docker Desktop has wedged the network — fail fast and let the network
    outage detector (_LLM_NET_FAIL_STREAK) trip after 3 consecutive timeouts.

    === reasoning_content fallback ===
    When content is empty with finish_reason=stop, the 35B model has finished
    naturally but put its answer in reasoning_content. Extract it from there.
    finish_reason=length means the token limit was hit mid-think — retry.

    Strips any stray <think>...</think> tags as a safety net.
    """
    global _LLM_NET_FAIL_STREAK
    if _LLM_NET_FAIL_STREAK >= _LLM_NET_FAIL_LIMIT:
        # Docker Desktop / LM Studio outage already detected — fail fast.
        return None
    saw_network_error = False
    for attempt in range(max_retries + 1):
        payload = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "temperature": 0.3,
            "max_tokens": max_tokens,
            "chat_template_kwargs": {"enable_thinking": False},
        }).encode()

        try:
            req = urllib.request.Request(
                f"{LLM_URL}/v1/chat/completions",
                data=payload,
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                result = json.loads(resp.read().decode())
                msg = result["choices"][0]["message"]
                content = msg.get("content") or ""
                finish_reason = result["choices"][0].get("finish_reason", "?")
                usage = result.get("usage", {})
                reasoning_tokens = usage.get("completion_tokens_details", {}).get("reasoning_tokens", 0)

                if not content:
                    reasoning_content = str(msg.get("reasoning_content", ""))
                    if finish_reason == "stop" and reasoning_content:
                        # 35B+ models that ignore chat_template_kwargs put their
                        # entire answer in reasoning_content and return empty
                        # content when they finish naturally (finish_reason=stop).
                        # Extract the usable answer from reasoning_content.
                        print(
                            f"  LLM reasoning_content fallback (attempt {attempt+1}): "
                            f"finish={finish_reason}, reasoning_tokens={reasoning_tokens}, "
                            f"total_tokens={usage.get('completion_tokens', '?')}",
                            file=sys.stderr
                        )
                        content = reasoning_content
                    else:
                        # Token limit hit mid-think: content is genuinely empty.
                        r_preview = reasoning_content[:120]
                        print(
                            f"  LLM returned empty content (attempt {attempt+1}): "
                            f"finish={finish_reason}, reasoning_tokens={reasoning_tokens}, "
                            f"total_tokens={usage.get('completion_tokens', '?')}, "
                            f"reasoning_preview={r_preview!r}",
                            file=sys.stderr
                        )
                        if attempt < max_retries:
                            time.sleep(5)
                        continue

                # Strip thinking tokens emitted inline (toggle OFF mode).
                # Also strips any <think> wrapper that may appear in reasoning_content.
                content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
                if not content:
                    # reasoning_content was entirely a <think> block — retry
                    if attempt < max_retries:
                        time.sleep(5)
                    continue
                if reasoning_tokens > 0:
                    print(f"  LLM used {reasoning_tokens} reasoning tokens "
                          f"(thinking not fully disabled — check LM Studio settings)",
                          file=sys.stderr)
                # Successful call clears the network-outage streak.
                _LLM_NET_FAIL_STREAK = 0
                return content

        except Exception as e:
            print(f"  LLM call attempt {attempt+1}/{max_retries+1} failed: {e}", file=sys.stderr)
            if _looks_like_network_outage(e):
                saw_network_error = True
            if attempt < max_retries:
                time.sleep(5)
    if saw_network_error:
        _LLM_NET_FAIL_STREAK += 1
        if _LLM_NET_FAIL_STREAK >= _LLM_NET_FAIL_LIMIT:
            # Surface the outage in the persistent log; the caller (Pass B
            # chunk loop) checks llm_net_outage() and breaks out so we don't
            # spend 30 minutes producing zero moments after Docker Desktop
            # has wedged the bridge to host.docker.internal.
            print(
                f"  [FATAL] LM Studio unreachable for {_LLM_NET_FAIL_STREAK} "
                "consecutive Pass B calls — aborting LLM stage. Check Docker "
                "Desktop and LM Studio, then rerun with --force.",
                file=sys.stderr,
            )
    else:
        # Non-network failure (model returned empty content, parse error, etc.)
        # doesn't count toward the outage streak.
        _LLM_NET_FAIL_STREAK = 0
    return None


def llm_net_outage():
    """True when call_llm() has hit the consecutive-network-failure ceiling."""
    return _LLM_NET_FAIL_STREAK >= _LLM_NET_FAIL_LIMIT

def parse_llm_moments(response_text, chunk_start, chunk_end):
    """Defensively parse LLM JSON response into moments."""
    if not response_text:
        return []

    clean = response_text.strip()

    if "\`\`\`" in clean:
        parts = clean.split("\`\`\`")
        if len(parts) >= 2:
            clean = parts[1]
            if clean.startswith("json"):
                clean = clean[4:]
            clean = clean.strip()

    arr_start = clean.find("[")
    arr_end = clean.rfind("]") + 1

    parsed_moments = []

    if arr_start >= 0 and arr_end > arr_start:
        try:
            arr = json.loads(clean[arr_start:arr_end])
            if isinstance(arr, list):
                parsed_moments = arr
        except json.JSONDecodeError:
            pass

    # Phase 1.2: LM Studio's JSON mode requires a top-level OBJECT, not an
    # array. So the Pass B prompt asks for {"moments": [...]} when JSON mode
    # is on. Accept that shape here too (also accept a few common alias keys
    # in case a model returns a nearby synonym).
    if not parsed_moments:
        obj_start = clean.find("{")
        obj_end = clean.rfind("}") + 1
        if obj_start >= 0 and obj_end > obj_start:
            try:
                root = json.loads(clean[obj_start:obj_end])
                if isinstance(root, dict):
                    for _key in ("moments", "clips", "highlights", "items", "results"):
                        _val = root.get(_key)
                        if isinstance(_val, list):
                            parsed_moments = _val
                            break
            except json.JSONDecodeError:
                pass

    if not parsed_moments:
        for match in re.finditer(r'\{[^{}]+\}', clean):
            try:
                obj = json.loads(match.group())
                if "time" in obj or "timestamp" in obj:
                    parsed_moments.append(obj)
            except:
                pass

    results = []
    seen_at_start = 0  # BUG 35: count of moments that landed at chunk_start
    for m in parsed_moments:
        time_val = m.get("time") or m.get("timestamp", "")
        if isinstance(time_val, str):
            ts = time_str_to_seconds(time_val)
        elif isinstance(time_val, (int, float)):
            ts = int(time_val)
        else:
            continue

        if ts is None:
            continue

        # BUG 35: when the LLM returns garbage timestamps (chunk-relative
        # "00:00", null, or values outside the chunk window), the clamp
        # below pins them to chunk_start — we observed runs with 3+ moments
        # ALL at chunk_start in the same chunk, none of which corresponded
        # to real on-stream timing. Detect and drop the duplicates: keep
        # AT MOST the first chunk-start moment per chunk.
        was_out_of_range = ts < chunk_start or ts > chunk_end
        ts = max(chunk_start, min(ts, chunk_end))
        if was_out_of_range and ts == chunk_start:
            seen_at_start += 1
            if seen_at_start > 1:
                # 2nd+ moment at chunk_start with a junk timestamp — drop.
                continue

        score = 0
        try:
            score = int(m.get("score", 0))
        except (ValueError, TypeError):
            pass

        if score < 1:
            continue

        # Normalize LLM score from 1-10 to 0.0-1.0
        norm_score = round(max(0.0, min((score - 1) / 9.0, 1.0)), 3)

        category = str(m.get("category", "unknown")).lower().strip()
        cat_map = {
            "comedy": "funny", "humor": "funny", "humour": "funny",
            "emotion": "emotional", "sad": "emotional", "heartfelt": "emotional",
            "controversy": "hot_take", "controversial": "controversial", "debate": "hot_take",
            "hot-take": "hot_take", "hottake": "hot_take", "opinion": "hot_take",
            "rage": "reactive", "anger": "reactive", "frustration": "reactive",
            "ragebait": "reactive", "reaction": "reactive",
            "story": "storytime", "narrative": "storytime", "anecdote": "storytime",
            "excitement": "hype", "intense": "hype", "skill": "hype", "clutch": "hype",
            "dance": "dancing", "dancing": "dancing", "twerk": "dancing", "moves": "dancing"
        }
        category = cat_map.get(category, category)
        VALID_CATEGORIES = ("hype", "funny", "emotional", "hot_take", "storytime", "reactive", "dancing", "controversial")
        if category not in VALID_CATEGORIES:
            category = "hype"

        # Parse clip boundaries if LLM provided them
        clip_start_time = None
        clip_end_time = None
        raw_start = m.get("start_time") or m.get("start", "")
        raw_end = m.get("end_time") or m.get("end", "")
        if isinstance(raw_start, str) and raw_start:
            clip_start_time = time_str_to_seconds(raw_start)
        elif isinstance(raw_start, (int, float)):
            clip_start_time = int(raw_start)
        if isinstance(raw_end, str) and raw_end:
            clip_end_time = time_str_to_seconds(raw_end)
        elif isinstance(raw_end, (int, float)):
            clip_end_time = int(raw_end)

        # Validate and clamp boundaries
        if clip_start_time is not None and clip_end_time is not None:
            clip_start_time = max(chunk_start, min(clip_start_time, chunk_end))
            clip_end_time = max(chunk_start, min(clip_end_time, chunk_end))
            duration = clip_end_time - clip_start_time
            # Tier-1 Q5: storytime/emotional get up to 150s for genuine narrative arcs;
            # everything else stays capped at 90s. Min remains 15s.
            max_dur = 150 if category in ("storytime", "emotional") else 90
            if duration < 15:
                clip_start_time = None
                clip_end_time = None
            elif duration > max_dur:
                half = max_dur // 2
                clip_start_time = max(chunk_start, ts - half)
                clip_end_time = clip_start_time + max_dur

        # Tier-4 Phase 4.3: pull primary/secondary pattern IDs from the LLM
        # response. Validate against the loaded catalog; unknown IDs are
        # dropped (not an error — the model occasionally invents patterns).
        primary_pattern = (m.get("primary_pattern") or "").strip()
        if primary_pattern and PATTERN_IDS and primary_pattern not in PATTERN_IDS:
            primary_pattern = ""
        secondary_raw = m.get("secondary_patterns") or []
        if not isinstance(secondary_raw, list):
            secondary_raw = []
        secondary_patterns = [
            str(s).strip() for s in secondary_raw
            if isinstance(s, (str,)) and (not PATTERN_IDS or str(s).strip() in PATTERN_IDS)
        ]

        result_entry = {
            "timestamp": ts,
            "score": norm_score,
            "preview": str(m.get("why", m.get("reason", "")))[:120],
            "categories": [category],
            "primary_category": category,
            "source": "llm",
            "why": str(m.get("why", m.get("reason", "")))[:200]
        }
        if primary_pattern:
            result_entry["primary_pattern"] = primary_pattern
        if secondary_patterns:
            result_entry["secondary_patterns"] = secondary_patterns
        if clip_start_time is not None and clip_end_time is not None:
            result_entry["clip_start"] = clip_start_time
            result_entry["clip_end"] = clip_end_time
        results.append(result_entry)

    return results

# Segment-specific LLM prompts — tailored to what each segment type produces
SEGMENT_PROMPTS = {
    "gaming": """Focus on GAMEPLAY moments:
- Clutch plays, skillful outplays, close calls, narrow escapes
- Epic wins or devastating losses, comeback moments
- Funny fails, glitches, unexpected game events
- Rage moments when losing, trash talk, celebrating wins
- Reactions to in-game events viewers would find exciting
- Moments where the streamer narrates something absurd happening in-game""",

    "irl": """Focus on IRL moments (these are NATURALLY QUIETER so lower your bar for what counts):
- Funny real-world situations, awkward encounters with strangers
- Interesting locations, unexpected events happening around streamer
- STORYTIME: Streamer telling a story while walking, traveling, or doing something — look for narrative arc
- Genuine emotional moments, real talk while walking/traveling
- Interactions with friends, strangers, or the environment
- Someone off-camera saying something unexpected that changes the situation
- Situational irony — streamer claims something then reality contradicts them
- Getting kicked out, confronted, or encountering unexpected resistance
- DANCING or physical performance — streamer vibing, dancing, doing moves
- Even small charming or relatable moments count here""",

    "just_chatting": """Focus on CONVERSATION moments (lower your bar for what counts — subtle is fine):
- STORYTIME: A story building to a punchline, reveal, or unexpected twist. The setup matters as much as the payoff
- HOT TAKES: Unpopular opinions, controversial claims, bold statements that will make viewers react
- Funny stories, witty one-liners, comedic timing
- Emotional vulnerability, real talk, genuine audience connection
- CONTROVERSIAL: Drama, tea-spilling, call-outs, gossip, beef, exposing someone
- Audience interaction moments that are entertaining
- Moments where the streamer says something quotable
- Someone (chat, friend, co-host) calling the streamer out or correcting them
- The streamer setting something up (bragging, explaining) and then getting undercut
- DANCING or vibing to music, physical comedy""",

    "reaction": """Focus on REACTION moments:
- Strong emotional reactions to content (shock, anger, laughter, disbelief)
- HOT TAKES about what they're watching — opinions viewers will argue about
- Reactive rage or disbelief — things viewers will clip and share
- Disagreeing strongly with popular opinion
- Over-the-top reactions that are entertaining to watch
- Moments where the streamer's reaction IS the content
- Streamer confidently stating something, then immediately being proven wrong
- Double-takes, jaw drops, or moments where they have to pause and process
- CONTROVERSIAL takes that would blow up on social media""",

    "debate": """Focus on DEBATE/ARGUMENT moments:
- Strongest arguments, mic-drop moments
- When someone gets heated, raises their voice, or loses composure
- Controversial claims that would generate engagement
- Funny comebacks or roasts during arguments
- When the conversation takes an unexpected turn
- Moments where someone says something the audience will quote
- Someone getting caught in a contradiction or logical trap"""
}

# Score boost for naturally quieter segments to compete fairly (0-1 scale)
SEGMENT_SCORE_BOOST = {
    "gaming": 0.0,
    "irl": 0.10,
    "just_chatting": 0.10,
    "reaction": 0.0,
    "debate": 0.0,
}

# Build style-specific prompt emphasis
style_prompts = {
    "auto": "Find the most engaging moments of ANY type. Balance variety.",
    "hype": "Prioritize exciting, intense, high-energy moments. Celebrations, clutch plays, shock reactions.",
    "funny": "Prioritize comedy. Funny stories, awkward moments, witty lines, ironic situations, fails, deadpan delivery.",
    "emotional": "Prioritize emotional depth. Vulnerable sharing, heartfelt gratitude, real talk, difficult topics, genuine moments.",
    "hot_take": "Prioritize controversial opinions, hot takes, unpopular opinions, bold claims that viewers will debate.",
    "storytime": "Prioritize narrative moments — stories with setup and payoff, anecdotes building to a punchline or reveal.",
    "reactive": "Prioritize strong reactions — rage, shock, disbelief, over-the-top responses to events or content.",
    "controversial": "Prioritize drama, call-outs, beef, tea-spilling, edgy statements, anything that would blow up on social media.",
    "dancing": "Prioritize physical performance moments — dancing, moves, vibing, physical comedy, any body-based entertainment.",
    "variety": "Find ONE moment from EACH category. Maximum diversity across all categories."
}
style_hint = style_prompts.get(CLIP_STYLE, style_prompts["auto"])

# Tier-1 Q4: per-segment chunk windows. Storytimes and arguments routinely run
# 4-8 minutes; the old uniform 5-minute chunk was cutting those arcs in half.
# Reactions and gameplay moments are 30s-2min so 5min stays the right size there.
# Sizing chunks to segment type lets the LLM see whole arcs instead of halves.
CHUNK_DURATION_BY_SEGMENT = {
    "just_chatting": 480,   # 8 min — storytimes need room
    "irl":           480,
    "debate":        360,   # 6 min — argument arcs
    "reaction":      300,   # 5 min — current default
    "gaming":        300,
}
CHUNK_OVERLAP_BY_SEGMENT = {
    "just_chatting": 60,
    "irl":           60,
    "debate":        45,
    "reaction":      30,
    "gaming":        30,
}
DEFAULT_CHUNK_DURATION = 300
DEFAULT_CHUNK_OVERLAP = 30

def _chunk_window_for(start_ts):
    """Pick (duration, overlap) for the chunk starting at start_ts. Peek at +150s
    (a coarse midpoint estimate) so we know what segment type dominates and can
    size the window for it. Edge case noted in the upgrade plan: a chunk that
    straddles a segment-type boundary uses the +150s side's window, which is the
    best we can do without iterating."""
    seg = get_segment_type(start_ts + 150)
    return (
        CHUNK_DURATION_BY_SEGMENT.get(seg, DEFAULT_CHUNK_DURATION),
        CHUNK_OVERLAP_BY_SEGMENT.get(seg, DEFAULT_CHUNK_OVERLAP),
        seg,
    )

chunk_start = segments[0]["start"]
llm_moments = []
chunk_count = 0

# Tier-1 Q1 + Q4: pre-compute total_chunks by walking the timeline with the same
# per-segment window logic the loop uses, so the prior-context block can show
# the model where in the stream the chunk sits ("3/22") with stable indices.
_total_chunks = 0
_walk = chunk_start
while _walk < max_time:
    _total_chunks += 1
    _walk_dur, _walk_ov, _ = _chunk_window_for(_walk)
    _walk += _walk_dur
total_chunks = max(_total_chunks, 1)

# Tier-1 Q1: cache per-chunk one-line summaries for prior-context injection.
# Populated at the END of each chunk's Pass B work (after parse + grounding) so
# subsequent chunks see them.
chunk_summaries = []

# Tier-4 Phase 4.2: accumulate per-chunk conversation_shape records and write
# them to /tmp/clipper/conversation_shape.json after the Pass B loop so Pass D
# and Stage 6 can look up shape data by chunk index. Keys are chunk_count
# (1-indexed) as strings.
CONVO_SHAPE_INDEX = {}

while chunk_start < max_time:
    # Tier-1 Q4: pick the chunk window from the segment type at the +150s peek.
    # cur_chunk_dur/cur_chunk_overlap stand in for the old CHUNK_DURATION/
    # CHUNK_OVERLAP constants for the rest of this iteration. seg_type comes
    # from the same peek so we don't re-query downstream.
    cur_chunk_dur, cur_chunk_overlap, seg_type = _chunk_window_for(chunk_start)
    chunk_end = chunk_start + cur_chunk_dur
    overlap_start = max(0, chunk_start - cur_chunk_overlap)
    chunk_segs = [s for s in segments if s["start"] < chunk_end + cur_chunk_overlap and s["end"] > overlap_start]

    if not chunk_segs:
        chunk_start += cur_chunk_dur
        continue

    chunk_count += 1
    chunk_text = format_chunk(chunk_segs)
    word_count = sum(len(s["text"].split()) for s in chunk_segs)

    if word_count < 15:
        print(f"  Chunk {chunk_count} ({int(chunk_start)}s-{int(chunk_end)}s): too sparse ({word_count} words), skipping", file=sys.stderr)
        chunk_start += cur_chunk_dur
        continue

    seg_instructions = SEGMENT_PROMPTS.get(seg_type, SEGMENT_PROMPTS["just_chatting"])

    # Tier-4 Phase 4.2 — compute conversation shape for THIS chunk and
    # serialize a compact summary block. Pass B reads it before the raw
    # transcript so the LLM sees the structural skeleton (turn graph, off-screen
    # intrusions, monologue runs, discourse markers) before reading the words.
    # Each record is also accumulated into CONVO_SHAPE_INDEX which gets written
    # to /tmp/clipper/conversation_shape.json after the loop so Pass D + Stage 6
    # can look up the shape by chunk timestamp.
    convo_shape_block = ""
    convo_shape_record = None
    if CONVO_SHAPE is not None:
        try:
            convo_shape_record = CONVO_SHAPE.analyze_chunk(
                chunk_segs, float(chunk_start), float(chunk_end),
                markers=CONVO_MARKERS,
            )
            try:
                CONVO_SHAPE_INDEX[chunk_count] = convo_shape_record
            except NameError:
                pass
            _summary = CONVO_SHAPE.serialize_for_prompt(convo_shape_record, max_chars=900)
            if _summary:
                convo_shape_block = (
                    "\nConversation shape (from speech analysis):\n"
                    + _summary + "\n"
                )
        except Exception as _ce:
            print(f"  Chunk {chunk_count}: conversation_shape failed ({_ce}); continuing", file=sys.stderr)

    # Tier-1 Q1: prior-context block. Pass B today sees only its own 5-minute
    # chunk — any setup that landed in an earlier chunk is invisible, which is
    # how the canonical Lacy penthouse callback gets missed. Show the model the
    # last 2 chunk summaries so it can spot setup→payoff arcs that cross chunk
    # boundaries and name them in 'why'.
    prior_context_block = ""
    if chunk_count >= 2 and chunk_summaries:
        recent = chunk_summaries[-2:]
        prior_lines = []
        # chunk_summaries holds (chunk_index, summary_text) tuples; show indices
        # so the model knows how far back the setup landed.
        for ci, summ in recent:
            prior_lines.append(f"  • ({ci}/{total_chunks}) {summ}")
        prior_context_block = (
            "\nEarlier in this stream:\n"
            + "\n".join(prior_lines)
            + "\n\nLook for SETUP-PAYOFF arcs where something the streamer said "
              "earlier is now contradicted, fulfilled, or referenced. These "
              "callbacks are the highest-value clips. If THIS chunk is a payoff "
              "for one of the earlier setups above, mention the callback "
              "explicitly in 'why' (e.g. \"earlier they said X, now Y\").\n"
        )

    # /no_think sentinel: Qwen3.5-35B-A3B burns 2-4k reasoning tokens per Pass B
    # chunk when thinking isn't properly disabled (ClippingResearch.md Additional
    # topic 1). This is pure tagging/classification — there's no payoff to the
    # reasoning trace. No-op on 9B/Gemma where thinking is off by default.
    #
    # Tier-4 Phase 4.3: prompt evaluates against the Pattern Catalog (named
    # interaction shapes) instead of the legacy 6-rule keyword-style prompt.
    # When the catalog is unavailable (config missing) the legacy prompt is
    # preserved as a fallback below.
    if PATTERN_CATALOG_PROMPT:
        prompt = f"""/no_think
You are a stream clip scout. This is a {seg_type.upper()} segment. Find 0-3 clip-worthy moments by matching against the PATTERN CATALOG below — do NOT score on keywords alone.

{seg_instructions}

STYLE: {style_hint}
{prior_context_block}{convo_shape_block}
PATTERN CATALOG (evaluate against these — pick the best fit):
{PATTERN_CATALOG_PROMPT}

How to use the catalog:
- For each candidate moment, identify which pattern's signature is satisfied. Set "primary_pattern" to that pattern's id.
- If a second pattern also fits, list it under "secondary_patterns".
- If NO pattern's signature is satisfied, do not emit the moment. Don't invent patterns.
- Use the conversation_shape signals above as evidence: off_screen_intrusions support setup_external_contradiction; pushback markers support challenge_and_fold and hot_take_pushback; long monologue_runs support storytelling_arc and informational_ramble.
- "why" must name the pattern signature being satisfied AND cite specific transcript+shape evidence. Example: "Pattern setup_external_contradiction: streamer claims X at 14:02, off-screen voice contradicts at 14:28, streamer concedes at 14:33."

Skip these:
- Routine gameplay or "oh my god" reactions that don't fit any pattern's signature.
- Generic hype with no setup, no payoff, no social dynamic.

When in doubt, lean toward INCLUDING with a lower score (3-5) over skipping — the scoring system handles the rest.

Transcript (timestamps MM:SS from stream start):
{chunk_text}

Respond with ONLY a single JSON object: {{"moments": [ ... ]}}. Each element: {{"time": "MM:SS", "start_time": "MM:SS", "end_time": "MM:SS", "score": 1-10, "category": "hype|funny|emotional|hot_take|storytime|reactive|dancing|controversial", "primary_pattern": "<pattern_id>", "secondary_patterns": ["<pattern_id>", ...], "why": "one sentence naming WHICH pattern signature is satisfied and HOW the transcript+shape evidence it"}}

IMPORTANT — start_time and end_time define the CLIP BOUNDARIES:
- start_time: where the moment BEGINS (include setup/context). For storytimes, this is where the story starts.
- end_time: where the moment ENDS (after the payoff/reaction lands). Don't trail into dead air.
- Minimum clip: 15 seconds. Maximum: 150 seconds for storytime/emotional, 90 seconds for everything else.
- One-liner reactions: 15-25 s
- Standard funny/hype/hot_take: 25-50 s
- Storytime/emotional with narrative arc: 60-120 s (default 90)
- Setup-payoff callbacks with multi-minute setup: up to 150 s (cite the setup line in 'why')

If nothing stands out at all, respond: {{"moments": []}}"""
    else:
        # Legacy fallback when Pattern Catalog config is unreadable. Identical
        # to the pre-Tier-4 prompt minus the few-shots block (the catalog
        # carries the few-shot equivalent through its examples field).
        prompt = f"""/no_think
You are a stream clip scout finding moments viewers will watch, share, and clip. This is a {seg_type.upper()} segment. Find 0-3 clip-worthy moments.

{seg_instructions}

STYLE: {style_hint}
{prior_context_block}{convo_shape_block}
IMPORTANT — Look beyond keywords:
Streamers say "oh my god", "bruh", "no way" constantly. These words alone don't make a clip. Look at what's HAPPENING — the situation, the context, the story.

Good clips have at least one of these:
1. SETUP + PAYOFF — something established then subverted
2. STORYTELLING — a story building to a punchline or reveal
3. GENUINE REACTIONS — reacting to something specific and interesting
4. SITUATIONAL IRONY — confidence followed by failure
5. SOCIAL DYNAMICS — someone calls them out, friend roast
6. QUOTABLE MOMENTS — a one-liner, hot take, or deadpan observation

Skip routine gameplay reactions and generic hype.

Transcript (timestamps MM:SS from stream start):
{chunk_text}

Respond with ONLY a single JSON object of the form {{"moments": [ ... ]}}. Each element of the array: {{"time": "MM:SS", "start_time": "MM:SS", "end_time": "MM:SS", "score": 1-10, "category": "hype|funny|emotional|hot_take|storytime|reactive|dancing|controversial", "why": "one sentence explaining the SITUATION not just the words"}}

IMPORTANT — start_time and end_time define the CLIP BOUNDARIES:
- start_time: where the moment BEGINS (include setup/context). For storytimes, this is where the story starts.
- end_time: where the moment ENDS (after the payoff/reaction lands). Don't trail into dead air.
- Minimum clip: 15 seconds. Maximum: 150 seconds for storytime/emotional, 90 seconds for everything else.
- One-liner reactions: 15-25 s
- Standard funny/hype/hot_take: 25-50 s
- Storytime/emotional with narrative arc: 60-120 s (default 90)
- Setup-payoff callbacks with multi-minute setup: up to 150 s (cite the setup line in 'why')

Categories:
- hype: exciting, intense, clutch plays, celebrations
- funny: comedy, fails, awkward moments, ironic situations
- emotional: vulnerable, heartfelt, real talk, genuine moments
- hot_take: unpopular opinions, bold claims that viewers will debate
- storytime: narrative buildup with payoff, anecdotes, storytelling
- reactive: strong reactions to something, rage, shock, disbelief
- dancing: physical performance, dancing, moves, physical comedy
- controversial: drama, call-outs, edgy statements, tea-spilling, beef
If nothing stands out at all, respond: {{"moments": []}}"""

    print(f"  Chunk {chunk_count} ({int(chunk_start)}s-{int(chunk_end)}s): {seg_type}, {word_count} words...", file=sys.stderr)

    response = call_llm(prompt)  # uses call_llm default max_tokens (3000)
    if response:
        chunk_moments = parse_llm_moments(response, int(chunk_start), int(chunk_end))

        # Apply segment score boost for quieter segments (0-1 scale)
        boost = SEGMENT_SCORE_BOOST.get(seg_type, 0.0)
        for m in chunk_moments:
            m["score"] = min(m["score"] + boost, 1.0)
            m["segment_type"] = seg_type

        # Null the "why" field on any moment whose summary fails the 2-tier
        # grounding cascade (Tier 1 denylist + content-overlap → main-model
        # LLM judge). Tier 1's hard-event check (zero-count Twitch event) is
        # the structural safety net. The moment itself stays (Pass C still
        # scores it); only the potentially-hallucinated "why" is stripped
        # so it can't seed Stage 6's prompt.
        if _grounding is not None:
            # Phase 2.4d: pull this moment's ±8 s hard-event counts for the
            # cascade's ground-truth check. If chat is unavailable we pass
            # None and the cascade behaves identically to its Phase-1 form.
            chunk_hard_events = None
            chunk_event_map = None
            if CHAT_FEATURES is not None:
                try:
                    import chat_features as _cf_mod
                    chunk_event_map = _cf_mod.denylist_event_map()
                except Exception:
                    chunk_event_map = None
            for m in chunk_moments:
                why = (m.get("why") or "").strip()
                if not why:
                    continue
                if CHAT_FEATURES is not None:
                    mt = m.get("timestamp")
                    if isinstance(mt, (int, float)):
                        _cw = CHAT_FEATURES.window(mt - 8, mt + 8)
                        chunk_hard_events = {
                            "sub_count": _cw.get("sub_count", 0),
                            "bit_count": _cw.get("bit_count", 0),
                            "raid_count": _cw.get("raid_count", 0),
                            "donation_count": _cw.get("donation_count", 0),
                        }
                # BUG 34: extract a tight ±90 s window around the moment so
                # the LLM judge sees the relevant context, not 5 minutes of
                # surrounding chatter. The whole chunk_text is kept as a
                # second reference so any rare evidence outside the window
                # can still pass tier 1's overlap check. (Originally tuned
                # for MiniCheck NLI's truncation behavior; carries forward
                # because the judge benefits from the same focus.)
                mt_for_ref = m.get("timestamp")
                tight_ref = ""
                if isinstance(mt_for_ref, (int, float)):
                    nearby = [
                        s for s in chunk_segs
                        if s.get("end", 0) > mt_for_ref - 90
                        and s.get("start", 0) < mt_for_ref + 90
                    ]
                    if nearby:
                        tight_ref = format_chunk(nearby)
                refs_for_cascade = [r for r in (tight_ref, chunk_text) if r]
                check = _grounding.cascade_check(
                    why,
                    refs_for_cascade,
                    GROUNDING_DENYLIST,
                    GROUNDING_CONFIG,
                    min_overlap=0.15,
                    hard_events=chunk_hard_events,
                    event_map=chunk_event_map,
                )
                if not check["passed"]:
                    hit_summary = ",".join(
                        h["match"] for h in check.get("denylist_hits", [])
                    ) or (
                        f"judge={check.get('judge_weighted')}"
                        if check.get("judge_weighted") is not None
                        else f"overlap={check.get('overlap')}"
                    )
                    print(
                        f"    [GROUND] Pass B null why T={m.get('timestamp')} "
                        f"tier={check['tier']} reason={check['reason']} ({hit_summary})",
                        file=sys.stderr,
                    )
                    m["why"] = ""
                    m["grounding_fail"] = check["reason"]
                    m["grounding_tier"] = check["tier"]

        # Tier-2 M1: annotate each LLM moment with speaker context from its
        # ±15 s payoff window so Pass C can boost multi-speaker moments and
        # Stage 6 can mention "off-screen voice" / interjection in titles.
        for m in chunk_moments:
            mt = m.get("timestamp")
            if not isinstance(mt, (int, float)):
                continue
            nearby = [
                s for s in chunk_segs
                if s.get("end", 0) > mt - 15 and s.get("start", 0) < mt + 15 and s.get("speaker")
            ]
            if not nearby:
                continue
            sp_dur = {}
            for s in nearby:
                sp = s.get("speaker")
                sp_dur[sp] = sp_dur.get(sp, 0.0) + max(0.0, float(s.get("end", 0)) - float(s.get("start", 0)))
            if not sp_dur:
                continue
            total_d = sum(sp_dur.values()) or 1.0
            dom_sp, dom_d = max(sp_dur.items(), key=lambda kv: kv[1])
            m["dominant_speaker"] = dom_sp
            m["speaker_count"] = len(sp_dur)
            m["dominant_speaker_share"] = round(dom_d / total_d, 3)

        print(f"  Chunk {chunk_count}: found {len(chunk_moments)} moments", file=sys.stderr)
        for m in chunk_moments:
            print(f"    T={m['timestamp']}s [{m['primary_category']}] score={m['score']} — {m.get('why','')[:60]}", file=sys.stderr)
        llm_moments.extend(chunk_moments)

        # Tier-1 Q1: ask for a one-line summary of THIS chunk so subsequent
        # chunks can see prior setup. /no_think is honored by Qwen but IGNORED
        # by Gemma 4 in LM Studio (permanent thinking) — on Gemma the model
        # burns 1500–4000 reasoning tokens before producing the 15-word answer.
        # max_tokens=200 was correct for Qwen but caused Gemma to always
        # finish=length with empty content, so every chunk's summary fell back
        # to the 12-word transcript snippet, hollowing out the cross-chunk
        # callback signal that motivates Tier-1 Q1 in the first place. 4000
        # covers the Gemma reasoning budget plus the short answer; on Qwen
        # the unused budget is free.
        summary_text = ""
        try:
            summary_prompt = (
                "/no_think\n"
                "Summarize the streamer's main claim, topic, or activity in this "
                "transcript chunk in 15 words or less. Output ONLY a single "
                "quoted line of plain English — no JSON, no preamble, no "
                "explanation.\n\n"
                f"Transcript:\n{chunk_text}\n\n"
                "Summary:"
            )
            summary_resp = call_llm(summary_prompt, max_tokens=4000, max_retries=0)
            if summary_resp:
                # Strip surrounding quotes/whitespace and any leftover wrapper.
                cleaned = summary_resp.strip().strip('"').strip("'").strip()
                # Take only the first line (defensive against models that
                # over-explain despite the prompt).
                cleaned = cleaned.splitlines()[0].strip() if cleaned else ""
                if cleaned:
                    # Hard cap so a runaway summary can't blow up later prompts.
                    summary_text = cleaned[:160]
        except Exception as _summ_err:
            print(f"  Chunk {chunk_count}: summary call errored ({_summ_err})", file=sys.stderr)
        if not summary_text:
            # Neutral fallback: first ~12 transcript words. Better than nothing —
            # later chunks at least know what topic the prior chunk was on.
            _fallback = " ".join(s["text"] for s in chunk_segs[:6]).split()[:14]
            summary_text = " ".join(_fallback)[:160] or "(no summary)"
        chunk_summaries.append((chunk_count, summary_text))
    else:
        print(f"  Chunk {chunk_count}: LLM call failed, skipping", file=sys.stderr)

    # BUG 31: short-circuit Pass B when LM Studio has been unreachable for
    # 3 consecutive chunks. Without this, a Docker-Desktop bridge failure or
    # a hung LM Studio queue burns through every remaining chunk producing
    # zero moments — wasting 20+ minutes and obscuring the real failure.
    if llm_net_outage():
        print(
            f"[PASS B] Aborting after chunk {chunk_count}: persistent LM Studio "
            "outage detected. Pass A keyword moments will still be used downstream.",
            file=sys.stderr,
        )
        break

    chunk_start += cur_chunk_dur

print(f"[PASS B] LLM found {len(llm_moments)} moments across {chunk_count} chunks", file=sys.stderr)
with open(f"{TEMP_DIR}/llm_moments.json", "w") as f:
    json.dump(llm_moments, f, indent=2)

# Tier-4 Phase 4.2: persist per-chunk conversation_shape records so Pass D
# (stage4_rubric.py) and Stage 6 (stage6_vision.py) can look them up by
# chunk_start/chunk_end. Keys stringified for JSON safety.
if CONVO_SHAPE_INDEX:
    try:
        with open(f"{TEMP_DIR}/conversation_shape.json", "w") as f:
            json.dump({str(k): v for k, v in CONVO_SHAPE_INDEX.items()}, f)
        print(f"[CONVO] Wrote {len(CONVO_SHAPE_INDEX)} chunk shape records to conversation_shape.json", file=sys.stderr)
    except Exception as _se:
        print(f"[CONVO] Failed to persist shape index ({_se}); Pass D + Stage 6 will run without shape data", file=sys.stderr)


# ==============================================================
# Tier-3 A1 — Two-stage Pass B (global pass over the stream skeleton)
# ==============================================================
# Pass B-local processed each chunk independently. Even with Tier-1 Q1's
# prior-context block, the model still only sees its own chunk + the last
# 2 summaries — so an arc that spans more than ~3 chunks (15+ minutes) is
# largely invisible. A1 sends the FULL skeleton (all chunk_summaries) in
# one Gemma call and asks for cross-chunk arcs explicitly. The skeleton
# fits comfortably in the context window: ~30-60 lines for a 3-hour VOD,
# ~3 KB total.
#
# Skeleton arcs are added as first-class moments with category="arc",
# cross_validated=True, and a 1.4× score boost. Pass C ranks them
# alongside local + callback moments.
arc_moments = []
if (
    not llm_net_outage()
    and chunk_summaries
    and len(chunk_summaries) >= 3   # need enough horizon for a real arc
):
    try:
        skeleton_lines = []
        for ci, summ in chunk_summaries:
            # Translate chunk_count -> approximate timestamp for the arc
            # boundaries to make sense to the model. The summaries were
            # produced in order so we can map back via the loop's chunk
            # tracking — but the simplest faithful proxy is to pre-compute
            # a chunk_index -> mid_time map by walking the timeline once.
            skeleton_lines.append((ci, summ))
        # Build a chunk_index -> (start, end) map by re-walking the timeline
        # with the same per-segment window logic used by the loop. Stable
        # under Tier-1 Q4's variable chunk sizes.
        chunk_time_map = {}
        _scan_idx = 0
        _scan_t = segments[0]["start"]
        while _scan_t < max_time:
            _scan_idx += 1
            _scan_dur, _scan_ov, _ = _chunk_window_for(_scan_t)
            chunk_time_map[_scan_idx] = (int(_scan_t), int(min(max_time, _scan_t + _scan_dur)))
            _scan_t += _scan_dur

        skeleton_text = []
        for ci, summ in skeleton_lines:
            t_range = chunk_time_map.get(ci)
            if t_range is None:
                continue
            s_min, s_sec = divmod(t_range[0], 60)
            e_min, e_sec = divmod(t_range[1], 60)
            skeleton_text.append(
                f"[{s_min:02d}:{s_sec:02d}-{e_min:02d}:{e_sec:02d}] (chunk {ci}/{total_chunks}) {summ}"
            )
        skeleton = "\n".join(skeleton_text)

        a1_prompt = f"""/no_think
Below is a skeleton of a stream, line by line, with timestamp ranges. Each line summarizes one chunk's main claim, topic, or activity.

Identify SETUP-PAYOFF ARCS that span MULTIPLE chunks. Look for:
- A claim made early that's contradicted, fulfilled, or undermined later (the canonical "I'm in my penthouse / actually it's not my penthouse" pattern)
- A theme introduced and revisited 30+ minutes later as a callback
- A friend / off-screen voice / chat exposing a fake or contradiction
- A long storytelling arc that crosses chunks
- A predicted outcome ("watch this work") that comes true or fails

Skeleton:
{skeleton}

Respond with ONLY a single JSON object: {{"arcs": [ ... ]}}. Each arc:
{{"setup_chunk": <int>, "payoff_chunk": <int>, "setup_time": "MM:SS", "payoff_time": "MM:SS", "arc_kind": "irony|contradiction|fulfillment|theme_return|exposure|prediction", "score": 1-10, "why": "one sentence naming both halves of the arc"}}.

Rules:
- Setup_chunk MUST be earlier than payoff_chunk by at least 1 chunk.
- Both timestamps MUST fall within their chunk's range.
- Skip "arcs" where the connection is just a shared topic — there must be a real beat (irony / contradiction / payoff).
- 0 arcs is a valid answer.  Quality > quantity.

If no arcs, respond {{"arcs": []}}."""
        print(
            f"[PASS B-GLOBAL] A1 sending skeleton of {len(skeleton_lines)} chunks "
            f"({len(skeleton)} chars) for cross-chunk arc detection",
            file=sys.stderr,
        )
        # 6000: Gemma 4-26B's permanent thinking can use 3000–5000 tokens on a
        # cross-chunk-arc skeleton before emitting the JSON. 2000 was tight
        # enough that A1 would silently truncate to {} on Gemma, dropping the
        # entire two-stage Pass B pass with no log signal.
        a1_resp = call_llm(a1_prompt, max_tokens=6000, max_retries=1)
        if a1_resp:
            text = a1_resp.strip()
            # Visibility: log response length + preview so a silent
            # empty-arcs response from a model that bypassed thinking is
            # distinguishable from a genuine "no arcs found" verdict.
            # Markdown backticks intentionally avoided in this comment —
            # we are inside the unquoted Stage 4 PYEOF heredoc; bash
            # would treat any raw backtick as command substitution and
            # mangle the body (BUG 39 / BUG 46 redux).
            # Prior 0-arc runs gave no signal at all (only the bare
            # "produced 0 arcs" line at the end), making it impossible
            # to tell whether the model actually deliberated.
            _preview = text.replace("\n", " ")[:160]
            print(
                f"[PASS B-GLOBAL] A1 raw response: len={len(text)} chars, "
                f"preview={_preview!r}",
                file=sys.stderr,
            )
            # Triple-backtick fence: must use the \-escaped form inside this
            # unquoted bash heredoc, otherwise bash treats the backticks as
            # command substitution before Python sees them and mangles the
            # heredoc (BUG 29 / BUG 38b). Same escape pattern already used
            # at lines 1439 and 3416.
            if "\`\`\`" in text:
                _parts = text.split("\`\`\`")
                if len(_parts) >= 2:
                    text = _parts[1]
                    if text.startswith("json"):
                        text = text[4:]
            _o = text.find("{"); _e = text.rfind("}") + 1
            if 0 <= _o < _e:
                try:
                    _root = json.loads(text[_o:_e])
                except json.JSONDecodeError as _je:
                    print(
                        f"[PASS B-GLOBAL] A1 JSON parse failed ({_je}); treating as 0 arcs",
                        file=sys.stderr,
                    )
                    _root = {}
            else:
                print(
                    "[PASS B-GLOBAL] A1 response had no {...} object — treating as 0 arcs",
                    file=sys.stderr,
                )
                _root = {}
        else:
            # call_llm returned None: outage detected, all retries exhausted,
            # or empty content from a token-starved Gemma response. Surface it
            # so the operator can distinguish "no arcs" from "API failure".
            print(
                "[PASS B-GLOBAL] A1 call_llm returned None — skipping arc detection",
                file=sys.stderr,
            )
            _root = {}
        for arc in (_root.get("arcs") or []):
            if not isinstance(arc, dict):
                continue
            try:
                setup_chunk = int(arc.get("setup_chunk", 0))
                payoff_chunk = int(arc.get("payoff_chunk", 0))
            except (ValueError, TypeError):
                continue
            if setup_chunk <= 0 or payoff_chunk <= setup_chunk:
                continue
            setup_t = time_str_to_seconds(arc.get("setup_time", ""))
            payoff_t = time_str_to_seconds(arc.get("payoff_time", ""))
            if setup_t is None or payoff_t is None or payoff_t <= setup_t:
                continue
            # Sanity-check that timestamps land inside their declared chunks.
            setup_range = chunk_time_map.get(setup_chunk)
            payoff_range = chunk_time_map.get(payoff_chunk)
            if setup_range and not (setup_range[0] - 60 <= setup_t <= setup_range[1] + 60):
                continue
            if payoff_range and not (payoff_range[0] - 60 <= payoff_t <= payoff_range[1] + 60):
                continue
            try:
                raw_score = int(arc.get("score", 7))
            except (ValueError, TypeError):
                raw_score = 7
            if raw_score < 1:
                continue
            norm_score = round(max(0.0, min((raw_score - 1) / 9.0, 1.0)), 3)
            # 1.4× boost per the plan, capped at 1.0 so it doesn't break Pass C math.
            norm_score = round(min(norm_score * 1.4, 1.0), 3)
            arc_kind = str(arc.get("arc_kind") or "theme_return").lower().strip()
            why = str(arc.get("why") or "")[:240]
            # Default clip window: 35 s centered on the payoff. The
            # boundary-snap stage will tighten this to actual sentence
            # boundaries, and Pass C dedup will merge with any nearby
            # local moment.
            clip_start = max(0, int(payoff_t) - 12)
            clip_end = min(int(max_time), int(payoff_t) + 23)
            arc_moment = {
                "timestamp": int(payoff_t),
                "score": norm_score,
                "preview": why[:120],
                "categories": ["arc", "controversial"],
                "primary_category": "arc",
                "source": "arc",
                "why": why,
                "clip_start": clip_start,
                "clip_end": clip_end,
                "arc_kind": arc_kind,
                "setup_time": int(setup_t),
                "setup_chunk": setup_chunk,
                "payoff_chunk": payoff_chunk,
                "cross_validated": True,
            }
            arc_moments.append(arc_moment)
            print(
                f"[PASS B-GLOBAL] +arc T={int(payoff_t)}s setup_T={int(setup_t)}s "
                f"kind={arc_kind} score={norm_score} why={why[:60]}",
                file=sys.stderr,
            )
        if arc_moments:
            llm_moments.extend(arc_moments)
            with open(f"{TEMP_DIR}/arcs.json", "w") as f:
                json.dump(arc_moments, f, indent=2)
            with open(f"{TEMP_DIR}/llm_moments.json", "w") as f:
                json.dump(llm_moments, f, indent=2)
        print(
            f"[PASS B-GLOBAL] A1 produced {len(arc_moments)} arcs from "
            f"{len(skeleton_lines)}-chunk skeleton",
            file=sys.stderr,
        )
    except Exception as _a1e:
        print(f"[PASS B-GLOBAL] A1 failed ({_a1e}); proceeding without skeleton arcs", file=sys.stderr)
else:
    print(
        f"[PASS B-GLOBAL] A1 skipped (chunk_summaries={len(chunk_summaries)}, "
        f"net_outage={llm_net_outage()})",
        file=sys.stderr,
    )


# ==============================================================
# Tier-2 M3 — Long-range callback detection
# ==============================================================
# Run only when Pass B produced moments (no point searching for callbacks
# when the pipeline has nothing to anchor a payoff to). Module gracefully
# no-ops when sentence-transformers / faiss-cpu aren't installed.
try:
    import callbacks as _callbacks_mod
    if llm_moments and not llm_net_outage():
        callback_moments = _callbacks_mod.detect_callbacks(
            segments=segments,
            llm_moments=llm_moments,
            call_llm_fn=call_llm,
            cache_dir=os.environ.get("CALLBACKS_CACHE_DIR") or "/root/.cache/sentence-transformers",
        )
        if callback_moments:
            llm_moments.extend(callback_moments)
            with open(f"{TEMP_DIR}/callbacks.json", "w") as f:
                json.dump(callback_moments, f, indent=2)
            # Persist the augmented llm_moments so the diagnostic dump in
            # Stage 8 reflects the callbacks too.
            with open(f"{TEMP_DIR}/llm_moments.json", "w") as f:
                json.dump(llm_moments, f, indent=2)
            print(
                f"[PASS B+] M3 added {len(callback_moments)} callback moments "
                f"(total LLM-side now {len(llm_moments)})",
                file=sys.stderr,
            )
        else:
            print("[PASS B+] M3 found no callback candidates", file=sys.stderr)
    else:
        print("[PASS B+] M3 skipped (no LLM moments to anchor on, or LM Studio outage)", file=sys.stderr)
except ImportError as _cbe:
    print(f"[PASS B+] M3 module not importable ({_cbe}); proceeding without callbacks", file=sys.stderr)
except Exception as _cbe:
    print(f"[PASS B+] M3 callback detection failed ({_cbe}); proceeding without callbacks", file=sys.stderr)


# ==============================================================
# PASS C — Merge, Deduplicate, Diversify, Select
# ==============================================================
print(f"[PASS C] Merging and selecting (target: {MAX_CLIPS} clips, max candidates: {MAX_CANDIDATES})...", file=sys.stderr)

all_moments = []

# Scores are already 0.0-1.0 from both passes.
# Keywords are useful for catching moments the LLM missed, but keyword-only
# moments should be penalized slightly since keywords lack context understanding.
# Tier-1 Q3: per-category ceiling. High-noise categories (hype/funny/reactive/dancing)
# stay at 0.75 — single-word triggers like "bruh"/"lmao" are weak signal. Categories
# whose keyword phrases are RARE and semantically specific ("let me tell you", "hot
# take", "unpopular opinion") get a higher ceiling — a cluster of those phrases is
# very strong signal even without LLM cross-validation.
KEYWORD_CEILING = {
    "hype": 0.75, "funny": 0.70, "reactive": 0.75, "dancing": 0.70,
    "storytime": 0.90, "hot_take": 0.85, "emotional": 0.85, "controversial": 0.85,
}
for m in keyword_moments:
    ceiling = KEYWORD_CEILING.get(m.get("primary_category", "hype"), 0.75)
    m["normalized_score"] = min(m["score"], ceiling)
    all_moments.append(m)

for m in llm_moments:
    m["normalized_score"] = m["score"]  # already 0.0-1.0
    all_moments.append(m)

all_moments.sort(key=lambda x: x["timestamp"])

# Deduplicate: merge moments within 25 seconds
deduped = []
for m in all_moments:
    merged = False
    for d in deduped:
        if abs(m["timestamp"] - d["timestamp"]) < 25:
            if m["source"] != d["source"]:
                # Cross-validated: multiplicative boost (×1.25) — much better than additive
                d["normalized_score"] = min(max(d["normalized_score"], m["normalized_score"]) * 1.25, 1.0)
                d["cross_validated"] = True
                for cat in m.get("categories", []):
                    if cat not in d.get("categories", []):
                        d["categories"].append(cat)
                # Inherit clip boundaries from LLM if keyword doesn't have them
                if "clip_start" not in d and "clip_start" in m:
                    d["clip_start"] = m["clip_start"]
                    d["clip_end"] = m["clip_end"]
                if m["normalized_score"] > d["normalized_score"] * 0.8:
                    d["preview"] = m.get("why") or m.get("preview", d["preview"])
            elif m["normalized_score"] > d["normalized_score"]:
                old_boundaries = {k: d.get(k) for k in ("clip_start", "clip_end") if k in d}
                d.update(m)
                # Preserve boundaries from earlier entry if new one lacks them
                for k, v in old_boundaries.items():
                    if k not in d and v is not None:
                        d[k] = v
            merged = True
            break
    if not merged:
        m["cross_validated"] = False
        deduped.append(m)

print(f"  After dedup: {len(deduped)} unique moments ({sum(1 for d in deduped if d.get('cross_validated'))} cross-validated)", file=sys.stderr)

# --- LENGTH PENALTY FUNCTION ---
# Prevents over-clipping: longer clips need higher base scores to survive selection.
# Short punchy clips are favored unless the content genuinely justifies length.
def length_penalty(duration_sec):
    """Returns a multiplier 0.0-1.0 based on clip duration."""
    if duration_sec <= 30:
        return 1.0       # ideal short-form length, no penalty
    elif duration_sec <= 45:
        return 0.95       # slight penalty
    elif duration_sec <= 60:
        return 0.85       # needs to be genuinely good
    elif duration_sec <= 75:
        return 0.75       # only strong storytime/emotional survives
    else:
        return 0.65       # exceptional content only

# Compute clip duration for each moment
for m in deduped:
    if "clip_start" in m and "clip_end" in m:
        m["clip_duration"] = m["clip_end"] - m["clip_start"]
    else:
        # Default duration based on category
        cat = m.get("primary_category", "hype")
        DEFAULT_DURATIONS = {
            "storytime": 45, "emotional": 40, "controversial": 35,
            "hot_take": 35, "funny": 30, "hype": 30,
            "reactive": 25, "dancing": 25
        }
        dur = DEFAULT_DURATIONS.get(cat, 30)
        m["clip_duration"] = dur
        # Set default boundaries centered on the peak timestamp
        half = dur // 2
        m["clip_start"] = max(0, m["timestamp"] - half)
        m["clip_end"] = m["clip_start"] + dur

# Apply style weighting and length penalty
for m in deduped:
    base = m["normalized_score"]
    cat = m.get("primary_category", "hype")

    weight_map = {
        "auto": {},
        "hype": {"hype": 1.3},
        "funny": {"funny": 1.3},
        "emotional": {"emotional": 1.3},
        "hot_take": {"hot_take": 1.3},
        "storytime": {"storytime": 1.3, "emotional": 1.15},
        "reactive": {"reactive": 1.3, "hot_take": 1.15},
        "controversial": {"controversial": 1.3, "hot_take": 1.2, "reactive": 1.15},
        "dancing": {"dancing": 1.3, "funny": 1.1},
        "variety": {}
    }

    weights = weight_map.get(CLIP_STYLE, {})
    multiplier = weights.get(cat, 1.0)
    styled_score = base * multiplier

    # Cross-validated moments get multiplicative boost
    if m.get("cross_validated"):
        styled_score *= 1.20

    # Tier-2 M1: speaker-change boost. Multi-speaker windows where no single
    # voice dominates are the canonical "off-screen voice exposes the
    # streamer" / "friend interruption" pattern. Multiplicative ×1.15, smaller
    # than cross-val so a real keyword+LLM agreement still outranks a
    # speaker-only signal.
    if m.get("speaker_count", 0) >= 2 and (m.get("dominant_speaker_share") or 1.0) < 0.7:
        styled_score *= 1.15

    # Apply length penalty — longer clips need higher base scores
    lp = length_penalty(m["clip_duration"])
    # BUG 37: was min(... * lp, 1.0) — caused 9/10 selected clips to land
    # at exactly 1.000 because cross-val × style × position routinely pushed
    # base 0.7-0.9 over the cap. Score saturation destroyed Pass C's
    # ranking — at the cap, ties resolve by insertion order (chunk index),
    # which compounds the bucket-overflow bias (BUG 36). Soft-cap instead:
    # raw scores can land in [0, ~1.4]; we display by clipping to 1.0 only
    # at the user-facing rendering side, and Pass C ranks on the raw value.
    m["final_score"] = round(styled_score * lp, 4)
    m["length_penalty"] = lp

# ---- TIME-BUCKET DISTRIBUTION ----
# Divide VOD into equal time buckets and guarantee each bucket gets representation.
# This prevents early-VOD bias where high-scoring early moments dominate selection.
NUM_BUCKETS = max(3, min(int(vod_hours * 2), 10))  # 2 buckets per hour, 3-10 range
bucket_duration = max_time / NUM_BUCKETS
clips_per_bucket = max(1, MAX_CLIPS // NUM_BUCKETS)
overflow_slots = MAX_CLIPS - (clips_per_bucket * NUM_BUCKETS)  # leftover slots for best-of

print(f"  Time distribution: {NUM_BUCKETS} buckets of {bucket_duration/60:.0f}min, {clips_per_bucket} clips/bucket + {overflow_slots} overflow", file=sys.stderr)

# Place each moment into its time bucket
buckets = [[] for _ in range(NUM_BUCKETS)]
for m in deduped:
    bucket_idx = min(int(m["timestamp"] / bucket_duration), NUM_BUCKETS - 1)
    buckets[bucket_idx].append(m)

# --- STREAM POSITION WEIGHTING ---
# Streamers warm up over time. The best content is typically 20-70% through the stream.
# Apply a mild position weight to counter early-stream and late-stream bias.
# Shape: slight penalty at start (cold open), peak at 30-60%, gentle decline at end.
def position_weight(timestamp, max_t):
    """Returns a multiplier 0.85-1.05 based on stream position."""
    if max_t <= 0:
        return 1.0
    pos = timestamp / max_t  # 0.0 = start, 1.0 = end
    if pos < 0.10:
        return 0.88  # first 10% — intros, setup, low energy
    elif pos < 0.25:
        return 0.95  # warming up
    elif pos < 0.70:
        return 1.05  # prime content zone
    elif pos < 0.90:
        return 1.0   # still good, winding down
    else:
        return 0.92  # last 10% — outros, low energy

for m in deduped:
    pw = position_weight(m["timestamp"], max_time)
    # BUG 37: same soft-cap reasoning as the cross-val boost above.
    m["final_score"] = round(m["final_score"] * pw, 4)
    m["position_weight"] = pw

# --- WITHIN-BUCKET NORMALIZATION ---
# Normalize scores within each bucket so moments in quiet segments compete fairly
# with moments in high-energy segments. A 0.6 in a dead bucket is as valuable as
# a 0.8 in a bucket where everything scores high.
for bucket in buckets:
    if len(bucket) < 2:
        continue
    bucket_max = max(m["final_score"] for m in bucket)
    bucket_min = min(m["final_score"] for m in bucket)
    if bucket_max - bucket_min < 0.05:
        continue  # all scores are nearly identical, skip
    for m in bucket:
        # Blend: 70% global score + 30% within-bucket normalized score
        if bucket_max > bucket_min:
            bucket_norm = (m["final_score"] - bucket_min) / (bucket_max - bucket_min)
        else:
            bucket_norm = 0.5
        m["final_score"] = round(0.70 * m["final_score"] + 0.30 * bucket_norm, 4)

# Sort each bucket by final_score
for b in buckets:
    b.sort(key=lambda x: x["final_score"], reverse=True)

# Minimum spacing based on clip duration (prevents overlapping clips)
def min_spacing(m):
    """Minimum seconds between this clip and neighbors."""
    return max(30, m.get("clip_duration", 30) + 10)

# Selection: pick top N from each bucket, then fill overflow with best remaining
selected = []

# Phase 1: Guaranteed picks from each bucket (ensures time spread)
for i, bucket in enumerate(buckets):
    picked = 0
    for m in bucket:
        if picked >= clips_per_bucket:
            break
        # Check spacing against already-selected (use clip-duration-aware spacing)
        spacing = min_spacing(m)
        too_close = any(abs(m["timestamp"] - s["timestamp"]) < spacing for s in selected)
        if not too_close:
            selected.append(m)
            picked += 1
    bucket_start_min = (i * bucket_duration) / 60
    bucket_end_min = ((i + 1) * bucket_duration) / 60
    print(f"  Bucket {i+1} ({bucket_start_min:.0f}-{bucket_end_min:.0f}min): {picked} clips from {len(bucket)} candidates", file=sys.stderr)

# Phase 2: Fill overflow slots with best remaining moments.
#
# BUG 36: previously this just sorted globally by final_score and picked top-N.
# When score saturation (BUG 37) collapses many moments to 1.000 in 1-2 buckets,
# all overflow slots cluster in those buckets — we observed runs with 7/10
# clips in one quarter of the stream. Round-robin instead: in each pass walk
# the buckets in score-balanced order, taking the highest-scored UNUSED moment
# from each, until overflow_slots is filled. Buckets that already got their
# Phase-1 pick yield first to buckets that didn't, so an unfilled bucket
# always wins a slot before any other bucket gets a SECOND.
def _phase2_round_robin(buckets, selected, max_clips, min_spacing_fn):
    """Yield moments to add, prioritizing under-filled buckets."""
    # Per-bucket cursors into their already-sorted lists.
    bucket_iters = [iter(b) for b in buckets]
    bucket_picked = [
        sum(1 for s in selected
            if any(s is m for m in b))
        for b in buckets
    ]
    while len(selected) < max_clips:
        # Sort bucket indices by (already-picked-count asc, top-remaining-score desc).
        order = []
        peeks = []
        for i, b in enumerate(buckets):
            top = next(
                (m for m in b
                 if not any(m is s for s in selected)),
                None,
            )
            peeks.append(top)
            if top is not None:
                order.append((bucket_picked[i], -top["final_score"], i))
        if not order:
            return
        order.sort()
        added = False
        for _, _, i in order:
            cand = peeks[i]
            if cand is None:
                continue
            spacing = min_spacing_fn(cand)
            if any(abs(cand["timestamp"] - s["timestamp"]) < spacing for s in selected):
                # remove this peek and try next-best in this bucket on next outer pass.
                buckets[i].remove(cand)
                continue
            selected.append(cand)
            bucket_picked[i] += 1
            added = True
            break
        if not added:
            return  # nothing pickable anywhere

_phase2_round_robin(buckets, selected, MAX_CLIPS, min_spacing)

# Phase 3: If a style is specified, apply style-aware re-ranking within the selection
if CLIP_STYLE == "variety":
    # Round-robin by category from the selected pool
    by_category = {}
    for m in selected:
        cat = m.get("primary_category", "hype")
        by_category.setdefault(cat, []).append(m)
    for cat in by_category:
        by_category[cat].sort(key=lambda x: x["final_score"], reverse=True)
    final = []
    cats = list(by_category.keys())
    idx = 0
    while len(final) < MAX_CLIPS and any(by_category.values()):
        cat = cats[idx % len(cats)]
        if by_category.get(cat):
            final.append(by_category[cat].pop(0))
        idx += 1
        cats = [c for c in cats if by_category.get(c)]
        if not cats:
            break
elif CLIP_STYLE not in ("auto", ""):
    # Style-specific: re-sort selected by style-weighted score, pick top N
    selected.sort(key=lambda x: x["final_score"], reverse=True)
    final = selected[:MAX_CLIPS]
else:
    # Auto: category cap — no single category exceeds 50% of clips
    selected.sort(key=lambda x: x["final_score"], reverse=True)
    final = []
    cat_counts = {}
    max_per_cat = max(2, int(MAX_CLIPS * 0.50))
    for m in selected:
        cat = m.get("primary_category", "hype")
        if cat_counts.get(cat, 0) < max_per_cat:
            final.append(m)
            cat_counts[cat] = cat_counts.get(cat, 0) + 1
        if len(final) >= MAX_CLIPS:
            break
    # Backfill if we didn't reach MAX_CLIPS due to category cap
    if len(final) < MAX_CLIPS:
        for m in selected:
            if m not in final:
                final.append(m)
                if len(final) >= MAX_CLIPS:
                    break

final.sort(key=lambda x: x["final_score"], reverse=True)

print(f"  Final selection: {len(final)} clips across {len(set(min(int(m['timestamp']/bucket_duration), NUM_BUCKETS-1) for m in final))} of {NUM_BUCKETS} time buckets", file=sys.stderr)

# Write output with clip boundaries and 0-1 scores.
# BUG 37: final_score may exceed 1.0 because we soft-cap during ranking
# (saturation at the cap destroyed Pass C's tie-breaking). Clip to [0, 1]
# only at the user-facing serialization boundary.
output = []
for m in final:
    raw = m.get("final_score", 0.0)
    display_score = round(min(max(raw, 0.0), 1.0), 3)
    entry = {
        "timestamp": m["timestamp"],
        "score": display_score,
        # BUG 37: raw_score may exceed 1.0 because we soft-cap during ranking;
        # this field preserves the unclamped value so operators can see the
        # actual ranking distinction (vs. the user-facing 0–1 display score
        # that clamps multiple top moments to a visually-tied 1.000).
        "raw_score": round(raw, 4),
        "clip_start": m.get("clip_start", max(0, m["timestamp"] - 15)),
        "clip_end": m.get("clip_end", m["timestamp"] + 15),
        "clip_duration": m.get("clip_duration", 30),
        "preview": m.get("preview", "")[:120],
        "category": m.get("primary_category", "unknown"),
        "why": m.get("why", m.get("preview", ""))[:200],
        "source": m.get("source", "unknown"),
        "cross_validated": m.get("cross_validated", False),
        "segment_type": m.get("segment_type", get_segment_type(m["timestamp"])),
        "length_penalty": m.get("length_penalty", 1.0),
        "position_weight": m.get("position_weight", 1.0),
        # Tier-2 M1 — speaker context (used by Stage 6 prompt + A2)
        "dominant_speaker": m.get("dominant_speaker"),
        "speaker_count": m.get("speaker_count"),
        "dominant_speaker_share": m.get("dominant_speaker_share"),
        # Tier-3 A1/A2 + Tier-2 M3 — preserve setup info for Stage 5 setup-frame
        # extraction and Stage 6 setup-aware prompt. Present only on
        # callback/arc moments; None for everything else.
        "setup_time": m.get("setup_time"),
        "setup_text": m.get("setup_text"),
        "arc_kind": m.get("arc_kind") or m.get("callback_kind"),
        "callback_cosine": m.get("callback_cosine"),
    }
    output.append(entry)

with open(f"{TEMP_DIR}/hype_moments.json", "w") as f:
    json.dump(output, f, indent=2)

cats_found = {}
segs_found = {}
for m in output:
    cat = m.get("category", "?")
    cats_found[cat] = cats_found.get(cat, 0) + 1
    seg = m.get("segment_type", "?")
    segs_found[seg] = segs_found.get(seg, 0) + 1

print(f"\n[PASS C] Selected {len(output)} moments:", file=sys.stderr)
for m in output:
    xv = " [CROSS-VALIDATED]" if m.get("cross_validated") else ""
    dur = m.get("clip_duration", 30)
    lp = m.get("length_penalty", 1.0)
    pw = m.get("position_weight", 1.0)
    raw = m.get("raw_score", m["score"])
    # Show raw score next to the clamped display value so the operator can
    # see actual ranking distinction even when several moments display 1.000
    # (BUG 37: ranking happens on raw values that routinely exceed 1.0).
    print(f"  T={m['timestamp']}s [{m['category']}] score={m['score']:.3f} raw={raw:.4f} dur={dur}s lp={lp} pw={pw:.2f} segment={m.get('segment_type','')} src={m['source']}{xv} — {m.get('why','')[:60]}", file=sys.stderr)
print(f"  Category breakdown: {json.dumps(cats_found)}", file=sys.stderr)
print(f"  Segment breakdown: {json.dumps(segs_found)}", file=sys.stderr)
print(f"Detected {len(output)} clip-worthy moments")
for m in output:
    dur = m.get("clip_duration", 30)
    print(f"  T={m['timestamp']}s score={m['score']:.3f} [{m['category']}] ({m.get('segment_type','')}) dur={dur}s — {m.get('why','')[:60]}")
