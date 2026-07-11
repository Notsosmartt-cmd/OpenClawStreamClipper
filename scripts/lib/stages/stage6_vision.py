"""Stage 6 — Vision Enrichment (frame scoring, titles, hook, originality hints).

Extracted from scripts/clip-pipeline.sh as part of the modularization plan
(see AIclippingPipelineVault/wiki/concepts/modularization-plan.md, Phase A2).

Reads bash-interpolated config from environment variables:
    LLM_URL, VISION_MODEL_STAGE6

Vision is non-gatekeeping: it can only boost moment scores or annotate them,
never eliminate them. See concepts/vision-enrichment for the score-blending
formula. Behavior is byte-identical to the pre-extraction heredoc.
"""
import json, re, base64, os, sys, time, threading
from concurrent.futures import ThreadPoolExecutor
try:
    import urllib.request
except:
    pass

LLM_URL = os.environ["LLM_URL"]
# Phase 5.1: Stage 6 honors vision_model_stage6 override (falls back to
# vision_model when unset). See config/models.json.
VISION_MODEL = os.environ["VISION_MODEL_STAGE6"]
TEMP_DIR = os.environ.get("CLIP_WORK_DIR", "/tmp/clipper")

# Phase 1.1: 3-tier grounding cascade on the VLM's title/hook/description
# plus the regenerate-once policy on cascade failure. Loads same module the
# Stage 4 heredoc uses; the cascade config + denylist are bind-mounted from
# ./config into /root/.openclaw inside the container.
sys.path.insert(0, "/root/scripts/lib")
import thinking

# Part 1 (P1.2/P1.4) — creative-caption quality gate. `title`/`hook` are the
# owner-VISIBLE fields and historically ran Tier-1 only (no fidelity/voice
# check). CLIP_CAPTION_JUDGE (LLM fidelity+voice) and CLIP_CAPTION_LINT
# (deterministic AI-tell linter) both default ON, both individually
# toggleable, both failure-soft. See concepts/plan-captions-and-ab-variants-2026-07.
_CAPTION_JUDGE = os.environ.get("CLIP_CAPTION_JUDGE", "1").strip().lower() not in ("0", "false", "no", "off")
_CAPTION_LINT = os.environ.get("CLIP_CAPTION_LINT", "1").strip().lower() not in ("0", "false", "no", "off")
try:
    import caption_lint as _caption_lint
except Exception:
    _caption_lint = None

try:
    import grounding as _grounding
    GROUNDING_DENYLIST = _grounding.load_denylist()
    GROUNDING_CONFIG = _grounding.load_grounding_config()
    _t2 = GROUNDING_CONFIG.get("tier_2", {}).get("enabled", False)
    _t3 = GROUNDING_CONFIG.get("tier_3", {}).get("enabled", False)
    _regen = GROUNDING_CONFIG.get("regeneration", {}).get("enabled", True)
    print(
        f"[GROUND] Stage 6 loaded denylist with {len(GROUNDING_DENYLIST)} categories "
        f"(Tier 2={_t2}, Tier 3={_t3}, regen={_regen})",
        file=sys.stderr,
    )
except Exception as _e:
    _grounding = None
    GROUNDING_DENYLIST = {}
    GROUNDING_CONFIG = {}
    print(f"[GROUND] grounding module unavailable in Stage 6 ({_e}); skipping gate", file=sys.stderr)

# Phase 2: load chat features for Stage 6 hard-event ground truth check.
# When the ±8 s window around the moment has any sub/bit/raid/donation
# events, the cascade allows the corresponding claim through; when all are
# zero it hard-rejects the claim. Burst/emote-density chat scoring was
# removed 2026-05-01.
CHAT_FEATURES = None
CHAT_EVENT_MAP = {}
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
        CHAT_EVENT_MAP = _chat_feat.denylist_event_map()
        if CHAT_FEATURES.is_empty():
            CHAT_FEATURES = None
            print("[CHAT] Stage 6: chat file is empty — running without hard-event check", file=sys.stderr)
        else:
            print(
                f"[CHAT] Stage 6: chat features loaded "
                f"({CHAT_FEATURES.message_count} msgs); hard-event ground truth ENABLED",
                file=sys.stderr,
            )
    except Exception as _ce:
        CHAT_FEATURES = None
        print(f"[CHAT] Stage 6 chat_features unavailable ({_ce})", file=sys.stderr)

# Load stream profile for context hints
stream_profile = {"dominant_type": "unknown", "is_variety": False}
try:
    with open(f"{TEMP_DIR}/stream_profile.json") as f:
        stream_profile = json.load(f)
except:
    pass

with open(f"{TEMP_DIR}/hype_moments.json") as f:
    moments = json.load(f)

# Transition-animation inference (gated). Only ask the vision model for
# cuts/flashes when the matching Stage-7 mode is on; otherwise the prompt is
# byte-identical to before — zero risk to normal runs. Rule-based modes
# (CLIP_JUMP_CUTS=gaps, the flash cadence) don't need the model at all.
_EDIT_LLM_CUTS = os.environ.get("CLIP_JUMP_CUTS", "off").strip().lower() in ("llm", "on")
_EDIT_LLM_FLASH = os.environ.get("CLIP_FLASH_CUTS", "off").strip().lower() in ("on",)
_EDIT_INFER = _EDIT_LLM_CUTS or _EDIT_LLM_FLASH

# EVERY moment that survived detection WILL be rendered.
# Vision only enriches with titles/descriptions and can boost the score.
enriched = []

# Total stage timeout: 20 minutes max for all vision calls combined
VISION_STAGE_START = time.time()
VISION_STAGE_TIMEOUT = 3600  # 1 hour — 35B model: up to ~220s/moment × 11 moments + margin
VISION_PER_MOMENT_TIMEOUT = 300  # 5 min per moment — 35B models need ~200s per vision call

# BUG 31: Stage 6 mirrors Pass B's outage detector. Track consecutive vision
# calls that fail with a network signature (Errno 101, timed out, ECONNREFUSED).
# When it hits the limit we abandon vision enrichment for the remaining moments
# — every moment still renders with its transcript-based defaults, the pipeline
# just skips the title/description AI step.
#
# 2026-06-04: counter is now thread-safe under the parallel-dispatch path
# (``STAGE6_WORKERS`` env var; default 2 — see ``_resolve_stage6_workers``).
# Without a lock, two concurrent workers seeing streak<LIMIT each fail and
# leave streak=2 instead of 2 (read-modify-write race); semantically the
# "3 consecutive failures" loosens to "3 since last success" but is still
# the right circuit-breaker for the LM-Studio-down case.
_VISION_NET_FAIL_STREAK = 0
_VISION_NET_FAIL_LIMIT = 3
_VISION_NET_FAIL_LOCK = threading.Lock()
_VISION_NET_PATTERNS = (
    "Network is unreachable",
    "Errno 101",
    "Connection refused",
    "Errno 111",
    "Name or service not known",
    "timed out",
    "Read timed out",
)

# Stage 6 parallel dispatch (2026-06-04). VLM calls are per-moment and
# fully independent — no cross-moment state to coordinate beyond the
# net-fail counter above. Concurrent HTTP to LM Studio is allowed but
# the VLM image-encoder pipeline may serialize internally; cap at 2
# workers (conservative) and measure before increasing. Tunable via
# ``STAGE6_WORKERS`` env var; set to 1 to force the original serial loop.
_STAGE6_WORKERS_DEFAULT = 2


def _resolve_stage6_workers():
    """``STAGE6_WORKERS`` env override → ``_STAGE6_WORKERS_DEFAULT``.
    0 or 1 disables parallelism (forces the original serial loop)."""
    _env = os.environ.get("STAGE6_WORKERS", "").strip()
    if _env:
        try:
            _v = int(_env)
            if _v > 0:
                return _v
        except ValueError:
            pass
    return _STAGE6_WORKERS_DEFAULT

def _vision_looks_like_outage(err_msg):
    s = str(err_msg)
    return any(pat in s for pat in _VISION_NET_PATTERNS)


def _derive_baseline_title(why, category, T):
    """Build a meaningful baseline title used when vision can't generate one.

    Vision overrides this on success. When vision fails (LM Studio outage,
    HTTP 400, parse error, stage timeout) the baseline survives all the way
    to Stage 7. The pre-Tier-4 baseline was f"Clip_T{T}" — that string passes
    through Stage 7's sanitizer (which keeps alphanumerics + space + dash and
    drops underscores) and produces filenames like "ClipT1805.mp4" plus the
    same string burned into the hook caption. Both look like the pipeline
    was broken when really vision was just unavailable.

    Preference order:
      1. First sentence of Pass B's `why` field (capped at 60 chars), AFTER
         stripping the "Pattern <id>:" debug prefix that Pass B prepends.
         Without that strip the baseline leaks a raw pattern label — e.g.
         "Pattern setup_external_contradiction: ..." sanitizes (Stage 7) to the
         garbage title "Pattern setupexternalcontradiction Streamer claims".
         See concepts/clip-quality-remediation-2026-06.md Fix 1.
      2. "<Category> at MM:SS" — predictable and readable when `why` is empty.
    """
    if why:
        # Strip Pass B's "Pattern <id>:" prefix so the title is a readable
        # sentence, never a sanitized pattern label (Fix 1B).
        why = re.sub(r"^\s*Pattern\s+[A-Za-z0-9_]+\s*:\s*", "", why, flags=re.IGNORECASE)
        first = why.strip().split(". ")[0].strip().rstrip(".")
        if len(first) > 60:
            first = first[:57].rstrip() + "..."
        if first:
            return first
    cat_pretty = (category or "moment").replace("_", " ").strip().capitalize() or "Moment"
    try:
        T_int = int(T)
    except Exception:
        T_int = 0
    mm, ss = T_int // 60, T_int % 60
    return f"{cat_pretty} at {mm}:{ss:02d}"


