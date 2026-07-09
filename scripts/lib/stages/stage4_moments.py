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
from pathlib import Path
try:
    import urllib.request
except:
    pass

LLM_URL = os.environ["LLM_URL"]
TEXT_MODEL = os.environ["TEXT_MODEL"]
TEXT_MODEL_PASSB = os.environ["TEXT_MODEL_PASSB"]
CLIP_STYLE = os.environ["CLIP_STYLE"]
TEMP_DIR = os.environ.get("CLIP_WORK_DIR", "/tmp/clipper")

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

# 2026-06-06 regression fix: torchcodec is now pip-installed (for pyannote), and
# this Stage 4 process's M3 callback detection imports torch-ecosystem libs that
# eagerly probe torchcodec. torchcodec needs FFmpeg *shared* libs on the DLL
# search path or it hard-fails ("Could not load libtorchcodec ... or one of its
# dependencies"), which took out M3 entirely on the 6/6 run. speech.py already
# does this for Stage 2; do it here too so the Stage 4 subprocess can load
# torchcodec. Best-effort, idempotent, no-op off Windows. See
# concepts/pass-b-false-negatives.md + clip-quality-remediation-2026-06.md Fix 4.
try:
    import ffmpeg_dll as _ffdll
    _ffdir = _ffdll.enable_ffmpeg_dll_dir()
    if _ffdir:
        print(f"[STAGE4] FFmpeg shared libs on DLL path ({_ffdir}) — torchcodec can load", file=sys.stderr)
except Exception as _ffe:
    print(f"[STAGE4] ffmpeg_dll bootstrap skipped ({_ffe})", file=sys.stderr)

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
    _pat_path = os.environ.get("CLIP_PATTERNS_CONFIG") or "/root/.openclaw/patterns.json"
    if not os.path.exists(_pat_path):
        _pat_path = "/root/scripts/lib/../../config/patterns.json"
    with open(_pat_path) as _pf:
        _pat_raw = json.load(_pf)
    PATTERN_CATALOG = list(_pat_raw.get("patterns") or [])
    print(f"[PATTERNS] loaded {len(PATTERN_CATALOG)} interaction patterns", file=sys.stderr)
except Exception as _pe:
    print(f"[PATTERNS] catalog unavailable ({_pe}); Pass B will fall back to legacy 6-rule prompt", file=sys.stderr)

PATTERN_IDS = {p["id"] for p in PATTERN_CATALOG if isinstance(p, dict) and p.get("id")}

# Rare-pattern bonus (2026-06-12 — case-rap-battle-missed deferred Phase 2).
# Per-pattern Pass C multiplier read from the catalog itself: a pattern entry
# may carry `pass_c_bonus` (default 1.0). This is a style-INDEPENDENT rarity
# bonus so a rare, high-value pattern (a once-a-VOD rap battle) survives
# time-bucket competition against everyday patterns — the 2026-06-05 re-run
# detected the Delaware battle (Pass B 0.878, cross-validated) but Pass C
# dropped it to a 0.433 competitor riding a 1.55 axis product. Distinct from
# config/style_pattern_weights.json, which is style-CONDITIONAL and applied in
# the Phase 4.6 diversity step. CLIP_PATTERN_BONUS=0 disables. Stamped on the
# moment as `pattern_bonus` for the axis report / future calibration fitter.
_PATTERN_BONUS_ON = os.environ.get("CLIP_PATTERN_BONUS", "1").strip().lower() not in (
    "0", "false", "no", "off",
)
_PATTERN_BONUS = {}
if _PATTERN_BONUS_ON:
    for _p in PATTERN_CATALOG:
        if not isinstance(_p, dict) or not _p.get("id"):
            continue
        try:
            _b = float(_p.get("pass_c_bonus", 1.0) or 1.0)
        except (TypeError, ValueError):
            _b = 1.0
        # Clamp to a sane envelope so a config typo can't dominate Pass C.
        _b = max(0.8, min(_b, 1.3))
        if _b != 1.0:
            _PATTERN_BONUS[_p["id"]] = _b
    if _PATTERN_BONUS:
        print(f"[PATTERNS] rare-pattern Pass C bonuses: {_PATTERN_BONUS}", file=sys.stderr)

# Selection Sub-Plan A — arc-completeness scorer. Structural setup->payoff
# completeness -> a gentle, category-aware multiplier folded into Pass C
# raw_score (boost-leaning, never gates). Failure-soft: if the module or
# conversation_shape is unavailable, Pass C runs with a 1.0 multiplier.
try:
    import arc_completeness as _arc
    _ARC_CFG = _arc.load_config()
    print(f"[ARC] arc-completeness loaded (enabled={_ARC_CFG.get('enabled', True)})", file=sys.stderr)
except Exception as _arce:
    _arc = None
    _ARC_CFG = {}
    print(f"[ARC] arc_completeness unavailable ({_arce}); Pass C runs without arc factor", file=sys.stderr)

# Selection Sub-Plan B — reaction-worthy. A cheap intensity pre-signal (audio
# crowd-response + a post-beat chat-breadth spike) -> a small, boost-only
# multiplier. Intentionally the lightest axis (energy is already well-rewarded
# in Pass C); authenticity is left to the Vision Judge. Failure-soft -> 1.0.
try:
    import reaction_signals as _reaction
    _REACTION_CFG = _reaction.load_config()
    print(f"[REACTION] reaction-worthy loaded (enabled={_REACTION_CFG.get('enabled', True)}, ceil={_REACTION_CFG.get('multiplier_ceil')})", file=sys.stderr)
except Exception as _rxe:
    _reaction = None
    _REACTION_CFG = {}
    print(f"[REACTION] reaction_signals unavailable ({_rxe}); Pass C runs without reaction factor", file=sys.stderr)

# Phase 4 (B4) — fittable log-space ranker. Failure-soft + default-OFF: with no
# config/selection_ranker.json present, `maybe_rescore` returns None and Pass C keeps
# the hand-tuned final_score (byte-identical to legacy). See scripts/lib/ranker.py.
try:
    import ranker as _ranker
    if _ranker.load_weights() is not None:
        print("[RANKER] fitted selection_ranker.json loaded — Pass C re-scores via learned weights", file=sys.stderr)
except Exception as _rke:
    _ranker = None
    print(f"[RANKER] ranker unavailable ({_rke}); Pass C uses hand-tuned scores", file=sys.stderr)

# Selection Sub-Plan C — baseline-contrast. The streamer's per-VOD 'normal' is
# computed once (speaking-rate mean/std, modal segment-type, topic boundaries);
# moments that break it (rate/topic/genre deviation) get a small, boost-only
# multiplier. The corrective for energy bias and the most novel axis, so it is
# given the most authority (ceil 1.18). Failure-soft -> 1.0.
try:
    import baseline_contrast as _baseline
    _BASELINE_CFG = _baseline.load_config()
    print(f"[BASELINE] baseline-contrast loaded (enabled={_BASELINE_CFG.get('enabled', True)}, ceil={_BASELINE_CFG.get('multiplier_ceil')})", file=sys.stderr)
except Exception as _bce:
    _baseline = None
    _BASELINE_CFG = {}
    print(f"[BASELINE] baseline_contrast unavailable ({_bce}); Pass C runs without baseline factor", file=sys.stderr)

# Selection Sub-Plan E — engagement / discussion-worthiness. Surfaces low-impact
# but high-engagement "yap"/take clips: a firm stance + SUSTAINED post-moment chat
# discussion over [T, T+60] (breadth-gated debate, distinct from Plan B's [T, T+12]
# spike). Boost-only; the predicted-stance term is kept modest because the hot_take
# category + spicy/engagement style already weight stance. Failure-soft -> 1.0.
try:
    import engagement_signals as _engagement
    _ENGAGEMENT_CFG = _engagement.load_config()
    print(f"[ENGAGEMENT] engagement loaded (enabled={_ENGAGEMENT_CFG.get('enabled', True)}, ceil={_ENGAGEMENT_CFG.get('multiplier_ceil')})", file=sys.stderr)
except Exception as _ege:
    _engagement = None
    _ENGAGEMENT_CFG = {}
    print(f"[ENGAGEMENT] engagement_signals unavailable ({_ege}); Pass C runs without engagement factor", file=sys.stderr)

# Cross-axis compounding guardrail (clipping-quality-overhaul eval finding #1):
# the selection axes (A/B/C/E) each return a bounded multiplier, but their
# PRODUCT is unbounded — a moment tripping several correlated axes could run
# away. We accumulate them and clamp the product to [floor, ceil] before
# applying it once. Loaded from the "global" block of selection_axes.json.
_AXIS_FLOOR, _AXIS_CEIL = 0.80, 1.35
try:
    for _sap in (os.environ.get("CLIP_SELECTION_AXES_CONFIG"),
                 str(Path(__file__).resolve().parents[3] / "config" / "selection_axes.json")):
        if _sap and os.path.exists(_sap):
            _sa_glob = (json.loads(Path(_sap).read_text(encoding="utf-8")) or {}).get("global", {})
            _AXIS_FLOOR = float(_sa_glob.get("axis_multiplier_floor", _AXIS_FLOOR))
            _AXIS_CEIL = float(_sa_glob.get("axis_multiplier_ceil", _AXIS_CEIL))
            break
    print(f"[AXES] selection-axis product clamp = [{_AXIS_FLOOR}, {_AXIS_CEIL}]", file=sys.stderr)
except Exception as _sae:
    print(f"[AXES] global clamp using defaults [{_AXIS_FLOOR}, {_AXIS_CEIL}] ({_sae})", file=sys.stderr)

# Fix 3A — empirical ceiling used to soft-squash the user-facing `score` so top
# moments differentiate instead of all pinning at 1.000 (see the display block
# in the Pass C output loop). Top `final_score`s cluster ~1.0-1.6 (axis product
# clamp _AXIS_CEIL=1.35 × style/cross-val/speaker multipliers); 1.6 maps the
# realistic top to ~0.96 and reserves a true 1.000 for genuinely off-the-charts
# raw scores. Display-only — ranking + Stage 6 math use the unclamped raw_score.
_DISPLAY_SCALE = float(os.environ.get("CLIP_DISPLAY_SCORE_SCALE", "1.6") or "1.6")

# Fix 5 / arc Phase 3 (2026-06-06) — bounded guarantee that the single
# strongest A1 cross-chunk arc gets a final slot if none won Pass C on score.
# Arcs are A1's unique value-add (conceptual/ironic setup->payoff the keyword
# and local-LLM passes structurally miss), and the pipeline's philosophy is
# "a missed clip costs more than a false positive". Bounded to ONE arc, behind
# a quality floor (the arc's final_score must be >= MIN_RATIO x the weakest
# selected clip's) so a weak arc can't evict a much stronger clip. Env-tunable;
# CLIP_ARC_GUARANTEE=0 disables. See concepts/arc-aware-extraction.md Phase 3.
_ARC_GUARANTEE = os.environ.get("CLIP_ARC_GUARANTEE", "1").strip().lower() not in (
    "0", "false", "no", "off",
)
try:
    _ARC_GUARANTEE_MIN_RATIO = float(
        os.environ.get("CLIP_ARC_GUARANTEE_MIN_RATIO", "0.6") or "0.6"
    )
except ValueError:
    _ARC_GUARANTEE_MIN_RATIO = 0.6


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

# --- Plan A: adaptive clip COUNT (bounds + relative tail floor) --------------
# The legacy quota (MAX_CLIPS) fills to a duration-derived number with NO quality
# floor -> padding on thin VODs, truncation on dense ones. Flag-gated + failure-soft
# (concepts/plan-adaptive-clip-count-2026-07). OFF or SHADOW => byte-identical:
# SELECT_TARGET == MAX_CLIPS and the tail trim below only LOGS.
def _count_flag(_name):
    return os.environ.get(_name, "").strip().lower() in ("1", "true", "yes", "on")
_COUNT_ADAPTIVE = _count_flag("CLIP_COUNT_ADAPTIVE")
_COUNT_SHADOW = _count_flag("CLIP_COUNT_SHADOW")
try:
    _COUNT_TAU = float(os.environ.get("CLIP_COUNT_TAU", "0.94") or "0.94")
except ValueError:
    _COUNT_TAU = 0.94
# Non-shadow adaptive mode WIDENS the selection ceiling (x5, cap 24) so dense VODs
# aren't truncated; the tail floor trims weak picks back down. Bucket-guarantee math
# (clips_per_bucket) stays on the legacy MAX_CLIPS so per-bucket time-spread is
# unchanged -- the extra headroom is filled by the spread-preserving round-robin.
if _COUNT_ADAPTIVE and not _COUNT_SHADOW:
    SELECT_TARGET = max(3, min(int(math.ceil(vod_hours * 5)), 24))
else:
    SELECT_TARGET = MAX_CLIPS
