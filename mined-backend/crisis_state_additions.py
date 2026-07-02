# crisis_state_additions.py
# ─────────────────────────────────────────────────────────────────────────────
# NOT a standalone file to drop in as-is. This shows the specific additions
# needed in context_service.py and chat_service.py to support the new
# ESCALATION ON REPEATED CRISIS SIGNAL block in prompts.py.
#
# I don't have your actual context_service.py / chat_service.py in this
# session, so merge these pieces into your real files rather than overwrite
# anything. Three things need to happen:
#
#   1. Track consecutive_crisis_count per session (in-memory or in
#      chat_sessions table — in-memory is fine for now, see below)
#   2. Detect whether the user's PREVIOUS bot reply already asked
#      "are you safe right now?" and whether THIS user message answers it
#   3. Inject consecutive_crisis_count into user_context_block so the prompt
#      can read it
# ─────────────────────────────────────────────────────────────────────────────

import re

# ─────────────────────────────────────────────────────────────────────────────
# 1. SESSION-LEVEL CRISIS STATE
#
# Simplest version: an in-memory dict keyed by session_id. This resets on
# server restart, which is fine for now — it does NOT need to survive across
# the nightly summarisation job, only across consecutive turns within one
# live session. If you want it durable across restarts later, add a
# `consecutive_crisis_count` int column to chat_sessions instead and read/
# write it there. For now, in-memory keeps this a small, low-risk change.
# ─────────────────────────────────────────────────────────────────────────────

_crisis_state: dict[str, int] = {}  # session_id -> consecutive_crisis_count


def get_consecutive_crisis_count(session_id: str) -> int:
    return _crisis_state.get(session_id, 0)


# ─────────────────────────────────────────────────────────────────────────────
# 2. DETECTING WHETHER THE SAFETY QUESTION WAS ANSWERED
#
# We need to know: did the bot's last reply ask "are you safe right now?",
# and if so, does the user's new message actually answer it (yes/no, in any
# wording) or just repeat distress without answering?
#
# This is intentionally simple pattern matching, not another ML model — it
# only needs to catch the common cases. Err toward "not answered" if unsure,
# since the cost of escalating one turn early is much lower than the cost of
# looping the same question past someone in crisis.
# ─────────────────────────────────────────────────────────────────────────────

_SAFETY_QUESTION_MARKERS = (
    "are you safe right now",
    "are you safe",
)

# Loose patterns for an actual answer to "are you safe right now?" — not
# trying to be exhaustive, just catching the common direct responses so we
# don't over-escalate when someone DID answer.
_SAFETY_ANSWER_PATTERNS = [
    r"\byes\b", r"\bno\b", r"\byeah\b", r"\bnah\b", r"\bya\b",
    r"\bi'?m safe\b", r"\bi am safe\b", r"\bi'?m not safe\b",
    r"\bi'?m okay\b", r"\bi'?m ok\b", r"\bnot okay\b", r"\bnot ok\b",
]


def bot_reply_asked_safety_question(last_bot_reply: str) -> bool:
    if not last_bot_reply:
        return False
    text = last_bot_reply.lower()
    return any(marker in text for marker in _SAFETY_QUESTION_MARKERS)


def user_answered_safety_question(user_message: str) -> bool:
    if not user_message:
        return False
    text = user_message.lower().strip()
    return any(re.search(pattern, text) for pattern in _SAFETY_ANSWER_PATTERNS)


# ─────────────────────────────────────────────────────────────────────────────
# 3. UPDATE STATE — call this in chat_service.py BEFORE building the prompt
#    for the current turn, once you have:
#      - session_id
#      - the current user message
#      - the crisis score for the current message (from crisis_service.py)
#      - the bot's previous reply in this session (you likely already pull
#        this for conversation history / context window)
# ─────────────────────────────────────────────────────────────────────────────

def update_crisis_state(
    session_id: str,
    current_crisis_score: float,
    crisis_threshold: float,
    user_message: str,
    last_bot_reply: str | None,
) -> int:
    """
    Returns the updated consecutive_crisis_count for this session, and
    updates internal state. Call once per turn, before building the prompt.
    """
    is_crisis_now = current_crisis_score >= crisis_threshold
    prev_count = _crisis_state.get(session_id, 0)

    if not is_crisis_now:
        # Current message is not crisis-flagged — reset the streak.
        _crisis_state[session_id] = 0
        return 0

    if prev_count == 0:
        # First crisis-flagged message in this streak.
        _crisis_state[session_id] = 1
        return 1

    # We were already in a crisis streak. Check if the user just answered
    # the safety question the bot previously asked.
    if bot_reply_asked_safety_question(last_bot_reply or "") and \
            user_answered_safety_question(user_message):
        # They answered — reset the streak, even though this message also
        # happens to score above threshold (e.g. "no I'm not safe" can
        # itself score high). We want the prompt to respond to what they
        # said, not keep escalating.
        _crisis_state[session_id] = 1
        return 1

    # Still in crisis, still hasn't answered — increment.
    new_count = prev_count + 1
    _crisis_state[session_id] = new_count
    return new_count


# ─────────────────────────────────────────────────────────────────────────────
# 4. WIRING — what changes in context_service.py's build_context_block()
#
# Add consecutive_crisis_count as a parameter, and inject it as a labelled
# line in the context block so the model can read it directly (the prompt's
# ESCALATION block references "{user_context_block} includes a field called
# consecutive_crisis_count").
#
# Example of what to add inside build_context_block():
#
#   def build_context_block(
#       profile_summary: str,
#       recent_mood_logs: str,
#       recent_journal_entries: str,
#       cbt_chunks: list[str],
#       consecutive_crisis_count: int,   # <-- NEW PARAM
#       self_care_capsule: str | None = None,
#   ) -> str:
#       context_parts = [
#           f"User profile summary: {profile_summary}",
#           f"Recent mood logs: {recent_mood_logs}",
#           f"Recent journal entries: {recent_journal_entries}",
#           f"Relevant CBT material: {' | '.join(cbt_chunks)}",
#           f"consecutive_crisis_count: {consecutive_crisis_count}",  # <-- NEW
#       ]
#       if self_care_capsule:
#           context_parts.append(f"Self Care Capsule: {self_care_capsule}")
#       return "\n".join(context_parts)
#
# And in chat_service.py, before calling build_context_block():
#
#   crisis_count = update_crisis_state(
#       session_id=session_id,
#       current_crisis_score=crisis_score,
#       crisis_threshold=settings.CRISIS_SCORE_THRESHOLD,
#       user_message=user_message,
#       last_bot_reply=previous_turn.bot_reply if previous_turn else None,
#   )
#   context_block = build_context_block(
#       ...,
#       consecutive_crisis_count=crisis_count,
#   )
# ─────────────────────────────────────────────────────────────────────────────