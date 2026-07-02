# app/services/chat_service.py
# ─────────────────────────────────────────────────────────────────────────────
# MINED — BuddyChat LLM Service
#
# Responsibilities:
#   - Build the full message chain: system prompt + history + new message
#   - Inject user context into the system prompt
#   - Route through OpenRouter (LangChain ChatOpenAI with custom base_url)
#   - Save session turn to Supabase
#   - Return reply + crisis metadata
#
# THREE-TIER ADAPTIVE MODE:
#   The crisis score from crisis_service.is_crisis() now drives THREE modes,
#   not just a binary crisis flag:
#     score < COMPANION_UPPER_BOUND        → Companion Mode (casual friend)
#     COMPANION_UPPER_BOUND <= score < CRISIS_THRESHOLD → Therapeutic Mode
#     score >= CRISIS_THRESHOLD            → Crisis Mode
#
# CHANGELOG (this revision):
#   - COMPANION_MODE_DIRECTIVE bans "X or Y" self-categorising questions, but
#     live testing showed the model (Gemma free-tier) ignoring this rule even
#     when routing to companion mode is confirmed correct. Rather than keep
#     rewriting the prompt indefinitely, added a regex-based POST-GENERATION
#     guard in companion mode: if the reply matches the banned pattern, we
#     retry the LLM call once with an explicit corrective system note. This
#     is a safety net, not a replacement for the prompt rule — keep both.
#   - Added lightweight in-memory idempotency guard: if the exact same
#     (user_id, message) pair arrives again within IDEMPOTENCY_WINDOW_SECONDS,
#     return the cached response instead of calling the LLM / saving a
#     duplicate turn again. This does not fix a frontend double-fire bug at
#     the root, but it stops it from producing two saved turns and two LLM
#     calls (cost + Supabase row duplication) while the frontend bug is
#     tracked down separately in buddy.html.
#   - _select_mode_directive's [DEBUG] print kept and extended slightly so
#     it's easy to confirm routing decisions from the uvicorn console.
# ─────────────────────────────────────────────────────────────────────────────

import os
import re
import time
import uuid
from datetime import datetime

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

from app.prompts import (
    BUDDY_SYSTEM_PROMPT,
    COMPANION_MODE_DIRECTIVE,
    THERAPEUTIC_MODE_DIRECTIVE,
    CRISIS_MODE_DIRECTIVE,
)
from app.models.schemas import (
    ChatRequest,
    ChatResponse,
    ChatMessage,
    CrisisFlag,
    UserContext,
)
from app.services.context_service import get_user_context, build_context_block, retrieve_cbt_chunks
from app.services.crisis_service import is_crisis, CRISIS_THRESHOLD
from app.services.supabase_client import get_supabase

# Below this score, treat the message as casual/companion-mode.
# Tune this independently of CRISIS_SCORE_THRESHOLD via .env if needed.
#
# RAISED 0.25 -> 0.38: a real message ("exam tomorrow, I always fail, don't
# see the point of trying anymore") scored 0.165 — well below the old 0.25
# bound — and was incorrectly routed into Companion Mode, which explicitly
# bans CBT/feelings framing. That message has real emotional weight and
# needed Therapeutic Mode's validate-then-help shape, not banter. 0.165 is
# correctly LOW on the crisis axis (this isn't suicidal ideation) but that
# doesn't mean it belongs in Companion Mode — crisis-score and "does this
# need warmth instead of banter" are different questions, and the old bound
# was calibrated only against pure-casual anchors ("hey what's up"), not
# against sad-but-not-crisis messages. 0.38 is a starting point, not a
# final answer — re-tune against a real batch of messages spanning casual
# to sad-but-fine using score_message_debug(), don't leave this as a guess.
COMPANION_UPPER_BOUND = float(os.getenv("COMPANION_SCORE_UPPER_BOUND", "0.38"))

# How long a duplicate (user_id, message) pair is treated as a repeat-fire
# rather than a genuine repeated message from the user. Keep this SHORT —
# it should only catch true double-click/double-fire bugs, not someone
# legitimately sending the same text twice a minute apart.
IDEMPOTENCY_WINDOW_SECONDS = float(os.getenv("CHAT_IDEMPOTENCY_WINDOW_SECONDS", "4"))