# Hook-text template fallback (concepts/hook-engineering-2026-06). Every clip
# should ship a top-of-video hook card; the vision model produces one when it
# can, but when vision is skipped/fails (or grounding nulls the hook) the card
# was previously absent. These category-keyed templates fill that gap so the
# clip still opens with a curiosity hook. Vision always overrides the template.
_HOOK_TEMPLATES_CACHE: dict | None = None
_CAPTION_STYLE_CACHE: dict | None = None


def _load_caption_style():
    """Phase 7.2 — load config/caption_style.json (learned competitor caption VOICE)
    with the standard env -> Linux -> repo fallback. Cached. Returns {} on any
    failure so the hook prompt simply gets no voice examples (prior behavior)."""
    global _CAPTION_STYLE_CACHE
    if _CAPTION_STYLE_CACHE is not None:
        return _CAPTION_STYLE_CACHE
    candidates = [
        os.environ.get("CLIP_CAPTION_STYLE_CONFIG"),
        "/root/.openclaw/caption_style.json",
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "config", "caption_style.json"),
    ]
    cfg: dict = {}
    for c in candidates:
        if c and os.path.exists(c):
            try:
                cfg = json.loads(open(c, encoding="utf-8").read()) or {}
                break
            except (OSError, ValueError):
                cfg = {}
    _CAPTION_STYLE_CACHE = cfg
    return cfg


def _caption_style_fewshot():
    """A short 'match this VOICE' block for the Stage 6 hook prompt, built from the
    learned caption-style profile. Returns "" unless config/caption_style.json has
    enabled=true AND CLIP_CAPTION_STYLE isn't force-disabled — so it's opt-in (owner
    reviews the profile first) and failure-soft (no file / disabled => no change to
    the prompt, identical to prior behavior). Style-only: we feed the voice summary +
    a few real hook phrasings so the model mimics the STYLE, never the exact words.

    GENERALIZATION GUARD (owner directive 2026-07-05: high-variety content incoming,
    pipeline must stay generalized): the profile's `applies_to` list scopes the learned
    voice to the channels/niche it was distilled FROM. Non-empty applies_to + no
    substring match against the current VOD basename => NO injection — an unknown
    channel gets the neutral prompt, never another niche's slang. Empty/missing
    applies_to = applies everywhere (explicit owner choice)."""
    if os.environ.get("CLIP_CAPTION_STYLE", "1").strip().lower() in ("0", "false", "no", "off"):
        return ""
    cfg = _load_caption_style()
    if not cfg.get("enabled"):
        return ""
    applies = [str(a).strip().lower() for a in (cfg.get("applies_to") or []) if str(a).strip()]
    if applies:
        vod = os.environ.get("VOD_BASENAME", "").lower()
        if not vod or not any(a in vod for a in applies):
            return ""
    ex = [e for e in (cfg.get("hook_phrasings") or cfg.get("examples") or []) if e][:5]
    if not ex:
        return ""
    voice = (cfg.get("voice_summary") or "").strip()
    casing = (cfg.get("casing_rule") or "").strip()
    ex_lines = "\n".join(f"- {e}" for e in ex)
    return (f"\nCaption VOICE to match (learned from top clips in this niche — mimic the "
            f"STYLE/tone/casing, NOT the words):\n{voice}"
            f"{(' Casing: ' + casing) if casing else ''}\n"
            f"Example hook phrasings in this voice:\n{ex_lines}\n")


# Part 1 (P1.3) — the voice CONTRACT. The recurring owner critique is that
# titles/hooks "look too much like an AI wrote it" (Title Case Headlines,
# scare-quotes around invented names, "The 'X' Y", clickbait adjectives). The
# neutral prompt was letting the model default to headline-ese. This block bans
# those patterns explicitly and shows a good/bad contrast. Default on; disable
# with CLIP_CAPTION_VOICE=0. The deterministic caption_lint linter enforces the
# same rules after generation (regenerate-once on drift).
def _caption_voice_contract() -> str:
    if os.environ.get("CLIP_CAPTION_VOICE", "1").strip().lower() in ("0", "false", "no", "off"):
        return ""
    return (
        "\nCAPTION VOICE — the \"title\" and \"hook\" must sound like a real short-form "
        "creator talking, NOT an AI headline:\n"
        "- sentence case or all-lowercase; NEVER Title Case Every Word\n"
        "- NO quotation marks around invented names (write: samurai slicer diss — NOT: the \"Samurai Slicer\" Diss)\n"
        "- NO \"The X: Y\" or \"The 'X' Y\" headline shapes; NO em-dashes; NO hashtags\n"
        "- NO clickbait words (epic, insane, hilarious, ensues, ultimate, unbelievable, iconic)\n"
        "- say the ACTUAL payoff of THIS clip (what changes / what's funny), grounded in the transcript\n"
        "- title <= 9 words; hook <= 8 words\n"
        "Good: \"he really said bring the chop after school\" | \"grab your balls, twist, pop\"\n"
        "Bad:  \"Streamer Threatens 'Bring the Chop'\" | \"The Ultimate Freestyle Challenge\"\n"
    )


def _load_hook_templates():
    """Load config/hook_templates.json with the standard env -> Linux -> repo
    fallback. Cached. Returns {} on any failure so the hook simply stays empty
    (the prior behavior) rather than crashing."""
    global _HOOK_TEMPLATES_CACHE
    if _HOOK_TEMPLATES_CACHE is not None:
        return _HOOK_TEMPLATES_CACHE
    candidates = [
        os.environ.get("CLIP_HOOK_TEMPLATES_CONFIG"),
        "/root/.openclaw/hook_templates.json",
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "config", "hook_templates.json"),
    ]
    cfg: dict = {}
    for c in candidates:
        if c and os.path.exists(c):
            try:
                cfg = json.loads(open(c, encoding="utf-8").read()) or {}
                break
            except (OSError, ValueError):
                cfg = {}
    _HOOK_TEMPLATES_CACHE = cfg
    return cfg


def _hook_from_template(category, title, T):
    """Pick a deterministic fallback hook for a category. {title} slots are
    filled with a short cleaned title; slotless templates are used verbatim.
    Returns "" when templates are disabled/missing (-> no hook card)."""
    cfg = _load_hook_templates()
    if not cfg or not cfg.get("enabled", True):
        return ""
    templates = cfg.get("templates", {}) or {}
    cat = str(category or "").strip().lower()
    pool = templates.get(cat) or templates.get("default") or []
    if not pool:
        return ""
    try:
        idx = int(T) % len(pool)
    except Exception:
        idx = 0
    tmpl = str(pool[idx])
    if "{title}" in tmpl:
        short = re.sub(r"^\s*Pattern\s+[A-Za-z0-9_]+\s*:\s*", "", str(title or ""), flags=re.IGNORECASE)
        short = short.strip().rstrip(".")
        words = short.split()
        if len(words) > 6:
            short = " ".join(words[:6])
        if not short:
            return ""
        tmpl = tmpl.replace("{title}", short)
    return tmpl.strip()


# Fix 1A — grounding is field-aware. `title` and `hook` are intentionally
# CREATIVE (the Stage 6 prompt asks for a "viral title" and a hook "in the
# voice of a content creator"), so they have low literal overlap with the
# transcript and were spuriously nulled by the Tier-2 weighted judge (which
# weights `grounding` at 0.55, threshold 5.0). That nulling forced the entry
# back to the Pass-B `why` baseline → the "Pattern <id>:" garbage-title leak.
# Fix: ground creative fields with the Tier-1 DENYLIST + Phase-2.4d hard-event
# check ONLY (overlap gate disabled via min_overlap=0.0, no LLM judge). This
# still blocks fabricated events ("gifted subs" with sub_count==0) while
# letting punchy phrasing through. `description` is meant to be literal
# ("grounded in what was literally said/shown") so it keeps the full two-tier
# cascade. See concepts/clip-quality-remediation-2026-06.md Fix 1.
_CREATIVE_FIELDS = ("title", "hook")


