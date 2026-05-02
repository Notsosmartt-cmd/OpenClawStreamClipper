"""Stage 6 — Vision Enrichment (frame scoring, titles, hook, originality hints).

Extracted from scripts/clip-pipeline.sh as part of the modularization plan
(see AIclippingPipelineVault/wiki/concepts/modularization-plan.md, Phase A2).

Reads bash-interpolated config from environment variables:
    LLM_URL, VISION_MODEL_STAGE6

Vision is non-gatekeeping: it can only boost moment scores or annotate them,
never eliminate them. See concepts/vision-enrichment for the score-blending
formula. Behavior is byte-identical to the pre-extraction heredoc.
"""
import json, re, base64, os, sys, time
try:
    import urllib.request
except:
    pass

LLM_URL = os.environ["LLM_URL"]
# Phase 5.1: Stage 6 honors vision_model_stage6 override (falls back to
# vision_model when unset). See config/models.json.
VISION_MODEL = os.environ["VISION_MODEL_STAGE6"]
TEMP_DIR = "/tmp/clipper"

# Phase 1.1: 3-tier grounding cascade on the VLM's title/hook/description
# plus the regenerate-once policy on cascade failure. Loads same module the
# Stage 4 heredoc uses; the cascade config + denylist are bind-mounted from
# ./config into /root/.openclaw inside the container.
sys.path.insert(0, "/root/scripts/lib")
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
_VISION_NET_FAIL_STREAK = 0
_VISION_NET_FAIL_LIMIT = 3
_VISION_NET_PATTERNS = (
    "Network is unreachable",
    "Errno 101",
    "Connection refused",
    "Errno 111",
    "Name or service not known",
    "timed out",
    "Read timed out",
)

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
      1. First sentence of Pass B's `why` field (capped at 60 chars). Usually
         a phrase like "Streamer reacts to first sub of the stream" — fine
         as both filename and hook text.
      2. "<Category> at MM:SS" — predictable and readable when `why` is empty.
    """
    if why:
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


for moment in moments:
    T = moment["timestamp"]
    transcript_score = moment.get("score", 5)
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
        "category": transcript_category,
        "title": _derive_baseline_title(transcript_why, transcript_category, T),
        "description": transcript_why[:100] if transcript_why else "",
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

        # Pull ±8 seconds of actual transcript around the peak so the vision
        # model is grounded in the streamer's real words rather than relying
        # on the upstream Pass-B "why" summary (which is itself LLM output
        # and can propagate hallucinations downstream).
        local_transcript = ""
        try:
            with open(f"{TEMP_DIR}/transcript.json") as _tf:
                _tr = json.load(_tf)
            window = [seg for seg in _tr
                      if seg.get("start", 0) >= T - 8 and seg.get("end", 0) <= T + 8]
            local_transcript = " ".join(s.get("text", "").strip() for s in window)[:500]
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
- interaction_shape: one of "monologue" (one person, talking to camera), "reading-chat" (gaze toward chat panel; reading or paraphrasing), "dialog-with-on-screen-guest" (two visible people in conversation), "dialog-with-off-screen-voice" (one visible person reacting to an off-screen voice), "gameplay-with-commentary" (game footage primary, person inset), "silent-gameplay" (game footage, no commentary visible), "multi-speaker-stage" (3+ visible speakers).
- pattern_match: which catalog pattern the frames best support — "setup_external_contradiction", "challenge_and_fold", "reading_chat_reaction", "storytelling_arc", "hot_take_pushback", "informational_ramble", "interview_revelation", "rap_battle_freestyle", "social_callout", "unexpected_topic_shift", or null.
- pattern_match_strength: 0.0-1.0 — confidence the frames support pattern_match.
- gaze_direction: "at-camera" / "at-chat" / "at-screen" / "at-guest" / "off-screen" / "down".

Respond ONLY with JSON: {{
  "score": 1-10,
  "category": "comedy/skill/reaction/controversy/emotional/irl",
  "title": "short viral title rooted in the payoff and transcript",
  "description": "one sentence grounded in what was literally said/shown",
  "hook": "punchy 1-line hook shown at top of the video, max 8 words, in the voice of a {stream_type} content creator, no hashtags",
  "grounded_in_transcript": true|false,
  "mirror_safe": true|false,
  "voiceover": {{"text": "...", "placement": "intro|peak|outro", "tone": "hype|deadpan|earnest|snarky", "duration_estimate_s": 3.0}},
  "interaction_shape": "monologue|reading-chat|dialog-with-on-screen-guest|dialog-with-off-screen-voice|gameplay-with-commentary|silent-gameplay|multi-speaker-stage",
  "pattern_match": "<pattern_id or null>",
  "pattern_match_strength": 0.0-1.0,
  "gaze_direction": "at-camera|at-chat|at-screen|at-guest|off-screen|down"{("," + chr(10) + "  " + chr(34) + "callback_confirmed" + chr(34) + ": 0-10 (Tier-3 A2 — does the visual continuity between setup frames 1-2 and payoff frames 3+ support the claimed callback? 0=different scene/person, 5=ambiguous, 10=same person/scene clearly drives both halves)") if a2_active else ""}
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
                    "chat_template_kwargs": {"enable_thinking": False},
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
                    if _vision_looks_like_outage(_ve):
                        _VISION_NET_FAIL_STREAK += 1
                    else:
                        _VISION_NET_FAIL_STREAK = 0
                    return None, 0
                # A successful response — even if we end up failing to parse
                # the JSON below — proves the network is up and resets streak.
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
                if "\`\`\`" in _clean:
                    _parts = _clean.split("\`\`\`")
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
                        _chk = _grounding.cascade_check(
                            _claim, refs, GROUNDING_DENYLIST, GROUNDING_CONFIG,
                            min_overlap=0.15,
                            hard_events=hard_events,
                            event_map=CHAT_EVENT_MAP,
                        )
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
                        if _hits:
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

Rewrite your JSON so every claim in title, hook, and description is directly supported by the transcript above. If the transcript doesn't contain enough detail to justify a field, make that field a plain description of what is visibly happening in the frames — do NOT invent subscription events, raids, hype trains, kills, or any specifics not present in the transcript or frames.]"""

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
                            _rchk = _grounding.cascade_check(
                                _rclaim, refs, GROUNDING_DENYLIST, GROUNDING_CONFIG,
                                min_overlap=0.15,
                                hard_events=hard_events,
                                event_map=CHAT_EVENT_MAP,
                            )
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
                # Downstream Stage 7 falls back to the transcript-only defaults
                # already seeded in entry (title=f"Clip_T{T}", description=why).
                for _field, _chk in field_results.items():
                    if _chk["passed"]:
                        continue
                    _hit_summary = ",".join(
                        h["match"] for h in _chk.get("denylist_hits", [])
                    ) or (
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
        v_title = best_vision_result.get("title", "")
        if v_title and v_title != "":
            entry["title"] = v_title
        v_desc = best_vision_result.get("description", "")
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
        b_pattern = entry.get("primary_pattern") or moment.get("primary_pattern", "")
        d_pattern = entry.get("pattern_confirmed") or moment.get("pattern_confirmed", "")
        v_pattern = entry.get("vision_pattern_match", "")
        d_strength = float(moment.get("pattern_match_strength", 0) or 0)
        v_strength = entry.get("vision_pattern_match_strength", 0)
        if b_pattern and b_pattern == d_pattern == v_pattern and d_strength >= 0.6 and v_strength >= 0.6:
            entry["cross_validated_full"] = True
            new_score = min(float(entry.get("score", 0) or 0) + 0.1, 1.0)
            print(f"  T={T} cross_validated_full pattern={b_pattern} score+=0.1 -> {new_score:.3f}", file=sys.stderr)
            entry["score"] = round(new_score, 3)
        # Blend scores: transcript is primary, vision is a bonus (never penalizes)
        # Vision >= 0.67 (was 7/10): multiply by 1.15
        # Vision >= 0.44 (was 5/10): multiply by 1.08
        # Vision < 0.44: keep transcript score unchanged
        if vision_norm >= 0.67:
            entry["score"] = round(min(transcript_score * 1.15, 1.0), 3)
            print(f"  T={T} vision BOOST: {transcript_score:.3f} -> {entry['score']:.3f}", file=sys.stderr)
        elif vision_norm >= 0.44:
            entry["score"] = round(min(transcript_score * 1.08, 1.0), 3)
        # else: keep transcript_score as-is

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

    enriched.append(entry)
    print(f"  T={T} FINAL score={entry['score']:.3f} dur={entry['clip_duration']}s title=\"{entry['title']}\" [{entry['category']}]", file=sys.stderr)

# NO FILTERING HERE — every moment goes to rendering.
# Sort by raw_score (uncapped) descending so A2-boosted callbacks above 1.0
# don't tie-break arbitrarily with vanilla 1.000s. Falls back to the clamped
# "score" when no raw is recorded (older entries / non-A2 paths).
enriched.sort(key=lambda x: x.get("raw_score", x["score"]), reverse=True)

with open(f"{TEMP_DIR}/scored_moments.json", "w") as f:
    json.dump(enriched, f, indent=2)

vision_ok_count = sum(1 for e in enriched if e.get("vision_ok"))
print(f"\\nEnriched {len(enriched)} moments ({vision_ok_count} with vision data). ALL will be rendered.")
for s in enriched:
    v_tag = "V" if s.get("vision_ok") else "T"
    print(f"  [{v_tag}] T={s['timestamp']} score={s['score']:.3f} dur={s.get('clip_duration',30)}s [{s['category']}] ({s.get('segment_type','')}) — {s['title']}")