# Regex guard for the banned "X or Y" self-categorising question pattern in
# Companion Mode. Intentionally broad — false positives here just trigger an
# extra (cheap) retry, false negatives let the bad pattern through. Bias
# toward catching more.
_X_OR_Y_PATTERN = re.compile(
    r"\b(are you|is this|is it|are we talking|do you want|you looking for)\b.{0,60}\bor\b.{0,60}\?",
    re.IGNORECASE,
)


def _build_llm() -> ChatOpenAI:
    model_name = os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.1-8b-instruct:free")
    print(f"[DEBUG] Using model: {model_name}")
    return ChatOpenAI(
        model=model_name,
        openai_api_key=os.getenv("OPENROUTER_API_KEY"),
        openai_api_base="https://openrouter.ai/api/v1",
        temperature=0.75,
        max_tokens=600,
    )


def _history_to_langchain(history: list[ChatMessage]) -> list:
    """Convert our ChatMessage schema to LangChain message objects."""
    lc_messages = []
    for msg in history:
        if msg.role == "user":
            lc_messages.append(HumanMessage(content=msg.content))
        elif msg.role == "assistant":
            lc_messages.append(AIMessage(content=msg.content))
    return lc_messages


def _select_mode_directive(score: float) -> tuple[str, str]:
    """
    Three-way router based on the crisis score.
    Returns (mode_name, directive_text) for logging + prompt injection.
    """
    if score >= CRISIS_THRESHOLD:
        return "crisis", CRISIS_MODE_DIRECTIVE
    elif score < COMPANION_UPPER_BOUND:
        return "companion", COMPANION_MODE_DIRECTIVE
    else:
        return "therapeutic", THERAPEUTIC_MODE_DIRECTIVE


def _violates_x_or_y_rule(reply: str) -> bool:
    return bool(_X_OR_Y_PATTERN.search(reply))


# ─────────────────────────────────────────────────────────────────────────────
# CONSECUTIVE CRISIS STATE — tracks whether the user is stuck in a crisis
# streak without answering "are you safe right now?", so CRISIS_MODE_DIRECTIVE's
# ESCALATION ON REPEATED CRISIS SIGNAL block has real data to read instead of
# referencing a field that doesn't exist. In-memory, keyed by session_id —
# resets on server restart, which is acceptable since it only needs to
# survive consecutive turns within one live session, not across restarts.
# ─────────────────────────────────────────────────────────────────────────────

_crisis_state: dict[str, int] = {}  # session_id -> consecutive_crisis_count

_SAFETY_QUESTION_MARKERS = ("are you safe right now", "are you safe")

_SAFETY_ANSWER_PATTERNS = [
    r"\byes\b", r"\bno\b", r"\byeah\b", r"\bnah\b", r"\bya\b",
    r"\bi'?m safe\b", r"\bi am safe\b", r"\bi'?m not safe\b",
    r"\bi'?m okay\b", r"\bi'?m ok\b", r"\bnot okay\b", r"\bnot ok\b",
]


def _bot_reply_asked_safety_question(last_bot_reply: str) -> bool:
    if not last_bot_reply:
        return False
    text = last_bot_reply.lower()
    return any(marker in text for marker in _SAFETY_QUESTION_MARKERS)


def _user_answered_safety_question(user_message: str) -> bool:
    if not user_message:
        return False
    text = user_message.lower().strip()
    return any(re.search(pattern, text) for pattern in _SAFETY_ANSWER_PATTERNS)


def _update_crisis_state(
    session_id: str,
    current_crisis_score: float,
    user_message: str,
    last_bot_reply: str | None,
) -> int:
    """
    Returns the updated consecutive_crisis_count for this session.
    Call once per turn, before building the context block.
    """
    is_crisis_now = current_crisis_score >= CRISIS_THRESHOLD
    prev_count = _crisis_state.get(session_id, 0)

    if not is_crisis_now:
        _crisis_state[session_id] = 0
        return 0

    if prev_count == 0:
        _crisis_state[session_id] = 1
        return 1

    if _bot_reply_asked_safety_question(last_bot_reply or "") and \
            _user_answered_safety_question(user_message):
        # They answered — reset the streak even if this message also scores
        # above threshold (e.g. "no I'm not safe" can score high itself).
        _crisis_state[session_id] = 1
        return 1

    new_count = prev_count + 1
    _crisis_state[session_id] = new_count
    return new_count