def _caption_gate(field, claim, refs, description, chk):
    """Part 1 (P1.4 then P1.2) — the quality gate the creative fields lacked.
    Mutates ``chk`` to failed when the caption reads AI-generated (deterministic
    linter) or doesn't describe the clip (LLM fidelity judge). Only ever called
    on an already-PASSING Tier-1 result, so a denylist/hard-event fail still
    wins. Both checks are individually toggleable + failure-soft — an exception
    or a down LM Studio leaves ``chk`` passing (prior behavior)."""
    # Deterministic AI-tell linter first (free): Title Case, scare-quotes,
    # "The 'X' Y", clickbait words, em-dash, hashtag.
    if _CAPTION_LINT and _caption_lint is not None:
        try:
            if _caption_lint.is_ai_voice(claim, kind=field):
                chk["passed"] = False
                chk["reason"] = "caption_ai_voice"
                chk["caption_detail"] = _caption_lint.summarize(claim, kind=field)
                return
        except Exception:
            pass
    # LLM fidelity judge (one short call): does the caption describe THIS clip?
    if _CAPTION_JUDGE and _grounding is not None:
        cj_cfg = GROUNDING_CONFIG.get("caption_judge", {}) or {}
        if cj_cfg.get("enabled", True):
            try:
                window = "\n".join(r for r in refs if r)
                cj = _grounding.caption_judge(
                    field, claim, window, description or "",
                    # Pin to the model already resident for Stage 6 (VISION_MODEL);
                    # the default resolve chain would pick CLIP_TEXT_MODEL and force
                    # a model swap mid-stage on split-model configs. Same unified
                    # model here, so this is a no-op on this rig but swap-safe.
                    lm_studio_model=VISION_MODEL,
                    url=os.environ.get("CLIP_LLM_URL", LLM_URL),
                    timeout=float(cj_cfg.get("timeout_s", 20)),
                )
                if cj is not None:
                    chk["caption_fidelity"] = cj["fidelity"]
                    chk["caption_human_voice"] = cj["human_voice"]
                    if cj["fidelity"] < float(cj_cfg.get("fidelity_threshold", 6)):
                        chk["passed"] = False
                        chk["reason"] = "caption_low_fidelity"
                        chk["caption_detail"] = f"fidelity {cj['fidelity']:.0f}/10: {cj['rationale']}"
            except Exception:
                pass


def _ground_field(field, claim, refs, hard_events, description=""):
    """Field-aware grounding check. Returns the cascade/check_claim result dict
    (always carries `passed`, `reason`, `tier`)."""
    if _grounding is None:
        return {"passed": True, "reason": "no_grounding", "tier": 0}
    if field in _CREATIVE_FIELDS:
        chk = _grounding.check_claim(
            claim, refs, GROUNDING_DENYLIST,
            min_overlap=0.0,                 # disable the literal-overlap gate
            hard_events=hard_events,         # keep the anti-fabrication guard
            event_map=CHAT_EVENT_MAP,
        )
        chk.setdefault("tier", 1)
        # P1.2/P1.4: escalate a PASSING Tier-1 creative field through the
        # caption linter + fidelity judge (the semantic check these fields
        # never had). A Tier-1 FAIL is left as-is (denylist/hard-event wins).
        if chk.get("passed"):
            _caption_gate(field, claim, refs, description, chk)
        return chk
    return _grounding.cascade_check(
        claim, refs, GROUNDING_DENYLIST, GROUNDING_CONFIG,
        min_overlap=0.15,
        hard_events=hard_events,
        event_map=CHAT_EVENT_MAP,
    )


# ---------------------------------------------------------------------------
# Part 2 (P2.1) — classic A/B caption variant. A = the entry's primary
# (already-grounded) caption; B = ONE alternate-angle challenger, validated
# through the SAME gate as A (denylist + linter + fidelity judge) so a variant
# can never be lower-quality than the primary. Gated by CLIP_AB_VARIANTS>=2
# (default off) — none of this runs on a normal run. See
# concepts/plan-captions-and-ab-variants-2026-07 §P2.1.
# ---------------------------------------------------------------------------
_VARIANT_PROMPT = """/no_think
You are writing Version B of a short-form clip caption for A/B testing. Version A already exists. Write ONE Version B that describes the SAME clip from a DIFFERENT angle, so the two feel distinct scrolling a feed.

Version A title: {title_a}
Version A hook: {hook_a}
Transcript (what is actually said in the clip): {transcript}

Choose the angle for B that contrasts MOST with A:
- reaction-POV: the viewer's reaction ("the way he...", "bro really...")
- context-tease: withhold the payoff so they watch ("watch what he does when...")
- quote: the funniest verbatim line from the transcript

VOICE (same rules as A): sentence case or lowercase, NO Title Case Every Word, NO scare-quotes around names, NO em-dash, NO hashtags, NO clickbait words (epic/insane/ensues/ultimate). Title <= 9 words, hook <= 8 words, grounded in the transcript.

Respond with ONLY JSON: {{"title": "...", "hook": "...", "angle": "reaction-POV|context-tease|quote"}}"""


def _variant_llm(prompt):
    """One text call on the ALREADY-LOADED Stage-6 model (VISION_MODEL — no swap).
    Returns the parsed JSON dict or None on any failure."""
    try:
        import lmstudio
    except Exception:
        return None
    txt = lmstudio.chat(prompt, model=VISION_MODEL, url=LLM_URL,
                        timeout=60, response_json=True, max_tokens=300)
    if not txt:
        return None
    s, e = txt.find("{"), txt.rfind("}")
    if s < 0 or e <= s:
        return None
    try:
        obj = json.loads(txt[s:e + 1])
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _generate_variant_b(entry, local_transcript, hard_events):
    """Return {label:'B', title, hook, angle} or None. B must pass the same
    creative-field gate as A and differ from A (no near-duplicate). Up to one
    regen on failure; give up (return None → only A ships) after that.
    DEFAULT ON since 2026-07-10 (owner promotion: 9/9-GOOD spot-check on run
    20260710_202308). Kill switch: CLIP_AB_VARIANTS=0."""
    try:
        n = int(os.environ.get("CLIP_AB_VARIANTS", "2") or "2")
    except ValueError:
        n = 2
    if n < 2:
        return None
    title_a = (entry.get("title") or "").strip()
    hook_a = (entry.get("hook") or "").strip()
    if not (title_a or hook_a) or not (local_transcript or "").strip():
        return None
    refs = [local_transcript, entry.get("description", "")]
    prompt = _VARIANT_PROMPT.format(
        title_a=title_a or "(none)", hook_a=hook_a or "(none)",
        transcript=local_transcript[:3000])
    for _attempt in range(2):
        obj = _variant_llm(prompt)
        if not obj:
            return None
        b_title = str(obj.get("title") or "").strip()
        b_hook = str(obj.get("hook") or "").strip()
        angle = str(obj.get("angle") or "").strip()[:20]
        if not (b_title or b_hook):
            return None
        # Must be a genuine alternate — a near-duplicate hook is no A/B test.
        if b_hook and hook_a and b_hook.lower() == hook_a.lower():
            prompt += "\n\n[Version B was identical to A. Use a genuinely different angle.]"
            continue
        ok, detail = True, ""
        for _f, _c in (("hook", b_hook), ("title", b_title)):
            if not _c:
                continue
            chk = _ground_field(_f, _c, refs, hard_events,
                                description=entry.get("description", ""))
            if not chk.get("passed"):
                ok = False
                detail = chk.get("caption_detail") or chk.get("reason", "")
                break
        if ok:
            return {"label": "B", "title": b_title or title_a,
                    "hook": b_hook or hook_a, "angle": angle or "alt"}
        prompt += f"\n\n[Version B failed: {detail}. Fix it and keep the voice rules.]"
    return None


# ---------------------------------------------------------------------------
# Part 2 (P2.3) — per-platform post kit. The 9:16 file already fits
# TikTok/Reels/Shorts; only the POST TEXT differs per platform. One text call
# on the already-loaded Stage-6 model (Stage 7 runs with the model UNLOADED, so
# generating here avoids a reload). NO hashtags anywhere (owner decision).
# Gated by CLIP_POST_KIT (default off). Stored on the entry; Stage 7 just writes
# the "<title>.post.json" sidecar. See plan §P2.3.
# ---------------------------------------------------------------------------
_POST_KIT_PROMPT = """/no_think
Write ready-to-post captions for ONE short-form clip, for three platforms. Do NOT use hashtags anywhere — the creator posts with zero tags. Sound like a real person, sentence case, no clickbait words.

Clip title: {title}
On-screen hook: {hook}
What happens in the clip (transcript): {transcript}

Respond with ONLY JSON:
{{"tiktok": "one short caption, no hashtags",
  "instagram": "a hook line then one context sentence, no hashtags",
  "youtube_title": "<= 90 chars, no hashtags",
  "youtube_description": "1-2 sentences, no hashtags"}}"""


def _strip_hashtags(s: str) -> str:
    """Defensive: remove any #tag the model slipped in despite the instruction
    (owner runs zero tags)."""
    return re.sub(r"\s*#\w+", "", str(s or "")).strip()


