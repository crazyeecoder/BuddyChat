# app/routers/chat.py
from fastapi import APIRouter, HTTPException
from app.models.schemas import ChatRequest, ChatResponse
from app.services.chat_service import process_chat

router = APIRouter(prefix="/chat", tags=["BuddyChat"])


@router.post("/", response_model=ChatResponse)
async def buddy_chat(request: ChatRequest):
    """
    Main BuddyChat endpoint.

    Send a message, get Buddy's reply with crisis metadata.

    Request body:
        user_id    — Supabase auth user ID
        session_id — optional; omit to start a new session
        message    — the user's current message
        history    — full conversation so far [{role, content}, ...]

    Response:
        session_id      — use this for all subsequent turns
        reply           — Buddy's response
        crisis.detected — bool: was distress flagged?
        crisis.score    — float 0–1: raw semantic score
        crisis.capsule_content — user's own capsule message (if crisis + capsule exists)
        tone_shift      — bool: did we activate crisis mode?
    """
    try:
        return await process_chat(request)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
