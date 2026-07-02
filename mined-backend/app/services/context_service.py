# app/services/context_service.py
#
# CHANGELOG (this revision):
#   - Added consecutive_crisis_count param to build_context_block(), injected
#     as a new "CRISIS STATE" labelled section in the context block. This was
#     previously referenced by prompts.py's ESCALATION ON REPEATED CRISIS
#     SIGNAL block but never actually wired in — the model was reading a
#     prompt that talked about a field that didn't exist in its context,
#     which likely contributed to crisis-mode jumping straight to the
#     counsellor offer instead of following the safety-check-first sequence.
#     See chat_service.py for where this is now populated via
#     update_crisis_state() before calling build_context_block().
from datetime import datetime, timedelta
from app.models.schemas import UserContext
from app.services.supabase_client import get_supabase

# ── vector search ──────────────────────────────────────────────────────────────
from sentence_transformers import SentenceTransformer

_embedder = None

def _get_embedder() -> SentenceTransformer:
    """Lazy-load the same model used during ingest so embeddings match."""
    global _embedder
    if _embedder is None:
        _embedder = SentenceTransformer("all-MiniLM-L6-v2")
    return _embedder


async def retrieve_cbt_chunks(user_message: str, top_k: int = 4) -> list[str]:
    """
    Embed the user message and pull the top-k most similar CBT chunks
    from knowledge_base using pgvector cosine similarity.
    Returns a list of plain-text chunk strings (empty list on any failure).
    """
    try:
        embedder = _get_embedder()
        query_vector = embedder.encode(user_message).tolist()

        db = get_supabase()
        # Calls the match_knowledge_base RPC defined in schema.sql (see note below)
        res = db.rpc(
            "match_knowledge_base",
            {
                "query_embedding": query_vector,
                "match_threshold": 0.35,   # cosine similarity floor
                "match_count": top_k,
            },
        ).execute()

        if not res.data:
            return []

        return [row["content"] for row in res.data if row.get("content")]
    except Exception as e:
        print(f"[context_service] RAG retrieval failed: {e}")
        return []


def _format_cbt_chunks(chunks: list[str]) -> str:
    if not chunks:
        return "No relevant CBT/DBT/ACT knowledge retrieved for this message."
    lines = [f"  [{i+1}] {chunk.strip()}" for i, chunk in enumerate(chunks)]
    return "\n\n".join(lines)
# ──────────────────────────────────────────────────────────────────────────────


def _compute_mood_trend(mood_logs: list) -> str:
    if len(mood_logs) < 2:
        return "stable"
    scores = [m["score"] for m in mood_logs if "score" in m]
    if len(scores) < 2:
        return "stable"
    mid = len(scores) // 2
    first_half = sum(scores[:mid]) / mid
    second_half = sum(scores[mid:]) / (len(scores) - mid)
    delta = second_half - first_half
    if delta >= 1.0:
        return "improving"
    elif delta <= -1.0:
        return "declining"
    return "stable"


def _format_mood_logs(logs: list) -> str:
    if not logs:
        return "No mood logs in the last 7 days."
    lines = []
    for log in logs:
        date = log.get("logged_at", "")[:10]
        score = log.get("score", "?")
        emoji = log.get("emoji", "")
        note = log.get("note", "")
        line = f"  • {date}: {score}/10 {emoji}"
        if note:
            line += f' — "{note}"'
        lines.append(line)
    return "\n".join(lines)


def _format_journal_entries(entries: list) -> str:
    if not entries:
        return "No journal entries in the last 7 days."
    lines = []
    for entry in entries[:5]:
        date = entry.get("created_at", "")[:10]
        content = entry.get("content", "")
        preview = content[:300] + "..." if len(content) > 300 else content
        lines.append(f'  • {date}: "{preview}"')
    return "\n".join(lines)


def build_context_block(
    ctx: UserContext,
    cbt_chunks: list[str] | None = None,
    consecutive_crisis_count: int = 0,
) -> str:
    trend_emoji = {"improving": "📈", "declining": "📉", "stable": "➡️"}.get(
        ctx.mood_trend, "➡️"
    )
    block = f"""
USER PROFILE
────────────
Name: {ctx.display_name or "Unknown"}
Mood trend (last 7 days): {ctx.mood_trend} {trend_emoji}

PROFILE SUMMARY (compressed history)
──────────────────────────────────────
{ctx.profile_summary or "No summary yet — this is an early user. Treat this as a first or early session."}

RECENT MOOD LOGS (last 7 days)
────────────────────────────────
{_format_mood_logs(ctx.recent_mood_logs)}

RECENT JOURNAL ENTRIES (last 7 days)
──────────────────────────────────────
{_format_journal_entries(ctx.recent_journal_entries)}

SELF CARE CAPSULE
──────────────────
{ctx.self_care_capsule or "The user has not written a Self Care Capsule yet."}

RELEVANT CBT / DBT / ACT KNOWLEDGE
─────────────────────────────────────
Use these retrieved excerpts to inform your response where relevant.
Do NOT quote them directly — use them to shape your therapeutic approach.

{_format_cbt_chunks(cbt_chunks or [])}

CRISIS STATE
─────────────
consecutive_crisis_count: {consecutive_crisis_count}
(Number of consecutive crisis-flagged messages in this session where the
user has NOT yet answered "are you safe right now?". 0 means no active
crisis streak. Only relevant in Crisis Mode — see ESCALATION ON REPEATED
CRISIS SIGNAL in the system prompt for how to use this number.)
""".strip()
    return block


async def get_user_context(user_id: str) -> UserContext:
    db = get_supabase()
    seven_days_ago = (datetime.utcnow() - timedelta(days=7)).isoformat()

    # 1. User profile — safe fetch
    try:
        profile_res = (
            db.table("user_profiles")
            .select("display_name, profile_summary")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        profile = profile_res.data[0] if profile_res.data else {}
    except Exception:
        profile = {}

    # 2. Recent mood logs — safe fetch
    try:
        mood_res = (
            db.table("mood_logs")
            .select("score, emoji, note, logged_at")
            .eq("user_id", user_id)
            .gte("logged_at", seven_days_ago)
            .order("logged_at", desc=False)
            .execute()
        )
        mood_logs = mood_res.data if mood_res.data else []
    except Exception:
        mood_logs = []

    # 3. Recent journal entries — safe fetch
    try:
        journal_res = (
            db.table("journal_entries")
            .select("content, created_at, prompt_used")
            .eq("user_id", user_id)
            .gte("created_at", seven_days_ago)
            .order("created_at", desc=True)
            .limit(7)
            .execute()
        )
        journal_entries = journal_res.data if journal_res.data else []
    except Exception:
        journal_entries = []

    # 4. Self Care Capsule — safe fetch
    try:
        capsule_res = (
            db.table("self_care_capsules")
            .select("content")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        capsule_content = capsule_res.data[0]["content"] if capsule_res.data else None
    except Exception:
        capsule_content = None

    return UserContext(
        user_id=user_id,
        display_name=profile.get("display_name"),
        profile_summary=profile.get("profile_summary"),
        recent_mood_logs=mood_logs,
        recent_journal_entries=journal_entries,
        self_care_capsule=capsule_content,
        mood_trend=_compute_mood_trend(mood_logs),
    )