def _generate_post_kit(entry, local_transcript):
    """Return the per-platform post-copy dict or None. Failure-soft. DEFAULT ON
    since 2026-07-10 (owner promotion after the 9/9-GOOD run 20260710_202308).
    Kill switch: CLIP_POST_KIT=0. Includes both A/B hooks so the owner posting
    variant B has its line."""
    if os.environ.get("CLIP_POST_KIT", "1").strip().lower() in ("0", "false", "no", "off"):
        return None
    title = (entry.get("title") or "").strip()
    hook = (entry.get("hook") or "").strip()
    if not (title or hook) or not (local_transcript or "").strip():
        return None
    prompt = _POST_KIT_PROMPT.format(
        title=title or "(none)", hook=hook or "(none)",
        transcript=local_transcript[:3000])
    obj = _variant_llm(prompt)
    if not obj:
        return None
    kit = {
        "tiktok": _strip_hashtags(obj.get("tiktok")),
        "instagram": _strip_hashtags(obj.get("instagram")),
        "youtube_shorts": {
            "title": _strip_hashtags(obj.get("youtube_title"))[:100],
            "description": _strip_hashtags(obj.get("youtube_description")),
        },
    }
    # Variant hooks for the owner (trial-reel marker set when a B exists).
    b = next((v for v in (entry.get("hook_variants") or [])
              if str(v.get("label", "")).upper() == "B"), None)
    kit["variant_hooks"] = {"A": hook, "B": (b or {}).get("hook", "")}
    kit["trial_reel"] = bool(b)
    if not any((kit["tiktok"], kit["instagram"], kit["youtube_shorts"]["title"])):
        return None
    return kit


