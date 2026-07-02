# app/services/summariser.py
# ─────────────────────────────────────────────────────────────────────────────
# MINED — Nightly Summarisation Pipeline
#
# Runs as a background job (cron / Railway scheduler).
# For each user with sessions older than 7 days:
#   1. Fetch unsummarised turns
#   2. Ask the LLM to compress them into a structured profile summary
#   3. Merge with existing profile summary
#   4. Write back to user_profiles.profile_summary
#   5. Mark turns as summarised
#
# This is what keeps the context window manageable AND the AI smarter over time.
# ─────────────────────────────────────────────────────────────────────────────

import os
from datetime import datetime, timedelta
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from app.services.supabase_client import get_supabase

SUMMARISER_PROMPT = """
You are a clinical records assistant for MINED, a mental wellness platform.
You will be given a set of therapy session transcripts and the user's existing
profile summary. Your job is to produce an updated, compressed profile summary.

OUTPUT FORMAT (plain text, under 500 words):
━━━ RECURRING THEMES ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[2-4 bullet points: emotional patterns, life stressors, topics they return to]

━━━ MOOD BASELINE ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[Typical mood range, volatility, any notable shifts over the period]

━━━ KEY EVENTS ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[Significant events mentioned: exams, relationships, family, health]

━━━ PROGRESS & STRENGTHS ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[Coping strategies that helped, moments of growth, things the user values]

━━━ WATCH POINTS ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[Patterns that may need attention in future sessions — NOT diagnoses]

Rules:
- Be compassionate and clinical. No jargon. No diagnoses.
- Preserve specific details (names of people in their life, key dates) — these
  make future sessions feel continuous.
- Remove anything that is fully resolved and no longer relevant.
- If the existing summary already covers something, update don't duplicate.
"""


def _build_summariser_llm() -> ChatOpenAI:
    return ChatOpenAI(
        model=os.getenv("OPENROUTER_MODEL", "mistralai/mistral-7b-instruct"),
        openai_api_key=os.getenv("OPENROUTER_API_KEY"),
        openai_api_base="https://openrouter.ai/api/v1",
        temperature=0.3,    # more deterministic for summarisation
        max_tokens=700,
        model_kwargs={
            "headers": {
                "HTTP-Referer": "https://mined.lovable.app",
                "X-Title": "MINED — Summariser",
            }
        },
    )


async def summarise_user(user_id: str):
    """
    Summarise all unsummarised turns older than 7 days for a single user.
    Called by the nightly job for each eligible user.
    """
    db = get_supabase()
    cutoff = (datetime.utcnow() - timedelta(days=7)).isoformat()

    # ── Fetch unsummarised old turns ─────────────────────────────────────────
    turns_res = (
        db.table("chat_turns")
        .select("user_message, assistant_reply, created_at")
        .eq("user_id", user_id)
        .eq("summarised", False)
        .lt("created_at", cutoff)
        .order("created_at", desc=False)
        .execute()
    )
    turns = turns_res.data or []

    if not turns:
        return   # nothing to summarise

    # ── Fetch existing profile summary ───────────────────────────────────────
    profile_res = (
        db.table("user_profiles")
        .select("profile_summary")
        .eq("user_id", user_id)
        .maybe_single()
        .execute()
    )
    existing_summary = (profile_res.data or {}).get("profile_summary", "")

    # ── Build transcript block ───────────────────────────────────────────────
    transcript_lines = []
    for turn in turns:
        date = turn["created_at"][:10]
        transcript_lines.append(f"[{date}] User: {turn['user_message']}")
        transcript_lines.append(f"[{date}] Buddy: {turn['assistant_reply']}")
    transcript = "\n".join(transcript_lines)

    user_input = f"""
EXISTING PROFILE SUMMARY:
{existing_summary or "None — this is a first summarisation."}

NEW SESSION TRANSCRIPTS TO INCORPORATE:
{transcript}

Please produce an updated profile summary.
""".strip()

    # ── Call LLM ─────────────────────────────────────────────────────────────
    llm = _build_summariser_llm()
    response = await llm.ainvoke([
        SystemMessage(content=SUMMARISER_PROMPT),
        HumanMessage(content=user_input),
    ])
    new_summary = response.content.strip()

    # ── Write back ───────────────────────────────────────────────────────────
    db.table("user_profiles").upsert({
        "user_id": user_id,
        "profile_summary": new_summary,
        "summary_updated_at": datetime.utcnow().isoformat(),
    }).execute()

    # Mark turns as summarised
    turn_ids = [t["id"] for t in turns if "id" in t]
    if turn_ids:
        db.table("chat_turns").update({"summarised": True}).in_("id", turn_ids).execute()

    print(f"[Summariser] ✓ user={user_id} | turns={len(turns)}")


async def run_nightly_summarisation():
    """
    Entry point for the cron job.
    Finds all users with unsummarised old turns and processes them.
    Schedule this via Railway cron or a simple APScheduler job.
    """
    db = get_supabase()
    cutoff = (datetime.utcnow() - timedelta(days=7)).isoformat()

    # Get distinct users with unsummarised old turns
    res = (
        db.table("chat_turns")
        .select("user_id")
        .eq("summarised", False)
        .lt("created_at", cutoff)
        .execute()
    )
    user_ids = list({row["user_id"] for row in (res.data or [])})

    print(f"[Summariser] Starting nightly job for {len(user_ids)} users")
    for user_id in user_ids:
        try:
            await summarise_user(user_id)
        except Exception as e:
            print(f"[Summariser] ✗ user={user_id} error={e}")