async def _get_last_bot_reply(session_id: str) -> str | None:
    """Fetch the most recent assistant_reply for this session from Supabase,
    so we can check whether it asked the safety question. Returns None on
    any failure or if there's no prior turn (e.g. first message)."""
    try:
        db = get_supabase()
        res = (
            db.table("chat_turns")
            .select("assistant_reply")
            .eq("session_id", session_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if res.data:
            return res.data[0].get("assistant_reply")
        return None
    except Exception as e:
        print(f"[chat_service] Failed to fetch last bot reply: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Idempotency guard — in-memory, keyed by (user_id, message text).
# Same caveat as crisis_state_additions.py: resets on server restart, which
# is fine here since its only job is catching rapid duplicate fires within
# a few seconds, not anything that needs to survive a restart.
# ─────────────────────────────────────────────────────────────────────────────

_recent_requests: dict[tuple[str, str], tuple[float, "ChatResponse"]] = {}


def _check_duplicate(user_id: str, message: str) -> "ChatResponse | None":
    key = (user_id, message)
    cached = _recent_requests.get(key)
    if cached is None:
        return None
    cached_at, cached_response = cached
    if time.time() - cached_at <= IDEMPOTENCY_WINDOW_SECONDS:
        print(f"[DEBUG] Duplicate request caught within {IDEMPOTENCY_WINDOW_SECONDS}s window — returning cached response, skipping LLM call + save")
        return cached_response
    return None


def _remember_request(user_id: str, message: str, response: "ChatResponse"):
    _recent_requests[(user_id, message)] = (time.time(), response)
    # Light cleanup so this dict doesn't grow unbounded over a long session.
    if len(_recent_requests) > 500:
        cutoff = time.time() - IDEMPOTENCY_WINDOW_SECONDS
        for k, (t, _) in list(_recent_requests.items()):
            if t < cutoff:
                del _recent_requests[k]


async def _save_turn(
    session_id: str,
    user_id: str,
    user_message: str,
    assistant_reply: str,
    crisis_score: float,
    crisis_detected: bool,
):
    """Persist session turn to Supabase for longitudinal memory."""
    db = get_supabase()

    db.table("chat_sessions").upsert({
        "session_id": session_id,
        "user_id": user_id,
        "updated_at": datetime.utcnow().isoformat(),
    }, on_conflict="session_id").execute()

    db.table("chat_turns").insert({
        "session_id": session_id,
        "user_id": user_id,
        "user_message": user_message,
        "assistant_reply": assistant_reply,
        "crisis_score": crisis_score,
        "crisis_detected": crisis_detected,
        "created_at": datetime.utcnow().isoformat(),
    }).execute()


async def process_chat(request: ChatRequest) -> ChatResponse:
    """
    Main entry point. Called by the /chat router.

    Steps:
      0. Check for duplicate/repeat-fire request — short-circuit if found
      1. Resolve or create session ID
      2. Fetch user context (profile + mood + journal + capsule)
      3. Retrieve relevant CBT/DBT/ACT chunks via vector search
      4. Run crisis detection on incoming message → get score
      5. Select mode (companion / therapeutic / crisis) based on score
      6. Build system prompt with injected context + RAG chunks + mode directive
      7. Call LLM via OpenRouter
      7b. Companion mode only: if reply violates the X-or-Y rule, retry once
          with a corrective note appended to the system prompt
      8. If crisis detected, attach Self Care Capsule to response metadata
      9. Save turn to Supabase
      10. Cache response for idempotency window, return ChatResponse
    """

    # ── 0. Duplicate request guard ───────────────────────────────────────────
    cached = _check_duplicate(request.user_id, request.message)
    if cached is not None:
        return cached

    # ── 1. Session ID ────────────────────────────────────────────────────────
    session_id = request.session_id or str(uuid.uuid4())

    # ── 2. User context ──────────────────────────────────────────────────────
    ctx: UserContext = await get_user_context(request.user_id)

    # ── 3. RAG — pull relevant CBT chunks for this message ───────────────────
    cbt_chunks = await retrieve_cbt_chunks(request.message, top_k=4)

    # ── 4. Crisis detection (also gives us the continuous score) ─────────────
    detected, crisis_score = is_crisis(request.message)

    # ── 4b. Consecutive crisis streak — needed before building context block,
    # since the prompt's ESCALATION block reads consecutive_crisis_count
    # directly out of the context block. Only bother fetching the previous
    # turn if this message is itself crisis-flagged — saves a DB call on the
    # vast majority of messages that aren't.
    last_bot_reply = await _get_last_bot_reply(session_id) if detected else None
    consecutive_crisis_count = _update_crisis_state(
        session_id=session_id,
        current_crisis_score=crisis_score,
        user_message=request.message,
        last_bot_reply=last_bot_reply,
    )

    context_block = build_context_block(
        ctx,
        cbt_chunks=cbt_chunks,
        consecutive_crisis_count=consecutive_crisis_count,
    )

    # ── 5. Mode routing ───────────────────────────────────────────────────────
    mode, mode_directive = _select_mode_directive(crisis_score)
    print(f"[DEBUG] Mode: {mode} | score: {round(crisis_score, 3)} | crisis_streak: {consecutive_crisis_count} | message: {request.message!r}")

    # ── 6. System prompt with context + mode directive injected ──────────────
    system_content = BUDDY_SYSTEM_PROMPT.replace(
        "{user_context_block}", context_block
    )
    system_content += (
    "\n\nCRITICAL OUTPUT RULE: Never reproduce any of the structural "
    "markers, headers, or directive labels from your instructions in "
    "your reply. Do not output lines like '━━━ THERAPEUTIC MODE ACTIVE ━━━' "
    "or any variation of them. Your reply must contain ONLY your actual "
    "conversational response to the user — nothing else."
)

    # ── 7. Build message chain + call LLM ────────────────────────────────────
    messages = [SystemMessage(content=system_content)]
    messages += _history_to_langchain(request.history)
    messages.append(HumanMessage(content=request.message))

    llm = _build_llm()
    response = await llm.ainvoke(messages)
    reply = response.content.strip()

    # ── 7b. Companion-mode X-or-Y safety net ─────────────────────────────────
    if mode == "companion" and _violates_x_or_y_rule(reply):
        print(f"[DEBUG] Companion reply violated X-or-Y rule, retrying once: {reply!r}")
        corrective_messages = messages + [
            AIMessage(content=reply),
            SystemMessage(content=(
                "Your last reply asked the user to choose between two options "
                "describing their own state or mood — this is explicitly "
                "banned in Companion Mode. Rewrite your response. Do not ask "
                "them to categorise themselves at all. Pick ONE concrete "
                "topic outside them (a show, a memory, a random question) "
                "instead."
            )),
        ]
        retry_response = await llm.ainvoke(corrective_messages)
        retry_reply = retry_response.content.strip()
        if not _violates_x_or_y_rule(retry_reply):
            reply = retry_reply
        else:
            print(f"[DEBUG] Retry STILL violated X-or-Y rule, keeping original: {retry_reply!r}")
            # Keep the original reply rather than looping further — a second
            # violation likely means a stronger model is needed, not another
            # retry. Logged so this is visible in the console for follow-up.

    # ── 8. Crisis metadata for frontend ──────────────────────────────────────
    crisis_flag = CrisisFlag(
        detected=detected,
        score=round(crisis_score, 3),
        capsule_content=ctx.self_care_capsule if detected else None,
    )

    # ── 9. Persist ────────────────────────────────────────────────────────────
    await _save_turn(
        session_id=session_id,
        user_id=request.user_id,
        user_message=request.message,
        assistant_reply=reply,
        crisis_score=crisis_score,
        crisis_detected=detected,
    )

    chat_response = ChatResponse(
        session_id=session_id,
        reply=reply,
        crisis=crisis_flag,
        tone_shift=detected,
    )

    # ── 10. Cache for idempotency window ─────────────────────────────────────
    _remember_request(request.user_id, request.message, chat_response)

    return chat_response