def _process_moment(moment):
    """Per-moment vision enrichment (2026-06-04 refactor for parallel dispatch).

    Body is the original moment loop's iteration body verbatim — same
    prompt construction, same VLM call (via the nested ``_vision_call``
    closure), same grounding cascade, same score-blending. The only
    behavioural change is the global ``_VISION_NET_FAIL_STREAK`` counter
    now updates under ``_VISION_NET_FAIL_LOCK`` so concurrent workers
    can't race on the read-modify-write.

    Returns the per-moment ``entry`` dict that the caller appends to the
    module-level ``enriched`` list. Never returns ``None`` — even when
    vision skips/fails, a baseline transcript-derived entry is produced.
    """
    T = moment["timestamp"]
    # BUG 37 ripple: Stage 4 writes BOTH `score` (clamped to [0,1] for the
    # UI) and `raw_score` (uncapped, for internal ranking). Stage 6 used to
    # read only `score`, so when Pass C produced multiple raw>=1.0 winners
    # the vision boost (×1.15) hit the cap and degenerated to "1.000 ->
    # 1.000" — every clip displayed identical scores and the boost never
    # actually moved anything. Read raw_score when available so the boost
    # has headroom; the post-boost value propagates to downstream sort and
    # Pass D via entry["raw_score"] further down.
    transcript_score = moment.get("score", 5)
    transcript_raw = moment.get("raw_score", transcript_score)
    transcript_category = moment.get("category", "unknown")
    transcript_why = moment.get("why", "")
    segment_type = moment.get("segment_type", "unknown")

    # BUG 31: when LM Studio is unreachable, fall through immediately. Every
    # moment still gets a baseline entry (transcript title/score), Stage 7
    # renders all of them — we just skip the AI title/description boost.
    if _VISION_NET_FAIL_STREAK >= _VISION_NET_FAIL_LIMIT:
        skip_vision = True
        print(
            f"  T={T} SKIPPING vision — LM Studio outage (streak={_VISION_NET_FAIL_STREAK})",
            file=sys.stderr,
        )
    else:
        skip_vision = False

    # Carry forward clip boundaries from detection
    clip_start = moment.get("clip_start", max(0, T - 15))
    clip_end = moment.get("clip_end", T + 15)
    clip_duration = moment.get("clip_duration", 30)

    # Start with transcript data as the baseline. Title derived from Pass B's
    # `why` (or category + timestamp) so vision-failed moments still ship a
    # readable title in the filename + a sensible hook fallback. See
    # _derive_baseline_title docstring above.
    entry = {
        "timestamp": T,
        "score": transcript_score,
        # Carry the uncapped Pass C value forward by default. Vision/A2 paths
        # below overwrite raw_score with their own post-boost values; when
        # vision has nothing to boost we still want the ranking signal to
        # survive into Pass D and the final sort.
        "raw_score": transcript_raw,
        "category": transcript_category,
        "title": _derive_baseline_title(transcript_why, transcript_category, T),
        "description": transcript_why[:100] if transcript_why else "",
        # Category hook-text fallback (concepts/hook-engineering-2026-06). A
        # vision-generated hook overrides this below; when vision is skipped or
        # grounding nulls the hook, this curiosity hook still ships a hook card.
        "hook": _hook_from_template(
            transcript_category,
            _derive_baseline_title(transcript_why, transcript_category, T),
            T,
        ),
        "hype_score": transcript_score,
        "transcript_category": transcript_category,
        "segment_type": segment_type,
        "vision_score": 0,
        "vision_ok": False,
        "clip_start": clip_start,
        "clip_end": clip_end,
        "clip_duration": clip_duration,
        # Originality fields consumed by Stage 7. All default to safe
        # "no-op" values so a failed vision call still renders a valid clip.
        "mirror_safe": False,          # True = flipping horizontally won't reveal reversed text
        "grounded_in_transcript": False,  # True = vision claims its fields match the transcript
        "voiceover": None,              # {text, placement, tone, duration_estimate_s}
        "group_id": moment.get("group_id", ""),
        "group_kind": moment.get("group_kind", "solo"),
        # Anomaly-lane provenance (owner req 2026-07-08): _process_moment rebuilds the
        # entry from scratch, so `src` would be dropped here — carry it forward so Stage 7
        # can prefix ANOMALY_ onto the clip filename. None for normal moments.
        "src": moment.get("src"),
        # P-TIGHT exemption input (owner review 2026-07-08): same rebuild-drop bug hit
        # `primary_pattern` — without it Stage 7's tighten() saw "" and the designed
        # rap/freestyle/storytell exemption NEVER fired (T=9567 rap_battle_freestyle got
        # trimmed). Carry it forward.
        "primary_pattern": moment.get("primary_pattern"),
    }

    # Check stage timeout before attempting vision (OR with the outage flag
    # already evaluated above so a stage timeout AFTER an outage still skips).
    elapsed_vision = time.time() - VISION_STAGE_START
    if elapsed_vision > VISION_STAGE_TIMEOUT:
        skip_vision = True
        print(f"  T={T} SKIPPING vision — stage timeout ({int(elapsed_vision)}s > {VISION_STAGE_TIMEOUT}s)", file=sys.stderr)

    # Try to get vision enrichment (title, description, visual score).
    # NOTE (2026-04-23, Phase 0.1): previously this loop ran the VLM twice
    # per moment on frames 03/04 (≈ T-5 / T+0 under the old uniform-fps
    # sampler) and kept the best. The research doc (ClippingResearch.md
    # "Additional topic 2") identifies this as the single highest-impact
    # bug: the payoff lives at T+0..T+5, so the model was describing the
    # setup, not the punchline. Stage 5 now extracts 6 targeted frames
    # (T-2, T+0, T+1, T+2, T+3, T+5) and we send ALL of them to the VLM in
    # ONE call, labeled in time order so the model reasons about the arc.
    best_vision_score = 0
    best_vision_result = None

    if not skip_vision:
        # Context and ±8 s transcript are per-moment, not per-frame — compute
        # once before we build the payload.
        stream_type = stream_profile.get("dominant_type", "unknown")
        context_parts = [f"This is a {stream_type} stream"]
        if segment_type != stream_type:
            context_parts.append(f"currently in a {segment_type} segment")
        if transcript_why:
            context_parts.append(f"flagged as '{transcript_category}' because: {transcript_why}")
        context_hint = ". ".join(context_parts)

        # Pull verbatim transcript that overlaps with the rendered clip
        # window so the vision model is grounded in the words actually
        # inside the clip — not the upstream Pass-B "why" summary (LLM
        # output, can propagate hallucinations downstream) and not a
        # T±8 window that may sit outside the rendered range when peak T
        # has been re-centered by Pass C merge or boundary snap.
        # BUG 56 (2026-05-02): the prom/bus mismatch hit precisely because
        # the keyword merged-in moment kept its T=1179 while the rendered
        # clip ran [1187, 1212] — vision saw "prom coming up" from T±8 but
        # the actual content was a bus mishap 8 seconds later. Falling
        # back to T±8 is preserved for the (rare) case where clip_start /
        # clip_end aren't on the moment, e.g. legacy callers.
        local_transcript = ""
        local_transcript_ts = ""
        try:
            with open(f"{TEMP_DIR}/transcript.json") as _tf:
                _tr = json.load(_tf)
            _start = float(moment.get("clip_start", T - 8))
            _end   = float(moment.get("clip_end",   T + 8))
            # Defend against pathological windows: if start/end collapse
            # or the moment carries a 1-second sliver, fall back to T±8.
            if _end - _start < 4.0:
                _start, _end = T - 8, T + 8
            window = [seg for seg in _tr
                      if seg.get("end", 0) >= _start and seg.get("start", 0) <= _end]
            # P1.1: full clip-window transcript (was [:500] ≈ first ~25 s — long
            # clips were titled from a fraction of what they say, the structural
            # cause of "the caption doesn't encapsulate the clip"). 4000 chars
            # ≈ 1k tokens, comfortable in the 32k ctx. The SAME string feeds the
            # grounding refs below, so generation and validation see identical
            # evidence.
            local_transcript = " ".join(s.get("text", "").strip() for s in window)[:4000]
            if _EDIT_INFER:
                local_transcript_ts = " ".join(
                    f"[{float(s.get('start', 0)):.1f}-{float(s.get('end', 0)):.1f}] "
                    f"{s.get('text', '').strip()}" for s in window)[:4000]
        except Exception:
            pass

        # Load the 6 payoff-window frames produced by Stage 5. Each entry
        # is (file_label, human_caption) — captions go into the prompt so
        # the VLM knows which frame is which point in time.
        # Tier-3 A2: when this moment is a callback (M3) or arc (A1), prepend
        # 2 setup frames extracted by Stage 5 from setup_time-1/+1 so the VLM
        # can visually verify the same person/scene drives both halves of
        # the arc. Setup frames first so the prompt's "frames 1-2 are
        # earlier" labelling is positionally correct.
        FRAME_LABELS = []
        a2_setup_time = moment.get("setup_time")
        a2_arc_kind = moment.get("arc_kind")
        a2_active = (
            a2_setup_time is not None
            and os.path.exists(f"{TEMP_DIR}/frames_{T}_setupminus1.jpg")
        )
        if a2_active:
            setup_mm = int(a2_setup_time) // 60
            setup_ss = int(a2_setup_time) % 60
            FRAME_LABELS.extend([
                ("setupminus1", f"SETUP @ {setup_mm:02d}:{setup_ss:02d} (-1s)"),
                ("setupplus1",  f"SETUP @ {setup_mm:02d}:{setup_ss:02d} (+1s)"),
            ])
        FRAME_LABELS.extend([
            ("tminus2", "T-2s (pre-peak setup)"),
            ("t0",      "T+0s (peak)"),
            ("tplus1",  "T+1s"),
            ("tplus2",  "T+2s"),
            ("tplus3",  "T+3s (typical payoff)"),
            ("tplus5",  "T+5s (aftermath)"),
        ])
        image_parts = []
        loaded_captions = []
        for label, caption in FRAME_LABELS:
            frame_path = f"{TEMP_DIR}/frames_{T}_{label}.jpg"
            if not os.path.exists(frame_path):
                continue
            with open(frame_path, "rb") as f:
                img_b64 = base64.b64encode(f.read()).decode()
            image_parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"},
            })
            loaded_captions.append(caption)

        if not image_parts:
            print(f"  T={T} no frames on disk — skipping vision", file=sys.stderr)
        else:
            frame_guide = "\n".join(f"- Frame {i+1}: {cap}" for i, cap in enumerate(loaded_captions))

            # Phase 2.4d: hard-event ground truth for the grounding cascade.
            # The HARD RULE block fires only when the ±8 s chat window has
            # zero events of every type — at that point the cascade will
            # reject any title/hook/description that claims a sub/bit/raid/
            # donation. Burst-factor + emote-density chat scoring was
            # removed 2026-05-01 (chat is latent, biases timing).
            chat_context_block = ""
            hard_events = None
            if CHAT_FEATURES is not None:
                _cw = CHAT_FEATURES.window(T - 8, T + 8)
                hard_events = {
                    "sub_count": _cw.get("sub_count", 0),
                    "bit_count": _cw.get("bit_count", 0),
                    "raid_count": _cw.get("raid_count", 0),
                    "donation_count": _cw.get("donation_count", 0),
                }
                if not any(hard_events.values()):
                    chat_context_block = (
                        "\nHARD GROUND-TRUTH RULE: chat shows zero sub / bit / raid / donation "
                        "events in this ±8 s window. You may NOT mention gifted subs, sub trains, "
                        "hype trains, bit rain, raids, or donations in title/hook/description — "
                        "no matter what the frame overlay shows.\n"
                    )

            # Tier-3 A2: setup-aware prompt addendum. Only emitted for
            # callback / arc moments where Stage 5 produced setup frames.
            # When present, the model sees frames 1-2 as "earlier setup" and
            # is asked to verify visual continuity (same person/scene drove
            # both halves of the arc).
            a2_prompt_block = ""
            if a2_active:
                _setup_text = (moment.get("setup_text") or "").strip()[:240]
                a2_prompt_block = (
                    "\nTier-3 A2 — CALLBACK / ARC verification:\n"
                    f"This moment is tagged a {a2_arc_kind or 'callback'} — frames 1-2 are from the SETUP "
                    f"earlier in the stream (around {int(a2_setup_time)//60:02d}:{int(a2_setup_time)%60:02d}); "
                    "frames 3+ are from NOW (the payoff).\n"
                )
                if _setup_text:
                    a2_prompt_block += (
                        f'Earlier the streamer said: "{_setup_text}"\n'
                    )
                a2_prompt_block += (
                    "Verify visual continuity: is it the same person / same scene driving both "
                    "halves of the arc? If yes, the title/description should NAME the callback "
                    "explicitly (e.g. \"earlier they bragged X, now Y\"). If frames 1-2 show a "
                    "different person/scene than 3+, the callback is weaker — say so in description.\n"
                )
            edit_directive = ""
            edit_json = ""
            if _EDIT_INFER and local_transcript_ts:
                _ed = ["\nEDIT DECISIONS — use the ABSOLUTE-timestamped transcript below:\n",
                       f'"""{local_transcript_ts}"""\n']
                _jf = []
                if _EDIT_LLM_CUTS:
                    _ed.append("- cuts: spans of DEAD AIR / rambling / false-starts to DROP so the clip "
                               "reaches the payoff faster. KEEP the setup and the punchline; never drop the "
                               "last few seconds; total dropped < 40%. Empty list if it's already tight.\n")
                    _jf.append('"cuts": [{"drop_start": <abs s>, "drop_end": <abs s>}]')
                if _EDIT_LLM_FLASH:
                    _ed.append("- flashes: 0-2 absolute timestamps for a quick white-flash beat right before "
                               "a punchline or a hard topic shift (engagement). Empty list if none fit.\n")
                    _jf.append('"flashes": [{"t": <abs s>, "style": "soft"}]')
                edit_directive = "".join(_ed)
                edit_json = (",\n  " + ",\n  ".join(_jf)) if _jf else ""
            base_prompt = f"""Analyze this sequence of time-ordered frames from a livestream around a detected highlight moment, together with what the streamer is ACTUALLY saying.

Context: {context_hint}

Frame order (time moves forward across the sequence):
{frame_guide}
{a2_prompt_block}
Transcript at this exact moment (verbatim, ±8 s around the peak):
\"\"\"{local_transcript or '(transcript unavailable — use the frames alone)'}\"\"\"
{chat_context_block}

Grounding rules — you MUST follow these:
1. The "title", "description", and "hook" MUST describe the PAYOFF visible in the later frames (T+1..T+5) AND/OR what the transcript literally says. Do NOT describe the setup frame alone. Do NOT invent context that isn't in the frames or transcript.
2. If any frame shows a sub-celebration / follower alert / donation overlay but the transcript is about something else entirely (e.g. a game rank, a story), describe the TRANSCRIPT topic — the overlay is ambient, not the subject.
3. If you are not confident what the streamer is reacting to, say so in "description" rather than guessing.
4. Reason about CHANGE across the sequence — what's different between T-2 and T+5? That delta is the clip.

Also flag render hints:
- mirror_safe: true if horizontally flipping would NOT reveal reversed on-screen text, HUDs, scoreboards, or legible name tags in ANY frame (be strict — one bad frame disqualifies the clip).
- voiceover: a short creator-POV narration line (8-14 words, spoken English, no hashtags). Do NOT restate the streamer's words — instead point at the moment or add context. Specify placement = "intro" | "peak" | "outro" and tone = "hype" | "deadpan" | "earnest" | "snarky".

Tier-4 Phase 4.5 — also identify the INTERACTION SHAPE the frames depict:
- interaction_shape: one of "monologue" (one person, talking to camera), "reading-chat" (gaze toward chat panel; reading or paraphrasing), "dialog-with-on-screen-guest" (two visible people in conversation), "dialog-with-off-screen-voice" (one visible person reacting to an off-screen voice), "gameplay-with-commentary" (game footage primary, person inset), "silent-gameplay" (game footage, no commentary visible), "multi-speaker-stage" (3+ visible speakers), "media-pause-commentary" (external media/a video is paused or frozen on screen and the streamer turns to the camera to give their own take on it — the pause-and-opine archetype).
- pattern_match: which catalog pattern the frames best support — "setup_external_contradiction", "challenge_and_fold", "reading_chat_reaction", "storytelling_arc", "hot_take_pushback", "informational_ramble", "interview_revelation", "rap_battle_freestyle", "social_callout", "unexpected_topic_shift", or null.
- pattern_match_strength: 0.0-1.0 — confidence the frames support pattern_match.
- gaze_direction: "at-camera" / "at-chat" / "at-screen" / "at-guest" / "off-screen" / "down".
{edit_directive}{_caption_style_fewshot()}{_caption_voice_contract()}
Respond ONLY with JSON: {{
  "score": 1-10,
  "category": "comedy/skill/reaction/controversy/emotional/irl",
  "title": "<= 9 words, sentence case, names THIS clip's actual payoff — NOT a Title Case headline, NO scare-quotes around names",
  "description": "one sentence grounded in what was literally said/shown",
  "hook": "<= 8 words, spoken like a viewer texting a friend about the clip, no hashtags, no Title Case, no trailing period",
  "grounded_in_transcript": true|false,
  "mirror_safe": true|false,
  "voiceover": {{"text": "...", "placement": "intro|peak|outro", "tone": "hype|deadpan|earnest|snarky", "duration_estimate_s": 3.0}},
  "interaction_shape": "monologue|reading-chat|dialog-with-on-screen-guest|dialog-with-off-screen-voice|gameplay-with-commentary|silent-gameplay|multi-speaker-stage|media-pause-commentary",
  "pattern_match": "<pattern_id or null>",
  "pattern_match_strength": 0.0-1.0,
  "gaze_direction": "at-camera|at-chat|at-screen|at-guest|off-screen|down"{("," + chr(10) + "  " + chr(34) + "callback_confirmed" + chr(34) + ": 0-10 (Tier-3 A2 — does the visual continuity between setup frames 1-2 and payoff frames 3+ support the claimed callback? 0=different scene/person, 5=ambiguous, 10=same person/scene clearly drives both halves)") if a2_active else ""}{edit_json}
}}"""

            def _vision_call(_prompt_text):
                """Make ONE vision call. Returns (parsed_dict, reasoning_tokens) or (None, 0).

                Local closure over image_parts, VISION_MODEL, LLM_URL, T,
                VISION_PER_MOMENT_TIMEOUT. The token/parse fallback logic is
                exactly what the pre-Phase-1 inline block did — just factored
                out so we can call it twice (first attempt + regenerate-once)."""
                _payload = json.dumps({
                    "model": VISION_MODEL,
                    "messages": [{"role": "user", "content": [
                        {"type": "text", "text": _prompt_text},
                        *image_parts,
                    ]}],
                    "stream": False,
                    "temperature": 0.3,
                    # 8000 tokens: bumped from 6000 on 2026-04-30. Qwen3.5-35B-A3B
                    # uses 2000-4000 reasoning tokens on vision prompts. Gemma 4-26B
                    # ignores chat_template_kwargs (BUG 38) and routinely burns
                    # 4000-6000 reasoning tokens on a multi-frame vision prompt with
                    # the A2 callback verification block — at 6000 we observed empty
                    # content (with finish=length), which silently downgraded clips
                    # to transcript-only enrichment. 8000 covers the Gemma worst
                    # case plus the ~500-token JSON answer.
                    # NOTE: no markdown backticks in this comment — this body
                    # lives inside the Stage 6 unquoted PYEOF heredoc, which
                    # would interpret backticks as command substitution
                    # (BUG 39 / BUG 46 redux).
                    "max_tokens": 8000,
                    "chat_template_kwargs": thinking.template_kwargs(),
                }).encode()

                global _VISION_NET_FAIL_STREAK
                try:
                    _req = urllib.request.Request(
                        f"{LLM_URL}/v1/chat/completions",
                        data=_payload,
                        headers={"Content-Type": "application/json"},
                    )
                    with urllib.request.urlopen(_req, timeout=VISION_PER_MOMENT_TIMEOUT) as _resp:
                        _result = json.loads(_resp.read().decode())
                except Exception as _ve:
                    print(f"  T={T} vision call failed: {_ve}", file=sys.stderr)
                    # Thread-safe streak update for the parallel path. Reads
                    # of the counter elsewhere (skip-vision check at the top
                    # of _process_moment) tolerate stale-by-one values — the
                    # circuit-breaker just trips slightly later under parallel
                    # dispatch, which is the right behaviour when LM Studio
                    # is genuinely down (we'd rather skip a few moments than
                    # block on retries).
                    with _VISION_NET_FAIL_LOCK:
                        if _vision_looks_like_outage(_ve):
                            _VISION_NET_FAIL_STREAK += 1
                        else:
                            _VISION_NET_FAIL_STREAK = 0
                    return None, 0
                # A successful response — even if we end up failing to parse
                # the JSON below — proves the network is up and resets streak.
                with _VISION_NET_FAIL_LOCK:
                    _VISION_NET_FAIL_STREAK = 0

                _msg = _result["choices"][0]["message"]
                _raw = _msg.get("content") or ""
                _finish = _result["choices"][0].get("finish_reason", "?")
                _rt = _result.get("usage", {}).get(
                    "completion_tokens_details", {}).get("reasoning_tokens", 0)

                if not _raw:
                    _rc = str(_msg.get("reasoning_content", ""))
                    if _finish == "stop" and _rc:
                        print(f"  T={T} reasoning_content fallback "
                              f"(finish={_finish}, reasoning_tokens={_rt})",
                              file=sys.stderr)
                        _raw = _rc
                    else:
                        _rpv = _rc[:80]
                        print(f"  T={T} empty content "
                              f"(finish={_finish}, reasoning_tokens={_rt}, preview={_rpv!r})",
                              file=sys.stderr)
                        return None, _rt

                _response = re.sub(r"<think>.*?</think>", "", _raw, flags=re.DOTALL).strip()
                _clean = _response.strip()
                # Strip a ```json code fence if present. This is a real .py module
                # — the old \-escaped backticks were a vestigial heredoc artifact
                # that never matched a real fence and emitted a SyntaxWarning
                # (so vision JSON wrapped in a fence silently failed to parse,
                # forcing avoidable grounding REGEN cycles). Same fix as stage4.
                if "```" in _clean:
                    _parts = _clean.split("```")
                    if len(_parts) >= 2:
                        _clean = _parts[1]
                        if _clean.startswith("json"):
                            _clean = _clean[4:]
                        _clean = _clean.strip()

                _js = _clean.find("{")
                _je = _clean.rfind("}") + 1
                if _js < 0 or _je <= _js:
                    print(f"  T={T} no JSON in response: {_response[:80]}", file=sys.stderr)
                    return None, _rt
                try:
                    return json.loads(_clean[_js:_je]), _rt
                except (json.JSONDecodeError, ValueError):
                    print(f"  T={T} JSON parse error: {_clean[:80]}", file=sys.stderr)
                    return None, _rt

            # --- First attempt ---
            parsed, reasoning_tokens = _vision_call(base_prompt)

            if parsed is None:
                # Nothing parseable — leave best_vision_result as None, entry keeps transcript defaults.
                pass
            else:
                v_score = int(parsed.get("score", 0) or 0)
                n_frames = len(loaded_captions)

                # --- Two-tier grounding cascade on every generated field. ---
                # refs = (±8 s transcript window, upstream Pass-B why). The cascade
                # runs Tier 1 always (regex denylist + content overlap + Phase 2.4d
                # zero-count event check); the main-model LLM judge handles
                # borderline cases. Failures here are the signal for regenerate-once.
                field_results = {}
                if _grounding is not None and (local_transcript or transcript_why):
                    refs = [local_transcript, transcript_why]
                    for _field in ("title", "hook", "description"):
                        _claim = (parsed.get(_field) or "").strip()
                        if not _claim:
                            continue
                        _chk = _ground_field(_field, _claim, refs, hard_events,
                                             description=parsed.get("description", ""))
                        field_results[_field] = _chk

                # --- Phase 1.1: regenerate once when the first response failed. ---
                # We don't restart the pipeline or skip the clip — just try ONE
                # more VLM call with the violations named explicitly in the prompt.
                # If the retry passes, use the retried field; if not, fall through
                # to the null-and-default policy at the bottom of this block.
                failed_fields = {f: r for f, r in field_results.items() if not r["passed"]}
                regen_enabled = bool(
                    GROUNDING_CONFIG.get("regeneration", {}).get("enabled", True)
                )
                regen_max = int(
                    GROUNDING_CONFIG.get("regeneration", {}).get("stage_6_retry_count", 1)
                )
                if failed_fields and regen_enabled and regen_max > 0:
                    _violation_lines = []
                    for _f, _r in failed_fields.items():
                        _hits = ",".join(h["match"] for h in _r.get("denylist_hits", []))
                        if _r.get("caption_detail"):
                            _violation_lines.append(
                                f'- "{_f}" reads AI-written or off-topic: {_r["caption_detail"]}'
                            )
                        elif _hits:
                            _violation_lines.append(
                                f'- "{_f}" contained "{_hits}" but the transcript never mentions that'
                            )
                        elif _r.get("judge_weighted") is not None:
                            _violation_lines.append(
                                f'- "{_f}" did not match the transcript meaningfully '
                                f'(judge score {_r["judge_weighted"]:.1f}/10: {_r.get("judge_rationale", "")})'
                            )
                        else:
                            _violation_lines.append(
                                f'- "{_f}" had too little overlap with what was said '
                                f'(overlap {_r.get("overlap", 0):.2f})'
                            )
                    _violations = "\n".join(_violation_lines)

                    retry_prompt = base_prompt + f"""

[RETRY — your previous response failed the grounding cascade:
{_violations}

Rewrite your JSON so every claim in title, hook, and description is directly supported by the transcript above. If the transcript doesn't contain enough detail to justify a field, make that field a plain description of what is visibly happening in the frames — do NOT invent subscription events, raids, hype trains, kills, or any specifics not present in the transcript or frames. For any field flagged as reading AI-written, rewrite it in plain sentence-case creator voice: no Title Case Every Word, no scare-quotes around names, no clickbait adjectives.]"""

                    print(
                        f"  T={T} REGEN — {len(failed_fields)} field(s) failed grounding "
                        f"({','.join(failed_fields.keys())}); retrying once with stricter prompt",
                        file=sys.stderr,
                    )
                    retry_parsed, retry_rt = _vision_call(retry_prompt)
                    if retry_parsed is not None:
                        reasoning_tokens += retry_rt
                        # Re-check each previously-failing field against the retry output.
                        for _f in list(failed_fields.keys()):
                            _rclaim = (retry_parsed.get(_f) or "").strip()
                            if not _rclaim:
                                continue
                            _rchk = _ground_field(_f, _rclaim, refs, hard_events,
                                                  description=retry_parsed.get("description", ""))
                            if _rchk["passed"]:
                                parsed[_f] = _rclaim
                                field_results[_f] = _rchk
                                failed_fields.pop(_f, None)
                                print(
                                    f"    T={T} REGEN ok for {_f} (tier {_rchk['tier']})",
                                    file=sys.stderr,
                                )
                            else:
                                field_results[_f] = _rchk  # keep the latest reason
                                print(
                                    f"    T={T} REGEN still fails for {_f} "
                                    f"(tier {_rchk['tier']} reason {_rchk['reason']})",
                                    file=sys.stderr,
                                )
                        # If the retry proposed a higher score and its title passed,
                        # accept the bump (Stage 6's score is non-decreasing anyway).
                        _retry_score = int(retry_parsed.get("score", v_score) or v_score)
                        if _retry_score > v_score and field_results.get("title", {}).get("passed", False):
                            v_score = _retry_score

                # --- Final pass: null any field that is STILL failing. ---
                # A nulled field falls back to the baseline seeded in `entry`:
                # title <- _derive_baseline_title (Pass-B `why` with the
                # "Pattern <id>:" prefix stripped, Fix 1B), or — when the vision
                # description survived grounding — synthesized from that
                # description in the enrichment block below (Fix 1C).
                for _field, _chk in field_results.items():
                    if _chk["passed"]:
                        continue
                    _hit_summary = ",".join(
                        h["match"] for h in _chk.get("denylist_hits", [])
                    ) or _chk.get("caption_detail") or (
                        f"judge={_chk.get('judge_weighted')}"
                        if _chk.get("judge_weighted") is not None
                        else f"overlap={_chk.get('overlap')}"
                    )
                    print(
                        f"    [GROUND] Stage 6 null {_field} T={T} "
                        f"tier={_chk['tier']} reason={_chk['reason']} ({_hit_summary})",
                        file=sys.stderr,
                    )
                    parsed[_field] = ""
                    parsed.setdefault("grounding_fails", []).append(
                        f"{_field}:tier{_chk['tier']}:{_chk['reason']}"
                    )

                best_vision_score = v_score
                best_vision_result = parsed
                if reasoning_tokens > 0:
                    print(f"  T={T} vision_score={v_score} frames={n_frames} "
                          f"(thinking not disabled: {reasoning_tokens} reasoning tokens used)",
                          file=sys.stderr)
                else:
                    print(f"  T={T} vision_score={v_score} frames={n_frames}", file=sys.stderr)

    # Enrich the entry with vision data (if available)
    if best_vision_result:
        entry["vision_ok"] = True
        # Normalize vision score from 1-10 to 0-1
        vision_norm = max(0.0, min((best_vision_score - 1) / 9.0, 1.0))
        entry["vision_score"] = round(vision_norm, 3)
        # Use vision title/description (usually better than generic)
        v_desc = best_vision_result.get("description", "")
        v_title = best_vision_result.get("title", "")
        if v_title and v_title != "":
            entry["title"] = v_title
        elif v_desc.strip():
            # Fix 1C: the vision title was nulled by grounding but the
            # description passed — a grounded description is a far better title
            # seed than the Pass-B "Pattern <id>:" baseline. Use its first
            # clause (cap ~60 chars). See clip-quality-remediation-2026-06 Fix 1.
            _syn = v_desc.strip().split(". ")[0].strip().rstrip(".")
            if len(_syn) > 60:
                _syn = _syn[:57].rstrip() + "..."
            if _syn:
                entry["title"] = _syn
        if v_desc:
            entry["description"] = v_desc
        v_hook = best_vision_result.get("hook", "")
        if v_hook:
            entry["hook"] = v_hook
        # Originality render hints
        ms = best_vision_result.get("mirror_safe")
        if isinstance(ms, bool):
            entry["mirror_safe"] = ms
        grounded = best_vision_result.get("grounded_in_transcript")
        if isinstance(grounded, bool):
            entry["grounded_in_transcript"] = grounded
            if not grounded:
                # Model self-reports it couldn't ground its output in the
                # transcript. Leave the enrichment in place but flag it so
                # downstream review / dashboard can highlight questionable clips.
                print(f"  T={T} vision NOT grounded in transcript — "
                      f"title/hook may not match the streamer's actual words",
                      file=sys.stderr)
        vo = best_vision_result.get("voiceover")
        if isinstance(vo, dict):
            vtxt = str(vo.get("text", "") or "").strip()
            if vtxt:
                entry["voiceover"] = {
                    "text": vtxt[:160],
                    "placement": str(vo.get("placement", "intro") or "intro")[:12],
                    "tone": str(vo.get("tone", "deadpan") or "deadpan")[:12],
                    "duration_estimate_s": float(vo.get("duration_estimate_s", 3.0) or 3.0),
                }
        # Transition animations (jump-cut + flash). Store the model's raw picks
        # on edit_plan; Stage 7's transition pass normalizes + budget-caps them.
        _ep_cuts = best_vision_result.get("cuts")
        _ep_flashes = best_vision_result.get("flashes")
        if isinstance(_ep_cuts, list) or isinstance(_ep_flashes, list):
            _plan = dict(entry.get("edit_plan") or {})
            if isinstance(_ep_cuts, list):
                _plan["cuts"] = _ep_cuts
            if isinstance(_ep_flashes, list):
                _plan["flashes"] = _ep_flashes
            entry["edit_plan"] = _plan
        # Tier-4 Phase 4.5 — vision-as-shape-detector. Stamp the four new
        # fields onto the entry so Stage 7 (manifest sort) can compute
        # cross_validated_full when Pass B primary_pattern + Pass D
        # pattern_confirmed + this pattern_match all agree.
        VALID_SHAPES = (
            "monologue", "reading-chat", "dialog-with-on-screen-guest",
            "dialog-with-off-screen-voice", "gameplay-with-commentary",
            "silent-gameplay", "multi-speaker-stage",
        )
        VALID_GAZE = ("at-camera", "at-chat", "at-screen", "at-guest", "off-screen", "down")
        ish = best_vision_result.get("interaction_shape")
        if isinstance(ish, str) and ish in VALID_SHAPES:
            entry["interaction_shape"] = ish
        gd = best_vision_result.get("gaze_direction")
        if isinstance(gd, str) and gd in VALID_GAZE:
            entry["gaze_direction"] = gd
        pm = best_vision_result.get("pattern_match")
        if isinstance(pm, str) and pm:
            entry["vision_pattern_match"] = pm
        try:
            pms = float(best_vision_result.get("pattern_match_strength") or 0.0)
            if 0.0 <= pms <= 1.0:
                entry["vision_pattern_match_strength"] = round(pms, 3)
        except (ValueError, TypeError):
            pass

        # Cross-validation across three channels: Pass B primary_pattern,
        # Pass D pattern_confirmed, Stage 6 vision_pattern_match. When all
        # three agree AND each strength >= 0.6, bump score by +0.1 (capped).
        # Operate on the uncapped raw score so a +0.1 nudge actually moves
        # ranking even when the clamped score is already pinned at 1.000.
        b_pattern = entry.get("primary_pattern") or moment.get("primary_pattern", "")
        d_pattern = entry.get("pattern_confirmed") or moment.get("pattern_confirmed", "")
        v_pattern = entry.get("vision_pattern_match", "")
        d_strength = float(moment.get("pattern_match_strength", 0) or 0)
        v_strength = entry.get("vision_pattern_match_strength", 0)
        if b_pattern and b_pattern == d_pattern == v_pattern and d_strength >= 0.6 and v_strength >= 0.6:
            entry["cross_validated_full"] = True
            new_raw = float(entry.get("raw_score", entry.get("score", 0)) or 0) + 0.1
            entry["raw_score"] = round(new_raw, 4)
            entry["score"] = round(min(new_raw, 1.0), 3)
            print(
                f"  T={T} cross_validated_full pattern={b_pattern} "
                f"score+=0.1 -> {entry['score']:.3f} (raw {new_raw:.4f})",
                file=sys.stderr,
            )
        # Blend scores: transcript is primary, vision is a bonus (never penalizes)
        # Vision >= 0.67 (was 7/10): multiply by 1.15
        # Vision >= 0.44 (was 5/10): multiply by 1.08
        # Vision < 0.44: keep transcript score unchanged
        # BUG 53 (2026-05-02): boost was applied to the clamped transcript
        # score, so a 1.000 ceiling × 1.15 just re-clamped to 1.000 — the
        # log line read "vision BOOST: 1.000 -> 1.000" for every Pass C
        # winner. Drive the boost off the uncapped transcript_raw, write
        # the post-boost raw_score, and clamp only at the display field.
        if vision_norm >= 0.67:
            base_raw = float(entry.get("raw_score", transcript_raw) or 0)
            new_raw = base_raw * 1.15
            entry["raw_score"] = round(new_raw, 4)
            entry["score"] = round(min(new_raw, 1.0), 3)
            print(
                f"  T={T} vision BOOST: {transcript_score:.3f} -> {entry['score']:.3f} "
                f"(raw {transcript_raw:.4f} -> {new_raw:.4f})",
                file=sys.stderr,
            )
        elif vision_norm >= 0.44:
            base_raw = float(entry.get("raw_score", transcript_raw) or 0)
            new_raw = base_raw * 1.08
            entry["raw_score"] = round(new_raw, 4)
            entry["score"] = round(min(new_raw, 1.0), 3)
        # else: keep transcript_score / raw_score as-is

        # Tier-3 A2 — visual-callback-confirmed multiplier. Only applies to
        # moments where Stage 5 produced setup frames (callback / arc) AND
        # the VLM returned a callback_confirmed score. Maps 0-10 onto the
        # multiplicative window [0.85, 1.20] so a strong visual confirmation
        # nudges up and a contradictory one nudges down. Only A2 is allowed
        # to PENALIZE a moment (vs vision_score which is bonus-only) because
        # for callbacks the visual continuity IS the substantive evidence.
        if a2_active:
            cc_raw = best_vision_result.get("callback_confirmed")
            if isinstance(cc_raw, (int, float)):
                cc = max(0.0, min(10.0, float(cc_raw)))
                a2_mult = 0.85 + (cc / 10.0) * (1.20 - 0.85)
                pre = entry["score"]
                # BUG 37 pattern: track the uncapped raw score for any
                # downstream ranking that would otherwise see a sea of 1.000s
                # when A2 boosts moments that were already top-of-pile. The
                # serialized "score" remains clamped to [0, 1] for UI/logging
                # consumers; "raw_score" carries the true magnitude. Without
                # this, two strong A2-confirmed callbacks (pre=0.95 ×1.20 = 1.14)
                # both display 1.000 and Stage 7 sort order becomes arbitrary.
                raw_post = pre * a2_mult
                entry["score"] = round(min(raw_post, 1.0), 3)
                entry["raw_score"] = round(raw_post, 4)
                entry["callback_confirmed"] = round(cc, 1)
                entry["a2_multiplier"] = round(a2_mult, 3)
                print(
                    f"  T={T} A2 callback_confirmed={cc} mult={a2_mult:.2f} "
                    f"score: {pre:.3f} -> {entry['score']:.3f} (raw {raw_post:.4f})",
                    file=sys.stderr,
                )
    else:
        # Vision failed — that's OK, use transcript data as-is
        print(f"  T={T} vision failed/no-parse — using transcript score={transcript_score:.3f}", file=sys.stderr)

    # 2026-06-04: append moved to the caller (parallel dispatch). Each
    # worker returns its ``entry`` and the dispatcher collects them into
    # the module-level ``enriched`` list — preserves the input order on
    # the serial path and on ``Pool.map`` (which yields in submission
    # order); the final sort by raw_score doesn't care either way.
    # Surface raw_score next to the clamped score so the operator can
    # actually tell clips apart when several land at score=1.000.
    _raw = entry.get("raw_score", entry["score"])
    print(
        f"  T={T} FINAL score={entry['score']:.3f} raw={_raw:.4f} "
        f"dur={entry['clip_duration']}s title=\"{entry['title']}\" "
        f"[{entry['category']}]",
        file=sys.stderr,
    )

    # P2.1 — classic A/B: attach ONE alternate-angle caption variant B (default
    # off; CLIP_AB_VARIANTS>=2). Additive + failure-soft: on any failure the key
    # stays unset and only A ships. Stage 7 renders B (with varied SFX/visual)
    # for the top-N clips.
    try:
        _b = _generate_variant_b(entry, local_transcript, hard_events)
        if _b:
            entry["hook_variants"] = [_b]
            print(f"  T={T} variant-B [{_b['angle']}]: hook=\"{_b['hook']}\"", file=sys.stderr)
    except Exception as _ve:
        print(f"  T={T} variant-B skipped ({_ve})", file=sys.stderr)

    # P2.3 — per-platform post kit (default off; runs after variant so its hook
    # is included). Written to a "<title>.post.json" sidecar by Stage 7.
    try:
        _pk = _generate_post_kit(entry, local_transcript)
        if _pk:
            entry["post_kit"] = _pk
            print(f"  T={T} post-kit ready (tiktok/instagram/youtube_shorts)", file=sys.stderr)
    except Exception as _pke:
        print(f"  T={T} post-kit skipped ({_pke})", file=sys.stderr)

    return entry