if _COUNT_ADAPTIVE:
    print(f"[COUNT] adaptive count ON (shadow={_COUNT_SHADOW}) tau={_COUNT_TAU} "
          f"legacy_target={MAX_CLIPS} select_ceiling={SELECT_TARGET}", file=sys.stderr)

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
        "you're not gonna believe this", "so picture this", "fun fact",
        # 2026-06-04 (Delaware case): interview_revelation pattern markers.
        # Probe-and-reveal shape where someone is being asked or probed
        # and reveals something — the catalog's "interview_revelation"
        # pattern. Distinct from generic storytime but classed here
        # because the resulting clip IS a story payoff.
        "wait so tell me", "what really happened", "be honest with me",
        "i want to know", "you can tell me", "off the record",
        "between us", "the real story"
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
        "choreo", "choreography", "performing", "the dance",
        # 2026-06-04 (Delaware case): rap battle / freestyle / verbal-duel
        # vocabulary. Maps to the `rap_battle_freestyle` pattern in the
        # PATTERN_CATALOG; these chunks were invisible to Pass A before
        # (the rakai 10:54 rap battle scored 0/12 chunks because none of
        # the existing keyword sets had any signature for the pattern).
        # Conservative selection — only 3-5+ word phrases or phrases that
        # are unmistakeably rap-battle context. See case-rap-battle-missed.
        "kill him with words", "kill him again", "drop a verse",
        "with the gun talk", "let me cook", "rap battle",
        "freestyle", "go in", "round 2", "go again",
        "spit some bars", "bars on bars"
    ],
    "controversial": [
        "drama", "beef", "called out", "exposed", "receipts", "caught",
        "tea", "spill", "shade", "throwing shade", "shots fired",
        "that's cap", "lying", "fake", "two-faced", "snake",
        "banned", "canceled", "cancelled", "suspended", "kicked",
        "he said she said", "clipped out of context", "oh hell no",
        # 2026-06-04: social_callout pattern markers. These are framed as
        # "look at this/him/her" callouts toward an off-screen party —
        # the streamer pointing out someone else as the moment subject.
        "look at this guy", "look at this dude", "this dude is",
        "you see that", "did you see that", "watch this guy"
    ]
}

# Per-channel keyword packs (2026-06-12 — clipping-intelligence opportunity D).
# Mirrors config/streamer_prompts.json: config/channel_keywords.json carries
# per-channel slang/catchphrase additions, matched case-insensitively against
# VOD_BASENAME via `filename_substrings` (first match wins, same policy as the
# Whisper prompt packs). Additive only — extends the KEYWORD_SETS lists above;
# unknown categories are skipped (weights/thresholds wouldn't know them).
# Failure-soft: missing/unreadable file or no VOD_BASENAME → base sets only.
def _merge_channel_keywords(keyword_sets):
    path = os.environ.get("CLIP_CHANNEL_KEYWORDS") or "/root/.openclaw/channel_keywords.json"
    vb = (os.environ.get("VOD_BASENAME") or "").lower()
    if not vb or not os.path.exists(path):
        return
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8")) or {}
    except Exception as e:
        print(f"[PASS A] channel_keywords.json unreadable ({e}); base keyword sets only", file=sys.stderr)
        return
    for chan, spec in (data.get("channels") or {}).items():
        if not isinstance(spec, dict):
            continue
        subs = spec.get("filename_substrings") or []
        if not any(isinstance(s, str) and s and s.lower() in vb for s in subs):
            continue
        added = 0
        for cat, phrases in (spec.get("keywords") or {}).items():
            if cat not in keyword_sets or not isinstance(phrases, list):
                continue
            for ph in phrases:
                ph_l = ph.lower().strip() if isinstance(ph, str) else ""
                if ph_l and ph_l not in keyword_sets[cat]:
                    keyword_sets[cat].append(ph_l)
                    added += 1
        print(f"[PASS A] channel keyword pack '{chan}' matched '{vb}': +{added} phrases", file=sys.stderr)
        break  # first_match policy, mirroring streamer_prompts.json

try:
    _merge_channel_keywords(KEYWORD_SETS)
except Exception as _cke:
    print(f"[PASS A] channel keyword merge failed ({_cke}); base keyword sets only", file=sys.stderr)

# Word-boundary keyword matching (2026-06-12 — clipping-intelligence
# opportunity D). The scanner previously used plain substring checks, so "pog"
# fired on "pogo stick" and "lol" on "lollipop" — junk co-fires that inflate
# Pass A signal counts and, worse, the A∩B cross-validation denominator that
# Pass C trusts as its strongest lever. Each phrase compiles once to a regex:
#   - \b word boundaries at both ends,
#   - \W+ between words (tolerates "no, way!" punctuation/spacing),
#   - final-letter elongation tolerance ("let's gooo" still matches
#     "let's goooooo"; \b still keeps "pog" from matching "pogo").
# CLIP_KEYWORD_BOUNDARY=0 reverts to legacy substring matching.
_KEYWORD_BOUNDARY = os.environ.get("CLIP_KEYWORD_BOUNDARY", "1").strip().lower() not in (
    "0", "false", "no", "off",
)

def _compile_keyword(phrase):
    words = phrase.split()
    if not words:
        return None
    parts = [re.escape(w) for w in words]
    last = words[-1]
    if last and last[-1].isalpha():
        parts[-1] = parts[-1] + re.escape(last[-1]) + "*"
    try:
        return re.compile(r"\b" + r"\W+".join(parts) + r"\b")
    except re.error:
        return None

_KEYWORD_PATTERNS = {}
if _KEYWORD_BOUNDARY:
    for _cat, _phrases in KEYWORD_SETS.items():
        _KEYWORD_PATTERNS[_cat] = [
            _kp for _kp in (_compile_keyword(_ph) for _ph in _phrases) if _kp
        ]
    print(
        f"[PASS A] word-boundary keyword matching ON "
        f"({sum(len(v) for v in _KEYWORD_PATTERNS.values())} compiled patterns)",
        file=sys.stderr,
    )

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

# Fix 2 (2026-06-06): optional embedding-similarity category signal for Pass A.
# The literal keyword match is brittle; an additive cosine-to-category-prototype
# term catches semantically-relevant windows that lack the literal word. Reuses
# the M3 sentence-transformers stack (callbacks.embed_segments). Additive,
# capped, and GATED (CLIP_PASSA_EMBED, default OFF — it adds a model load to
# Pass A and changes recall, so opt-in + validate). Failure-soft: if the stack
# is unavailable, keywords still run. See concepts/detection-improvements-plan Fix 2.
_PASSA_EMBED = os.environ.get("CLIP_PASSA_EMBED", "0").strip().lower() in ("1", "true", "yes", "on")
# Empirically (2026-06-06) cosines between a 30s window and a one-line category
# description run ~0.15-0.27 — short prototypes are only mildly discriminative
# (the crudeness this fix targets is partly inherent to tiny prototypes). 0.20
# fires on clear matches (hot_take/reactive ~0.26) while skipping ambiguous
# ones. Richer prototypes (config/patterns.json signatures) are the follow-up
# upgrade — see concepts/detection-improvements-plan.md Fix 2.
_PASSA_EMBED_THRESHOLD = float(os.environ.get("CLIP_PASSA_EMBED_THRESHOLD", "0.20") or "0.20")
_PASSA_EMBED_WEIGHT = float(os.environ.get("CLIP_PASSA_EMBED_WEIGHT", "2.5") or "2.5")
_PASSA_EMBED_CAP = 1.0
# One-line semantic prototype per category (1:1 with KEYWORD_SETS keys), lifted
# from the legacy Pass B prompt so Pass A + that prompt share one source.
_CATEGORY_DESCRIPTIONS = {
    "hype": "exciting, intense, clutch plays, celebrations",
    "funny": "comedy, fails, awkward moments, ironic situations",
    "emotional": "vulnerable, heartfelt, real talk, genuine moments",
    "hot_take": "unpopular opinions, bold claims that viewers will debate",
    "storytime": "narrative buildup with payoff, anecdotes, storytelling",
    "reactive": "strong reactions to something, rage, shock, disbelief",
    "dancing": "physical performance, dancing, moves, physical comedy",
    "controversial": "drama, call-outs, edgy statements, tea-spilling, beef",
}
_CATEGORY_ORDER = list(_CATEGORY_DESCRIPTIONS.keys())


def keyword_scan(segments):
    """Segment-aware keyword scan with dynamic thresholds."""
    WINDOW_SIZE = 30
    STEP = 10
    flagged = []

    if not segments:
        return flagged

    max_time = max(s["end"] for s in segments)

    # Fix 2: pre-embed every window's text + the category prototypes in ONE
    # batched call (reuse the M3 stack), then cosine-compare per window below.
    # Gated + failure-soft; _proto_emb stays None when disabled/unavailable.
    _win_emb = {}
    _proto_emb = None
    if _PASSA_EMBED:
        try:
            import callbacks as _cb
            _wins = []
            _wt = segments[0]["start"]
            while _wt < max_time:
                _wsegs = [s for s in segments if s["start"] < _wt + WINDOW_SIZE and s["end"] > _wt]
                if _wsegs:
                    _wins.append({"start": _wt, "text": " ".join(s["text"] for s in _wsegs)})
                _wt += STEP
            if _wins:
                _proto_docs = [{"text": _CATEGORY_DESCRIPTIONS[c]} for c in _CATEGORY_ORDER]
                _res = _cb.embed_segments(
                    _proto_docs + _wins,
                    cache_dir=os.environ.get("CALLBACKS_CACHE_DIR"),
                )
                if _res is not None:
                    _emb, _ = _res
                    _n = len(_CATEGORY_ORDER)
                    _proto_emb = _emb[:_n]
                    for _i, _w in enumerate(_wins):
                        _win_emb[_w["start"]] = _emb[_n + _i]
                    print(f"[PASS A] embedding signal active ({len(_wins)} windows, "
                          f"{_n} category prototypes)", file=sys.stderr)
        except Exception as _ee:
            print(f"[PASS A] embedding signal unavailable ({type(_ee).__name__}: {_ee}); "
                  "keywords only", file=sys.stderr)
            _proto_emb = None

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

            # Category-specific keyword matching with segment weights.
            # Word-boundary regexes by default (see _KEYWORD_BOUNDARY above);
            # CLIP_KEYWORD_BOUNDARY=0 restores the legacy substring scan.
            for cat, phrases in KEYWORD_SETS.items():
                cat_signals = 0
                if _KEYWORD_BOUNDARY:
                    for _pat in _KEYWORD_PATTERNS.get(cat, ()):
                        if _pat.search(combined):
                            cat_signals += 1
                else:
                    for phrase in phrases:
                        if phrase in combined:
                            cat_signals += 1
                if cat_signals > 0:
                    weight = weights.get(cat, 1.0)
                    weighted = cat_signals * weight
                    categories_found[cat] = weighted
                    total_signals += weighted

            # Fix 2: additive embedding-similarity term (semantic recall) — a
            # window that's clearly e.g. "storytime" by meaning but lacks the
            # literal keywords still scores. Capped + segment-weighted so it
            # augments the keyword count without dominating it.
            if _proto_emb is not None:
                _wv = _win_emb.get(window_start)
                if _wv is not None:
                    for _ci, _cat in enumerate(_CATEGORY_ORDER):
                        _sim = float((_wv * _proto_emb[_ci]).sum())
                        if _sim >= _PASSA_EMBED_THRESHOLD:
                            _term = min(
                                (_sim - _PASSA_EMBED_THRESHOLD) * _PASSA_EMBED_WEIGHT
                                * weights.get(_cat, 1.0),
                                _PASSA_EMBED_CAP,
                            )
                            categories_found[_cat] = categories_found.get(_cat, 0) + _term
                            total_signals += _term

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


# Speed #5 I5.0: validation-only greedy-decode switch (see call_llm). Default off.
_PASSB_DETERMINISTIC = os.environ.get("CLIP_PASSB_DETERMINISTIC", "").strip().lower() in (
    "1", "true", "yes", "on")


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
            # Speed #5 I5.0: CLIP_PASSB_DETERMINISTIC forces greedy decoding so a
            # workers=1-vs-workers=N prompt/output comparison is meaningful (temp 0.3 is
            # stochastic run-to-run). VALIDATION-ONLY; never the production default.
            "temperature": 0.0 if _PASSB_DETERMINISTIC else 0.3,
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

    if "```" in clean:
        parts = clean.split("```")
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
    "engagement": "Prioritize discussion-worthy takes — a clear, relatable opinion on a topic the audience will argue about in the comments. Pause-and-opine moments, side-notes on a named brand/person/event, confident stances. The streamer doesn't need a big reaction; they need a take worth debating.",
    "variety": "Find ONE moment from EACH category. Maximum diversity across all categories."
}

# Unified prompt config (2026-06-12 — clipping-intelligence opportunity D).
# SEGMENT_PROMPTS + style_prompts were code constants that could silently
# drift from config/patterns.json (the legacy fallback prompt already had).
# config/prompts.json is now the editable source of truth: entries found
# there override the in-code defaults above, which remain as the
# failure-soft fallback when the file is missing or unreadable.
try:
    _prompts_path = os.environ.get("CLIP_PROMPTS_CONFIG") or "/root/.openclaw/prompts.json"
    if os.path.exists(_prompts_path):
        _pcfg = json.loads(Path(_prompts_path).read_text(encoding="utf-8")) or {}
        _seg_over = {
            k: v for k, v in (_pcfg.get("segment_prompts") or {}).items()
            if isinstance(v, str) and v.strip()
        }
        _style_over = {
            k: v for k, v in (_pcfg.get("style_prompts") or {}).items()
            if isinstance(v, str) and v.strip()
        }
        SEGMENT_PROMPTS.update(_seg_over)
        style_prompts.update(_style_over)
        print(
            f"[PROMPTS] config/prompts.json loaded "
            f"({len(_seg_over)} segment, {len(_style_over)} style entries)",
            file=sys.stderr,
        )
except Exception as _ppe:
    print(f"[PROMPTS] prompts config unavailable ({_ppe}); using in-code defaults", file=sys.stderr)

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

# Tier-3 A1+ arc-aware "chunk cards" (2026-06-06). Replaces the 15-word
# free-text per-chunk summary with a structured extraction of the *arc-bait*
# — concrete claims / predictions / named entities / open loops — so the A1
# global pass can match setup<->payoff on verifiable anchors instead of a
# genericized topic line. chunk_cards is PARALLEL to chunk_summaries:
#   chunk_cards[ci]  = {topic, claims[], predictions[], entities[], open_loops[]}
#   chunk_summaries  = [(ci, one_liner)]  (a flattened card line, kept so the
#                       Tier-1 Q1 prior-context block keeps working unchanged)
# Full design + research: AIclippingPipelineVault/wiki/concepts/arc-aware-extraction.
chunk_cards = {}

# Gap #1 (2026-06-06): chunks whose Pass B LLM call fails (timeout / HTTP 400 /
# connection refused / empty content) are captured here and retried ONCE after
# the main loop, when LM Studio has usually drained its queue / recovered from a
# transient stall. Without this, a momentary blip drops every moment in that
# ~5-min window — a pure false negative. See concepts/pass-b-false-negatives.
_failed_chunks = []


def _arc_extract_json_obj(text):
    """Best-effort single-JSON-object parse for a chunk card: strip a code
    fence, slice the outermost {...}, json.loads. Returns {} on failure.
    (This is a real .py module — plain backticks are fine here.)"""
    if not text:
        return {}
    t = text.strip()
    if "```" in t:
        parts = t.split("```")
        if len(parts) >= 2:
            t = parts[1]
            if t.lstrip().lower().startswith("json"):
                t = t.lstrip()[4:]
    a = t.find("{")
    b = t.rfind("}")
    if a < 0 or b <= a:
        return {}
    try:
        obj = json.loads(t[a:b + 1])
        return obj if isinstance(obj, dict) else {}
    except (json.JSONDecodeError, ValueError):
        return {}


def _arc_verify_quotes(items, source_text, max_items):
    """Keep only strings that actually appear in source_text (whitespace-
    normalized, case-insensitive) — kills hallucinated quotes so setup_text
    and the A1 'quote both halves' are trustworthy. Caps list length."""
    if not isinstance(items, list):
        return []
    src_norm = " ".join((source_text or "").lower().split())
    out = []
    for it in items:
        if not isinstance(it, str):
            continue
        s = it.strip().strip('"').strip("'").strip()
        if not s:
            continue
        s_norm = " ".join(s.lower().split())
        if len(s_norm) >= 3 and s_norm in src_norm:
            out.append(s[:120])
        if len(out) >= max_items:
            break
    return out


def _build_chunk_card(chunk_text):
    """One LLM call: extract arc-bait as a structured card. The model may
    reason in free text; only the final JSON is consumed (constrain the
    EMISSION, not the reasoning — EMNLP 2024 'Let Me Speak Freely?'). Quotes
    are substring-verified. Returns a card dict, or None on failure."""
    prompt = (
        "/no_think\n"
        "From this stream transcript chunk, extract anything that could PAY OFF "
        "LATER in the stream — a brag, claim, prediction, named stake, or dangling "
        "question. Quote EXACTLY from the transcript; if nothing of a type exists, "
        "use []. Prefer specific nouns over general topics.\n\n"
        f"Transcript:\n{chunk_text}\n\n"
        "Output ONLY this JSON object (no prose, no preamble):\n"
        '{"topic":"<=12 words","claims":[],"predictions":[],"entities":[],"open_loops":[]}'
    )
    resp = call_llm(prompt, max_tokens=4000, max_retries=0)
    obj = _arc_extract_json_obj(resp)
    if not obj:
        return None
    _topic = obj.get("topic")
    topic = _topic.strip()[:120] if isinstance(_topic, str) else ""
    card = {
        "topic": topic,
        "claims": _arc_verify_quotes(obj.get("claims"), chunk_text, 3),
        "predictions": _arc_verify_quotes(obj.get("predictions"), chunk_text, 2),
        "entities": _arc_verify_quotes(obj.get("entities"), chunk_text, 5),
        "open_loops": _arc_verify_quotes(obj.get("open_loops"), chunk_text, 2),
    }
    if not topic and not any(card[k] for k in
                             ("claims", "predictions", "entities", "open_loops")):
        return None
    return card


def _card_to_oneliner(card):
    """Flatten a card to a short readable line for the Pass B prior-context
    block (Tier-1 Q1): topic + the single most concrete anchor."""
    if not card:
        return ""
    topic = card.get("topic") or ""
    anchor = ""
    for k in ("claims", "predictions", "open_loops"):
        if card.get(k):
            anchor = card[k][0]
            break
    if not anchor and card.get("entities"):
        anchor = ", ".join(card["entities"][:2])
    if topic and anchor:
        line = f"{topic} — {anchor}"
    else:
        line = topic or anchor
    return line[:160]


def _build_arc_register(cards_by_chunk, chunk_time_map):
    """Phase 2: type-grouped register for the A1 global pass (counters
    'Lost in the Middle' — Liu 2023). Groups every chunk's arc-bait by signal
    type so arc detection is near-neighbour scanning within a register, not a
    linear read of N verbose lines. Returns '' when there's nothing to group."""
    def _ts(ci):
        tr = chunk_time_map.get(ci)
        if not tr:
            return "??:??"
        m, s = divmod(int(tr[0]), 60)
        return f"{m:02d}:{s:02d}"

    claims, preds, loops, topics = [], [], [], []
    for ci in sorted(cards_by_chunk):
        card = cards_by_chunk[ci]
        ts = _ts(ci)
        if card.get("topic"):
            topics.append(f"{ci:>2} {ts}  {card['topic']}")
        for q in card.get("claims", []):
            claims.append(f'{ci:>2} {ts}  "{q}"')
        for q in card.get("predictions", []):
            preds.append(f'{ci:>2} {ts}  "{q}"')
        for q in card.get("open_loops", []):
            loops.append(f'{ci:>2} {ts}  {q}')
    sections = []
    if claims:
        sections.append("== CLAIMS (chunk time — quote) ==\n" + "\n".join(claims))
    if preds:
        sections.append("== PREDICTIONS (chunk time — quote) ==\n" + "\n".join(preds))
    if loops:
        sections.append("== OPEN LOOPS (chunk time) ==\n" + "\n".join(loops))
    if topics:
        sections.append("== TOPICS (context) ==\n" + "\n".join(topics))
    return "\n\n".join(sections)


# Tier-4 Phase 4.2: accumulate per-chunk conversation_shape records and write
# them to /tmp/clipper/conversation_shape.json after the Pass B loop so Pass D
# and Stage 6 can look up shape data by chunk index. Keys are chunk_count
# (1-indexed) as strings.
CONVO_SHAPE_INDEX = {}

# 2026-06-04 Pass B dead-chunk gate — multi-signal version with audit log.
# State accumulated across the chunk loop; written to
# {TEMP_DIR}/pass_b_skipped_chunks.json after the loop so `logtool dead`
# can show what was skipped + why. See
# concepts/pipeline-optimizations-2026-06.md §5/§D.
_PASSB_SKIPPED_CHUNKS = []          # records: {chunk_index, time_range, signals, mode}
_PASSB_DEAD_STREAK = [0]            # list so nested writes can mutate (closure friendly)
# Speed #5 I5.0 (plan-speed56-execution-2026-07): per-chunk Pass-B prompt hashes. Purely
# observational — the GOLDEN BASELINE that the two-phase cut-over is validated against
# (a passing two-phase must reproduce every chunk's prompt hash). Written post-loop.
_PASSB_PROMPT_HASHES = {}           # chunk_count -> sha1(prompt)[:12]


def _passb_resolve_gate_mode():
    """Decide which dead-chunk gate mode to apply this run.

    Priority:
      1. ``CLIP_PASSB_DEAD_GATE`` env (off | strict | multi | sample)
      2. Legacy ``CLIP_PASSB_KEEP_DEAD_CHUNKS=1`` → ``off``
      3. Default → ``off`` (selection-safe; was ``strict`` 2026-06-04 morning)

    The default changed from ``strict`` to ``off`` after the rakai Delaware
    case showed the strict 2-signal gate has a ~5-10% false-negative rate
    and a missed clip displaces a worse clip in its time-bucket slot.
    See concepts/case-rap-battle-missed.md.
    """
    mode = os.environ.get("CLIP_PASSB_DEAD_GATE", "").strip().lower()
    if mode in ("off", "strict", "multi", "sample"):
        return mode
    legacy = os.environ.get("CLIP_PASSB_KEEP_DEAD_CHUNKS", "").strip().lower()
    if legacy in ("1", "true", "yes"):
        return "off"
    return "off"


def _passb_dead_sample_rate():
    """``CLIP_PASSB_DEAD_SAMPLE_RATE`` env → default 3.
    Every Nth dead chunk passes through to the LLM even in ``sample`` mode
    so the gate can't silently drop N+ consecutive moments.
    """
    raw = os.environ.get("CLIP_PASSB_DEAD_SAMPLE_RATE", "").strip()
    if raw:
        try:
            v = int(raw)
            if v > 0:
                return v
        except ValueError:
            pass
    return 3


_PASSB_GATE_MODE = _passb_resolve_gate_mode()
_PASSB_DEAD_SAMPLE_RATE = _passb_dead_sample_rate()
print(
    f"[PASS B] dead-chunk gate mode = {_PASSB_GATE_MODE}"
    + (f" (sample rate 1-in-{_PASSB_DEAD_SAMPLE_RATE})"
       if _PASSB_GATE_MODE == "sample" else "")
    + " — CLIP_PASSB_DEAD_GATE env to change "
      "(off | strict | multi | sample)",
    file=sys.stderr,
)


# Speed #5 CUT-OVER 1 (card-parallel, plan-speed56-execution-2026-07): the arc-card call
# (_build_chunk_card) is chunk-LOCAL — it depends ONLY on chunk_text — so all cards can be
# built in parallel up front instead of one-at-a-time inside the loop, removing them from
# the sequential critical path (~35% of Stage 4). Gated by CLIP_PASSB_CARD_WORKERS
# (default 1 = OFF = the original inline call, byte-identical). SAFETY BY CONSTRUCTION: the
# loop looks its card up BY chunk_text and FALLS BACK to the inline call on any miss, so
# even if this windowing walk ever drifts from the loop's, output is unchanged — only the
# parallelization is best-effort. Prompts / moment calls / grounding / summary-gating are
# ALL untouched (gate = prompt-hash manifest must equal the golden baseline). At temp 0 the
# precomputed card == the inline card (deterministic) → byte-identical; at production temp
# 0.3 it's a different-but-valid card draw (same statistical-equivalence as any temp>0 run).
_PASSB_PRECOMPUTED_CARDS = {}
try:
    _card_workers = int(os.environ.get("CLIP_PASSB_CARD_WORKERS", "1") or "1")
except ValueError:
    _card_workers = 1