# Dispatch the per-moment work. Serial path is unchanged; parallel path
# uses ``ThreadPoolExecutor`` with ``STAGE6_WORKERS`` workers. ``map``
# preserves submission order so log lines stay readable when threads
# finish out-of-order (each worker prints its full per-moment block as
# a single contiguous run since stderr is line-buffered).
_stage6_workers = _resolve_stage6_workers()
if _stage6_workers <= 1 or len(moments) <= 1:
    print(f"[STAGE6] serial: processing {len(moments)} moments", file=sys.stderr)
    for moment in moments:
        _entry = _process_moment(moment)
        if _entry is not None:
            enriched.append(_entry)
else:
    print(
        f"[STAGE6] parallel: dispatching {len(moments)} VLM calls across "
        f"{_stage6_workers} workers",
        file=sys.stderr,
    )
    with ThreadPoolExecutor(max_workers=_stage6_workers) as _stage6_pool:
        for _entry in _stage6_pool.map(_process_moment, moments):
            if _entry is not None:
                enriched.append(_entry)

# NO FILTERING HERE — every moment goes to rendering.
# Sort by raw_score (uncapped) descending so A2-boosted callbacks above 1.0
# don't tie-break arbitrarily with vanilla 1.000s. Falls back to the clamped
# "score" when no raw is recorded (older entries / non-A2 paths).
enriched.sort(key=lambda x: x.get("raw_score", x["score"]), reverse=True)