if _card_workers >= 2:
    _pre_texts, _seen_txt, _cs = [], set(), segments[0]["start"]
    while _cs < max_time:               # mirrors the loop's windowing (1649-1665)
        _cd, _co, _ = _chunk_window_for(_cs)
        _ce, _os = _cs + _cd, max(0, _cs - _co)
        _segs = [s for s in segments if s["start"] < _ce + _co and s["end"] > _os]
        if _segs:
            _txt = format_chunk(_segs)
            if sum(len(s["text"].split()) for s in _segs) >= 15 and _txt not in _seen_txt:
                _seen_txt.add(_txt)
                _pre_texts.append(_txt)
        _cs += _cd
    if _pre_texts:
        from concurrent.futures import ThreadPoolExecutor as _TPE
        print(f"[PASS B] card-parallel precompute: {len(_pre_texts)} cards across "
              f"{_card_workers} threads", file=sys.stderr)
        def _precompute_card(_t):
            try:
                return _t, _build_chunk_card(_t)
            except Exception:
                return _t, None
        try:
            with _TPE(max_workers=_card_workers) as _ex:
                for _t, _card in _ex.map(_precompute_card, _pre_texts):
                    _PASSB_PRECOMPUTED_CARDS[_t] = _card   # store even None (card-failure)
        except Exception as _pce:
            print(f"[PASS B] card precompute failed ({type(_pce).__name__}: {_pce}); "
                  f"falling back to inline cards", file=sys.stderr)
            _PASSB_PRECOMPUTED_CARDS = {}

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

    # 2026-06-04 Pass B dead-chunk gate (round 4 — multi-signal + sampling
    # + audit log). Original "strict" 2-signal gate (keywords + audio events)
    # had a ~5-10% false-negative rate per the rakai Delaware case study.
    # Default is now ``off`` (no filtering); operators opt into ``multi``
    # (6 signals, conservative) or ``sample`` (multi + 1-in-N pass-through
    # of dead chunks so no more than N-1 consecutive skips can ever happen).
    # See concepts/pipeline-optimizations-2026-06.md §5/§D and
    # concepts/case-rap-battle-missed.md.
    if _PASSB_GATE_MODE != "off":
        # Signal 1: Pass A keyword hits in this chunk's time range.
        _kw_hits = sum(
            1 for _m in keyword_moments
            if chunk_start <= float(_m.get("timestamp", -1) or -1) < chunk_end
        )
        # Signal 2: Tier-2 M2 audio_events fires (rhythmic / crowd / music)
        # overlapping this chunk's time range.
        _audio_fires = 0
        if AUDIO_EVENTS:
            for (_ws, _we), _ev in AUDIO_EVENTS.items():
                if _we <= chunk_start or _ws >= chunk_end:
                    continue
                if (_ev.get("rhythmic_speech", 0) >= 0.7
                        or _ev.get("crowd_response", 0) >= 0.5
                        or _ev.get("music_dominance", 0) >= 0.6):
                    _audio_fires += 1
        # Signal 3: chat hard-events (sub/bit/raid/donation) — strong
        # clip-worthy signal regardless of speech content. Only available
        # in ``multi`` / ``sample`` modes; ``strict`` skips this.
        _chat_events = 0
        _speaker_count = 0
        _word_density = 0.0
        _subjective_segment = False
        if _PASSB_GATE_MODE in ("multi", "sample"):
            if CHAT_FEATURES is not None and not CHAT_FEATURES.is_empty():
                try:
                    _cw = CHAT_FEATURES.window(chunk_start, chunk_end)
                    _chat_events = sum(
                        int(_cw.get(_k, 0) or 0)
                        for _k in ("sub_count", "bit_count",
                                   "raid_count", "donation_count")
                    )
                except Exception:
                    _chat_events = 0
            # Signal 4: diarization speaker count — multi-speaker chunks
            # are often clip-worthy (interviews, guest banter, verbal duels
            # like the Delaware rap battle). Speakers are embedded in the
            # transcript segments by the Tier-2 M1 diarization stage; when
            # diarization didn't run, every speaker is None → count = 0
            # and this signal is a no-op (rest of the gate still fires).
            _speakers = {_s.get("speaker") for _s in chunk_segs
                         if _s.get("speaker")}
            _speakers.discard(None)
            _speaker_count = len(_speakers)
            # Signal 5: word density (engaged-talking proxy). A streamer
            # mid-story or mid-rant hits 2-4 words/sec; silent gameplay
            # drops below 1.0. Threshold 1.5 sits in the engaged band.
            _chunk_dur_s = max(1.0, float(chunk_end) - float(chunk_start))
            _word_density = float(word_count) / _chunk_dur_s
            # Signal 6: subjective-content segment types — these need LLM
            # judgement by default regardless of other signals.
            _subjective_segment = seg_type in (
                "reaction", "hot_take", "just_chatting"
            )

        if _PASSB_GATE_MODE == "strict":
            _alive = (_kw_hits > 0) or (_audio_fires > 0)
        else:  # multi or sample
            _alive = (
                _kw_hits > 0
                or _audio_fires > 0
                or _chat_events > 0
                or _speaker_count >= 2
                or _word_density >= 1.5
                or _subjective_segment
            )

        if not _alive:
            # Sampling pass-through: even when all signals are zero, run
            # the LLM every Nth dead chunk so a sustained quiet stretch
            # can't silently swallow N+ consecutive moments. The streak
            # resets every time a chunk passes the gate AND every time
            # we sample-pass-through.
            _force_sample = False
            if _PASSB_GATE_MODE == "sample":
                _PASSB_DEAD_STREAK[0] += 1
                if _PASSB_DEAD_STREAK[0] >= _PASSB_DEAD_SAMPLE_RATE:
                    _force_sample = True
                    _PASSB_DEAD_STREAK[0] = 0

            _signals_block = {
                "keywords": _kw_hits,
                "audio_events": _audio_fires,
                "chat_events": _chat_events,
                "diar_speakers": _speaker_count,
                "word_density": round(_word_density, 2),
                "segment_type": seg_type,
            }
            if _force_sample:
                print(
                    f"  Chunk {chunk_count} ({int(chunk_start)}s-{int(chunk_end)}s): "
                    f"gate={_PASSB_GATE_MODE} dead but SAMPLED "
                    f"(streak hit 1-in-{_PASSB_DEAD_SAMPLE_RATE}) — "
                    f"running LLM anyway. signals={_signals_block}",
                    file=sys.stderr,
                )
            else:
                _skip_record = {
                    "chunk_index": chunk_count,
                    "time_range": [int(chunk_start), int(chunk_end)],
                    "signals": _signals_block,
                    "mode": _PASSB_GATE_MODE,
                }
                _PASSB_SKIPPED_CHUNKS.append(_skip_record)
                print(
                    f"  Chunk {chunk_count} ({int(chunk_start)}s-{int(chunk_end)}s): "
                    f"gate={_PASSB_GATE_MODE} SKIPPED — "
                    f"kw={_kw_hits} audio={_audio_fires} chat={_chat_events} "
                    f"spk={_speaker_count} wd={_word_density:.1f} seg={seg_type} "
                    f"[CLIP_PASSB_DEAD_GATE=off to disable]",
                    file=sys.stderr,
                )
                chunk_start += cur_chunk_dur
                continue
        else:
            # Chunk passed the gate — reset the dead streak so the next
            # quiet patch starts a fresh sampling cycle.
            _PASSB_DEAD_STREAK[0] = 0

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

When in doubt, lean toward INCLUDING with a lower score (3-5) over skipping — the scoring system handles the rest. List EVERY distinct qualifying moment in this chunk — do NOT stop at a tidy 2-3. A busy chunk can legitimately have 5+ separate moments; a quiet one may have 0. Under-reporting a real moment is worse than including a weak one (downstream scoring + grounding filter the weak ones for free).

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

    # Speed #5 I5.0: capture the assembled prompt's hash (observational — the golden
    # baseline for the two-phase cut-over). Includes only chunks that reach the LLM call
    # (post-gate), so the manifest also proves the two-phase path gates the same chunks.
    try:
        import hashlib as _hl
        _PASSB_PROMPT_HASHES[chunk_count] = _hl.sha1(prompt.encode("utf-8")).hexdigest()[:12]
    except Exception:
        pass

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

        # Tier-3 A1+ arc-aware chunk card (2026-06-06, replaces the 15-word
        # summary). One LLM call extracts structured arc-bait (claims /
        # predictions / entities / open_loops) so the A1 global pass can match
        # setup<->payoff on concrete verifiable anchors instead of a
        # genericized "main topic" line. The card is stored in chunk_cards;
        # a flattened one-liner still goes into chunk_summaries so the Tier-1
        # Q1 prior-context block above keeps working unchanged. Quotes are
        # substring-verified against the chunk to kill hallucinations. The same
        # max_tokens=4000 budget (Gemma thinking headroom) is reused; the only
        # added cost is ~2-4x output tokens vs the old 15-word summary.
        # See concepts/arc-aware-extraction (research-backed plan).
        summary_text = ""
        card = None
        try:
            # Speed #5 card-parallel: use the precomputed card when present (built in
            # parallel before the loop); fall back to the inline call on any miss so
            # correctness never depends on the precompute — see the precompute block above.
            if chunk_text in _PASSB_PRECOMPUTED_CARDS:
                card = _PASSB_PRECOMPUTED_CARDS[chunk_text]
            else:
                card = _build_chunk_card(chunk_text)
        except Exception as _card_err:
            print(f"  Chunk {chunk_count}: card extraction errored ({_card_err}); continuing", file=sys.stderr)
        if card:
            chunk_cards[chunk_count] = card
            summary_text = _card_to_oneliner(card)
            print(
                f"  Chunk {chunk_count}: card — {len(card['claims'])} claim(s), "
                f"{len(card['predictions'])} prediction(s), "
                f"{len(card['entities'])} entity(s), "
                f"{len(card['open_loops'])} open-loop(s)",
                file=sys.stderr,
            )
        if not summary_text:
            # Neutral fallback: first ~12 transcript words. Better than nothing —
            # later chunks at least know what topic the prior chunk was on.
            _fallback = " ".join(s["text"] for s in chunk_segs[:6]).split()[:14]
            summary_text = " ".join(_fallback)[:160] or "(no summary)"
        chunk_summaries.append((chunk_count, summary_text))
    else:
        # Gap #1: don't drop the chunk — queue it for one end-of-pass retry.
        print(f"  Chunk {chunk_count}: LLM call failed — queued for end-of-pass retry", file=sys.stderr)
        _failed_chunks.append({
            "chunk_count": chunk_count,
            "chunk_start": chunk_start,
            "chunk_end": chunk_end,
            "seg_type": seg_type,
            "chunk_text": chunk_text,
            "prompt": prompt,
        })

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

# Gap #1 (2026-06-06): re-queue chunks whose LLM call failed mid-loop. call_llm
# already retried 3x in-line, but a transient LM Studio stall / restart can take
# out a whole chunk -> every moment in those ~5 min is a false negative. Give
# each failed chunk ONE more attempt now that the queue has usually drained.
# Best-effort recovery: recovered moments get the core scoring + a LIGHT
# grounding pass (denylist + content-overlap vs the chunk, so a hallucinated
# `why` still can't reach Stage 6); the per-chunk M1 speaker annotation and arc
# card are skipped (enrichments — recovering the moment matters more). Skipped
# entirely if a persistent outage is still in effect. See
# concepts/pass-b-false-negatives.
if _failed_chunks and not llm_net_outage():
    print(
        f"[PASS B] Re-queueing {len(_failed_chunks)} failed chunk(s) for one retry...",
        file=sys.stderr,
    )
    _recovered = 0
    for _fc in _failed_chunks:
        _resp = call_llm(_fc["prompt"])
        if not _resp:
            print(f"  Chunk {_fc['chunk_count']}: re-queue still failed", file=sys.stderr)
            if llm_net_outage():
                print("[PASS B] outage during re-queue — stopping retries", file=sys.stderr)
                break
            continue
        _cms = parse_llm_moments(_resp, int(_fc["chunk_start"]), int(_fc["chunk_end"]))
        _boost = SEGMENT_SCORE_BOOST.get(_fc["seg_type"], 0.0)
        for _m in _cms:
            _m["score"] = min(_m["score"] + _boost, 1.0)
            _m["segment_type"] = _fc["seg_type"]
            _m["requeued"] = True
            if _grounding is not None and _m.get("why"):
                try:
                    _gc = _grounding.cascade_check(
                        _m["why"], [_fc["chunk_text"]], GROUNDING_DENYLIST,
                        GROUNDING_CONFIG, min_overlap=0.15,
                    )
                    if not _gc["passed"]:
                        _m["why"] = ""
                        _m["grounding_fail"] = _gc["reason"]
                        _m["grounding_tier"] = _gc.get("tier")
                except Exception:
                    pass  # grounding is best-effort on the recovery path
        llm_moments.extend(_cms)
        _recovered += len(_cms)
        print(
            f"  Chunk {_fc['chunk_count']}: re-queue recovered {len(_cms)} moment(s)",
            file=sys.stderr,
        )
    print(
        f"[PASS B] Re-queue recovered {_recovered} moment(s) from "
        f"{len(_failed_chunks)} failed chunk(s)",
        file=sys.stderr,
    )
elif _failed_chunks:
    print(
        f"[PASS B] {len(_failed_chunks)} chunk(s) failed and were NOT retried "
        "(persistent LM Studio outage) — Pass A keyword moments still apply",
        file=sys.stderr,
    )

print(f"[PASS B] LLM found {len(llm_moments)} moments across {chunk_count} chunks", file=sys.stderr)
with open(f"{TEMP_DIR}/llm_moments.json", "w") as f:
    json.dump(llm_moments, f, indent=2)

# Tier-3 A1+ arc-aware chunk cards (2026-06-06): persist for observability so a
# future session can inspect what arc-bait each chunk produced (Phase 0/3 of the
# arc-aware-extraction plan). Keyed by chunk index (stringified for JSON).
try:
    _card_claims = sum(len(c.get("claims", [])) for c in chunk_cards.values())
    _card_preds = sum(len(c.get("predictions", [])) for c in chunk_cards.values())
    with open(f"{TEMP_DIR}/chunk_cards.json", "w") as _cf:
        json.dump({
            "total_cards": len(chunk_cards),
            "total_chunks": chunk_count,
            "total_claims": _card_claims,
            "total_predictions": _card_preds,
            "cards": {str(k): v for k, v in chunk_cards.items()},
        }, _cf, indent=2)
    print(
        f"[A1+] Wrote {len(chunk_cards)} arc-aware chunk cards "
        f"({_card_claims} claims, {_card_preds} predictions) to chunk_cards.json",
        file=sys.stderr,
    )
except (OSError, TypeError) as _ccerr:
    print(f"[A1+] failed to persist chunk_cards ({_ccerr}); continuing", file=sys.stderr)

# Persist the dead-chunk skip audit log so `logtool dead` can show what was
# skipped + why. Written even when no chunks were skipped (an empty list
# is a positive signal that the gate didn't drop anything this run).
try:
    with open(f"{TEMP_DIR}/passb_prompt_hashes.json", "w") as _phf:
        json.dump({
            "deterministic": _PASSB_DETERMINISTIC,
            "workers": int(os.environ.get("CLIP_PASSB_WORKERS", "1") or "1"),
            "n_chunks": len(_PASSB_PROMPT_HASHES),
            "hashes": _PASSB_PROMPT_HASHES,
        }, _phf, indent=2)
    print(f"[PASS B] prompt-hash manifest: {len(_PASSB_PROMPT_HASHES)} chunks "
          f"(deterministic={_PASSB_DETERMINISTIC}) -> passb_prompt_hashes.json", file=sys.stderr)
except Exception as _phe:
    print(f"[PASS B] prompt-hash manifest failed ({type(_phe).__name__}: {_phe})", file=sys.stderr)

try:
    with open(f"{TEMP_DIR}/pass_b_skipped_chunks.json", "w") as _sf:
        json.dump({
            "mode": _PASSB_GATE_MODE,
            "sample_rate": _PASSB_DEAD_SAMPLE_RATE if _PASSB_GATE_MODE == "sample" else None,
            "skipped_count": len(_PASSB_SKIPPED_CHUNKS),
            "total_chunks": chunk_count,
            "skipped": _PASSB_SKIPPED_CHUNKS,
        }, _sf, indent=2)
    if _PASSB_SKIPPED_CHUNKS:
        print(
            f"[PASS B] dead-chunk gate skipped {len(_PASSB_SKIPPED_CHUNKS)}/{chunk_count} "
            f"chunks (mode={_PASSB_GATE_MODE}) → pass_b_skipped_chunks.json "
            "(view with `logtool dead <run>`)",
            file=sys.stderr,
        )
    elif _PASSB_GATE_MODE != "off":
        print(
            f"[PASS B] dead-chunk gate (mode={_PASSB_GATE_MODE}) passed every chunk through — "
            "no false-negative risk this run",
            file=sys.stderr,
        )