# Item A (2026-06-06): spread the USER-FACING manifest score. Internally the
# pipeline keeps raw_score (true magnitude) and a hard-clamped `score` for its
# math (BUG 37/53), which pins multiple top clips at a visually-tied 1.000 in
# the Discord card / dashboard. Recompute the final display `score` from
# raw_score with the SAME soft-squash as Pass C (Fix 3A, _DISPLAY_SCALE=1.6)
# so clips differentiate. Output-only: every score computation above already
# ran on raw_score / the clamped score; this only reshapes the serialized
# display value (Stage 7 reads m["score"]). See clip-quality-remediation Fix 3.
_disp_scale = float(os.environ.get("CLIP_DISPLAY_SCORE_SCALE", "1.6") or "1.6") or 1.6
for _e in enriched:
    _rs = float(_e.get("raw_score", _e.get("score", 0.0)) or 0.0)
    _e["score"] = round(min(max(_rs, 0.0) / _disp_scale, 1.0), 3)

with open(f"{TEMP_DIR}/scored_moments.json", "w") as f:
    json.dump(enriched, f, indent=2)

vision_ok_count = sum(1 for e in enriched if e.get("vision_ok"))
print(f"\\nEnriched {len(enriched)} moments ({vision_ok_count} with vision data). ALL will be rendered.")
for s in enriched:
    v_tag = "V" if s.get("vision_ok") else "T"
    print(f"  [{v_tag}] T={s['timestamp']} score={s['score']:.3f} dur={s.get('clip_duration',30)}s [{s['category']}] ({s.get('segment_type','')}) — {s['title']}")