except OSError as _skip_err:
    print(f"[PASS B] failed to persist skip log ({_skip_err}); continuing", file=sys.stderr)

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

        # Phase 2 (2026-06-06): prefer the type-grouped register built from the
        # arc-aware chunk cards. Grouping claims/predictions/open-loops by type
        # turns arc detection into near-neighbour scanning within a register
        # (counters 'Lost in the Middle' — Liu 2023) and gives the model a
        # structural prior on what an arc looks like. Falls back to the flat
        # summary skeleton when no cards were produced (all extractions failed).
        register = _build_arc_register(chunk_cards, chunk_time_map)
        if register:
            skeleton = register
            _skeleton_kind = "grouped-register"
        else:
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
            _skeleton_kind = "flat-summary-fallback"

        a1_prompt = f"""/no_think
Below are CLAIMS, PREDICTIONS and OPEN LOOPS pulled from a stream, each tagged with its chunk number and time. Quotes are verbatim from the streamer.

Find SETUP-PAYOFF ARCS that span MULTIPLE chunks:
- A claim made early that's later contradicted, exposed, or undermined (the canonical "I'm in my penthouse / actually it's not his penthouse" pattern)
- A prediction ("watch this work") that later lands or fails
- An open loop / dangling stake that later closes
- A theme introduced and revisited 30+ minutes later as a callback
- A friend / off-screen voice / chat exposing a fake or contradiction

Match on MEANING, not shared words — the payoff is often worded differently from the setup. A real arc has a BEAT (irony, contradiction, fulfillment, exposure); a merely shared topic is NOT an arc.

{skeleton}

Respond with ONLY a single JSON object: {{"arcs": [ ... ]}}. Each arc:
{{"setup_chunk": <int>, "payoff_chunk": <int>, "setup_time": "MM:SS", "payoff_time": "MM:SS", "arc_kind": "irony|contradiction|fulfillment|theme_return|exposure|prediction", "score": 1-10, "why": "one sentence naming BOTH halves, quoting each"}}.

Rules:
- setup_chunk MUST be earlier than payoff_chunk by at least 1 chunk.
- Both timestamps MUST fall within their chunk's range.
- Skip "arcs" that are just a shared topic — there must be a real beat.
- 0 arcs is a valid answer.  Quality > quantity.

If no arcs, respond {{"arcs": []}}."""
        print(
            f"[PASS B-GLOBAL] A1 sending {_skeleton_kind} "
            f"({len(chunk_cards)} cards, {len(skeleton)} chars) for cross-chunk arc detection",
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
            # Strip a ```json code fence if present (this is a real .py module
            # now — the old \-escaped form was a vestigial heredoc artifact that
            # never matched a real fence and emitted a SyntaxWarning).
            if "```" in text:
                _parts = text.split("```")
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

# ── Anomaly lane (upgrade plan Phase 1; concepts/case-incongruity-comedy) ──────
# Propose moments the transcript-only passes miss: strong audience REACTION with
# weak keyword signal (the bus / George-Bush class). Gated CLIP_ANOMALY_LANE
# (default OFF), boost-only (adds src=ANOMALY candidates to the same Pass-C pool;
# never gates/evicts), failure-soft. Cheap by design: the reaction signal is the
# librosa crowd_response ALREADY computed in Stage 2 (no CLAP-over-VOD cost); the
# few-shot LLM verifier runs only on the top-K (<=6) survivors.
if os.environ.get("CLIP_ANOMALY_LANE", "").strip().lower() in ("1", "true", "yes", "on"):
    try:
        import event_timeline as _et
        import anomaly_propose as _ap
        _aud = [{"t": (s + e) / 2.0, "label": "crowd",
                 "score": float(v.get("crowd_response", 0.0))}
                for (s, e), v in (AUDIO_EVENTS or {}).items()
                if float(v.get("crowd_response", 0.0)) >= 0.40]
        _words = []
        try:
            _tj = json.load(open(f"{TEMP_DIR}/transcript.json"))
            _segs = _tj.get("segments") if isinstance(_tj, dict) else _tj
            for _sg in (_segs or []):
                for _w in (_sg.get("words") or []):
                    if _w.get("start") is not None:
                        _words.append({"word": _w.get("word", ""),
                                       "start": _w.get("start"), "end": _w.get("end", _w.get("start"))})
        except Exception:
            pass
        _tl = _et.build_timeline(words=_words, audio_events=_aud)

        def _kw_explained(t0, t1):
            best = 0.0
            for _km in keyword_moments:
                if t0 <= _km.get("timestamp", -1) <= t1:
                    best = max(best, float(_km.get("score", 0.0)))
            return best

        _anoms = _ap.propose(
            _tl, _kw_explained, top_k=6,
            verify_fn=_ap.verify_via_lmstudio,
            render_fn=lambda a, b: _et.render_for_prompt(_tl, a, b))
        for _a in _anoms:
            _cat = _a.get("category") or "funny"
            if _cat == "anomaly":
                _cat = "funny"
            all_moments.append({
                "timestamp": round(_a["timestamp"]),
                # modest, verifier-confirmed score — a real contender, capped so
                # it can't crowd out strong LLM moments (boost-only spirit).
                "score": round(max(0.5, min(0.85, float(_a.get("score", 0.5)))), 3),
                "normalized_score": round(max(0.5, min(0.85, float(_a.get("score", 0.5)))), 3),
                "preview": str(_a.get("why", ""))[:120],
                "categories": [_cat],
                "primary_category": _cat,
                "category": _cat,
                "source": "anomaly",
                "src": "ANOMALY",
                "segment_type": get_segment_type(_a["timestamp"]),
                "clip_start": float(_a.get("clip_start") or _a["timestamp"]),
                "clip_end": float(_a.get("clip_end") or _a["timestamp"]),
                "clip_duration": round(float(_a.get("clip_end", 0) or 0)
                                       - float(_a.get("clip_start", 0) or 0), 2) or 8.0,
                "why": str(_a.get("why", "")),
                "cues": _a.get("cues", []),
            })
        print(f"[ANOMALY] lane proposed {len(_anoms)} src=ANOMALY moment(s) "
              f"from {len(_aud)} crowd-reaction windows", file=sys.stderr)
    except Exception as _anx:
        print(f"[ANOMALY] lane failed ({type(_anx).__name__}: {_anx}); skipping", file=sys.stderr)

all_moments.sort(key=lambda x: x["timestamp"])

# Deduplicate: merge moments within 25 seconds
deduped = []
for m in all_moments:
    merged = False
    for d in deduped:
        if abs(m["timestamp"] - d["timestamp"]) < 25:
            if m["source"] != d["source"]:
                # BUG 56 (2026-05-02): the merge identifies the LLM moment
                # (richer semantic preview, possibly different peak) and the
                # keyword moment (transcript snippet at the literal trigger
                # word) as the same event. Previously we kept the keyword's
                # timestamp + preview but inherited the LLM's clip window,
                # so peak T sat OUTSIDE the rendered clip — Stage 5 frame
                # extraction (T-2..T+5) and Stage 6 transcript grounding
                # (±8 s around T) both pulled content from before the clip
                # starts. Vision then titled the clip about whatever the
                # keyword caught, not what was actually rendered.
                #
                # Fix: when keyword + LLM merge AND the LLM has a real clip
                # window, treat the LLM as authoritative for peak T,
                # boundaries, primary_pattern, and the human-readable
                # preview/why. Keyword still contributes the cross-val
                # boost and "score" via the max() below.
                llm_side = m if m["source"] == "llm" else d
                kw_side  = d if m["source"] == "llm" else m
                d["normalized_score"] = min(
                    max(d["normalized_score"], m["normalized_score"]) * 1.25,
                    1.0,
                )
                d["cross_validated"] = True
                for cat in m.get("categories", []):
                    if cat not in d.get("categories", []):
                        d["categories"].append(cat)
                # Re-center on the LLM's peak when its clip window exists
                # AND the keyword's timestamp falls outside it. Keeps the
                # cohort happy for cases where keyword+LLM agree on peak
                # (just both fired in the same beat) — only re-centers
                # when they actually disagree about where the story is.
                llm_start = llm_side.get("clip_start")
                llm_end   = llm_side.get("clip_end")
                if llm_start is not None and llm_end is not None:
                    d["clip_start"] = llm_start
                    d["clip_end"]   = llm_end
                    # If the keyword's T is outside the LLM's window,
                    # snap d's timestamp to the LLM's T so downstream
                    # Stage 5 / 6 (which key off `timestamp`) work on
                    # frames + transcript that are inside the clip.
                    if not (llm_start <= d["timestamp"] <= llm_end):
                        d["timestamp"] = llm_side["timestamp"]
                # LLM preview/why is the semantic description ("Pattern
                # storytelling_arc: ..."); keyword preview is just the
                # transcript fragment containing the trigger word. The
                # old `> d * 0.8` check fired AFTER the ×1.25 boost, so
                # it was effectively dead code for cross-validated
                # moments. Always prefer the LLM's why when it exists.
                llm_why = llm_side.get("why") or llm_side.get("preview")
                if llm_why:
                    d["preview"] = llm_why
                    d["why"] = llm_why
                # Carry the LLM's pattern label into the merged record so
                # Pass D / Stage 6 / Stage 7 cross-validation paths can
                # use it. Without this the keyword survivor has no
                # primary_pattern even though the LLM identified one.
                if llm_side.get("primary_pattern") and not d.get("primary_pattern"):
                    d["primary_pattern"] = llm_side["primary_pattern"]
                # Same for primary_category — LLM categorization is more
                # reliable than the keyword's pattern-word lookup.
                if llm_side.get("primary_category"):
                    d["primary_category"] = llm_side["primary_category"]
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
        # Fix 5A (2026-06-06): use setdefault, NOT a hard reset. A standalone
        # arc (or M3 callback) is created with cross_validated=True because
        # skeleton-/embedding-level evidence is itself high-signal (see the A1
        # creation site + comment). The old `= False` clobbered that for any
        # arc that didn't merge with a nearby keyword/LLM moment — which is the
        # usual case — silently stripping its 1.20x boost and contradicting the
        # stated "first-class moments, cross_validated=True" intent. Keyword/LLM
        # moments never set the flag at creation, so they still default to False.
        m.setdefault("cross_validated", False)
        deduped.append(m)

print(f"  After dedup: {len(deduped)} unique moments ({sum(1 for d in deduped if d.get('cross_validated'))} cross-validated)", file=sys.stderr)

# ── Known-format tagging (upgrade plan Phase 3; concepts/reference-humor) ──────
# Attach `known_format` metadata when a moment's transcript matches a
# meme/skit-format trigger (George-Bush "ever heard of george", etc.) so Stage 6
# titling/hook + diagnostics can use it. Gated CLIP_KNOWN_FORMAT (default OFF),
# metadata-only (does NOT change score), failure-soft. Transcript-only here
# (verbal triggers); visual/audio corroboration is a Stage-6 v2 enhancement.
if os.environ.get("CLIP_KNOWN_FORMAT", "").strip().lower() in ("1", "true", "yes", "on"):
    try:
        import meme_match as _mm
        _kf_n = 0
        for _d in deduped:
            _txt = " ".join(str(_d.get(k, "")) for k in ("preview", "why"))
            _cues = [str(c).split("(")[0] for c in (_d.get("cues") or [])]
            _hits = _mm.match(_txt, audio_labels=_cues)
            if _hits:
                _d["known_format"] = [{"name": h["name"], "confidence": h["confidence"]}
                                      for h in _hits[:2]]
                _kf_n += 1
        print(f"[KNOWN_FORMAT] tagged {_kf_n}/{len(deduped)} moments", file=sys.stderr)
    except Exception as _kfe:
        print(f"[KNOWN_FORMAT] tagging failed ({type(_kfe).__name__}: {_kfe}); skipping", file=sys.stderr)

# --- LENGTH PENALTY FUNCTION (legacy; used when CLIP_LENGTH_NEUTRAL=0) ---
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


# --- Fix 4 (2026-06-06): length-NEUTRAL duration --------------------------
# The ~30s skew came from (a) the flat per-category default below and (b)
# length_penalty docking long clips at selection. When CLIP_LENGTH_NEUTRAL is
# on (default), duration follows CONTENT: boundary-less moments get a
# content-shaped window (expand across contiguous speech to silence gaps), and
# the selection multiplier judges TIGHTNESS (words/sec), not raw seconds — so a
# tight 70s monologue isn't penalized but a padded 30s clip is. Nothing pushes
# toward long; the duration-causing biases are simply removed.
# See concepts/detection-improvements-plan.md Fix 4.
_LENGTH_NEUTRAL = os.environ.get("CLIP_LENGTH_NEUTRAL", "1").strip().lower() not in (
    "0", "false", "no", "off",
)


def _wps_in(segs, a, b):
    """Overlap-weighted words/sec spoken in [a, b]. (Lifted from baseline_contrast.)"""
    if b <= a:
        return 0.0
    words = 0.0
    for s in segs:
        ss, se = float(s.get("start", 0) or 0), float(s.get("end", 0) or 0)
        if se <= a or ss >= b:
            continue
        ov = min(b, se) - max(a, ss)
        words += len((s.get("text") or "").split()) * (ov / max(1e-6, se - ss))
    return words / (b - a)


def _tightness_multiplier(m, segs):
    """Length-neutral replacement for length_penalty: judge content density, not
    seconds. Maps words/sec over the clip window into [floor, 1.0]; slower
    categories (emotional/storytime/arc/callback) keep a higher floor so a
    heartfelt pause isn't over-penalized. Duration is NOT an input."""
    cs, ce = m.get("clip_start"), m.get("clip_end")
    if cs is None or ce is None or ce <= cs:
        return 1.0
    wps = _wps_in(segs, float(cs), float(ce))
    # Engaged speech ~1.5-3.5 wps (gate calibration); map ~[0.7, 2.3] wps -> [0,1].
    dens = max(0.0, min((wps - 0.7) / (2.3 - 0.7), 1.0))
    cat = m.get("primary_category", "hype")
    floor = 0.85 if cat in ("emotional", "storytime", "arc", "callback") else 0.7
    return round(floor + (1.0 - floor) * dens, 4)


def _infer_content_window(ts, segs, max_dur):
    """Content-shaped window for a boundary-less moment: anchor on the speech
    segment nearest the peak, grow outward across contiguous segments (silence
    gap <= GAP) up to max_dur. Bounded to >=15s. Symmetric ~30s fallback when
    the transcript is too sparse around ts."""
    GAP = 1.5
    ordered = sorted(
        (s for s in segs if s.get("start") is not None and s.get("end") is not None),
        key=lambda s: float(s["start"]),
    )
    if not ordered:
        return max(0, int(ts) - 15), int(ts) + 15
    ai = min(range(len(ordered)),
             key=lambda i: abs((float(ordered[i]["start"]) + float(ordered[i]["end"])) / 2 - ts))
    start, end = float(ordered[ai]["start"]), float(ordered[ai]["end"])
    j = ai + 1
    while j < len(ordered):
        ss, se = float(ordered[j]["start"]), float(ordered[j]["end"])
        if ss - end > GAP or se - start > max_dur:
            break
        end = se; j += 1
    k = ai - 1
    while k >= 0:
        ss, se = float(ordered[k]["start"]), float(ordered[k]["end"])
        if start - se > GAP or end - ss > max_dur:
            break
        start = ss; k -= 1
    if end - start > max_dur:
        # Anchor speech run longer than the cap — center a max_dur window on the peak.
        start = max(start, float(ts) - max_dur / 2.0)
        end = start + max_dur
    if end - start < 15:
        end = start + 15
    return max(0, int(start)), int(end)


# Compute clip duration for each moment
for m in deduped:
    if "clip_start" in m and "clip_end" in m:
        m["clip_duration"] = m["clip_end"] - m["clip_start"]
    else:
        cat = m.get("primary_category", "hype")
        if _LENGTH_NEUTRAL:
            # Fix 4a: content-shaped window instead of a flat per-category default.
            _maxd = 150 if cat in ("storytime", "emotional") else 90
            cs, ce = _infer_content_window(m["timestamp"], segments, _maxd)
            m["clip_start"], m["clip_end"] = cs, ce
            m["clip_duration"] = ce - cs
        else:
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

# Plan C: baseline-contrast — compute the streamer's per-VOD 'normal' ONCE
# (before the scoring loop) so Pass C can boost moments that break it. Topic
# boundaries are flattened from the conversation_shape index; the segment-type
# router supplies the modal genre. Failure-soft: any error -> baseline skipped.
_BASELINE = None
if _baseline is not None:
    try:
        _tbs = [float(_b.get("t")) for _rec in (CONVO_SHAPE_INDEX or {}).values()
                for _b in (_rec.get("topic_boundaries") or []) if _b.get("t") is not None]
        _BASELINE = _baseline.compute_baseline(
            segments, segment_at=get_segment_type, topic_boundaries=_tbs, cfg=_BASELINE_CFG)
        print(f"[BASELINE] per-VOD baseline: rate_mean={_BASELINE.get('rate_mean')} "
              f"std={_BASELINE.get('rate_std')} n={_BASELINE.get('n_windows')} "
              f"ok={_BASELINE.get('ok')} modal={_BASELINE.get('modal_segment')} "
              f"topic_bounds={len(_BASELINE.get('topic_boundaries') or [])}", file=sys.stderr)
    except Exception as _be:
        _BASELINE = None
        print(f"[BASELINE] baseline computation failed ({_be}); Pass C runs without baseline-contrast", file=sys.stderr)

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
        "engagement": {"hot_take": 1.25, "controversial": 1.2, "storytime": 1.1, "emotional": 1.1},
        "variety": {}
    }

    weights = weight_map.get(CLIP_STYLE, {})
    multiplier = weights.get(cat, 1.0)
    styled_score = base * multiplier
    m["style_multiplier"] = round(multiplier, 3)   # Phase 4 B1: stamp factors for the fittable ranker

    # Cross-validated moments get multiplicative boost
    m["cross_val_factor"] = 1.20 if m.get("cross_validated") else 1.0
    if m.get("cross_validated"):
        styled_score *= 1.20

    # Tier-2 M1: speaker-change boost. Multi-speaker windows where no single
    # voice dominates are the canonical "off-screen voice exposes the
    # streamer" / "friend interruption" pattern. Multiplicative ×1.15, smaller
    # than cross-val so a real keyword+LLM agreement still outranks a
    # speaker-only signal.
    _spk_boost = m.get("speaker_count", 0) >= 2 and (m.get("dominant_speaker_share") or 1.0) < 0.7
    m["speaker_factor"] = 1.15 if _spk_boost else 1.0   # Phase 4 B1
    if _spk_boost:
        styled_score *= 1.15

    # Rare-pattern bonus (case-rap-battle-missed Phase 2): style-independent
    # rarity multiplier from the Pattern Catalog's `pass_c_bonus` field, so a
    # rare high-value pattern survives bucket competition. See _PATTERN_BONUS
    # at module top. Stamped for observability; 1.0 when no pattern matched.
    _pb = _PATTERN_BONUS.get(m.get("primary_pattern") or "", 1.0)
    m["pattern_bonus"] = _pb   # Phase 4 B1: always stamp (default 1.0) for the fittable ranker
    if _pb != 1.0:
        styled_score *= _pb

    # --- Selection axes (Plans A/B/C/E) ------------------------------------
    # Each axis returns a bounded, failure-soft multiplier. They ACCUMULATE into
    # one product (`axis_mult`) that is globally clamped before being applied, so
    # no moment can run away by tripping several (often correlated) axes at once.
    # Each axis still only re-ranks — none ever gates a clip.
    axis_mult = 1.0
    _mt = float(m.get("timestamp", 0) or 0)

    # Plan A: arc-completeness — gentle, category-aware factor rewarding
    # self-contained setup->payoff arcs and lightly penalizing fragments. The
    # only axis that may demote (floor 0.85). Failure-soft -> 1.0 multiplier.
    if _arc is not None:
        try:
            _ace = _arc.evaluate(m, segments, shape_module=CONVO_SHAPE, markers=CONVO_MARKERS, cfg=_ARC_CFG)
        except Exception:
            _ace = {"completeness": None, "multiplier": 1.0, "signals": {}}
        axis_mult *= _ace.get("multiplier", 1.0)
        m["arc_completeness"] = _ace.get("completeness")
        m["arc_multiplier"] = round(_ace.get("multiplier", 1.0), 3)
        if _ace.get("signals"):
            m["arc_signals"] = _ace["signals"]
    else:
        m["arc_completeness"] = None
        m["arc_multiplier"] = 1.0

    # Plan B: reaction-worthy — cheap intensity pre-signal (audio crowd-pop +
    # post-beat chat-breadth spike). Boost-only, smallest ceiling. Authenticity
    # is the Vision Judge's job; this only measures that a reaction is present.
    if _reaction is not None:
        _audio_sig = None
        try:
            if AUDIO_EVENTS:
                _awin = float(getattr(_audio_events_mod, "WINDOW_SIZE_DEFAULT", 30.0))
                _audio_sig = _audio_events_mod.lookup_window(
                    AUDIO_EVENTS, max(0.0, _mt - _awin / 2.0), _awin)
        except Exception:
            _audio_sig = None
        _chat_sig = None
        try:
            if CHAT_FEATURES is not None:
                _pw = float(_REACTION_CFG.get("post_window_s", 12.0))
                _chat_sig = CHAT_FEATURES.window(_mt, _mt + _pw)
        except Exception:
            _chat_sig = None
        try:
            _rx = _reaction.evaluate(m, segments, audio=_audio_sig, chat=_chat_sig, cfg=_REACTION_CFG)
        except Exception:
            _rx = {"reaction_score": None, "multiplier": 1.0, "signals": {}}
        axis_mult *= _rx.get("multiplier", 1.0)
        m["reaction_score"] = _rx.get("reaction_score")
        m["reaction_multiplier"] = round(_rx.get("multiplier", 1.0), 3)
        if _rx.get("signals"):
            m["reaction_signals"] = _rx["signals"]
    else:
        m["reaction_score"] = None
        m["reaction_multiplier"] = 1.0

    # Plan C: baseline-contrast — boost moments that break the streamer's own
    # norm (two-sided rate deviation + start-aligned topic pivot + genre shift).
    # Boost-only; the corrective for energy bias (a quiet beat on a hype streamer
    # can win). Orthogonal to the M1 speaker boost — no double-count.
    if _baseline is not None and _BASELINE is not None:
        try:
            _bc = _baseline.evaluate(m, segments, baseline=_BASELINE, segment_at=get_segment_type, cfg=_BASELINE_CFG)
        except Exception:
            _bc = {"contrast_score": None, "multiplier": 1.0, "signals": {}}
        axis_mult *= _bc.get("multiplier", 1.0)
        m["baseline_contrast"] = _bc.get("contrast_score")
        m["baseline_multiplier"] = round(_bc.get("multiplier", 1.0), 3)
        if _bc.get("signals"):
            m["baseline_signals"] = _bc["signals"]
    else:
        m["baseline_contrast"] = None
        m["baseline_multiplier"] = 1.0

    # Plan E: engagement / discussion-worthiness — boost low-impact-but-talkable
    # takes (a firm stance + sustained post-moment chat discussion over [T, T+60]).
    # Boost-only; predicted-stance kept modest to avoid double-counting hot_take.
    if _engagement is not None:
        _ech = None
        try:
            if CHAT_FEATURES is not None:
                _epw = float(_ENGAGEMENT_CFG.get("post_window_s", 60.0))
                _ech = CHAT_FEATURES.window(_mt, _mt + _epw)
        except Exception:
            _ech = None
        try:
            _eg = _engagement.evaluate(m, segments, chat=_ech, shape_module=CONVO_SHAPE, markers=CONVO_MARKERS, cfg=_ENGAGEMENT_CFG)
        except Exception:
            _eg = {"engagement_score": None, "multiplier": 1.0, "signals": {}}
        axis_mult *= _eg.get("multiplier", 1.0)
        m["engagement_score"] = _eg.get("engagement_score")
        m["engagement_multiplier"] = round(_eg.get("multiplier", 1.0), 3)
        if _eg.get("signals"):
            m["engagement_signals"] = _eg["signals"]
    else:
        m["engagement_score"] = None
        m["engagement_multiplier"] = 1.0

    # Compounding guardrail: clamp the accumulated A-E product, then apply once.
    axis_mult = max(_AXIS_FLOOR, min(_AXIS_CEIL, axis_mult))
    m["axis_multiplier"] = round(axis_mult, 3)
    styled_score *= axis_mult

    # Fix 4b: length-NEUTRAL — judge tightness (words/sec), not raw duration,
    # so long-but-dense clips aren't penalized and padded clips are. Falls back
    # to the legacy duration penalty when CLIP_LENGTH_NEUTRAL=0.
    lp = _tightness_multiplier(m, segments) if _LENGTH_NEUTRAL else length_penalty(m["clip_duration"])
    # BUG 37: was min(... * lp, 1.0) — caused 9/10 selected clips to land
    # at exactly 1.000 because cross-val × style × position routinely pushed
    # base 0.7-0.9 over the cap. Score saturation destroyed Pass C's
    # ranking — at the cap, ties resolve by insertion order (chunk index),
    # which compounds the bucket-overflow bias (BUG 36). Soft-cap instead:
    # raw scores can land in [0, ~1.4]; we display by clipping to 1.0 only
    # at the user-facing rendering side, and Pass C ranks on the raw value.
    m["length_penalty"] = lp
    m["final_score"] = round(styled_score * lp, 4)
    # Phase 4 (B4): if a fitted ranker is loaded, replace final_score with its
    # learned score (linear space). Default-off -> maybe_rescore returns None and
    # this is a no-op; even an all-default fitted file reproduces styled_score*lp
    # exactly (exp∘log identity). Position-weight + bucket-norm still apply below.
    if _ranker is not None:
        try:
            _rescored = _ranker.maybe_rescore(m)
            if _rescored is not None:
                m["final_score"] = round(_rescored, 4)
        except Exception:
            pass  # failure-soft: keep the hand-tuned score

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

# Plan A (adaptive clip count): snapshot the post-position, PRE-bucket-norm score.
# The bucket-norm blend below LIFTS dead-bucket moments (good for time spread) but
# HIDES tail weakness -- the relative tail floor must judge on this un-lifted score.
for m in deduped:
    m["pre_bucket_score"] = m["final_score"]

# Plan B: calibrated absolute COUNT. If a fitted, count-GATED ranker is loaded
# (count_mode='absolute'), the clip COUNT becomes a consequence of content: keep
# candidates whose calibrated pre_bucket_score (= sigmoid(ranker)xposition, which is
# exactly what maybe_rescore+position produced above) clears the learned threshold,
# clamped to [3, ceiling]. Double-gated (a fitted file that PASSED fit_ranker's count
# gate) => default-off. Plan A's relative tail floor is skipped when this is active
# (theta IS the boundary); the absolute floor in the trim block enforces theta on the
# final set, and the [3, ceiling] bounds remain the backstop against a miscalibrated fit.
_COUNT_ABSOLUTE = False
_cthresh = None
try:
    if _ranker is not None:
        _cmode, _cthresh = _ranker.count_config()
        if _cmode == "absolute" and _cthresh is not None:
            _count_ceiling = max(3, min(int(math.ceil(vod_hours * 5)), 24))
            _n_elig = sum(1 for m in deduped if (m.get("pre_bucket_score") or 0.0) >= _cthresh)
            SELECT_TARGET = max(3, min(_n_elig, _count_ceiling))
            _COUNT_ABSOLUTE = True
            print(f"[COUNT] absolute threshold theta={_cthresh:.4f}: {_n_elig} eligible "
                  f"-> target {SELECT_TARGET} (ceiling {_count_ceiling})", file=sys.stderr)
except Exception as _cae:
    print(f"[COUNT] absolute-count path skipped ({type(_cae).__name__}: {_cae})", file=sys.stderr)
    _COUNT_ABSOLUTE = False
    _cthresh = None

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

# --- Selection-axis observability (rank stamps + per-run tuning report) ------
# final_score is now finalized (post style/length/position/bucket-norm) and the
# axis multipliers are stamped, but selection hasn't pruned yet — so this is the
# point to (1) stamp each candidate's base_rank (by the pre-multiplier
# normalized_score) and pass_c_rank (by the post-axis final_score) for the
# rank-churn view, and (2) emit an aggregate report of what each axis actually
# did this run. Both are failure-soft and never affect selection.
def _axis_stats(vals):
    s = sorted(float(v) for v in vals if v is not None)
    if not s:
        return {"n": 0}
    n = len(s)
    median = s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0
    return {"n": n, "min": round(s[0], 3), "median": round(median, 3),
            "mean": round(sum(s) / n, 3), "max": round(s[-1], 3)}

def _emit_axis_report(moments):
    n = len(moments)

    def block(mult_key, score_key, ceil, floor=None):
        mults = [float(m.get(mult_key) or 1.0) for m in moments]
        active = sum(1 for x in mults if abs(x - 1.0) > 1e-9)
        b = {"active": active, "pct_active": round(100.0 * active / max(1, n), 1),
             "at_ceil": sum(1 for x in mults if ceil is not None and abs(x - ceil) < 1e-6),
             "multiplier": _axis_stats(mults)}
        if floor is not None:
            b["at_floor"] = sum(1 for x in mults if abs(x - floor) < 1e-6)
        sc = [m.get(score_key) for m in moments if m.get(score_key) is not None]
        if sc:
            b["score"] = _axis_stats(sc)
        return b

    # how often the global clamp actually bound the accumulated axis product
    bound = 0
    for m in moments:
        prod = (float(m.get("arc_multiplier") or 1.0) * float(m.get("reaction_multiplier") or 1.0)
                * float(m.get("baseline_multiplier") or 1.0) * float(m.get("engagement_multiplier") or 1.0))
        if abs(prod - float(m.get("axis_multiplier") or 1.0)) > 1e-6:
            bound += 1

    report = {
        "candidates": n,
        "style": CLIP_STYLE,
        "dependencies": {
            "arc": _arc is not None,
            "reaction": _reaction is not None,
            "baseline": _baseline is not None and _BASELINE is not None,
            "engagement": _engagement is not None,
            "chat_features": CHAT_FEATURES is not None,
            "audio_events": bool(AUDIO_EVENTS),
            "conversation_shape": CONVO_SHAPE is not None,
        },
        "global_clamp": {"floor": _AXIS_FLOOR, "ceil": _AXIS_CEIL, "bound_count": bound},
        "axes": {
            "arc": block("arc_multiplier", "arc_completeness",
                         float(_ARC_CFG.get("multiplier_ceil", 1.12)), float(_ARC_CFG.get("multiplier_floor", 0.85))),
            "reaction": block("reaction_multiplier", "reaction_score", float(_REACTION_CFG.get("multiplier_ceil", 1.10))),
            "baseline": block("baseline_multiplier", "baseline_contrast", float(_BASELINE_CFG.get("multiplier_ceil", 1.18))),
            "engagement": block("engagement_multiplier", "engagement_score", float(_ENGAGEMENT_CFG.get("multiplier_ceil", 1.12))),
        },
    }
    try:
        with open(f"{TEMP_DIR}/axis_report.json", "w", encoding="utf-8") as _rf:
            json.dump(report, _rf, indent=2)
    except OSError:
        pass

    d = report["dependencies"]
    print(f"[AXES] Selection-axis report over {n} candidates (style={CLIP_STYLE}):", file=sys.stderr)
    print(f"[AXES]   deps: arc={d['arc']} reaction={d['reaction']}(audio={d['audio_events']},chat={d['chat_features']}) "
          f"baseline={d['baseline']} engagement={d['engagement']} shape={d['conversation_shape']}", file=sys.stderr)
    for name, blk in report["axes"].items():
        ms = blk["multiplier"]
        rng = f"{ms.get('min','-')}/{ms.get('median','-')}/{ms.get('max','-')}" if ms.get("n") else "-"
        extra = f" at_floor={blk['at_floor']}" if "at_floor" in blk else ""
        print(f"[AXES]   {name:10s} active {blk['active']:>3}/{n} ({blk['pct_active']:>5.1f}%)  "
              f"mult min/med/max {rng}  at_ceil={blk['at_ceil']}{extra}", file=sys.stderr)
    print(f"[AXES]   global clamp [{_AXIS_FLOOR},{_AXIS_CEIL}] bound {bound} moment(s)", file=sys.stderr)

try:
    for _r, _m in enumerate(sorted(deduped, key=lambda x: x.get("normalized_score", 0.0) or 0.0, reverse=True), 1):
        _m["base_rank"] = _r
    for _r, _m in enumerate(sorted(deduped, key=lambda x: x.get("final_score", 0.0) or 0.0, reverse=True), 1):
        _m["pass_c_rank"] = _r
    _emit_axis_report(deduped)
except Exception as _are:
    print(f"[AXES] axis report skipped ({_are})", file=sys.stderr)

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

_phase2_round_robin(buckets, selected, SELECT_TARGET, min_spacing)

# Phase 2.5 (Fix 5 / arc Phase 3): guarantee the single strongest A1 arc a
# slot if none survived Phase 1/2 on score. A1 arcs are the conceptual/ironic
# cross-chunk setup->payoffs that keyword + local-LLM passes structurally miss
# (the whole reason A1 exists), and the pipeline treats a missed clip as more
# costly than a false positive. Bounded to ONE swap, gated by a quality floor
# so a weak arc can't displace a much stronger clip, and spacing-safe.
if _ARC_GUARANTEE and selected and not any(
    s.get("primary_category") == "arc" for s in selected
):
    _arc_cands = sorted(
        (m for m in deduped if m.get("primary_category") == "arc"),
        key=lambda x: x.get("final_score", 0.0), reverse=True,
    )
    _non_arcs = [s for s in selected if s.get("primary_category") != "arc"]
    if _arc_cands and _non_arcs:
        _weakest = min(_non_arcs, key=lambda x: x.get("final_score", 0.0))
        _floor = _ARC_GUARANTEE_MIN_RATIO * _weakest.get("final_score", 0.0)
        for _arc in _arc_cands:
            if _arc.get("final_score", 0.0) < _floor:
                break  # sorted desc — no remaining arc clears the quality floor
            _kept = [s for s in selected if s is not _weakest]
            _sp = min_spacing(_arc)
            if any(abs(_arc["timestamp"] - s["timestamp"]) < _sp for s in _kept):
                continue  # too close to a clip we're keeping; try the next arc
            selected.remove(_weakest)
            selected.append(_arc)
            print(
                f"[ARC] Phase 2.5 guaranteed arc T={_arc['timestamp']} "
                f"(kind={_arc.get('arc_kind','?')}, score={_arc.get('final_score',0):.3f}) "
                f"over weakest clip T={_weakest['timestamp']} "
                f"({_weakest.get('final_score',0):.3f}) "
                f"[CLIP_ARC_GUARANTEE=0 to disable]",
                file=sys.stderr,
            )
            break

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
    while len(final) < SELECT_TARGET and any(by_category.values()):
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
    final = selected[:SELECT_TARGET]
else:
    # Auto: category cap — no single category exceeds 50% of clips
    selected.sort(key=lambda x: x["final_score"], reverse=True)
    final = []
    cat_counts = {}
    max_per_cat = max(2, int(SELECT_TARGET * 0.50))
    for m in selected:
        cat = m.get("primary_category", "hype")
        if cat_counts.get(cat, 0) < max_per_cat:
            final.append(m)
            cat_counts[cat] = cat_counts.get(cat, 0) + 1
        if len(final) >= SELECT_TARGET:
            break
    # Backfill if we didn't reach the target due to category cap
    if len(final) < SELECT_TARGET:
        for m in selected:
            if m not in final:
                final.append(m)
                if len(final) >= SELECT_TARGET:
                    break

final.sort(key=lambda x: x["final_score"], reverse=True)

# --- Plan A tail floor: drop weak tail picks (relative to THIS run) ----------
# Floor = tau x median(pre_bucket_score of the top `MAX_CLIPS` picks). Judges on the
# PRE-bucket-norm score (the blend hides tail weakness). Only ever REMOVES; never
# below min 3; never trims an arc-guaranteed pick (Phase 2.5 gave it its own quality
# floor and the pipeline treats a missed arc as costlier than a false positive).
# Failure-soft: any exception leaves `final` exactly as selected.
_count_trim_info = None
if (_COUNT_ADAPTIVE or _COUNT_ABSOLUTE) and len(final) > 3:
    try:
        def _pbs(_m):
            return _m.get("pre_bucket_score", _m.get("final_score", 0.0)) or 0.0
        _ranked_final = sorted(final, key=_pbs, reverse=True)
        if _COUNT_ABSOLUTE:
            # Plan B: theta IS the boundary (calibrated, count-gated). Not shadowed —
            # the gate already validated it leave-one-VOD-out.
            _floor = float(_cthresh); _median = None
            _shadow = False; _fmode = "absolute"; _fdesc = f"theta={_floor:.3f}"
        else:
            # Plan A: relative floor = tau x median(pre_bucket_score of top MAX_CLIPS).
            _head = _ranked_final[:MAX_CLIPS] or _ranked_final
            _svals = sorted(_pbs(m) for m in _head)
            _n = len(_svals)
            _median = _svals[_n // 2] if _n % 2 else 0.5 * (_svals[_n // 2 - 1] + _svals[_n // 2])
            _floor = _COUNT_TAU * _median
            _shadow = _COUNT_SHADOW; _fmode = "relative"
            _fdesc = f"tau{_COUNT_TAU}x median{_median:.3f}={_floor:.3f}"
        _keep, _trimmed = [], []
        for m in _ranked_final:
            if len(_keep) < 3 or _pbs(m) >= _floor or m.get("primary_category") == "arc":
                _keep.append(m)
            else:
                _trimmed.append(m)
        if _trimmed:
            _verb = "WOULD trim" if _shadow else "trimmed"
            print(f"[COUNT] {_verb} {len(_trimmed)} tail clip(s) ({_fmode} floor {_fdesc}; "
                  f"keep {len(_keep)}/{len(final)}):", file=sys.stderr)
            for m in _trimmed:
                _ttl = (m.get("why") or m.get("preview") or "")[:60]
                print(f"[COUNT]   - t={m.get('timestamp')} score={_pbs(m):.3f} "
                      f"[{m.get('primary_category','?')}] {_ttl}", file=sys.stderr)
            if not _shadow:
                _keep_ids = {id(m) for m in _keep}
                final = [m for m in final if id(m) in _keep_ids]
        _count_trim_info = {
            "mode": _fmode, "shadow": _shadow, "tau": _COUNT_TAU,
            "floor": round(_floor, 4), "median": round(_median, 4) if _median is not None else None,
            "legacy_target": MAX_CLIPS, "select_ceiling": SELECT_TARGET,
            "kept": len(_keep), "trimmed": len(_trimmed),
            "trimmed_ts": [m.get("timestamp") for m in _trimmed],
        }
    except Exception as _cte:
        print(f"[COUNT] tail floor skipped ({type(_cte).__name__}: {_cte})", file=sys.stderr)
        _count_trim_info = None

print(f"  Final selection: {len(final)} clips across {len(set(min(int(m['timestamp']/bucket_duration), NUM_BUCKETS-1) for m in final))} of {NUM_BUCKETS} time buckets", file=sys.stderr)

# 2026-06-05 — Pass C candidate trace (observability fix). Dump every deduped
# candidate's full scoring chain so post-run analysis can answer "why did the
# Delaware rap battle lose to T=1828?" without re-deriving multipliers. Pairs
# with `logtool selection <run>` for human-readable inspection.
# See concepts/pipeline-optimizations-2026-06 and case-rap-battle-missed.
try:
    _selected_ids = {id(m) for m in final}
    _trace_records = []
    for _m in deduped:
        _t = float(_m.get("timestamp", 0) or 0)
        _bucket_idx = min(int(_t / bucket_duration), NUM_BUCKETS - 1) if bucket_duration else 0
        _rec = {
            "timestamp": _m.get("timestamp"),
            "bucket_idx": _bucket_idx,
            "selected": id(_m) in _selected_ids,
            "source": _m.get("source"),
            "primary_pattern": _m.get("primary_pattern"),
            "primary_category": _m.get("primary_category"),
            "segment_type": _m.get("segment_type") or get_segment_type(_t),
            "score": _m.get("score"),
            "normalized_score": _m.get("normalized_score"),
            "cross_validated": bool(_m.get("cross_validated", False)),
            "clip_duration": _m.get("clip_duration"),
            "length_penalty": _m.get("length_penalty"),
            "position_weight": _m.get("position_weight"),
            # Phase 4 B1 — the remaining fittable factors + raw interaction signals,
            # so pass_c_candidates.json is a complete feature row for fit_ranker.py.
            "style_multiplier": _m.get("style_multiplier"),
            "cross_val_factor": _m.get("cross_val_factor"),
            "speaker_factor": _m.get("speaker_factor"),
            "pattern_bonus": _m.get("pattern_bonus"),
            "reaction_score": _m.get("reaction_score"),
            "keyword_score": _m.get("keyword_score"),
            "motion_score": _m.get("motion_score"),
            "arc_multiplier": _m.get("arc_multiplier"),
            "reaction_multiplier": _m.get("reaction_multiplier"),
            "baseline_multiplier": _m.get("baseline_multiplier"),
            "engagement_multiplier": _m.get("engagement_multiplier"),
            "axis_multiplier": _m.get("axis_multiplier"),
            "pre_bucket_score": _m.get("pre_bucket_score"),
            "final_score": _m.get("final_score"),
            "base_rank": _m.get("base_rank"),
            "pass_c_rank": _m.get("pass_c_rank"),
        }
        # 1-line preview helps the human inspector identify the moment without
        # cross-referencing the full transcript. Truncate aggressively.
        _why = _m.get("why") or _m.get("preview") or ""
        if _why:
            _rec["why"] = str(_why)[:140]
        _trace_records.append(_rec)
    # Compute per-bucket rank within bucket (1-indexed by final_score desc).
    _by_bucket = {}
    for _r in _trace_records:
        _by_bucket.setdefault(_r["bucket_idx"], []).append(_r)
    for _bi, _rs in _by_bucket.items():
        _rs.sort(key=lambda x: x.get("final_score") or 0.0, reverse=True)
        for _idx, _r in enumerate(_rs, 1):
            _r["bucket_rank"] = _idx

    _trace_payload = {
        # L1.1 (learning loop): stamp the source VOD so labels (Path C viewer-clip
        # alignments, keyed by VOD) join to this run's trace without a sidecar map.
        "vod": os.environ.get("VOD_BASENAME", ""),
        "total_candidates": len(deduped),
        "selected_count": len(final),
        "num_buckets": NUM_BUCKETS,
        "bucket_duration_s": round(bucket_duration, 1),
        "clips_per_bucket": clips_per_bucket,
        "overflow_slots": overflow_slots,
        "style": CLIP_STYLE,
        "max_time_s": round(max_time, 1),
        "count_trim": _count_trim_info,   # Plan A: adaptive-count trim record (or None)
        "candidates": _trace_records,
    }
    with open(f"{TEMP_DIR}/pass_c_candidates.json", "w", encoding="utf-8") as _tf:
        json.dump(_trace_payload, _tf, indent=2)
    print(f"[PASS C] candidate trace ({len(deduped)} candidates, {len(final)} selected) -> "
          f"pass_c_candidates.json (view with `logtool selection`)",
          file=sys.stderr)
except OSError as _trace_err:
    print(f"[PASS C] failed to write candidate trace ({_trace_err}); continuing", file=sys.stderr)

# Write output with clip boundaries and 0-1 scores.
# BUG 37: final_score may exceed 1.0 because we soft-cap during ranking
# (saturation at the cap destroyed Pass C's tie-breaking). Clip to [0, 1]
# only at the user-facing serialization boundary.
output = []
for m in final:
    raw = m.get("final_score", 0.0)
    # Fix 3A (2026-06-06): soft-squash the display instead of a hard
    # min(raw, 1.0). Top Pass C moments cluster at raw 1.0-1.6 (the axis
    # product is clamped at 1.35, times the style/cross-val/speaker
    # multipliers), so the old hard clamp pinned every winner at a
    # visually-tied 1.000. Dividing by an empirical ceiling (_DISPLAY_SCALE)
    # spreads them — e.g. raw 1.33->0.83, 1.54->0.96 — so the `score` field
    # is informative. Selection is UNAFFECTED: it ranks on the unclamped
    # `final_score`, and Stage 6 drives all its score math off `raw_score`
    # (below); `score` is display-only. See clip-quality-remediation-2026-06
    # Fix 3.
    display_score = round(min(max(raw, 0.0) / _DISPLAY_SCALE, 1.0), 3)
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
        # Plan A — arc-completeness (0-1) + the applied multiplier; also consumed
        # by the future Stage 5.5 Vision Judge and the clip-card diagnostics.
        "arc_completeness": m.get("arc_completeness"),
        "arc_multiplier": m.get("arc_multiplier", 1.0),
        # Plan B — reaction-worthy intensity pre-signal + the CLAMPED product of
        # every selection-axis multiplier actually applied to raw_score (so the
        # Vision Judge / diagnostics can see the net axis contribution).
        "reaction_score": m.get("reaction_score"),
        "reaction_multiplier": m.get("reaction_multiplier", 1.0),
        # Plan C — baseline-contrast (how much this moment breaks the streamer's norm)
        "baseline_contrast": m.get("baseline_contrast"),
        "baseline_multiplier": m.get("baseline_multiplier", 1.0),
        # Plan E — engagement / discussion-worthiness (talkable take + sustained chat)
        "engagement_score": m.get("engagement_score"),
        "engagement_multiplier": m.get("engagement_multiplier", 1.0),
        "axis_multiplier": m.get("axis_multiplier", 1.0),
        # Rank-churn observability: base_rank = rank by pre-multiplier
        # normalized_score; pass_c_rank = rank by post-axis final_score. The
        # Vision Judge later stamps vision_rank, giving base -> pass_c -> vision.
        "base_rank": m.get("base_rank"),
        "pass_c_rank": m.get("pass_c_rank"),
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
        # Upgrade plan (2026-07): anomaly-lane provenance + known meme/skit format.
        # None for normal moments; present when the lanes are on + matched.
        "src": m.get("src"),
        "known_format": m.get("known_format"),
        # BUG 66 (complete fix 2026-07-09): the ACTUAL source gap. primary_pattern lived only
        # in the trace, never in hype_moments → Stage 6 preserved None → Stage 7 P-TIGHT saw ""
        # → the rap/freestyle exemption NEVER fired (a rap_battle_freestyle clip, T=9832, was
        # still trimmed after the first fix). Emit it here so it flows Stage 4→6→7.
        # (segment_type already emitted above.)
        "primary_pattern": m.get("primary_pattern"),
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
    print(f"  T={m['timestamp']}s [{m['category']}] score={m['score']:.3f} raw={raw:.4f} dur={dur}s lp={lp} pw={pw:.2f} arc={m.get('arc_completeness')} rx={m.get('reaction_score')} bc={m.get('baseline_contrast')} eng={m.get('engagement_score')} ax={m.get('axis_multiplier',1.0)} segment={m.get('segment_type','')} src={m['source']}{xv} — {m.get('why','')[:60]}", file=sys.stderr)
print(f"  Category breakdown: {json.dumps(cats_found)}", file=sys.stderr)
print(f"  Segment breakdown: {json.dumps(segs_found)}", file=sys.stderr)
print(f"Detected {len(output)} clip-worthy moments")
for m in output:
    dur = m.get("clip_duration", 30)
    print(f"  T={m['timestamp']}s score={m['score']:.3f} [{m['category']}] ({m.get('segment_type','')}) dur={dur}s — {m.get('why','')[:60]}")
